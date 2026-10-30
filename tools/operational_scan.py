"""Conditional standard-scan adapters for the existing operational probes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.assessment_test_catalog import FAMILIES, RECORD_FAMILIES, LOG_CHALLENGE_FAMILY, SYNTHETIC_FAMILIES, SYNTHETIC_DIALOGUE_CASES, SYNTHETIC_PRECISION_CASES, SYNTHETIC_EXPOSURE_CASES


def registry_rows() -> list[dict[str, Any]]:
    # Registration reads the existing catalog without loading live probe code.
    # Bounded registry generators do not need session/stop runtime dependencies.
    path = Path(__file__).resolve().parents[1] / "rubric" / "requirement_registry.v1.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))["test_catalog"]

    return [
        {"test_id": family, "case_id": case, "status": "active", "pillar": "OPS",
         "category": family, "risk_or_rubric_id": "UNSCORED." + family.upper(),
         "source_pack": ("evals/oss_pilots/synthetic_exposure.py" if family in SYNTHETIC_EXPOSURE_CASES else
                         "evals/oss_pilots/synthetic_precision.py" if family in SYNTHETIC_PRECISION_CASES else
                         "evals/oss_pilots/synthetic_dialogue.py" if family in SYNTHETIC_DIALOGUE_CASES else
                         "evals/oss_pilots/synthetic_assurance.py" if family in SYNTHETIC_FAMILIES else
                         "evals/oss_pilots/assessment_records.py" if family in RECORD_FAMILIES else
                         "evals/oss_pilots/log_challenges.py" if family == LOG_CHALLENGE_FAMILY else
                         "evals/oss_pilots/log_quality.py" if family == "operational_logging_quality" else
                         "evals/oss_pilots/operational_controls.py"),
         "runner": "operational_controls", "framework": "backbond-operational",
         "underwriting_trigger": "reported_not_scored", "required_or_optional": "conditional",
         "expected_result": "actual synthetic-task response/attempt reconciliation" if family in SYNTHETIC_FAMILIES else "reviewed record reconciliation" if family in RECORD_FAMILIES else "scanner-owned operational receipt or scoped log challenge",
         "evidence_uri_template": "evidence://{scan_id}/{test_id}/{case_id}.json",
         # Source contract review date; execution_evidenced remains false.
         "owner": "scanner_engineer", "last_verified_at": "2026-10-30" if family in SYNTHETIC_PRECISION_CASES or family in SYNTHETIC_EXPOSURE_CASES else "2026-10-29" if family in SYNTHETIC_FAMILIES else "2026-10-28",
         "activation_blocker": None, "surface_seed_rotation_enabled": False,
         "canary_registration": "", "runtime_case_identity": case, "source_aliases": [],
         "version_pin": catalog[family]["probe_set_version"], "production_runner_executable": True,
         "target_bound": True, "execution_evidenced": False,
         "execution_evidence_status": "per_scan_capture_required",
         "report_only_depth_countable": family in SYNTHETIC_FAMILIES or family == "operational_oversight",
         "case_class": "operational_report_only", "scoring_active": False,
         "reporting_status": "reported_not_scored", "disposition": "report-only",
         "score_contribution": False, "weight": 0.0, "k_replicates": 1,
         "canonical_runtime_case": False, "execution_mode": "conditional_operational",
         "applicability_gate": {"requires": "reviewed_scoped_records" if family in RECORD_FAMILIES else
                                "scanner_mediated_tool_session" if family == "operational_oversight" or family in SYNTHETIC_FAMILIES else "trusted_scoped_log_capture"}}
        for family in sorted(FAMILIES)
        for case in catalog[family]["case_ids"]
    ]


def select_rows(rows, supported_families=()):
    """Do not expand fixed packs; the full-registry caller supplies capabilities."""
    supported = set(supported_families)
    return tuple(row for row in rows if row.get("test_id") not in FAMILIES
                 or row.get("test_id") in supported)


def http_session_factory(endpoint, secret, config, *, reserve, complete):
    """Only the explicitly configured OpenAI tool transport is supported.

    Provider base URLs/text transports cannot be used as native tool sessions.
    The shared egress policy and scan ledger guard every actual HTTP request.
    """
    if not isinstance(config, dict) or config.get("transport") != "openai_chat" or not config.get("model"):
        return None
    auth = config.get("auth") or {"type": "bearer"}
    if auth.get("type", "bearer") not in {"bearer", "none", "custom_header"}:
        return None
    from harness.customer_runner import _CustomerAgentHttpRunner, customer_agent_http_runner_factory
    prototype = customer_agent_http_runner_factory(endpoint, secret, endpoint_config=config)
    if type(prototype) is not _CustomerAgentHttpRunner:
        return None

    def factory(setup):
        from harness.agent_interface import HTTPAgentSession
        from types import SimpleNamespace
        runner = customer_agent_http_runner_factory(endpoint, secret, endpoint_config=config)
        # This is a fresh per-case transport: message history cannot race with
        # other registry workers. Reuse its scoped SSRF/redirect, streaming size,
        # content-type, compression and authentication guards on every send.
        runner.bind_transport_attempt_ledger(reserve, complete)

        def poster(url, *, headers, json, **kwargs):
            message = runner.complete_messages(
                json["messages"], tools=json.get("tools", []),
                max_output_tokens=256, temperature=0,
            )
            return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": message}]})

        # Preserve the customer's system instruction alongside the inert test policy.
        system = "\n\n".join(p for p in (config.get("system_prompt"), setup.system_prompt) if p)
        return HTTPAgentSession(url=endpoint, model=config["model"], tools=setup.tools,
                                system_prompt=system, poster=poster, timeout_s=30,
                                label="customer-operational-tool-session")
    return factory
