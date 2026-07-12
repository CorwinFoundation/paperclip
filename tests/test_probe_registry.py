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
        self.assertEqual(registry["unique_executable_count"], 4)

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
        self.assertEqual(claim["supported_unique_executable_count"], 4)
        self.assertGreater(claim["unsupported_rows_count"], 0)

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
