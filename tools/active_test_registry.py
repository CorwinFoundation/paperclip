#!/usr/bin/env python3
"""Canonical BackBond Scanner active-test registry and count gate.

The scored registry is derived from ``rubric/probe_mappings/*.yaml``. Validated
reported-not-scored content candidates may be merged through a pinned runtime
adapter without implying a scoring/rubric mapping. The gate only counts rows
that are active and production-runner executable.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from harness.bench.corpus_pin import load_probe_set_version

from tools.founder_dev_mode import (
    ATTESTATION_FOUNDER_DEV,
    ATTESTATION_PRODUCTION_SIGNED,
    FounderDevModeError,
    assert_production_attested,
    attestation_tier as _attestation_tier,
    dev_scoring_declared as _dev_scoring_declared,
    is_dev_mode as _is_dev_mode,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
MAPPING_DIR = REPO_ROOT / "rubric" / "probe_mappings"
CLAIMS_PATH = REPO_ROOT / "tools" / "active_test_registry_claims.json"
AUTHORITATIVE_REGISTRY_PATH = (
    REPO_ROOT / "tools" / "probe-registry" / "authoritative_registry.json"
)
ACTIVE_REGISTRY_PATH = REPO_ROOT / "tools" / "probe-registry" / "active_registry.json"
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
# BEAAA-23857 scored-lane expansion at the thinnest rubric points. These
# families are single-turn and judge-model-free; the runtime binding check is
# "prompt + a non-empty oracle", not the text_case_materializer path.
SCORED_LANE_DETERMINISTIC_FAMILIES = frozenset(
    {
        "p1_2_citation_integrity_golden",
        "p1_6_eval_coverage_breadth_golden",
        "p2_5_sentiment_skew_golden",
    }
)
BEAAA_24210_SCORED_FLOOR_FAMILIES = frozenset(
    {
        "p1_2_citation_integrity_floor",
        "p1_6_eval_breadth_floor",
        "p2_5_sentiment_skew_floor",
        "p3_2_indirect_injection_floor",
        "p3_3_secret_exfil_floor",
        "p1_2_citation_integrity_floor30",
        "p1_6_eval_breadth_floor30",
        "p2_5_sentiment_skew_floor30",
        "p3_2_indirect_injection_floor30",
        "p3_3_secret_exfil_floor30",
        "p1_5_confidence_calibration_floor30",
    }
)
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
        "authentication_bypass_resistance",
        "sensitive_data_disclosure_resistance",
        # BEAAA-28131: these newly authored Day-1 supporting families are
        # runnable through the direct-family adapter, but they remain outside
        # the published executed-test count until target-derived evidence is
        # promoted separately.  Runtime capability and claim countability are
        # intentionally independent.
        "tool_log_trace_secret_leakage",
        "delegated_authority_confused_deputy",
        "revocation_execution_time_authorization",
        "file_temporary_link_leakage",
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
# Families promoted to scored (underwriting-eligibility, zero score weight) under
# Founder-Dev-Mode only: scored when BACKBOND_FOUNDER_DEV_MODE=on, reported-not-
# scored otherwise, never production-attested. See tools/founder_dev_mode.py.
#
# FOUNDER_DEV_SCORED_FAMILIES drives the scoring resolution for all dev-promoted
# families. It is split by flag-OFF countability so promotion never shifts the
# frozen production count (340) when the flag is off:
#   * ..._FOUNDER_DEV_SCORING_ACTIVE_FAMILIES — non-countable while reported-not-
#     scored; joined to the non-countable union so the flag-off count is unchanged
#     and they become countable only when the dev flag is on.
#   * FOUNDER_DEV_LEGACY_SCORED_FAMILIES — ALREADY counted in the 340 as reported-
#     not-scored; the dev flag only flips them scored (count unchanged).
AIUC_PATH_B_FOUNDER_DEV_SCORING_ACTIVE_FAMILIES = frozenset(
    {
        "p3_5_multiturn_jailbreak",
        "p3_6_output_attack",
        "system_prompt_extraction",
        "insecure_code_generation",
        "crescendo",
        "backbond-output-attack-fixtures",
    }
)
FOUNDER_DEV_LEGACY_SCORED_FAMILIES = frozenset(
    {
        "ai_disclosure",
        "cbrn_refusal",
        "cyber_misuse_refusal",
        "harmful_output_golden",
        "out_of_scope_containment",
        "voice_adversarial_pack",
    }
)
FOUNDER_DEV_SCORED_FAMILIES = (
    AIUC_PATH_B_FOUNDER_DEV_SCORING_ACTIVE_FAMILIES | FOUNDER_DEV_LEGACY_SCORED_FAMILIES
)
BEAAA_21704_REPORTED_NOT_SCORED_CASES = frozenset(
    {
        ("agentdojo", "docs.attachment.rendered_html.command_hijack"),
        ("agentdojo", "retrieval.citation_card.canary_exfil"),
        ("agentdojo", "tools.email_thread.injected_followup"),
        ("agentdojo", "workspace.write_then_summarize.indirect_system_override"),
        ("garak", "dan.Dan_11_0"),
        ("garak_beaaa152", "garak.dan.AntiDAN"),
        ("garak_beaaa152", "garak.dan.Dan_11_0"),
        ("garak_beaaa152", "garak.malwaregen.Evasion"),
        ("garak_beaaa152", "garak.misleading.FalseAssertion"),
        ("garak_beaaa152", "garak.promptinject.HijackHateHumans"),
        ("garak_beaaa152", "garak.snowball.GraphConnectivity"),
        ("garak_beaaa152", "garak.xss.MarkdownImageExfil"),
        ("jailbreakbench", "jbb.privacy.injection"),
        ("privacybench", "privacybench.direct_regurg"),
        ("privacybench", "privacybench.indirect_inference.exfil"),
        ("privacybench", "privacybench.jailbreak_pii.exfil"),
        ("sentiment_skew", "ss.bold_subset"),
        ("sentiment_skew", "ss.holisticbias_subset"),
        ("sentiment_skew", "ss.legal_intake_counterfactual"),
        ("demographic_parity", "dp.bbq_disambig"),
        ("demographic_parity", "dp.legal_intake_counterfactual"),
        ("equal_opportunity", "eo.uw_labeled_subset"),
        ("counterfactual_stability", "cs.all_axes"),
        ("eval_representation", "er.derived_coverage"),
    }
)
# These rows were already target-bound runtime aliases before BEAAA-21704 added
# external execution evidence.  Report-only status keeps them out of the scored
# executable total, but must not erase their pre-existing runtime coverage.
BEAAA_21704_RETAINED_RUNTIME_CASES = frozenset(
    {
        ("garak", "dan.Dan_11_0"),
        ("garak_beaaa152", "garak.dan.AntiDAN"),
        ("garak_beaaa152", "garak.dan.Dan_11_0"),
        ("garak_beaaa152", "garak.malwaregen.Evasion"),
        ("garak_beaaa152", "garak.misleading.FalseAssertion"),
        ("garak_beaaa152", "garak.promptinject.HijackHateHumans"),
        ("garak_beaaa152", "garak.snowball.GraphConnectivity"),
        ("garak_beaaa152", "garak.xss.MarkdownImageExfil"),
        ("jailbreakbench", "jbb.privacy.injection"),
        ("privacybench", "privacybench.direct_regurg"),
        ("privacybench", "privacybench.indirect_inference.exfil"),
        ("privacybench", "privacybench.jailbreak_pii.exfil"),
    }
)
BEAAA_21704_PROVENANCE = "BEAAA-21704"
BEAAA_21704_ALLOWED_TARGET_CLASSES = frozenset(
    {
        "approved_non_customer_production_like_bounded_agent",
        "self_hosted_agentdojo_native_task_suite_gateway",
    }
)
_NON_EXECUTION_MARKERS = re.compile(
    r"(?:^|[\s_:-])(placeholder|fixture|fabricated|synthetic[-_ ]?response|todo|n/?a)(?:$|[\s_.,:-])",
    re.IGNORECASE,
)
LEGACY_TARGET_BOUND_REPORTED_NOT_SCORED_FAMILIES = frozenset(
    {
        # Pre-AIUC candidate invariant: these eight candidate rows remain target
        # bound for runtime planning, but they are not executable/published.
        "cross_customer_data_exposure",
    }
)
# These families have historical/external evidence but no production adapter in
# RegisteredCaseExecutor. They remain visible in the registry and reports, but
# must stay outside the required full-scan set until an adapter is bound.
UNBOUND_REGISTERED_CASE_FAMILIES = frozenset(
    {
        "agentdojo",
        "counterfactual_stability",
        # BEAAA-29742. The live controlled scan on BEAAA-29082 hit the same
        # ``registered case adapter not bound`` raise on six more families.
        # Three are lane-A packs whose bench module is capture tooling that the
        # production executor never dispatches; three are aggregate P2 fairness
        # statistics with no per-case dispatch shape at all.
        #
        # BEAAA-29777 QA correction. An earlier revision of this comment said
        # all six are reported_not_scored. That was wrong at the row level: 50
        # of the flipped rows carry ``reporting_status: scored`` -- 30 in
        # harmful_output_golden (hog-001..030) and 20 in cyber_misuse_refusal
        # (cmr-001..020), all ``content_candidate_inventory: True``. The right
        # claim is narrower and is measured, not asserted: gating changes no
        # SCORE because ``is_canonical_scored_row`` reads status,
        # canonical_runtime_case, reporting_status, execution_evidenced and
        # target_bound -- not production_runner_executable. This gate writes
        # only production_runner_executable and execution_evidence_status, so
        # ``count_canonical_scored_rows`` is 431 at master 79c5d1f1 and 431
        # here. The already-gated rows in ai_disclosure and cbrn_refusal were
        # canonical scored rows too and have been outside the executable set
        # outside the executable set since BEAAA-29085, on the same reasoning.
        # BEAAA-29957 binds cbrn_refusal end to end. BEAAA-29973, BEAAA-29998,
        # BEAAA-30094, and BEAAA-30123 do the same for cyber_misuse_refusal,
        # harmful_output_golden, out_of_scope_containment, and
        # consequential_action_confirmation, and BEAAA-30127 ai_disclosure;
        # none is quarantined here anymore.
        "demographic_parity",
        "equal_opportunity",
        "eval_representation",
        # The BEAAA-29742 registry-wide sweep found two families the live scan
        # census missed. Both are the same unbound-adapter class as the six
        # above and neither moves count_canonical_scored_rows.
        "sentiment_skew",
        "voice_adversarial_pack",
        # BEAAA-29742, co-signed by the CTO on BEAAA-29746. ``pii_attestation``
        # is gated for a DIFFERENT reason than the ten families above. They are
        # reported_not_scored. This one is scoring_active: true -- and it is
        # still correct to gate it, because its score does not come from this
        # row. GOVERNANCE.PII.HANDLING_ATTESTATION is measured by
        # ``harness/p4_attestation_adapter.py`` (benchmark
        # pii_attestation_evidence_pack_v0_1, deterministic ADR-0025 grading),
        # which ``harness/contract_envelope.py`` imports on the production
        # envelope path. This registry row is synthetic, not declared:
        # rubric/probe_mappings/pii_attestation.yaml has no ``probes:`` block,
        # so SYNTHETIC_ATTESTATION_CASES manufactures
        # ``pii_attestation.evidence_pack`` and ``_pack_runner`` stamps it
        # executable only because the filename appears in
        # EXECUTABLE_ATTESTATION_PACKS. Its case_id matches no bench case, so
        # it has never executed in any release -- it is a second, dead
        # execution surface for a rubric point already produced elsewhere.
        # Gating it moves census, not score; Delta=0 on the golden set is the
        # proof, recorded in tools/calibration-deltas/. Binding an adapter was
        # declined deliberately: it would give one rubric point two producers.
        # Do not re-open this as an escalation on the next registry sweep.
        "pii_attestation",
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
    attestation_tier: str = ATTESTATION_PRODUCTION_SIGNED


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
        normalized = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        normalized = path
    # Registry rows are hashed into the release identity. Native Windows path
    # separators would make the same source tree produce a different marker
    # from Linux CI/production, so the serialized contract always uses POSIX.
    return normalized.as_posix()


def _case_has_bound_execution(benchmark: str, probe: dict[str, Any]) -> bool:
    """Return whether the standard runtime can materialize and send this case."""

    case_id = str(probe.get("probe_id") or probe.get("id") or "")
    if not case_id:
        return False
    from harness.direct_family_adapter import DIRECT_REGISTRY_FAMILIES, has_bound_case

    if benchmark in DIRECT_REGISTRY_FAMILIES:
        return has_bound_case(benchmark, case_id)
    if benchmark == "p1_1_factuality_golden":
        return bool(probe.get("prompt_text") and probe.get("ground_truth_answer"))
    if benchmark == "p1_3_consistency_golden":
        return bool(probe.get("prompt_a") and probe.get("prompt_b"))
    if benchmark == "p1_4_refusal_golden":
        return bool(probe.get("prompt") and probe.get("expected_action"))
    if benchmark == "p1_5_calibration_golden":
        return bool(probe.get("prompt") and probe.get("gold_answer"))
    if benchmark in (SCORED_LANE_DETERMINISTIC_FAMILIES | BEAAA_24210_SCORED_FLOOR_FAMILIES):
        # BEAAA-23857. A case is runtime-bound when it carries the single-turn
        # payload plus at least one oracle side; a roster entry with an empty
        # oracle is not materializable and must not count.
        oracle = probe.get("oracle") or {}
        return bool(
            probe.get("prompt")
            and (oracle.get("pass_requires_any") or oracle.get("fail_on_any"))
        )

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


#: Provenance a lane-C row must carry before ``target_bound`` may be true
#: (BEAAA-23860): who was executed, what class of target it was, and the
#: per-seed record of the runs that produced the verdict.
LANE_C_TARGET_PROVENANCE_FIELDS = ("target_identity", "target_class", "run_provenance")


def _is_lane_c_family(benchmark: str) -> bool:
    from harness.lane_c_adapter import is_lane_c_family

    return is_lane_c_family(benchmark)


def _lane_c_runner_bound(benchmark: str, case_id: Any) -> bool:
    """Whether the registered executor has a bound adapter for this case.

    An unrecognized lane-C family or case must not fall through as executable,
    so this gates ``production_runner_executable`` rather than trusting the
    mapping's own declaration.
    """

    from harness.lane_c_adapter import has_bound_case

    return has_bound_case(benchmark, str(case_id))


def _has_lane_c_run_provenance(
    probe: dict[str, Any],
    mapping: dict[str, Any],
) -> bool:
    """Whether this probe's evidence names a real target and real runs."""

    evidence = probe.get("execution_evidence") or mapping.get("execution_evidence") or {}
    if not isinstance(evidence, dict):
        return False
    if any(not evidence.get(field) for field in LANE_C_TARGET_PROVENANCE_FIELDS):
        return False
    runs = evidence.get("run_provenance")
    if not isinstance(runs, list) or not runs:
        return False
    return all(
        isinstance(run, dict)
        and run.get("seed") in (0, 1, 2, 3, 4)
        and bool(run.get("sha256"))
        and bool(run.get("evidence_uri"))
        and bool(run.get("verdict"))
        for run in runs
    )


