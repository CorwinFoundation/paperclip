"""Durable evaluator reservations. No prompt, endpoint or credential is stored.

Reservations are never refunded: timeouts and unknown provider outcomes may
still be billed. Retrying reserves again. The owner supplies a persistent local
SQLite path, reused across worker processes and service restarts.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

USD10_MICRO = 10_000_000


class EvaluatorBudgetError(RuntimeError):
    pass


class EvaluatorBudget:
    def __init__(self, path: str | Path, run_id: str, *, limit_micro: int) -> None:
        if (not isinstance(run_id, str) or not run_id or len(run_id) > 128
                or type(limit_micro) is not int or not 0 < limit_micro <= USD10_MICRO
                or str(path) == ':memory:'):
            raise EvaluatorBudgetError('invalid evaluator budget configuration')
        self.path, self.run_id, self.limit_micro = str(path), run_id, limit_micro
        # The directory must already exist. Never fall back to memory or a
        # temporary database when durable storage is unavailable.
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS evaluator_budget (
                run_id TEXT PRIMARY KEY, limit_micro INTEGER NOT NULL,
                reserved_micro INTEGER NOT NULL CHECK(reserved_micro >= 0))''')
            db.execute('INSERT OR IGNORE INTO evaluator_budget VALUES (?, ?, 0)',
                       (run_id, limit_micro))
            stored = db.execute('SELECT limit_micro FROM evaluator_budget WHERE run_id=?',
                                (run_id,)).fetchone()
            if stored != (limit_micro,):
                raise EvaluatorBudgetError('evaluator budget identity changed')

    def reserve(self, upper_bound_micro: int) -> None:
        if type(upper_bound_micro) is not int or not 0 < upper_bound_micro <= USD10_MICRO:
            raise EvaluatorBudgetError('unknown or invalid evaluator cost bound')
        # BEGIN IMMEDIATE serializes competing processes before checking funds.
        # Commit finishes before the caller is allowed to make an HTTP request.
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute('BEGIN IMMEDIATE')
            cursor = db.execute('''UPDATE evaluator_budget
                SET reserved_micro=reserved_micro+?
                WHERE run_id=? AND limit_micro=?
                  AND reserved_micro <= limit_micro-?''',
                (upper_bound_micro, self.run_id, self.limit_micro, upper_bound_micro))
            if cursor.rowcount != 1:
                raise EvaluatorBudgetError('evaluator budget exhausted or unavailable')
