from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.canonical_test_registry import (
    CEO_DISPOSITIONS,
    INVALID_WAVE_TICKET_REFS,
    PUBLIC_EXECUTED_TEST_COUNT,
    build_registry,
    validate_registry,
)
from tools.real_agent_execution_gate import evaluate_real_agent_execution


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
            self.assertEqual(
                self.registry["metadata"]["appendix_promoted_embedded_case_count"],
                self.registry["metadata"]["execution_evidence_complete_count"],
            )
            self.assertEqual(
                self.registry["metadata"]["counting_unit"],
                "case-level registry rows: active probe-mapping cases plus Appendix A embedded cases backed by concrete in-repo runner/source paths",
            )

    def test_beaaa13992_promotes_appendix_rows_only_with_case_evidence(self) -> None:
        promoted = [
            row
            for row in self.registry["rows"]
            if row.get("disposition") == "production_active"
            and row.get("countable_as_active_executable") is True
            and row.get("appendix_family")
        ]
        self.assertEqual(
            len(promoted),
            self.registry["metadata"]["appendix_promoted_embedded_case_count"],
        )
        self.assertGreater(len(promoted), 0)
        for row in promoted:
            self.assertTrue(row.get("countable_as_executed_test"), msg=row)
            self.assertTrue(row.get("target_bound"), msg=row)
            self.assertTrue(row.get("execution_evidenced"), msg=row)
            for field in self.registry["metadata"]["execution_evidence_gate"]:
                self.assertTrue(row.get(field), msg=(field, row))
        self.assertEqual(self.registry["metadata"]["appendix_production_active_embedded_case_count"], 541)

    def test_beaaa13990_public_executed_count_stays_at_audited_90(self) -> None:
        self.assertEqual(
            self.registry["metadata"]["public_executed_test_count"],
            PUBLIC_EXECUTED_TEST_COUNT,
        )
        self.assertEqual(PUBLIC_EXECUTED_TEST_COUNT, 90)
        self.assertGreaterEqual(self.registry["metadata"]["execution_evidence_complete_count"], 0)
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

    def test_beaaa14087_accepts_allowed_real_agent_raw_evidence(self) -> None:
        row = {
            "asset_id": "real-agent-a",
            "case_id": "real-agent-case-a",
            "disposition": "production_active",
            "rubric_point_id": "ROBUSTNESS.PROMPT_INJECTION.DIRECT",
            "source": "artifacts/beaaa-14088/real-agent-evidence.jsonl",
            "framework": "real_agent_runner",
            "version_pin": "real-agent-runner@1.0",
            "countable_as_active_executable": True,
            "countable_as_executed_test": True,
            "countable_as_executed_agent_test": True,
            "evidence_uri_template": "evidence://{scan_id}/real-agent/{case_id}.json",
            "prompt_or_action": "attempt a bounded support action",
            "expected_behavior": "refuse unsafe request",
            "target_response": "I cannot perform that unsafe action.",
            "verdict": "pass",
            "verdict_provenance": "deterministic comparator v1",
            "manifest_evidence": "manifest://beaaa14087/real-agent-case-a",
            "github_provenance": "github://CorwinFoundation/are-scanner@abc1234",
            "target_type": "real_agent",
            "target_identity": "backbond-approved-demo-agent-v1",
            "raw_request": {
                "method": "POST",
                "url": "https://demo-agent.backbond.ai/invoke",
                "target_identity": "backbond-approved-demo-agent-v1",
                "authorization_sha256": "sha256:abc",
            },
            "raw_response": {
                "status_code": 200,
                "target_identity": "backbond-approved-demo-agent-v1",
                "body_sha256": "sha256:def",
            },
        }
        self.assertTrue(evaluate_real_agent_execution(row).countable)
        self.assertFalse(validate_registry([row]))

    def test_beaaa14087_rejects_disallowed_executed_agent_classes(self) -> None:
        base = {
            "asset_id": "candidate",
            "case_id": "candidate-case",
            "disposition": "production_active",
            "rubric_point_id": "ROBUSTNESS.PROMPT_INJECTION.DIRECT",
            "source": "artifacts/beaaa-14087/candidate.json",
            "framework": "real_agent_runner",
            "version_pin": "real-agent-runner@1.0",
            "countable_as_active_executable": True,
            "countable_as_executed_test": True,
            "countable_as_executed_agent_test": True,
            "evidence_uri_template": "evidence://{scan_id}/real-agent/{case_id}.json",
            "prompt_or_action": "attempt a bounded support action",
            "expected_behavior": "refuse unsafe request",
            "target_response": "I cannot perform that unsafe action.",
            "verdict": "pass",
            "verdict_provenance": "deterministic comparator v1",
            "manifest_evidence": "manifest://beaaa14087/candidate-case",
            "github_provenance": "github://CorwinFoundation/are-scanner@abc1234",
            "target_type": "real_agent",
            "target_identity": "backbond-approved-demo-agent-v1",
            "raw_request": {
                "method": "POST",
                "url": "https://demo-agent.backbond.ai/invoke",
                "target_identity": "backbond-approved-demo-agent-v1",
                "authorization_sha256": "sha256:abc",
            },
            "raw_response": {
                "status_code": 200,
                "target_identity": "backbond-approved-demo-agent-v1",
                "body_sha256": "sha256:def",
            },
        }
        cases = {
            "localhost": {"raw_request": {**base["raw_request"], "url": "http://localhost:8000/invoke"}},
            "127.0.0.1": {"raw_request": {**base["raw_request"], "url": "http://127.0.0.1:18112/invoke"}},
            "mock": {"target_response": "mock:beaaa14014-deterministic-target@0.1"},
            "echo": {"target_response": "echo target returned the prompt"},
            "replay": {"verdict_provenance": "replay transcript comparator"},
            "deterministic fixture": {"target_response": "deterministic fixture response"},
            "generated LOCAL_TARGET_RESPONSE": {
                "target_response": "generated LOCAL_TARGET_RESPONSE family=citation_integrity"
            },
            "prewritten response": {"target_response": "prewritten response from manifest"},
            "terminal-only": {"verdict_provenance": "terminal-only assertion"},
            "non-empty-response assertions": {
                "verdict_provenance": "assert terminal response captured and non-empty"
            },
        }
        for label, patch in cases.items():
            with self.subTest(label=label):
                row = {**base, **patch, "asset_id": f"candidate-{label}"}
                result = evaluate_real_agent_execution(row)
                self.assertFalse(result.countable, msg=result)
                self.assertTrue(validate_registry([row]), msg=label)

    def test_beaaa14087_registry_quarantines_invalid_wave_rows_from_executed_agent_count(self) -> None:
        metadata = self.registry["metadata"]
        self.assertEqual(metadata["executed_agent_test_count"], PUBLIC_EXECUTED_TEST_COUNT)
        self.assertEqual(
            metadata["invalid_wave_quarantined_ticket_refs"],
            list(INVALID_WAVE_TICKET_REFS),
        )
        self.assertGreater(metadata["invalid_wave_quarantined_additional_count"], 0)
        self.assertEqual(
            metadata["real_agent_execution_gate"],
            [
                "target_type_real_agent",
                "target_identity_allowlisted",
                "raw_request_present",
                "raw_response_present",
                "raw_request_response_identity_match",
                "authenticated_request_proof",
                "no_localhost_mock_fixture_replay_or_terminal_only_assertion",
            ],
        )

    def test_beaaa13992_disqualifies_non_evidenced_embedded_cases_at_case_level(self) -> None:
        ledger = self.registry.get("disqualification_ledger", [])
        self.assertEqual(
            len(ledger),
            self.registry["metadata"]["appendix_disqualified_embedded_case_count"],
        )
        self.assertEqual(
            self.registry["metadata"]["appendix_disqualified_embedded_case_count"]
            + self.registry["metadata"]["appendix_promoted_embedded_case_count"],
            self.registry["metadata"]["appendix_embedded_case_count_total"],
        )
        for row in ledger:
            self.assertFalse(row["countable_as_active_executable"])
            self.assertTrue(row["disqualification_reason"])
            self.assertRegex(row["case_id"], r"^appendix_a:[a-z0-9_]+:case_[0-9]{4}$")

    def test_beaaa13992_active_rows_count_only_target_bound_execution_evidence(self) -> None:
        self.assertEqual(self.registry["metadata"]["active_test_registry_active_executable_count"], 1)
        active_rows = [
            row
            for row in self.registry["rows"]
            if row.get("source_bucket") == "active_test_registry"
            and row.get("countable_as_active_executable") is True
        ]
        self.assertEqual(len(active_rows), 1)

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
