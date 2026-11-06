-- Task045 Checkpoint A ONLY: explicit verification-foundation installation.
-- No registry activation, registration, L08 creation or financial grant.
-- Install after authenticated SQL1/SQL2 in a disposable qualification database.
-- No IF NOT EXISTS on protected state: missing state is never silently repaired.
BEGIN;
CREATE EXTENSION IF NOT EXISTS plpython3u;
DO $roles$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='atis_soe_registry_owner') THEN
    CREATE ROLE atis_soe_registry_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='atis_soe_registry_migrator') THEN
    CREATE ROLE atis_soe_registry_migrator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname IN
    ('atis_soe_owner','atis_soe_registry_owner','atis_soe_registry_migrator')
    AND (rolsuper OR rolcanlogin OR rolcreaterole OR rolcreatedb OR rolbypassrls)) THEN
    RAISE EXCEPTION 'U3 installation rejects unsafe protected role attributes';
  END IF;
END
$roles$;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA atis_soe FROM PUBLIC;
GRANT USAGE ON SCHEMA atis_soe TO atis_soe_registry_owner, atis_soe_registry_migrator;
ALTER DEFAULT PRIVILEGES FOR ROLE atis_soe_registry_owner IN SCHEMA atis_soe
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

CREATE TABLE atis_soe.u3_verification_policy (
  capability text NOT NULL CHECK (capability IN ('ACCOUNT','PORTFOLIO','SCOPE','REGISTRY_TRANSITION')),
  producer text NOT NULL CHECK (producer<>''),
  deployment text NOT NULL CHECK (deployment<>''),
  key_id text NOT NULL CHECK (key_id<>''),
  key_version bigint NOT NULL CHECK (key_version>0),
  policy_version bigint NOT NULL CHECK (policy_version>0),
  public_blob bytea NOT NULL CHECK (octet_length(public_blob)=72),
  valid_from timestamptz NOT NULL,
  valid_until timestamptz NOT NULL CHECK (valid_until>valid_from),
  revoked boolean NOT NULL,
  PRIMARY KEY(capability,producer,deployment,key_id,key_version,policy_version)
);
ALTER TABLE atis_soe.u3_verification_policy OWNER TO atis_soe_registry_owner;
CREATE TABLE atis_soe.u3_verifier_configuration (
  singleton boolean PRIMARY KEY CHECK(singleton),
  python_paths text[] NOT NULL CHECK(cardinality(python_paths)=3),
  module_hashes jsonb NOT NULL CHECK(jsonb_typeof(module_hashes)='object')
);
ALTER TABLE atis_soe.u3_verifier_configuration OWNER TO atis_soe_registry_owner;
REVOKE ALL ON atis_soe.u3_verification_policy,atis_soe.u3_verifier_configuration FROM PUBLIC;
GRANT SELECT ON atis_soe.u3_verification_policy,atis_soe.u3_verifier_configuration TO atis_soe_owner;

