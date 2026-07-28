"""Durable per-invocation checkpoint seam for the Syllable corpus run."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Iterable

MAX_HTTP_ATTEMPTS = 700
MAX_WALL_SECONDS = 36 * 60 * 60


class ExecutionCapReached(RuntimeError):
    """Raised before an authorized execution cap would be exceeded."""


@dataclass(frozen=True)
class Invocation:
    case_id: str
    seed: int
    payload: dict[str, Any]

    @property
    def key(self) -> str:
        return f"{self.case_id}::seed={self.seed}"


class CheckpointLedger:
    """Atomic ledger with separate semantic-invocation and HTTP-attempt counts."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], float] = time.time,
        max_http_attempts: int = MAX_HTTP_ATTEMPTS,
        max_wall_seconds: float = MAX_WALL_SECONDS,
    ) -> None:
        self.path = path
        self.clock = clock
        self.max_http_attempts = max_http_attempts
        self.max_wall_seconds = max_wall_seconds
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if state.get("schema_version") != 1:
                raise ValueError("unsupported checkpoint schema")
            return state
        return {
            "schema_version": 1,
            "started_at_epoch_s": self.clock(),
            "semantic_invocations_started": 0,
            "semantic_invocations_terminal": 0,
            "http_attempts": 0,
            "invocations": {},
        }

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (json.dumps(self.state, indent=2, sort_keys=True) + "\n").encode()
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}."
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
            dir_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def _check_wall_cap(self) -> None:
        elapsed = self.clock() - float(self.state["started_at_epoch_s"])
        if elapsed >= self.max_wall_seconds:
            raise ExecutionCapReached("36-hour wall-clock cap reached")

    def is_terminal(self, invocation: Invocation) -> bool:
        row = self.state["invocations"].get(invocation.key)
        return bool(row and row.get("terminal"))

    def start(self, invocation: Invocation) -> None:
        self._check_wall_cap()
        if self.is_terminal(invocation):
            raise ValueError("terminal invocation cannot be started again")
        self.state["semantic_invocations_started"] += 1
        self.state["invocations"][invocation.key] = {
            "case_id": invocation.case_id,
            "seed": invocation.seed,
            "terminal": False,
            "started_at_epoch_s": self.clock(),
        }
        self._persist()

    def record_http_attempt(self, detail: dict[str, Any]) -> None:
        self._check_wall_cap()
        if self.state["http_attempts"] >= self.max_http_attempts:
            raise ExecutionCapReached("700 HTTP-attempt cap reached")
        self.state["http_attempts"] += 1
        self.state["last_http_attempt"] = {
            **detail,
            "recorded_at_epoch_s": self.clock(),
        }
        self._persist()

    def terminal(
        self,
        invocation: Invocation,
        *,
        outcome: str,
        evidence_ref: str | None,
    ) -> None:
        row = self.state["invocations"].get(invocation.key)
        if row is None or row.get("terminal"):
            raise ValueError("invocation must be started and non-terminal")
        row.update(
            {
                "terminal": True,
                "outcome": outcome,
                "evidence_ref": evidence_ref,
                "terminal_at_epoch_s": self.clock(),
            }
        )
        self.state["semantic_invocations_terminal"] += 1
        self._persist()


def run_invocations(
    invocations: Iterable[Invocation],
    ledger: CheckpointLedger,
    execute: Callable[
        [Invocation, Callable[[dict[str, Any]], None]],
        tuple[str, str | None],
    ],
) -> None:
    """Execute non-terminal rows; never reuse a cached response on resume."""
    for invocation in invocations:
        if ledger.is_terminal(invocation):
            continue
        ledger.start(invocation)
        outcome, evidence_ref = execute(invocation, ledger.record_http_attempt)
        ledger.terminal(invocation, outcome=outcome, evidence_ref=evidence_ref)
