from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.canonical_test_registry import CEO_DISPOSITIONS, build_registry, validate_registry


REPO = Path(__file__).resolve().parents[1]


class CanonicalTestRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = build_registry(REPO)

    def test_every_asset_has_exactly_one_ceo_disposition(self) -> None:
        rows = self.registry["rows"]
        self.assertGreater(len(rows), 0)
        for row in rows:
            self.assertIn(row["disposition"], CEO_DISPOSITIONS, msg=row)
            self.assertIsInstance(row["disposition"], str, msg=row)
        self.assertNotIn("errors", self.registry)

    def test_unique_active_executable_count_is_exact_case_count(self) -> None:
        rows = self.registry["rows"]
        active = [
            row
            for row in rows
            if row["disposition"] == "production_active"
            and row["countable_as_active_executable"] is True
        ]
        active_case_ids = {row["case_id"] for row in active}
        self.assertEqual(len(active), len(active_case_ids))
        self.assertEqual(
            self.registry["metadata"]["unique_active_executable_case_count"],
            len(active_case_ids),
        )
        self.assertGreater(len(active_case_ids), 4)

    def test_duplicate_and_fixture_rows_do_not_count(self) -> None:
        duplicate = {
            "asset_id": "asset-a",
            "case_id": "same-case",
            "disposition": "duplicate",
            "rubric_point_id": "P1.1",
            "source": "tests/fixture.json",
            "framework": "fixture",
            "version_pin": "fixture@n/a",
            "countable_as_active_executable": False,
        }
        fixture = dict(duplicate, asset_id="asset-b", case_id="fixture-case", disposition="fixture_only")
        self.assertFalse(validate_registry([duplicate, fixture]))

    def test_gate_rejects_missing_disposition_and_duplicate_active_case_id(self) -> None:
        rows = [
            {
                "asset_id": "asset-a",
                "case_id": "same-case",
                "disposition": "production_active",
                "rubric_point_id": "P1.1",
                "source": "scanner",
                "framework": "inspect",
                "version_pin": "inspect@0.3.220",
                "countable_as_active_executable": True,
            },
            {
                "asset_id": "asset-b",
                "case_id": "same-case",
                "disposition": "production_active",
                "rubric_point_id": "P1.1",
                "source": "scanner",
                "framework": "inspect",
                "version_pin": "inspect@0.3.220",
                "countable_as_active_executable": True,
            },
            {
                "asset_id": "asset-c",
                "case_id": "missing-disposition",
                "disposition": "",
                "rubric_point_id": "P1.2",
                "source": "scanner",
                "framework": "inspect",
                "version_pin": "inspect@0.3.220",
                "countable_as_active_executable": False,
            },
        ]
        errors = validate_registry(rows)
        self.assertTrue(any("duplicate executable case_id: same-case" in error for error in errors))
        self.assertTrue(any("missing required fields: disposition" in error for error in errors))

    def test_cli_emit_is_machine_readable_and_checkable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "canonical_registry.json"
            subprocess.check_call(
                [
                    sys.executable,
                    "-m",
                    "tools.canonical_test_registry",
                    "--repo",
                    str(REPO),
                    "--emit",
                    str(out),
                    "--check",
                ],
                cwd=REPO,
            )
            emitted = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(emitted["metadata"]["ticket_ref"], "BEAAA-13762")
        self.assertEqual(emitted["metadata"]["contract"], "are-scanner-api-v1@v1.0")


if __name__ == "__main__":
    unittest.main()
