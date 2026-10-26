"""Offline Path C preparation; never exports source data or creates AWS resources.

A customer administrator obtains DescribeAgent. Review that private export for
secrets/PHI before using it. Real spaces/connectors are replaced explicitly;
production permissions, connectors and data are never cloned automatically.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from harness.amazon_quick import _sha


def prepare(export, review):
    encoded = json.dumps(export, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    if (review.get("schema") != "backbond-quick-copy-review/v1.2" or review.get("approved") is not True
            or review.get("identity_path") != "C" or review.get("export_sha256") != _sha(encoded)
            or review.get("no_real_patient_staff_or_secrets") is not True
            or not review.get("customer_authorization_ref") or not review.get("reviewed_by")
            or not re.fullmatch(r"\d{12}", review.get("sandbox_account_id", ""))
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", review.get("sandbox_agent_id", ""))):
        raise ValueError("approved_export_bound_path_c_review_required")
    agent = export["Agent"]
    arn = agent["Arn"]
    source_account = arn.split(":")[4]
    if source_account == review["sandbox_account_id"] and review.get("own_synthetic_baseline") is not True:
        raise ValueError("separate_sandbox_account_required")
    original_spaces = agent.get("Spaces", [])
    mappings = review.get("synthetic_space_mapping", {})
    if set(mappings) != set(original_spaces) or not all(
        isinstance(v, str) and re.fullmatch(r"arn:aws:quicksight:[a-z0-9-]+:" + review["sandbox_account_id"] + r":space/[A-Za-z0-9_.+-]+", v)
        for v in mappings.values()):
        raise ValueError("complete_synthetic_space_replacement_required")
    regions = {v.split(":")[3] for v in mappings.values()}
    sandbox_region = review.get("sandbox_region", next(iter(regions), arn.split(":")[3]))
    if not re.fullmatch(r"[a-z]{2}-[a-z]+-\d", sandbox_region) or regions - {sandbox_region}:
        raise ValueError("same_region_synthetic_spaces_required")
    # No connection endpoints, source document bodies, credentials, Creator,
    # QbsAwsAccountId, subscription identifiers or production source ARNs enter
    # the CreateAgent request. Administrative review is required for free text.
    custom = agent.get("CustomPromptInterface", {})
    allowed = ("Identity", "CustomInstructions", "OutputStyle", "ResponseLength", "Tone")
    prompt = {key: custom[key] for key in allowed if key in custom}
    if not prompt.get("CustomInstructions"):
        raise ValueError("reviewed_custom_instructions_required")
    request = {"AwsAccountId": review["sandbox_account_id"], "AgentId": review["sandbox_agent_id"],
               "Name": review.get("sandbox_name", "BackBond synthetic configuration copy"),
               "Description": "SYNTHETIC CONFIGURATION COPY. Production grants, RLS, SSO and real connected sources are not tested.",
               "Spaces": list(dict.fromkeys(mappings.values())), "ActionConnectors": [],
               "StarterPrompts": agent.get("StarterPrompts", []), "WelcomeMessage": agent.get("WelcomeMessage", ""),
               "CustomPromptInput": {"NewPrompt": prompt}}
    differences = ["production_spaces_replaced_with_synthetic_spaces", "real_action_connectors_disabled",
                   "sandbox_native_test_identities_and_grants", "synthetic_row_rules_recreated",
                   "production_identity_and_device_policies_not_exercised"]
    if custom.get("ModelProfileId"):
        differences.append("model_profile_requires_manual_equivalence_review")
    receipt = {"schema": "backbond-quick-configuration-copy/v1.2", "identity_path": "C",
               "assessment_subject": "configuration_copy", "source_export_sha256": _sha(encoded),
               "source_account_sha256": _sha(source_account), "source_agent_sha256": _sha(agent["AgentId"]),
               "sandbox_account_sha256": _sha(review["sandbox_account_id"]),
               "sandbox_agent_sha256": _sha(review["sandbox_agent_id"]), "sandbox_region": sandbox_region,
               "copied_instructions_sha256": _sha(prompt["CustomInstructions"]),
               "sandbox_request_sha256": _sha(json.dumps(request, sort_keys=True, ensure_ascii=False)),
               "differences": differences, "customer_authorization_ref": review["customer_authorization_ref"],
               "reviewed_by": review["reviewed_by"], "fidelity_attestation_required_after_creation": True,
               "backbond_score": "N/A", "target_calls": 0, "judge_calls": 0}
    return request, receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.export.stat().st_size > 1024 * 1024 or args.review.stat().st_size > 1024 * 1024:
            raise ValueError
        request, receipt = prepare(json.loads(args.export.read_text(encoding="utf-8")),
                                   json.loads(args.review.read_text(encoding="utf-8")))
        args.output.mkdir(parents=True, exist_ok=False)
        for name, value in (("create-agent.private.json", request), ("configuration-copy-receipt.json", receipt)):
            with (args.output / name).open("x", encoding="utf-8") as stream:
                json.dump(value, stream, indent=2, ensure_ascii=False)
        print(json.dumps({"status": "configuration_copy_plan_only", "target_calls": 0, "judge_calls": 0}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print(json.dumps({"status": "incomplete", "error": "reviewed_export_and_new_output_required"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
