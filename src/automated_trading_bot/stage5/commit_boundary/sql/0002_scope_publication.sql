BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA atis_soe;

DO $roles$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='atis_soe_registry') THEN
        CREATE ROLE atis_soe_registry NOLOGIN NOINHERIT;
    END IF;
END
$roles$;

SET ROLE atis_soe_owner;
SET search_path = pg_catalog, atis_soe, pg_temp;

CREATE TABLE atis_soe.deployment_integrity (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    deployment_identity text NOT NULL,
    policy_version bigint NOT NULL CHECK (policy_version > 0),
    CHECK (deployment_identity <> '')
);

CREATE TABLE atis_soe.scope_catalogue (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    active_epoch bigint NOT NULL CHECK (active_epoch > 0),
    reserved_epoch bigint CHECK (reserved_epoch > active_epoch),
    state text NOT NULL CHECK (state IN ('ACTIVE','TRANSITION_PENDING','RECOVERY_REQUIRED'))
);

CREATE TABLE atis_soe.scope_identity (
    catalogue_epoch bigint NOT NULL,
    canonical_key text NOT NULL,
    kind text NOT NULL CHECK (kind IN (
        'GLOBAL','ACCOUNT','PORTFOLIO','STRATEGY','INSTRUMENT','CURRENCY','OPERATION'
    )),
    namespace text NOT NULL,
    identity text NOT NULL,
    canonical_bytes bytea NOT NULL,
    active boolean NOT NULL,
    PRIMARY KEY (catalogue_epoch, canonical_key),
    UNIQUE (catalogue_epoch, kind, namespace, identity),
    CHECK (namespace <> '' AND identity <> '')
);

CREATE TABLE atis_soe.scope_relation (
    catalogue_epoch bigint NOT NULL,
    child_key text NOT NULL,
    parent_key text NOT NULL,
    PRIMARY KEY (catalogue_epoch, child_key, parent_key),
    FOREIGN KEY (catalogue_epoch, child_key)
      REFERENCES atis_soe.scope_identity(catalogue_epoch, canonical_key),
    FOREIGN KEY (catalogue_epoch, parent_key)
      REFERENCES atis_soe.scope_identity(catalogue_epoch, canonical_key),
    CHECK (child_key <> parent_key)
);

CREATE TABLE atis_soe.operation_scope_registration (
    operation_id text PRIMARY KEY,
    request_digest text NOT NULL,
    operation_class text NOT NULL,
    deployment_identity text NOT NULL,
    catalogue_epoch bigint NOT NULL,
    operation_core_digest text NOT NULL,
    scope_envelope_digest text NOT NULL,
    canonical_envelope bytea NOT NULL,
    registered_by text NOT NULL,
    registered_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp()
);

CREATE TABLE atis_soe.operation_applicable_scope (
    operation_id text NOT NULL REFERENCES atis_soe.operation_scope_registration(operation_id),
    scope_key text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (operation_id, scope_key),
    UNIQUE (operation_id, ordinal)
);

CREATE TABLE atis_soe.scope_barrier (
    scope_key text PRIMARY KEY,
    pending_ticket bigint,
    effective_ticket bigint,
    CHECK (pending_ticket IS NULL OR pending_ticket >= 0),
    CHECK (effective_ticket IS NULL OR effective_ticket >= 0)
);

CREATE TABLE atis_soe.restriction_publication (
    operation_id text PRIMARY KEY,
    request_digest text NOT NULL,
    issuer_identity text NOT NULL,
    issuer_domain text NOT NULL,
    scope_key text NOT NULL,
    ticket_sequence bigint NOT NULL CHECK (ticket_sequence >= 0),
    state text NOT NULL CHECK (state IN (
        'PENDING_DENY','EFFECTIVE_DENY','RECONCILIATION_REQUIRED','TERMINAL_DENIED'
    )),
    receipt_digest text NOT NULL UNIQUE,
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp()
);

CREATE TABLE atis_soe.trusted_time_high_water (
    policy_identity text PRIMARY KEY,
    observed_at timestamptz NOT NULL,
    proof_identity text NOT NULL UNIQUE
);

CREATE TABLE atis_soe.scope_capital_state_v2 (
    scope_key text PRIMARY KEY,
    reserved_minor bigint NOT NULL DEFAULT 0 CHECK (reserved_minor >= 0)
);

