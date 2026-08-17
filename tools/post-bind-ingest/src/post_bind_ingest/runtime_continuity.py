from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .continuity_gate import CONTINUITY_GATE_VERSION, TelemetryGapState, evaluate_attach_renew_continuity
from .invariants import canonical_dumps
from .normalizer import normalize_receiver_events


RUNTIME_CONTINUITY_VERSION = "0.1.0"
SCANNER_CONTRACT_REF = "are-scanner-api-v1@v1.3.0"


class RuntimeContinuityError(ValueError):
    """A signed batch cannot advance the persistent continuity chain."""


class PersistentContinuityRuntime:
    """Vendor-local signed ingest with persistent, fail-closed continuity state.

    Raw receiver events remain in this local SQLite database. Only decisions,
    hashes, reason codes, and evidence references are written to the audit spine.
    """

    def __init__(self, path: str | Path, *, enabled: bool = False) -> None:
        self.path = Path(path)
        self.enabled = enabled
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        self.db.close()

    def _migrate(self) -> None:
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS streams (
              stream_key TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, agent_id TEXT NOT NULL,
              deployment_id TEXT NOT NULL, collector_id TEXT NOT NULL,
              last_accepted_seq INTEGER NOT NULL DEFAULT 0, latest_heartbeat_at TEXT,
              gap_active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS local_events (
              stream_key TEXT NOT NULL, batch_seq INTEGER NOT NULL, batch_id TEXT NOT NULL UNIQUE,
              event_json TEXT NOT NULL, accepted_at TEXT NOT NULL,
              PRIMARY KEY(stream_key, batch_seq)
            );
            CREATE TABLE IF NOT EXISTS audit_spine (
              record_no INTEGER PRIMARY KEY AUTOINCREMENT, stream_key TEXT NOT NULL,
              decision_kind TEXT NOT NULL, reason_code TEXT NOT NULL, action_class TEXT,
              evidence_ref TEXT, component_versions TEXT NOT NULL, input_hash TEXT NOT NULL,
              decision_json TEXT NOT NULL, recorded_at TEXT NOT NULL
            );
            """
        )
        self.db.commit()

    @staticmethod
    def _key(tenant_id: str, agent_id: str, deployment_id: str, collector_id: str) -> str:
        return "\x1f".join((tenant_id, agent_id, deployment_id, collector_id))

    def ingest_signed_batch(
        self,
        payload: Mapping[str, Any],
        *,
        jwt_keyring: Mapping[str, str],
        tenant_id: str,
        deployment_id: str,
        collector_id: str,
        batch_seq: int,
        heartbeat_at: str,
    ) -> dict[str, Any]:
        agent_id = str(payload.get("agent") or "")
        stream_key = self._key(tenant_id, agent_id, deployment_id, collector_id)
        evidence_ref = f"receiver-batch:{payload.get('batch_id', 'unknown')}"
        if not self.enabled:
            self._audit(stream_key, "ingest_refused", "continuity_runtime_disabled", None, evidence_ref, payload)
            raise RuntimeContinuityError("continuity_runtime_disabled")
        normalized = normalize_receiver_events(dict(payload), jwt_keyring=jwt_keyring)
        row = self.db.execute("SELECT * FROM streams WHERE stream_key=?", (stream_key,)).fetchone()
        expected = (int(row["last_accepted_seq"]) if row else 0) + 1
        if batch_seq != expected:
            reason = "sequence_replayed_or_reset" if batch_seq < expected else "sequence_skipped"
            self._audit(stream_key, "ingest_refused", reason, None, evidence_ref, {"expected": expected, "received": batch_seq})
            raise RuntimeContinuityError(reason)
        enriched = []
        for event in normalized:
            copy = dict(event)
            copy["tenant_id"] = tenant_id
            copy["agent_id"] = agent_id
            copy["deployment_id"] = deployment_id
            copy["collector_id"] = collector_id
            copy["batch_seq"] = batch_seq
            enriched.append(copy)
        accepted_at = self._now()
        with self.db:
            self.db.execute(
                "INSERT INTO streams VALUES(?,?,?,?,?,?,?,COALESCE((SELECT gap_active FROM streams WHERE stream_key=?),1)) "
                "ON CONFLICT(stream_key) DO UPDATE SET last_accepted_seq=excluded.last_accepted_seq,latest_heartbeat_at=excluded.latest_heartbeat_at",
                (stream_key, tenant_id, agent_id, deployment_id, collector_id, batch_seq, heartbeat_at, stream_key),
            )
            self.db.execute(
                "INSERT INTO local_events VALUES(?,?,?,?,?)",
                (stream_key, batch_seq, str(payload["batch_id"]), json.dumps(enriched, sort_keys=True, separators=(",", ":")), accepted_at),
            )
        return {"accepted": True, "batch_seq": batch_seq, "evidence_ref": evidence_ref}

    def decide(self, *, action_class: str, tenant_id: str, agent_id: str, deployment_id: str,
               collector_id: str, evaluated_at: str, heartbeat_max_age_seconds: int) -> dict[str, Any]:
        stream_key = self._key(tenant_id, agent_id, deployment_id, collector_id)
        row = self.db.execute("SELECT * FROM streams WHERE stream_key=?", (stream_key,)).fetchone()
        events = []
        for stored in self.db.execute("SELECT event_json FROM local_events WHERE stream_key=? ORDER BY batch_seq", (stream_key,)):
            events.extend(json.loads(stored[0]))
        if row and row["latest_heartbeat_at"]:
            events.append({"event_type": "security_event", "logged_at": row["latest_heartbeat_at"], "payload": {
                "action_class": "heartbeat", "tenant_id": tenant_id, "agent_id": agent_id,
                "deployment_id": deployment_id, "collector_id": collector_id,
                "batch_seq": row["last_accepted_seq"], "heartbeat_at": row["latest_heartbeat_at"]}})
        prior = TelemetryGapState(tenant_id, agent_id, deployment_id, collector_id, bool(row["gap_active"]) if row else True)
        decision = evaluate_attach_renew_continuity(events, action_class=action_class, tenant_id=tenant_id,
            agent_id=agent_id, deployment_id=deployment_id, collector_id=collector_id, evaluated_at=evaluated_at,
            heartbeat_max_age_seconds=heartbeat_max_age_seconds, prior_gap_state=prior)
        data = decision.to_dict()
        reason = "eligible" if decision.permitted else ",".join(decision.gap_state.reasons)
        self._audit(stream_key, "eligibility_decision", reason, action_class,
                    f"stream:{hashlib.sha256(stream_key.encode()).hexdigest()}", self._stable_decision(data))
        if row:
            with self.db:
                self.db.execute("UPDATE streams SET gap_active=? WHERE stream_key=?", (int(decision.gap_state.active), stream_key))
        return data

    def audit_records(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute("SELECT * FROM audit_spine ORDER BY record_no")]

    def _audit(self, stream_key: str, kind: str, reason: str, action: str | None,
               evidence_ref: str | None, inputs: Any) -> None:
        stable = canonical_dumps(inputs)
        digest = "sha256:" + hashlib.sha256(stable).hexdigest()
        decision = json.dumps({"kind": kind, "reason_code": reason, "action_class": action,
                               "evidence_ref": evidence_ref, "input_hash": digest}, sort_keys=True, separators=(",", ":"))
        versions = json.dumps({"runtime": RUNTIME_CONTINUITY_VERSION, "continuity_gate": CONTINUITY_GATE_VERSION,
                               "scanner_contract": SCANNER_CONTRACT_REF}, sort_keys=True, separators=(",", ":"))
        with self.db:
            self.db.execute("INSERT INTO audit_spine(stream_key,decision_kind,reason_code,action_class,evidence_ref,component_versions,input_hash,decision_json,recorded_at) VALUES(?,?,?,?,?,?,?,?,?)",
                            (stream_key, kind, reason, action, evidence_ref, versions, digest, decision, self._now()))

    @staticmethod
    def _stable_decision(data: Mapping[str, Any]) -> dict[str, Any]:
        stable = dict(data)
        stable.pop("audit_events", None)
        return stable

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")
