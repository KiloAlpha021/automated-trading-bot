from __future__ import annotations

import os
from typing import Any

import pytest

from automated_trading_bot.stage5.commit_boundary import (
    AttemptContext,
    DurableOutcome,
    OperationRequest,
    OutcomeStatus,
    SerializableOperationEnvelope,
    VerifiedAttemptEvidence,
)
from automated_trading_bot.stage5.commit_boundary.psycopg_adapter import (
    PsycopgConnectionFactory,
)


DSN = os.environ.get("ATIS_SOE_PG_DSN")
QUALIFICATION_REQUIRED = os.environ.get("ATIS_SOE_QUALIFICATION_REQUIRED") == "1"
if QUALIFICATION_REQUIRED and not DSN:
    raise RuntimeError(
        "protected PostgreSQL qualification was required but ATIS_SOE_PG_DSN is absent"
    )
pytestmark = pytest.mark.skipif(not DSN, reason="isolated PostgreSQL DSN not supplied")


class FixtureBackend:
    def lookup(
        self, cursor: Any, request: OperationRequest, context: AttemptContext
    ) -> DurableOutcome | None:
        del context
        cursor.execute(
            "SELECT operation_id, request_digest, status, result_code, "
            "capital_effect_minor, liability_effect_minor "
            "FROM stage5_soe_fixture.operation_outcome WHERE operation_id = %s",
            (request.operation_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return DurableOutcome(
            str(row[0]), str(row[1]), OutcomeStatus(str(row[2])), str(row[3]),
            int(row[4]), int(row[5]),
        )

    def verify_attempt(
        self, cursor: Any, request: OperationRequest, context: AttemptContext
    ) -> VerifiedAttemptEvidence:
        del cursor
        return VerifiedAttemptEvidence(
            request.operation_id, request.digest, context.attempt,
            context.backend_identity, context.transaction_identity, request.scope,
            request.expected_generation, request.expected_epoch, "I01-FIXTURE",
            f"TP-{context.transaction_identity}", f"VD-{context.transaction_identity}",
        )

    def execute(
        self,
        cursor: Any,
        request: OperationRequest,
        context: AttemptContext,
        evidence: VerifiedAttemptEvidence,
    ) -> DurableOutcome:
        del context, evidence
        cursor.execute(
            "SELECT * FROM stage5_soe_fixture.commit_operation(%s, %s, %s, %s)",
            (request.operation_id, request.digest, request.scope, request.amount_minor),
        )
        row = cursor.fetchone()
        assert row is not None
        return DurableOutcome(
            str(row[0]), str(row[1]), OutcomeStatus(str(row[2])), str(row[3]),
            int(row[4]), int(row[5]),
        )


def make_request(operation_id: str = "PG-OP-1", amount: int = 5000) -> OperationRequest:
    return OperationRequest.create(
        operation_id=operation_id,
        request_type="CAPITAL_RESERVATION",
        scope="PORTFOLIO:PG",
        amount_minor=amount,
        currency="GBP",
        expected_generation=1,
        expected_epoch=1,
        immutable_parameters={"g00_ticket_id": f"G00-{operation_id}"},
    )


@pytest.fixture(autouse=True)
def reset_fixture() -> None:
    assert DSN is not None
    import psycopg

    with psycopg.connect(DSN, autocommit=True) as connection:
        connection.execute("TRUNCATE stage5_soe_fixture.operation_outcome")
        connection.execute("TRUNCATE stage5_soe_fixture.capital_state")


def test_atomic_outcome_and_idempotent_financial_effect() -> None:
    assert DSN is not None
    envelope = SerializableOperationEnvelope(PsycopgConnectionFactory(DSN), FixtureBackend())
    first = envelope.execute(make_request())
    second = envelope.execute(make_request())
    assert first.outcome == second.outcome
    assert second.attempts[-1].disposition == "IDEMPOTENT_READBACK"
    import psycopg

    with psycopg.connect(DSN) as connection:
        row = connection.execute(
            "SELECT reserved_minor, liability_minor "
            "FROM stage5_soe_fixture.capital_state WHERE scope = 'PORTFOLIO:PG'"
        ).fetchone()
    assert row == (5000, 5000)


def test_database_rejects_denial_with_financial_effect() -> None:
    assert DSN is not None
    import psycopg

    with psycopg.connect(DSN) as connection, pytest.raises(psycopg.errors.CheckViolation):
        connection.execute(
            "INSERT INTO stage5_soe_fixture.operation_outcome VALUES "
            "('DENY-1', 'digest', 'DENIED', 'DENIED', 1, 1)"
        )


def test_actual_transaction_is_serializable() -> None:
    assert DSN is not None
    result = SerializableOperationEnvelope(
        PsycopgConnectionFactory(DSN), FixtureBackend()
    ).execute(make_request("PG-ISO-1"))
    assert result.attempts[0].isolation == "serializable"


def test_conflicting_digest_cannot_repeat_financial_effect() -> None:
    assert DSN is not None
    subject = SerializableOperationEnvelope(PsycopgConnectionFactory(DSN), FixtureBackend())
    subject.execute(make_request("PG-CONFLICT", 5000))
    from automated_trading_bot.stage5.commit_boundary import OperationConflict

    with pytest.raises(OperationConflict):
        subject.execute(make_request("PG-CONFLICT", 6000))


# U1/025: real protected-schema containment; the earlier generic fixture tests
# remain unchanged and do not establish positive protected financial authority.
def _u1_dsn(user="u1_runtime"):
    from psycopg.conninfo import make_conninfo
    return make_conninfo(DSN, user=user)


def _u1_snapshot():
    import psycopg
    with psycopg.connect(DSN) as c:
        names = [r[0] for r in c.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='atis_soe' ORDER BY tablename"
        )]
        from psycopg import sql
        return {n: c.execute(sql.SQL(
            "SELECT COALESCE(jsonb_agg(v ORDER BY v::text),'[]'::jsonb) "
            "FROM (SELECT to_jsonb(t) v FROM atis_soe.{} t) q"
        ).format(sql.Identifier(n))).fetchone()[0] for n in names}


def _u1_record(name, before, after, result):
    import json
    from pathlib import Path
    folder = Path(os.environ["ATIS_U1_EVIDENCE"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (name + '.json')).write_text(json.dumps(
        {"before": before, "after": after, "result": result}, indent=2, default=str
    ), encoding='utf-8')


def _u1_seed(op, variant="missing_identity"):
    """Disposable owner seeds current legacy evidence, NOT qualified authority.

    No signed account/portfolio, capacity/predecessor or liability authority is
    installed. This recreates the accepted-input gap, not a runtime bypass.
    """
    import psycopg
    scope = 'PORTFOLIO:PG'
    with psycopg.connect(DSN) as c:
        c.execute("INSERT INTO atis_soe.scope_control VALUES (%s,1,2,10,NULL,NULL) ON CONFLICT DO NOTHING", (scope,))
        c.execute("INSERT INTO atis_soe.identity_policy VALUES ('ACTIVE',1,'G00-TEST','DEPLOYMENT-TEST','SOE-TEST','I01-TEST',1,2,10000000000,10000000000,0,false) ON CONFLICT DO NOTHING")
        c.execute("INSERT INTO atis_soe.g00_attestation VALUES (%s,%s,'G00-TEST','DEPLOYMENT-TEST','SOE-TEST',%s,%s,'CAPITAL_RESERVATION',500,'GBP',%s,1,2,7,NULL,1,clock_timestamp()+interval '1 hour',false,'u1_owner')", ('N-'+op,'V-'+op,op,'D-'+op,scope))
        c.execute("INSERT INTO atis_soe.attempt_evidence (attempt_identity,verification_digest,operation_id,request_digest,scope,generation,issuer_epoch,authority_identity,timeproof_identity,authority_issued_ns,timeproof_issued_ns,verified_at_ns,policy_version,registered_by) VALUES (%s,%s,%s,%s,%s,1,2,'I01-TEST',%s,1,1,1,1,'u1_owner')", ('A-'+op,'E-'+op,op,'D-'+op,scope,'T-'+op))
        c.execute("INSERT INTO atis_soe.scope_barrier VALUES (%s,NULL,NULL) ON CONFLICT DO NOTHING",(scope,))
        c.execute("INSERT INTO atis_soe.operation_scope_registration(operation_id,request_digest,operation_class,deployment_identity,catalogue_epoch,operation_core_digest,scope_envelope_digest,canonical_envelope,registered_by) VALUES (%s,%s,'CAPITAL_RESERVATION','DEPLOYMENT-TEST',1,'unqualified-core','unsigned-envelope',%s,'u1_owner')",(op,'D-'+op,b'{}'))
        c.execute("INSERT INTO atis_soe.operation_applicable_scope VALUES (%s,%s,0)",(op,scope))
        # Intentionally no deployment/catalogue/high-water rows. Current v2
        # PERFORM checks do not establish their existence: the oracle must fail.
        if variant == 'stale_authority':
            c.execute("UPDATE atis_soe.g00_attestation SET valid_until=clock_timestamp()-interval '1 hour' WHERE operation_id=%s",(op,))


def _u1_call(op, route, user='u1_runtime'):
    import psycopg
    try:
        with psycopg.connect(_u1_dsn(user)) as c:
            c.execute('BEGIN ISOLATION LEVEL SERIALIZABLE')
            if route == 'legacy':
                row=c.execute('SELECT * FROM atis_soe.commit_capital(%s,%s,%s,500,\'GBP\',1,2,%s,7,%s,%s)',(op,'D-'+op,'PORTFOLIO:PG','N-'+op,'A-'+op,'T-'+op)).fetchone()
            else:
                row=c.execute('SELECT * FROM atis_soe.commit_capital_v2(%s,%s,500,\'GBP\',%s,7,%s,%s)',(op,'D-'+op,'N-'+op,'A-'+op,'T-'+op)).fetchone()
        return {'row':row,'sqlstate':None,'role':user}
    except psycopg.Error as exc:
        return {'row':None,'sqlstate':exc.sqlstate,'error':str(exc),'role':user}


@pytest.mark.parametrize('route',['legacy','v2'])
@pytest.mark.parametrize('gap',[
    'missing_scope','missing_identity','missing_catalogue','stale_authority',
    'missing_predecessor','missing_capacity','invalid_liability_authority',
    'unqualified_committed','caller_legacy_selector',
])
def test_u1_direct_sql_new_exposure_is_contained(route,gap):
    op='U1-'+route+'-'+gap
    _u1_seed(op,gap)
    before=_u1_snapshot()
    result=_u1_call(op,route)
    after=_u1_snapshot()
    _u1_record(op,before,after,result)
    assert result['sqlstate']=='42501',result
    assert before==after


@pytest.mark.parametrize('route',['legacy','v2'])
@pytest.mark.parametrize('user',['u1_verifier','u1_recovery','u1_publisher','u1_plain'])
def test_u1_other_roles_cannot_grant(route,user):
    before=_u1_snapshot();result=_u1_call('FOREIGN',route,user);after=_u1_snapshot()
    _u1_record('roles-'+route+'-'+user,before,after,result)
    assert result['sqlstate']=='42501'
    assert before==after


def test_u1_historical_readback_and_privileges():
    import psycopg
    before=_u1_snapshot()
    with psycopg.connect(_u1_dsn()) as c:
        assert c.execute("SELECT * FROM atis_soe.lookup_outcome('HISTORIC','HISTORIC-DIGEST')").fetchone()==('HISTORIC','HISTORIC-DIGEST','COMMITTED','RESERVED',900,900)
    for user in ['u1_runtime','u1_verifier','u1_recovery','u1_publisher','u1_plain']:
        for statement in ["UPDATE atis_soe.capital_state SET reserved_minor=0", "SET ROLE atis_soe_owner"]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege),psycopg.connect(_u1_dsn(user)) as c:
                c.execute(statement)
    with pytest.raises(psycopg.errors.DataException),psycopg.connect(_u1_dsn()) as c:
        c.execute("SELECT * FROM atis_soe.lookup_outcome('HISTORIC','WRONG')")
    with psycopg.connect(DSN) as c:
        functions=c.execute("SELECT p.proname,p.proconfig,r.rolname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner WHERE n.nspname='atis_soe' AND p.prosecdef").fetchall()
    assert functions
    assert all(owner=='atis_soe_owner' and 'search_path=pg_catalog, atis_soe, pg_temp' in settings for _,settings,owner in functions)
    after=_u1_snapshot();_u1_record('historic-privileges',before,after,functions)
    assert before==after


def test_u1_concurrent_duplicate_requests_and_restriction_hold():
    from concurrent.futures import ThreadPoolExecutor
    import psycopg
    op='U1-CONCURRENT';_u1_seed(op)
    before=_u1_snapshot()
    with psycopg.connect(DSN) as publisher:
        # Owner fixture holds an uncommitted emergency barrier. It is rolled
        # back: no production publisher or cross-issuer authority is simulated.
        publisher.execute("UPDATE atis_soe.scope_barrier SET pending_ticket=0 WHERE scope_key='PORTFOLIO:PG'")
        with ThreadPoolExecutor(max_workers=2) as workers:
            results=list(workers.map(lambda r:_u1_call(op,r),['legacy','v2']))
        publisher.rollback()
    after=_u1_snapshot();_u1_record('concurrent-restriction',before,after,results)
    assert all(r['sqlstate']=='42501' for r in results)
    assert before==after


def test_u1_real_preconnected_worker_keeps_ambiguous_hold(tmp_path):
    import psycopg
    from dataclasses import replace
    from automated_trading_bot.stage5.commit_boundary import IsolatedCommand,PreconnectedWorkerPoolSupervisor,IsolatedStatus,RecoveryState
    from automated_trading_bot.stage5.commit_boundary.psycopg_adapter import ProtectedPsycopgPersistentSessionFactory
    from test_stage5_preconnected_worker_pool import RecoveryJournal,g00_controller,fresh_evidence,TEST_G00_ROOT
    from stage5_recovery_authority_fixtures import open_journal
    op='U1-REAL-WORKER'
    request=OperationRequest.create(operation_id=op,request_type='CAPITAL_RESERVATION',scope='ACCOUNT:TEST',amount_minor=500,currency='GBP',expected_generation=1,expected_epoch=1,immutable_parameters={'g00_ticket_sequence':10,'g00_replay_identity':'N-'+op})
    command=IsolatedCommand(op,request.digest,'A-'+op,'10',{'request_type':request.request_type,'scope':request.scope,'amount_minor':500,'currency':'GBP','expected_generation':1,'expected_epoch':1,'immutable_parameters':dict(request.immutable_parameters)})
    with psycopg.connect(DSN) as c:
        c.execute("INSERT INTO atis_soe.scope_control VALUES ('ACCOUNT:TEST',1,1,10,NULL,NULL) ON CONFLICT DO NOTHING")
        c.execute("UPDATE atis_soe.identity_policy SET issuer_identity='SYNTHETIC-G00',deployment_identity='Q05-ISOLATED',recipient='SOE-01',authority_identity='SYNTHETIC-I01',generation=1,issuer_epoch=1")
    def evidence(item,admitted_ns):
        value=fresh_evidence(item,admitted_ns)
        receipt=TEST_G00_ROOT.sign(replace(value.g00_receipt,nonce='N-'+op,signature=''))
        return replace(value,g00_receipt=receipt)
    before=_u1_snapshot();path=tmp_path/'u1-journal.sqlite';journal=RecoveryJournal(path)
    with PreconnectedWorkerPoolSupervisor(journal,ProtectedPsycopgPersistentSessionFactory(_u1_dsn(),_u1_dsn('u1_verifier'),1),g00_controller=g00_controller(),capacity=1,require_protected_factory=True) as pool:
        result=pool.execute(command,evidence)
    after=_u1_snapshot()
    _u1_record('real-worker',before,after,repr(result))
    assert result.outcome.status is IsolatedStatus.RESTRICTIVE_UNKNOWN
    assert result.journal_state is RecoveryState.AMBIGUOUS_HOLD
    for table in ['capital_state','scope_capital_state_v2','operation_outcome','liability']:
        assert before[table]==after[table]
    # Reopen the same durable journal; no terminal denial was fabricated.
    reopened=open_journal(path)
    assert reopened.get(op,command.attempt_id).state is RecoveryState.AMBIGUOUS_HOLD
