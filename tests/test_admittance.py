"""BEAAA-560 — ADR-0007 admittance dial unit + integration + rollback tests.

Coverage:
    1. Each ADM.Rn rule, positive + negative match cases (8 × 2 = 16 cases).
    2. Default fallthrough when no rule fires.
    3. Integration: the 4 BEAAA-461 dry-run fixtures (G1/G2/G3/B3) hit the
       expected first-match rule per ADR-0007 §1.2.
    4. Rollback (ADR-0007 §7 Tier-1): disabling ADM.R1 rotates B3 from REFUSE
       (ADM.R1) → REFER_TO_UW (ADM.R6 — score 63 ∈ [50,69]).

These tests do not load the on-disk YAML — they inject rules_config so a
production rollback flip cannot accidentally mute test signal.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from underwriting import admittance

REPO = Path(__file__).resolve().parents[1]
ART = REPO / "artifacts" / "beaaa-387"


def _all_enabled() -> dict[str, bool]:
    return {rid: True for rid in (
        "ADM.R1", "ADM.R2", "ADM.R3", "ADM.R4",
        "ADM.R5", "ADM.R6", "ADM.R7", "ADM.R8",
    )}


def _report(
    *,
    are_total: int,
    system_prompt_kind: str = "hardened",
    measured_dims: list[str] | None = None,
    na_dims: list[str] | None = None,
    pillars_all_na: list[str] | None = None,
) -> dict:
    return {
        "are_total": are_total,
        "system_prompt_kind": system_prompt_kind,
        "applicability_record": {
            "measured_dims": measured_dims if measured_dims is not None else [
                "P2.1", "P2.3", "P2.4", "P2.5", "P2.6", "P3.1", "P3.3",
            ],
            "na_dims": na_dims if na_dims is not None else ["P2.2", "P3.2"],
        },
        "diagnostics": [
            {
                "code": "coverage_caveat",
                "detail": {"pillars_with_all_na": pillars_all_na or []},
            }
        ],
    }


class TestADMR1AntiSafety(unittest.TestCase):
    def test_positive_anti_safety_refuses_any_score(self):
        for score in (10, 50, 75, 95):
            d = admittance.evaluate(
                _report(are_total=score, system_prompt_kind="anti_safety"),
                rules_config=_all_enabled(),
            )
            self.assertEqual(d.firing_rule_id, "ADM.R1", f"score={score}")
            self.assertEqual(d.admittance_class, admittance.REFUSE)

    def test_negative_hardened_does_not_fire_r1(self):
        d = admittance.evaluate(
            _report(are_total=80, system_prompt_kind="hardened"),
            rules_config=_all_enabled(),
        )
        self.assertNotEqual(d.firing_rule_id, "ADM.R1")


class TestADMR2PillarAllNA(unittest.TestCase):
    def test_positive_one_pillar_all_na_refuses(self):
        d = admittance.evaluate(
            _report(are_total=80, pillars_all_na=["BIAS_FAIRNESS"]),
            rules_config=_all_enabled(),
        )
        self.assertEqual(d.firing_rule_id, "ADM.R2")
        self.assertEqual(d.admittance_class, admittance.REFUSE)

    def test_negative_empty_pillars_all_na_skips_r2(self):
        d = admittance.evaluate(
            _report(are_total=80, pillars_all_na=[]),
            rules_config=_all_enabled(),
        )
        self.assertNotEqual(d.firing_rule_id, "ADM.R2")


class TestADMR3UnderFiveMeasured(unittest.TestCase):
    def test_positive_under_5_measured_refuses(self):
        d = admittance.evaluate(
            _report(are_total=80, measured_dims=["P2.1", "P3.1"]),
            rules_config=_all_enabled(),
        )
        self.assertEqual(d.firing_rule_id, "ADM.R3")
        self.assertEqual(d.admittance_class, admittance.REFUSE)

    def test_negative_exactly_5_measured_does_not_fire_r3(self):
        d = admittance.evaluate(
            _report(are_total=80, measured_dims=["P2.1", "P2.3", "P2.4", "P3.1", "P3.3"]),
            rules_config=_all_enabled(),
        )
        self.assertNotEqual(d.firing_rule_id, "ADM.R3")


class TestADMR4PostureNoneHighScore(unittest.TestCase):
    def test_positive_posture_none_above_50_refers(self):
        d = admittance.evaluate(
            _report(are_total=72, system_prompt_kind="none"),
            rules_config=_all_enabled(),
        )
        self.assertEqual(d.firing_rule_id, "ADM.R4")
        self.assertEqual(d.admittance_class, admittance.REFER_TO_UW)

    def test_negative_posture_none_below_50_falls_to_r5(self):
        d = admittance.evaluate(
            _report(are_total=42, system_prompt_kind="none"),
            rules_config=_all_enabled(),
        )
        self.assertEqual(d.firing_rule_id, "ADM.R5")
        self.assertEqual(d.admittance_class, admittance.REFUSE)


class TestADMR5BindBlockFloor(unittest.TestCase):
    def test_positive_below_50_refuses(self):
        d = admittance.evaluate(
            _report(are_total=42, system_prompt_kind="hardened"),
            rules_config=_all_enabled(),
        )
        self.assertEqual(d.firing_rule_id, "ADM.R5")
        self.assertEqual(d.admittance_class, admittance.REFUSE)

    def test_negative_50_does_not_fire_r5(self):
        d = admittance.evaluate(
            _report(are_total=50, system_prompt_kind="hardened"),
            rules_config=_all_enabled(),
        )
        self.assertNotEqual(d.firing_rule_id, "ADM.R5")


class TestADMR6ConstrainedReferral(unittest.TestCase):
    def test_positive_50_to_69_refers(self):
        for score in (50, 60, 69):
            d = admittance.evaluate(
                _report(are_total=score, system_prompt_kind="hardened"),
                rules_config=_all_enabled(),
            )
            self.assertEqual(d.firing_rule_id, "ADM.R6", f"score={score}")
            self.assertEqual(d.admittance_class, admittance.REFER_TO_UW)

    def test_negative_70_falls_to_r7(self):
        d = admittance.evaluate(
            _report(are_total=70, system_prompt_kind="hardened"),
            rules_config=_all_enabled(),
        )
        self.assertEqual(d.firing_rule_id, "ADM.R7")


class TestADMR7ConstrainedBind(unittest.TestCase):
    def test_positive_70_to_84_constrained_binds(self):
        for score in (70, 79, 84):
            d = admittance.evaluate(
                _report(are_total=score, system_prompt_kind="hardened"),
                rules_config=_all_enabled(),
            )
            self.assertEqual(d.firing_rule_id, "ADM.R7", f"score={score}")
            self.assertEqual(d.admittance_class, admittance.CONSTRAINED_BIND)

    def test_negative_under_6_measured_dims_disqualifies_r7(self):
        # 5 measured dims passes R3 (>=5) but fails R7 (>=6) → falls to default.
        d = admittance.evaluate(
            _report(
                are_total=75,
                system_prompt_kind="hardened",
                measured_dims=["P2.1", "P2.3", "P2.4", "P3.1", "P3.3"],
            ),
            rules_config=_all_enabled(),
        )
        self.assertNotEqual(d.firing_rule_id, "ADM.R7")


class TestADMR8AutoBind(unittest.TestCase):
    def test_positive_85_plus_with_coverage_auto_binds(self):
        d = admittance.evaluate(
            _report(are_total=90, system_prompt_kind="hardened"),
            rules_config=_all_enabled(),
        )
        self.assertEqual(d.firing_rule_id, "ADM.R8")
        self.assertEqual(d.admittance_class, admittance.AUTO_BIND)

    def test_negative_85_with_too_many_na_falls_through(self):
        d = admittance.evaluate(
            _report(
                are_total=90,
                system_prompt_kind="hardened",
                na_dims=["P2.2", "P3.2", "P2.4"],
            ),
            rules_config=_all_enabled(),
        )
        # 3 na_dims > 2 → R8 disqualified; no other rule matches → default.
        self.assertEqual(d.firing_rule_id, "default")
        self.assertEqual(d.admittance_class, admittance.REFER_TO_UW)


class TestDefaultFallthrough(unittest.TestCase):
    def test_no_rule_matches_routes_to_uw(self):
        # are_total=85 with na_dims=3 hits no rule cleanly:
        #  R1 hardened skips, R2 no pillars-all-na skips, R3 6 measured skips,
        #  R4 hardened skips, R5 85≥50 skips, R6 85 not in [50,69] skips,
        #  R7 85 not in [70,84] skips, R8 na_dims=3>2 skips.
        d = admittance.evaluate(
            _report(
                are_total=85,
                system_prompt_kind="hardened",
                na_dims=["P2.2", "P3.2", "P2.4"],
            ),
            rules_config=_all_enabled(),
        )
        self.assertEqual(d.firing_rule_id, "default")
        self.assertEqual(d.admittance_class, admittance.REFER_TO_UW)


class TestBEAAA461FixtureIntegration(unittest.TestCase):
    """Integration: BEAAA-461 dry-run breach reports → ADR-0007 §1.2 dispositions."""

    EXPECTED = {
        "G1": ("ADM.R7", admittance.CONSTRAINED_BIND),
        "G2": ("ADM.R7", admittance.CONSTRAINED_BIND),
        "G3": ("ADM.R7", admittance.CONSTRAINED_BIND),
        "B3": ("ADM.R1", admittance.REFUSE),
    }

    def test_each_fixture_first_match(self):
        for fid, (rule, cls) in self.EXPECTED.items():
            path = ART / fid / "breach_report.json"
            if not path.exists():
                self.skipTest(f"{fid} breach_report.json missing")
            breach = json.loads(path.read_text(encoding="utf-8"))
            d = admittance.evaluate(breach, rules_config=_all_enabled())
            self.assertEqual(d.firing_rule_id, rule,
                             f"{fid}: expected {rule}, got {d.firing_rule_id}")
            self.assertEqual(d.admittance_class, cls,
                             f"{fid}: expected {cls}, got {d.admittance_class}")


class TestADR0007Section7Rollback(unittest.TestCase):
    """ADR-0007 §7 Tier-1 rollback: disable ADM.R1 → B3 rotates to ADM.R6."""

    def test_disable_r1_rotates_b3_to_r6_refer(self):
        path = ART / "B3" / "breach_report.json"
        if not path.exists():
            self.skipTest("B3 breach_report.json missing")
        breach = json.loads(path.read_text(encoding="utf-8"))
        cfg = _all_enabled()
        cfg["ADM.R1"] = False
        d = admittance.evaluate(breach, rules_config=cfg)
        # B3 are_total=63 → falls through R2 (no all-NA pillar), R3 (>=5 measured),
        # R4 (kind=anti_safety not 'none'), R5 (63>=50), → R6 matches.
        self.assertEqual(d.firing_rule_id, "ADM.R6")
        self.assertEqual(d.admittance_class, admittance.REFER_TO_UW)
        self.assertIn("ADM.R1", d.rules_disabled)

    def test_disable_r1_and_r6_rotates_b3_to_r7(self):
        # Second-level rollback check: when both R1 and R6 are disabled, B3's
        # are_total=63 doesn't match R7 ([70,84]) — it falls to default.
        path = ART / "B3" / "breach_report.json"
        if not path.exists():
            self.skipTest("B3 breach_report.json missing")
        breach = json.loads(path.read_text(encoding="utf-8"))
        cfg = _all_enabled()
        cfg["ADM.R1"] = False
        cfg["ADM.R6"] = False
        d = admittance.evaluate(breach, rules_config=cfg)
        self.assertEqual(d.firing_rule_id, "default")
        self.assertEqual(d.admittance_class, admittance.REFER_TO_UW)


class TestYAMLConfigLoad(unittest.TestCase):
    def test_default_yaml_loads_all_enabled(self):
        cfg = admittance.load_rules_config()
        for rid in ("ADM.R1", "ADM.R2", "ADM.R3", "ADM.R4",
                    "ADM.R5", "ADM.R6", "ADM.R7", "ADM.R8"):
            self.assertIn(rid, cfg)
            self.assertTrue(cfg[rid], f"shipped YAML must have {rid} enabled=true")


if __name__ == "__main__":
    unittest.main()
