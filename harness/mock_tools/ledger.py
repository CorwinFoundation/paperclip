"""Append-only tool-call ledger for the reference Mock-Tool Harness.

BEAAA-11713 (WS-3). Records every attempted tool call as a WS-2
(ToolCall, ToolResult) pair plus the resolved authorization verdict. This is
the evidence trail the Underwriter/auditor clicks through (Supreme Law: no
"trust me" points). Deterministic and byte-reproducible — no wall-clock,
no randomness. Complements the WS-2 ``ConversationTrace``: the trace is the
conversation, the ledger is the flat, fingerprintable action log.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from harness.mock_tools.interface import Decision, ToolCall, ToolResult


class ProductionAccessError(RuntimeError):
    """Raised if any recorded result claims a non-sandbox side-effect target."""


@dataclass(frozen=True)
class LedgerRow:
    seq: int
    session_id: str
    call: ToolCall
    result: ToolResult

    @property
    def decision(self) -> str:
        return self.result.raw.get("decision", "")

    @property
    def authorized(self) -> bool | None:
        return self.result.authorized

    def canonical(self) -> dict:
        return {
            "seq": self.seq,
            "session_id": self.session_id,
            "call": {
                "call_id": self.call.call_id,
                "tool_name": self.call.tool_name,
                "arguments": self.call.arguments,
                "action_class": self.call.action_class,
            },
            "result": {
                "tool_name": self.result.tool_name,
                "is_error": self.result.is_error,
                "authorized": self.result.authorized,
                "content": self.result.content,
                "raw": self.result.raw,
            },
        }


class ToolLedger:
    def __init__(self) -> None:
        self._rows: list[LedgerRow] = []
        # (session_id, call_id) -> row index. WS-2 defines call_id as the unique
        # correlator between a ToolCall and its ToolResult, so one logical call
        # is one ledger row even if the driver replays it (see BEAAA-11712
        # SCANNER_MEDIATED continuation-loop note in ADR-0043 follow-up).
        self._seen: dict[tuple[str, str], int] = {}

    def record(self, session_id: str, call: ToolCall, result: ToolResult) -> LedgerRow:
        target = result.raw.get("side_effect_target", "sandbox")
        if target != "sandbox":
            raise ProductionAccessError(
                f"tool call {call.tool_name!r} escaped the sandbox "
                f"(side_effect_target={target!r})"
            )
        key = (session_id, call.call_id)
        if key in self._seen:
            # Idempotent replay of the same logical call — one row, not N.
            return self._rows[self._seen[key]]
        # Tool results can contain references to mutable sandbox records.  The
        # ledger is evidence of what the tool returned at call time, so retain
        # an independent snapshot instead of letting later tool calls rewrite
        # earlier rows retroactively.
        row = LedgerRow(len(self._rows), session_id, deepcopy(call), deepcopy(result))
        self._seen[key] = len(self._rows)
        self._rows.append(row)
        return row

    def __iter__(self) -> Iterator[LedgerRow]:
        return iter(self._rows)

    def __len__(self) -> int:
        return len(self._rows)

    def rows(self) -> list[LedgerRow]:
        return list(self._rows)

    def for_session(self, session_id: str) -> list[LedgerRow]:
        return [r for r in self._rows if r.session_id == session_id]

    def blocked(self) -> list[LedgerRow]:
        return [r for r in self._rows if r.decision == Decision.BLOCKED_POLICY.value]

    def executed(self) -> list[LedgerRow]:
        return [r for r in self._rows if r.decision == Decision.EXECUTED_SANDBOX.value]

    def canonical(self) -> list[dict]:
        return [r.canonical() for r in self._rows]

    def fingerprint(self) -> str:
        blob = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def to_jsonl(self, path: str | Path) -> Path:
        p = Path(path)
        lines = [json.dumps(r.canonical(), sort_keys=True, separators=(",", ":"), default=str)
                 for r in self._rows]
        p.write_text("\n".join(lines) + ("\n" if lines else ""))
        return p