CREATE FUNCTION atis_soe.canonical_jsonb_text(p_value jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path = pg_catalog, atis_soe, pg_temp AS $function$
DECLARE v_result text;
BEGIN
  CASE jsonb_typeof(p_value)
    WHEN 'object' THEN
      SELECT '{' || COALESCE(string_agg(
        to_jsonb(key)::text || ':' || atis_soe.canonical_jsonb_text(value),
        ',' ORDER BY key), '') || '}' INTO v_result FROM jsonb_each(p_value);
    WHEN 'array' THEN
      SELECT '[' || COALESCE(string_agg(
        atis_soe.canonical_jsonb_text(value), ',' ORDER BY ordinal), '') || ']'
        INTO v_result FROM jsonb_array_elements(p_value) WITH ORDINALITY a(value,ordinal);
    ELSE v_result := p_value::text;
  END CASE;
  RETURN v_result;
END
$function$;

CREATE FUNCTION atis_soe.canonical_jsonb_bytes(p_value jsonb) RETURNS bytea
LANGUAGE sql IMMUTABLE STRICT
SET search_path = pg_catalog, atis_soe, pg_temp
AS $function$
    SELECT pg_catalog.convert_to(atis_soe.canonical_jsonb_text(p_value), 'UTF8')
$function$;

CREATE FUNCTION atis_soe.register_scope_envelope_v1(
    p_operation_id text, p_request_digest text, p_operation_class text,
    p_catalogue_epoch bigint, p_operation_core_digest text,
    p_scope_envelope_digest text, p_canonical_envelope bytea
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, atis_soe, pg_temp AS $function$
DECLARE
    v_catalogue atis_soe.scope_catalogue%ROWTYPE;
    v_deployment atis_soe.deployment_integrity%ROWTYPE;
    v_envelope jsonb;
    v_scope jsonb;
    v_key text;
    v_ordinal integer := 0;
    v_kinds text[] := ARRAY[]::text[];
BEGIN
    PERFORM atis_soe.require_serializable();
    PERFORM atis_soe.require_member('atis_soe_verifier');
    SELECT * INTO v_deployment FROM atis_soe.deployment_integrity
      WHERE singleton FOR SHARE;
    SELECT * INTO v_catalogue FROM atis_soe.scope_catalogue
      WHERE singleton FOR SHARE;
    IF NOT FOUND OR v_catalogue.state <> 'ACTIVE'
       OR v_catalogue.active_epoch <> p_catalogue_epoch THEN
        RAISE EXCEPTION 'stale or unavailable scope catalogue';
    END IF;
    BEGIN
        v_envelope := pg_catalog.convert_from(p_canonical_envelope, 'UTF8')::jsonb;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'scope envelope is not canonical JSON';
    END;
    IF p_canonical_envelope <> atis_soe.canonical_jsonb_bytes(v_envelope)
       OR v_envelope->>'deployment_identity' <> v_deployment.deployment_identity
       OR (v_envelope->>'catalogue_epoch')::bigint <> p_catalogue_epoch
       OR v_envelope->>'operation_class' <> p_operation_class
       OR v_envelope->>'operation_id' <> p_operation_id
       OR v_envelope->>'operation_core_digest' <> p_operation_core_digest
       OR pg_catalog.encode(atis_soe.digest(
            pg_catalog.decode(
              '415449532d53352d43414e4f4e4943414c2d53434f50452d454e56454c4f50452d563100',
              'hex')
            || p_canonical_envelope, 'sha256'), 'hex') <> p_scope_envelope_digest THEN
        RAISE EXCEPTION 'scope envelope canonical binding mismatch';
    END IF;
    IF jsonb_typeof(v_envelope->'scopes') <> 'array' THEN
        RAISE EXCEPTION 'scope envelope scopes are missing';
    END IF;
    INSERT INTO atis_soe.operation_scope_registration VALUES (
        p_operation_id,p_request_digest,p_operation_class,v_deployment.deployment_identity,
        p_catalogue_epoch,p_operation_core_digest,p_scope_envelope_digest,
        p_canonical_envelope,session_user,pg_catalog.clock_timestamp()
    );
    FOR v_scope IN SELECT value FROM jsonb_array_elements(v_envelope->'scopes') LOOP
        v_key := pg_catalog.convert_from(atis_soe.canonical_jsonb_bytes(v_scope),'UTF8');
        IF NOT EXISTS (
            SELECT 1 FROM atis_soe.scope_identity s
             WHERE s.catalogue_epoch=p_catalogue_epoch AND s.canonical_key=v_key
               AND s.active AND s.canonical_bytes=atis_soe.canonical_jsonb_bytes(v_scope)
        ) THEN RAISE EXCEPTION 'unknown or noncanonical scope identity'; END IF;
        IF v_ordinal > 0 AND v_key <= (
            SELECT scope_key FROM atis_soe.operation_applicable_scope
             WHERE operation_id=p_operation_id AND ordinal=v_ordinal-1
        ) THEN RAISE EXCEPTION 'scope identities are not strictly ordered'; END IF;
        INSERT INTO atis_soe.operation_applicable_scope VALUES (
            p_operation_id,v_key,v_ordinal
        );
        INSERT INTO atis_soe.scope_barrier(scope_key) VALUES(v_key)
          ON CONFLICT DO NOTHING;
        v_kinds := array_append(v_kinds, v_scope->>'kind');
        v_ordinal := v_ordinal + 1;
    END LOOP;
    IF NOT ARRAY['GLOBAL','ACCOUNT','PORTFOLIO','OPERATION'] <@ v_kinds THEN
        RAISE EXCEPTION 'mandatory applicable scopes are missing';
    END IF;
    IF EXISTS (
        WITH RECURSIVE closure(scope_key) AS (
          SELECT scope_key FROM atis_soe.operation_applicable_scope
           WHERE operation_id=p_operation_id
          UNION
          SELECT r.parent_key FROM atis_soe.scope_relation r JOIN closure c
            ON r.catalogue_epoch=p_catalogue_epoch AND r.child_key=c.scope_key
        )
        SELECT 1 FROM closure c WHERE NOT EXISTS (
          SELECT 1 FROM atis_soe.operation_applicable_scope a
           WHERE a.operation_id=p_operation_id AND a.scope_key=c.scope_key
        )
    ) THEN RAISE EXCEPTION 'applicable parent scope omitted'; END IF;
END
$function$;

CREATE FUNCTION atis_soe.begin_restriction_v2(
    p_operation_id text, p_request_digest text, p_issuer_identity text,
    p_issuer_domain text, p_scope_key text, p_ticket_sequence bigint,
    p_receipt_digest text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, atis_soe, pg_temp AS $function$
BEGIN
    PERFORM atis_soe.require_serializable();
    PERFORM atis_soe.require_member('atis_soe_publisher');
    PERFORM 1 FROM atis_soe.deployment_integrity WHERE singleton FOR SHARE;
    PERFORM 1 FROM atis_soe.scope_catalogue WHERE singleton AND state='ACTIVE' FOR SHARE;
    PERFORM 1 FROM atis_soe.scope_barrier WHERE scope_key=p_scope_key FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'unknown restriction scope'; END IF;
    INSERT INTO atis_soe.restriction_publication VALUES (
      p_operation_id,p_request_digest,p_issuer_identity,p_issuer_domain,p_scope_key,
      p_ticket_sequence,'PENDING_DENY',p_receipt_digest,pg_catalog.clock_timestamp()
    );
    UPDATE atis_soe.scope_barrier SET pending_ticket=LEAST(
      COALESCE(pending_ticket,p_ticket_sequence),p_ticket_sequence
    ) WHERE scope_key=p_scope_key;
END
$function$;

CREATE FUNCTION atis_soe.finalize_restriction_v2(
    p_operation_id text, p_request_digest text, p_issuer_identity text,
    p_terminal_state text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, atis_soe, pg_temp AS $function$
DECLARE v_r atis_soe.restriction_publication%ROWTYPE;
BEGIN
    PERFORM atis_soe.require_serializable();
    IF NOT pg_has_role(session_user,'atis_soe_publisher','MEMBER')
       AND NOT pg_has_role(session_user,'atis_soe_recovery','MEMBER') THEN
       RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='publisher or recovery role required';
    END IF;
    PERFORM 1 FROM atis_soe.deployment_integrity WHERE singleton FOR SHARE;
    PERFORM 1 FROM atis_soe.scope_catalogue WHERE singleton FOR SHARE;
    SELECT * INTO v_r FROM atis_soe.restriction_publication
      WHERE operation_id=p_operation_id;
    IF NOT FOUND OR v_r.request_digest<>p_request_digest
       OR v_r.issuer_identity<>p_issuer_identity THEN
       RAISE EXCEPTION 'restriction identity or issuer conflict';
    END IF;
    PERFORM 1 FROM atis_soe.scope_barrier WHERE scope_key=v_r.scope_key FOR UPDATE;
    PERFORM 1 FROM atis_soe.restriction_publication
      WHERE operation_id=p_operation_id FOR UPDATE;
    IF p_terminal_state NOT IN ('EFFECTIVE_DENY','RECONCILIATION_REQUIRED','TERMINAL_DENIED')
      THEN RAISE EXCEPTION 'invalid terminal restriction state'; END IF;
    UPDATE atis_soe.restriction_publication SET state=p_terminal_state,
      updated_at=pg_catalog.clock_timestamp() WHERE operation_id=p_operation_id;
    IF p_terminal_state IN ('EFFECTIVE_DENY','TERMINAL_DENIED') THEN
      UPDATE atis_soe.scope_barrier SET effective_ticket=LEAST(
        COALESCE(effective_ticket,v_r.ticket_sequence),v_r.ticket_sequence
      ) WHERE scope_key=v_r.scope_key;
    END IF;
END
$function$;

CREATE FUNCTION atis_soe.read_restriction_status_v2(
    p_operation_id text,p_request_digest text,p_issuer_identity text
) RETURNS TABLE(state text,scope_key text,ticket_sequence bigint,receipt_digest text)
LANGUAGE sql SECURITY DEFINER STABLE
SET search_path = pg_catalog, atis_soe, pg_temp AS $function$
 SELECT r.state,r.scope_key,r.ticket_sequence,r.receipt_digest
 FROM atis_soe.restriction_publication r
 WHERE r.operation_id=p_operation_id AND r.request_digest=p_request_digest
   AND r.issuer_identity=p_issuer_identity
$function$;

CREATE FUNCTION atis_soe.commit_capital_v2(
    p_operation_id text, p_request_digest text, p_amount_minor bigint,
    p_currency text, p_replay_identity text, p_ticket_sequence bigint,
    p_attempt_identity text, p_timeproof_identity text
) RETURNS TABLE (
    operation_id text, request_digest text, status text, result_code text,
    capital_effect_minor bigint, liability_effect_minor bigint
)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, atis_soe, pg_temp AS $function$
BEGIN
    PERFORM atis_soe.require_serializable();
    PERFORM atis_soe.require_member('atis_soe_runtime');
    -- U1/C013 containment is unconditional, including syntactically valid
    -- metadata and verifier registration. No caller-controlled unlock exists.
    -- Use the separately authorised lookup_outcome for historical readback.
    -- Do not consume evidence, change liabilities or manufacture DENIED rows.
    RAISE EXCEPTION USING ERRCODE = '42501',
        MESSAGE = 'U1_FINANCIAL_CONTAINMENT: new financial authority is unqualified';
END
$function$;

REVOKE ALL ON ALL TABLES IN SCHEMA atis_soe FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA atis_soe FROM PUBLIC;
GRANT USAGE ON SCHEMA atis_soe TO atis_soe_registry;
GRANT EXECUTE ON FUNCTION atis_soe.register_scope_envelope_v1(
 text,text,text,bigint,text,text,bytea) TO atis_soe_verifier;
GRANT EXECUTE ON FUNCTION atis_soe.begin_restriction_v2(
 text,text,text,text,text,bigint,text) TO atis_soe_publisher;
GRANT EXECUTE ON FUNCTION atis_soe.finalize_restriction_v2(
 text,text,text,text) TO atis_soe_publisher,atis_soe_recovery;
GRANT EXECUTE ON FUNCTION atis_soe.read_restriction_status_v2(
 text,text,text) TO atis_soe_publisher,atis_soe_recovery;
REVOKE ALL ON FUNCTION atis_soe.commit_capital_v2(
 text,text,bigint,text,text,bigint,text,text) FROM PUBLIC, atis_soe_runtime, atis_soe_verifier, atis_soe_publisher, atis_soe_recovery;

-- Harden every candidate-owned SECURITY DEFINER function, including the v1
-- compatibility surface, without relying on caller or temporary schemas.
DO $harden$
DECLARE v_function regprocedure;
BEGIN
  FOR v_function IN
    SELECT p.oid::regprocedure FROM pg_proc p
    JOIN pg_namespace n ON n.oid=p.pronamespace
    JOIN pg_roles r ON r.oid=p.proowner
    WHERE n.nspname='atis_soe' AND r.rolname='atis_soe_owner' AND p.prosecdef
  LOOP
    EXECUTE format(
      'ALTER FUNCTION %s SET search_path = pg_catalog, atis_soe, pg_temp',
      v_function
    );
  END LOOP;
END
$harden$;

ALTER DEFAULT PRIVILEGES FOR ROLE atis_soe_owner IN SCHEMA atis_soe
  REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE atis_soe_owner IN SCHEMA atis_soe
  REVOKE ALL ON FUNCTIONS FROM PUBLIC;

RESET ROLE;
COMMIT;
