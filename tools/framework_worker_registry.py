#!/usr/bin/env python3
"""Framework-worker ownership and pin gate for BEAAA-13682.

This gate is metadata-only. It maps active BackBond Scanner registry rows to
owned worker definitions, verifies their version/corpus/seed/evidence pins, and
emits release blockers for unavailable workers. It does not execute probes or
change score math.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.active_test_registry import (
    REPO_ROOT,
    _is_countable_active_executable,
    build_registry,
)


FRAMEWORK_WORKER_MANIFEST_VERSION = "framework-worker-ownership/v0.1"
CONTRACT_VERSION = "are-scanner-api-v1@v1.0"
OWNER = "scanner_engineer_2_eval_calibration"
REQUIRED_WORKER_FIELDS = {
    "worker_id",
    "owner",
    "framework_version",
    "dependency_pins",
    "corpus_hash",
    "seed_policy",
    "evidence_output_schema",
}
REQUIRED_EVIDENCE_SCHEMA_FIELDS = {
    "schema_id",
    "required_fields",
    "artifact_uri_template",
}
UNPINNED_VALUES = {"", "latest", "floating", "unpinned", "unknown", "n/a"}


@dataclass(frozen=True)
class WorkerDefinition:
    worker_id: str
    owner: str
    framework_version: str
    dependency_pins: dict[str, str]
    corpus_sources: tuple[str, ...]
    seed_policy: str
    evidence_output_schema: dict[str, Any]
    measured_score_allowed: bool = True
    reported_not_scored_only: bool = False


def _evidence_schema(schema_id: str) -> dict[str, Any]:
    return {
        "schema_id": schema_id,
        "required_fields": [
            "scan_id",
            "test_id",
            "case_id",
            "rubric_point_id",
            "verdict",
            "artifact_uri",
            "seed",
            "framework_worker_id",
            "framework_version",
            "corpus_hash",
        ],
        "artifact_uri_template": "evidence://{scan_id}/{test_id}/{case_id}.json",
    }


WORKERS: dict[str, WorkerDefinition] = {
    "promptfoo": WorkerDefinition(
        worker_id="promptfoo",
        owner=OWNER,
        framework_version="0.115.0",
        dependency_pins={"promptfoo": "0.115.0"},
        corpus_sources=(
            "requirements.txt",
            "rubric/probe_mappings/advbench.yaml",
            "rubric/probe_mappings/factscore.yaml",
            "rubric/probe_mappings/halueval.yaml",
            "rubric/probe_mappings/harmbench.yaml",
            "rubric/probe_mappings/jailbreakbench.yaml",
            "rubric/probe_mappings/privacybench.yaml",
            "rubric/probe_mappings/promptbench.yaml",
            "rubric/probe_mappings/promptinject.yaml",
            "rubric/probe_mappings/truthfulqa.yaml",
        ),
        seed_policy="seeded deterministic prompt rendering; judge temperature 0 where applicable",
        evidence_output_schema=_evidence_schema("promptfoo-evidence/v1"),
    ),
    "content_candidate": WorkerDefinition(
        worker_id="content_candidate",
        owner=OWNER,
        framework_version="beaaa-18720-1.0.0",
        dependency_pins={
            "inventory_sha256": "fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988"
        },
        corpus_sources=(
            "artifacts/beaaa-18720/unified-probe-inventory.jsonl",
            "artifacts/beaaa-18720/reconciliation-validation.json",
        ),
        seed_policy="pinned candidate prompts; sequential turn order; judge temperature 0",
        evidence_output_schema=_evidence_schema("content-candidate-evidence/v1"),
        measured_score_allowed=False,
        reported_not_scored_only=True,
    ),
    "garak": WorkerDefinition(
        worker_id="garak",
        owner=OWNER,
        framework_version="0.10.0",
        dependency_pins={"garak": "0.10.0"},
        corpus_sources=(
            "requirements.txt",
            "rubric/probe_mappings/garak.yaml",
            "rubric/probe_mappings/garak_beaaa152.yaml",
        ),
        seed_policy="seed fixed by BackBond Scanner scan seed and garak probe namespace pin",
        evidence_output_schema=_evidence_schema("garak-evidence/v1"),
    ),
    "inspect": WorkerDefinition(
        worker_id="inspect",
        owner=OWNER,
        framework_version="0.3.220",
        dependency_pins={"inspect-ai": "0.3.220"},
        corpus_sources=(
            "requirements.txt",
            "scanners/inspect_runner.py",
            "scanners/wraps/inspect_section1_v0.py",
            "rubric/probe_mappings/citation_integrity_legal.yaml",
            "rubric/probe_mappings/confidence_calibration.yaml",
            "rubric/probe_mappings/hallucination_legal.yaml",
            "rubric/probe_mappings/p1_1_factuality_golden.yaml",
            "rubric/probe_mappings/p1_3_consistency_golden.yaml",
            "rubric/probe_mappings/p1_4_refusal_golden.yaml",
            "rubric/probe_mappings/p1_5_calibration_golden.yaml",
        ),
        seed_policy="seed fixed by ScanRequest.seed; repeatability verified by N=5 release gate",
        evidence_output_schema=_evidence_schema("inspect-evidence/v1"),
    ),
    "pyrit": WorkerDefinition(
        worker_id="pyrit",
        owner=OWNER,
        framework_version="0.6.0",
        dependency_pins={"pyrit": "0.6.0"},
        corpus_sources=(
            "requirements.txt",
            "scanners/pyrit_runner.py",
            "rubric/probe_mappings/pyrit.yaml",
        ),
        seed_policy="seed fixed by BackBond Scanner scan seed; PyRIT attack generator version pinned",
        evidence_output_schema=_evidence_schema("pyrit-evidence/v1"),
    ),
    "agentdojo": WorkerDefinition(
        worker_id="agentdojo",
        owner=OWNER,
        framework_version="0.1.35",
        dependency_pins={"agentdojo": "0.1.35"},
        corpus_sources=(
            "requirements.txt",
            "scanners/agentdojo_runner.py",
            "rubric/probe_mappings/agentdojo.yaml",
            "rubric/probe_mappings/tool_misuse_param_tamper.yaml",
            "rubric/probe_mappings/tool_misuse_scope_escape.yaml",
            "rubric/probe_mappings/tool_misuse_unauthorized_call.yaml",
        ),
        seed_policy="seed fixed by ScanRequest.seed; tool-call fixtures replay deterministically",
        evidence_output_schema=_evidence_schema("agentdojo-evidence/v1"),
    ),
    "ailuminate": WorkerDefinition(
        worker_id="ailuminate",
        owner=OWNER,
        framework_version="corpus-provenance-pin",
        dependency_pins={"harness/bench/ailuminate.py": "source-provenance"},
        corpus_sources=("harness/bench/ailuminate.py",),
        seed_policy="seed fixed by corpus row id and ScanRequest.seed; no external package pin exists",
        evidence_output_schema=_evidence_schema("ailuminate-evidence/v1"),
    ),
    "fairness_custom": WorkerDefinition(
        worker_id="fairness_custom",
        owner=OWNER,
        framework_version="bbq-donotanswer-custom-fairness@0.1",
        dependency_pins={"PyYAML": "6.0.2"},
        corpus_sources=(
            "requirements.txt",
            "rubric/probe_mappings/bbq.yaml",
            "rubric/probe_mappings/counterfactual_stability.yaml",
            "rubric/probe_mappings/demographic_parity.yaml",
            "rubric/probe_mappings/donotanswer.yaml",
            "rubric/probe_mappings/equal_opportunity.yaml",
            "rubric/probe_mappings/eval_representation.yaml",
            "rubric/probe_mappings/p2_1_demographic_parity_gap.yaml",
            "rubric/probe_mappings/p2_2_equal_opportunity_gap.yaml",
            "rubric/probe_mappings/p2_3_counterfactual_stability.yaml",
            "rubric/probe_mappings/p2_4_eval_representation.yaml",
            "rubric/probe_mappings/p2_5_sentiment_skew_by_attribute.yaml",
            "rubric/probe_mappings/p2_6_mitigation_evidence.yaml",
            "rubric/probe_mappings/sentiment_skew.yaml",
        ),
        seed_policy="seed fixed by pair/case id and ScanRequest.seed; judge temperature 0 where used",
        evidence_output_schema=_evidence_schema("fairness-custom-evidence/v1"),
    ),
    "llm_guard_presidio": WorkerDefinition(
        worker_id="llm_guard_presidio",
        owner=OWNER,
        framework_version="llm-guard@0.3.16+presidio@2.2.358",
        dependency_pins={
            "llm-guard": "0.3.16",
            "presidio-analyzer": "2.2.358",
            "presidio-anonymizer": "2.2.358",
            "spacy": "3.8.14",
            "transformers": "4.51.3",
        },
        corpus_sources=(
            "requirements.txt",
            "rubric/probe_mappings/pii_attestation.yaml",
            "rubric/probe_mappings/pii_scan_time_detection.yaml",
            "rubric/probe_mappings/pii_substrate.yaml",
            "harness/bench/pii_scan_time_detection.py",
        ),
        seed_policy="detector thresholds pinned; scan seed stamped into evidence envelope",
        evidence_output_schema=_evidence_schema("llm-guard-presidio-evidence/v1"),
    ),
    "governance_attestation": WorkerDefinition(
        worker_id="governance_attestation",
        owner=OWNER,
        framework_version="backbond-governance-attestation@0.1",
        dependency_pins={"PyYAML": "6.0.2"},
        corpus_sources=(
            "requirements.txt",
            "rubric/probe_mappings/eval_coverage_breadth.yaml",
            "rubric/probe_mappings/eval_coverage_ledger.schema.yaml",
            "rubric/probe_mappings/mitigation_evidence.yaml",
            "rubric/probe_mappings/p4_audit_log.yaml",
            "rubric/probe_mappings/p4_eval_cadence.yaml",
            "rubric/probe_mappings/p4_incident_response.yaml",
            "rubric/probe_mappings/p4_kill_switch.yaml",
            "rubric/probe_mappings/p4_owner_matrix.yaml",
            "rubric/probe_mappings/p4_version_control.yaml",
        ),
        seed_policy="attestation evidence is deterministic; ScanRequest.seed is retained for envelope parity",
        evidence_output_schema=_evidence_schema("governance-attestation-evidence/v1"),
    ),
    "tau_bench": WorkerDefinition(
        worker_id="tau_bench",
        owner=OWNER,
        framework_version="59a200c6d575d595120f1cb70fea53cef0632f6b",
        dependency_pins={
            "tau-bench": "59a200c6d575d595120f1cb70fea53cef0632f6b",
            "tau2-bench": "5ebebbe827b455b3ed04fcb9294235c6ef4e5fd6",
        },
        corpus_sources=("requirements/eval_pins.txt",),
        seed_policy="seed fixed by benchmark task id and ScanRequest.seed",
        evidence_output_schema=_evidence_schema("tau-bench-evidence/v1"),
    ),
}


PROMPTFOO_TEST_IDS = {
    "advbench",
    "factscore",
    "halueval",
    "harmbench",
    "jailbreakbench",
    "privacybench",
    "promptbench",
    "promptinject",
    "truthfulqa",
    "p3_5_multiturn_jailbreak",
    "p3_6_output_attack",
    "secret_exfil",
    "indirect_injection",
    "p3_2_indirect_injection_rob",
    "p3_3_secret_exfiltration_rob",
    "hallusquatting-promptware-fixtures",
}
INSPECT_TEST_IDS = {
    "citation_integrity_legal",
    "confidence_calibration",
    "hallucination_legal",
    "p1_1_factuality_golden",
    "p1_3_consistency_golden",
    "p1_4_refusal_golden",
    "p1_5_calibration_golden",
    "self_contradiction_pairs",
    "perturbation_seeds",
}
FAIRNESS_TEST_IDS = {
    "bbq",
    "counterfactual_stability",
    "demographic_parity",
    "donotanswer",
    "equal_opportunity",
    "eval_representation",
    "p2_1_demographic_parity_gap",
    "p2_2_equal_opportunity_gap",
    "p2_3_counterfactual_stability",
    "p2_4_eval_representation",
    "p2_5_sentiment_skew_by_attribute",
    "p2_6_mitigation_evidence",
    "sentiment_skew",
}
GOVERNANCE_TEST_IDS = {
    "eval_coverage_breadth",
    "eval_coverage_ledger",
    "mitigation_evidence",
    "p4_audit_log",
    "p4_eval_cadence",
    "p4_incident_response",
    "p4_kill_switch",
    "p4_owner_matrix",
    "p4_version_control",
}
PII_TEST_IDS = {"pii_attestation", "pii_scan_time_detection", "pii_substrate"}
AGENTDOJO_TEST_IDS = {
    "agentdojo",
    "tool_misuse_param_tamper",
    "tool_misuse_scope_escape",
    "tool_misuse_unauthorized_call",
}


def worker_id_for_registry_row(row: dict[str, Any]) -> str:
    test_id = str(row.get("test_id") or "")
    runner = str(row.get("runner") or "")
    if runner == "content_candidate_worker":
        return "content_candidate"
    if test_id in AGENTDOJO_TEST_IDS or runner == "live_probe_harness":
        return "agentdojo"
    if test_id.startswith("garak") or runner == "garak_wrapper":
        return "garak"
    if test_id == "pyrit":
        return "pyrit"
    if test_id == "ailuminate":
        return "ailuminate"
    if test_id in PII_TEST_IDS:
        return "llm_guard_presidio"
    if test_id in FAIRNESS_TEST_IDS:
        return "fairness_custom"
    if test_id in GOVERNANCE_TEST_IDS or row.get("case_class") == "attestation":
        return "governance_attestation"
    if test_id in INSPECT_TEST_IDS:
        return "inspect"
    if test_id in PROMPTFOO_TEST_IDS:
        return "promptfoo"
    return "promptfoo"


def build_worker_manifest(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = rows if rows is not None else build_registry()
    active_rows = [
        row
        for row in rows
        if _is_countable_active_executable(row)
    ]
    worker_usage = Counter(worker_id_for_registry_row(row) for row in active_rows)
    workers = []
    for worker_id in sorted(set(WORKERS) | set(worker_usage)):
        worker = WORKERS.get(worker_id)
        if worker is None:
            workers.append(
                {
                    "worker_id": worker_id,
                    "owner": "",
                    "framework_version": "unpinned",
                    "dependency_pins": {},
                    "corpus_sources": [],
                    "corpus_hash": "",
                    "seed_policy": "",
                    "evidence_output_schema": {},
                    "active_registry_rows": worker_usage[worker_id],
                    "measured_score_allowed": False,
                    "release_blocker": True,
                    "blocker_reason": "worker definition missing",
                }
            )
            continue

        worker_dict = asdict(worker)
        worker_dict["corpus_hash"] = corpus_hash(worker.corpus_sources)
        worker_dict["active_registry_rows"] = worker_usage[worker_id]
        errors = validate_worker(worker_dict)
        worker_dict["release_blocker"] = bool(errors)
        worker_dict["blocker_reason"] = "; ".join(errors)
        worker_dict["measured_score_allowed"] = worker.measured_score_allowed and not errors
        if worker_id == "content_candidate":
            from tools.tranche1_observational import ALLOWLIST, ENVELOPE_VERSION

            worker_dict["measured_score_family_allowlist"] = dict(ALLOWLIST)
            worker_dict["row_bound_measured_contract"] = ENVELOPE_VERSION
        workers.append(worker_dict)

    blocker_count = sum(1 for worker in workers if worker["release_blocker"])
    return {
        "artifact_metadata": {
            "artifact_type": "other",
            "owner": "scanner_engineer",
            "persona_audience": "internal",
            "phase": 1,
            "canonical_terms_only": True,
            "focus_claim": (
                "BEAAA-13682 preserves BackBond Scanner pricing discipline by blocking "
                "unpinned framework workers from measured score contribution."
            ),
            "contracts_referenced": ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"],
            "ticket_refs": ["BEAAA-13682", "BEAAA-13678", "BEAAA-13644"],
            "proof_points_cited": ["tools/active_test_registry.py", "requirements.txt", "requirements/eval_pins.txt"],
            "version": 0.1,
            "prior_version": None,
            "self_audit_passed": True,
            "escalations_noted": [],
        },
        "manifest_version": FRAMEWORK_WORKER_MANIFEST_VERSION,
        "contract_version": CONTRACT_VERSION,
        "active_registry_rows": len(active_rows),
        "workers": workers,
        "release_blocker_count": blocker_count,
        "release_blockers": [
            {
                "worker_id": worker["worker_id"],
                "reason": worker["blocker_reason"],
                "active_registry_rows": worker["active_registry_rows"],
            }
            for worker in workers
            if worker["release_blocker"]
        ],
    }


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    workers = manifest.get("workers")
    if not isinstance(workers, list) or not workers:
        return ["manifest has no workers"]
    active_worker_seen = False
    for worker in workers:
        worker_id = worker.get("worker_id") or "<missing-worker-id>"
        worker_errors = validate_worker(worker)
        if worker_errors:
            errors.extend(f"{worker_id}: {error}" for error in worker_errors)
        if worker.get("active_registry_rows", 0) > 0:
            active_worker_seen = True
            if worker.get("release_blocker"):
                errors.append(f"{worker_id}: active worker is release-blocked")
            if (
                worker.get("measured_score_allowed") is not True
                and worker.get("reported_not_scored_only") is not True
            ):
                errors.append(f"{worker_id}: active worker cannot emit measured score")
    if not active_worker_seen:
        errors.append("manifest has no active workers")
    return errors


def validate_worker(worker: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = sorted(field for field in REQUIRED_WORKER_FIELDS if not worker.get(field))
    if missing:
        errors.append("missing required metadata: " + ", ".join(missing))

    framework_version = str(worker.get("framework_version") or "").lower()
    if framework_version in UNPINNED_VALUES:
        errors.append("framework_version is unpinned")

    dependency_pins = worker.get("dependency_pins") or {}
    if not isinstance(dependency_pins, dict) or not dependency_pins:
        errors.append("dependency_pins must be a non-empty object")
    else:
        for name, version in dependency_pins.items():
            if not name or str(version).lower() in UNPINNED_VALUES:
                errors.append(f"dependency pin is unpinned: {name}")

    evidence_schema = worker.get("evidence_output_schema") or {}
    if not isinstance(evidence_schema, dict):
        errors.append("evidence_output_schema must be an object")
    else:
        schema_missing = sorted(
            field for field in REQUIRED_EVIDENCE_SCHEMA_FIELDS if not evidence_schema.get(field)
        )
        if schema_missing:
            errors.append("evidence_output_schema missing: " + ", ".join(schema_missing))
    return errors


def corpus_hash(source_paths: tuple[str, ...] | list[str]) -> str:
    digest = hashlib.sha256()
    for rel_path in sorted(source_paths):
        path = REPO_ROOT / rel_path
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        if not path.exists():
            digest.update(b"MISSING")
            continue
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, help="write worker manifest JSON")
    parser.add_argument("--check", action="store_true", help="fail on worker pin violations")
    args = parser.parse_args()

    manifest = build_worker_manifest()
    errors = validate_manifest(manifest)
    if errors:
        manifest["errors"] = errors
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    print(json.dumps(manifest, indent=2, sort_keys=True))
    if args.check and errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
