"""BEAAA-464 — unit tests for harness/cli_runner.py and bin/are-scan.

Coverage:
  - load_inputs: valid + every rejection path (path traversal, oversize
    file, malformed agent_id, missing eval_results, bad JSON, raw out of
    range, missing dim id).
  - run_scan: end-to-end against G1 golden fixture, asserting are_total /
    pillar subscores match the locked expected_score.json.
  - Output reproducibility (Supreme Law): same inputs + same seed +
    pinned scored_at → byte-identical JSON across N=5 runs of the CLI
    subprocess.
  - Exit code semantics: 0 / 1 / 2 / 3 via bin/are-scan subprocess.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.cli_runner import (  # noqa: E402
    CliEvidenceError,
    CliInputError,
    load_inputs,
    run_scan,
)

CLI = REPO / "bin" / "are-scan"
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
G1_EVAL = REPO / "tests" / "fixtures" / "golden_agents" / "G1" / "eval_outputs.json"
G1_EXPECTED = (
    REPO / "tests" / "fixtures" / "golden_agents" / "G1" / "expected_score.json"
)
FIXED_SCORED_AT = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


class InputLoaderTests(unittest.TestCase):
    def _g1_dir(self, tmp: Path) -> Path:
        d = tmp / "G1"
        d.mkdir(parents=True)
        (d / "eval_results.json").write_text(G1_EVAL.read_text())
        return d

    def test_load_g1_succeeds(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            d = self._g1_dir(tmp)
            inputs = load_inputs("G1", d)
            self.assertEqual(inputs.agent_id, "G1")
            self.assertIn("P1.1", inputs.eval_results)
            self.assertEqual(inputs.eval_results["P1.1"]["raw"], 4.0)
            self.assertIn("eval_results.json", inputs.file_sha256s)
            self.assertTrue(
                inputs.file_sha256s["eval_results.json"].startswith("sha256:")
            )

    def test_agent_id_rejects_traversal(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpd:
            d = self._g1_dir(Path(tmpd))
            with self.assertRaises(CliInputError):
                load_inputs("../etc/passwd", d)
            with self.assertRaises(CliInputError):
                load_inputs("", d)
            with self.assertRaises(CliInputError):
                load_inputs("a" * 200, d)

    def test_eval_results_required(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpd:
            empty = Path(tmpd) / "empty"
            empty.mkdir()
            with self.assertRaises(CliInputError) as ctx:
                load_inputs("G1", empty)
            self.assertIn("eval_results.json", str(ctx.exception))

    def test_eval_results_rejects_bad_raw(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpd:
            d = self._g1_dir(Path(tmpd))
            (d / "eval_results.json").write_text(
                json.dumps({"P1.1": {"raw": 99}})
            )
            with self.assertRaises(CliInputError) as ctx:
                load_inputs("G1", d)
            self.assertIn("outside [0,4]", str(ctx.exception))

    def test_eval_results_rejects_bad_dim_id(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpd:
            d = self._g1_dir(Path(tmpd))
            (d / "eval_results.json").write_text(
                json.dumps({"NOPE": {"raw": 2.0}})
            )
            with self.assertRaises(CliInputError):
                load_inputs("G1", d)

    def test_eval_results_rejects_malformed_json(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpd:
            d = self._g1_dir(Path(tmpd))
            (d / "eval_results.json").write_bytes(b"{not json")
            with self.assertRaises(CliInputError):
                load_inputs("G1", d)

    def test_rejects_oversize_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpd:
            d = self._g1_dir(Path(tmpd))
            # 11MB system prompt should trip the 10MB cap.
            (d / "system_prompt.txt").write_bytes(b"A" * (11 * 1024 * 1024))
            with self.assertRaises(CliInputError) as ctx:
                load_inputs("G1", d)
            self.assertIn("exceeds", str(ctx.exception))

    def test_symlink_outside_input_dir_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            outside = tmp / "outside.txt"
            outside.write_text("secret")
            d = self._g1_dir(tmp)
            link = d / "system_prompt.txt"
            link.symlink_to(outside)
            # Resolution puts the file outside the input-dir → reject.
            with self.assertRaises(CliInputError) as ctx:
                load_inputs("G1", d)
            self.assertIn("outside", str(ctx.exception))


class RunScanTests(unittest.TestCase):
    def _setup_g1(self, tmp: Path) -> Path:
        d = tmp / "G1"
        d.mkdir(parents=True)
        (d / "eval_results.json").write_text(G1_EVAL.read_text())
        return d

    def test_run_scan_matches_g1_expected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpd:
            d = self._setup_g1(Path(tmpd))
            result = run_scan(
                agent_id="G1",
                input_dir=d,
                seed=42,
                corpus_version="0.1.1",
                rubric_path=RUBRIC_PATH,
                scored_at=FIXED_SCORED_AT,
            )
        expected = json.loads(G1_EXPECTED.read_text())
        # Core scoring math must match the locked golden — this is the
        # Supreme Law in-band-with-priors reproducibility check.
        self.assertEqual(result["bmi"], expected["bmi"])
        self.assertEqual(result["are_total"], expected["bmi"])
        for pid in ("OPS", "BIAS_FAIRNESS", "ROBUSTNESS", "GOVERNANCE"):
            self.assertAlmostEqual(
                result["subscores"][pid],
                expected["pillars"][pid]["score"],
                places=3,
            )
        # Output-shape additions must all be present (contract surface).
        for field in (
            "are_total", "subscores", "band",
            "band_letter", "confidence", "evidence_refs",
            "uncovered_dimensions", "scanner_version", "corpus_version",
            "seed", "run_timestamp", "contract_version", "agent_id",
            "input_file_sha256s",
        ):
            self.assertIn(field, result, f"missing output field {field}")
        # Post-BEAAA-470: the ADR-0005 subscores_contract_named bridge view
        # is removed — `subscores` is contract-named at the source.
        self.assertNotIn("subscores_contract_named", result)
        # Reproducibility stamps must round-trip.
        self.assertEqual(result["seed"], 42)
        self.assertEqual(result["corpus_version"], "0.1.1")
        self.assertEqual(result["agent_id"], "G1")

    def test_run_scan_byte_identical_across_replicates(self):
        """Supreme Law: same inputs + same seed → byte-identical JSON."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpd:
            d = self._setup_g1(Path(tmpd))
            blobs = []
            for _ in range(5):
                result = run_scan(
                    agent_id="G1",
                    input_dir=d,
                    seed=42,
                    corpus_version="0.1.1",
                    rubric_path=RUBRIC_PATH,
                    scored_at=FIXED_SCORED_AT,
                )
                blobs.append(json.dumps(result, sort_keys=True, default=str))
        self.assertEqual(len(set(blobs)), 1, "outputs drifted across replicates")

    def test_run_scan_subscore_within_one_point_band(self):
        """Hard guarantee #1: total AND every subscore within ±1 across N=5."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpd:
            d = self._setup_g1(Path(tmpd))
            totals: list[int] = []
            subs: dict[str, list[float]] = {p: [] for p in ("OPS", "BIAS_FAIRNESS", "ROBUSTNESS", "GOVERNANCE")}
            for seed in range(5):
                result = run_scan(
                    agent_id="G1",
                    input_dir=d,
                    seed=seed,
                    corpus_version="0.1.1",
                    rubric_path=RUBRIC_PATH,
                    scored_at=FIXED_SCORED_AT,
                )
                totals.append(result["are_total"])
                for p in subs:
                    subs[p].append(result["subscores"][p])
        self.assertLessEqual(max(totals) - min(totals), 1,
                             f"are_total drift > 1: {totals}")
        for p, vals in subs.items():
            self.assertLessEqual(
                max(vals) - min(vals), 1.0,
                f"{p} subscore drift > 1: {vals}",
            )

    def test_run_scan_refuses_when_evidence_uri_empty(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpd:
            d = self._setup_g1(Path(tmpd))
            # Strip evidence_uri off every dim — refuse-to-quote condition.
            eo = {
                k: {"raw": v["raw"], "evidence_uri": ""}
                for k, v in json.loads(G1_EVAL.read_text()).items()
            }
            (d / "eval_results.json").write_text(json.dumps(eo))
            with self.assertRaises(CliEvidenceError):
                run_scan(
                    agent_id="G1",
                    input_dir=d,
                    seed=42,
                    corpus_version="0.1.1",
                    rubric_path=RUBRIC_PATH,
                    scored_at=FIXED_SCORED_AT,
                )


class CliSubprocessTests(unittest.TestCase):
    """Exercise the actual `bin/are-scan` executable end-to-end."""

    def _setup_g1(self, tmp: Path) -> Path:
        d = tmp / "G1"
        d.mkdir(parents=True)
        (d / "eval_results.json").write_text(G1_EVAL.read_text())
        return d

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            capture_output=True, text=True, cwd=str(REPO),
        )

    def test_exit_0_clean(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpd:
            d = self._setup_g1(Path(tmpd))
            r = self._run(
                "--agent", "G1", "--seed", "42",
                "--input-dir", str(d),
                "--scored-at", FIXED_SCORED_AT.isoformat(),
            )
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["are_total"], 84)

    def test_exit_2_bad_agent_id(self):
        r = self._run(
            "--agent", "../etc/passwd", "--seed", "42",
            "--input-dir", "/tmp",
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("agent_id", r.stderr)

    def test_exit_2_version_mismatch(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpd:
            d = self._setup_g1(Path(tmpd))
            r = self._run(
                "--agent", "G1", "--seed", "42",
                "--version", "99.0.0",
                "--input-dir", str(d),
            )
        self.assertEqual(r.returncode, 2)
        self.assertIn("scanner", r.stderr.lower())

    def test_exit_1_evidence_incomplete(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpd:
            d = self._setup_g1(Path(tmpd))
            eo = {
                k: {"raw": v["raw"], "evidence_uri": ""}
                for k, v in json.loads(G1_EVAL.read_text()).items()
            }
            (d / "eval_results.json").write_text(json.dumps(eo))
            r = self._run(
                "--agent", "G1", "--seed", "42",
                "--input-dir", str(d),
                "--scored-at", FIXED_SCORED_AT.isoformat(),
            )
        self.assertEqual(r.returncode, 1)
        self.assertIn("refuse-to-quote", r.stderr)

    def test_subprocess_byte_identical_n5(self):
        """End-to-end CLI reproducibility check across 5 subprocess runs."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpd:
            d = self._setup_g1(Path(tmpd))
            outs: list[str] = []
            for i in range(5):
                out_path = Path(tmpd) / f"run_{i}.json"
                r = self._run(
                    "--agent", "G1", "--seed", "42",
                    "--input-dir", str(d),
                    "--scored-at", FIXED_SCORED_AT.isoformat(),
                    "--out", str(out_path),
                )
                self.assertEqual(r.returncode, 0, r.stderr)
                outs.append(out_path.read_text())
        self.assertEqual(len(set(outs)), 1,
                         "bin/are-scan output drifted across replicates")


if __name__ == "__main__":
    unittest.main()
