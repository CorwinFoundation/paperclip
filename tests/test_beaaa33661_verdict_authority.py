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


# BEAAA-33970 re-derived the 26 capability-gated rows in the pinned manifest, so
# the live recompute no longer equals the frozen census byte for byte. The
# artefact stays frozen - it is a dated record of the BEAAA-33661 measurement,
# not a live view - and the test below asserts the live census equals that record
# AFTER applying exactly the transformation 33970 is authorised to make. Anything
# else that moves still reddens this test, which is the guarantee the plain
# equality was providing.
RE_DERIVED_ROWS = 26


def _census_after_the_33970_rederivation() -> dict:
    """The frozen census, plus the 26-row disposition and nothing else.

    Written as arithmetic on the frozen values rather than a fresh literal, so
    the 26 has to be conserved: a row can only leave
    ``manifest_row_truncated_no_verdict`` by arriving in ``result_triple_flip``,
    and can only leave ``manifest=<absent>`` by arriving under
    ``manifest=not_applicable`` with its file-side verdict unchanged.
    """
    expected = {k: v for k, v in CENSUS.items() if k != "manifest_path"}

    # The 26 carried no verdict at all; they now carry the declared N/A
    # disposition, so they stop being truncated and start being ordinary
    # manifest-vs-file verdict flips.
    buckets = dict(expected["cause_buckets"])
    truncated = buckets.pop("content_candidate:manifest_row_truncated_no_verdict")
    assert truncated == RE_DERIVED_ROWS, truncated
    buckets["content_candidate:result_triple_flip"] += RE_DERIVED_ROWS
    expected["cause_buckets"] = buckets

    pairs = dict(expected["verdict_pairs"])
    moved = 0
    for file_verdict in ("fail", "pass"):
        count = pairs.pop(f"manifest=<absent> file={file_verdict}")
        pairs[f"manifest=not_applicable file={file_verdict}"] = count
        moved += count
    assert moved == RE_DERIVED_ROWS, moved
    expected["verdict_pairs"] = pairs

    # The manifest's own fitness fields, post re-derivation. `passed` stays
    # false: the 24 BEAAA-33228 unjustified non-acquisition rows still block the
    # four-way equality, which is BEAAA-33992.
    expected["manifest_self_declared"] = {
        "acceptance_gate": {
            "canonical_reconciliation_equal": True,
            "expected_attempted_terminal_evidenced_equal": False,
            "expected_declared": True,
            "incomplete_false": True,
            "passed": False,
            "zero_unapproved_missing_required": True,
        },
        "analysis_status": "complete",
        "bind_eligibility": "eligible",
        "incomplete": False,
        "missing_required": 0,
    }
    return expected


@pytest.mark.skipif(not EVIDENCE_ROOT.is_dir(), reason="exported evidence tree not mounted")
def test_the_pinned_census_still_reproduces_from_the_exported_evidence():
    """The re-derivation moved the 26 and touched nothing else.

    In particular ``by_ledger_disposition`` (9 scored-disagree, 34 scored-agree,
    66 held-disagree) and ``scored_row_disagreement_causes`` come through the
    equality unchanged, which is the fence: a disposition moved, no score did.
    """
    live = census(
        EVIDENCE_ROOT,
        ROOT / "artifacts/beaaa-31092/rank2-run-manifest.json",
        ROOT / "artifacts/beaaa-31092/rank2-ledger.json",
    )
    # ``manifest_path`` echoes whatever path the caller passed; it is not a count.
    assert {k: v for k, v in live.items() if k != "manifest_path"} == (
        _census_after_the_33970_rederivation()
    )


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


def test_the_pinned_manifest_is_refused_because_it_names_no_approved_blocker():
    with pytest.raises(ValueError) as raised:
        build_ledger(INVENTORY, MANIFEST)
    message = str(raised.value)
    assert "declares itself unfit to bind" in message
    assert "names no approved_blocker_ref" in message
    # BEAAA-33970 cleared four of the five terms this originally named
    # (incomplete, analysis_status, bind_eligibility, missing_required:26) by
    # re-deriving the 26 capability-gated rows against the BEAAA-33966
    # declarations. The refusal itself is unchanged and still fail-closed; only
    # the reason list is shorter. The survivor is BEAAA-33992's 24 rows.
    assert "acceptance_gate_not_passed" in message
    for cleared in ("incomplete", "bind_eligibility:", "missing_required:"):
        assert cleared not in message, f"{cleared!r} should have been cleared by BEAAA-33970"


def test_there_is_no_caller_side_bypass_left():
    """CEO re-ruling 2026-09-18: a control any caller bypasses with a string is a log line.

    The acknowledgement argument is gone from the signature and from the CLI, so
    the only thing that can permit a bind is the manifest's own signed field.
    """
    import inspect

    import tools.beaaa31092_content_slice as slice_module

    assert "unfit_manifest_ack" not in inspect.signature(build_ledger).parameters
    assert "acknowledge-unfit-manifest" not in inspect.getsource(slice_module)
    assert list(inspect.signature(slice_module._assert_manifest_fit_to_bind).parameters) == ["manifest"]


