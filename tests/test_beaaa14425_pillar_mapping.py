"""BEAAA-14425 [F4] — reconcile live-probe pillar/dim mapping to rubric.

Reproduce-first coverage for two defects (parent BEAAA-14405 Code Review F4):

* **Defect A** (``scanners/wraps/inspect_section1_v0.py``) — the four
  ``ProbeSpec.dim_id`` labels were inverted relative to ``rubric.yaml``: each
  probe's ``scoring_rule`` measured a peril that landed in the WRONG pillar
  under ``_pillar_of``. The corrected labels attribute every live probe to the
  pillar whose peril its ``scoring_rule`` actually measures, and the
  home-less instruction-following probe is parked out of the live set (it must
  NOT be attributed to GOVERNANCE).
* **Defect B** (``scanners/live_probe_orchestrator.py``) — ``_pillar_of`` only
  accepted ``P<n>`` prefixes, so fully-qualified named dims
  (``ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL``, ``*.PII.*``) split to a head
  like ``ROBUSTNESS`` that is not a prefix → returned ``""`` → fell out of
  ``pillar_spread``, blinding the variance-band guard to those dims. The fix
  resolves named dims to their pillar prefix via a rubric-derived map.

Pre-fix, the Defect-B tests below FAIL (``_pillar_of`` → ``""`` for every
named dim, and ``pillar_spread`` never reflects a named-dim spread); the
Defect-A tests FAIL on the inverted labels. Post-fix, all pass.
"""
from __future__ import annotations

import re
from pathlib import Path

from rubric.scoring import Rubric
from scanners.live_probe_orchestrator import _aggregate_band, _pillar_of
from scanners.wraps import inspect_section1_v0

REPO_ROOT = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO_ROOT / "rubric" / "rubric.yaml"


# ---------------------------------------------------------------------------
# Defect B — _pillar_of resolves fully-qualified named dims to their pillar.
# ---------------------------------------------------------------------------


class TestDefectBPillarOfNamedDims:
    def test_prefix_dims_still_resolve(self) -> None:
        for did, prefix in (("P1.1", "P1"), ("P2.3", "P2"), ("P3.2", "P3"), ("P4.6", "P4")):
            assert _pillar_of(did) == prefix

    def test_named_robustness_dims_resolve_to_p3(self) -> None:
        # Pre-fix these returned "" (head "ROBUSTNESS" not in _PILLAR_PREFIXES).
        for did in (
            "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL",
            "ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER",
            "ROBUSTNESS.PII.SCAN_TIME_DETECTION",
        ):
            assert _pillar_of(did) == "P3", did

    def test_named_governance_dim_resolves_to_p4(self) -> None:
        assert _pillar_of("GOVERNANCE.PII.HANDLING_ATTESTATION") == "P4"

    def test_unknown_head_and_bare_dim_return_empty(self) -> None:
        assert _pillar_of("NOT_A_PILLAR.SUB.LEAF") == ""
        assert _pillar_of("no_separator") == ""

    def test_map_is_rubric_derived_not_hardcoded(self) -> None:
        """The pillar-name→prefix map is derived from rubric.yaml, matching
        each pillar to the prefix of its own ``P<n>.<m>`` dims."""
        from scanners.live_probe_orchestrator import _pillar_prefix_by_name

        mapping = dict(_pillar_prefix_by_name())
        rubric = Rubric.load(RUBRIC_PATH)
        prefix_re = re.compile(r"^(P\d+)\.")
        expected: dict[str, str] = {}
        for pillar_name, pdata in rubric.pillars.items():
            for did in pdata["dim_ids"]:
                m = prefix_re.match(did)
                if m:
                    expected[pillar_name] = m.group(1)
                    break
        assert mapping == expected
        # Sanity anchor against the rubric's canonical pillar order.
        assert mapping == {
            "OPS": "P1",
            "BIAS_FAIRNESS": "P2",
            "ROBUSTNESS": "P3",
            "GOVERNANCE": "P4",
        }

    def test_named_dim_spread_reflected_in_pillar_spread(self) -> None:
        """AC#1: a named dim with replicate spread > 0 is reflected in
        ``pillar_spread`` under its correct pillar. Pre-fix it was dropped
        (``_pillar_of`` → "" → not in pillar_spread) leaving P3 spread 0.0."""
        raws_by_dim = {
            "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL": [1.0, 4.0],  # spread 3.0
            "GOVERNANCE.PII.HANDLING_ATTESTATION": [2.0, 3.0],       # spread 1.0
            "P1.1": [2.0, 2.0],                                       # spread 0.0
        }
        _, spread_per_dim, pillar_spread, _ = _aggregate_band(raws_by_dim)
        assert spread_per_dim["ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL"] == 3.0
        assert pillar_spread["P3"] == 3.0, "named ROBUSTNESS dim must land in P3"
        assert pillar_spread["P4"] == 1.0, "named GOVERNANCE dim must land in P4"
        assert pillar_spread["P1"] == 0.0


