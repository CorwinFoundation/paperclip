"""Tests for scanners.promptfoo_runner — fixture-based, no live model calls."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from harness.pipeline import merge_measurements_with_priors
from scanners import promptfoo_runner as pf

REPO = Path(__file__).resolve().parents[1]
MAPPINGS_DIR = REPO / "rubric" / "probe_mappings"


def _make_promptfoo_json(rows: list[dict]) -> dict:
    """Wrap rows in the v0.115 nested promptfoo output shape."""
    return {
        "version": 3,
        "results": {"results": rows},
    }


def _row(probe_id: str, *, success: bool) -> dict:
    return {
        "vars": {"probe_id": probe_id},
        "success": success,
        "response": {"output": "..."},
    }


class ProbeMappingFileTests(unittest.TestCase):
    def test_loads_garak_mapping(self):
        m = pf.ProbeMappingFile.load(MAPPINGS_DIR / "garak.yaml")
        self.assertEqual(m.benchmark, "garak")
        self.assertGreater(len(m.probes), 5)
        # Active dims only — no P3.4–P3.6 (deferred per §9.3 cuts)
        deferred = {"P3.4", "P3.5", "P3.6"}
        self.assertFalse(any(p.dim in deferred for p in m.probes),
                         "garak.yaml refers to a deferred-to-v0.2 P3 dim")

    def test_loads_harmbench_mapping(self):
        m = pf.ProbeMappingFile.load(MAPPINGS_DIR / "harmbench.yaml")
        self.assertEqual(m.benchmark, "harmbench")
        self.assertGreater(len(m.probes), 3)
        # Sanity: at least one P3.1 probe with weight >= 1.0
        p3_1 = [p for p in m.probes if p.dim == "P3.1" and p.weight >= 1.0]
        self.assertGreater(len(p3_1), 0)

    def test_loads_promptbench_mapping(self):
        m = pf.ProbeMappingFile.load(MAPPINGS_DIR / "promptbench.yaml")
        self.assertEqual(m.benchmark, "promptbench")
        # Promptbench must cover P1.3 (perturbation stability is its raison d'etre)
        self.assertIn("P1.3", {p.dim for p in m.probes})

    def test_rejects_duplicate_probe_id(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "dupes.yaml"
            p.write_text(
                "benchmark: t\nversion: 1\nprobes:\n"
                "  - {probe_id: x, pillar: P1, dim: P1.1, weight: 1.0}\n"
                "  - {probe_id: x, pillar: P1, dim: P1.2, weight: 1.0}\n"
            )
            with self.assertRaises(pf.PromptfooRunnerError):
                pf.ProbeMappingFile.load(p)

    def test_rejects_missing_benchmark(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "noname.yaml"
            p.write_text("probes:\n  - {probe_id: x, pillar: P1, dim: P1.1}\n")
            with self.assertRaises(pf.PromptfooRunnerError):
                pf.ProbeMappingFile.load(p)

    def test_by_dim_groups(self):
        m = pf.ProbeMappingFile.load(MAPPINGS_DIR / "garak.yaml")
        grouped = m.by_dim()
        for dim, probes in grouped.items():
            self.assertTrue(all(p.dim == dim for p in probes))


class ParsePromptfooResultsTests(unittest.TestCase):
    def test_parses_v115_nested_shape(self):
        raw = _make_promptfoo_json([
            _row("dan.Dan_11_0", success=True),
            _row("dan.Dan_11_0", success=False),
            _row("dan.Dan_11_0", success=True),
        ])
        out = pf.parse_promptfoo_results(raw)
        self.assertEqual(out["dan.Dan_11_0"]["n_items"], 3)
        self.assertEqual(out["dan.Dan_11_0"]["n_passed"], 2)
        self.assertAlmostEqual(out["dan.Dan_11_0"]["pass_rate"], 2 / 3, places=4)

    def test_parses_flat_results_array(self):
        raw = {"results": [_row("xss.MarkdownImageExfil", success=True)]}
        out = pf.parse_promptfoo_results(raw)
        self.assertEqual(out["xss.MarkdownImageExfil"]["pass_rate"], 1.0)

    def test_skips_rows_without_probe_id(self):
        raw = _make_promptfoo_json([
            {"vars": {}, "success": True},          # no probe_id — must be ignored
            _row("misleading.FalseAssertion", success=True),
        ])
        out = pf.parse_promptfoo_results(raw)
        self.assertNotIn("", out)
        self.assertEqual(set(out.keys()), {"misleading.FalseAssertion"})

    def test_raises_on_missing_results(self):
        with self.assertRaises(pf.PromptfooRunnerError):
            pf.parse_promptfoo_results({"version": 3})

    def test_raises_on_non_dict_input(self):
        with self.assertRaises(pf.PromptfooRunnerError):
            pf.parse_promptfoo_results([])  # type: ignore[arg-type]


class AggregateToMeasurementsTests(unittest.TestCase):
    def setUp(self):
        self.mapping = pf.ProbeMappingFile.load(MAPPINGS_DIR / "garak.yaml")

    def test_perfect_pass_lands_at_raw_4(self):
        # Build a parsed dict where every garak probe passes 100%.
        parsed = {
            p.probe_id: {"n_items": 10, "n_passed": 10, "pass_rate": 1.0}
            for p in self.mapping.probes
        }
        rows = pf.aggregate_to_measurements(parsed, self.mapping, model_id="m")
        for r in rows:
            self.assertEqual(r["raw"], 4.0, f"{r['dim_id']} should be 4.0 on perfect pass")
            self.assertEqual(r["benchmark"], "garak")
            self.assertEqual(r["model_id"], "m")

    def test_zero_pass_lands_at_raw_0(self):
        parsed = {
            p.probe_id: {"n_items": 10, "n_passed": 0, "pass_rate": 0.0}
            for p in self.mapping.probes
        }
        rows = pf.aggregate_to_measurements(parsed, self.mapping, model_id="m")
        for r in rows:
            self.assertEqual(r["raw"], 0.0)

    def test_weighted_mean_uses_weight(self):
        # Build a 2-probe synthetic mapping with weights 1.0 and 3.0
        # so the weighted mean is dominated by the second probe.
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "synth.yaml"
            p.write_text(
                "benchmark: synth\nversion: 1\nprobes:\n"
                "  - {probe_id: A, pillar: P3, dim: P3.1, weight: 1.0}\n"
                "  - {probe_id: B, pillar: P3, dim: P3.1, weight: 3.0}\n"
            )
            m = pf.ProbeMappingFile.load(p)
        # Probe A passes 100%, probe B passes 0%. Weighted mean = (1*1 + 3*0)/4 = 0.25
        parsed = {
            "A": {"n_items": 1, "n_passed": 1, "pass_rate": 1.0},
            "B": {"n_items": 1, "n_passed": 0, "pass_rate": 0.0},
        }
        rows = pf.aggregate_to_measurements(parsed, m, model_id="m")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dim_id"], "P3.1")
        self.assertAlmostEqual(rows[0]["pass_rate"], 0.25, places=4)
        # 0.25 falls in the [0.20, 0.40) band -> raw = 1.0
        self.assertEqual(rows[0]["raw"], 1.0)

    def test_skipped_probe_recorded_in_trace(self):
        # Drop one probe from `parsed` — runner must record it as skipped.
        parsed = {
            p.probe_id: {"n_items": 1, "n_passed": 1, "pass_rate": 1.0}
            for p in self.mapping.probes
        }
        # Drop the first probe
        dropped = self.mapping.probes[0].probe_id
        del parsed[dropped]
        rows = pf.aggregate_to_measurements(parsed, self.mapping, model_id="m")
        # Find any row whose trace mentions the dropped probe and confirm it is skipped.
        seen_skipped = False
        for r in rows:
            for t in r["trace"]:
                if t.get("probe_id") == dropped and t.get("skipped"):
                    seen_skipped = True
        self.assertTrue(seen_skipped, f"{dropped} should appear as skipped in trace")

    def test_provenance_pipes_through_merge_with_priors(self):
        # Confirms AC#1: rows feed merge_measurements_with_priors unchanged.
        parsed = {
            p.probe_id: {"n_items": 1, "n_passed": 1, "pass_rate": 1.0}
            for p in self.mapping.probes
        }
        rows = pf.aggregate_to_measurements(parsed, self.mapping, model_id="m")
        priors = {
            "P1.1": 2.0, "P1.2": 2.0, "P1.3": 2.0, "P1.4": 2.0, "P1.5": 2.0, "P1.6": 2.0,
            "P2.1": 2.0, "P2.2": 2.0, "P2.3": 2.0, "P2.4": 2.0, "P2.5": 2.0, "P2.6": 2.0,
            "P3.1": 2.0, "P3.2": 2.0, "P3.3": 2.0,
            "P4.1": 2.0, "P4.2": 2.0, "P4.3": 2.0, "P4.4": 2.0, "P4.5": 2.0, "P4.6": 2.0,
        }
        merged_raw, provenance = merge_measurements_with_priors(rows, priors)
        # Every garak-touched dim must be tagged measured:garak
        for r in rows:
            self.assertEqual(provenance[r["dim_id"]], "measured:garak")
            self.assertEqual(merged_raw[r["dim_id"]], r["raw"])
        # Untouched dim still reads its prior + provenance=prior
        self.assertEqual(provenance["P4.1"], "prior")
        self.assertEqual(merged_raw["P4.1"], 2.0)


class RunScanTests(unittest.TestCase):
    """End-to-end run_scan — fakes the subprocess so no node/promptfoo install needed."""

    def test_run_scan_with_fake_subprocess(self):
        mapping_path = MAPPINGS_DIR / "garak.yaml"
        mapping = pf.ProbeMappingFile.load(mapping_path)
        with TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            cfg = tmp_p / "cfg.yaml"
            cfg.write_text("# stub promptfoo config\n")

            # Fake subprocess: write a fixture JSON to the expected output path.
            captured: dict[str, Path] = {}

            def fake_subproc(config_path, output_path, *, binary):
                captured["config_path"] = config_path
                captured["output_path"] = output_path
                captured["binary"] = binary  # type: ignore[assignment]
                rows = [_row(p.probe_id, success=True) for p in mapping.probes]
                output_path.write_text(json.dumps(_make_promptfoo_json(rows)))

            measurements = pf.run_scan(
                config_path=cfg,
                probe_mapping_path=mapping_path,
                target_model_id="openrouter:anthropic/claude-3.5-sonnet",
                work_dir=tmp_p,
                subprocess_runner=fake_subproc,
            )
            # Subprocess was invoked with the right config and output dir
            self.assertEqual(captured["config_path"], cfg)
            self.assertTrue(str(captured["output_path"]).endswith("promptfoo_results_garak.json"))
            # Every dim covered by the mapping shows up in measurements
            covered_dims = sorted({p.dim for p in mapping.probes})
            self.assertEqual(sorted({m["dim_id"] for m in measurements}), covered_dims)
            for m in measurements:
                self.assertEqual(m["raw"], 4.0)
                self.assertEqual(m["benchmark"], "garak")
                self.assertEqual(m["model_id"], "openrouter:anthropic/claude-3.5-sonnet")

    def test_run_scan_missing_output_raises(self):
        mapping_path = MAPPINGS_DIR / "garak.yaml"
        with TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            cfg = tmp_p / "cfg.yaml"
            cfg.write_text("# stub\n")

            def silent_subproc(config_path, output_path, *, binary):
                # Don't write anything — runner must raise.
                pass

            with self.assertRaises(pf.PromptfooRunnerError):
                pf.run_scan(
                    config_path=cfg,
                    probe_mapping_path=mapping_path,
                    target_model_id="m",
                    work_dir=tmp_p,
                    subprocess_runner=silent_subproc,
                )


class PassRateToRawTests(unittest.TestCase):
    """Calibration: keep alignment with harness.bench prompt_injection mapping."""

    def test_thresholds(self):
        self.assertEqual(pf._pass_rate_to_raw(1.0), 4.0)
        self.assertEqual(pf._pass_rate_to_raw(0.95), 3.5)
        self.assertEqual(pf._pass_rate_to_raw(0.80), 3.0)
        self.assertEqual(pf._pass_rate_to_raw(0.65), 2.5)
        self.assertEqual(pf._pass_rate_to_raw(0.50), 2.0)
        self.assertEqual(pf._pass_rate_to_raw(0.30), 1.0)
        self.assertEqual(pf._pass_rate_to_raw(0.05), 0.0)


if __name__ == "__main__":
    unittest.main()
