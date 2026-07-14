from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class ProbeRegistryBaselineTest(unittest.TestCase):
    def _emit(self, path: Path) -> dict:
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "tools.probe_registry",
                "--repo",
                str(REPO),
                "--emit",
                str(path),
            ],
            cwd=REPO,
        )
        return json.loads(path.read_text(encoding="utf-8"))

    def test_emit_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.json"
            second = Path(tmp) / "second.json"
            self.assertEqual(self._emit(first), self._emit(second))

    def test_unique_executable_count_matches_runnable_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = self._emit(Path(tmp) / "registry.json")
        runnable = [
            row for row in registry["rows"]
            if row["bucket"] == "runnable_today" and row["countable_as_executable"]
        ]
        self.assertEqual(registry["unique_executable_count"], len(runnable))
        self.assertGreaterEqual(registry["unique_executable_count"], 1)

    def test_every_active_rubric_point_has_executable_or_fail_closed_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = self._emit(Path(tmp) / "registry.json")
        self.assertEqual(len(registry["active_rubric_points"]), registry["metadata"]["active_rubric_point_count"])
        for point in registry["active_rubric_points"]:
            if point["runnable_entries"]:
                self.assertEqual(point["status"], "executable")
                self.assertIsNone(point["unavailable_reason"])
            else:
                self.assertEqual(point["status"], "fail_closed_unavailable")
                self.assertIn("no current customer-true walker row", point["unavailable_reason"])

    def test_claimed_1180_disposition_is_bucketed_and_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = self._emit(Path(tmp) / "registry.json")
        claim = registry["claimed_1180_disposition"]
        self.assertEqual(claim["claimed_count"], 1180)
        self.assertFalse(claim["claim_supported"])
        self.assertEqual(claim["supported_unique_executable_count"], registry["unique_executable_count"])
        self.assertGreater(claim["unsupported_rows_count"], 0)

    def test_unbound_worker_rows_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = self._emit(Path(tmp) / "registry.json")
        active_by_id = {
            point["rubric_point_id"]: point
            for point in registry["active_rubric_points"]
        }
        for rubric_point_id in (
            "P1.2",
            "P2.2",
            "P2.6",
            "P3.2",
            "P4.2",
            "P4.3",
            "P4.4",
            "P4.6",
            "ROBUSTNESS.PII.SCAN_TIME_DETECTION",
            "ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER",
            "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL",
        ):
            self.assertEqual(
                active_by_id[rubric_point_id]["status"],
                "fail_closed_unavailable",
            )
            self.assertFalse(active_by_id[rubric_point_id]["runnable_entries"])

    def test_unbound_attestation_rows_are_not_counted_as_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = self._emit(Path(tmp) / "registry.json")
        active_by_id = {
            point["rubric_point_id"]: point
            for point in registry["active_rubric_points"]
        }
        target_rows = {
            "P2.6",
            "P4.2",
            "P4.3",
            "P4.4",
            "P4.6",
        }
        for rubric_point_id in target_rows:
            point = active_by_id[rubric_point_id]
            self.assertEqual(point["status"], "fail_closed_unavailable", rubric_point_id)
            self.assertIsNotNone(point["unavailable_reason"], rubric_point_id)
            self.assertFalse(point["runnable_entries"], rubric_point_id)

    def test_bound_governance_attestation_pack_is_counted_as_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = self._emit(Path(tmp) / "registry.json")
        active_by_id = {
            point["rubric_point_id"]: point
            for point in registry["active_rubric_points"]
        }
        point = active_by_id["GOVERNANCE.PII.HANDLING_ATTESTATION"]
        self.assertEqual(point["status"], "executable")
        self.assertIsNone(point["unavailable_reason"])
        self.assertEqual(len(point["runnable_entries"]), 1)

    def test_cto_open_source_first_constraint_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = self._emit(Path(tmp) / "registry.json")
        self.assertEqual(registry["metadata"]["parser_dependency"], "PyYAML==6.0.2")
        self.assertEqual(
            registry["metadata"]["stdlib_dependencies"],
            ["pathlib", "json", "csv", "hashlib"],
        )
        self.assertEqual(registry["upstream_framework_pins"]["promptfoo"], "0.115.0")
        self.assertEqual(registry["upstream_framework_pins"]["garak"], "0.10.0")
        self.assertEqual(registry["upstream_framework_pins"]["inspect-ai"], "0.3.220")
        self.assertIn("rubric/probe_mappings/*.yaml", registry["source_inputs"])


if __name__ == "__main__":
    unittest.main()
