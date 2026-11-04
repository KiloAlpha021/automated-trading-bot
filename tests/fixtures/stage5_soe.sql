\ir ../../src/automated_trading_bot/stage5/commit_boundary/sql/0001_soe_commit_boundary.sql
\ir ../../src/automated_trading_bot/stage5/commit_boundary/sql/0002_scope_publication.sql

-- Owner-only simulation schema for the pre-existing generic envelope tests.
-- This is not a runtime grant path; protected U1 tests use atis_soe directly.
CREATE SCHEMA stage5_soe_fixture;
REVOKE ALL ON SCHEMA stage5_soe_fixture FROM PUBLIC;
CREATE TABLE stage5_soe_fixture.operation_outcome (
 operation_id text PRIMARY KEY, request_digest text NOT NULL,
 status text NOT NULL CHECK(status IN ('COMMITTED','DENIED')),
 result_code text NOT NULL, capital_effect_minor bigint NOT NULL CHECK(capital_effect_minor>=0),
 liability_effect_minor bigint NOT NULL CHECK(liability_effect_minor>=0),
 CHECK(capital_effect_minor=liability_effect_minor),
 CHECK(status='COMMITTED' OR (capital_effect_minor=0 AND liability_effect_minor=0))
);
CREATE TABLE stage5_soe_fixture.capital_state (
 scope text PRIMARY KEY, reserved_minor bigint NOT NULL CHECK(reserved_minor>=0),
 liability_minor bigint NOT NULL CHECK(liability_minor>=0),CHECK(reserved_minor=liability_minor)
);
CREATE FUNCTION stage5_soe_fixture.commit_operation(op text,digest text,scope_key text,amount bigint)
RETURNS SETOF stage5_soe_fixture.operation_outcome LANGUAGE plpgsql AS $$
BEGIN
 IF current_setting('transaction_isolation')<>'serializable' THEN RAISE EXCEPTION 'SERIALIZABLE required'; END IF;
 IF amount<=0 THEN RAISE EXCEPTION 'positive amount required'; END IF;
 IF EXISTS(SELECT 1 FROM stage5_soe_fixture.operation_outcome WHERE operation_id=op AND request_digest<>digest) THEN RAISE EXCEPTION USING ERRCODE='22000',MESSAGE='digest conflict'; END IF;
 IF NOT EXISTS(SELECT 1 FROM stage5_soe_fixture.operation_outcome WHERE operation_id=op) THEN
  INSERT INTO stage5_soe_fixture.capital_state VALUES(scope_key,amount,amount)
  ON CONFLICT(scope) DO UPDATE SET reserved_minor=stage5_soe_fixture.capital_state.reserved_minor+amount,liability_minor=stage5_soe_fixture.capital_state.liability_minor+amount;
  INSERT INTO stage5_soe_fixture.operation_outcome VALUES(op,digest,'COMMITTED','RESERVED',amount,amount);
 END IF;
 RETURN QUERY SELECT * FROM stage5_soe_fixture.operation_outcome WHERE operation_id=op;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA stage5_soe_fixture FROM PUBLIC;
