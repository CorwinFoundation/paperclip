from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.probe_registry_test_support import copy_registry_fixture

REPO = Path(__file__).resolve().parents[1]

class ProbeRegistryBaselineTest(unittest.TestCase):
    _cached_registry: dict | None = None

    def _emit(self, path: Path) -> dict:
        if self.__class__._cached_registry is None:
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
            self.__class__._cached_registry = json.loads(path.read_text(encoding="utf-8"))
        return self.__class__._cached_registry

    def test_emit_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.json"
            second = Path(tmp) / "second.json"
            self.assertEqual(self._emit(first), self._emit(second))

    def test_active_view_is_byte_equivalent_to_authoritative_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            authoritative = Path(tmp) / "authoritative.json"
            active = Path(tmp) / "active.json"
            subprocess.check_call(
                [
                    sys.executable,
                    "-m",
                    "tools.probe_registry",
                    "--repo",
                    str(REPO),
                    "--emit",
                    str(authoritative),
                    "--active-emit",
                    str(active),
                ],
                cwd=REPO,
            )
            authoritative_payload = json.loads(authoritative.read_text(encoding="utf-8"))
            active_payload = json.loads(active.read_text(encoding="utf-8"))
        self.assertEqual(active_payload, authoritative_payload)

    def test_added_probe_without_active_regeneration_fails_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp) / "repo"
            copy_registry_fixture(REPO, clone)
            mapping = (
                clone
                / "rubric"
                / "probe_mappings"
                / "p1_1_factuality_golden.yaml"
            )
            text = mapping.read_text(encoding="utf-8")
            marker = "\n  - probe_id: BEAAA-23954-DRIFT-REGRESSION\n    dim: P1.1\n"
            mapping.write_text(text + marker, encoding="utf-8")
            stale_active = clone / "tools" / "probe-registry" / "active_registry.json"
            preserved = stale_active.read_bytes()
            throwaway_active = Path(tmp) / "throwaway-active.json"
            subprocess.check_call(
                [
                    sys.executable,
                    "-m",
                    "tools.probe_registry",
                    "--repo",
                    str(clone),
                    "--active-emit",
                    str(throwaway_active),
                ],
                cwd=clone,
            )
            self.assertEqual(stale_active.read_bytes(), preserved)
            result = subprocess.run(
                [sys.executable, "-m", "tools.probe_registry", "--repo", str(clone), "--check"],
                cwd=clone,
            )
            self.assertNotEqual(result.returncode, 0)

    def test_active_view_uses_canonical_probe_set_pin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp) / "repo"
            copy_registry_fixture(REPO, clone)
            mapping = clone / "rubric" / "probe_mappings" / "p1_1_factuality_golden.yaml"
            text = mapping.read_text(encoding="utf-8")
            mapping.write_text(
                text.replace('version: "0.2.0"', 'version: "9.9.9"', 1),
                encoding="utf-8",
            )
            active = Path(tmp) / "active.json"
            subprocess.check_call(
                [
                    sys.executable,
                    "-m",
                    "tools.probe_registry",
                    "--repo",
                    str(clone),
                    "--emit",
                    str(Path(tmp) / "authoritative.json"),
                    "--active-emit",
                    str(active),
                ],
                cwd=clone,
            )
            payload = json.loads(active.read_text(encoding="utf-8"))

        rows = [
            row
            for row in payload["rows"]
            if row["corpus_or_family"] == "p1_1_factuality_golden"
        ]
        self.assertEqual(len(rows), 34)
        self.assertEqual(
            {row["version_pin"] for row in rows},
            {"p1_1_factuality_golden@0.2.0"},
        )

    def test_unique_executable_count_matches_runnable_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = self._emit(Path(tmp) / "registry.json")
        runnable = [
            row for row in registry["rows"]
            if row["categories"]["executable"]
        ]
        self.assertEqual(registry["unique_executable_count"], len(runnable))
        self.assertGreaterEqual(registry["category_counts"]["executable"], 1)

    def test_registry_paths_use_portable_separators(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = self._emit(Path(tmp) / "registry.json")
        fixture_rows = [
            row for row in registry["rows"]
            if row["bucket"] == "fixture_test_only"
        ]
        self.assertTrue(fixture_rows)
        for row in fixture_rows:
            self.assertNotIn("\\", row["case_id_or_probe_id"])
        for row in registry["rows"]:
            self.assertNotIn("\\", row["source"])
            provenance = row.get("source_provenance") or {}
            if "path" in provenance:
                self.assertNotIn("\\", provenance["path"])

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
        self.assertEqual(claim["claim_gate"], "manual_founder_approval_required")
        self.assertEqual(
            claim["disposition"],
            "unsupported_claim_capped_to_runnable_today_pending_founder_approval",
        )
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
            "P2.2",
            "P2.6",
            "P4.2",
            "P4.3",
            "P4.4",
            "P4.6",
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

    def test_bound_governance_attestation_pack_is_not_default_runtime_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = self._emit(Path(tmp) / "registry.json")
        active_by_id = {
            point["rubric_point_id"]: point
            for point in registry["active_rubric_points"]
        }
        point = active_by_id["GOVERNANCE.PII.HANDLING_ATTESTATION"]
        self.assertEqual(point["status"], "fail_closed_unavailable")
        self.assertIsNotNone(point["unavailable_reason"])
        self.assertFalse(point["runnable_entries"])

    def test_cto_open_source_first_constraint_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = self._emit(Path(tmp) / "registry.json")
        self.assertEqual(registry["metadata"]["parser_dependency"], "PyYAML==6.0.2")
        self.assertEqual(
            registry["metadata"]["stdlib_dependencies"],
            ["pathlib", "json", "csv", "hashlib"],
        )
        self.assertEqual(registry["upstream_framework_pins"]["promptfoo"], "0.115.0")
        self.assertEqual(registry["upstream_framework_pins"]["garak"], "0.16.0")
        self.assertEqual(registry["upstream_framework_pins"]["inspect-ai"], "0.3.220")
        self.assertIn("rubric/probe_mappings/*.yaml", registry["source_inputs"])


if __name__ == "__main__":
    unittest.main()
