"""Timeline read API for Log Tracker v0.

BEAAA-6162 implements child F of BEAAA-6151 §9: the BackBond-internal
``GET /api/v1/tracker/policy/{policy_id}/timeline`` read surface with
read-time redaction. The module is deliberately consumer-side: it joins
already-persisted tracker rows and does not import Scanner scoring, rubric,
band, judge, or pricing code.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


CONTRACT_VERSION = "log-tracker-v0@v0.1.2"
TRACKER_VERSION = "v0.1.2"
AUTH_POSTURE = "mtls_client_cert_plus_service_account_jwt"
REDACTION_CLASSES = {"clear", "hash", "redact-on-receipt", "drop"}
TIMELINE_READ_ROLES = {
    "underwriter",
    "head_of_uw",
    "claims_architect",
    "actuary",
    "auditor",
    "cto",
    "scanner_engineer",
}
AUDIT_ROLES = {"auditor", "cto", "scanner_engineer"}
CLAIM_REVIEW_KINDS = {"submitted", "paid", "denied"}
SHA256_HEX_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SECRET_FAMILY_RE = re.compile(
    r"(?i)(sk-[a-z0-9_-]{12,}|AKIA[0-9A-Z]{16}|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
)


class TimelineReadError(ValueError):
    """Raised when the timeline read API cannot serve the request."""


@dataclass(frozen=True)
class TimelineReadAuth:
    """Minimal v0 auth context for the internal read endpoint."""

    role: str
    subject: str


class SQLiteTimelineStore:
    """SQLite-backed local adapter for the Log Tracker v0 read API.

    Production storage is Postgres per BEAAA-6151 §3. This local adapter keeps
    the read API executable and regression-testable without a database service.
    The table columns mirror the contract shapes needed by child F.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scanner_runs (
                scan_id              TEXT PRIMARY KEY,
                agent_id             TEXT NOT NULL,
                customer_id          TEXT NOT NULL,
                purpose              TEXT NOT NULL,
                run_timestamp        TEXT NOT NULL,
                scanner_version      TEXT NOT NULL,
                corpus_version       TEXT NOT NULL,
                score_json           TEXT NOT NULL,
                inserted_at          TEXT NOT NULL,
                supersedes_id        TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_timeline_scanner_agent_time
                ON scanner_runs (agent_id, run_timestamp);

            CREATE TABLE IF NOT EXISTS evidence_refs (
                evidence_ref_id      TEXT PRIMARY KEY,
                scan_id              TEXT NOT NULL,
                ref_json             TEXT NOT NULL,
                redaction_class      TEXT NOT NULL,
                inserted_at          TEXT NOT NULL,
                FOREIGN KEY (scan_id) REFERENCES scanner_runs(scan_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS ix_timeline_evidence_scan
                ON evidence_refs (scan_id);

            CREATE TABLE IF NOT EXISTS policy_events (
                policy_event_id      TEXT PRIMARY KEY,
                policy_id            TEXT NOT NULL,
                agent_id             TEXT NOT NULL,
                customer_id          TEXT NOT NULL,
                event_kind           TEXT NOT NULL,
                scan_id_priced       TEXT,
                event_t              TEXT NOT NULL,
                premium_band         TEXT,
                wording_version      TEXT NOT NULL,
                metadata             TEXT NOT NULL,
                redaction_class      TEXT NOT NULL,
                inserted_at          TEXT NOT NULL,
                supersedes_id        TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_timeline_policy_events_policy_time
                ON policy_events (policy_id, event_t);

            CREATE TABLE IF NOT EXISTS claim_events (
                claim_event_id       TEXT PRIMARY KEY,
                claim_id             TEXT NOT NULL,
                policy_id            TEXT NOT NULL,
                agent_id             TEXT NOT NULL,
                customer_id          TEXT NOT NULL,
                peril                TEXT NOT NULL,
                severity_class       TEXT NOT NULL,
                claim_kind           TEXT NOT NULL,
                event_t              TEXT NOT NULL,
                scan_id_at_bind      TEXT,
                scan_id_at_loss      TEXT,
                evidence_refs_uri    TEXT,
                metadata             TEXT NOT NULL,
                redaction_class      TEXT NOT NULL,
                inserted_at          TEXT NOT NULL,
                supersedes_id        TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_timeline_claim_events_policy_time
                ON claim_events (policy_id, event_t);
            """
        )

    def append_scanner_run(
        self,
        *,
        scan_id: str,
        agent_id: str,
        customer_id: str,
        purpose: str,
        run_timestamp: str,
        scanner_version: str,
        corpus_version: str,
        score_json: Mapping[str, Any],
        evidence_refs: list[Mapping[str, Any]],
        evidence_redaction_class: Mapping[str, str] | None = None,
        inserted_at: str | None = None,
        supersedes_id: str | None = None,
    ) -> None:
        inserted_at = inserted_at or _now_iso()
        self._conn.execute(
            "INSERT INTO scanner_runs "
            "(scan_id, agent_id, customer_id, purpose, run_timestamp, scanner_version, "
            "corpus_version, score_json, inserted_at, supersedes_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scan_id,
                agent_id,
                customer_id,
                purpose,
                run_timestamp,
                scanner_version,
                corpus_version,
                json.dumps(dict(score_json), sort_keys=True),
                inserted_at,
                supersedes_id,
            ),
        )
        class_map = dict(evidence_redaction_class or {})
        for ordinal, ref in enumerate(evidence_refs):
            evidence_ref_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{scan_id}:{ordinal}"))
            self._conn.execute(
                "INSERT INTO evidence_refs "
                "(evidence_ref_id, scan_id, ref_json, redaction_class, inserted_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    evidence_ref_id,
                    scan_id,
                    json.dumps(dict(ref), sort_keys=True),
                    json.dumps(class_map, sort_keys=True),
                    inserted_at,
                ),
            )

    def append_policy_event(
        self,
        *,
        policy_event_id: str,
        policy_id: str,
        agent_id: str,
        customer_id: str,
        event_kind: str,
        event_t: str,
        wording_version: str,
        redaction_class: Mapping[str, str],
        metadata: Mapping[str, Any] | None = None,
        scan_id_priced: str | None = None,
        premium_band: str | None = None,
        inserted_at: str | None = None,
        supersedes_id: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO policy_events "
            "(policy_event_id, policy_id, agent_id, customer_id, event_kind, "
            "scan_id_priced, event_t, premium_band, wording_version, metadata, "
            "redaction_class, inserted_at, supersedes_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                policy_event_id,
                policy_id,
                agent_id,
                customer_id,
                event_kind,
                scan_id_priced,
                event_t,
                premium_band,
                wording_version,
                json.dumps(dict(metadata or {}), sort_keys=True),
                json.dumps(_validate_redaction_class(redaction_class), sort_keys=True),
                inserted_at or _now_iso(),
                supersedes_id,
            ),
        )

    def append_claim_event(
        self,
        *,
        claim_event_id: str,
        claim_id: str,
        policy_id: str,
        agent_id: str,
        customer_id: str,
        peril: str,
        severity_class: str,
        claim_kind: str,
        event_t: str,
        redaction_class: Mapping[str, str],
        metadata: Mapping[str, Any] | None = None,
        scan_id_at_bind: str | None = None,
        scan_id_at_loss: str | None = None,
        evidence_refs_uri: str | None = None,
        inserted_at: str | None = None,
        supersedes_id: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO claim_events "
            "(claim_event_id, claim_id, policy_id, agent_id, customer_id, peril, "
            "severity_class, claim_kind, event_t, scan_id_at_bind, scan_id_at_loss, "
            "evidence_refs_uri, metadata, redaction_class, inserted_at, supersedes_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                claim_event_id,
                claim_id,
                policy_id,
                agent_id,
                customer_id,
                peril,
                severity_class,
                claim_kind,
                event_t,
                scan_id_at_bind,
                scan_id_at_loss,
                evidence_refs_uri,
                json.dumps(dict(metadata or {}), sort_keys=True),
                json.dumps(_validate_redaction_class(redaction_class), sort_keys=True),
                inserted_at or _now_iso(),
                supersedes_id,
            ),
        )

    def policy_exists(self, policy_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM policy_events WHERE policy_id = ? "
            "UNION SELECT 1 FROM claim_events WHERE policy_id = ? LIMIT 1",
            (policy_id, policy_id),
        ).fetchone()
        return row is not None

    def get_policy_timeline(
        self,
        policy_id: str,
        *,
        auth: TimelineReadAuth,
        include_audit: bool = False,
        query: Mapping[str, str] | None = None,
        generated_at: str | None = None,
    ) -> dict[str, Any]:
        _validate_query(query or {})
        if auth.role not in TIMELINE_READ_ROLES:
            raise TimelineReadError("role_forbidden")
        if include_audit and auth.role not in AUDIT_ROLES:
            raise TimelineReadError("audit_scope_forbidden")
        if auth.role == "claims_architect" and not self._policy_has_claim_review_event(policy_id):
            raise TimelineReadError("claims_architect_policy_scope_forbidden")
        if not self.policy_exists(policy_id):
            raise TimelineReadError("policy_not_found")

        policy_rows = self._policy_rows(policy_id)
        claim_rows = self._claim_rows(policy_id)
        scanner_rows = self._scanner_rows(_timeline_scan_ids(policy_rows, claim_rows))

        timeline: list[dict[str, Any]] = []
        for row in scanner_rows:
            score = json.loads(row["score_json"])
            evidence_refs = [
                apply_read_redaction(
                    json.loads(ref["ref_json"]),
                    json.loads(ref["redaction_class"]),
                )
                for ref in self._evidence_rows(row["scan_id"])
            ]
            event = {
                "kind": "scanner_run",
                "event_id": row["scan_id"],
                "event_t": row["run_timestamp"],
                "scan_id": row["scan_id"],
                "agent_id": row["agent_id"],
                "customer_id": row["customer_id"],
                "purpose": row["purpose"],
                "are_total": score.get("are_total"),
                "subscores": score.get("subscores"),
                "band": score.get("band"),
                "confidence": score.get("confidence"),
                "scanner_version": score.get("scanner_version") or row["scanner_version"],
                "corpus_version": score.get("corpus_version") or row["corpus_version"],
                "model_versions": score.get("model_versions"),
                "seed": score.get("seed"),
                "uncovered_dimensions": score.get("uncovered_dimensions", []),
                "evidence_refs": evidence_refs,
            }
            _add_audit(event, row, include_audit)
            timeline.append(drop_absent(event))

        for row in policy_rows:
            event = {
                "kind": "policy_event",
                "event_id": row["policy_event_id"],
                "event_t": row["event_t"],
                "policy_event_id": row["policy_event_id"],
                "event_kind": row["event_kind"],
                "scan_id_priced": row["scan_id_priced"],
                "wording_version": row["wording_version"],
                "premium_band": row["premium_band"],
                "metadata": apply_read_redaction(
                    json.loads(row["metadata"]),
                    json.loads(row["redaction_class"]),
                ),
            }
            _add_audit(event, row, include_audit)
            timeline.append(drop_absent(event))

        for row in claim_rows:
            event = {
                "kind": "claim_event",
                "event_id": row["claim_event_id"],
                "event_t": row["event_t"],
                "claim_event_id": row["claim_event_id"],
                "claim_id": row["claim_id"],
                "claim_kind": row["claim_kind"],
                "peril": row["peril"],
                "severity_class": row["severity_class"],
                "scan_id_at_bind": row["scan_id_at_bind"],
                "scan_id_at_loss": row["scan_id_at_loss"],
                "evidence_refs_uri": row["evidence_refs_uri"],
                "metadata": apply_read_redaction(
                    json.loads(row["metadata"]),
                    json.loads(row["redaction_class"]),
                ),
            }
            _add_audit(event, row, include_audit)
            timeline.append(drop_absent(event))

        timeline.sort(key=lambda row: (row["event_t"], row["kind"], row["event_id"]))
        anchor = policy_rows[0] if policy_rows else claim_rows[0]
        return {
            "contract_version": CONTRACT_VERSION,
            "tracker_version": TRACKER_VERSION,
            "policy_id": policy_id,
            "agent_id": anchor["agent_id"],
            "customer_id": anchor["customer_id"],
            "generated_at": generated_at or _now_iso(),
            "timeline": timeline,
            "pagination": {"limit": 500, "next_cursor": None},
        }

    def _policy_rows(self, policy_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM policy_events WHERE policy_id = ? ORDER BY event_t ASC, policy_event_id ASC",
            (policy_id,),
        ).fetchall()

    def _claim_rows(self, policy_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM claim_events WHERE policy_id = ? ORDER BY event_t ASC, claim_event_id ASC",
            (policy_id,),
        ).fetchall()

    def _scanner_rows(self, scan_ids: set[str]) -> list[sqlite3.Row]:
        if not scan_ids:
            return []
        placeholders = ",".join("?" for _ in scan_ids)
        return self._conn.execute(
            f"SELECT * FROM scanner_runs WHERE scan_id IN ({placeholders})",
            tuple(sorted(scan_ids)),
        ).fetchall()

    def _evidence_rows(self, scan_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM evidence_refs WHERE scan_id = ? ORDER BY evidence_ref_id ASC",
            (scan_id,),
        ).fetchall()

    def _policy_has_claim_review_event(self, policy_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM claim_events WHERE policy_id = ? AND claim_kind IN ('submitted', 'paid', 'denied') LIMIT 1",
            (policy_id,),
        ).fetchone()
        return row is not None

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SQLiteTimelineStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def authorize_timeline_read(
    *,
    headers: Mapping[str, str],
    service_token: str,
) -> TimelineReadAuth:
    """Authorize the internal timeline read API.

    v0 represents the mTLS + service-account JWT posture with explicit headers:
    ``x-client-cert-verified: true``, ``authorization: Bearer <token>``,
    ``x-backbond-role``, and ``x-backbond-sub``. The eventual HTTP service can
    swap this for decoded JWT claims without changing timeline semantics.
    """
    normalized = {k.lower(): v for k, v in headers.items()}
    if normalized.get("x-client-cert-verified", "").lower() not in {"1", "true", "yes"}:
        raise TimelineReadError("mtls_required")
    auth = normalized.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise TimelineReadError("service_jwt_required")
    if not hmac.compare_digest(auth.removeprefix("Bearer "), service_token):
        raise TimelineReadError("service_jwt_invalid")
    role = normalized.get("x-backbond-role", "")
    if role not in TIMELINE_READ_ROLES:
        raise TimelineReadError("role_forbidden")
    subject = normalized.get("x-backbond-sub", "")
    if not subject:
        raise TimelineReadError("subject_required")
    return TimelineReadAuth(role=role, subject=subject)


def handle_timeline_read(
    *,
    store: SQLiteTimelineStore,
    policy_id: str,
    headers: Mapping[str, str],
    service_token: str,
    query: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    auth = authorize_timeline_read(headers=headers, service_token=service_token)
    include_audit = (query or {}).get("include_audit", "false").lower() in {"1", "true", "yes"}
    return store.get_policy_timeline(
        policy_id,
        auth=auth,
        include_audit=include_audit,
        query=query,
    )


def apply_read_redaction(value: Mapping[str, Any], redaction_class: Mapping[str, str]) -> dict[str, Any]:
    out = json.loads(json.dumps(dict(value)))
    class_map = _validate_redaction_class(redaction_class)
    for field_path, class_name in sorted(class_map.items()):
        parts = _field_parts(field_path)
        if not parts:
            continue
        if class_name == "drop":
            out = _drop_path(out, parts)
        elif class_name == "hash":
            out = _set_path(out, parts, _hash_value)
        elif class_name == "redact-on-receipt":
            out = _set_path(out, parts, _redact_value)
        elif class_name == "clear":
            continue
    return drop_absent(out)


def drop_absent(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: pruned
            for key, item in value.items()
            if (pruned := drop_absent(item)) is not None
        }
    if isinstance(value, list):
        return [pruned for item in value if (pruned := drop_absent(item)) is not None]
    return value


def _validate_redaction_class(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise TimelineReadError("redaction_class_invalid")
    out = dict(value)
    for field_path, class_name in out.items():
        if not isinstance(field_path, str) or field_path == "":
            raise TimelineReadError("redaction_class_path_invalid")
        if class_name not in REDACTION_CLASSES:
            raise TimelineReadError(f"redaction_class_invalid:{field_path}")
    return out


def _validate_query(query: Mapping[str, str]) -> None:
    if any(key in {"show_raw", "raw", "bypass_redaction"} for key in query):
        raise TimelineReadError("raw_bypass_forbidden")


def _timeline_scan_ids(policy_rows: list[sqlite3.Row], claim_rows: list[sqlite3.Row]) -> set[str]:
    scan_ids = {row["scan_id_priced"] for row in policy_rows if row["scan_id_priced"]}
    for row in claim_rows:
        if row["scan_id_at_bind"]:
            scan_ids.add(row["scan_id_at_bind"])
        if row["scan_id_at_loss"]:
            scan_ids.add(row["scan_id_at_loss"])
    return scan_ids


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash_value(value: Any) -> str:
    if isinstance(value, str) and SHA256_HEX_RE.match(value):
        return value
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return SECRET_FAMILY_RE.sub("[REDACTED:read_floor]", value)
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def _field_parts(field_path: str) -> list[str]:
    return [part for part in field_path.replace("[]", ".*").split(".") if part]


def _drop_path(value: Any, parts: list[str]) -> Any:
    if not parts:
        return value
    if isinstance(value, dict):
        key = parts[0]
        if key in value:
            if len(parts) == 1:
                value.pop(key, None)
            else:
                value[key] = _drop_path(value[key], parts[1:])
    elif isinstance(value, list):
        if parts[0] == "*":
            for idx, item in enumerate(value):
                value[idx] = _drop_path(item, parts[1:])
        elif parts[0].isdigit():
            idx = int(parts[0])
            if 0 <= idx < len(value):
                value[idx] = _drop_path(value[idx], parts[1:])
    return value


def _set_path(value: Any, parts: list[str], transform) -> Any:
    if not parts:
        return transform(value)
    if isinstance(value, dict):
        key = parts[0]
        if key in value:
            value[key] = _set_path(value[key], parts[1:], transform)
    elif isinstance(value, list):
        if parts[0] == "*":
            for idx, item in enumerate(value):
                value[idx] = _set_path(item, parts[1:], transform)
        elif parts[0].isdigit():
            idx = int(parts[0])
            if 0 <= idx < len(value):
                value[idx] = _set_path(value[idx], parts[1:], transform)
    return value


def _add_audit(event: dict[str, Any], row: sqlite3.Row, include_audit: bool) -> None:
    if include_audit:
        event["audit"] = {
            "inserted_at": row["inserted_at"],
            "supersedes_id": row["supersedes_id"],
        }


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "AUTH_POSTURE",
    "CONTRACT_VERSION",
    "SHA256_HEX_RE",
    "TRACKER_VERSION",
    "TimelineReadAuth",
    "TimelineReadError",
    "SQLiteTimelineStore",
    "apply_read_redaction",
    "authorize_timeline_read",
    "handle_timeline_read",
]
