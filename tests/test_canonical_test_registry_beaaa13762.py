from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.canonical_test_registry import (
    CEO_DISPOSITIONS,
    PUBLIC_EXECUTED_TEST_COUNT,
    build_registry,
    validate_registry,
)


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
        self.assertGreater(len(active_case_ids), 0)
        if self.registry["metadata"]["source_mode"] == "appendix_a_case_level_with_active_registry":
            self.assertEqual(self.registry["metadata"]["appendix_family_count"], 61)
            self.assertEqual(self.registry["metadata"]["appendix_t1_embedded_case_count"], 715)
            self.assertEqual(self.registry["metadata"]["appendix_promoted_embedded_case_count"], 0)
            self.assertEqual(
                self.registry["metadata"]["counting_unit"],
                "case-level registry rows: active probe-mapping cases plus Appendix A embedded cases backed by concrete in-repo runner/source paths",
            )

    def test_beaaa13992_does_not_promote_appendix_rows_without_case_evidence(self) -> None:
        promoted = [
            row
            for row in self.registry["rows"]
            if row.get("disposition") == "production_active"
            and row.get("countable_as_active_executable") is True
            and row.get("appendix_family")
        ]
        self.assertEqual(len(promoted), 0)
        self.assertEqual(self.registry["metadata"]["appendix_promoted_embedded_case_count"], 0)
        self.assertEqual(self.registry["metadata"]["appendix_production_active_embedded_case_count"], 429)

    def test_beaaa13990_public_executed_count_stays_at_audited_90(self) -> None:
        self.assertEqual(
            self.registry["metadata"]["public_executed_test_count"],
            PUBLIC_EXECUTED_TEST_COUNT,
        )
        self.assertEqual(PUBLIC_EXECUTED_TEST_COUNT, 90)
        self.assertEqual(self.registry["metadata"]["execution_evidence_complete_count"], 0)
        self.assertEqual(
            self.registry["metadata"]["execution_evidence_gate"],
            [
                "expected_behavior",
                "github_provenance",
                "manifest_evidence",
                "prompt_or_action",
                "target_response",
                "verdict",
                "verdict_provenance",
            ],
        )

    def test_beaaa13990_rejects_counted_executed_case_missing_execution_evidence(self) -> None:
        row = {
            "asset_id": "executed-a",
            "case_id": "executed-case-a",
            "disposition": "production_active",
            "rubric_point_id": "P1.1",
            "source": "tests/fixtures/executed.json",
            "framework": "inspect",
            "version_pin": "inspect@0.3.220",
            "countable_as_active_executable": True,
            "countable_as_executed_test": True,
            "evidence_uri_template": "evidence://{scan_id}/executed/{case_id}.json",
            "prompt_or_action": "attempt a bounded support-answer action",
            "expected_behavior": "refuse unsafe instruction and cite allowed source",
            "target_response": "refused unsafe instruction",
            "verdict": "pass",
            "verdict_provenance": "deterministic comparator v1",
            "manifest_evidence": "manifest://beaaa13990/executed-case-a",
        }
        errors = validate_registry([row])
        self.assertTrue(
            any("missing execution evidence: github_provenance" in error for error in errors),
            errors,
        )

    def test_beaaa13990_accepts_counted_executed_case_with_complete_execution_evidence(self) -> None:
        row = {
            "asset_id": "executed-b",
            "case_id": "executed-case-b",
            "disposition": "production_active",
            "rubric_point_id": "P1.1",
            "source": "tests/fixtures/executed.json",
            "framework": "inspect",
            "version_pin": "inspect@0.3.220",
            "countable_as_active_executable": True,
            "countable_as_executed_test": True,
            "evidence_uri_template": "evidence://{scan_id}/executed/{case_id}.json",
            "prompt_or_action": "attempt a bounded support-answer action",
            "expected_behavior": "refuse unsafe instruction and cite allowed source",
            "target_response": "refused unsafe instruction",
            "verdict": "pass",
            "verdict_provenance": "deterministic comparator v1",
            "manifest_evidence": "manifest://beaaa13990/executed-case-b",
            "github_provenance": "github://CorwinFoundation/are-scanner@34687b5",
        }
        self.assertFalse(validate_registry([row]))

    def test_beaaa13992_disqualifies_non_evidenced_embedded_cases_at_case_level(self) -> None:
        ledger = self.registry.get("disqualification_ledger", [])
        self.assertEqual(len(ledger), 743)
        self.assertEqual(self.registry["metadata"]["appendix_disqualified_embedded_case_count"], 743)
        for row in ledger:
            self.assertFalse(row["countable_as_active_executable"])
            self.assertTrue(row["disqualification_reason"])
            self.assertRegex(row["case_id"], r"^appendix_a:[a-z0-9_]+:case_[0-9]{4}$")

    def test_beaaa13992_active_rows_count_only_target_bound_execution_evidence(self) -> None:
        # BEAAA-16775 promotes the production registry to 304 customer-target
        # runtime identities plus one governance attestation.
        self.assertEqual(self.registry["metadata"]["active_test_registry_active_executable_count"], 305)
        active_rows = [
            row
            for row in self.registry["rows"]
            if row.get("source_bucket") == "active_test_registry"
            and row.get("countable_as_active_executable") is True
        ]
        self.assertEqual(len(active_rows), 305)

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
            "evidence_uri_template": "evidence://{scan_id}/fixture/{case_id}.json",
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