def _execution_evidence_status(
    probe: dict[str, Any],
    mapping: dict[str, Any],
    *,
    allow_external_evidence: bool = True,
) -> tuple[bool, str]:
    evidence = probe.get("execution_evidence") or mapping.get("execution_evidence") or {}
    external_evidence = False
    benchmark = str(mapping.get("benchmark") or "")
    probe_id = str(probe.get("probe_id") or probe.get("id") or "")
    if allow_external_evidence and (not evidence) and EXTERNAL_EVIDENCE_PATH.exists():
        records = json.loads(EXTERNAL_EVIDENCE_PATH.read_text(encoding="utf-8"))
        if len(records) == len(BEAAA_21704_REPORTED_NOT_SCORED_CASES):
            evidence = records.get(f"{benchmark}:{probe_id}") or {}
            external_evidence = bool(evidence)
    if not isinstance(evidence, dict):
        return False, "execution_evidence_not_object"
    missing = sorted(
        field
        for field in EXECUTION_EVIDENCE_REQUIRED_FIELDS
        if evidence.get(field) in (None, "")
    )
    if missing:
        return False, "missing_execution_evidence:" + ",".join(missing)
    response = str(evidence.get("recorded_target_response") or "").strip()
    if external_evidence:
        if _NON_EXECUTION_MARKERS.search(response):
            return False, "invalid_execution_evidence:non_execution_response"
        if str(evidence.get("github_provenance") or "") != BEAAA_21704_PROVENANCE:
            return False, "invalid_execution_evidence:provenance"
        if not str(evidence.get("target_identity") or "").strip():
            return False, "invalid_execution_evidence:missing_target_identity"
        target_class = str(evidence.get("target_class") or "").strip()
        if target_class not in BEAAA_21704_ALLOWED_TARGET_CLASSES:
            return False, "invalid_execution_evidence:target_class"
        executed_at = str(evidence.get("executed_at") or "").strip()
        try:
            parsed = dt.datetime.fromisoformat(executed_at.replace("Z", "+00:00"))
        except ValueError:
            return False, "invalid_execution_evidence:executed_at"
        if parsed.tzinfo is None:
            return False, "invalid_execution_evidence:executed_at"
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


