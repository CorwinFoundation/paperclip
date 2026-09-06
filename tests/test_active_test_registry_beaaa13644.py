"""BEAAA-13644 active-test registry count gate."""
from __future__ import annotations

import copy
import unittest

from tools.active_test_registry import (
    ALLOWED_PILLARS,
    REQUIRED_FIELDS,
    build_registry,
    count_active_executable,
    count_canonical_scored_rows,
    count_target_bound_runtime,
    is_canonical_scored_row,
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

    def test_production_registry_counts_canonical_runtime_identities_once(self) -> None:
        # Train 1 reconciles the previously stale published manifest to the
        # already-established BEAAA-24273 executable floor. Keep the explicit
        # equality pins so either the manifest or runtime inventory drifting
        # independently remains a hard failure.
        self.assertGreaterEqual(count_active_executable(self.rows), load_published_count())
        self.assertEqual(load_published_count(), 2257)
        self.assertEqual(count_active_executable(self.rows), 2257)
        # Twelve BEAAA-21704 cases retain their pre-existing target-bound alias
        # coverage while external evidence remains separate from the canonical
        # runtime-identity evidence gate.
        self.assertEqual(count_target_bound_runtime(self.rows), 2353)
        promoted = [row for row in self.rows if row.get("canonical_runtime_case")]
        aliases = [row for row in self.rows if row.get("alias_of_runtime_case_identity")]
        # The C10 zero-weight train turns 40 previously non-canonical content
        # inventory rows into canonical runtime identities.  The remaining 82
        # activated rows were already canonical runtime cases, so the runtime
        # identity count moves by 40 while executable/target-bound totals stay
        # unchanged.
        self.assertEqual(len(promoted), 2131)
        self.assertEqual(len(aliases), 12)

    def test_summary_reports_count_delta_to_1180(self) -> None:
        summary = summarize(self.rows, published_count=load_published_count())
        self.assertEqual(summary["registry_rows"], len(self.rows))
        self.assertEqual(summary["active_executable_count"], count_active_executable(self.rows))
        self.assertEqual(
            summary["canonical_scored_row_count"],
            count_canonical_scored_rows(self.rows),
        )
        self.assertEqual(
            summary["target_bound_runtime_count"], count_target_bound_runtime(self.rows)
        )
        self.assertEqual(summary["delta_to_1180"], summary["active_executable_count"] - 1180)
        self.assertIn("by_pillar", summary)
        self.assertIn("by_category", summary)
        self.assertIn("by_status", summary)

    def test_canonical_scored_row_count_uses_exact_contract_predicates(self) -> None:
        canonical = next(row for row in self.rows if is_canonical_scored_row(row))
        excluded_values = {
            "status": "quarantined",
            "canonical_runtime_case": False,
            "reporting_status": "reported_not_scored",
            "execution_evidenced": False,
            "target_bound": False,
        }
        for field, value in excluded_values.items():
            excluded = copy.deepcopy(canonical)
            excluded[field] = value
            self.assertFalse(is_canonical_scored_row(excluded), msg=field)
            self.assertEqual(count_canonical_scored_rows([excluded]), 0, msg=field)

        attestation = copy.deepcopy(canonical)
        attestation["case_class"] = "attestation"
        self.assertTrue(is_canonical_scored_row(attestation))


if __name__ == "__main__":
    unittest.main()
