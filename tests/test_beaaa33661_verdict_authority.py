"""BEAAA-33661: the manifest/evidence verdict disagreement is not a case-sensitivity bug.

``tools/beaaa31092_content_slice.py`` casefolds every verdict it reads except one
(``:1069``, the ``evidenced`` tally), so a uniform-looking divergence could in
principle be a misparsed uppercase ``FAIL``.  The CEO asked for that hypothesis to
be proven or killed with a test before anyone blamed the data.  It is killed: on
the pinned corpus, zero rows agree after casefolding and disagree before it.

The census is pinned as an artefact so CI can assert it without the exported
evidence tree; when the tree is present the same numbers are recomputed live.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.beaaa33661_verdict_census import census  # noqa: E402

CENSUS = json.loads((ROOT / "artifacts/beaaa-33661/verdict-census.json").read_text(encoding="utf-8"))
EVIDENCE_ROOT = Path(
    "/var/lib/are-scanner/evidence/beaaa29082-rerun-20260909T123404Z/a935915a-ddc0-473f-ba46-ebd3593cc38b"
)


def test_casefolding_explains_none_of_the_disagreements():
    """The read-path hypothesis, killed. Not one row's agreement turns on case."""
    assert CENSUS["casefold_only_disagreements"] == 0


def test_every_manifest_evidence_ref_resolves_against_the_true_root():
    assert CENSUS["unreadable_evidence_refs"] == 0
    assert CENSUS["rows_compared"] == 187


def test_content_candidate_rows_are_correctly_bound():
    """Mis-binding is ruled out: all 160 rows name the file that computed their id."""
    assert CENSUS["misbound_rows"].get("content_candidate", 0) == 0


def test_the_evidence_file_never_contradicts_itself_but_the_manifest_does():
    """Verdict, score and the raw judge ensemble agree in all 160 files.

    The manifest contradicts its own ``numeric_result`` on 50 of the same rows, so
    the two sides are not symmetric witnesses: only one of them is self-consistent.
    """
    consistency = CENSUS["self_consistency"]
    assert consistency.get("content_candidate:evidence_file_disagrees_with_its_own_score", 0) == 0
    assert consistency["content_candidate:manifest_row_disagrees_with_its_own_numeric_result"] == 50


def test_the_disagreements_bucket_by_cause():
    assert CENSUS["cause_buckets"] == {
        "content_candidate:manifest_row_truncated_no_verdict": 26,
        "content_candidate:manifest_verdict_defaulted_on_unmeasured_case": 24,
        "content_candidate:result_triple_flip": 25,
        "promptfoo:result_triple_flip": 2,
    }


def test_the_manifest_declares_itself_unfit_to_bind():
    """No inference needed: the manifest says so in its own fields.

    ``build_ledger`` binds to it anyway, which is the defect underneath the 75.
    """
    declared = CENSUS["manifest_self_declared"]
    assert declared["incomplete"] is True
    assert declared["analysis_status"] == "analysis_incomplete"
    assert declared["bind_eligibility"] == "blocked_base_suite_incomplete"
    assert declared["acceptance_gate"]["passed"] is False
    assert declared["missing_required"] == 26


@pytest.mark.skipif(not EVIDENCE_ROOT.is_dir(), reason="exported evidence tree not mounted")
def test_the_pinned_census_still_reproduces_from_the_exported_evidence():
    live = census(
        EVIDENCE_ROOT,
        ROOT / "artifacts/beaaa-31092/rank2-run-manifest.json",
        ROOT / "artifacts/beaaa-31092/rank2-ledger.json",
    )
    # ``manifest_path`` echoes whatever path the caller passed; it is not a count.
    assert {k: v for k, v in live.items() if k != "manifest_path"} == {
        k: v for k, v in CENSUS.items() if k != "manifest_path"
    }


def test_only_nine_of_the_forty_three_scored_rows_are_in_the_disagreement_set():
    """The 75 is the alarming number; the 9 is the load-bearing one.

    The ledger's scored population is 43 rows.  34 of them agree with their own
    evidence file.  The other 9 are the only rows where ratifying R1 moves a
    verdict that is currently carrying weight - and the 66 held disagreements
    cannot move a score, because a held row scores nothing either way.
    """
    by_disposition = CENSUS["by_ledger_disposition"]
    assert by_disposition["scored:disagree"] == 9
    assert by_disposition["scored:agree"] == 34
    assert by_disposition["scored:agree"] + by_disposition["scored:disagree"] == 43
    assert by_disposition["held_reported_not_scored:disagree"] == 66