def _direct_family_probes(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Materialize the checked-in direct-family corpora for the live registry."""

    if data.get("runner") != "harness.direct_family_adapter":
        return []
    from harness.direct_family_adapter import registry_probes

    return list(registry_probes(str(data.get("benchmark") or "")))


def _scoring_active(data: Mapping[str, Any]) -> bool:
    # Founder-Dev-Mode override: dev-scored only when the env opt-in is set;
    # otherwise fail closed to not-scored so production defaults are unchanged.
    # A family opts in via the registry frozenset OR via its mapping binding.
    if (
        str(data.get("benchmark") or "") in FOUNDER_DEV_SCORED_FAMILIES
        or (_attestation_tier(data) == ATTESTATION_FOUNDER_DEV and _dev_scoring_declared(data))
    ):
        return _is_dev_mode()
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
    if (
        str(data.get("benchmark") or "") in FOUNDER_DEV_SCORED_FAMILIES
        or (_attestation_tier(data) == ATTESTATION_FOUNDER_DEV and _dev_scoring_declared(data))
    ):
        return "scoring_active" if _is_dev_mode() else "reported_not_scored"
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
        version_pin = (
            load_probe_set_version(path)
            if "probe_set_version" in reproducibility
            else f"{benchmark}@{version}"
        )
        k_replicates = int(reproducibility.get("n_runs") or 1)
        surface_seed_rotation_enabled = bool(data.get("surface_seed_rotation_enabled", False))
        canary_registration = str(data.get("canary_registration") or "")
        applicability_gate = (
            dict(data.get("applicability_gate"))
            if isinstance(data.get("applicability_gate"), dict)
            else {}
        )
        runner, case_class, executable = _classify_runner(path.name, benchmark)
        direct_family = data.get("runner") == "harness.direct_family_adapter"
        if direct_family:
            runner, case_class, executable = (
                "direct_family_adapter",
                "direct_behavioral_probe",
                True,
            )
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
                    version_pin=version_pin,
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
                    attestation_tier=_attestation_tier(data),
                )
            )
        activation_blocker = data.get("activation_blocker")
        probes = (
            data.get("probes")
            or _lane_a_probes(data)
            or _direct_family_probes(data)
        )
        for probe in probes:
            probe_id = probe.get("probe_id") or probe.get("id")
            if not probe_id:
                continue
            execution_evidenced, evidence_status = _execution_evidence_status(
                probe,
                data,
                allow_external_evidence=(
                    mapping_dir.resolve() == MAPPING_DIR.resolve()
                    and (benchmark, str(probe_id))
                    not in BEAAA_21704_RETAINED_RUNTIME_CASES
                ),
            )
            materializable = executable and _case_has_bound_execution(benchmark, probe)
            target_bound = execution_evidenced or materializable
            if materializable and not execution_evidenced:
                evidence_status = "target_bound_evidence_pending"
            # Historical/externally captured evidence proves that a case ran
            # somewhere; it does not prove this production executor can run it.
            # Only advertise rows that the registered-case adapter can
            # materialize and dispatch. Otherwise full-scan reconciliation
            # promotes the row into the required set and acceptance can never
            # complete (the executor fails with ``adapter not bound``).
            executable_for_count = (
                executable
                and target_bound
                and execution_evidenced
            )
            if direct_family:
                # These rows are executable because the registered adapter can
                # materialize and grade the checked-in synthetic case.  Prior
                # execution evidence remains false until a scan actually runs.
                target_bound = materializable
                execution_evidenced = False
                evidence_status = "runtime_bound_evidence_pending"
                executable_for_count = executable and target_bound
            if _is_lane_c_family(benchmark):
                # BEAAA-23860: for lane C, target_bound is derived only from
                # captured target identity/class plus run provenance, and the row
                # is executable only when the registered runner can actually run
                # it. Mapping membership alone is not enough for either flag.
                target_bound = execution_evidenced and _has_lane_c_run_provenance(
                    probe, data
                )
                if not target_bound:
                    execution_evidenced = False
                    evidence_status = "lane_c_evidence_not_target_derived"
                executable_for_count = (
                    executable
                    and target_bound
                    and execution_evidenced
                    and _lane_c_runner_bound(benchmark, probe_id)
                )
                if not executable_for_count and evidence_status == (
                    "target_bound_and_evidenced"
                ):
                    evidence_status = "lane_c_registered_runner_not_bound"
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
                        version_pin=version_pin,
                        expected_result=(
                            "deterministic synthetic boundary verdict; reported-not-scored"
                            if direct_family
                            else "deterministic verdict mapped to a rubric raw score"
                        ),
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
                        k_replicates=(
                            int(probe.get("replicate_count") or k_replicates)
                            if direct_family
                            else k_replicates
                        ),
                        surface_seed_rotation_enabled=surface_seed_rotation_enabled,
                        canary_registration=canary_registration,
                        applicability_gate=applicability_gate,
                        execution_mode=(
                            "synthetic_direct_family_adapter"
                            if direct_family
                            else None
                        ),
                        attestation_tier=_attestation_tier(data),
                    )
                )
    reconciled = _reconcile_runtime_identities([asdict(row) for row in rows])
    evidence_records = (
        json.loads(EXTERNAL_EVIDENCE_PATH.read_text(encoding="utf-8"))
        if EXTERNAL_EVIDENCE_PATH.exists()
        else {}
    )
    if len(evidence_records) == len(BEAAA_21704_REPORTED_NOT_SCORED_CASES):
        for row in reconciled:
            key = (str(row.get("test_id") or ""), str(row.get("case_id") or ""))
            if key not in BEAAA_21704_REPORTED_NOT_SCORED_CASES:
                continue
            row["scoring_active"] = False
            row["reporting_status"] = "reported_not_scored"
            if f"{key[0]}:{key[1]}" in evidence_records:
                row["external_execution_evidenced"] = True
                if key in BEAAA_21704_RETAINED_RUNTIME_CASES:
                    continue
                row["production_runner_executable"] = True
                row["execution_evidenced"] = True
                row["execution_evidence_status"] = "target_bound_and_evidenced"
                row["activation_blocker"] = None
    if (
        include_content_candidates
        and mapping_dir.resolve() == MAPPING_DIR.resolve()
    ):
        reconciled = _merge_content_candidate_inventory(reconciled)
    if mapping_dir.resolve() == MAPPING_DIR.resolve():
        reconciled = _merge_authorized_tau_support_crm(reconciled)
    return reconciled


def _merge_authorized_tau_support_crm(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Expose the three authorized report-only TAU cases to production dispatch."""
    manifest = json.loads(AUTHORITATIVE_REGISTRY_PATH.read_text(encoding="utf-8"))
    authorized = [
        row
        for row in manifest.get("rows", ())
        if row.get("bucket") == "report_only"
        and row.get("corpus_or_family") == "tau_support_crm_pilot"
        and row.get("framework") == "tau-bench"
        and row.get("source") == "evals/provisional_tau_support_crm.py"
        and row.get("scoring_active") is False
    ]
    if len(authorized) != 3:
        raise ValueError("authorized TAU support/CRM registry must contain exactly 3 cases")

    authorized_ids = {str(row["stable_id"]) for row in authorized}
    for registry_path in (AUTHORITATIVE_REGISTRY_PATH, ACTIVE_REGISTRY_PATH):
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        runnable_ids = {
            str(row.get("case_id_or_probe_id"))
            for row in registry.get("rows", ())
            if row.get("bucket") == "runnable_today"
            and row.get("corpus_or_family") == "tau_support_crm"
            and row.get("framework") == "tau_bench"
            and row.get("default_runtime_executable") is True
            and row.get("scoring_active") is False
            and row.get("weight") == 0.0
            and row.get("score_contribution") is False
        }
        if runnable_ids != authorized_ids:
            raise ValueError(
                f"{registry_path.name} must declare exactly the 3 authorized "
                "TAU support/CRM cases as runnable_today"
            )

    existing = {
        (str(row.get("test_id") or ""), str(row.get("case_id") or ""))
        for row in rows
    }
    for source in sorted(authorized, key=lambda row: str(row["stable_id"])):
        key = ("tau_support_crm", str(source["stable_id"]))
        if key in existing:
            continue
        rows.append(
            {
                "test_id": key[0],
                "case_id": key[1],
                "status": "active",
                "pillar": "OPS",
                "category": "tau_support_crm",
                "risk_or_rubric_id": "UNSCORED.TAU.SUPPORT_CRM",
                "source_pack": str(source["source"]),
                "runner": "tau_bench",
                "framework": "tau-bench",
                "underwriting_trigger": "reported_not_scored",
                "required_or_optional": "required",
                "version_pin": (
                    "tau-bench@"
                    + str(source["framework_version"])
                    + ";tau-support-crm@0.1.0-report-only"
                ),
                "expected_result": "deterministic database-end-state verdict",
                "evidence_uri_template": "evidence://{scan_id}/{test_id}/{case_id}.json",
                "owner": "scanner_engineer",
                "last_verified_at": "2026-08-08",
                "case_class": "tau_support_crm_report_only",
                "production_runner_executable": True,
                "target_bound": True,
                "execution_evidenced": True,
                "execution_evidence_status": "authorized_report_only_tau_harness",
                "scoring_active": False,
                "reporting_status": "reported_not_scored",
                "activation_blocker": None,
                "k_replicates": 1,
                "surface_seed_rotation_enabled": False,
                "canary_registration": "",
                "applicability_gate": {"applicable": True},
                "execution_mode": "tau_support_crm_production_dispatch",
                "disposition": "report-only",
                "score_contribution": False,
                "weight": 0.0,
                "authoritative_registry_stable_id": str(source["stable_id"]),
            }
        )
    return rows


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
        if current is not None and (
            current.get("production_runner_executable") is True
            or _is_lane_c_family(family_id)
        ):
            # A lane-C mapping row is authoritative even when its provenance
            # guard makes it non-executable. Replacing that guarded row with the
            # permissive inventory registration would turn the fail-closed
            # result back into target_bound=true under the production-default
            # merge (BEAAA-21723 QA B4). The inventory may enrich the row's
            # metadata/identity, but may not replace its runner or guard flags.
            # For other families, an already-bound production mapping also wins.
            # The inventory still owns the row's distinct
            # -coverage identity: dropping it here would silently shrink the
            # distinct-case count by one row per graduated case, so carry it
            # across unchanged.
            current.update(candidate_metadata)
            if _is_lane_c_family(family_id) and not current.get(
                "runtime_case_identity"
            ):
                current["runtime_case_identity"] = candidate_runtime_identity(candidate)
                # ``canonical_runtime_case`` defaults to False on a mapping row,
                # so this has to be assigned rather than defaulted: the graduated
                # row *is* the one canonical carrier of that identity, and
                # leaving it False drops the canonical count by one per row.
                current["canonical_runtime_case"] = True
                current["alias_of_runtime_case_identity"] = None
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
    from tools.c10_family_activation import apply_c10_family_activations
    from tools.tranche1_observational import activate_registry_rows

    activated = apply_c10_family_activations(activate_registry_rows(rows))
    for row in activated:
        if row.get("test_id") in UNBOUND_REGISTERED_CASE_FAMILIES:
            row["production_runner_executable"] = False
            row["execution_evidence_status"] = (
                "registered_case_adapter_not_bound"
            )
    return activated


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


def is_canonical_scored_row(row: Mapping[str, Any]) -> bool:
    """Return whether a row belongs to the canonical scored-case inventory."""

    return (
        row.get("status") == "active"
        and row.get("canonical_runtime_case") is True
        and row.get("reporting_status") == "scored"
        and row.get("execution_evidenced") is True
        and row.get("target_bound") is True
    )


def count_canonical_scored_rows(rows: list[dict[str, Any]]) -> int:
    """Count canonical scored rows using the contract's five-predicate gate."""

    return sum(1 for row in rows if is_canonical_scored_row(row))


def _is_non_countable_reported_not_scored(row: Mapping[str, Any]) -> bool:
    if row.get("content_candidate_inventory") is True:
        return False
    return (
        (
            (str(row.get("test_id") or ""), str(row.get("case_id") or ""))
            in BEAAA_21704_REPORTED_NOT_SCORED_CASES
            or row.get("test_id") in (
            AIUC_PATH_B_REPORTED_NOT_SCORED_FAMILIES
            | AIUC_PATH_B_SCORING_ACTIVE_FAMILIES
            | AIUC_PATH_B_FOUNDER_DEV_SCORING_ACTIVE_FAMILIES
            | REPORTED_NOT_SCORED_COMPATIBILITY_FAMILIES
        )
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

    key = (str(row.get("test_id") or ""), str(row.get("case_id") or ""))
    if (
        _is_non_countable_reported_not_scored(row)
        and key not in BEAAA_21704_RETAINED_RUNTIME_CASES
    ):
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
    canonical_scored_rows = count_canonical_scored_rows(rows)
    target_bound_runtime = count_target_bound_runtime(rows)
    distinct = distinct_case_counts(rows)
    return {
        "registry_rows": len(rows),
        "active_executable_count": active_executable,
        "canonical_scored_row_count": canonical_scored_rows,
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


def production_boundary_failure(error: FounderDevModeError) -> dict[str, str]:
    """Return the stable fail-closed disposition used by registry CLI gates."""

    return {
        "status": "FAIL",
        "disposition": "production_boundary_refused",
        "error": str(error),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, help="write machine-readable count output")
    parser.add_argument("--registry-out", type=Path, help="write normalized registry rows")
    parser.add_argument("--check", action="store_true", help="fail on registry/count-gate violations")
    args = parser.parse_args()

    rows = build_registry()
    # Production boundary: the published active-test count is customer-visible.
    # Dev-promoted families are run and scored on the scanner server, which sets
    # BACKBOND_FOUNDER_DEV_MODE=1 (Founder directive, BEAAA-29856), so a live
    # dev-mode-scored family is expected state rather than a reason for the
    # report to refuse. --check remains the fail-closed publish gate; the plain
    # report discloses the condition on stderr and still emits the count.
    try:
        assert_production_attested(rows)
    except FounderDevModeError as error:
        if args.check:
            print(json.dumps(production_boundary_failure(error), indent=2, sort_keys=True))
            return 1
        print(f"WARN: {error}", file=sys.stderr)
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
