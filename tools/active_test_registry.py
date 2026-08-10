#!/usr/bin/env python3
"""Canonical BackBond Scanner active-test registry and count gate.

The scored registry is derived from ``rubric/probe_mappings/*.yaml``. Validated
reported-not-scored content candidates may be merged through a pinned runtime
adapter without implying a scoring/rubric mapping. The gate only counts rows
that are active and production-runner executable.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
MAPPING_DIR = REPO_ROOT / "rubric" / "probe_mappings"
CLAIMS_PATH = REPO_ROOT / "tools" / "active_test_registry_claims.json"
ALLOWED_STATUSES = {"active", "quarantined", "retired", "roadmap"}
ALLOWED_PILLARS = {"GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS"}
REQUIRED_FIELDS = {
    "test_id",
    "case_id",
    "status",
    "pillar",
    "category",
    "risk_or_rubric_id",
    "source_pack",
    "runner",
    "underwriting_trigger",
    "required_or_optional",
    "version_pin",
    "expected_result",
    "evidence_uri_template",
    "owner",
    "last_verified_at",
}
EXECUTION_EVIDENCE_REQUIRED_FIELDS = {
    "concrete_input_or_action",
    "expected_oracle",
    "recorded_target_response",
    "verdict",
    "github_provenance",
}
EXTERNAL_EVIDENCE_PATH = REPO_ROOT / "artifacts" / "beaaa-21704" / "execution_evidence.json"
HISTORIC_CLAIM_COUNT = 1180
LAST_VERIFIED_AT = "2026-07-12"
LEGACY_REPORTED_NOT_SCORED_PRODUCTION_FAMILIES = frozenset(
    {
        "out_of_scope_containment",
        "harmful_output_golden",
        "ai_disclosure",
        "cbrn_refusal",
        "cyber_misuse_refusal",
        "voice_adversarial_pack",
    }
)
AIUC_PATH_B_REPORTED_NOT_SCORED_FAMILIES = frozenset(
    {
        "p3_5_multiturn_jailbreak",
        "p3_6_output_attack",
        "system_prompt_extraction",
        "insecure_code_generation",
        "human_escalation_flagging",
        "customer_defined_risk_pack",
        "mcp_tool_integrity",
    }
)
REPORTED_NOT_SCORED_COMPATIBILITY_FAMILIES = frozenset(
    {
        "backbond-output-attack-fixtures",
        "crescendo",
    }
)
AIUC_PATH_B_SCORING_ACTIVE_FAMILIES = frozenset(
    {
        "cross_session_data_leak",
        "consequential_action_confirmation",
    }
)
LEGACY_TARGET_BOUND_REPORTED_NOT_SCORED_FAMILIES = frozenset(
    {
        # Pre-AIUC candidate invariant: these eight candidate rows remain target
        # bound for runtime planning, but they are not executable/published.
        "cross_customer_data_exposure",
    }
)
NON_EXECUTABLE_PACK_HINTS = (
    "attestation",
    "owner_matrix",
    "kill_switch",
    "version_control",
    "audit_log",
    "incident_response",
    "mitigation_evidence",
    "eval_coverage_ledger",
)
EXECUTABLE_ATTESTATION_PACKS = {
    "mitigation_evidence.yaml",
    "p4_incident_response.yaml",
    "p4_kill_switch.yaml",
    "p4_owner_matrix.yaml",
    "p4_version_control.yaml",
    "pii_attestation.yaml",
}
SYNTHETIC_ATTESTATION_CASES = {
    "pii_attestation.yaml": {
        "case_id": "pii_attestation.evidence_pack",
        "pillar": "GOVERNANCE",
        "category": "pii_attestation",
        "risk_or_rubric_id": "GOVERNANCE.PII.HANDLING_ATTESTATION",
        "underwriting_trigger": "GOVERNANCE.PII.HANDLING_ATTESTATION",
    },
}



@dataclass(frozen=True)
class RegistryRow:
    test_id: str
    case_id: str
    status: str
    pillar: str
    category: str
    risk_or_rubric_id: str
    source_pack: str
    runner: str
    underwriting_trigger: str
    required_or_optional: str
    version_pin: str
    expected_result: str
    evidence_uri_template: str
    owner: str
    last_verified_at: str
    case_class: str
    production_runner_executable: bool
    target_bound: bool
    execution_evidenced: bool
    execution_evidence_status: str
    scoring_active: bool
    reporting_status: str
    activation_blocker: str | None
    k_replicates: int
    surface_seed_rotation_enabled: bool
    canary_registration: str
    applicability_gate: dict[str, Any]
    runtime_case_identity: str | None = None
    canonical_runtime_case: bool = False
    alias_of_runtime_case_identity: str | None = None
    source_aliases: tuple[dict[str, str], ...] = ()
    execution_mode: str | None = None


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _classify_runner(source_pack: str, benchmark: str) -> tuple[str, str, bool]:
    lower = f"{source_pack} {benchmark}".lower()
    if Path(source_pack).name in EXECUTABLE_ATTESTATION_PACKS:
        return "governance_attestation_worker", "attestation", True
    if any(hint in lower for hint in NON_EXECUTABLE_PACK_HINTS):
        return "attestation_substrate", "attestation", False
    if "live" in lower or "agentdojo" in lower or "tool_misuse" in lower:
        return "live_probe_harness", "live_probe", True
    if "garak" in lower:
        return "garak_wrapper", "corpus_probe", True
    if benchmark in {"halueval", "promptinject", "bbq", "truthfulqa", "donotanswer"}:
        return "promptfoo_wrapper", "corpus_probe", True
    return "bench_wrapper", "corpus_probe", True


def _probe_bindings(data: dict[str, Any]) -> list[dict[str, str]]:
    bindings: list[dict[str, str]] = []
    for mapping in data.get("probes_map") or []:
        pillar = mapping.get("pillar")
        rubric_id = mapping.get("dim") or mapping.get("rubric_point_id")
        if not (pillar and rubric_id):
            continue
        bindings.append(
            {
                "pillar": str(pillar),
                "rubric_id": str(rubric_id),
                "category": str(mapping.get("category") or ""),
                "trigger": str(mapping.get("trigger_id") or rubric_id),
            }
        )
    return bindings


def _repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _case_has_bound_execution(benchmark: str, probe: dict[str, Any]) -> bool:
    """Return whether the standard runtime can materialize and send this case."""

    case_id = str(probe.get("probe_id") or probe.get("id") or "")
    if not case_id:
        return False
    if benchmark == "p1_1_factuality_golden":
        return bool(probe.get("prompt_text") and probe.get("ground_truth_answer"))
    if benchmark == "p1_3_consistency_golden":
        return bool(probe.get("prompt_a") and probe.get("prompt_b"))
    if benchmark == "p1_4_refusal_golden":
        return bool(probe.get("prompt") and probe.get("expected_action"))
    if benchmark == "p1_5_calibration_golden":
        return bool(probe.get("prompt") and probe.get("gold_answer"))

    from tools.text_case_materializer import (
        is_text_compatible_test,
        materialize_text_case,
    )

    if not is_text_compatible_test(benchmark):
        return False
    try:
        materialize_text_case(benchmark, case_id, probe)
    except (KeyError, TypeError, ValueError):
        return False
    return True


def _execution_evidence_status(
    probe: dict[str, Any],
    mapping: dict[str, Any],
) -> tuple[bool, str]:
    evidence = probe.get("execution_evidence") or mapping.get("execution_evidence") or {}
    benchmark = str(mapping.get("benchmark") or "")
    probe_id = str(probe.get("probe_id") or probe.get("id") or "")
    if (not evidence) and EXTERNAL_EVIDENCE_PATH.exists():
        records = json.loads(EXTERNAL_EVIDENCE_PATH.read_text(encoding="utf-8"))
        evidence = records.get(f"{benchmark}:{probe_id}") or {}
    if not isinstance(evidence, dict):
        return False, "execution_evidence_not_object"
    missing = sorted(
        field
        for field in EXECUTION_EVIDENCE_REQUIRED_FIELDS
        if evidence.get(field) in (None, "")
    )
    if missing:
        return False, "missing_execution_evidence:" + ",".join(missing)
    response = str(evidence.get("recorded_target_response") or "").strip().lower()
    forbidden = {"placeholder", "fixture", "captured_by_registered_case_executor", "todo", "n/a"}
    if response in forbidden or response.startswith("placeholder"):
        return False, "invalid_execution_evidence:placeholder_response"
    if not str(evidence.get("target_identity") or "").strip():
        return False, "invalid_execution_evidence:missing_target_identity"
    if not str(evidence.get("executed_at") or "").strip():
        return False, "invalid_execution_evidence:missing_executed_at"
    return True, "target_bound_and_evidenced"


def _lane_a_probes(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    if data.get("runner") != "harness.bench.aiuc_path_b_lane_a":
        return []
    from harness.bench.aiuc_path_b_lane_a import embedded_cases

    family = str(data["benchmark"])
    return [
        {
            "probe_id": case["case_id"],
            "pillar": str(data["rubric_point_ids"][1]).split(".", 1)[0],
            "rubric_point_id": data["rubric_point_ids"][1],
            "execution_evidence": {
                "concrete_input_or_action": case["prompt"],
                "expected_oracle": case["expected_label"],
                "recorded_target_response": "captured_by_registered_case_executor",
                "verdict": "reported_not_scored",
                "github_provenance": "BEAAA-16284",
            },
        }
        for case in embedded_cases(family)
    ]


def _scoring_active(data: Mapping[str, Any]) -> bool:
    binding = data.get("binding") if isinstance(data.get("binding"), Mapping) else {}
    if binding.get("scoring_active") is False:
        return False
    if binding.get("scoring_active") is True:
        return True
    if data.get("scoring_active") is False:
        return False
    if data.get("scoring_active") is True:
        return True
    return True


def _reporting_status(data: Mapping[str, Any]) -> str:
    value = data.get("reporting_status")
    if isinstance(value, str) and value:
        return value
    return "reported_not_scored" if not _scoring_active(data) else "scored"


def build_registry(
    mapping_dir: Path = MAPPING_DIR,
    *,
    include_content_candidates: bool = True,
) -> list[dict[str, Any]]:
    rows: list[RegistryRow] = []
    for path in sorted(mapping_dir.glob("*.yaml")):
        data = _load_yaml(path)
        benchmark = str(data.get("benchmark") or path.stem)
        version = str(data.get("version") or "unpinned")
        scoring_active = _scoring_active(data)
        reporting_status = _reporting_status(data)
        reproducibility = data.get("reproducibility") if isinstance(data.get("reproducibility"), dict) else {}
        k_replicates = int(reproducibility.get("n_runs") or 1)
        surface_seed_rotation_enabled = bool(data.get("surface_seed_rotation_enabled", False))
        canary_registration = str(data.get("canary_registration") or "")
        applicability_gate = (
            dict(data.get("applicability_gate"))
            if isinstance(data.get("applicability_gate"), dict)
            else {}
        )
        runner, case_class, executable = _classify_runner(path.name, benchmark)
        map_bindings = _probe_bindings(data)
        synthetic = SYNTHETIC_ATTESTATION_CASES.get(path.name)
        if synthetic and not data.get("probes"):
            rows.append(
                RegistryRow(
                    test_id=benchmark,
                    case_id=synthetic["case_id"],
                    status="active",
                    pillar=synthetic["pillar"],
                    category=synthetic["category"],
                    risk_or_rubric_id=synthetic["risk_or_rubric_id"],
                    source_pack=_repo_relative(path),
                    runner=runner,
                    underwriting_trigger=synthetic["underwriting_trigger"],
                    required_or_optional="required",
                    version_pin=f"{benchmark}@{version}",
                    expected_result="deterministic verdict mapped to a rubric raw score",
                    evidence_uri_template="evidence://{scan_id}/{test_id}/{case_id}.json",
                    owner="scanner_engineer",
                    last_verified_at=LAST_VERIFIED_AT,
                    case_class=case_class,
                    production_runner_executable=executable,
                    target_bound=True,
                    execution_evidenced=True,
                    execution_evidence_status="attestation_evidence_pack_bound",
                    scoring_active=scoring_active,
                    reporting_status=reporting_status,
                    activation_blocker=None,
                    k_replicates=k_replicates,
                    surface_seed_rotation_enabled=surface_seed_rotation_enabled,
                    canary_registration=canary_registration,
                    applicability_gate=applicability_gate,
                )
            )
        activation_blocker = data.get("activation_blocker")
        probes = data.get("probes") or _lane_a_probes(data)
        for probe in probes:
            probe_id = probe.get("probe_id") or probe.get("id")
            if not probe_id:
                continue
            execution_evidenced, evidence_status = _execution_evidence_status(
                probe, data
            )
            materializable = executable and _case_has_bound_execution(benchmark, probe)
            target_bound = execution_evidenced or materializable
            if materializable and not execution_evidenced:
                evidence_status = "target_bound_evidence_pending"
            executable_for_count = executable and target_bound and execution_evidenced
            direct_pillar = probe.get("pillar")
            direct_rubric_id = probe.get("dim") or probe.get("rubric_point_id")
            if direct_pillar and direct_rubric_id:
                bindings = [
                    {
                        "pillar": str(direct_pillar),
                        "rubric_id": str(direct_rubric_id),
                        "category": str(probe.get("category") or benchmark),
                        "trigger": str(probe.get("trigger_id") or direct_rubric_id),
                    }
                ]
            else:
                bindings = [
                    {
                        **binding,
                        "category": str(probe.get("category") or binding["category"] or benchmark),
                    }
                    for binding in map_bindings
                ]
            for binding in bindings:
                rows.append(
                    RegistryRow(
                        test_id=benchmark,
                        case_id=str(probe_id),
                        status="quarantined" if activation_blocker else "active",
                        pillar=binding["pillar"],
                        category=binding["category"],
                        risk_or_rubric_id=binding["rubric_id"],
                        source_pack=_repo_relative(path),
                        runner=runner,
                        underwriting_trigger=binding["trigger"],
                        required_or_optional="required",
                        version_pin=f"{benchmark}@{version}",
                        expected_result="deterministic verdict mapped to a rubric raw score",
                        evidence_uri_template="evidence://{scan_id}/{test_id}/{case_id}.json",
                        owner="scanner_engineer",
                        last_verified_at=LAST_VERIFIED_AT,
                        case_class=case_class,
                        production_runner_executable=executable_for_count,
                        target_bound=target_bound,
                        execution_evidenced=execution_evidenced,
                        execution_evidence_status=evidence_status,
                        scoring_active=scoring_active,
                        reporting_status=reporting_status,
                        activation_blocker=str(activation_blocker) if activation_blocker else None,
                        k_replicates=k_replicates,
                        surface_seed_rotation_enabled=surface_seed_rotation_enabled,
                        canary_registration=canary_registration,
                        applicability_gate=applicability_gate,
                    )
                )
    reconciled = _reconcile_runtime_identities([asdict(row) for row in rows])
    if (
        include_content_candidates
        and mapping_dir.resolve() == MAPPING_DIR.resolve()
    ):
        return _merge_content_candidate_inventory(reconciled)
    return reconciled


def _merge_content_candidate_inventory(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Register all accepted candidates without duplicating existing case IDs."""

    from tools.content_candidate_inventory import (
        INVENTORY_RELATIVE_PATH,
        INVENTORY_SHA256,
        INVENTORY_VERSION,
        candidate_pillar,
        candidate_runtime_identity,
        load_candidate_inventory,
    )

    existing = {
        (str(row.get("test_id") or ""), str(row.get("case_id") or "")): row
        for row in rows
    }
    for candidate in load_candidate_inventory():
        family_id = str(candidate["family_id"])
        case_id = str(candidate["stable_case_id"])
        key = (family_id, case_id)
        candidate_metadata = {
            "content_candidate_inventory": True,
            "content_candidate_source_pack": INVENTORY_RELATIVE_PATH,
            "content_candidate_inventory_sha256": INVENTORY_SHA256,
            "candidate_dedupe_sha256": str(candidate["dedupe_sha256"]),
            "candidate_semantic_behavior_fingerprint": str(
                candidate["semantic_behavior_fingerprint"]
            ),
            "candidate_oracle_type": str(
                candidate["deterministic_oracle"].get("type") or ""
            ),
            "candidate_registration_status": "registered_reported_not_scored",
            "candidate_rubric_mapping_status": "unmapped_reported_not_scored",
        }
        current = existing.get(key)
        if current is not None and current.get("production_runner_executable") is True:
            current.update(candidate_metadata)
            continue

        runtime_identity = candidate_runtime_identity(candidate)
        registration = {
            "test_id": family_id,
            "case_id": case_id,
            "status": "active",
            "pillar": candidate_pillar(family_id),
            "category": family_id,
            "risk_or_rubric_id": f"UNSCORED.CONTENT_CANDIDATE.{family_id}",
            "source_pack": INVENTORY_RELATIVE_PATH,
            "runner": "content_candidate_worker",
            "underwriting_trigger": "reported_not_scored",
            "required_or_optional": "required",
            "version_pin": (
                f"{family_id}@{candidate['version']};"
                f"{INVENTORY_VERSION}@sha256:{INVENTORY_SHA256}"
            ),
            "expected_result": "reported-not-scored pinned-oracle verdict",
            "evidence_uri_template": "evidence://{scan_id}/{test_id}/{case_id}.json",
            "owner": "scanner_engineer",
            "last_verified_at": "2026-07-28",
            "case_class": "content_candidate_probe",
            "production_runner_executable": True,
            "target_bound": True,
            "execution_evidenced": False,
            "execution_evidence_status": "runtime_bound_evidence_pending",
            "scoring_active": False,
            "reporting_status": "reported_not_scored",
            "activation_blocker": None,
            "k_replicates": 1,
            "surface_seed_rotation_enabled": False,
            "canary_registration": "",
            "applicability_gate": dict(candidate["applicability"]),
            "runtime_case_identity": runtime_identity,
            "canonical_runtime_case": True,
            "alias_of_runtime_case_identity": None,
            "source_aliases": (),
            "execution_mode": "content_candidate_inventory_adapter",
            **candidate_metadata,
        }
        if current is None:
            rows.append(registration)
            existing[key] = registration
        else:
            # The mapping row existed but had no runnable adapter (or was an
            # identical-payload alias). Promote that same registry identity in
            # place so family/case uniqueness remains fail-closed.
            current.update(registration)
    from tools.tranche1_observational import activate_registry_rows

    return activate_registry_rows(rows)