CREATE FUNCTION atis_soe.u3_private_verify(capability text,context_bytes bytea,evidence_bytes bytea)
RETURNS text LANGUAGE plpython3u SECURITY INVOKER
SET search_path=pg_catalog,atis_soe,pg_temp
AS $python$
import sys, hashlib, json, os
try:
    rows=plpy.execute("SELECT python_paths,module_hashes FROM atis_soe.u3_verifier_configuration WHERE singleton")
    if len(rows)!=1: return 'UNAVAILABLE'
    paths=list(rows[0]['python_paths'])
    hashes=rows[0]['module_hashes']
    if isinstance(hashes,str): hashes=json.loads(hashes)
    if not hashes or len(paths)!=3: return 'UNAVAILABLE'
    root=os.path.realpath(paths[2])
    required=os.path.join(root,'automated_trading_bot','stage5','commit_boundary','postgres_verification.py')
    normalized={os.path.normcase(os.path.realpath(p)):digest for p,digest in hashes.items()}
    if os.path.normcase(required) not in normalized: return 'UNAVAILABLE'
    for path,expected in hashes.items():
        if os.path.commonpath([root,os.path.realpath(path)])!=root: return 'UNAVAILABLE'
        with open(path,'rb') as stream:
            if hashlib.sha256(stream.read()).hexdigest()!=expected: return 'UNAVAILABLE'
    sys.path=paths
    sys.dont_write_bytecode=True
    # Reject a previously loaded application module from a different bundle.
    for name,module in tuple(sys.modules.items()):
        if name=='automated_trading_bot' or name.startswith('automated_trading_bot.'):
            path=getattr(module,'__file__',None)
            if not path or os.path.normcase(os.path.realpath(path)) not in normalized:
                return 'UNAVAILABLE'
    from datetime import datetime
    from automated_trading_bot.stage5.commit_boundary.postgres_verification import verify_signature,policies_from_rows
    from automated_trading_bot.stage5.identity_scope import IdentityScopeError
    policies=policies_from_rows(plpy.execute("SELECT * FROM atis_soe.u3_verification_policy"))
    now=datetime.fromisoformat(str(plpy.execute("SELECT pg_catalog.clock_timestamp() AS now")[0]['now']))
    try:
        result=verify_signature(capability,bytes(context_bytes),bytes(evidence_bytes),policies,now)
    except IdentityScopeError as exc:
        # Fixed semantic classifications only; do not return raw native exceptions.
        known={'U3_CONTEXT_REJECTED','U3_CAPABILITY_REJECTED','U3_VALIDITY_REJECTED','U3_POLICY_REJECTED','U3_SIGNATURE_REJECTED'}
        return str(exc) if str(exc) in known else 'U3_CANONICAL_REJECTED'
    # Native calls are not forcibly cancellable. Observe pending interrupts
    # before returning any successful verification result (Task043 correction).
    plpy.execute("SELECT 1")
    return result
except Exception as exc:
    if getattr(exc,'sqlstate',None) in ('57014','40001','40P01','55P03'): raise
    return 'UNAVAILABLE'
$python$;
ALTER FUNCTION atis_soe.u3_private_verify(text,bytea,bytea) OWNER TO atis_soe_owner;
REVOKE ALL ON FUNCTION atis_soe.u3_private_verify(text,bytea,bytea) FROM PUBLIC;
CREATE FUNCTION atis_soe.verify_u3_signature(capability text,context_bytes bytea,evidence_bytes bytea)
RETURNS text LANGUAGE sql SECURITY DEFINER
SET search_path=pg_catalog,atis_soe,pg_temp
AS $sql$
  SELECT atis_soe.u3_private_verify($1,$2,$3)
