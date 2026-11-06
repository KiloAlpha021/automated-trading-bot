"""Standalone U3 wire contracts. Parsing does not grant authority.

PostgreSQL owns registry state. This module contains no authoritative cache,
signer, key selection, financial permission, or implicit database bootstrap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from .commit_boundary.operation_envelope import OperationRequest
from .identity_scope import (
    AuthenticatedScopeEnvelopeV1,
    IdentityScopeError,
    OperationCoreV1,
    canonical_bytes,
    exact_fields,
    hex_digest,
    parse_canonical,
    parse_container,
    parse_timestamp,
    positive,
)

REGISTRY_DOMAIN = b"ATIS-S5-REGISTRY-TRANSITION-V1\0"
REGISTRY_FIELDS = (
    "schema",
    "capability",
    "actor",
    "deployment_id",
    "action",
    "transition_id",
    "predecessor_epoch",
    "successor_epoch",
    "change_set",
    "change_set_digest",
    "preparation_digest",
    "issued_at",
    "expires_at",
    "nonce",
    "key_id",
    "key_version",
    "policy_version",
)


def registry_signing_bytes(payload: object) -> bytes:
    """Exact D1 carrier validation; cryptographic verification belongs to SQL3."""
    exact_fields(payload, REGISTRY_FIELDS)
    if not isinstance(payload, dict):
        raise IdentityScopeError("registry object required")
    if (
        payload["schema"] != "registry-transition/v1"
        or payload["capability"] != "REGISTRY_TRANSITION"
        or payload["action"] not in ("PREPARE", "ACTIVATE")
    ):
        raise IdentityScopeError("registry capability/schema/action")
    for name in ("actor", "deployment_id", "transition_id", "key_id"):
        value = payload[name]
        if type(value) is not str or not value or value != value.strip():
            raise IdentityScopeError("invalid registry " + name)
    predecessor = positive(payload["predecessor_epoch"], "predecessor epoch")
    successor = positive(payload["successor_epoch"], "successor epoch")
    if successor != predecessor + 1:
        raise IdentityScopeError("registry epoch must advance exactly once")
    for name in ("key_version", "policy_version"):
        positive(payload[name], name)
    hex_digest(payload["nonce"])
    exact_fields(payload["change_set"], ("identities", "relations", "rules", "issuers"))
    changes = payload["change_set"]
    if any(type(changes[name]) is not list for name in changes):
        raise IdentityScopeError("registry change-set collections required")
    if sha256(canonical_bytes(changes)).hexdigest() != hex_digest(
        payload["change_set_digest"]
    ):
        raise IdentityScopeError("registry change-set digest mismatch")
    if payload["action"] == "PREPARE":
        if payload["preparation_digest"] is not None:
            raise IdentityScopeError(
                "preparation cannot reference a predecessor preparation"
            )
    else:
        hex_digest(payload["preparation_digest"])
    if parse_timestamp(payload["expires_at"]) <= parse_timestamp(payload["issued_at"]):
        raise IdentityScopeError("registry validity interval")
    return REGISTRY_DOMAIN + canonical_bytes(payload)


@dataclass(frozen=True, slots=True)
class RegistryTransitionV1:
    raw: bytes

    def __post_init__(self) -> None:
        payload, _ = parse_container(self.raw)
        registry_signing_bytes(payload)

    @property
    def signing_bytes(self) -> bytes:
        return registry_signing_bytes(parse_container(self.raw)[0])

    @property
    def digest(self) -> str:
        return sha256(b"ATIS-S5-SIGNED-REGISTRY-TRANSITION-V1\0" + self.raw).hexdigest()


@dataclass(frozen=True, slots=True)
class U3RegistrationEvidenceV1:
    """Exact immutable sidecar; byte binding alone is NOT authentication."""

    schema: str
    operation_id: str
    request_digest: str
    operation_core_bytes: bytes
    signed_scope_envelope_bytes: bytes
    final_request_bytes: bytes

    def __post_init__(self) -> None:
        if self.schema != "u3-registration-evidence/v1":
            raise IdentityScopeError("invalid registration sidecar schema")
        core = OperationCoreV1(self.operation_core_bytes)
        envelope = AuthenticatedScopeEnvelopeV1(self.signed_scope_envelope_bytes)
        body = parse_canonical(core.raw)
        payload, _ = parse_container(envelope.raw)
        if (
            self.operation_id != body["operation_id"]
            or payload["operation_id"] != self.operation_id
            or payload["core_digest"] != core.digest
            or payload["deployment_id"] != body["deployment_id"]
            or payload["operation_class"] != body["operation_class"]
        ):
            raise IdentityScopeError("operation/core/envelope context mismatch")
        parameters = dict(body["immutable_business_parameters"])
        if {
            "scope_envelope",
            "scope_envelope_digest",
            "operation_core_digest",
            "catalogue_epoch",
            "operation_class",
        } & parameters.keys():
            raise IdentityScopeError("mixed legacy and signed registration metadata")
        parameters["atis_scope_envelope_digest"] = envelope.digest
        request = OperationRequest.create(
            operation_id=self.operation_id,
            request_type=body["request_type"],
            scope=body["legacy_scope_hint"],
            amount_minor=body["amount_minor"],
            currency=body["currency"],
            expected_generation=body["expected_generation"],
            expected_epoch=body["expected_epoch"],
            immutable_parameters=parameters,
        )
        # Reconstruct instead of parsing final JSON with a second permissive codec.
        expected = json.dumps(
            request._body(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        if (
            type(self.final_request_bytes) is not bytes
            or self.final_request_bytes != expected
            or hex_digest(self.request_digest) != request.digest
        ):
            raise IdentityScopeError("original final request bytes/digest mismatch")

    def sql_arguments(self) -> tuple[Any, ...]:
        return (
            self.operation_id,
            self.request_digest,
            self.operation_core_bytes,
            self.signed_scope_envelope_bytes,
            self.final_request_bytes,
        )


class RegistryError(IdentityScopeError):
    """A restrictive, transaction-aborting registry rejection."""


class RegistryCommitUncertain(RuntimeError):
    """COMMIT acknowledgement is unknown; use exact readback, never blind retry."""


def submit_catalogue(
    connection: Any, raw: bytes, *, timeout_ms: int = 5000
) -> dict[str, Any]:
    """Standalone transaction owner. No worker, publisher or financial dispatch."""
    from psycopg import Error as DatabaseError

    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 30000:
        raise RegistryError("U3_INVALID_STATEMENT_BUDGET")
    command = RegistryTransitionV1(raw)
    payload, _ = parse_container(raw)
    if connection.info.transaction_status.name != "IDLE":
        raise RegistryError("U3_CONNECTION_NOT_IDLE")
    query = {
        "PREPARE": "SELECT atis_soe.prepare_scope_catalogue_transition(%s)",
        "ACTIVATE": "SELECT atis_soe.transition_scope_catalogue(%s)",
    }[payload["action"]]
    committing = False
    try:
        connection.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
        connection.execute(
            "SELECT pg_catalog.set_config('statement_timeout',%s,true), "
            "pg_catalog.set_config('lock_timeout',%s,true)",
            (str(timeout_ms), str(min(2000, timeout_ms))),
        )
        result = connection.execute(query, (raw,)).fetchone()[0]
        committing = True
        connection.execute("COMMIT")
        if not isinstance(result, dict):
            raise RegistryCommitUncertain(command.digest)
        return result
    except BaseException as exc:
        if committing and getattr(exc, "sqlstate", None) not in ("40001", "40P01"):
            raise RegistryCommitUncertain(command.digest) from exc
        try:
            connection.execute("ROLLBACK")
        except DatabaseError as rollback_error:
            exc.add_note(
                "Rollback acknowledgement unavailable: " + type(rollback_error).__name__
            )
        raise


class _RegistryTransaction:
    """Database-resident implementation; SPI never comes from the wire.

    All metadata is read from PostgreSQL within the current transaction. The Python
    objects below are temporary validation values, not another authoritative cache.
    """

    def __init__(self, spi: Any) -> None:
        self.spi = spi

    def sql(
        self,
        statement: str,
        types: list[str] | None = None,
        values: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        if types is None:
            return list(self.spi.execute(statement))
        return list(self.spi.execute(self.spi.prepare(statement, types), values or []))

    def authenticate(self, raw: bytes) -> dict[str, Any]:
        payload, _ = parse_container(raw)
        RegistryTransitionV1(raw)
        context = canonical_bytes(
            {"actor": payload["actor"], "deployment_id": payload["deployment_id"]}
        )
        status = self.sql(
            "SELECT atis_soe.verify_u3_signature('REGISTRY_TRANSITION',$1,$2) AS result",
            ["bytea", "bytea"],
            [context, raw],
        )[0]["result"]
        if status != "VALID_SIGNATURE_ONLY":
            raise RegistryError(str(status))
        return payload

    def prefix(self, write: bool) -> dict[str, Any]:
        if (
            self.sql("SHOW transaction_isolation")[0]["transaction_isolation"]
            != "serializable"
        ):
            self.spi.error("U3_SERIALIZABLE_REQUIRED", sqlstate="25001")
        lock = "FOR UPDATE" if write else "FOR SHARE"
        deployment = self.sql(
            "SELECT * FROM atis_soe.deployment_integrity WHERE singleton " + lock
        )
        state = self.sql(
            "SELECT * FROM atis_soe.scope_catalogue_state WHERE singleton " + lock
        )
        if len(deployment) != 1 or len(state) != 1 or state[0]["status"] != "ACTIVE":
            raise RegistryError("U3_REGISTRY_STATE")
        result = dict(state[0])
        result["deployment"] = deployment[0]["deployment_identity"]
        self.verify_ledger()
        if result["catalogue_epoch"] > 1:
            history = self.sql(
                "SELECT raw_command FROM atis_soe.registry_transition_command WHERE command_digest=$1 AND action='ACTIVATE'",
                ["text"],
                [result["last_activation_digest"]],
            )
            if len(history) != 1:
                raise RegistryError("U3_REGISTRY_STATE")
            previous = self.authenticate(bytes(history[0]["raw_command"]))
            if previous["successor_epoch"] != result["catalogue_epoch"]:
                raise RegistryError("U3_REGISTRY_STATE")
            current = self.snapshot()
            if canonical_bytes(current) != canonical_bytes(previous["change_set"]):
                raise RegistryError("U3_REGISTRY_STATE")
        elif result["catalogue_epoch"] != 1 or self.sql(
            "SELECT 1 FROM atis_soe.scope_identity WHERE invalid_after_epoch IS NULL"
        ):
            raise RegistryError("U3_REGISTRY_STATE")
        return result

    @staticmethod
    def expected_result(raw: bytes) -> dict[str, Any]:
        payload, _ = parse_container(raw)
        return {
            "transition_id": payload["transition_id"],
            "command_digest": RegistryTransitionV1(raw).digest,
            "status": "PREPARED" if payload["action"] == "PREPARE" else "ACTIVATED",
            "catalogue_epoch": payload["predecessor_epoch"]
            if payload["action"] == "PREPARE"
            else payload["successor_epoch"],
            "change_set_digest": payload["change_set_digest"],
        }

    def verify_ledger(self) -> None:
        """Detect missing/partial protected history before any advancement.

        This is local integrity checking, not external snapshot anti-rollback.
        Signature authentication is additionally required for the actual commands.
        """
        commands = self.sql("SELECT * FROM atis_soe.registry_transition_command")
        nonces = self.sql("SELECT * FROM atis_soe.registry_nonce")
        history = self.sql("SELECT * FROM atis_soe.scope_catalogue_transition_history")
        if len(commands) != len(nonces) or len(commands) != len(history):
            raise RegistryError("U3_REGISTRY_STATE")
        nonce_by_digest = {r["command_digest"]: r for r in nonces}
        history_by_digest = {r["command_digest"]: r for r in history}
        for row in commands:
            raw = bytes(row["raw_command"])
            payload, _ = parse_container(raw)
            expected = self.expected_result(raw)
            digest = expected["command_digest"]
            nonce = nonce_by_digest.get(digest)
            event = history_by_digest.get(digest)
            stored = (
                json.loads(row["result"])
                if isinstance(row["result"], str)
                else row["result"]
            )
            if (
                row["command_digest"] != digest
                or stored != expected
                or row["transition_id"] != payload["transition_id"]
                or row["action"] != payload["action"]
                or nonce is None
                or event is None
                or any(
                    nonce[k] != payload[k] for k in ("deployment_id", "actor", "nonce")
                )
                or any(
                    event[k] != payload[k]
                    for k in (
                        "transition_id",
                        "action",
                        "predecessor_epoch",
                        "successor_epoch",
                        "actor",
                        "change_set_digest",
                    )
                )
            ):
                raise RegistryError("U3_REGISTRY_STATE")

    def snapshot(self) -> dict[str, Any]:
        identities = self.sql(
            "SELECT entry FROM atis_soe.scope_identity WHERE invalid_after_epoch IS NULL ORDER BY scope_rank,canonical_bytes"
        )
        relations = self.sql(
            "SELECT child_digest AS child,parent_digest AS parent,relation_type AS type FROM atis_soe.scope_relation WHERE invalid_after_epoch IS NULL ORDER BY child_digest,parent_digest,relation_type"
        )
        rules = self.sql(
            "SELECT operation_class,dimension,cardinality_min AS minimum,cardinality_max AS maximum FROM atis_soe.operation_class_rule WHERE invalid_after_epoch IS NULL ORDER BY operation_class,scope_rank"
        )
        issuers = self.sql(
            "SELECT issuer_domain FROM atis_soe.scope_required_issuer ORDER BY issuer_rank"
        )
        return {
            "identities": [
                json.loads(r["entry"]) if isinstance(r["entry"], str) else r["entry"]
                for r in identities
            ],
            "relations": relations,
            "rules": rules,
            "issuers": [r["issuer_domain"] for r in issuers],
        }

    def validate(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        from .identity_scope import SCOPE_KINDS, IdentityRecordV1

        change = payload["change_set"]
        supported = ("GLOBAL", "ACCOUNT", "PORTFOLIO", "OPERATION")
        issuers = self.sql(
            "SELECT * FROM atis_soe.scope_required_issuer ORDER BY issuer_rank"
        )
        if not issuers or any(
            r["deployment_id"] != payload["deployment_id"] for r in issuers
        ):
            raise RegistryError("U3_ISSUER_SET")
        if change["issuers"] != [r["issuer_domain"] for r in issuers]:
            raise RegistryError("U3_ISSUER_SET")
        records: dict[str, Any] = {}
        entries: dict[str, Any] = {}
        parents: dict[str, str] = {}
        order = []
        for entry in change["identities"]:
            exact_fields(entry, ("record", "assertion"))
            identity = IdentityRecordV1.parse(entry["record"])
            if (
                identity.dimension not in supported
                or identity.deployment_id != payload["deployment_id"]
            ):
                raise RegistryError("U3_IDENTITY_CONTEXT")
            digest = identity.digest
            if digest in records:
                raise RegistryError("U3_DUPLICATE_IDENTITY")
            records[digest], entries[digest] = identity, entry
            order.append(
                (
                    SCOPE_KINDS.index(identity.dimension),
                    canonical_bytes(identity.body()),
                )
            )
            if identity.dimension in ("ACCOUNT", "PORTFOLIO"):
                assertion = canonical_bytes(entry["assertion"])
                status = self.sql(
                    "SELECT atis_soe.verify_u3_signature($1,$2,$3) AS result",
                    ["text", "bytea", "bytea"],
                    [identity.dimension, canonical_bytes(identity.body()), assertion],
                )[0]["result"]
                if status != "VALID_SIGNATURE_ONLY":
                    raise RegistryError("U3_IDENTITY_AUTHENTICATION:" + str(status))
                ap, _ = parse_container(assertion)
                if identity.dimension == "PORTFOLIO":
                    parents[digest] = ap["parent_account_identity_digest"]
            elif (
                entry["assertion"] is not None
                or identity.producer_id != payload["actor"]
            ):
                raise RegistryError("U3_REGISTRY_IDENTITY_CONTEXT")
            # GLOBAL/OPERATION are registry-owned metadata authenticated by D1's
            # complete signed change set, never forged account/portfolio assertions.
            if (
                identity.dimension == "GLOBAL"
                and identity.value != payload["deployment_id"]
            ):
                raise RegistryError("U3_GLOBAL_ROOT")
            old = self.sql(
                "SELECT identity_digest,entry,producer_epoch,invalid_after_epoch FROM atis_soe.scope_identity WHERE deployment_id=$1 AND dimension=$2 AND namespace=$3 AND value=$4",
                ["text"] * 4,
                [
                    identity.deployment_id,
                    identity.dimension,
                    identity.namespace,
                    identity.value,
                ],
            )
            for row in old:
                old_entry = (
                    json.loads(row["entry"])
                    if isinstance(row["entry"], str)
                    else row["entry"]
                )
                if row["identity_digest"] == digest:
                    if row["invalid_after_epoch"] is not None or canonical_bytes(
                        old_entry
                    ) != canonical_bytes(entry):
                        raise RegistryError("U3_IDENTITY_ROLLBACK")
                elif row["producer_epoch"] >= identity.producer_epoch:
                    raise RegistryError("U3_IDENTITY_ROLLBACK")
        if (
            order != sorted(order)
            or not records
            or sum(r.dimension == "GLOBAL" for r in records.values()) != 1
        ):
            raise RegistryError("U3_IDENTITY_ORDER_OR_ROOT")
        if not set(supported) <= {r.dimension for r in records.values()}:
            raise RegistryError("U3_INCOMPLETE_CLOSURE")
        graph: dict[str, list[str]] = {d: [] for d in records}
        relation_order = []
        for relation in change["relations"]:
            exact_fields(relation, ("child", "parent", "type"))
            child, parent, kind = (
                relation["child"],
                relation["parent"],
                relation["type"],
            )
            if child not in records or parent not in records:
                raise RegistryError("U3_MISSING_PARENT")
            relation_order.append((child, parent, kind))
            graph[child].append(parent)
        if relation_order != sorted(set(relation_order)):
            raise RegistryError("U3_RELATION_ORDER")

        def closure(node: str, visiting: set[str]) -> set[str]:
            if node in visiting:
                raise RegistryError("U3_CYCLE")
            result = {node}
            for parent in graph[node]:
                result.update(closure(parent, visiting | {node}))
            return result

        closures = {node: closure(node, set()) for node in records}
        for relation in change["relations"]:
            child, parent = records[relation["child"]], records[relation["parent"]]
            if relation["type"] == "PORTFOLIO_ACCOUNT":
                if (
                    child.dimension != "PORTFOLIO"
                    or parent.dimension != "ACCOUNT"
                    or parents.get(child.digest) != parent.digest
                ):
                    raise RegistryError("U3_PARENT_SUBSTITUTION")
            elif (
                relation["type"] != "OPERATION_LEAF"
                or child.dimension != "OPERATION"
                or parent.dimension != "PORTFOLIO"
            ):
                raise RegistryError("U3_RELATION_CONTEXT")
        for node, identity in records.items():
            expected = 1 if identity.dimension in ("PORTFOLIO", "OPERATION") else 0
            if len(graph[node]) != expected:
                raise RegistryError("U3_AMBIGUOUS_PARENT")
        rule_order = []
        classes: dict[str, dict[str, tuple[int, int]]] = {}
        for rule in change["rules"]:
            exact_fields(rule, ("operation_class", "dimension", "minimum", "maximum"))
            name, kind = rule["operation_class"], rule["dimension"]
            if type(name) is not str or not name or kind not in supported:
                raise RegistryError("U3_RULE_CONTEXT")
            minimum, maximum = (
                positive(rule["minimum"], "cardinality"),
                positive(rule["maximum"], "cardinality"),
            )
            if maximum < minimum or maximum > 256:
                raise RegistryError("U3_RULE_CARDINALITY")
            rule_order.append((name, SCOPE_KINDS.index(kind)))
            classes.setdefault(name, {})[kind] = (minimum, maximum)
        if not classes or rule_order != sorted(set(rule_order)):
            raise RegistryError("U3_RULE_CONTEXT")
        for rules in classes.values():
            if set(rules) != set(supported):
                raise RegistryError("U3_INCOMPLETE_APPLICABILITY")
            for node, identity in records.items():
                if identity.dimension != "OPERATION":
                    continue
                dimensions = [records[d].dimension for d in closures[node]] + ["GLOBAL"]
                if any(
                    not lo <= dimensions.count(kind) <= hi
                    for kind, (lo, hi) in rules.items()
                ):
                    raise RegistryError("U3_INCOMPLETE_CLOSURE")
        return [
            {
                "identity_digest": d,
                "record": identity.body(),
                "entry": entries[d],
                "canonical": canonical_bytes(identity.body()),
                "scope_rank": SCOPE_KINDS.index(identity.dimension),
                "issuer_domain": issuer["issuer_domain"],
                "issuer_rank": issuer["issuer_rank"],
            }
            for d, identity in records.items()
            for issuer in issuers
        ]

    def command(self, raw: bytes, action: str) -> dict[str, Any]:
        state = self.prefix(True)
        payload = self.authenticate(raw)
        digest = RegistryTransitionV1(raw).digest
        if (
            payload["action"] != action
            or payload["deployment_id"] != state["deployment"]
        ):
            raise RegistryError("U3_DEPLOYMENT_OR_ACTION")
        existing = self.sql(
            "SELECT * FROM atis_soe.registry_transition_command WHERE transition_id=$1 AND action=$2",
            ["text", "text"],
            [payload["transition_id"], action],
        )
        if existing:
            if (
                bytes(existing[0]["raw_command"]) != raw
                or existing[0]["command_digest"] != digest
            ):
                raise RegistryError("U3_TRANSITION_CONFLICT")
            return self.expected_result(raw)
        nonce = self.sql(
            "SELECT 1 FROM atis_soe.registry_nonce WHERE deployment_id=$1 AND actor=$2 AND nonce=$3",
            ["text"] * 3,
            [payload["deployment_id"], payload["actor"], payload["nonce"]],
        )
        if nonce:
            raise RegistryError("U3_NONCE_CONFLICT")
        if payload["predecessor_epoch"] != state["catalogue_epoch"]:
            raise RegistryError("U3_STALE_EPOCH")
        barriers = self.validate(payload)
        transition = payload["transition_id"]
        if action == "PREPARE":
            if state["prepared_transition_id"] is not None:
                raise RegistryError("U3_RESERVATION_CONFLICT")
            for item in barriers:
                rows = self.sql(
                    "SELECT * FROM atis_soe.scope_barrier WHERE identity_digest=$1 AND issuer_domain=$2",
                    ["text", "text"],
                    [item["identity_digest"], item["issuer_domain"]],
                )
                if rows:
                    row = rows[0]
                    if (
                        row["lifecycle"] != "ACTIVE"
                        or bytes(row["canonical_bytes"]) != item["canonical"]
                        or row["gate_status"] != "DENY"
                    ):
                        raise RegistryError("U3_FOREIGN_RESERVATION")
                else:
                    self.sql(
                        "INSERT INTO atis_soe.scope_barrier VALUES($1,$2,$3,$4,$5,$6,'RESERVED',$7,'DENY')",
                        ["text", "text", "text", "integer", "bytea", "integer", "text"],
                        [
                            item["identity_digest"],
                            item["issuer_domain"],
                            payload["deployment_id"],
                            item["scope_rank"],
                            item["canonical"],
                            item["issuer_rank"],
                            transition,
                        ],
                    )
                self.sql(
                    "INSERT INTO atis_soe.scope_reservation VALUES($1,$2,$3,$4,$5)",
                    ["text", "text", "text", "bytea", "text"],
                    [
                        transition,
                        item["identity_digest"],
                        item["issuer_domain"],
                        item["canonical"],
                        digest,
                    ],
                )
            self.sql(
                "UPDATE atis_soe.scope_catalogue_state SET prepared_transition_id=$1 WHERE singleton",
                ["text"],
                [transition],
            )
        else:
            preparation = self.sql(
                "SELECT * FROM atis_soe.registry_transition_command WHERE transition_id=$1 AND action='PREPARE'",
                ["text"],
                [transition],
            )
            if state["prepared_transition_id"] != transition or len(preparation) != 1:
                raise RegistryError("U3_MISSING_PREPARATION")
            prior = preparation[0]
            original = self.authenticate(bytes(prior["raw_command"]))
            if (
                prior["command_digest"] != payload["preparation_digest"]
                or canonical_bytes(original["change_set"])
                != canonical_bytes(payload["change_set"])
                or original["actor"] != payload["actor"]
                or original["predecessor_epoch"] != payload["predecessor_epoch"]
            ):
                raise RegistryError("U3_PREPARATION_BINDING")
            # L02: lock existing ACTIVE keys plus every RESERVED future key.
            # The ordered rows already exist. No missing-row locking fiction.
            rows = self.sql(
                "SELECT * FROM atis_soe.scope_barrier WHERE lifecycle IN ('ACTIVE','RESERVED') ORDER BY scope_rank,canonical_bytes,issuer_rank FOR UPDATE"
            )
            expected = {(b["identity_digest"], b["issuer_domain"]): b for b in barriers}
            reservations = self.sql(
                "SELECT * FROM atis_soe.scope_reservation WHERE transition_id=$1",
                ["text"],
                [transition],
            )
            if {
                (r["identity_digest"], r["issuer_domain"]) for r in reservations
            } != set(expected):
                raise RegistryError("U3_MISSING_RESERVATION")
            actual = {(r["identity_digest"], r["issuer_domain"]): r for r in rows}
            if any(
                row["lifecycle"] == "RESERVED"
                and (
                    key not in expected
                    or row["reservation_transition_id"] != transition
                )
                for key, row in actual.items()
            ):
                raise RegistryError("U3_FOREIGN_RESERVATION")
            for row in reservations:
                key = (row["identity_digest"], row["issuer_domain"])
                barrier = actual.get(key)
                if (
                    barrier is None
                    or bytes(row["canonical_bytes"]) != expected[key]["canonical"]
                    or row["preparation_digest"] != prior["command_digest"]
                    or barrier["gate_status"] != "DENY"
                    or bytes(barrier["canonical_bytes"]) != expected[key]["canonical"]
                    or (
                        barrier["lifecycle"] == "RESERVED"
                        and barrier["reservation_transition_id"] != transition
                    )
                ):
                    raise RegistryError("U3_FOREIGN_RESERVATION")
            self.validate(
                payload
            )  # repeat complete metadata validation under L00/L01/L02
            # C013 6.3: intent precedes metadata changes in this same transaction.
            # Neither the intent nor TRANSITION_PENDING is externally committed
            # on its own; any failure rolls back both, retaining reservations.
            self.sql(
                "UPDATE atis_soe.scope_catalogue_state SET status='TRANSITION_PENDING' WHERE singleton"
            )
            self.record_command(raw, payload)
            self.activate_metadata(payload, barriers)
            for key in actual:
                self.sql(
                    "UPDATE atis_soe.scope_barrier SET lifecycle=$3 WHERE identity_digest=$1 AND issuer_domain=$2",
                    ["text", "text", "text"],
                    [key[0], key[1], "ACTIVE" if key in expected else "RETIRED"],
                )
            self.sql(
                "UPDATE atis_soe.scope_catalogue_state SET status='ACTIVE',catalogue_epoch=$1,prepared_transition_id=NULL,last_activation_digest=$2 WHERE singleton",
                ["bigint", "text"],
                [payload["successor_epoch"], digest],
            )
        if action == "PREPARE":
            self.record_command(raw, payload)
        return self.expected_result(raw)

    def record_command(self, raw: bytes, payload: dict[str, Any]) -> None:
        transition, action = payload["transition_id"], payload["action"]
        digest = RegistryTransitionV1(raw).digest
        result = self.expected_result(raw)
        self.sql(
            "INSERT INTO atis_soe.registry_transition_command VALUES($1,$2,$3,$4,$5)",
            ["text", "text", "text", "bytea", "jsonb"],
            [transition, action, digest, raw, json.dumps(result)],
        )
        self.sql(
            "INSERT INTO atis_soe.registry_nonce VALUES($1,$2,$3,$4)",
            ["text"] * 4,
            [payload["deployment_id"], payload["actor"], payload["nonce"], digest],
        )
        self.sql(
            "INSERT INTO atis_soe.scope_catalogue_transition_history(transition_id,action,command_digest,predecessor_epoch,successor_epoch,actor,change_set_digest) VALUES($1,$2,$3,$4,$5,$6,$7)",
            ["text", "text", "text", "bigint", "bigint", "text", "text"],
            [
                transition,
                action,
                digest,
                payload["predecessor_epoch"],
                payload["successor_epoch"],
                payload["actor"],
                payload["change_set_digest"],
            ],
        )

    def activate_metadata(
        self, payload: dict[str, Any], barriers: list[dict[str, Any]]
    ) -> None:
        from .identity_scope import SCOPE_KINDS

        epoch = payload["successor_epoch"]
        wanted = list(dict.fromkeys(b["identity_digest"] for b in barriers))
        self.sql(
            "UPDATE atis_soe.scope_identity SET invalid_after_epoch=$1 WHERE invalid_after_epoch IS NULL AND NOT(identity_digest=ANY($2))",
            ["bigint", "text[]"],
            [epoch, wanted],
        )
        # GLOBAL, ACCOUNT, PORTFOLIO, OPERATION: parents precede children.
        for item in barriers[:: len(payload["change_set"]["issuers"])]:
            if self.sql(
                "SELECT 1 FROM atis_soe.scope_identity WHERE identity_digest=$1",
                ["text"],
                [item["identity_digest"]],
            ):
                continue  # validate() has already required exact existing bytes.
            record = item["record"]
            self.sql(
                "INSERT INTO atis_soe.scope_identity VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,NULL)",
                [
                    "text",
                    "text",
                    "text",
                    "text",
                    "text",
                    "text",
                    "bigint",
                    "integer",
                    "bytea",
                    "jsonb",
                    "bigint",
                ],
                [
                    item["identity_digest"],
                    record["deployment_id"],
                    record["dimension"],
                    record["namespace"],
                    record["value"],
                    record["producer_id"],
                    record["producer_epoch"],
                    item["scope_rank"],
                    item["canonical"],
                    json.dumps(item["entry"]),
                    epoch,
                ],
            )
        self.sql(
            "UPDATE atis_soe.scope_relation SET invalid_after_epoch=$1 WHERE invalid_after_epoch IS NULL",
            ["bigint"],
            [epoch],
        )
        for relation in payload["change_set"]["relations"]:
            self.sql(
                "INSERT INTO atis_soe.scope_relation VALUES($1,$2,$3,$4,NULL)",
                ["text", "text", "text", "bigint"],
                [relation["child"], relation["parent"], relation["type"], epoch],
            )
        self.sql(
            "UPDATE atis_soe.operation_class_rule SET invalid_after_epoch=$1 WHERE invalid_after_epoch IS NULL",
            ["bigint"],
            [epoch],
        )
        for rule in payload["change_set"]["rules"]:
            self.sql(
                "INSERT INTO atis_soe.operation_class_rule VALUES($1,$2,$3,$4,$5,$6,NULL)",
                ["text", "text", "integer", "integer", "integer", "bigint"],
                [
                    rule["operation_class"],
                    rule["dimension"],
                    SCOPE_KINDS.index(rule["dimension"]),
                    rule["minimum"],
                    rule["maximum"],
                    epoch,
                ],
            )

    def read(self, transition: str, digest: str) -> dict[str, Any]:
        self.prefix(False)
        hex_digest(digest)
        rows = self.sql(
            "SELECT * FROM atis_soe.registry_transition_command WHERE transition_id=$1 AND command_digest=$2",
            ["text", "text"],
            [transition, digest],
        )
        if (
            len(rows) != 1
            or RegistryTransitionV1(bytes(rows[0]["raw_command"])).digest != digest
        ):
            raise RegistryError("U3_READBACK_UNRESOLVED")
        self.authenticate(bytes(rows[0]["raw_command"]))
        return self.expected_result(bytes(rows[0]["raw_command"]))


def registry_database_entry(
    spi: Any, action: str, raw: bytes | None, transition: str | None, digest: str | None
) -> str:
    """Private PL/Python entry; SQL owns roles, locks, commit and rollback."""
    transaction = _RegistryTransaction(spi)
    if action == "READ":
        if transition is None or digest is None:
            raise RegistryError("U3_READBACK_UNRESOLVED")
        return json.dumps(transaction.read(transition, digest))
    if raw is None or action not in ("PREPARE", "ACTIVATE"):
        raise RegistryError("U3_ACTION_REJECTED")
    return json.dumps(transaction.command(raw, action))


def _registration_digest(evidence: U3RegistrationEvidenceV1) -> str:
    """Domain-separated identity of all exact sidecar fields, not authority."""
    return sha256(
        b"ATIS-S5-U3-REGISTRATION-V1\0"
        + canonical_bytes(
            [
                evidence.schema,
                evidence.operation_id,
                evidence.request_digest,
                evidence.operation_core_bytes.hex(),
                evidence.signed_scope_envelope_bytes.hex(),
                evidence.final_request_bytes.hex(),
            ]
        )
    ).hexdigest()


class _RegistrationTransaction(_RegistryTransaction):
    """Nonfinancial D2 preparation and registration; all authority remains SQL-owned."""

    def verify_registration(
        self, evidence: U3RegistrationEvidenceV1, state: dict[str, Any], lock: bool
    ) -> tuple[dict[str, Any], list[str]]:
        from .identity_scope import IdentityRecordV1, SCOPE_KINDS, digest_list

        payload, _ = parse_container(evidence.signed_scope_envelope_bytes)
        if payload["deployment_id"] != state["deployment"]:
            raise RegistryError("U3_DEPLOYMENT_REJECTED")
        if payload["catalogue_epoch"] != state["catalogue_epoch"]:
            raise RegistryError("U3_STALE_EPOCH")
        active = self.sql(
            "SELECT raw_command FROM atis_soe.registry_transition_command WHERE command_digest=$1 AND action='ACTIVATE'",
            ["text"],
            [state["last_activation_digest"]],
        )
        if len(active) != 1:
            raise RegistryError("U3_REGISTRY_STATE")
        # prefix() already authenticated this exact active command and compared
        # its complete snapshot while holding L00/L01. This is key discovery;
        # independent identity verification occurs under the required lock set.
        command = parse_container(bytes(active[0]["raw_command"]))[0]
        change = command["change_set"]
        records = {
            IdentityRecordV1.parse(x["record"]).digest: x for x in change["identities"]
        }
        operations = [
            d
            for d, x in records.items()
            if x["record"]["dimension"] == "OPERATION"
            and x["record"]["value"] == evidence.operation_id
        ]
        roots = [d for d, x in records.items() if x["record"]["dimension"] == "GLOBAL"]
        if len(operations) != 1 or len(roots) != 1:
            raise RegistryError("U3_OPERATION_CONTEXT")
        graph: dict[str, list[str]] = {d: [] for d in records}
        for relation in change["relations"]:
            graph[relation["child"]].append(relation["parent"])
        closure = set(roots)

        def visit(digest: str) -> None:
            if digest in closure:
                return
            closure.add(digest)
            for parent in graph[digest]:
                visit(parent)

        visit(operations[0])
        ordered = sorted(
            closure,
            key=lambda d: (SCOPE_KINDS.index(records[d]["record"]["dimension"]), d),
        )
        leaves = [IdentityRecordV1.parse(x) for x in payload["leaf_identities"]]
        if [x.digest for x in leaves] != ordered or payload[
            "closure_digest"
        ] != digest_list(ordered):
            raise RegistryError("U3_INCOMPLETE_CLOSURE")
        rules = [
            x
            for x in change["rules"]
            if x["operation_class"] == payload["operation_class"]
        ]
        if not rules or any(
            not x["minimum"]
            <= sum(v.dimension == x["dimension"] for v in leaves)
            <= x["maximum"]
            for x in rules
        ):
            raise RegistryError("U3_INCOMPLETE_APPLICABILITY")
        for identity, assertion in zip(
            leaves, payload["identity_assertions"], strict=True
        ):
            expected = records[identity.digest]
            if canonical_bytes(identity.body()) != canonical_bytes(
                expected["record"]
            ) or canonical_bytes(assertion) != canonical_bytes(
                expected["assertion"] or {}
            ):
                raise RegistryError("U3_IDENTITY_CONTEXT")
        issuers = self.sql(
            "SELECT * FROM atis_soe.scope_required_issuer ORDER BY issuer_rank"
        )
        wanted = {
            (d, issuer["issuer_domain"]): {
                "canonical": canonical_bytes(records[d]["record"]),
                "scope_rank": SCOPE_KINDS.index(records[d]["record"]["dimension"]),
                "issuer_rank": issuer["issuer_rank"],
            }
            for d in ordered
            for issuer in issuers
        }
        rows = self.sql(
            "SELECT * FROM atis_soe.scope_barrier WHERE identity_digest=ANY($1) ORDER BY scope_rank,canonical_bytes,issuer_rank"
            + (" FOR SHARE" if lock else ""),
            ["text[]"],
            [ordered],
        )
        if {(r["identity_digest"], r["issuer_domain"]) for r in rows} != set(wanted):
            raise RegistryError("U3_MISSING_BARRIER")
        for row in rows:
            item = wanted[(row["identity_digest"], row["issuer_domain"])]
            if (
                row["lifecycle"] != "ACTIVE"
                or row["gate_status"] != "DENY"
                or row["deployment_id"] != state["deployment"]
                or bytes(row["canonical_bytes"]) != item["canonical"]
                or row["scope_rank"] != item["scope_rank"]
                or row["issuer_rank"] != item["issuer_rank"]
            ):
                raise RegistryError("U3_BARRIER_CONTEXT")
        if lock:
            # Re-derive protected metadata after the complete ordered L02 lock set.
            if canonical_bytes(self.snapshot()) != canonical_bytes(change):
                raise RegistryError("U3_REGISTRY_STATE")
        self.validate(command)
        status = self.sql(
            "SELECT atis_soe.verify_u3_signature('SCOPE',$1,$2) AS status",
            ["bytea", "bytea"],
            [evidence.operation_core_bytes, evidence.signed_scope_envelope_bytes],
        )[0]["status"]
        if status != "VALID_SIGNATURE_ONLY":
            raise RegistryError(str(status))
        return payload, ordered

    def registration(
        self, action: str, evidence: U3RegistrationEvidenceV1
    ) -> dict[str, Any]:
        # Conservative L00/L01 UPDATE serializes all D2 mutations before any
        # implicit unique/FK waits. No lower-order lock is acquired after L08.
        state = self.prefix(True)
        payload, members = self.verify_registration(
            evidence, state, action != "PREPARE"
        )
        digest = _registration_digest(evidence)
        identity = self.sql(
            "SELECT * FROM atis_soe.u3_operation_identity WHERE operation_id=$1 FOR UPDATE",
            ["text"],
            [evidence.operation_id],
        )
        nonce = self.sql(
            "SELECT * FROM atis_soe.u3_operation_nonce WHERE deployment_id=$1 AND signer_id=$2 AND nonce=$3",
            ["text"] * 3,
            [payload["deployment_id"], payload["signer_id"], payload["nonce"]],
        )
        history = self.sql(
            "SELECT action,evidence_digest FROM atis_soe.u3_registration_history WHERE operation_id=$1 ORDER BY action",
            ["text"],
            [evidence.operation_id],
        )
        registered = self.sql(
            "SELECT evidence_digest FROM atis_soe.u3_scope_registration WHERE operation_id=$1",
            ["text"],
            [evidence.operation_id],
        )
        membership = self.sql(
            "SELECT identity_digest,ordinal FROM atis_soe.u3_registration_member WHERE operation_id=$1 ORDER BY ordinal",
            ["text"],
            [evidence.operation_id],
        )
        if not identity:
            if nonce:
                raise RegistryError("U3_OPERATION_NONCE_CONFLICT")
            if history or registered or membership:
                raise RegistryError("U3_REGISTRATION_STATE")
            if action != "PREPARE":
                raise RegistryError("U3_OPERATION_IDENTITY_MISSING")
            self.sql(
                "INSERT INTO atis_soe.u3_operation_identity(operation_id,request_digest,evidence_digest,core_bytes,envelope_bytes,final_bytes,deployment_id,signer_id,nonce,catalogue_epoch) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                [
                    "text",
                    "text",
                    "text",
                    "bytea",
                    "bytea",
                    "bytea",
                    "text",
                    "text",
                    "text",
                    "bigint",
                ],
                [
                    evidence.operation_id,
                    evidence.request_digest,
                    digest,
                    evidence.operation_core_bytes,
                    evidence.signed_scope_envelope_bytes,
                    evidence.final_request_bytes,
                    payload["deployment_id"],
                    payload["signer_id"],
                    payload["nonce"],
                    payload["catalogue_epoch"],
                ],
            )
            self.sql(
                "INSERT INTO atis_soe.u3_operation_nonce VALUES($1,$2,$3,$4,$5)",
                ["text"] * 5,
                [
                    payload["deployment_id"],
                    payload["signer_id"],
                    payload["nonce"],
                    evidence.operation_id,
                    digest,
                ],
            )
            self.event(evidence.operation_id, "PREPARE", digest)
        else:
            row = identity[0]
            if (
                row["request_digest"] != evidence.request_digest
                or row["evidence_digest"] != digest
                or bytes(row["core_bytes"]) != evidence.operation_core_bytes
                or bytes(row["envelope_bytes"]) != evidence.signed_scope_envelope_bytes
                or bytes(row["final_bytes"]) != evidence.final_request_bytes
            ):
                raise RegistryError("U3_OPERATION_CONFLICT")
            if (
                any(
                    row[k] != payload[k]
                    for k in ("deployment_id", "signer_id", "nonce", "catalogue_epoch")
                )
                or len(nonce) != 1
                or nonce[0]["operation_id"] != evidence.operation_id
                or nonce[0]["evidence_digest"] != digest
            ):
                raise RegistryError("U3_REGISTRATION_STATE")
            expected_history = [{"action": "PREPARE", "evidence_digest": digest}]
            if registered:
                expected_history.append(
                    {"action": "REGISTER", "evidence_digest": digest}
                )
                if registered != [{"evidence_digest": digest}] or membership != [
                    {"identity_digest": d, "ordinal": i} for i, d in enumerate(members)
                ]:
                    raise RegistryError("U3_REGISTRATION_STATE")
            elif membership:
                raise RegistryError("U3_REGISTRATION_STATE")
            if history != expected_history:
                raise RegistryError("U3_REGISTRATION_STATE")
        if action == "REGISTER" and not registered:
            self.sql(
                "INSERT INTO atis_soe.u3_scope_registration VALUES($1,$2)",
                ["text", "text"],
                [evidence.operation_id, digest],
            )
            for ordinal, member in enumerate(members):
                # FK only targets the already-held L08 operation identity/registration.
                self.sql(
                    "INSERT INTO atis_soe.u3_registration_member VALUES($1,$2,$3)",
                    ["text", "text", "integer"],
                    [evidence.operation_id, member, ordinal],
                )
            self.event(evidence.operation_id, "REGISTER", digest)
        return {
            "status": "REGISTERED"
            if action == "REGISTER" or (action == "READ" and registered)
            else "IDENTITY_PREPARED",
            "operation_id": evidence.operation_id,
            "request_digest": evidence.request_digest,
            "evidence_digest": digest,
            "catalogue_epoch": payload["catalogue_epoch"],
        }

    def event(self, operation: str, action: str, digest: str) -> None:
        self.sql(
            "INSERT INTO atis_soe.u3_registration_history(operation_id,action,evidence_digest) VALUES($1,$2,$3)",
            ["text"] * 3,
            [operation, action, digest],
        )


def registration_database_entry(
    spi: Any,
    action: str,
    operation: str,
    digest: str,
    core: bytes,
    envelope: bytes,
    final: bytes,
) -> str:
    if action not in ("PREPARE", "REGISTER", "READ"):
        raise RegistryError("U3_ACTION_REJECTED")
    try:
        evidence = U3RegistrationEvidenceV1(
            "u3-registration-evidence/v1", operation, digest, core, envelope, final
        )
    except IdentityScopeError as exc:
        raise RegistryError("U3_REGISTRATION_BINDING") from exc
    return json.dumps(_RegistrationTransaction(spi).registration(action, evidence))


def submit_registration(
    connection: Any,
    evidence: U3RegistrationEvidenceV1,
    *,
    action: str,
    timeout_ms: int = 5000,
) -> dict[str, Any]:
    """Explicit standalone transaction; never dispatches workers or financial work."""
    from psycopg import Error as DatabaseError

    names = {
        "PREPARE": "prepare_operation_identity_v1",
        "REGISTER": "register_signed_scope_v1",
        "READ": "read_signed_scope_v1",
    }
    if action not in names or type(evidence) is not U3RegistrationEvidenceV1:
        raise RegistryError("U3_ACTION_REJECTED")
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 30000:
        raise RegistryError("U3_INVALID_STATEMENT_BUDGET")
    if connection.info.transaction_status.name != "IDLE":
        raise RegistryError("U3_CONNECTION_NOT_IDLE")
    committing = False
    try:
        connection.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
        connection.execute(
            "SELECT pg_catalog.set_config('statement_timeout',%s,true),pg_catalog.set_config('lock_timeout',%s,true)",
            (str(timeout_ms), str(min(timeout_ms, 2000))),
        )
        result = connection.execute(
            "SELECT atis_soe." + names[action] + "(%s,%s,%s,%s,%s)",
            evidence.sql_arguments(),
        ).fetchone()[0]
        committing = True
        connection.execute("COMMIT")
        if not isinstance(result, dict):
            raise RegistryCommitUncertain(_registration_digest(evidence))
        return result
    except BaseException as exc:
        if committing and getattr(exc, "sqlstate", None) not in ("40001", "40P01"):
            raise RegistryCommitUncertain(_registration_digest(evidence)) from exc
        try:
            connection.execute("ROLLBACK")
        except DatabaseError as rollback_error:
            exc.add_note(
                "Rollback acknowledgement unavailable: " + type(rollback_error).__name__
            )
        raise
