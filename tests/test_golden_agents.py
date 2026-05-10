"""Golden-Agent Set v1.0 — Layer-1 deterministic regression tests.

Routed under BEAAA-163 Option C/B+C combined (golden-set v1 draft
attachment 1934b351, comment 0d9e7859).

For each fixture under tests/fixtures/golden_agents/{id}/:
  - agent.json           — metadata (always present)
  - eval_outputs.json    — locked per-dim raw scores from a tagged
                           measurement (only on live fixtures)
  - expected_score.json  — what score() must produce given those
                           eval_outputs (only on live fixtures)

Layer 1 (this file): deterministic. Fixture in -> rubric/scoring runs ->
byte-identical match against expected_score. Catches rubric/scoring-
engine regressions on every CI run; ~$0 to run.

Layer 2 (separate, quarterly, NOT this file): rerun probe packs against
the live agent N=5; assert per-dim raw +/-0.05 and ARE total +/-1.

Placeholder fixtures (status == "pending_baseline_scan") are skipped
with their reason; the skip clears automatically when eval_outputs.json
is populated by a baseline scan.
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from rubric.scoring import Rubric, score

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
FIXTURES = REPO / "tests" / "fixtures" / "golden_agents"

# Fixed scored_at for byte-identical reproduction. Must match what was
# used to build expected_score.json in the fixture.
SCORED_AT = datetime(2026, 5, 9, 0, 0, 0, tzinfo=timezone.utc)
SNAPSHOT_HASH = "sha256:" + "a" * 64


def _agent_ids() -> list[str]:
    return sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())


def _load_agent(fid: str) -> dict:
    return json.loads((FIXTURES / fid / "agent.json").read_text())


def _is_live(fid: str) -> bool:
    return _load_agent(fid).get("status") == "live"


class GoldenAgentInventoryTests(unittest.TestCase):
    """Sanity checks on the set itself: composition, slot coverage, IDs."""

    def setUp(self):
        self.ids = _agent_ids()

    def test_set_has_nine_agents(self):
        self.assertEqual(len(self.ids), 9, f"golden set must have 9 agents, got {self.ids}")

    def test_set_has_three_three_three_split(self):
        slots = [_load_agent(fid)["slot"] for fid in self.ids]
        self.assertEqual(slots.count("known_good"), 3)
        self.assertEqual(slots.count("known_bad"), 3)
        self.assertEqual(slots.count("graded_middle"), 3)

    def test_every_agent_has_id_and_name(self):
        for fid in self.ids:
            a = _load_agent(fid)
            self.assertEqual(a["id"], fid, f"{fid}: agent.json id mismatch")
            self.assertTrue(a.get("name"), f"{fid}: missing name")


class GoldenAgentScoringRegressionTests(unittest.TestCase):
    """Layer-1 deterministic regression: rubric/scoring must reproduce
    the locked expected_score for every live fixture, byte-identical."""

    @classmethod
    def setUpClass(cls):
        cls.rubric = Rubric.load(RUBRIC_PATH)

    def _rerun_score(self, fid: str) -> dict:
        eo = json.loads((FIXTURES / fid / "eval_outputs.json").read_text())
        return score(
            self.rubric, eo,
            snapshot_hash=SNAPSHOT_HASH,
            forensic_bundle_uri=f"https://golden.local/{fid}",
            scored_at=SCORED_AT,
        )

    def test_live_fixtures_reproduce_expected_score(self):
        live_ids = [fid for fid in _agent_ids() if _is_live(fid)]
        self.assertGreater(len(live_ids), 0, "no live fixtures — golden set must have at least one")
        for fid in live_ids:
            with self.subTest(agent=fid):
                expected = json.loads((FIXTURES / fid / "expected_score.json").read_text())
                actual = self._rerun_score(fid)
                # Byte-identical match across every top-level field.
                self.assertEqual(
                    actual, expected,
                    f"{fid}: scoring regression — actual diverges from locked expected_score",
                )

    def test_live_fixture_bmi_matches_agent_metadata(self):
        for fid in _agent_ids():
            agent = _load_agent(fid)
            if agent.get("status") != "live":
                continue
            with self.subTest(agent=fid):
                actual = self._rerun_score(fid)
                self.assertEqual(
                    actual["bmi"], agent["expected_bmi"],
                    f"{fid}: scoring engine produced bmi={actual['bmi']} but "
                    f"agent.json declares expected_bmi={agent['expected_bmi']}",
                )
                self.assertEqual(
                    actual["band"]["display"], agent["expected_band"],
                    f"{fid}: band mismatch vs agent metadata",
                )


class GoldenAgentPlaceholderHygieneTests(unittest.TestCase):
    """Placeholders must be cleanly tagged so future scans can promote
    them without ambiguity. No silent half-states."""

    def test_placeholders_have_no_eval_outputs(self):
        for fid in _agent_ids():
            agent = _load_agent(fid)
            if agent.get("status") == "live":
                continue
            with self.subTest(agent=fid):
                self.assertFalse(
                    (FIXTURES / fid / "eval_outputs.json").exists(),
                    f"{fid}: placeholder must not have eval_outputs.json yet",
                )
                self.assertFalse(
                    (FIXTURES / fid / "expected_score.json").exists(),
                    f"{fid}: placeholder must not have expected_score.json yet",
                )

    def test_placeholders_carry_a_reason(self):
        for fid in _agent_ids():
            agent = _load_agent(fid)
            if agent.get("status") == "live":
                continue
            with self.subTest(agent=fid):
                # At least one of: pending_baseline_scan tag, baseline_scan_due,
                # or status_note explaining why it's not live.
                has_reason = (
                    agent.get("status") == "pending_baseline_scan"
                    or agent.get("baseline_scan_due")
                    or agent.get("status_note")
                )
                self.assertTrue(
                    has_reason,
                    f"{fid}: placeholder must carry a baseline_scan_due or status_note",
                )


if __name__ == "__main__":
    unittest.main()