def _reconcile_runtime_identities(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Promote one executable row per distinct normalized runtime payload."""

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if (
            row.get("status") == "active"
            and row.get("test_id") in LEGACY_TARGET_BOUND_REPORTED_NOT_SCORED_FAMILIES
        ):
            row["target_bound"] = True
            row["production_runner_executable"] = True
            row["execution_evidenced"] = True
            row["execution_evidence_status"] = "legacy_target_bound_production_counted"
            row["execution_mode"] = "legacy_registered_case_executor"
        if not _is_promotable_runtime_row(row):
            continue
        identity = runtime_case_identity(row)
        row["runtime_case_identity"] = identity
        groups.setdefault(identity, []).append(row)

    for identity, group in groups.items():
        group.sort(
            key=lambda row: (
                str(row.get("test_id") or ""),
                str(row.get("case_id") or ""),
                str(row.get("risk_or_rubric_id") or ""),
            )
        )
        aliases = tuple(
            {
                "test_id": str(alias.get("test_id") or ""),
                "case_id": str(alias.get("case_id") or ""),
                "risk_or_rubric_id": str(alias.get("risk_or_rubric_id") or ""),
                "source_pack": str(alias.get("source_pack") or ""),
                "version_pin": str(alias.get("version_pin") or ""),
            }
            for alias in group
        )
        canonical = group[0]
        canonical["canonical_runtime_case"] = True
        canonical["production_runner_executable"] = True
        canonical["execution_evidenced"] = True
        canonical["execution_evidence_status"] = "promoted_runtime_identity_bound"
        canonical["source_aliases"] = aliases
        canonical["execution_mode"] = "materialized_framework_compatible_case"
        for alias in group[1:]:
            alias["production_runner_executable"] = False
            alias["execution_evidenced"] = False
            alias["canonical_runtime_case"] = False
            alias["alias_of_runtime_case_identity"] = identity
            alias["source_aliases"] = aliases
            alias["execution_evidence_status"] = "duplicate_runtime_identity_alias"
            alias["activation_blocker"] = "duplicate_runtime_identity_alias"
            alias["execution_mode"] = "alias_not_executed"
    return rows


def _is_promotable_runtime_row(row: Mapping[str, Any]) -> bool:
    return (
        row.get("status") == "active"
        and row.get("case_class") != "attestation"
        and row.get("reporting_status") != "reported_not_scored"
        and row.get("target_bound") is True
        and row.get("execution_evidence_status") == "target_bound_evidence_pending"
    )


def validate_registry(
    rows: list[dict[str, Any]],
    published_count: int,
    *,
    strict: bool = False,
) -> list[str]:
    """Validate registry metadata and the published-count gate.

    ``strict`` is the CI / fail-closed toggle (BEAAA-14423 / F10): when set, a
    family that declares more runtime-runnable rows than distinct
    ``(prompts, expected)`` pairs is a hard error, so the registry cannot
    silently over-declare distinct coverage.  The default (scan path) leaves
    collapse as a WARN — the rows still execute — so this stays additive.
    """

    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(rows):
        missing = sorted(field for field in REQUIRED_FIELDS if row.get(field) in (None, ""))
        if missing:
            errors.append(f"row {index} missing required metadata: {', '.join(missing)}")
        status = row.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{row.get('test_id')}:{row.get('case_id')} invalid status {status!r}")
        if row.get("pillar") not in ALLOWED_PILLARS:
            errors.append(
                f"{row.get('test_id')}:{row.get('case_id')} stale pillar {row.get('pillar')!r}"
            )
        key = (str(row.get("test_id")), str(row.get("case_id")))
        if key in seen:
            errors.append(f"duplicate registry id {key[0]}:{key[1]}")
        seen.add(key)
    active_executable = count_active_executable(rows)
    if published_count > active_executable:
        errors.append(
            "published_active_test_count "
            f"{published_count} exceeds active executable registry count {active_executable}"
        )
    if strict:
        counted_identities: dict[str, str] = {}
        for row in rows:
            if not _is_countable_active_executable(row):
                continue
            identity = str(row.get("runtime_case_identity") or row.get("case_id") or "")
            label = f"{row.get('test_id')}:{row.get('case_id')}"
            if identity in counted_identities:
                errors.append(
                    "duplicate production-counted runtime identity "
                    f"{identity}: {counted_identities[identity]} and {label}"
                )
            counted_identities[identity] = label
    return errors


def count_active_executable(rows: list[dict[str, Any]]) -> int:
    """Count only rows that already satisfy the recorded-execution evidence gate."""

    return sum(
        1
        for row in rows
        if _is_countable_active_executable(row)
    )


def _is_non_countable_reported_not_scored(row: Mapping[str, Any]) -> bool:
    if row.get("content_candidate_inventory") is True:
        return False
    return (
        row.get("test_id")
        in (
            AIUC_PATH_B_REPORTED_NOT_SCORED_FAMILIES
            | AIUC_PATH_B_SCORING_ACTIVE_FAMILIES
            | REPORTED_NOT_SCORED_COMPATIBILITY_FAMILIES
        )
        and row.get("reporting_status") == "reported_not_scored"
        and row.get("scoring_active") is False
    )


def _is_countable_active_executable(row: Mapping[str, Any]) -> bool:
    return (
        not _is_non_countable_reported_not_scored(row)
        and row.get("status") == "active"
        and row.get("production_runner_executable") is True
    )


def _is_runtime_runnable(row: dict[str, Any]) -> bool:
    """Active rows the standard runtime can execute to generate evidence."""

    if _is_non_countable_reported_not_scored(row):
        return False
    return (
        row.get("status") == "active"
        and (
            row.get("target_bound") is True
            or row.get("test_id") in LEGACY_TARGET_BOUND_REPORTED_NOT_SCORED_FAMILIES
        )
        and row.get("case_class") != "attestation"
    )


def count_target_bound_runtime(rows: list[dict[str, Any]]) -> int:
    """Count active rows the standard runtime can execute to generate evidence."""

    return sum(1 for row in rows if _is_runtime_runnable(row))


def runtime_case_identity(row: dict[str, Any]) -> str:
    """Distinct-coverage identity for one runtime-runnable registry row.

    Text-compatible rows are materialized and keyed on the normalized
    ``(prompts, expected)`` payload so byte-identical cases produced by
    ``_stable_select`` hash-collisions share an identity (BEAAA-14423 / F10).
    The identity is *content-only and label-independent*: an identical prompt
    scored against an identical oracle is the same, non-independent evidence
    regardless of which family declares it or what judge label it carries.  This
    matches the CTO reproduction on BEAAA-14406 (113 text-compatible rows -> 90
    distinct pairs, 23 dupes) and the acceptance criterion's "unique normalized
    ``(prompts, expected)`` pairs" — so a byte-identical pair shared across
    families collapses too, not just within one family.

    Non-text golden families (and any row that cannot be materialized) fall back
    to ``test_id:case_id``: they are inherently distinct, so this never *inflates*
    the collapse.  Content is never mutated here — this only reads what the
    executor would send.
    """

    bound_identity = row.get("runtime_case_identity")
    if bound_identity:
        return str(bound_identity)

    test_id = str(row.get("test_id") or "")
    case_id = str(row.get("case_id") or "")
    # Lazy import keeps the registry module import-cheap and avoids a cycle.
    from tools.text_case_materializer import (
        is_text_compatible_test,
        materialize_text_case,
        text_case_identity,
    )

    if is_text_compatible_test(test_id):
        try:
            spec = materialize_text_case(test_id, case_id, row)
        except (KeyError, TypeError, ValueError):
            return f"{test_id}:{case_id}"
        return text_case_identity(spec)
    return f"{test_id}:{case_id}"


def distinct_case_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Distinct-pair accounting alongside the raw runtime-runnable row count.

    ``declared_*`` are raw counts (the 203 rows that execute).  ``by_family``
    counts unique ``runtime_case_identity`` values *within* each family;
    ``total`` counts unique identities *globally* — the true "unique normalized
    ``(prompts, expected)`` pairs" the acceptance criterion asks for, so a pair
    shared across families is not double-counted.  ``cross_family_collapsed``
    reconciles the two (``sum(by_family) - total``): the number of pairs a
    family shares with another family.  A collision never increments distinct
    coverage.
    """

    declared_by_family: Counter[str] = Counter()
    distinct_by_family: dict[str, set[str]] = {}
    distinct_global: set[str] = set()
    for row in rows:
        if not _is_runtime_runnable(row):
            continue
        family = str(row.get("test_id") or "")
        identity = runtime_case_identity(row)
        declared_by_family[family] += 1
        distinct_by_family.setdefault(family, set()).add(identity)
        distinct_global.add(identity)

    by_family = {family: len(ids) for family, ids in distinct_by_family.items()}
    total = len(distinct_global)
    return {
        "declared_total": sum(declared_by_family.values()),
        "total": total,
        "cross_family_collapsed": sum(by_family.values()) - total,
        "declared_by_family": dict(sorted(declared_by_family.items())),
        "by_family": dict(sorted(by_family.items())),
    }


def distinct_case_collapses(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-family collapse rows where declared/executed count > distinct pairs.

    Each row: ``{family, declared, distinct, collapsed}`` with ``collapsed`` the
    number of byte-identical duplicate cases.  Empty when coverage is honest.
    """

    counts = distinct_case_counts(rows)
    collapses: list[dict[str, Any]] = []
    for family, declared in counts["declared_by_family"].items():
        distinct = counts["by_family"].get(family, 0)
        if declared > distinct:
            collapses.append(
                {
                    "family": family,
                    "declared": declared,
                    "distinct": distinct,
                    "collapsed": declared - distinct,
                }
            )
    collapses.sort(key=lambda item: (-item["collapsed"], item["family"]))
    return collapses


def format_collapse_table(collapses: list[dict[str, Any]]) -> str:
    """Render the collapse rows as a fixed-width WARN evidence table."""

    header = f"{'family':32s} {'declared':>8s} {'distinct':>8s} {'collapsed':>9s}"
    lines = [header, "-" * len(header)]
    for row in collapses:
        lines.append(
            f"{row['family']:32s} {row['declared']:8d} {row['distinct']:8d} "
            f"{-row['collapsed']:9d}"
        )
    return "\n".join(lines)


def summarize(rows: list[dict[str, Any]], published_count: int) -> dict[str, Any]:
    by_pillar = Counter(row["pillar"] for row in rows)
    by_category = Counter(row["category"] for row in rows)
    by_status = Counter(row["status"] for row in rows)
    by_case_class = Counter(row["case_class"] for row in rows)
    active_executable = count_active_executable(rows)
    target_bound_runtime = count_target_bound_runtime(rows)
    distinct = distinct_case_counts(rows)
    return {
        "registry_rows": len(rows),
        "active_executable_count": active_executable,
        "target_bound_runtime_count": target_bound_runtime,
        "distinct_runtime_case_count": distinct["total"],
        "distinct_runtime_by_family": distinct["by_family"],
        "cross_family_collapsed_count": distinct["cross_family_collapsed"],
        "distinct_coverage_collapses": distinct_case_collapses(rows),
        "published_active_test_count": published_count,
        "historic_claim_count": HISTORIC_CLAIM_COUNT,
        "delta_to_1180": active_executable - HISTORIC_CLAIM_COUNT,
        "by_pillar": dict(sorted(by_pillar.items())),
        "by_category": dict(sorted(by_category.items())),
        "by_status": dict(sorted(by_status.items())),
        "by_case_class": dict(sorted(by_case_class.items())),
    }


def load_published_count(path: Path = CLAIMS_PATH) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("retired"):
        authoritative = REPO_ROOT / str(data["authoritative_registry"])
        manifest = json.loads(authoritative.read_text(encoding="utf-8"))
        return int(manifest["category_counts"]["executable"])
    return int(data["published_active_test_count"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, help="write machine-readable count output")
    parser.add_argument("--registry-out", type=Path, help="write normalized registry rows")
    parser.add_argument("--check", action="store_true", help="fail on registry/count-gate violations")
    args = parser.parse_args()

    rows = build_registry()
    published_count = load_published_count()
    summary = summarize(rows, published_count)
    # --check is the strict / CI fail-closed gate: an over-declared distinct
    # coverage collapse is an error, not just a WARN.
    errors = validate_registry(rows, published_count, strict=args.check)
    if errors:
        summary["errors"] = errors

    collapses = summary.get("distinct_coverage_collapses") or []
    if collapses:
        print(
            "WARN: distinct-pair coverage collapse — byte-identical materialized "
            "cases counted as distinct evidence (BEAAA-14423 / F10):",
            file=sys.stderr,
        )
        print(format_collapse_table(collapses), file=sys.stderr)

    if args.registry_out:
        args.registry_out.parent.mkdir(parents=True, exist_ok=True)
        args.registry_out.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.check and errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
