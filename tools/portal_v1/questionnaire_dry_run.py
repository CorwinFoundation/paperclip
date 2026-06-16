"""External dry-run for BEAAA-4919 acceptance check.

Runs the full questionnaire flow end-to-end against a freshly-bootstrapped
PortalService:
 1. Issue invite + accept invite + enroll connector + trigger scan +
    submit one envelope (to give the prefill resolver real evidence).
 2. Start a governance questionnaire draft (prefill from the scan).
 3. Upload one evidence artifact for a representative question.
 4. Submit the questionnaire with realistic but synthetic answers.
 5. Write the submitted payload, the printable PDF, and the scanner-ready
    evidence bundle into the target output directory.

Usage:
    python3 questionnaire_dry_run.py --out /tmp/beaaa4919_dryrun

Prints a JSON summary on stdout with paths + SHA-256 digests so the
calling shell can attach the artifacts to a Paperclip work product.
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import shutil
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

import portal  # noqa: E402 — wires questionnaire methods at import


SAMPLE_RETENTION_POLICY = (
    "Sample retention policy for dry-run.\n"
    "- prompt_in: 30 days\n"
    "- model_response: 30 days\n"
    "- tool_call: 90 days\n"
    "- tool_response: 90 days\n"
).encode("utf-8")


def _make_signing_key() -> tuple[ed25519.Ed25519PrivateKey, str]:
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")
    return key, pem


def seed_completed_scan(service: portal.PortalService) -> str:
    signing_key, pem = _make_signing_key()
    invite = service.create_invite("Demo Vendor (dry-run)", "owner@example.com", "call-center")
    dp = service.accept_invite(invite["invite_token"], "https://staging.example.invalid/agent", "dp-cert-ref", "staging")
    service.enroll_connector({
        "invite_token": invite["invite_token"],
        "dp_org_name": "Demo Vendor (dry-run)",
        "connector_version": "0.1.0",
        "ephemeral_public_key": {"alg": "x25519", "key": portal.b64url(secrets.token_bytes(32))},
        "key_attestation": {"type": "software", "quote": None},
        "signing_public_key_pem": pem,
    })
    scan = service.trigger_scan(dp["dp_id"])
    while True:
        status, task = service.next_task(scan["scan_id"], None)
        if status == 204:
            break
        envelope = {
            "envelope_version": portal.ENVELOPE_VERSION,
            "scan_id": scan["scan_id"],
            "task_id": task["task_id"],
            "connector_version": "0.1.0",
            "redaction_policy_version": portal.REDACTION_POLICY_VERSION,
            "timestamp": portal.now_iso(),
            "endpoint_called": {
                "url_hash": portal.sha256_hex("https://staging.example.invalid/agent"),
                "auth_method_hash": portal.sha256_hex("mtls"),
            },
            "assertions": {
                "response_received": True,
                "response_length_bucket": "<1k",
                "tool_called": True,
                "schema_match": "pass",
                "refusal_signal": False,
                "hallucination_signal": None,
            },
            "redacted_excerpts": [],
            "signature_chain": [],
        }
        signed = {k: v for k, v in envelope.items() if k not in {"signature", "signature_chain"}}
        envelope["signature"] = portal.b64url(signing_key.sign(portal.canonical_json(signed)))
        service.ingest_envelope(scan["scan_id"], envelope)
    return dp["dp_id"]


def build_dry_run_answers() -> dict[str, dict]:
    """Construct one self-consistent answer for every control_id."""
    return {
        "P4.1.Q01": {"value": {"logged_surfaces": ["prompt_in", "model_response", "tool_call", "tool_response", "user_id_or_session"]}, "evidence_state": "reviewed"},
        "P4.1.Q02": {"value": {"retention_days_by_surface": {"prompt_in": 30, "model_response": 30, "tool_call": 90, "tool_response": 90, "user_id_or_session": 365}}, "evidence_state": "reviewed"},
        "P4.1.Q03": {"value": {"storage_mode": "append_only"}, "evidence_state": "reviewed"},
        "P4.1.Q04": {"value": {"tamper_evidence": "hash_chain"}, "evidence_state": "reviewed"},
        "P4.1.Q05": {"value": {"pii_handling": "redacted_mirror"}, "evidence_state": "reviewed"},
        "P4.2.Q01": {"value": {"version_registry": "internal_git", "registry_link": "https://example.invalid/internal-registry"}, "evidence_state": "reviewed"},
        "P4.2.Q02": {"value": {"pin_policy": "pinned_per_release"}, "evidence_state": "reviewed"},
        "P4.2.Q03": {"value": {"prompt_change_control": "pr_review_required"}, "evidence_state": "reviewed"},
        "P4.2.Q04": {"value": {"model_change_control": "pr_review_required"}, "evidence_state": "reviewed"},
        "P4.2.Q05": {"value": {
            "rollback_to_prior_supported": True,
            "last_3_releases": [
                {"release_id": "rel-2026-05-30", "deployed_at": "2026-05-30", "model_id": "demo-llm-v3", "prompt_sha256": "a" * 64},
                {"release_id": "rel-2026-05-15", "deployed_at": "2026-05-15", "model_id": "demo-llm-v3", "prompt_sha256": "b" * 64},
                {"release_id": "rel-2026-05-01", "deployed_at": "2026-05-01", "model_id": "demo-llm-v2", "prompt_sha256": "c" * 64},
            ],
        }, "evidence_state": "reviewed"},
        "P4.3.Q01": {"value": {"kill_switch_mechanism": "feature_flag", "runbook_link": "https://example.invalid/runbook"}, "evidence_state": "reviewed"},
        "P4.3.Q02": {"value": {"time_to_disable_minutes": 5}, "evidence_state": "reviewed"},
        "P4.3.Q03": {"value": {"rollback_time_minutes": 15}, "evidence_state": "reviewed"},
        "P4.3.Q04": {"value": {"last_drill_at": "2026-04-20", "drill_cadence_days": 90}, "evidence_state": "incomplete"},
        "P4.3.Q05": {"value": {"approval_required_to_invoke": "single_approver"}, "evidence_state": "reviewed"},
        "P4.4.Q01": {"value": {"playbook_exists": True}, "evidence_state": "reviewed"},
        "P4.4.Q02": {"value": {"scope_covered": ["prompt_injection", "hallucinated_action", "data_leak", "tool_misuse", "wrong_recipient"]}, "evidence_state": "reviewed"},
        "P4.4.Q03": {"value": {"escalation_chain": [
            {"role": "On-call engineer", "sla_minutes": 15},
            {"role": "Service owner", "sla_minutes": 30},
            {"role": "Director of engineering", "sla_minutes": 60},
        ]}, "evidence_state": "reviewed"},
        "P4.4.Q04": {"value": {"external_notification_policy": "customer_within_24h"}, "evidence_state": "reviewed"},
        "P4.4.Q05": {"value": {"last_revision_at": "2026-05-01", "last_tabletop_at": "2026-04-15"}, "evidence_state": "incomplete"},
        "P4.5.Q01": {"value": {"eval_coverage_ledger_id": "eval-ledger-2026Q2", "submitted_at": "2026-06-01", "customer_confirmed": True}, "evidence_state": "reviewed"},
        "P4.5.Q02": {"value": {"policy_state": "implemented_with_evidence", "policy_doc_link": "https://example.invalid/policy/eval-cadence"}, "evidence_state": "reviewed"},
        "P4.5.Q03": {"value": {"forced_rerun_events": ["model_change", "prompt_change", "tool_change", "incident"], "attestation_statement": "Re-run forced within 24h after any of the above events; confirmed by release log."}, "evidence_state": "reviewed"},
        "P4.5.Q04": {"value": {"review_role": "Head of ML Engineering", "review_named_owner": "Alex Doe", "attestation_statement": "Owner reviews failed eval results before any production release."}, "evidence_state": "reviewed"},
        "P4.6.Q01": {"value": {"person": "Sam Reyes", "accountable_decisions": "Final go/no-go on production deployment of the agent."}, "evidence_state": "reviewed"},
        "P4.6.Q02": {"value": {"person": "Jamie Patel", "accountable_decisions": "Release approval, rollback decisions."}, "evidence_state": "reviewed"},
        "P4.6.Q03": {"value": {"person": "Pat Hwang", "accountable_decisions": "Approves prompt and model-route changes."}, "evidence_state": "reviewed"},
        "P4.6.Q04": {"value": {"person": "On-call rotation (PagerDuty)", "accountable_decisions": "Triage and containment of live incidents."}, "evidence_state": "reviewed"},
        "P4.6.Q05": {"value": {"person": "Robin Kim", "accountable_decisions": "Privacy, regulatory response, and audit evidence."}, "evidence_state": "reviewed"},
        "P4.6.Q06": {"value": {"succession_policy": "formal_succession", "review_cadence_days": 90, "last_reviewed_at": "2026-05-15"}, "evidence_state": "reviewed"},
    }


def run(out_dir: Path) -> dict:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    data_dir = out_dir / "portal_data"
    artifact_dir = out_dir / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    service = portal.build_service(data_dir)
    try:
        dp_id = seed_completed_scan(service)
        draft = service.start_questionnaire_draft(
            dp_id,
            agent_id="dry-run-agent-001",
            header_seed={"agent_name": "Demo Support Bot", "attested_by_name": "Sam Reyes", "attested_by_title": "Head of AI"},
        )
        submission_id = draft["submission_id"]
        evidence_b64 = base64.b64encode(SAMPLE_RETENTION_POLICY).decode("ascii")
        service.upload_questionnaire_evidence(submission_id, {
            "control_id": "P4.1.Q02",
            "filename": "retention-policy.txt",
            "content_type": "text/plain",
            "body_base64": evidence_b64,
        })
        service.upload_questionnaire_evidence(submission_id, {
            "control_id": "P4.4.Q01",
            "link_url": "https://example.invalid/playbook",
        })
        result = service.submit_questionnaire(submission_id, {
            "header": {
                "legal_entity_name": "Demo Vendor Co.",
                "agent_name": "Demo Support Bot",
                "production_status": "pilot",
                "attested_by_name": "Sam Reyes",
                "attested_by_title": "Head of AI",
                "attested_by_email": "sam@example.invalid",
                "attestation_date": portal.now_iso()[:10],
                "accountability_statement_accepted": True,
            },
            "answers": build_dry_run_answers(),
        })
        src_dir = data_dir / "evidence" / "questionnaire" / submission_id
        payload_path = artifact_dir / "submitted_payload.json"
        pdf_path = artifact_dir / "questionnaire.pdf"
        bundle_path = artifact_dir / "scanner_evidence_bundle.json"
        shutil.copy(src_dir / "payload.json", payload_path)
        shutil.copy(src_dir / "questionnaire.pdf", pdf_path)
        shutil.copy(src_dir / "scanner-evidence-bundle.json", bundle_path)
        summary = {
            "submission_id": submission_id,
            "dp_id": dp_id,
            "submitted_at": result["submitted_at"],
            "payload_artifact": str(payload_path),
            "payload_sha256": result["payload_sha256"],
            "pdf_artifact": str(pdf_path),
            "pdf_sha256": portal.sha256_hex(pdf_path.read_bytes()),
            "pdf_byte_size": pdf_path.stat().st_size,
            "bundle_artifact": str(bundle_path),
            "bundle_sha256": result["bundle_sha256"],
            "prefill_from_scan_present": "P4.1.Q01" in draft["prefill"],
            "audit_chain_ok": service.verify_audit_chain(),
            "draft_prefill": draft["prefill"],
        }
        (artifact_dir / "dry_run_summary.json").write_bytes(portal.canonical_json(summary))
        return summary
    finally:
        service.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/beaaa4919_dryrun")
    args = parser.parse_args()
    out = Path(args.out)
    summary = run(out)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