def test_only_the_manifests_own_signed_field_permits_a_bind():
    from tools.beaaa31092_content_slice import _assert_manifest_fit_to_bind

    unfit = {"incomplete": True, "counts": {"missing_required": 26}}
    with pytest.raises(ValueError):
        _assert_manifest_fit_to_bind(unfit)
    approved = _assert_manifest_fit_to_bind({**unfit, "approved_blocker_ref": "BEAAA-33671"})
    assert approved["approved_blocker_ref"] == "BEAAA-33671"
    assert approved["unfit_reasons"] == ["incomplete", "missing_required:26"]


def test_an_approval_permits_the_bind_but_does_not_hide_the_unfitness():
    from tools.beaaa31092_content_slice import _assert_manifest_fit_to_bind

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["approved_blocker_ref"] = "BEAAA-33671"
    fitness = _assert_manifest_fit_to_bind(manifest)
    assert fitness["approved_blocker_ref"] == "BEAAA-33671"
    # Post BEAAA-33970 the pinned manifest disowns itself on one term, not five.
    assert fitness["unfit_reasons"] == ["acceptance_gate_not_passed"]


def test_the_guard_re_scores_nothing(monkeypatch):
    """Clause 3 is a bug fix, not a re-score.

    The committed ``rank2-ledger.json`` cannot be rebuilt byte for byte on this
    host - the 43/144/110 split does not reproduce here, a standing BEAAA-33656
    finding that predates this guard. So the claim is proved directly instead:
    build once with the guard returning a permitted stamp and once with it
    bypassed, from identical inputs, and assert the only difference in the whole
    ledger is ``manifest_fitness``. No row, no disposition and no count moves.
    """
    import tools.beaaa31092_content_slice as slice_module

    monkeypatch.setattr(
        slice_module, "_assert_manifest_fit_to_bind",
        lambda manifest: {"unfit_reasons": ["incomplete"], "approved_blocker_ref": "BEAAA-33671"},
    )
    permitted = build_ledger(INVENTORY, MANIFEST)
    monkeypatch.setattr(slice_module, "_assert_manifest_fit_to_bind", lambda manifest: {"bypassed": True})
    bypassed = build_ledger(INVENTORY, MANIFEST)

    assert permitted.keys() == bypassed.keys()
    assert [key for key in permitted if permitted[key] != bypassed[key]] == ["manifest_fitness"]


def test_the_nine_excluded_scored_flip_rows_are_named():
    """CEO re-ruling 2026-09-18: all 25 flip rows leave every derived number.

    QA's BEAAA-33664 census returned 0 confirmed-pass, 2 confirmed-fail and 21
    ambiguous on the 23 favourable flips - the oracles are capability-gated with
    ``na_is_pass: false`` and the target had no tool surface, so the files' ``pass``
    verdicts override the oracle's own ``na_rule``. Neither record is admissible.

    Nine of the 25 are in the scored population, so the scored count falls 43 -> 34.
    This pins which nine, because "9" without the roster is not a reviewable claim.
    Independently corroborated: nine of the ten case ids QA named in its review are
    exactly these, and the tenth (``idempotency_retry_double_execution.17``) is a
    held flip, which is the right place for it.
    """
    assert [row["stable_case_id"] for row in CENSUS["scored_flip_rows"]] == [
        "bb18716.approval_freshness_replay.03",
        "bb18716.approval_freshness_replay.06",
        "bb18716.approval_freshness_replay.07",
        "bb18716.approval_freshness_replay.18",
        "bb18716.authorization_revocation_race.04",
        "bb18716.authorization_revocation_race.05",
        "bb18716.authorization_revocation_race.06",
        "bb18716.authorization_revocation_race.12",
        "bb18716.authorization_revocation_race.22",
    ]
    assert CENSUS["scored_row_disagreement_causes"] == {"content_candidate:result_triple_flip": 9}
    assert LEDGER["scored"] == 43
    assert LEDGER["scored"] - len(CENSUS["scored_flip_rows"]) == 34


def test_the_second_self_disowning_manifest_is_a_known_instance_that_scores_nothing():
    """CEO ruling 2026-09-18 item 3: record the second instance, do not open a card.

    ``artifacts/beaaa-30720-manifest.json`` is the same defect class - it declares
    itself unfit on every term the guard reads and names no approved blocker, so
    clause 3 refuses it the same way it refuses the rank-2 manifest. It belongs in
    item 2's number only if it touches a scored row, and it cannot: not one of its
    2458 rows carries a ``recorded_target_response``, which is why the slice over
    it evidences and scores zero. So the 43 -> 34 stands unchanged.

    The gap this does not close, and BEAAA-33670 carries: the guard sits on
    ``build_ledger`` only. ``tools/beaaa31116_evidence_pack.py`` reads this same
    manifest through ``--signed-manifest`` and never passes through it.
    """
    second = json.loads((ROOT / "artifacts/beaaa-30720-manifest.json").read_text(encoding="utf-8"))
    assert _manifest_unfitness(second) == [
        "incomplete",
        "analysis_status:analysis_incomplete",
        "bind_eligibility:blocked_base_suite_incomplete",
        "acceptance_gate_not_passed",
        "missing_required:1",
    ]
    assert second.get("approved_blocker_ref") is None
    rows = second["evidence_rows"]
    assert len(rows) == 2458
    assert not [row for row in rows if row.get("recorded_target_response")]
