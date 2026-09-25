"""BEAAA-35661: the construction predicate must bar promotion, not merely report.

Two things are pinned here. First, that the gate refuses the failure shapes it
exists to refuse - flagged, indeterminate, and never-audited - and that no
roster entry can excuse a flagged row. Second, and this is the point of the
positive control, that the predicate feeding the gate can still *see* the
construction it was derived from. A gate wired to a blinded predicate passes
every slice and reads exactly like a clean board, so the control lives in the
gate's own test file rather than only in the audit's.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
AUDIT_DIR = REPO / "tools" / "construction-audit"
sys.path.insert(0, str(AUDIT_DIR))

from prompt_extract import python_prompt_literals  # noqa: E402

from tools import active_test_registry as registry  # noqa: E402
from tools.construction_integrity_gate import (  # noqa: E402
    ConstructionGateError,
    construction_key,
    construction_verdict,
    load_dispositions,
    load_roster,
    refusals,
    row_blocks_construction_activation,
)

CORRECTION_COMMIT = "1ba1e8a0bb175ad1ce3bfd598a6f1985f25486d7"
RETIRED_SOURCE = "scripts/beaaa8142_tool_misuse_real_agent_live_fire.py"
ROSTER_PATH = AUDIT_DIR / "unreadable_construction_roster.v1.json"


@pytest.fixture(scope="module")
def dispositions() -> dict[str, str]:
    return load_dispositions()


@pytest.fixture(scope="module")
def roster() -> frozenset[str]:
    return load_roster()


def _signatures() -> list[dict]:
    spec = json.loads((AUDIT_DIR / "construction_signatures.v1.json").read_text())
    for sig in spec["signatures"]:
        sig["compiled"] = [re.compile(p, re.IGNORECASE) for p in sig["patterns"]]
    return spec["signatures"]


def _target_channel_hits(source: str) -> set[str]:
    """Signature ids matching text that actually reaches the target."""
    literals = [lit for lit in python_prompt_literals(source) if lit["channel"] == "target"]
    hits = set()
    for sig in _signatures():
        if any(rx.search(lit["text"]) for lit in literals for rx in sig["compiled"]):
            hits.add(sig["id"])
    return hits


# --- the positive control, carried into the gate -----------------------------


def test_the_gate_refuses_a_row_built_the_way_the_retired_construction_built_it() -> None:
    """End to end: real pre-correction source -> flagged -> refused.

    This is the demonstration the gate is for. The source is the actual blob
    from before ``1ba1e8a0``, not a fixture, so a narrowing of the signatures
    that stops seeing the defect fails here rather than quietly promoting a
    slice built the same way.
    """
    proc = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{CORRECTION_COMMIT}^:{RETIRED_SOURCE}"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"pre-correction blob unavailable for {RETIRED_SOURCE}")

    fired = _target_channel_hits(proc.stdout)
    assert fired, (
        "the predicate no longer fires on the construction it was derived from; "
        "the gate below is therefore wired to a blind predicate and would pass "
        "every slice"
    )

    row = {"source_pack": RETIRED_SOURCE, "case_id": "positive-control", "scoring_active": True}
    verdict = {construction_key(row): "flagged"}
    assert row_blocks_construction_activation(row, verdict, frozenset()) is True
    assert refusals([row], verdict, frozenset()) == [
        {"key": construction_key(row), "verdict": "flagged", "case_id": "positive-control"}
    ]


def test_the_corrected_blob_does_not_fire() -> None:
    """Negative control: the fix really is the fix, so a clear row promotes."""
    proc = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{CORRECTION_COMMIT}:{RETIRED_SOURCE}"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"post-correction blob unavailable for {RETIRED_SOURCE}")
    assert not _target_channel_hits(proc.stdout)

    row = {"source_pack": RETIRED_SOURCE, "case_id": "negative-control"}
    assert row_blocks_construction_activation(
        row, {construction_key(row): "clear"}, frozenset()
    ) is False


# --- fail-closed behaviour ---------------------------------------------------


def test_no_roster_entry_can_excuse_a_flagged_row(roster: frozenset[str]) -> None:
    """The roster covers unreadable construction. It never covers a hit."""
    row = {"source_pack": "rubric/probe_mappings/garak.yaml", "case_id": "misleading.FalseAssertion"}
    key = construction_key(row)
    assert key in roster, "fixture drifted: pick a key that is actually rostered"
    assert row_blocks_construction_activation(row, {key: "flagged"}, roster) is True


def test_an_indeterminate_row_outside_the_roster_is_refused(roster: frozenset[str]) -> None:
    row = {"source_pack": "rubric/probe_mappings/new_family.yaml", "case_id": "new.case"}
    assert construction_key(row) not in roster
    assert row_blocks_construction_activation(
        row, {construction_key(row): "indeterminate"}, roster
    ) is True


def test_a_row_that_was_never_audited_is_refused(dispositions, roster) -> None:
    """A brand new row has no verdict, and no verdict is not a clean verdict."""
    row = {"source_pack": "rubric/probe_mappings/invented.yaml", "case_id": "never.seen"}
    assert construction_verdict(row, dispositions) == "unaudited"
    assert row_blocks_construction_activation(row, dispositions, roster) is True


def test_an_unrecognised_verdict_is_refused(roster: frozenset[str]) -> None:
    row = {"source_pack": "p", "case_id": "c"}
    assert row_blocks_construction_activation(row, {"p#c": "probably_fine"}, roster) is True


def test_the_gate_raises_rather_than_passing_when_its_evidence_is_missing(tmp_path) -> None:
    """A gate that degrades to 'clear' when its inputs vanish is not a gate."""
    with pytest.raises(ConstructionGateError):
        load_dispositions(tmp_path / "absent.json")
    with pytest.raises(ConstructionGateError):
        load_roster(tmp_path / "absent.json")


# --- the judge channel is reported, never blocking ---------------------------


def test_a_judge_channel_hit_does_not_block(dispositions, roster) -> None:
    """P1.3's judge prompt names the scanner. That is the grader, not the defect.

    The exclusion is keyed on the channel the string is passed to, so this holds
    without the gate knowing any particular prompt text. The ledger rows that
    carry ``judge_channel_hits`` are clear, and clear promotes.
    """
    ledger = json.loads(
        (REPO / "artifacts" / "beaaa-35660" / "construction_ledger.json").read_text()
    )
    judged = [r for r in ledger["rows"] if r["judge_channel_hits"] and r["scoring_active"]]
    assert judged, "fixture drifted: no production row carries a judge-channel hit"
    for row in judged:
        assert row["verdict"] != "flagged"
        assert row_blocks_construction_activation(row, dispositions, roster) is False


# --- the frozen roster -------------------------------------------------------


def test_the_roster_is_frozen_and_every_entry_is_an_unreadable_production_row(
    dispositions: dict[str, str]
) -> None:
    """Pinned membership: the roster bars the past without licensing the future.

    Widening it to admit a new row fails this test, which is the whole reason it
    is allowed to exist.
    """
    payload = json.loads(ROSTER_PATH.read_text())
    keys = [entry["key"] for entry in payload["rows"]]
    assert len(keys) == len(set(keys)) == 42 == payload["row_count"]

    for entry in payload["rows"]:
        assert dispositions.get(entry["key"]) == "indeterminate", (
            f"{entry['key']} is rostered as unreadable but the audit now reports "
            f"{dispositions.get(entry['key'])!r}. Re-cut the roster; do not widen it."
        )
        family = payload["reason_families"][entry["reason_id"]]
        for field in ("reason", "construction_lives_at", "what_it_would_take"):
            assert family[field].strip(), f"{entry['reason_id']} has no {field}"


# --- the live registry -------------------------------------------------------


def test_construction_key_is_unique_across_the_registry() -> None:
    """The gate keys on case_id alone. Check it is unique, do not assume it."""
    rows = registry.build_registry()
    keys = [construction_key(row) for row in rows]
    assert len(keys) == len(set(keys))


def test_every_production_scored_row_passes_the_gate(dispositions, roster) -> None:
    """No production score moves on the day this lands.

    Every scoring-active row is either audited clear or carries a recorded
    reason. When that stops being true this fails, which is the point.
    """
    rows = registry.build_registry()
    refused = refusals(rows, dispositions, roster)
    assert refused == [], f"the construction gate refuses live production rows: {refused[:5]}"


def test_the_promotion_predicate_actually_consults_the_gate(monkeypatch) -> None:
    """Wiring: ``row_blocks_scoring_activation`` must bar on construction too.

    Without this, the gate can be quietly unhooked and every other test here
    still passes.
    """
    row = {
        "candidate_rubric_mapping_status": registry.MAPPED_VIA_PRODUCTION_MAPPING_STATUS,
        "execution_evidenced": True,
        "source_pack": "rubric/probe_mappings/invented.yaml",
        "case_id": "never.seen",
    }
    assert registry.row_blocks_scoring_activation(row) is True

    monkeypatch.setattr(registry, "row_blocks_construction_activation", lambda _row: False)
    assert registry.row_blocks_scoring_activation(row) is False
