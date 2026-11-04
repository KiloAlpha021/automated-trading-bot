BEGIN;

DO $roles$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atis_soe_owner') THEN
        CREATE ROLE atis_soe_owner NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atis_soe_runtime') THEN
        CREATE ROLE atis_soe_runtime NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atis_soe_publisher') THEN
        CREATE ROLE atis_soe_publisher NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atis_soe_verifier') THEN
        CREATE ROLE atis_soe_verifier NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atis_soe_recovery') THEN
        CREATE ROLE atis_soe_recovery NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atis_soe_audit') THEN
        CREATE ROLE atis_soe_audit NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atis_soe_migrator') THEN
        CREATE ROLE atis_soe_migrator NOLOGIN NOINHERIT;
    END IF;
END
$roles$;

GRANT atis_soe_owner TO atis_soe_migrator;

CREATE SCHEMA atis_soe AUTHORIZATION atis_soe_owner;
REVOKE ALL ON SCHEMA atis_soe FROM PUBLIC;
GRANT USAGE ON SCHEMA atis_soe TO
    atis_soe_runtime, atis_soe_publisher, atis_soe_verifier,
    atis_soe_recovery, atis_soe_audit, atis_soe_migrator;

SET ROLE atis_soe_owner;
SET search_path = atis_soe, pg_catalog;

CREATE TABLE scope_control (
    scope text PRIMARY KEY,
    generation bigint NOT NULL CHECK (generation >= 0),
    issuer_epoch bigint NOT NULL CHECK (issuer_epoch >= 0),
    next_ticket bigint NOT NULL CHECK (next_ticket >= 0),
    pending_cutover bigint,
    effective_cutover bigint,
    CHECK (pending_cutover IS NULL OR pending_cutover >= 0),
    CHECK (effective_cutover IS NULL OR effective_cutover >= 0)
);

CREATE TABLE identity_policy (
    policy_name text PRIMARY KEY CHECK (policy_name = 'ACTIVE'),
    policy_version bigint NOT NULL CHECK (policy_version > 0),
    issuer_identity text NOT NULL,
    deployment_identity text NOT NULL,
    recipient text NOT NULL,
    authority_identity text NOT NULL,
    generation bigint NOT NULL CHECK (generation >= 0),
    issuer_epoch bigint NOT NULL CHECK (issuer_epoch >= 0),
    authority_max_age_ns bigint NOT NULL CHECK (authority_max_age_ns > 0),
    timeproof_max_age_ns bigint NOT NULL CHECK (timeproof_max_age_ns > 0),
    future_skew_ns bigint NOT NULL CHECK (future_skew_ns >= 0),
    revoked boolean NOT NULL DEFAULT false,
    UNIQUE (policy_version)
);

CREATE TABLE g00_attestation (
    replay_identity text PRIMARY KEY,
    verification_digest text NOT NULL UNIQUE,
    issuer_identity text NOT NULL,
    deployment_identity text NOT NULL,
    recipient text NOT NULL,
    operation_id text NOT NULL,
    request_digest text NOT NULL,
    request_type text NOT NULL,
    amount_minor bigint NOT NULL CHECK (amount_minor >= 0),
    currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    scope text NOT NULL REFERENCES scope_control(scope),
    generation bigint NOT NULL CHECK (generation >= 0),
    issuer_epoch bigint NOT NULL CHECK (issuer_epoch >= 0),
    ticket_sequence bigint NOT NULL CHECK (ticket_sequence >= 0),
    observed_cutover_sequence bigint,
    policy_version bigint NOT NULL,
    valid_until timestamptz NOT NULL,
    consumed boolean NOT NULL DEFAULT false,
    registered_by text NOT NULL,
    CHECK (observed_cutover_sequence IS NULL OR observed_cutover_sequence >= 0)
);

