"""Offline Path C preparation; never exports source data or creates AWS resources.

A customer administrator obtains DescribeAgent. Review that private export for
secrets/PHI before using it. Real spaces/connectors are replaced explicitly;
production permissions, connectors and data are never cloned automatically.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from harness.amazon_quick import _sha

PROMPT_FIELDS = ("Identity", "CustomInstructions", "OutputStyle", "ResponseLength", "Tone")


def digest(value):
    return _sha(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False))


def reference(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 1024 and not value.startswith("REPLACE")


def agent_identity(agent):
    match = re.fullmatch(r"arn:aws:quicksight:([a-z]{2}-[a-z]+-[0-9]):([0-9]{12}):agent/([0-9a-zA-Z_.+-]{1,256})",
                         agent.get("Arn", ""))
    if not match or match[3] != agent.get("AgentId"):
        raise ValueError("consistent_agent_arn_and_id_required")
    return match[1], match[2], match[3]


def prompt_fields(agent):
    custom = agent.get("CustomPromptInterface", {})
    if not isinstance(custom, dict):
        raise ValueError("prompt_interface_object_required")
    prompt = {}
    for key in PROMPT_FIELDS:
        if key not in custom or custom[key] == "":
            continue  # AWS optional empty fields are omitted, never invented.
        value = custom[key]
        if not isinstance(value, str) or not 5 <= len(value) <= 350000:
            raise ValueError("valid_optional_prompt_text_required")
        prompt[key] = value
    return prompt


def prepare(export, review):
    if (review.get("schema") != "backbond-quick-copy-review/v1.2" or review.get("approved") is not True
            or review.get("identity_path") != "C" or review.get("export_sha256") != digest(export)
            or review.get("no_real_patient_staff_or_secrets") is not True
            or not reference(review.get("customer_authorization_ref")) or not reference(review.get("reviewed_by"))
            or not re.fullmatch(r"[0-9]{12}", review.get("sandbox_account_id", ""))
            or not re.fullmatch(r"[0-9a-zA-Z_.+-]{1,256}", review.get("sandbox_agent_id", ""))):
        raise ValueError("approved_export_bound_path_c_review_required")
    agent = export["Agent"]
    source_region, source_account, _ = agent_identity(agent)
    if source_account == review["sandbox_account_id"] and review.get("own_synthetic_baseline") is not True:
        raise ValueError("separate_sandbox_account_required")
    original_spaces = agent.get("Spaces", [])
    if (not isinstance(original_spaces, list) or len(original_spaces) > 10
            or any(not isinstance(v, str) or not v for v in original_spaces)
            or len(set(original_spaces)) != len(original_spaces)):
        raise ValueError("distinct_source_spaces_required")
    mappings = review.get("synthetic_space_mapping", {})
    if not isinstance(mappings, dict) or set(mappings) != set(original_spaces) or not all(
        isinstance(v, str) and re.fullmatch(r"arn:aws:quicksight:[a-z0-9-]+:" + review["sandbox_account_id"] + r":space/[A-Za-z0-9_.+-]+", v)
        for v in mappings.values()):
        raise ValueError("complete_synthetic_space_replacement_required")
    mapped = list(dict.fromkeys(mappings.values()))
    additional = review.get("additional_synthetic_spaces", [])
    if (not isinstance(additional, list) or any(not isinstance(v, str) or not re.fullmatch(
            r"arn:aws:quicksight:[a-z0-9-]+:" + review["sandbox_account_id"] + r":space/[A-Za-z0-9_.+-]+", v)
            for v in additional) or len(set(additional)) != len(additional)
            or set(additional) & set(mapped) or len(mapped) + len(additional) > 10):
        raise ValueError("explicit_distinct_synthetic_fixture_spaces_required")
    spaces = mapped + additional
    regions = {v.split(":")[3] for v in spaces}
    sandbox_region = review.get("sandbox_region", next(iter(regions), source_region))
    if not re.fullmatch(r"[a-z]{2}-[a-z]+-[0-9]", sandbox_region) or regions - {sandbox_region}:
        raise ValueError("same_region_synthetic_spaces_required")
    # No connection endpoints, source document bodies, credentials, Creator,
    # QbsAwsAccountId, subscription identifiers or production source ARNs enter
    # the CreateAgent request. Administrative review is required for free text.
    prompt = prompt_fields(agent)
    name = review.get("sandbox_name", "BackBond synthetic configuration copy")
    starter = agent.get("StarterPrompts", [])
    welcome = agent.get("WelcomeMessage", "")
    if (not isinstance(name, str) or not re.fullmatch(r"(?!\s*$).{1,50}", name)
            or not isinstance(starter, list) or len(starter) > 3
            or any(not isinstance(v, str) or len(v) > 100 for v in starter)
            or not isinstance(welcome, str) or len(welcome) > 300):
        raise ValueError("valid_agent_presentation_fields_required")
    request = {"AwsAccountId": review["sandbox_account_id"], "AgentId": review["sandbox_agent_id"],
               "Name": name,
               "Description": "SYNTHETIC CONFIGURATION COPY. Production grants, RLS, SSO and real connected sources are not tested.",
               "Spaces": spaces, "ActionConnectors": [],
               "StarterPrompts": starter, "WelcomeMessage": welcome}
    if prompt:
        request["CustomPromptInput"] = {"NewPrompt": prompt}
    differences = ["production_spaces_replaced_with_synthetic_spaces", "real_action_connectors_disabled",
                   "sandbox_native_test_identities_and_grants", "synthetic_row_rules_recreated",
                   "production_identity_and_device_policies_not_exercised"]
    if agent.get("CustomPromptInterface", {}).get("ModelProfileId"):
        differences.append("model_profile_requires_manual_equivalence_review")
    if additional:
        differences.append("additional_synthetic_spaces_for_approved_test_fixture")
    receipt = {"schema": "backbond-quick-configuration-copy/v1.2", "identity_path": "C",
               "assessment_subject": "configuration_copy", "source_export_sha256": digest(export),
               "source_account_sha256": _sha(source_account), "source_agent_sha256": _sha(agent["AgentId"]),
               "sandbox_account_sha256": _sha(review["sandbox_account_id"]),
               "sandbox_agent_sha256": _sha(review["sandbox_agent_id"]), "sandbox_region": sandbox_region,
               "copied_instructions_sha256": _sha(prompt["CustomInstructions"]) if "CustomInstructions" in prompt else None,
               "copied_prompt_fields_sha256": {key: _sha(value) for key, value in prompt.items()},
               "prompt_configuration_mode": "new_prompt_fields" if prompt else "service_default",
               "additional_synthetic_space_count": len(additional),
               "sandbox_request_sha256": digest(request),
               "differences": differences, "customer_authorization_ref": review["customer_authorization_ref"],
               "reviewed_by": review["reviewed_by"], "fidelity_attestation_required_after_creation": True,
               "backbond_score": "N/A", "target_calls": 0, "judge_calls": 0}
    return request, receipt


def attest(created_export, request, receipt, review):
    """Compare an administrator's readback with the approved plan, offline only.

    Source grants/model equivalence still require the administrator's explicit
    review. A copy's readback cannot prove production permissions or SSO.
    """
    if (receipt.get("schema") != "backbond-quick-configuration-copy/v1.2"
            or receipt.get("identity_path") != "C" or receipt.get("fidelity_attestation_approved") is True
            or any(not isinstance(receipt.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", receipt[key]) for key in
                   ("source_export_sha256", "source_account_sha256", "source_agent_sha256"))
            or digest(request) != receipt.get("sandbox_request_sha256")
            or review.get("schema") != "backbond-quick-copy-attestation/v1.2"
            or review.get("approved") is not True
            or review.get("created_export_sha256") != digest(created_export)
            or review.get("prepared_receipt_sha256") != digest(receipt)
            or review.get("customer_authorization_ref") != receipt.get("customer_authorization_ref")
            or not reference(review.get("reviewed_by")) or not reference(review.get("fidelity_attestation_ref"))
            or not reference(review.get("source_permissions_review_ref"))
            or review.get("configuration_differences_reviewed") is not True
            or review.get("model_profile_equivalence_reviewed") is not True):
        raise ValueError("exact_readback_bound_administrator_attestation_required")
    agent = created_export["Agent"]
    region, account, agent_id = agent_identity(agent)
    if (region != receipt.get("sandbox_region") or _sha(account) != receipt.get("sandbox_account_sha256")
            or _sha(agent_id) != receipt.get("sandbox_agent_sha256")
            or account != request.get("AwsAccountId") or agent_id != request.get("AgentId")
            or agent.get("AgentStatus") != "ACTIVE" or agent.get("AgentLifecycle") != "PUBLISHED"):
        raise ValueError("active_published_exact_sandbox_readback_required")
    spaces = request.get("Spaces")
    if (not isinstance(spaces, list) or len(spaces) > 10
            or any(not isinstance(value, str) or not re.fullmatch(
                r"arn:aws:quicksight:" + re.escape(region) + ":" + account + r":space/[A-Za-z0-9_.+-]+", value)
                for value in spaces) or len(set(spaces)) != len(spaces)
            or request.get("ActionConnectors") != [] or agent.get("ActionConnectors", []) != []
            or sorted(agent.get("Spaces", [])) != sorted(request["Spaces"])
            or agent.get("Name") != request["Name"]
            or agent.get("Description", "") != request["Description"]
            or agent.get("StarterPrompts", []) != request["StarterPrompts"]
            or agent.get("WelcomeMessage", "") != request["WelcomeMessage"]):
        raise ValueError("created_agent_configuration_differs_from_reviewed_request")
    expected = request.get("CustomPromptInput", {}).get("NewPrompt", {})
    if (set(request.get("CustomPromptInput", {})) - {"NewPrompt"}
            or {k: _sha(v) for k, v in expected.items()} != receipt.get("copied_prompt_fields_sha256")
            or receipt.get("copied_instructions_sha256") != (_sha(expected["CustomInstructions"]) if "CustomInstructions" in expected else None)
            or receipt.get("prompt_configuration_mode") != ("new_prompt_fields" if expected else "service_default")):
        raise ValueError("prepared_prompt_binding_required")
    actual = prompt_fields(agent)
    if any(actual.get(key) != value for key, value in expected.items()):
        raise ValueError("created_prompt_differs_from_reviewed_request")
    added = sorted(set(actual) - set(expected))
    if added and review.get("additional_default_prompt_fields_reviewed") is not True:
        raise ValueError("service_populated_prompt_fields_require_administrator_review")
    return {**receipt, "fidelity_attestation_approved": True,
            "fidelity_attestation_ref": review["fidelity_attestation_ref"],
            "fidelity_reviewed_by": review["reviewed_by"],
            "fidelity_verified_at": datetime.now(timezone.utc).isoformat(),
            "fidelity_attestation_review_sha256": digest(review),
            "fidelity_verified_export_sha256": digest(created_export),
            "fidelity_prepared_receipt_sha256": digest(receipt),
            "fidelity_verified_request_sha256": digest(request),
            "fidelity_agent_status": "ACTIVE", "fidelity_agent_lifecycle": "PUBLISHED",
            "fidelity_additional_prompt_fields": added,
            "source_permissions_review_ref": review["source_permissions_review_ref"],
            "model_profile_equivalence_reviewed": True, "target_calls": 0, "judge_calls": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "attest"), nargs="?", default="prepare")
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        inputs = [args.export, args.review]
        if args.operation == "attest":
            if args.request is None or args.receipt is None:
                raise ValueError("prepared_request_and_receipt_required")
            inputs += [args.request, args.receipt]
        elif args.request is not None or args.receipt is not None:
            raise ValueError("attestation_inputs_only_for_attest_operation")
        if any(path.stat().st_size > 8 * 1024 * 1024 for path in inputs):
            raise ValueError
        values = [json.loads(path.read_text(encoding="utf-8")) for path in inputs]
        if args.operation == "prepare":
            request, receipt = prepare(*values)
            files = (("create-agent.private.json", request), ("configuration-copy-receipt.json", receipt))
        else:
            receipt = attest(values[0], values[2], values[3], values[1])
            files = (("configuration-copy-receipt.json", receipt),)
        args.output.mkdir(parents=True, exist_ok=False)
        for name, value in files:
            with (args.output / name).open("x", encoding="utf-8") as stream:
                json.dump(value, stream, indent=2, ensure_ascii=False)
        print(json.dumps({"status": "configuration_copy_plan_only" if args.operation == "prepare" else
                         "administrator_readback_attested_offline", "target_calls": 0, "judge_calls": 0}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print(json.dumps({"status": "incomplete", "error": "reviewed_export_and_new_output_required"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
