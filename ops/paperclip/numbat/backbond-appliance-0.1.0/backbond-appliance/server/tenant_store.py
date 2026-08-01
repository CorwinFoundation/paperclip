"""Tenant-isolated persistence for appliance findings (counts and hashes only)."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass


def _slug(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Scope:
    customer_id: str
    agent_id: str


class TenantStore:
    """One SQLite partition per (customer, agent), never a shared row store."""

    def __init__(self, root: str, *, quota_findings: int = 100_000,
                 retention_seconds: int = 30 * 86400):
        self.root = root
        self.quota_findings = quota_findings
        self.retention_seconds = retention_seconds
        self.lock = threading.RLock()
        os.makedirs(root, mode=0o700, exist_ok=True)
        self.audit_path = os.path.join(root, "audit.ndjson")

    def _path(self, scope: Scope) -> str:
        # Opaque directories prevent identifiers leaking through filenames.
        return os.path.join(self.root, _slug(scope.customer_id), _slug(scope.agent_id), "tracker.sqlite3")

    def _connect(self, scope: Scope) -> sqlite3.Connection:
        path = self._path(scope)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        db = sqlite3.connect(path)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE IF NOT EXISTS findings (
            batch_hash TEXT NOT NULL, event_id TEXT NOT NULL, rule_id TEXT,
            severity TEXT, evidence_hash TEXT NOT NULL, accepted_at INTEGER NOT NULL,
            encryption_key_id TEXT NOT NULL,
            PRIMARY KEY(batch_hash, event_id, rule_id))""")
        return db

    def validate_append(self, scope: Scope, findings: list[dict], key_id: str) -> None:
        """Validate an append without creating or changing a tenant partition."""
        if not key_id:
            raise ValueError("tenant encryption key binding required")
        path = self._path(scope)
        current = 0
        if os.path.exists(path):
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                current = db.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
            finally:
                db.close()
        if current + len(findings) > self.quota_findings:
            self.audit("quota_rejected", scope, {"count": len(findings)})
            raise ValueError("tenant finding quota exceeded")

    def append(self, scope: Scope, batch_hash: str, findings: list[dict], key_id: str) -> None:
        now = int(time.time())
        with self.lock, self._connect(scope) as db:
            db.executemany(
                "INSERT OR IGNORE INTO findings VALUES (?,?,?,?,?,?,?)",
                [(batch_hash, str(f.get("event_id", "")), f.get("rule_id"),
                  f.get("severity"), str(f.get("evidence_hash", "")), now, key_id)
                 for f in findings],
            )
        self.audit("tracker_append", scope, {"count": len(findings), "batch_hash": batch_hash})

    def summary(self, scope: Scope) -> dict:
        path = self._path(scope)
        if not os.path.exists(path):
            self.audit("hub_read", scope, {"count": 0})
            return {"findings_total": 0, "findings_by_rule": {}, "findings_by_severity": {}}
        with self.lock, self._connect(scope) as db:
            cutoff = int(time.time()) - self.retention_seconds
            db.execute("DELETE FROM findings WHERE accepted_at < ?", (cutoff,))
            total = db.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
            by_rule = dict(db.execute("SELECT rule_id, COUNT(*) FROM findings GROUP BY rule_id"))
            by_severity = dict(db.execute("SELECT severity, COUNT(*) FROM findings GROUP BY severity"))
        self.audit("hub_read", scope, {"count": total})
        return {"findings_total": total, "findings_by_rule": by_rule,
                "findings_by_severity": by_severity}

    def delete(self, scope: Scope) -> None:
        path = self._path(scope)
        with self.lock:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except FileNotFoundError:
                    pass
        self.audit("tenant_deleted", scope, {})

    def audit(self, action: str, scope: Scope, detail: dict) -> None:
        row = {"at": int(time.time()), "action": action,
               "customer_ref": _slug(scope.customer_id), "agent_ref": _slug(scope.agent_id),
               **detail}
        with self.lock, open(self.audit_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