def test_no_scored_row_disagrees_for_a_non_measurement_reason():
    """The two cheap causes never reach a scored row.

    All 26 truncated and all 24 unmeasured rows are held.  Every scored
    disagreement is a genuine result-triple flip between two internally
    consistent sides, which is why the 9 need a ruling and not a repair.
    """
    assert CENSUS["scored_row_disagreement_causes"] == {
        "content_candidate:result_triple_flip": 9
    }


# --- BEAAA-33661 clause 3, CEO ratified 2026-09-18 -------------------------------
# ``build_ledger`` bound 187 verdicts out of a manifest that declares itself unfit
# and carried 9 of them into the scored population. It now refuses that read.

from tools.beaaa31092_content_slice import (  # noqa: E402
    _manifest_unfitness,
    build_ledger,
)

MANIFEST = ROOT / "artifacts/beaaa-31092/rank2-run-manifest.json"
INVENTORY = ROOT / "artifacts/beaaa-18720/unified-probe-inventory.jsonl"
LEDGER = json.loads((ROOT / "artifacts/beaaa-31092/rank2-ledger.json").read_text(encoding="utf-8"))


def test_the_pinned_manifest_is_refused_without_an_acknowledgement():
    with pytest.raises(ValueError) as raised:
        build_ledger(INVENTORY, MANIFEST)
    message = str(raised.value)
    assert "declares itself unfit to bind" in message
    for term in ("incomplete", "bind_eligibility:blocked_base_suite_incomplete",
                 "acceptance_gate_not_passed", "missing_required:26"):
        assert term in message


def test_the_guard_names_every_way_this_manifest_disowns_itself():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert _manifest_unfitness(manifest) == [
        "incomplete",
        "analysis_status:analysis_incomplete",
        "bind_eligibility:blocked_base_suite_incomplete",
        "acceptance_gate_not_passed",
        "missing_required:26",
    ]


def test_a_manifest_silent_on_fitness_is_not_treated_as_unfit():
    """Absence is not a declaration.

    Every synthetic manifest in the suite omits all four fields, and this control
    is scoped to a manifest that *says* it is unfit - not one that is unfit and
    silent. Stated here so the limit is a tested decision rather than an accident.
    """
    assert _manifest_unfitness({"evidence_rows": []}) == []


def test_a_fit_manifest_needs_no_acknowledgement():
    assert _manifest_unfitness({
        "incomplete": False,
        "analysis_status": "analysis_complete",
        "bind_eligibility": "eligible",
        "acceptance_gate": {"passed": True},
        "counts": {"missing_required": 0},
    }) == []


def test_acknowledging_binds_but_stamps_the_reasons_onto_the_ledger():
    """The escape hatch cannot be used quietly: the ledger carries the reasons."""
    ledger = build_ledger(INVENTORY, MANIFEST, unfit_manifest_ack="BEAAA-33661")
    assert ledger["manifest_fitness"]["acknowledged_by"] == "BEAAA-33661"
    assert "bind_eligibility:blocked_base_suite_incomplete" in ledger["manifest_fitness"]["unfit_reasons"]


def test_the_guard_re_scores_nothing(monkeypatch):
    """Clause 3 is a bug fix, not a re-score.

    The committed ``rank2-ledger.json`` cannot be rebuilt byte for byte on this
    host - the 43/144/110 split does not reproduce here, which is a standing
    finding from BEAAA-33656 and not something this guard introduced. So the claim
    is proved directly instead: build once with the guard bypassed and once with it
    acknowledged, from identical inputs, and assert the only difference in the
    whole ledger is the ``manifest_fitness`` stamp the guard adds. No row, no
    disposition and no count moves.
    """
    import tools.beaaa31092_content_slice as slice_module

    acknowledged = build_ledger(INVENTORY, MANIFEST, unfit_manifest_ack="BEAAA-33661")
    monkeypatch.setattr(
        slice_module, "_assert_manifest_fit_to_bind", lambda manifest, ack: {"bypassed": True}
    )
    bypassed = build_ledger(INVENTORY, MANIFEST)

    assert acknowledged.keys() == bypassed.keys()
    differing = [key for key in acknowledged if acknowledged[key] != bypassed[key]]
    assert differing == ["manifest_fitness"]