$sql$;
ALTER FUNCTION atis_soe.verify_u3_signature(text,bytea,bytea) OWNER TO atis_soe_owner;
REVOKE ALL ON FUNCTION atis_soe.verify_u3_signature(text,bytea,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION atis_soe.verify_u3_signature(text,bytea,bytea)
  TO atis_soe_verifier,atis_soe_registry_migrator;
COMMENT ON FUNCTION atis_soe.verify_u3_signature(text,bytea,bytea) IS
  'Signature-only foundation. Not lifecycle/currentness, registry, admission or financial authority.';
COMMIT;

-- Checkpoint B: fresh-schema registry. The Checkpoint A foundation above is
-- byte-preserved. No existing-state migration or Checkpoint C is implemented.
BEGIN;
DO $legacy$
BEGIN
  IF EXISTS(SELECT 1 FROM atis_soe.scope_identity)
     OR EXISTS(SELECT 1 FROM atis_soe.scope_relation)
     OR EXISTS(SELECT 1 FROM atis_soe.scope_barrier)
     OR EXISTS(SELECT 1 FROM atis_soe.operation_scope_registration)
     OR EXISTS(SELECT 1 FROM atis_soe.restriction_publication) THEN
    RAISE EXCEPTION 'U3_UNSUPPORTED_NONEMPTY_LEGACY_MIGRATION';
  END IF;
END
$legacy$;
-- Retain unsupported legacy bytes/objects without promoting them to authority.
ALTER TABLE atis_soe.scope_identity RENAME TO legacy_scope_identity;
ALTER TABLE atis_soe.scope_relation RENAME TO legacy_scope_relation;
ALTER TABLE atis_soe.scope_barrier RENAME TO legacy_scope_barrier;
REVOKE ALL ON FUNCTION atis_soe.register_scope_envelope_v1(text,text,text,bigint,text,text,bytea)
  FROM atis_soe_registry,atis_soe_verifier,PUBLIC;
GRANT USAGE,CREATE ON SCHEMA atis_soe TO atis_soe_registry_owner;
GRANT SELECT,UPDATE ON atis_soe.deployment_integrity TO atis_soe_registry_owner;
GRANT EXECUTE ON FUNCTION atis_soe.verify_u3_signature(text,bytea,bytea) TO atis_soe_registry_owner;
SET ROLE atis_soe_registry_owner;
SET search_path=pg_catalog,atis_soe,pg_temp;
CREATE TABLE atis_soe.scope_catalogue_state (
 singleton boolean PRIMARY KEY CHECK(singleton),
 catalogue_epoch bigint NOT NULL CHECK(catalogue_epoch>=1),
 status text NOT NULL CHECK(status IN ('ACTIVE','TRANSITION_PENDING','RECONCILIATION_REQUIRED')),
 prepared_transition_id text,
 last_activation_digest text CHECK(last_activation_digest ~ '^[0-9a-f]{64}$')
);
INSERT INTO atis_soe.scope_catalogue_state(singleton,catalogue_epoch,status) VALUES(true,1,'ACTIVE');
CREATE TABLE atis_soe.scope_required_issuer (
 deployment_id text NOT NULL,issuer_domain text PRIMARY KEY CHECK(issuer_domain<>''),
 issuer_rank integer NOT NULL UNIQUE CHECK(issuer_rank>=0)
);
CREATE TABLE atis_soe.registry_transition_command (
 transition_id text NOT NULL CHECK(transition_id<>''),
 action text NOT NULL CHECK(action IN ('PREPARE','ACTIVATE')),
 command_digest text NOT NULL UNIQUE CHECK(command_digest ~ '^[0-9a-f]{64}$'),
 raw_command bytea NOT NULL CHECK(octet_length(raw_command)<=1048576),
 result jsonb NOT NULL,
 PRIMARY KEY(transition_id,action)
);
CREATE TABLE atis_soe.registry_nonce (
 deployment_id text NOT NULL,actor text NOT NULL,nonce text NOT NULL CHECK(nonce ~ '^[0-9a-f]{64}$'),
 command_digest text NOT NULL UNIQUE REFERENCES atis_soe.registry_transition_command(command_digest),
 PRIMARY KEY(deployment_id,actor,nonce)
);
CREATE TABLE atis_soe.scope_catalogue_transition_history (
 transition_id text NOT NULL,action text NOT NULL,command_digest text NOT NULL UNIQUE
   REFERENCES atis_soe.registry_transition_command(command_digest),
 predecessor_epoch bigint NOT NULL CHECK(predecessor_epoch>=1),
 successor_epoch bigint NOT NULL CHECK(successor_epoch=predecessor_epoch+1),
 actor text NOT NULL,change_set_digest text NOT NULL CHECK(change_set_digest ~ '^[0-9a-f]{64}$'),
 recorded_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
 PRIMARY KEY(transition_id,action)
);
CREATE TABLE atis_soe.scope_barrier (
 identity_digest text NOT NULL CHECK(identity_digest ~ '^[0-9a-f]{64}$'),issuer_domain text NOT NULL,
 deployment_id text NOT NULL,scope_rank integer NOT NULL CHECK(scope_rank BETWEEN 0 AND 6),
 canonical_bytes bytea NOT NULL,issuer_rank integer NOT NULL CHECK(issuer_rank>=0),
 lifecycle text NOT NULL CHECK(lifecycle IN ('RESERVED','ACTIVE','RETIRED')),
 reservation_transition_id text NOT NULL,
 gate_status text NOT NULL CHECK(gate_status='DENY'),
 PRIMARY KEY(identity_digest,issuer_domain)
 -- Deliberately no FK to a not-yet-active identity (C013 L02 preparation).
);
CREATE TABLE atis_soe.scope_reservation (
 transition_id text NOT NULL,identity_digest text NOT NULL,issuer_domain text NOT NULL,
 canonical_bytes bytea NOT NULL,preparation_digest text NOT NULL CHECK(preparation_digest ~ '^[0-9a-f]{64}$'),
 PRIMARY KEY(transition_id,identity_digest,issuer_domain)
 -- Preparation metadata at L01; no FK that locks a future L02/identity row.
);
CREATE TABLE atis_soe.scope_identity (
 identity_digest text PRIMARY KEY CHECK(identity_digest ~ '^[0-9a-f]{64}$'),
 deployment_id text NOT NULL,dimension text NOT NULL CHECK(dimension IN ('GLOBAL','ACCOUNT','PORTFOLIO','OPERATION')),
 namespace text NOT NULL,value text NOT NULL,producer_id text NOT NULL,producer_epoch bigint NOT NULL CHECK(producer_epoch>0),
 scope_rank integer NOT NULL,canonical_bytes bytea NOT NULL,entry jsonb NOT NULL,
 valid_from_epoch bigint NOT NULL CHECK(valid_from_epoch>=1),invalid_after_epoch bigint,
 UNIQUE(deployment_id,dimension,namespace,value,producer_id,producer_epoch),
 CHECK(invalid_after_epoch IS NULL OR invalid_after_epoch>valid_from_epoch)
);
CREATE TABLE atis_soe.scope_relation (
 child_digest text NOT NULL REFERENCES atis_soe.scope_identity(identity_digest),
 parent_digest text NOT NULL REFERENCES atis_soe.scope_identity(identity_digest),
 relation_type text NOT NULL CHECK(relation_type IN ('PORTFOLIO_ACCOUNT','OPERATION_LEAF')),
 valid_from_epoch bigint NOT NULL,invalid_after_epoch bigint,
 PRIMARY KEY(child_digest,parent_digest,relation_type,valid_from_epoch),
 CHECK(child_digest<>parent_digest),CHECK(invalid_after_epoch IS NULL OR invalid_after_epoch>valid_from_epoch)
);
CREATE TABLE atis_soe.operation_class_rule (
 operation_class text NOT NULL,dimension text NOT NULL,scope_rank integer NOT NULL,
 cardinality_min integer NOT NULL CHECK(cardinality_min>=1),
 cardinality_max integer NOT NULL CHECK(cardinality_max BETWEEN cardinality_min AND 256),
 valid_from_epoch bigint NOT NULL,invalid_after_epoch bigint,
 PRIMARY KEY(operation_class,dimension,valid_from_epoch),
 CHECK(invalid_after_epoch IS NULL OR invalid_after_epoch>valid_from_epoch)
);
CREATE FUNCTION atis_soe.registry_append_only() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,atis_soe,pg_temp AS $guard$
BEGIN RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='U3_APPEND_ONLY_HISTORY'; END
$guard$;
CREATE TRIGGER immutable_command BEFORE UPDATE OR DELETE ON atis_soe.registry_transition_command
 FOR EACH ROW EXECUTE FUNCTION atis_soe.registry_append_only();
CREATE TRIGGER immutable_nonce BEFORE UPDATE OR DELETE ON atis_soe.registry_nonce
 FOR EACH ROW EXECUTE FUNCTION atis_soe.registry_append_only();
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON atis_soe.scope_catalogue_transition_history
 FOR EACH ROW EXECUTE FUNCTION atis_soe.registry_append_only();
REVOKE ALL ON atis_soe.scope_catalogue_state,atis_soe.scope_required_issuer,atis_soe.registry_transition_command,atis_soe.registry_nonce,atis_soe.scope_catalogue_transition_history,atis_soe.scope_barrier,atis_soe.scope_reservation,atis_soe.scope_identity,atis_soe.scope_relation,atis_soe.operation_class_rule FROM PUBLIC;
REVOKE ALL ON FUNCTION atis_soe.registry_append_only() FROM PUBLIC;
RESET ROLE;

CREATE FUNCTION atis_soe.registry_private_entry(action text,raw_command bytea,transition_id text,command_digest text)
RETURNS jsonb LANGUAGE plpython3u SECURITY INVOKER
SET search_path=pg_catalog,atis_soe,pg_temp AS $registry$
import json
try:
    raw=bytes(raw_command) if raw_command is not None else None
    if action=='READ':
        rows=plpy.execute(plpy.prepare("SELECT raw_command FROM atis_soe.registry_transition_command WHERE transition_id=$1 AND command_digest=$2",['text','text']),[transition_id,command_digest])
        if len(rows)!=1: plpy.error('U3_READBACK_UNRESOLVED',sqlstate='22000')
        raw=bytes(rows[0]['raw_command'])
    if raw is None or len(raw)>1048576: plpy.error('U3_CANONICAL_REJECTED',sqlstate='22000')
    # Preliminary routing only; original bytes are strictly parsed by A before
    # anything becomes authoritative. No lossy JSON result is persisted.
    p=json.loads(raw.decode('utf-8'))['payload']
    context=json.dumps({'actor':p['actor'],'deployment_id':p['deployment_id']},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    result=plpy.execute(plpy.prepare("SELECT atis_soe.verify_u3_signature('REGISTRY_TRANSITION',$1,$2) AS status",['bytea','bytea']),[context,raw])[0]['status']
    if result!='VALID_SIGNATURE_ONLY': plpy.error(result,sqlstate='22000')
    # A has verified the fixed pinned bundle and loaded scope_registry itself.
    from automated_trading_bot.stage5.scope_registry import registry_database_entry
    return registry_database_entry(plpy,action,raw,transition_id,command_digest)
except Exception as exc:
    code=getattr(exc,'sqlstate',None)
    if code in ('57014','40001','40P01','55P03','25001','22000'): raise
    from automated_trading_bot.stage5.identity_scope import IdentityScopeError
    if isinstance(exc,IdentityScopeError): plpy.error(str(exc),sqlstate='22000')
    plpy.error('U3_REGISTRY_UNAVAILABLE',sqlstate='55000')
$registry$;
ALTER FUNCTION atis_soe.registry_private_entry(text,bytea,text,text) OWNER TO atis_soe_registry_owner;
REVOKE ALL ON FUNCTION atis_soe.registry_private_entry(text,bytea,text,text) FROM PUBLIC;
SET ROLE atis_soe_registry_owner;
CREATE FUNCTION atis_soe.prepare_scope_catalogue_transition(raw_command bytea) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,atis_soe,pg_temp
AS $function$ SELECT atis_soe.registry_private_entry('PREPARE',$1,NULL,NULL) $function$;
CREATE FUNCTION atis_soe.transition_scope_catalogue(raw_command bytea) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,atis_soe,pg_temp
AS $function$ SELECT atis_soe.registry_private_entry('ACTIVATE',$1,NULL,NULL) $function$;
CREATE FUNCTION atis_soe.read_scope_catalogue_transition(transition_id text,command_digest text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,atis_soe,pg_temp
AS $function$ SELECT atis_soe.registry_private_entry('READ',NULL,$1,$2) $function$;
REVOKE ALL ON FUNCTION atis_soe.prepare_scope_catalogue_transition(bytea),atis_soe.transition_scope_catalogue(bytea),atis_soe.read_scope_catalogue_transition(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION atis_soe.prepare_scope_catalogue_transition(bytea),atis_soe.transition_scope_catalogue(bytea),atis_soe.read_scope_catalogue_transition(text,text) TO atis_soe_registry_migrator;
RESET ROLE;
REVOKE CREATE ON SCHEMA atis_soe FROM atis_soe_registry_owner;
COMMIT;

-- Checkpoint C: protected standalone nonfinancial identity and registration.
-- A/B bytes above remain preserved. No worker/publisher/financial integration.
BEGIN;
GRANT CREATE ON SCHEMA atis_soe TO atis_soe_registry_owner;
SET ROLE atis_soe_registry_owner;
CREATE TABLE atis_soe.u3_operation_identity (
 operation_id text PRIMARY KEY CHECK(operation_id<>''),
 request_digest text NOT NULL CHECK(request_digest ~ '^[0-9a-f]{64}$'),
 evidence_digest text NOT NULL UNIQUE CHECK(evidence_digest ~ '^[0-9a-f]{64}$'),
 core_bytes bytea NOT NULL, envelope_bytes bytea NOT NULL, final_bytes bytea NOT NULL,
 deployment_id text NOT NULL, signer_id text NOT NULL, nonce text NOT NULL,
 catalogue_epoch bigint NOT NULL CHECK(catalogue_epoch>0),
 CHECK(octet_length(core_bytes)<=1048576 AND octet_length(envelope_bytes)<=1048576 AND octet_length(final_bytes)<=1048576)
);
CREATE TABLE atis_soe.u3_operation_nonce (
 deployment_id text NOT NULL, signer_id text NOT NULL, nonce text NOT NULL CHECK(nonce ~ '^[0-9a-f]{64}$'),
 operation_id text NOT NULL UNIQUE REFERENCES atis_soe.u3_operation_identity(operation_id),
 evidence_digest text NOT NULL UNIQUE REFERENCES atis_soe.u3_operation_identity(evidence_digest),
 PRIMARY KEY(deployment_id,signer_id,nonce)
);
CREATE TABLE atis_soe.u3_scope_registration (
 operation_id text PRIMARY KEY REFERENCES atis_soe.u3_operation_identity(operation_id),
 evidence_digest text NOT NULL UNIQUE REFERENCES atis_soe.u3_operation_identity(evidence_digest)
);
CREATE TABLE atis_soe.u3_registration_member (
 operation_id text NOT NULL REFERENCES atis_soe.u3_scope_registration(operation_id),
 identity_digest text NOT NULL CHECK(identity_digest ~ '^[0-9a-f]{64}$'),
 ordinal integer NOT NULL CHECK(ordinal>=0),
 PRIMARY KEY(operation_id,identity_digest), UNIQUE(operation_id,ordinal)
 -- No late FK lock on an earlier-layer catalogue identity.
);
CREATE TABLE atis_soe.u3_registration_history (
 operation_id text NOT NULL REFERENCES atis_soe.u3_operation_identity(operation_id),
 action text NOT NULL CHECK(action IN ('PREPARE','REGISTER')),
 evidence_digest text NOT NULL REFERENCES atis_soe.u3_operation_identity(evidence_digest),
 recorded_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
 PRIMARY KEY(operation_id,action)
);
CREATE TRIGGER immutable_u3_identity BEFORE UPDATE OR DELETE ON atis_soe.u3_operation_identity FOR EACH ROW EXECUTE FUNCTION atis_soe.registry_append_only();
CREATE TRIGGER immutable_u3_nonce BEFORE UPDATE OR DELETE ON atis_soe.u3_operation_nonce FOR EACH ROW EXECUTE FUNCTION atis_soe.registry_append_only();
CREATE TRIGGER immutable_u3_registration BEFORE UPDATE OR DELETE ON atis_soe.u3_scope_registration FOR EACH ROW EXECUTE FUNCTION atis_soe.registry_append_only();
CREATE TRIGGER immutable_u3_member BEFORE UPDATE OR DELETE ON atis_soe.u3_registration_member FOR EACH ROW EXECUTE FUNCTION atis_soe.registry_append_only();
CREATE TRIGGER immutable_u3_history BEFORE UPDATE OR DELETE ON atis_soe.u3_registration_history FOR EACH ROW EXECUTE FUNCTION atis_soe.registry_append_only();
REVOKE ALL ON atis_soe.u3_operation_identity,atis_soe.u3_operation_nonce,atis_soe.u3_scope_registration,atis_soe.u3_registration_member,atis_soe.u3_registration_history FROM PUBLIC;
RESET ROLE;
CREATE FUNCTION atis_soe.u3_registration_private(action text,operation_id text,request_digest text,core_bytes bytea,envelope_bytes bytea,final_bytes bytea)
RETURNS jsonb LANGUAGE plpython3u SECURITY INVOKER
SET search_path=pg_catalog,atis_soe,pg_temp AS $registration$
try:
    # Only a fresh standalone transaction may enter. PostgreSQL's own locks,
    # not a caller-controlled GUC/cache, detect prior lower-layer work. This
    # also rejects PREPARE + REGISTER in one transaction: an uncommitted L08
    # cannot be treated as the durable prerequisite or precede new L02 locks.
    prior=plpy.execute("SELECT 1 FROM pg_catalog.pg_locks l JOIN pg_catalog.pg_class c ON c.oid=l.relation JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE l.pid=pg_catalog.pg_backend_pid() AND l.granted AND n.nspname='atis_soe' AND l.mode IN ('RowShareLock','RowExclusiveLock','ShareUpdateExclusiveLock','ShareLock','ShareRowExclusiveLock','ExclusiveLock','AccessExclusiveLock') AND c.relname NOT IN ('deployment_integrity','scope_catalogue_state') LIMIT 1")
    if prior: plpy.error('U3_STANDALONE_TRANSACTION_REQUIRED',sqlstate='25001')
    if any(v is None for v in (action,operation_id,request_digest,core_bytes,envelope_bytes,final_bytes)) or any(len(v)>1048576 for v in (core_bytes,envelope_bytes,final_bytes)):
        plpy.error('U3_REGISTRATION_BINDING',sqlstate='22000')
    # Authenticate pinned module provenance before importing the private entry.
    status=plpy.execute(plpy.prepare("SELECT atis_soe.verify_u3_signature('SCOPE',$1,$2) AS result",['bytea','bytea']),[core_bytes,envelope_bytes])[0]['result']
    if status!='VALID_SIGNATURE_ONLY': plpy.error(status,sqlstate='22000')
    from automated_trading_bot.stage5.scope_registry import registration_database_entry
    result=registration_database_entry(plpy,action,operation_id,request_digest,bytes(core_bytes),bytes(envelope_bytes),bytes(final_bytes))
    plpy.execute('SELECT 1')
    return result
except Exception as exc:
    code=getattr(exc,'sqlstate',None)
    if code in ('57014','40001','40P01','55P03','25001','22000'): raise
    from automated_trading_bot.stage5.identity_scope import IdentityScopeError
    if isinstance(exc,IdentityScopeError): plpy.error(str(exc),sqlstate='22000')
    plpy.error('U3_REGISTRATION_UNAVAILABLE',sqlstate='55000')
$registration$;
ALTER FUNCTION atis_soe.u3_registration_private(text,text,text,bytea,bytea,bytea) OWNER TO atis_soe_registry_owner;
REVOKE ALL ON FUNCTION atis_soe.u3_registration_private(text,text,text,bytea,bytea,bytea) FROM PUBLIC;
SET ROLE atis_soe_registry_owner;
CREATE FUNCTION atis_soe.prepare_operation_identity_v1(operation_id text,request_digest text,core_bytes bytea,envelope_bytes bytea,final_bytes bytea)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,atis_soe,pg_temp
AS $f$ SELECT atis_soe.u3_registration_private('PREPARE',$1,$2,$3,$4,$5) $f$;
CREATE FUNCTION atis_soe.register_signed_scope_v1(operation_id text,request_digest text,core_bytes bytea,envelope_bytes bytea,final_bytes bytea)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,atis_soe,pg_temp
AS $f$ SELECT atis_soe.u3_registration_private('REGISTER',$1,$2,$3,$4,$5) $f$;
CREATE FUNCTION atis_soe.read_signed_scope_v1(operation_id text,request_digest text,core_bytes bytea,envelope_bytes bytea,final_bytes bytea)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,atis_soe,pg_temp
AS $f$ SELECT atis_soe.u3_registration_private('READ',$1,$2,$3,$4,$5) $f$;
REVOKE ALL ON FUNCTION atis_soe.prepare_operation_identity_v1(text,text,bytea,bytea,bytea),atis_soe.register_signed_scope_v1(text,text,bytea,bytea,bytea),atis_soe.read_signed_scope_v1(text,text,bytea,bytea,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION atis_soe.prepare_operation_identity_v1(text,text,bytea,bytea,bytea),atis_soe.register_signed_scope_v1(text,text,bytea,bytea,bytea),atis_soe.read_signed_scope_v1(text,text,bytea,bytea,bytea) TO atis_soe_verifier;
RESET ROLE;
REVOKE CREATE ON SCHEMA atis_soe FROM atis_soe_registry_owner;
COMMIT;
