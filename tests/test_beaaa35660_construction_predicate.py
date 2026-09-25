"""BEAAA-35660: the construction predicate must fire on the retired construction.

A clean audit result is only meaningful if the predicate can still detect the
defect it was derived from. These tests pin both directions against the real
pre- and post-``1ba1e8a0`` blobs, so a future narrowing of the signatures that
would silently produce a zero fails CI instead.
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

CORRECTION_COMMIT = "1ba1e8a0bb175ad1ce3bfd598a6f1985f25486d7"
RETIRED = {
    "scripts/beaaa8142_tool_misuse_real_agent_live_fire.py": {
        "S1_ANNOUNCE_PROBE_ID",
        "S2_NARRATE_SCENARIO",
        "S3_REFUSAL_MENU",
    },
    "scripts/beaaa8125_tool_misuse_live_fire.py": {
        "S1_ANNOUNCE_PROBE_ID",
        "S2_NARRATE_SCENARIO",
    },
    "harness/live_tool_agent.py": {
        "S4_HARNESS_SELF_ANNOUNCE",
        "S5_IN_CHANNEL_ATTACK_DELIVERY",
    },
}


def _signatures() -> list[dict]:
    spec = json.loads((AUDIT_DIR / "construction_signatures.v1.json").read_text())
    for sig in spec["signatures"]:
        sig["compiled"] = [re.compile(p, re.IGNORECASE) for p in sig["patterns"]]
    return spec["signatures"]


def _blob(ref: str, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{ref}:{path}"],
        capture_output=True, text=True,
    )
    return proc.stdout if proc.returncode == 0 else None


def _fired(source: str) -> set[str]:
    """Signature ids that match target-channel prompt text in this source."""
    literals = [lit for lit in python_prompt_literals(source) if lit["channel"] == "target"]
    hits = set()
    for sig in _signatures():
        for lit in literals:
            if any(rx.search(lit["text"]) for rx in sig["compiled"]):
                hits.add(sig["id"])
                break
    return hits


@pytest.mark.parametrize("path,expected", sorted(RETIRED.items()))
def test_predicate_fires_on_retired_construction(path: str, expected: set[str]) -> None:
    """Positive control: the pre-correction blob must still trip the predicate."""
    before = _blob(f"{CORRECTION_COMMIT}^", path)
    if before is None:
        pytest.skip(f"pre-correction blob unavailable for {path}")
    fired = _fired(before)
    missing = expected - fired
    assert not missing, (
        f"{path} before {CORRECTION_COMMIT[:8]} must trip {sorted(missing)}; "
        f"predicate only fired {sorted(fired)}. The predicate has been narrowed "
        f"to the point where it can no longer see the defect it was derived from."
    )


@pytest.mark.parametrize("path", sorted(RETIRED))
def test_predicate_clean_after_correction(path: str) -> None:
    """Negative control: the corrected blob must not trip the predicate."""
    after = _blob(CORRECTION_COMMIT, path)
    if after is None:
        pytest.skip(f"post-correction blob unavailable for {path}")
    assert not _fired(after), (
        f"{path} at {CORRECTION_COMMIT[:8]} still trips "
        f"{sorted(_fired(after))} after the correction landed."
    )


def test_judge_prompt_is_not_counted_as_target_construction() -> None:
    """A judge naming the scanner is the grader knowing its rubric, not the defect."""
    source = (
        '_JUDGE_PROMPT = "You are a self-consistency judge for BackBond Scanner '
        'rubric point P1.3."\n'
    )
    literals = python_prompt_literals(source)
    assert literals, "judge prompt literal should still be extracted"
    assert all(lit["channel"] == "judge" for lit in literals)
    assert not _fired(source)


def test_registry_bookkeeping_is_not_construction() -> None:
    """A probe_id key in a schema must not be mistaken for an announced prompt."""
    source = 'SCHEMA = {"fields": ["probe_id", "scenario"]}\n'
    assert python_prompt_literals(source) == []
