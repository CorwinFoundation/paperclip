"""One-way budget admission over a preserved Quick ledger; no target calls.

The optional singleton applies to every pilot in this database, including
legacy serial callers. Existing counters, authorizations and evidence stay put.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timezone

TABLE = "quick_global_budget"
DDL = """CREATE TABLE quick_global_budget
    (id INTEGER PRIMARY KEY CHECK(id=1), cap INTEGER NOT NULL,
     reserved INTEGER NOT NULL, baseline_sha256 TEXT NOT NULL)"""
PHASE_DDL = """CREATE TABLE quick_selected_phases
    (authorization TEXT, selection_sha256 TEXT, phase TEXT, binding TEXT, started_at TEXT,
     PRIMARY KEY(authorization,selection_sha256,phase))"""


def ledger_digest(db):
    schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    rows = {}
    for kind, name, _, _ in schema:
        if kind == "table":
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError("known_ledger_table_required")
            rows[name] = sorted(db.execute('SELECT * FROM "' + name + '"').fetchall(),
                                key=lambda row: json.dumps(row, sort_keys=True))
    return hashlib.sha256(json.dumps({"schema": schema, "rows": rows},
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def checked_budget(db, *, required=False, expected=None, additional=0):
    """Call within the same transaction as any subsequent call reservation."""
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()
    if not exists:
        if required or expected is not None:
            raise ValueError("reviewed_global_budget_required")
        return None  # historical deployments retain their existing batch limits
    rows = db.execute("SELECT id,cap,reserved,baseline_sha256 FROM quick_global_budget").fetchall()
    if (len(rows) != 1 or rows[0][0] != 1 or type(additional) is not int or additional < 0
            or type(rows[0][1]) is not int or not 1 <= rows[0][1] <= 2500
            or type(rows[0][2]) is not int or not 0 <= rows[0][2] <= rows[0][1]
            or not re.fullmatch(r"[0-9a-f]{64}", rows[0][3] or "")):
        raise ValueError("invalid_global_budget")
    _, cap, reserved, baseline = rows[0]
    spec = {"baseline_ledger_sha256": baseline, "global_call_ceiling": cap, "retained_reserved_calls": reserved}
    if expected is not None and expected != {"schema": "backbond-quick-global-budget/v1", **spec}:
        raise ValueError("global_budget_binding_changed")
    calls = db.execute("SELECT calls FROM pilots").fetchall()
    if any(type(row[0]) is not int or row[0] < 0 for row in calls):
        raise ValueError("invalid_preserved_call_counter")
    actual = sum(row[0] for row in calls)
    if actual + reserved + additional > cap:
        raise ValueError("global_call_budget_exhausted")
    return {"schema": "backbond-quick-global-budget/v1", **spec,
            "actual_calls": actual, "charged_or_reserved": actual + reserved,
            "remaining": cap - actual - reserved}


def admit_global_budget(path: Path, *, baseline_sha256: str, cap: int, reserved: int):
    """Explicit one-shot local migration, before fresh registrations/readiness.

    A reviewed, exact logical snapshot is required. A prior admission cannot be
    replayed or modified, even with a larger cap or a smaller retained reserve.
    """
    if (not path.is_absolute() or not re.fullmatch(r"[0-9a-f]{64}", baseline_sha256 or "")
            or type(cap) is not int or not 1 <= cap <= 2500
            or type(reserved) is not int or not 0 <= reserved <= cap):
        raise ValueError("reviewed_bounded_global_budget_required")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True)
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone():
                raise ValueError("global_budget_already_admitted")
            if db.execute("PRAGMA quick_check").fetchone() != ("ok",) or ledger_digest(db) != baseline_sha256:
                raise ValueError("exact_preserved_ledger_required")
            if (db.execute("SELECT 1 FROM pilots WHERE inflight!=0").fetchone()
                    or db.execute("SELECT 1 FROM leases").fetchone()
                    or db.execute("SELECT 1 FROM quick_conversation_leases").fetchone()
                    or db.execute("SELECT 1 FROM quick_batch_submissions WHERE status IN ('submitting','unknown','timed_out','mismatch')").fetchone()):
                raise ValueError("unresolved_ledger_requires_operator_review")
            db.execute(DDL)
            db.execute("INSERT INTO quick_global_budget VALUES (1,?,?,?)", (cap, reserved, baseline_sha256))
            db.execute(PHASE_DDL)
            return checked_budget(db, required=True)
    finally:
        db.close()


def claim_selected_phase(path: Path, *, authorization: str, selection_sha256: str,
                         phase: str, binding: str, capture_binding=None):
    """Commit once before target work; a failed/uncertain phase stays consumed."""
    if (not path.is_absolute() or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',authorization)
            or phase not in {'capture','confirm'}
            or not all(re.fullmatch(r'[0-9a-f]{64}',v or '') for v in [selection_sha256,binding])
            or (phase=='confirm' and not re.fullmatch(r'[0-9a-f]{64}',capture_binding or ''))):
        raise ValueError('bound_selected_phase_required')
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE');checked_budget(db,required=True)
            auth=db.execute('SELECT halted FROM quick_batch_authorizations WHERE id=?',(authorization,)).fetchone()
            if auth!=(0,):raise ValueError('active_epoch_authorization_required')
            if phase=='confirm':
                capture=db.execute("SELECT binding FROM quick_selected_phases WHERE authorization=? AND selection_sha256=? AND phase='capture'",
                    (authorization,selection_sha256)).fetchone()
                if capture!=(capture_binding,):raise ValueError('original_capture_phase_binding_required')
            try:
                db.execute('INSERT INTO quick_selected_phases VALUES (?,?,?,?,?)',
                    (authorization,selection_sha256,phase,binding,datetime.now(timezone.utc).isoformat()))
            except sqlite3.IntegrityError:
                raise ValueError('selected_phase_already_claimed_no_replay') from None
    finally:db.close()
