from __future__ import annotations

import json
import os
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
from tools.real_agent_execution_gate import (
    ApprovedAgent, AuthoritativeRealAgentVerifier, load_authoritative_verifier,
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
                "request_sha256": "sha256:abc",
            },
            "raw_response": {
                "status_code": 200,
                "body_sha256": "sha256:def",
            },
        }
        verifier = self._verifier()
        row["authority_signature"] = verifier.sign_for_test(row)
        self.assertTrue(evaluate_real_agent_execution(row, verifier=verifier).countable)
        self.assertFalse(validate_registry([row], real_agent_verifier=verifier))

    @staticmethod
    def _verifier() -> AuthoritativeRealAgentVerifier:
        approval = ApprovedAgent(
            identity="backbond-approved-demo-agent-v1",
            https_origin="https://demo-agent.backbond.ai",
            evidence_hmac_key=b"test-control-plane-key-not-from-row-32-bytes",
        )
        return AuthoritativeRealAgentVerifier({approval.identity: approval})

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
                "request_sha256": "sha256:abc",
            },
            "raw_response": {
                "status_code": 200,
                "body_sha256": "sha256:def",
            },
        }
        verifier = self._verifier()
        base["authority_signature"] = verifier.sign_for_test(base)
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
                # Re-sign benign field changes so each rejection proves the named class.
                if label not in {"localhost", "127.0.0.1"}:
                    row["authority_signature"] = verifier.sign_for_test(row)
                result = evaluate_real_agent_execution(row, verifier=verifier)
                self.assertFalse(result.countable, msg=result)
                self.assertTrue(validate_registry([row], real_agent_verifier=verifier), msg=label)

    def test_beaaa14087_rejects_caller_spoofed_identity_and_hash(self) -> None:
        row = {
            "case_id": "spoof", "target_type": "real_agent",
            "target_identity": "backbond-approved-demo-agent-v1",
            "raw_request": {"url": "https://demo-agent.backbond.ai/invoke", "request_sha256": "sha256:caller"},
            "raw_response": {"body_sha256": "sha256:caller"},
            "authority_signature": "caller-supplied-but-invalid",
        }
        result = evaluate_real_agent_execution(row, verifier=self._verifier())
        self.assertFalse(result.countable)
        self.assertIn("invalid_authority_signature", result.reasons)

    def test_beaaa14087_approval_configuration_is_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            ApprovedAgent("weak", "https://agent.example", b"short")
        with self.assertRaises(ValueError):
            ApprovedAgent("loopback", "https://127.0.0.1", b"x" * 32)
        with self.assertRaises(ValueError):
            ApprovedAgent("private", "https://10.0.0.1", b"x" * 32)

    def test_beaaa14087_malformed_port_rejects_without_raising(self) -> None:
        row = self._authenticated_registry_row(self._verifier(), "bad-port")
        row["raw_request"]["url"] = "https://demo-agent.backbond.ai:bad/invoke"
        result = evaluate_real_agent_execution(row, verifier=self._verifier())
        self.assertFalse(result.countable)
        self.assertIn("request_origin_not_control_plane_approved", result.reasons)

    def test_beaaa14087_unrelated_metadata_markers_do_not_quarantine(self) -> None:
        verifier = self._verifier()
        row = self._authenticated_registry_row(verifier, "benign-metadata")
        row["notes"] = "The documentation discusses replay and echo attacks."
        self.assertTrue(evaluate_real_agent_execution(row, verifier=verifier).countable)

    def test_beaaa14087_agent_count_is_subset_of_general_execution_gate(self) -> None:
        verifier = self._verifier()
        row = self._authenticated_registry_row(verifier, "not-general-executed")
        row["countable_as_executed_test"] = False
        errors = validate_registry([row], real_agent_verifier=verifier)
        self.assertTrue(any("agent-countable but not countable" in error for error in errors), errors)

    def test_beaaa14087_invalid_wave_ledger_is_row_bound(self) -> None:
        from tools.canonical_test_registry import (
            _invalid_wave_quarantined_count, _invalid_wave_ticket_refs,
        )
        rows = [
            {"ticket_ref": "BEAAA-14012", "countable_as_executed_agent_test": False},
            {"ticket_ref": "BEAAA-99999", "countable_as_executed_agent_test": False},
        ]
        self.assertEqual(_invalid_wave_quarantined_count(rows), 1)
        self.assertEqual(_invalid_wave_ticket_refs(rows), {"BEAAA-14012"})

    def test_beaaa14087_production_verifier_loads_server_owned_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "approvals.json"
            path.write_text(json.dumps({"approved_agents": [{
                "identity": "backbond-approved-demo-agent-v1",
                "https_origin": "https://demo-agent.backbond.ai",
                "hmac_key_env": "BEAAA14087_TEST_HMAC_KEY",
            }]}), encoding="utf-8")
            prior = os.environ.get("BEAAA14087_TEST_HMAC_KEY")
            os.environ["BEAAA14087_TEST_HMAC_KEY"] = "x" * 32
            try:
                verifier = load_authoritative_verifier(path)
            finally:
                if prior is None:
                    os.environ.pop("BEAAA14087_TEST_HMAC_KEY", None)
                else:
                    os.environ["BEAAA14087_TEST_HMAC_KEY"] = prior
        row = self._authenticated_registry_row(verifier, "production-compose")
        self.assertTrue(evaluate_real_agent_execution(row, verifier=verifier).countable)

    def test_beaaa14087_absent_manifest_and_verifier_fail_closed_to_zero(self) -> None:
        registry = build_registry(REPO)
        self.assertEqual(registry["metadata"]["executed_agent_test_count"], 0)

    def test_beaaa14087_authenticated_manifest_counts_deduplicated_rows_only(self) -> None:
        verifier = self._verifier()
        row = self._authenticated_registry_row(verifier, "authenticated-case")
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "evidence.json"
            manifest.write_text(json.dumps({"rows": [row]}), encoding="utf-8")
            registry = build_registry(
                REPO, real_agent_verifier=verifier,
                real_agent_evidence_manifest=manifest,
            )
        self.assertEqual(registry["metadata"]["executed_agent_test_count"], 1)

    def _authenticated_registry_row(self, verifier, case_id: str) -> dict:
        row = {
            "asset_id": f"real-{case_id}", "case_id": case_id,
            "disposition": "production_active", "rubric_point_id": "P1.1",
            "source": "evidence/real-agent.json", "framework": "real_agent_runner",
            "version_pin": "real-agent-runner@1.0", "countable_as_active_executable": True,
            "countable_as_executed_test": True, "countable_as_executed_agent_test": True,
            "evidence_uri_template": "evidence://{scan_id}/real-agent/{case_id}.json",
            "prompt_or_action": "bounded action", "expected_behavior": "safe response",
            "target_response": "safe response", "verdict": "pass",
            "verdict_provenance": "deterministic comparator v1",
            "manifest_evidence": "manifest://authenticated", "github_provenance": "github://repo@sha",
            "target_type": "real_agent", "target_identity": "backbond-approved-demo-agent-v1",
            "raw_request": {"url": "https://demo-agent.backbond.ai/invoke", "request_sha256": "sha256:a"},
            "raw_response": {"body_sha256": "sha256:b"},
        }
        row["authority_signature"] = verifier.sign_for_test(row)
        return row

    def test_beaaa14087_registry_quarantines_invalid_wave_rows_from_executed_agent_count(self) -> None:
        metadata = self.registry["metadata"]
        self.assertEqual(metadata["executed_agent_test_count"], 0)
        self.assertEqual(metadata["invalid_wave_quarantined_ticket_refs"], [])
        self.assertEqual(metadata["invalid_wave_quarantined_additional_count"], 0)
        self.assertEqual(
            metadata["real_agent_execution_gate"],
            [
                "target_type_real_agent",
                "target_identity_control_plane_approved",
                "raw_request_present",
                "raw_response_present",
                "request_origin_control_plane_approved",
                "authority_signed_request_response_digest_envelope",
                "no_localhost_mock_fixture_replay_or_terminal_only_assertion",
            ],
        )

    def test_beaaa13992_disqualifies_non_evidenced_embedded_cases_at_case_level(self) -> None:
        ledger = self.registry.get("disqualification_ledger", [])
        self.assertEqual(len(ledger), 743)
        self.assertEqual(self.registry["metadata"]["appendix_disqualified_embedded_case_count"], 743)
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
