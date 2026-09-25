"""The Stage A abuse-case harness, run as a test so it actually guards something.

BEAAA-34396. ``tools/stage_a_abuse_harness.py`` carries 24 probes over the seams
this issue was opened to cover - self-approval, mandate replay, revocation,
duplicate events, concurrent reservations, missing evidence, tenant isolation,
receipt redaction, sharing opt-in, false coverage activation. Every one of them
ran only when a person ran it by hand: no workflow in ``.github/workflows``
referenced the harness or any ``qa/seam_probes_*`` file, so a change that broke a
seam left CI green and the probes silent. Probes nobody runs are documentation,
not a guard.

This wrapper rides the existing required ``python pytest`` check rather than
adding a gate of its own, and the whole harness costs ~2.5s.

The allowlist below is a tripwire in both directions. A newly failing probe fails
this test, which is the point. A probe in the allowlist that starts holding also
fails it, so a fixed seam cannot sit behind a stale exemption - when SA10 is
closed, this list shrinks in the same change.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Probes known to fail at master, each with the row that owns the gap. SA10 is
# the in-process capability boundary: within one Python process the execution
# side can reach the signing helper, and no amount of structure inside the demo
# changes that - it needs the approval authority behind a process boundary.
KNOWN_FAILING = {"SA10": "BEAAA-35021 - in-process capability boundary, parked by design"}


def _load_harness():
    path = REPO_ROOT / "tools" / "stage_a_abuse_harness.py"
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location("stage_a_abuse_harness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness_results():
    module = _load_harness()
    for probe in module.PROBES:
        probe()
    assert module.RESULTS, "the harness registered no results"
    return list(module.RESULTS)


def _probe_id(case: str) -> str:
    return case.split()[0]


def test_every_probe_reports_a_result(harness_results):
    """A probe that never reports is a silent hole, so count them explicitly."""
    module = _load_harness()
    assert len(harness_results) == len(module.PROBES)


def test_no_unexpected_probe_fails(harness_results):
    """Any seam failure outside the allowlist is a regression."""
    failing = {_probe_id(r["case"]) for r in harness_results if not r["holds"]}
    unexpected = sorted(failing - set(KNOWN_FAILING))
    detail = "\n".join(
        f"  [{_probe_id(r['case'])}] {r['case']}\n"
        f"      requirement: {r['requirement']}\n"
        f"      observed:    {r['observed']}"
        for r in harness_results
        if not r["holds"] and _probe_id(r["case"]) in unexpected
    )
    assert not unexpected, f"Stage A abuse probes newly failing: {unexpected}\n{detail}"


def test_allowlisted_failures_have_not_been_fixed(harness_results):
    """A fixed seam must not keep its exemption, or the allowlist hides coverage."""
    holding = {_probe_id(r["case"]) for r in harness_results if r["holds"]}
    fixed = sorted(set(KNOWN_FAILING) & holding)
    assert not fixed, (
        f"these probes now hold and must come out of KNOWN_FAILING: "
        + ", ".join(f"{p} ({KNOWN_FAILING[p]})" for p in fixed)
    )
