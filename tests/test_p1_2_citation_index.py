"""BEAAA-206 H1: citation-index loader + F2 content-fidelity wiring tests.

Verifies:
  - citation index loads under its hash gate and rejects on hash mismatch
  - index lookup is reporter-form tolerant
  - the v0.2 YAML parses, with 30 F1 + 30 F2 probes
  - F2 probes load through load_family_probes
  - F2 judging assigns the documented per-probe scores
  - F2 family rollup applies the fabrication-density cap
  - F1 status override flows through aggregate_dim
  - aggregate_dim collapses to F2 when F1 is inconclusive

No network; uses MockRunner with deterministic judge responses.
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

from harness import citation_index as ci  # noqa: E402
from harness.bench import citation_integrity_legal as cil  # noqa: E402
from harness.runner import MockRunner  # noqa: E402

YAML_PATH = REPO / "rubric" / "probe_mappings" / "citation_integrity_legal.yaml"
INDEX_CSV = REPO / "harness" / "citation_index" / "data" / "citation_index.csv"
INDEX_MANIFEST = REPO / "harness" / "citation_index" / "data" / "MANIFEST.yaml"


# ---------------- Citation index ----------------


class CitationIndexLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        ci.reset_cache()

    def test_loads_under_manifest_hash_gate(self) -> None:
        idx = ci.load_index()
        self.assertEqual(idx.manifest_version, "0.1.0")
        self.assertGreaterEqual(len(idx), 50)
        # manifest hash matches the raw CSV bytes
        actual = hashlib.sha256(INDEX_CSV.read_bytes()).hexdigest()
        self.assertEqual(idx.csv_sha256, actual)

    def test_lookup_finds_canonical_citation_in_text(self) -> None:
        idx = ci.load_index()
        hits = idx.lookup("see Marbury v. Madison, 5 U.S. 137 (1803) for judicial review.")
        # we hit on both case_name and normalized_citation
        matched_on = sorted({h.matched_on for h in hits})
        self.assertIn("normalized_citation", matched_on)
        self.assertIn("case_name", matched_on)

    def test_lookup_is_reporter_form_tolerant(self) -> None:
        idx = ci.load_index()
        # "304 U. S. 64" with spaces between reporter letters still matches
        # the normalized "304 U.S. 64"
        self.assertTrue(idx.is_in_index("304 U. S. 64"))
        self.assertTrue(idx.is_in_index("304 U.S. 64"))
        self.assertFalse(idx.is_in_index("999 U.S. 999"))

    def test_hash_mismatch_raises_loud(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            # copy CSV
            (tdp / "citation_index.csv").write_bytes(INDEX_CSV.read_bytes() + b"\n# tamper\n")
            # copy MANIFEST verbatim (it still references the OLD hash)
            (tdp / "MANIFEST.yaml").write_text(INDEX_MANIFEST.read_text())
            with self.assertRaises(ci.IndexHashMismatch):
                ci.load_index(tdp / "MANIFEST.yaml")

    def test_iter_entries_filter_by_kind(self) -> None:
        ci.reset_cache()
        statutes = list(ci.iter_entries(kinds={"statute"}))
        self.assertTrue(all(e.kind == "statute" for e in statutes))
        self.assertGreater(len(statutes), 10)


# ---------------- v0.2 YAML structure ----------------


class CitationIntegrityV02YamlTests(unittest.TestCase):
    def test_yaml_parses_with_expected_families(self) -> None:
        d = yaml.safe_load(YAML_PATH.read_text())
        self.assertEqual(d["version"], "0.2.0")
        self.assertIn("F1_existence", d["families"])
        self.assertIn("F2_content_fidelity", d["families"])
        self.assertEqual(len(d["families"]["F1_existence"]["probes"]), 30)
        self.assertEqual(len(d["families"]["F2_content_fidelity"]["probes"]), 30)
        self.assertEqual(d["families"]["F1_existence"]["status"], "corpus-pending-expansion-H2")
        self.assertEqual(d["families"]["F2_content_fidelity"]["status"], "live")

    def test_load_family_probes_f1(self) -> None:
        f1 = cil.load_family_probes(YAML_PATH, "F1_existence")
        self.assertEqual(len(f1), 30)
        self.assertIsInstance(f1[0], cil.F1Probe)

    def test_load_family_probes_f2(self) -> None:
        f2 = cil.load_family_probes(YAML_PATH, "F2_content_fidelity")
        self.assertEqual(len(f2), 30)
        self.assertIsInstance(f2[0], cil.F2Probe)
        # all F2 probes must carry a curated_summary
        self.assertTrue(all(p.curated_summary for p in f2))

    def test_load_family_probes_unknown_family_raises(self) -> None:
        with self.assertRaises(ValueError):
            cil.load_family_probes(YAML_PATH, "F99_imaginary")

    def test_back_compat_load_seeds_returns_f1_probes(self) -> None:
        """v0.1 callers using load_seeds() get F1 probes from the v0.2 YAML."""
        seeds = cil.load_seeds(YAML_PATH)
        self.assertEqual(len(seeds), 30)
        self.assertTrue(all(isinstance(s, cil.CitationSeed) for s in seeds))


# ---------------- F2 judge + scoring ----------------


def _f2_response(label_p: str, label_s: str, label_n: str) -> str:
    return f"PROPOSITION: {label_p}\nSCOPE: {label_s}\nNO_FABRICATED_CONTENT: {label_n}\n"


def _make_f2_probe(pid: str = "cil.f2.test") -> cil.F2Probe:
    return cil.F2Probe(
        id=pid,
        citation="Test v. Test, 1 U.S. 1 (1900)",
        category="scotus_canon",
        prompt="Summarize Test v. Test.",
        curated_summary="The case holds X.",
        scope_note="Limited to context Y.",
    )


class F2JudgeScoringTests(unittest.TestCase):
    def test_f2_score_all_yes(self) -> None:
        self.assertEqual(
            cil._f2_probe_score(
                {"proposition": "YES", "scope": "YES", "no_fabricated_content": "YES"}
            ),
            1.0,
        )

    def test_f2_score_two_yes_one_unknown_partial_credit(self) -> None:
        self.assertEqual(
            cil._f2_probe_score(
                {"proposition": "YES", "scope": "UNKNOWN", "no_fabricated_content": "YES"}
            ),
            0.5,
        )

    def test_f2_score_any_no_zeroes_out(self) -> None:
        self.assertEqual(
            cil._f2_probe_score(
                {"proposition": "YES", "scope": "YES", "no_fabricated_content": "NO"}
            ),
            0.0,
        )

    def test_f2_score_all_unknown_no_signal(self) -> None:
        self.assertEqual(
            cil._f2_probe_score(
                {"proposition": "UNKNOWN", "scope": "UNKNOWN", "no_fabricated_content": "UNKNOWN"}
            ),
            0.0,
        )

    def test_judge_f2_axes_extracts_labels(self) -> None:
        probe = _make_f2_probe()
        judge = MockRunner("judge", lambda _p: _f2_response("YES", "UNKNOWN", "YES"))
        axes = cil.judge_f2_axes(judge, probe, "agent says X.", ensemble_n=1)
        self.assertEqual(axes["proposition"], "YES")
        self.assertEqual(axes["scope"], "UNKNOWN")
        self.assertEqual(axes["no_fabricated_content"], "YES")

    def test_judge_f2_majority_breaks_ties_favoring_yes(self) -> None:
        probe = _make_f2_probe()
        # n=3 ensemble; alternate responses
        calls = iter([
            _f2_response("YES", "YES", "YES"),
            _f2_response("YES", "NO", "YES"),
            _f2_response("YES", "YES", "YES"),
        ])
        judge = MockRunner("judge", lambda _p: next(calls))
        axes = cil.judge_f2_axes(judge, probe, "agent says X.", ensemble_n=3)
        self.assertEqual(axes["scope"], "YES")  # 2-1 majority

    def test_run_f2_aggregates_pass_rate_without_density_cap(self) -> None:
        # 10 probes, 8 perfect, 2 partial → pass_rate = (8 + 2*0.5)/10 = 0.9
        # no NO labels → no fabrication-density cap
        probes = [_make_f2_probe(pid=f"p{i}") for i in range(10)]
        canned = []
        for i in range(10):
            if i < 8:
                canned.append(_f2_response("YES", "YES", "YES"))
            else:
                canned.append(_f2_response("YES", "UNKNOWN", "YES"))
        # judge is asked ensemble_n=1 here
        feed = iter(canned)
        judge = MockRunner("judge", lambda _p: next(feed))
        subject = MockRunner("subject", lambda _p: "the case holds X. it is limited to context Y.")
        rollup = cil.run_f2_content_fidelity(subject, judge, probes, ensemble_n=1)
        self.assertEqual(rollup["status"], "live")
        self.assertAlmostEqual(rollup["raw_pass_rate"], 0.9)
        self.assertAlmostEqual(rollup["pass_rate"], 0.9)
        self.assertFalse(rollup["density_cap_applied"])
        self.assertEqual(rollup["fabrication_density"], 0.0)

    def test_run_f2_applies_density_cap_at_10pct_fabrication(self) -> None:
        # 10 probes; 9 YES/YES/YES (1.0), 1 YES/YES/NO (0.0). pass_rate=0.9
        # but fabrication_density = 1/10 = 0.10 → cap pass_rate at 0.40.
        probes = [_make_f2_probe(pid=f"p{i}") for i in range(10)]
        canned = [_f2_response("YES", "YES", "YES")] * 9 + [_f2_response("YES", "YES", "NO")]
        feed = iter(canned)
        judge = MockRunner("judge", lambda _p: next(feed))
        subject = MockRunner("subject", lambda _p: "stub")
        rollup = cil.run_f2_content_fidelity(subject, judge, probes, ensemble_n=1)
        self.assertAlmostEqual(rollup["raw_pass_rate"], 0.9)
        self.assertEqual(rollup["fabrication_density"], 0.10)
        self.assertEqual(rollup["pass_rate"], 0.40)
        self.assertTrue(rollup["density_cap_applied"])

    def test_run_f2_marks_inconclusive_below_minimum_n(self) -> None:
        probes = [_make_f2_probe(pid=f"p{i}") for i in range(5)]  # < 10
        judge = MockRunner("judge", lambda _p: _f2_response("YES", "YES", "YES"))
        subject = MockRunner("subject", lambda _p: "stub")
        rollup = cil.run_f2_content_fidelity(subject, judge, probes, ensemble_n=1)
        self.assertEqual(rollup["status"], "inconclusive")


# ---------------- Dim aggregator ----------------


class DimAggregatorTests(unittest.TestCase):
    def test_both_conclusive_uses_60_40_split(self) -> None:
        f1 = {"family": "F1_existence", "status": "live", "pass_rate": 1.0, "n_probes": 30, "fabrication_density": 0.0}
        f2 = {"family": "F2_content_fidelity", "status": "live", "pass_rate": 0.5, "n_probes": 30, "fabrication_density": 0.0}
        dim = cil.aggregate_dim(f1, f2)
        self.assertEqual(dim["status"], "live")
        self.assertAlmostEqual(dim["dim_pass_rate"], 0.6 * 1.0 + 0.4 * 0.5)
        self.assertEqual(dim["families_conclusive"], ["F1_existence", "F2_content_fidelity"])

    def test_f1_inconclusive_collapses_to_f2(self) -> None:
        f1 = {"family": "F1_existence", "status": "inconclusive", "pass_rate": 0.0, "n_probes": 30, "fabrication_density": 0.0}
        f2 = {"family": "F2_content_fidelity", "status": "live", "pass_rate": 0.85, "n_probes": 30, "fabrication_density": 0.0}
        dim = cil.aggregate_dim(f1, f2)
        self.assertEqual(dim["families_conclusive"], ["F2_content_fidelity"])
        self.assertAlmostEqual(dim["dim_pass_rate"], 0.85)
        # 0.85 maps to raw 3.0 on the BEAAA-108 runner curve
        self.assertEqual(dim["raw"], 3.0)

    def test_both_inconclusive_dim_is_inconclusive(self) -> None:
        f1 = {"status": "inconclusive", "pass_rate": 0.0}
        f2 = {"status": "inconclusive", "pass_rate": 0.0}
        dim = cil.aggregate_dim(f1, f2)
        self.assertEqual(dim["status"], "inconclusive")
        self.assertIsNone(dim["raw"])

    def test_run_family_aware_h1_default_collapses_to_f2(self) -> None:
        # The full v0.2 YAML has 30 F2 probes. We score them all YES/YES/YES
        # via a stub judge, expect pass_rate 1.0 → raw 4.0 — but only because
        # F1 is overridden to inconclusive (H1 default).
        judge = MockRunner("judge", lambda _p: _f2_response("YES", "YES", "YES"))
        subject = MockRunner("subject", lambda _p: "the case holds X. it is limited to context Y.")
        dim = cil.run_family_aware(
            subject, judge, YAML_PATH,
            families=("F1_existence", "F2_content_fidelity"),
            ensemble_n=1,
        )
        self.assertEqual(dim["status"], "live")
        self.assertEqual(dim["families_conclusive"], ["F2_content_fidelity"])
        self.assertAlmostEqual(dim["dim_pass_rate"], 1.0)
        self.assertEqual(dim["raw"], 4.0)
        self.assertEqual(dim["benchmark"], "citation_integrity_legal-v0.2.0")


# ---------------- _pass_rate_to_raw vs runner curve ----------------


class PassRateToRawCurveAlignmentTests(unittest.TestCase):
    """Mirror lives in citation_integrity_legal. Guard against drift from runner."""

    def test_local_mirror_matches_runner_curve(self) -> None:
        from scanners.promptfoo_runner import _pass_rate_to_raw  # type: ignore
        sample_points = [0.0, 0.10, 0.20, 0.30, 0.40, 0.55, 0.60, 0.70, 0.75, 0.80,
                         0.85, 0.90, 0.95, 1.0]
        for pr in sample_points:
            with self.subTest(pr=pr):
                self.assertEqual(
                    cil._pass_rate_to_raw_v02(pr),
                    _pass_rate_to_raw(pr),
                    msg=f"P1.2 local mirror diverges from runner curve at pr={pr}",
                )


if __name__ == "__main__":
    unittest.main()
