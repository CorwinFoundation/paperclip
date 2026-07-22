"""BEAAA-13644 active-test registry count gate."""
from __future__ import annotations

import copy
import unittest

from tools.active_test_registry import (
    ALLOWED_PILLARS,
    REQUIRED_FIELDS,
    build_registry,
    count_active_executable,
    count_target_bound_runtime,
    load_published_count,
    summarize,
    validate_registry,
)


class ActiveTestRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = build_registry()

    def test_registry_rows_carry_required_metadata(self) -> None:
        self.assertGreater(len(self.rows), 0)
        for row in self.rows:
            self.assertFalse(
                [field for field in REQUIRED_FIELDS if row.get(field) in (None, "")],
                msg=row,
            )

    def test_registry_rejects_duplicate_ids_missing_metadata_and_stale_pillars(self) -> None:
        broken = copy.deepcopy(self.rows)
        broken.append(copy.deepcopy(broken[0]))
        broken[0]["owner"] = ""
        broken[1]["pillar"] = "P3"
        errors = validate_registry(broken, published_count=0)
        self.assertTrue(any("duplicate registry id" in error for error in errors))
        self.assertTrue(any("missing required metadata" in error for error in errors))
        self.assertTrue(any("stale pillar" in error for error in errors))

    def test_only_canonical_pillars_present(self) -> None:
        self.assertTrue({row["pillar"] for row in self.rows} <= ALLOWED_PILLARS)

    def test_published_count_cannot_exceed_active_executable_count(self) -> None:
        active_executable = count_active_executable(self.rows)
        errors = validate_registry(self.rows, published_count=active_executable + 1)
        self.assertTrue(any("exceeds active executable registry count" in error for error in errors))
        self.assertFalse(validate_registry(self.rows, published_count=load_published_count()))

    def test_demo_and_lane_a_cases_are_target_bound_without_inflating_evidence_count(self) -> None:
        self.assertEqual(count_active_executable(self.rows), load_published_count())
        self.assertEqual(count_active_executable(self.rows), 1)
        self.assertEqual(count_target_bound_runtime(self.rows), 203)
        runtime_cases = [
            row
            for row in self.rows
            if row["status"] == "active"
            and row["target_bound"] is True
            and row["case_class"] != "attestation"
            and row["reporting_status"] != "reported_not_scored"
        ]
        self.assertEqual(len(runtime_cases), 195)

    def test_summary_reports_count_delta_to_1180(self) -> None:
        summary = summarize(self.rows, published_count=load_published_count())
        self.assertEqual(summary["registry_rows"], len(self.rows))
        self.assertEqual(summary["active_executable_count"], count_active_executable(self.rows))
        self.assertEqual(
            summary["target_bound_runtime_count"], count_target_bound_runtime(self.rows)
        )
        self.assertEqual(summary["delta_to_1180"], summary["active_executable_count"] - 1180)
        self.assertIn("by_pillar", summary)
        self.assertIn("by_category", summary)
        self.assertIn("by_status", summary)


if __name__ == "__main__":
    unittest.main()