# ---------------------------------------------------------------------------
# Defect A — probe dim_id labels resolve to the pillar their peril measures.
# ---------------------------------------------------------------------------


class TestDefectAProbeLabels:
    def test_corrected_labels_and_order(self) -> None:
        assert [s.dim_id for s in inspect_section1_v0.probe_specs()] == [
            "P1.4",
            "ROBUSTNESS.PII.SCAN_TIME_DETECTION",
            "P1.1",
        ]

    def test_each_probe_dim_resolves_to_correct_pillar(self) -> None:
        """AC#2: each probe's dim_id resolves, through rubric-derived
        ``_pillar_of``, to the pillar whose peril its scoring_rule measures."""
        by_rule = {
            s.scoring_rule.__name__: s.dim_id for s in inspect_section1_v0.probe_specs()
        }
        # peril measured → expected pillar prefix
        expected_pillar = {
            "_truthfulness_scoring": "P1",       # hallucination → OPS
            "_safety_refusal_scoring": "P1",     # out-of-scope refusal → OPS
            "_privacy_redaction_scoring": "P3",  # PII non-disclosure → ROBUSTNESS
        }
        assert set(by_rule) == set(expected_pillar), by_rule
        for rule_name, dim_id in by_rule.items():
            assert _pillar_of(dim_id) == expected_pillar[rule_name], rule_name

    def test_all_live_dims_are_active_rubric_dims(self) -> None:
        active = set(Rubric.load(RUBRIC_PATH).active_dim_ids)
        for spec in inspect_section1_v0.probe_specs():
            assert spec.dim_id in active, f"{spec.dim_id} not an active rubric dim"

    def test_no_probe_attributed_to_pillar_it_does_not_measure(self) -> None:
        """The honest known-unknown: the live minimal set covers only OPS +
        ROBUSTNESS. BIAS_FAIRNESS (P2) and GOVERNANCE (P4) must receive NO
        live-walker probe — not a fake-covered pillar."""
        covered = {_pillar_of(s.dim_id) for s in inspect_section1_v0.probe_specs()}
        assert covered == {"P1", "P3"}
        assert "P2" not in covered, "BIAS_FAIRNESS must have no live probe"
        assert "P4" not in covered, "GOVERNANCE must have no live probe"

    def test_instruction_following_probe_parked_out_of_live_set(self) -> None:
        """The home-less instruction-following probe must NOT be in the live
        set and must NOT be attributed to GOVERNANCE (or any real pillar)."""
        live_rules = {s.scoring_rule.__name__ for s in inspect_section1_v0.probe_specs()}
        assert "_instruction_following_scoring" not in live_rules
        parked = inspect_section1_v0._PARKED_SPECS
        parked_rules = {s.scoring_rule.__name__ for s in parked}
        assert "_instruction_following_scoring" in parked_rules
        for spec in parked:
            assert _pillar_of(spec.dim_id) == "", (
                "parked probe must not resolve to any pillar"
            )