CREATE TABLE attempt_evidence (
    attempt_identity text PRIMARY KEY,
    verification_digest text NOT NULL UNIQUE,
    operation_id text NOT NULL,
    request_digest text NOT NULL,
    scope text NOT NULL REFERENCES scope_control(scope),
    generation bigint NOT NULL CHECK (generation >= 0),
    issuer_epoch bigint NOT NULL CHECK (issuer_epoch >= 0),
    authority_identity text NOT NULL,
    timeproof_identity text NOT NULL,
    authority_issued_ns bigint NOT NULL CHECK (authority_issued_ns >= 0),
    timeproof_issued_ns bigint NOT NULL CHECK (timeproof_issued_ns >= 0),
    verified_at_ns bigint NOT NULL CHECK (verified_at_ns >= 0),
    policy_version bigint NOT NULL,
    consumed boolean NOT NULL DEFAULT false,
    registered_by text NOT NULL,
    registered_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE identity_policy_history (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    policy_version bigint NOT NULL,
    action text NOT NULL CHECK (action IN ('INSTALL','REVOKE')),
    policy_digest text NOT NULL,
    actor text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE identity_policy_version_registry (
    policy_version bigint PRIMARY KEY,
    policy_digest text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE revoked_identity_context (
    issuer_identity text NOT NULL,
    deployment_identity text NOT NULL,
    recipient text NOT NULL,
    authority_identity text NOT NULL,
    generation bigint NOT NULL,
    issuer_epoch bigint NOT NULL,
    revoked_policy_version bigint NOT NULL,
    PRIMARY KEY (
        issuer_identity, deployment_identity, recipient, authority_identity,
        generation, issuer_epoch
    )
);

CREATE FUNCTION audit_identity_policy_transition() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = atis_soe, pg_catalog AS $function$
BEGIN
    INSERT INTO identity_policy_history(policy_version,action,policy_digest,actor)
    VALUES (NEW.policy_version, CASE WHEN NEW.revoked THEN 'REVOKE' ELSE 'INSTALL' END,
            md5(row_to_json(NEW)::text), session_user);
    RETURN NEW;
END
$function$;

CREATE TRIGGER identity_policy_audit
AFTER INSERT OR UPDATE ON identity_policy
FOR EACH ROW EXECUTE FUNCTION audit_identity_policy_transition();

CREATE FUNCTION transition_identity_policy(
    p_policy_version bigint, p_issuer text, p_deployment text, p_recipient text,
    p_authority text, p_generation bigint, p_epoch bigint,
    p_authority_max_age_ns bigint, p_timeproof_max_age_ns bigint,
    p_future_skew_ns bigint, p_revoked boolean
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = atis_soe, pg_catalog AS $function$
BEGIN
    PERFORM require_serializable();
    PERFORM require_member('atis_soe_migrator');
    IF p_policy_version <= 0 OR p_authority_max_age_ns <= 0 OR p_timeproof_max_age_ns <= 0
       OR p_future_skew_ns < 0
       OR p_issuer='' OR p_deployment='' OR p_recipient='' OR p_authority='' THEN
        RAISE EXCEPTION 'invalid identity policy transition';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('atis_soe.identity_policy', 0));
    IF EXISTS (SELECT 1 FROM identity_policy WHERE policy_version >= p_policy_version) THEN
        RAISE EXCEPTION 'identity policy version must increase monotonically';
    END IF;
    IF NOT p_revoked AND EXISTS (
        SELECT 1 FROM revoked_identity_context r
         WHERE (r.issuer_identity, r.deployment_identity, r.recipient,
                r.authority_identity, r.generation, r.issuer_epoch)
               = (p_issuer, p_deployment, p_recipient, p_authority,
                  p_generation, p_epoch)
    ) THEN
        RAISE EXCEPTION 'revoked identity context cannot be reactivated';
    END IF;
    INSERT INTO identity_policy_version_registry(policy_version, policy_digest)
    VALUES (
        p_policy_version,
        md5(concat_ws('|',p_issuer,p_deployment,p_recipient,p_authority,
                      p_generation::text,p_epoch::text,p_future_skew_ns::text,
                      p_revoked::text))
    );
    INSERT INTO identity_policy VALUES (
        'ACTIVE',p_policy_version,p_issuer,p_deployment,p_recipient,p_authority,
        p_generation,p_epoch,p_authority_max_age_ns,p_timeproof_max_age_ns,
        p_future_skew_ns,p_revoked
    ) ON CONFLICT(policy_name) DO UPDATE SET
        policy_version=excluded.policy_version, issuer_identity=excluded.issuer_identity,
        deployment_identity=excluded.deployment_identity, recipient=excluded.recipient,
        authority_identity=excluded.authority_identity, generation=excluded.generation,
        issuer_epoch=excluded.issuer_epoch,
        authority_max_age_ns=excluded.authority_max_age_ns,
        timeproof_max_age_ns=excluded.timeproof_max_age_ns,
        future_skew_ns=excluded.future_skew_ns, revoked=excluded.revoked;
    IF p_revoked THEN
        INSERT INTO revoked_identity_context VALUES (
            p_issuer,p_deployment,p_recipient,p_authority,p_generation,p_epoch,
            p_policy_version
        ) ON CONFLICT DO NOTHING;
    END IF;
END
$function$;

CREATE TABLE restriction_request (
    operation_id text PRIMARY KEY,
    request_digest text NOT NULL,
    scope text NOT NULL REFERENCES scope_control(scope),
    issuer_identity text NOT NULL,
    deployment_identity text NOT NULL,
    generation bigint NOT NULL,
    issuer_epoch bigint NOT NULL,
    ticket_sequence bigint NOT NULL,
    replay_identity text NOT NULL UNIQUE REFERENCES g00_attestation(replay_identity),
    state text NOT NULL CHECK (state IN (
        'PENDING', 'EFFECTIVE', 'RECONCILIATION_REQUIRED', 'TERMINAL_DENIED'
    )),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE operation_outcome (
    operation_id text PRIMARY KEY,
    request_digest text NOT NULL,
    scope text NOT NULL,
    status text NOT NULL CHECK (status IN ('COMMITTED', 'DENIED')),
    result_code text NOT NULL,
    capital_effect_minor bigint NOT NULL CHECK (capital_effect_minor >= 0),
    liability_effect_minor bigint NOT NULL CHECK (liability_effect_minor >= 0),
    replay_identity text NOT NULL,
    ticket_sequence bigint NOT NULL,
    committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (
        status = 'COMMITTED'
        OR (capital_effect_minor = 0 AND liability_effect_minor = 0)
    ),
    CHECK (capital_effect_minor = liability_effect_minor)
);

CREATE TABLE capital_state (
    scope text PRIMARY KEY REFERENCES scope_control(scope),
    reserved_minor bigint NOT NULL DEFAULT 0 CHECK (reserved_minor >= 0)
);

CREATE TABLE liability (
    operation_id text PRIMARY KEY REFERENCES operation_outcome(operation_id),
    amount_minor bigint NOT NULL CHECK (amount_minor > 0),
    status text NOT NULL CHECK (status = 'OUTSTANDING')
);

CREATE TABLE audit_event (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type text NOT NULL,
    operation_id text NOT NULL,
    request_digest text NOT NULL,
    session_identity text NOT NULL,
    detail text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE FUNCTION require_serializable() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = atis_soe, pg_catalog AS $function$
BEGIN
    IF current_setting('transaction_isolation') <> 'serializable' THEN
        RAISE EXCEPTION USING
            ERRCODE = '25001',
            MESSAGE = 'protected mutation requires SERIALIZABLE transaction';
    END IF;
END
$function$;

CREATE FUNCTION require_member(p_role name) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = atis_soe, pg_catalog AS $function$
BEGIN
    IF NOT pg_has_role(session_user, p_role, 'MEMBER') THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'session identity is not authorised for protected operation';
    END IF;
END
$function$;

CREATE FUNCTION register_g00_attestation(
    p_replay_identity text,
    p_verification_digest text,
    p_issuer_identity text,
    p_deployment_identity text,
    p_recipient text,
    p_operation_id text,
    p_request_digest text,
    p_request_type text,
    p_amount_minor bigint,
    p_currency text,
    p_scope text,
    p_generation bigint,
    p_issuer_epoch bigint,
    p_ticket_sequence bigint,
    p_observed_cutover_sequence bigint,
    p_valid_until timestamptz,
    p_policy_version bigint
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = atis_soe, pg_catalog AS $function$
BEGIN
    PERFORM require_serializable();
    PERFORM require_member('atis_soe_verifier');
    IF p_valid_until <= clock_timestamp() THEN
        RAISE EXCEPTION 'expired G00 attestation';
    END IF;
    IF p_replay_identity = '' OR p_verification_digest = ''
       OR p_issuer_identity = '' OR p_deployment_identity = ''
       OR p_recipient = '' OR p_operation_id = '' OR p_request_digest = ''
       OR p_request_type = '' OR p_amount_minor < 0
       OR p_currency !~ '^[A-Z]{3}$' OR p_scope = '' THEN
        RAISE EXCEPTION 'empty G00 attestation field';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM identity_policy p
         WHERE p.policy_name = 'ACTIVE' AND NOT p.revoked
           AND (p.policy_version, p.issuer_identity, p.deployment_identity,
                p.recipient, p.generation, p.issuer_epoch)
               = (p_policy_version, p_issuer_identity, p_deployment_identity,
                  p_recipient, p_generation, p_issuer_epoch)
    ) THEN
        RAISE EXCEPTION 'G00 identity policy is missing, stale, revoked or foreign';
    END IF;
    INSERT INTO g00_attestation (
        replay_identity, verification_digest, issuer_identity,
        deployment_identity, recipient, operation_id, request_digest,
        request_type, amount_minor, currency, scope,
        generation, issuer_epoch, ticket_sequence, observed_cutover_sequence,
        valid_until, policy_version, registered_by
    ) VALUES (
        p_replay_identity, p_verification_digest, p_issuer_identity,
        p_deployment_identity, p_recipient, p_operation_id, p_request_digest,
        p_request_type, p_amount_minor, p_currency, p_scope, p_generation,
        p_issuer_epoch, p_ticket_sequence, p_observed_cutover_sequence,
        p_valid_until, p_policy_version, session_user
    ) ON CONFLICT (replay_identity) DO NOTHING;
    IF NOT EXISTS (
        SELECT 1 FROM g00_attestation a
         WHERE (a.replay_identity, a.verification_digest, a.issuer_identity,
                a.deployment_identity, a.recipient, a.operation_id,
                a.request_digest, a.request_type, a.amount_minor, a.currency,
                a.scope, a.generation, a.issuer_epoch, a.ticket_sequence,
                a.observed_cutover_sequence, a.policy_version)
               IS NOT DISTINCT FROM
               (p_replay_identity, p_verification_digest, p_issuer_identity,
                p_deployment_identity, p_recipient, p_operation_id,
                p_request_digest, p_request_type, p_amount_minor, p_currency,
                p_scope, p_generation, p_issuer_epoch, p_ticket_sequence,
                p_observed_cutover_sequence, p_policy_version)
    ) THEN
        RAISE EXCEPTION 'conflicting G00 attestation registration';
    END IF;
END
$function$;

CREATE FUNCTION register_attempt_evidence(
    p_attempt_identity text,
    p_verification_digest text,
    p_operation_id text,
    p_request_digest text,
    p_scope text,
    p_generation bigint,
    p_issuer_epoch bigint,
    p_authority_identity text,
    p_timeproof_identity text,
    p_authority_issued_ns bigint,
    p_timeproof_issued_ns bigint,
    p_verified_at_ns bigint,
    p_policy_version bigint
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = atis_soe, pg_catalog AS $function$
DECLARE
    v_now_ns bigint;
BEGIN
    PERFORM require_serializable();
    PERFORM require_member('atis_soe_verifier');
    IF p_attempt_identity = '' OR p_verification_digest = ''
       OR p_operation_id = '' OR p_request_digest = '' OR p_scope = ''
       OR p_authority_identity = '' OR p_timeproof_identity = '' THEN
        RAISE EXCEPTION 'empty attempt evidence field';
    END IF;
    v_now_ns := floor(extract(epoch FROM clock_timestamp()) * 1000000000)::bigint;
    IF NOT EXISTS (
        SELECT 1 FROM identity_policy p WHERE p.policy_name='ACTIVE'
          AND NOT p.revoked AND p.policy_version=p_policy_version
          AND p.authority_identity=p_authority_identity
          AND p.generation=p_generation AND p.issuer_epoch=p_issuer_epoch
          AND p_authority_issued_ns <= v_now_ns+p.future_skew_ns
          AND p_timeproof_issued_ns <= v_now_ns+p.future_skew_ns
          AND v_now_ns-p_authority_issued_ns <= p.authority_max_age_ns
          AND v_now_ns-p_timeproof_issued_ns <= p.timeproof_max_age_ns
    ) THEN
        RAISE EXCEPTION 'stale, future or foreign attempt evidence';
    END IF;
    INSERT INTO attempt_evidence (
        attempt_identity, verification_digest, operation_id, request_digest,
        scope, generation, issuer_epoch, authority_identity,
        timeproof_identity, authority_issued_ns, timeproof_issued_ns,
        verified_at_ns, policy_version,
        registered_by
    ) VALUES (
        p_attempt_identity, p_verification_digest, p_operation_id,
        p_request_digest, p_scope, p_generation, p_issuer_epoch,
        p_authority_identity, p_timeproof_identity, p_authority_issued_ns,
        p_timeproof_issued_ns, v_now_ns, p_policy_version, session_user
    ) ON CONFLICT (attempt_identity) DO NOTHING;
    IF NOT EXISTS (
        SELECT 1 FROM attempt_evidence e
         WHERE (e.attempt_identity, e.verification_digest, e.operation_id,
                e.request_digest, e.scope, e.generation, e.issuer_epoch,
                e.authority_identity, e.timeproof_identity,
                 e.authority_issued_ns, e.timeproof_issued_ns, e.policy_version)
               = (p_attempt_identity, p_verification_digest, p_operation_id,
                  p_request_digest, p_scope, p_generation, p_issuer_epoch,
                  p_authority_identity, p_timeproof_identity,
                   p_authority_issued_ns, p_timeproof_issued_ns, p_policy_version)
    ) THEN
        RAISE EXCEPTION 'conflicting attempt evidence registration';
    END IF;
END
$function$;

CREATE FUNCTION assert_admission_attestation(
    p_replay_identity text,
    p_operation_id text,
    p_request_digest text,
    p_scope text,
    p_generation bigint,
    p_issuer_epoch bigint,
    p_ticket_sequence bigint,
    p_request_type text,
    p_amount_minor bigint,
    p_currency text,
    p_attempt_identity text,
    p_timeproof_identity text
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = atis_soe, pg_catalog AS $function$
DECLARE
    a g00_attestation%ROWTYPE;
    e attempt_evidence%ROWTYPE;
    c scope_control%ROWTYPE;
    v_now_ns bigint;
BEGIN
    PERFORM require_serializable();
    PERFORM require_member('atis_soe_runtime');
    SELECT * INTO a FROM g00_attestation
      WHERE replay_identity = p_replay_identity FOR UPDATE;
    IF NOT FOUND OR a.consumed OR a.valid_until <= clock_timestamp()
       OR (a.operation_id, a.request_digest, a.scope, a.generation,
           a.issuer_epoch, a.ticket_sequence, a.request_type,
           a.amount_minor, a.currency)
          IS DISTINCT FROM
          (p_operation_id, p_request_digest, p_scope, p_generation,
           p_issuer_epoch, p_ticket_sequence, p_request_type,
           p_amount_minor, p_currency) THEN
        RAISE EXCEPTION 'invalid or foreign G00 attestation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM identity_policy p
         WHERE p.policy_name = 'ACTIVE' AND NOT p.revoked
           AND (p.policy_version, p.issuer_identity, p.deployment_identity,
                p.recipient, p.generation, p.issuer_epoch)
               = (a.policy_version, a.issuer_identity, a.deployment_identity,
                  a.recipient, a.generation, a.issuer_epoch)
    ) THEN
        RAISE EXCEPTION 'G00 identity policy is stale, revoked or foreign';
    END IF;
    SELECT * INTO e FROM attempt_evidence
      WHERE attempt_identity = p_attempt_identity FOR UPDATE;
    IF NOT FOUND OR e.consumed
       OR (e.operation_id, e.request_digest, e.scope, e.generation,
           e.issuer_epoch, e.timeproof_identity)
          IS DISTINCT FROM
          (p_operation_id, p_request_digest, p_scope, p_generation,
           p_issuer_epoch, p_timeproof_identity) THEN
        RAISE EXCEPTION 'invalid or foreign attempt evidence';
    END IF;
    v_now_ns := floor(extract(epoch FROM clock_timestamp()) * 1000000000)::bigint;
    IF NOT EXISTS (
        SELECT 1 FROM identity_policy p WHERE p.policy_name='ACTIVE'
          AND NOT p.revoked AND p.policy_version=e.policy_version
          AND p.authority_identity=e.authority_identity
          AND e.authority_issued_ns <= v_now_ns+p.future_skew_ns
          AND e.timeproof_issued_ns <= v_now_ns+p.future_skew_ns
          AND v_now_ns-e.authority_issued_ns <= p.authority_max_age_ns
          AND v_now_ns-e.timeproof_issued_ns <= p.timeproof_max_age_ns
    ) THEN
        RAISE EXCEPTION 'attempt evidence policy is stale or revoked';
    END IF;
    SELECT * INTO c FROM scope_control WHERE scope = p_scope FOR SHARE;
    IF NOT FOUND OR c.generation <> p_generation OR c.issuer_epoch <> p_issuer_epoch
       OR a.observed_cutover_sequence IS DISTINCT FROM c.effective_cutover THEN
        RAISE EXCEPTION 'stale G00 authority or cutover binding';
    END IF;
    RETURN a.verification_digest;
END
$function$;

CREATE FUNCTION begin_restriction(
    p_replay_identity text,
    p_operation_id text,
    p_request_digest text,
    p_target_state text DEFAULT 'PENDING'
) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = atis_soe, pg_catalog AS $function$
DECLARE
    a g00_attestation%ROWTYPE;
    c scope_control%ROWTYPE;
BEGIN
    PERFORM require_serializable();
    PERFORM require_member('atis_soe_publisher');
    IF p_target_state <> 'PENDING' THEN
        RAISE EXCEPTION 'restriction must begin PENDING';
    END IF;
    SELECT * INTO a FROM g00_attestation
      WHERE replay_identity = p_replay_identity FOR UPDATE;
    IF NOT FOUND OR a.consumed OR a.valid_until <= clock_timestamp()
       OR a.operation_id <> p_operation_id
       OR a.request_digest <> p_request_digest
       OR a.request_type <> 'RESTRICTION' THEN
        RAISE EXCEPTION 'invalid restriction attestation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM identity_policy p
         WHERE p.policy_name = 'ACTIVE' AND NOT p.revoked
           AND (p.policy_version, p.issuer_identity, p.deployment_identity,
                p.recipient, p.generation, p.issuer_epoch)
               = (a.policy_version, a.issuer_identity, a.deployment_identity,
                  a.recipient, a.generation, a.issuer_epoch)
    ) THEN
        RAISE EXCEPTION 'restriction identity policy is stale, revoked or foreign';
    END IF;
    SELECT * INTO c FROM scope_control WHERE scope = a.scope FOR UPDATE;
    IF NOT FOUND OR c.generation <> a.generation OR c.issuer_epoch <> a.issuer_epoch
       OR a.observed_cutover_sequence IS DISTINCT FROM c.effective_cutover THEN
        RAISE EXCEPTION 'stale restriction authority or cutover binding';
    END IF;
    INSERT INTO restriction_request (
        operation_id, request_digest, scope, issuer_identity,
        deployment_identity, generation, issuer_epoch, ticket_sequence,
        replay_identity, state
    ) VALUES (
        a.operation_id, a.request_digest, a.scope, a.issuer_identity,
        a.deployment_identity, a.generation, a.issuer_epoch, a.ticket_sequence,
        a.replay_identity, 'PENDING'
    );
    UPDATE g00_attestation SET consumed = true
      WHERE replay_identity = a.replay_identity;
    UPDATE scope_control
       SET pending_cutover = LEAST(
           COALESCE(pending_cutover, a.ticket_sequence), a.ticket_sequence
       )
     WHERE scope = a.scope;
    INSERT INTO audit_event (
        event_type, operation_id, request_digest, session_identity, detail
    ) VALUES (
        'RESTRICTION_PENDING', a.operation_id, a.request_digest,
        session_user, a.scope
    );
    RETURN a.ticket_sequence;
END
$function$;

CREATE FUNCTION finalize_restriction(
    p_operation_id text,
    p_request_digest text,
    p_state text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = atis_soe, pg_catalog AS $function$
DECLARE
    r restriction_request%ROWTYPE;
    next_pending bigint;
BEGIN
    PERFORM require_serializable();
    IF NOT pg_has_role(session_user, 'atis_soe_publisher', 'MEMBER')
       AND NOT pg_has_role(session_user, 'atis_soe_recovery', 'MEMBER') THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'publisher or recovery role required';
    END IF;
    IF p_state NOT IN ('EFFECTIVE', 'RECONCILIATION_REQUIRED', 'TERMINAL_DENIED') THEN
        RAISE EXCEPTION 'invalid restriction terminal state';
    END IF;
    SELECT * INTO r FROM restriction_request
      WHERE operation_id = p_operation_id FOR UPDATE;
    IF NOT FOUND OR r.request_digest <> p_request_digest THEN
        RAISE EXCEPTION 'restriction identity conflict';
    END IF;
    PERFORM 1 FROM scope_control WHERE scope = r.scope FOR UPDATE;
    UPDATE restriction_request SET state = p_state, updated_at = clock_timestamp()
      WHERE operation_id = r.operation_id;
    IF p_state IN ('EFFECTIVE', 'TERMINAL_DENIED') THEN
        UPDATE scope_control
           SET effective_cutover = LEAST(
               COALESCE(effective_cutover, r.ticket_sequence), r.ticket_sequence
           )
         WHERE scope = r.scope;
    END IF;
    SELECT min(ticket_sequence) INTO next_pending FROM restriction_request
      WHERE scope = r.scope AND state IN ('PENDING', 'RECONCILIATION_REQUIRED');
    UPDATE scope_control SET pending_cutover = next_pending WHERE scope = r.scope;
    INSERT INTO audit_event (
        event_type, operation_id, request_digest, session_identity, detail
    ) VALUES (
        'RESTRICTION_' || p_state, r.operation_id, r.request_digest,
        session_user, r.scope
    );
END
$function$;

CREATE FUNCTION lookup_outcome(p_operation_id text, p_request_digest text)
RETURNS TABLE (
    operation_id text, request_digest text, status text, result_code text,
    capital_effect_minor bigint, liability_effect_minor bigint
)
LANGUAGE plpgsql SECURITY DEFINER STABLE
SET search_path = atis_soe, pg_catalog AS $function$
BEGIN
    IF NOT pg_has_role(session_user, 'atis_soe_runtime', 'MEMBER')
       AND NOT pg_has_role(session_user, 'atis_soe_recovery', 'MEMBER') THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'runtime or recovery role required';
    END IF;
    IF EXISTS (
        SELECT 1 FROM operation_outcome o
         WHERE o.operation_id = p_operation_id AND o.request_digest <> p_request_digest
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '22000', MESSAGE = 'operation digest conflict';
    END IF;
    RETURN QUERY
      SELECT o.operation_id, o.request_digest, o.status, o.result_code,
             o.capital_effect_minor, o.liability_effect_minor
        FROM operation_outcome o
       WHERE o.operation_id = p_operation_id AND o.request_digest = p_request_digest;
END
$function$;

CREATE FUNCTION commit_capital(
    p_operation_id text,
    p_request_digest text,
    p_scope text,
    p_amount_minor bigint,
    p_currency text,
    p_generation bigint,
    p_issuer_epoch bigint,
    p_replay_identity text,
    p_ticket_sequence bigint,
    p_attempt_identity text,
    p_timeproof_identity text
) RETURNS TABLE (
    operation_id text, request_digest text, status text, result_code text,
    capital_effect_minor bigint, liability_effect_minor bigint
)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = atis_soe, pg_catalog AS $function$
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

CREATE VIEW audit_view AS
SELECT sequence, event_type, operation_id, request_digest, session_identity,
       detail, recorded_at
  FROM audit_event;

REVOKE ALL ON ALL TABLES IN SCHEMA atis_soe FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA atis_soe FROM PUBLIC;
GRANT EXECUTE ON FUNCTION transition_identity_policy(
    bigint,text,text,text,text,bigint,bigint,bigint,bigint,bigint,boolean
) TO atis_soe_migrator;
GRANT EXECUTE ON FUNCTION register_g00_attestation(
    text,text,text,text,text,text,text,text,bigint,text,text,bigint,bigint,
    bigint,bigint,timestamptz,bigint
) TO atis_soe_verifier;
GRANT EXECUTE ON FUNCTION register_attempt_evidence(
    text,text,text,text,text,bigint,bigint,text,text,bigint,bigint,bigint,bigint
) TO atis_soe_verifier;
GRANT EXECUTE ON FUNCTION begin_restriction(text,text,text,text) TO atis_soe_publisher;
GRANT EXECUTE ON FUNCTION finalize_restriction(text,text,text)
  TO atis_soe_publisher, atis_soe_recovery;
GRANT EXECUTE ON FUNCTION assert_admission_attestation(
    text,text,text,text,bigint,bigint,bigint,text,bigint,text,text,text
) TO atis_soe_runtime;
REVOKE ALL ON FUNCTION commit_capital(
    text,text,text,bigint,text,bigint,bigint,text,bigint,text,text
) FROM PUBLIC, atis_soe_runtime, atis_soe_verifier, atis_soe_publisher, atis_soe_recovery;
GRANT EXECUTE ON FUNCTION lookup_outcome(text,text)
  TO atis_soe_runtime, atis_soe_recovery;
GRANT SELECT ON audit_view TO atis_soe_audit;

RESET ROLE;
COMMIT;
