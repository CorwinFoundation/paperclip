"""QA Authority core for BEAAA-29079 steps 1-3.

The public edge calls this module with an already signed corroboration result.
Paperclip credentials are deliberately absent from this process.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_QA_IDENTITIES = {"backbond-qa"}


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS bindings (
  id INTEGER PRIMARY KEY,
  repository_id INTEGER NOT NULL,
  pr_number INTEGER NOT NULL CHECK(pr_number > 0),
  implementation_issue_id TEXT NOT NULL,
  qa_issue_id TEXT NOT NULL,
  binding_revision INTEGER NOT NULL,
  active INTEGER NOT NULL CHECK(active IN (0, 1)),
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  superseded_by INTEGER REFERENCES bindings(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_binding
  ON bindings(repository_id, pr_number) WHERE active = 1;
CREATE TABLE IF NOT EXISTS dispositions (
  id INTEGER PRIMARY KEY,
  qa_issue_id TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK(outcome IN ('APPROVED', 'CHANGES_REQUESTED')),
  candidate_sha TEXT NOT NULL CHECK(length(candidate_sha) = 40),
  evidence_refs TEXT NOT NULL,
  author_identity_key TEXT NOT NULL,
  author_agent_id_snapshot TEXT NOT NULL,
  created_by_run_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  supersedes_disposition_id INTEGER REFERENCES dispositions(id)
);
CREATE TRIGGER IF NOT EXISTS dispositions_no_update
BEFORE UPDATE ON dispositions
BEGIN
  SELECT RAISE(ABORT, 'dispositions are append-only');
END;
CREATE TRIGGER IF NOT EXISTS dispositions_no_delete
BEFORE DELETE ON dispositions
BEGIN
  SELECT RAISE(ABORT, 'dispositions are append-only');
END;
"""


@dataclass(frozen=True)
class Decision:
    conclusion: str
    reason: str
    disposition_id: int | None = None


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def bind(
    db: sqlite3.Connection,
    *,
    repository_id: int,
    pr_number: int,
    implementation_issue_id: str,
    qa_issue_id: str,
    created_by: str,
) -> int:
    revision = db.execute(
        "SELECT COALESCE(MAX(binding_revision), 0) + 1 FROM bindings WHERE repository_id=? AND pr_number=?",
        (repository_id, pr_number),
    ).fetchone()[0]
    cur = db.execute(
        "INSERT INTO bindings(repository_id,pr_number,implementation_issue_id,qa_issue_id,binding_revision,active,created_by,created_at) VALUES(?,?,?,?,?,1,?,?)",
        (repository_id, pr_number, implementation_issue_id, qa_issue_id, revision, created_by, utcnow()),
    )
    db.commit()
    return int(cur.lastrowid)


def record_disposition(db: sqlite3.Connection, event: dict[str, Any]) -> int:
    sha = str(event.get("candidate_sha", "")).lower()
    evidence = event.get("evidence_refs")
    identity = event.get("author_identity_key")
    if not SHA_RE.fullmatch(sha):
        raise ValueError("candidate_sha must be a full 40-hex SHA")
    if not isinstance(evidence, list) or not evidence or not all(isinstance(x, str) and x for x in evidence):
        raise ValueError("evidence_refs must be a non-empty string array")
    if identity not in ALLOWED_QA_IDENTITIES:
        raise ValueError("unauthorized QA identity")
    if event.get("paperclip_role") != "qa" or event.get("paperclip_status") not in {"active", "idle"}:
        raise ValueError("QA identity corroboration failed")
    active = db.execute(
        "SELECT * FROM bindings WHERE qa_issue_id=? AND active=1", (event.get("qa_issue_id"),)
    ).fetchall()
    if len(active) != 1:
        raise ValueError("exactly one active canonical binding is required")
    cur = db.execute(
        "INSERT INTO dispositions(qa_issue_id,outcome,candidate_sha,evidence_refs,author_identity_key,author_agent_id_snapshot,created_by_run_id,created_at,supersedes_disposition_id) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            event["qa_issue_id"], event["outcome"], sha, json.dumps(evidence, separators=(",", ":")),
            identity, event["author_agent_id_snapshot"], event["created_by_run_id"], utcnow(),
            event.get("supersedes_disposition_id"),
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def evaluate(
    db: sqlite3.Connection,
    *,
    repository_id: int,
    pr_number: int,
    head_sha: str,
    corroboration: dict[str, Any] | None,
) -> Decision:
    if not SHA_RE.fullmatch(head_sha):
        return Decision("failure", "invalid live head SHA")
    if not corroboration or corroboration.get("valid") is not True:
        return Decision("failure", "Paperclip corroboration unavailable or disagreeing")
    if corroboration.get("qa_issue_status") != "done":
        return Decision("failure", "canonical QA gate is not done")
    rows = db.execute(
        "SELECT * FROM bindings WHERE repository_id=? AND pr_number=? AND active=1",
        (repository_id, pr_number),
    ).fetchall()
    if len(rows) != 1:
        return Decision("failure", "exactly one active binding is required")
    binding = rows[0]
    if corroboration.get("qa_issue_id") != binding["qa_issue_id"]:
        return Decision("failure", "corroborated QA issue disagrees with binding")
    latest = db.execute(
        "SELECT * FROM dispositions WHERE qa_issue_id=? ORDER BY id DESC LIMIT 1",
        (binding["qa_issue_id"],),
    ).fetchone()
    if latest is None:
        return Decision("failure", "no structured disposition")
    if latest["outcome"] != "APPROVED":
        return Decision("failure", "latest disposition is not APPROVED", latest["id"])
    if latest["candidate_sha"] != head_sha:
        return Decision("failure", "approval does not match live head", latest["id"])
    if corroboration.get("disposition_id") != latest["id"]:
        return Decision("failure", "corroborated disposition is stale", latest["id"])
    return Decision("success", "exact candidate has current evidence-backed QA approval", latest["id"])
