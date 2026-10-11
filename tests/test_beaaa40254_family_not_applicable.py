"""BEAAA-40254: per-family N/A is reported apart from could_not_run.

The starter-100@5 live acceptance run showed the 14 out-of-scope-actions rows
of a chat-only target (top-level N/A, not scored) as ``could_not_run: 14`` in
the family block. They must read ``not_applicable: 14, could_not_run: 0`` with
no change to the score, ran, or top-level could_not_run.
"""

from __future__ import annotations

import json

from api.scan_handlers import _starter_score_fields
from scanners.customer_agent_walker import customer_agent_probe_run_fn
from scanners.starter_judge import (
    COULD_NOT_RUN,
    FAILED,
    NOT_APPLICABLE,
    PASSED,
    starter_score,
)
from scanners.starter_pack_v5 import STARTER_PACK_V5_ID

OOS = "out_of_scope_actions"


def _legacy_score(rows: list[dict]) -> dict:
    """The pre-40254 formula, kept here to prove there is no score delta."""
    passed = sum(1 for r in rows if r.get("verdict") == PASSED)
    failed = sum(1 for r in rows if r.get("verdict") == FAILED)
    ran = passed + failed
    return {
        "score": round(100.0 * passed / ran, 1) if ran else None,
        "ran": ran,
        "passed": passed,
        "failed": failed,
        "could_not_run": sum(1 for r in rows if r.get("verdict") == COULD_NOT_RUN),
    }


class _Judge:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        verdict = FAILED if self.calls % 4 == 0 else PASSED
        return '{"verdict": "%s", "reason": "ok"}' % verdict


class _ChatOnlyRunner:
    pack_id = STARTER_PACK_V5_ID
    has_tools = False
    _messages_payload = None

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.sent = 0

    def complete(self, prompt: str) -> str:
        self.sent += 1
        return "I can't do that, but I can help with something else."

    def record_starter_verdict(self, **row) -> None:
        self.rows.append({"pack_id": STARTER_PACK_V5_ID, **row})


def _chat_only_rows(monkeypatch) -> list[dict]:
    monkeypatch.setattr(
        "scanners.customer_agent_walker.judge_client_from_env", lambda: _Judge()
    )
    runner = _ChatOnlyRunner()
    out = list(customer_agent_probe_run_fn(runner))
    assert len(out) == 100 and len(runner.rows) == 100
    return runner.rows


def test_chat_only_oos_family_reports_not_applicable_not_could_not_run(monkeypatch):
    rows = _chat_only_rows(monkeypatch)
    assert sum(1 for r in rows if r["verdict"] == NOT_APPLICABLE) == 14
    s = starter_score(rows)
    assert s["families"][OOS] == {
        "ran": 0,
        "passed": 0,
        "needs_attention": 0,
        "could_not_run": 0,
        "not_applicable": 14,
    }
    for family, block in s["families"].items():
        if family != OOS:
            assert block["not_applicable"] == 0, family


def test_no_score_delta_against_pre_40254_formula(monkeypatch):
    rows = _chat_only_rows(monkeypatch)
    s = starter_score(rows)
    legacy = _legacy_score(rows)
    assert {k: s[k] for k in legacy} == legacy
    assert s["score"] is not None and s["ran"] == 86
    assert s["could_not_run"] == 0


def test_could_not_run_and_unknown_verdicts_stay_could_not_run():
    rows = [
        {"family": "a", "verdict": PASSED},
        {"family": "a", "verdict": COULD_NOT_RUN},
        {"family": "a", "verdict": NOT_APPLICABLE},
        {"family": "a", "verdict": "something_else"},
    ]
    s = starter_score(rows)
    assert s["families"]["a"] == {
        "ran": 1,
        "passed": 1,
        "needs_attention": 0,
        "could_not_run": 2,
        "not_applicable": 1,
    }
    assert s["score"] == 100.0 and s["could_not_run"] == 1


def test_api_envelope_carries_family_not_applicable(monkeypatch):
    rows = _chat_only_rows(monkeypatch)
    fields = _starter_score_fields(rows)
    assert fields["starter_pack_id"] == STARTER_PACK_V5_ID
    block = json.loads(json.dumps(fields))["starter_score"]["families"][OOS]
    assert block["not_applicable"] == 14 and block["could_not_run"] == 0
