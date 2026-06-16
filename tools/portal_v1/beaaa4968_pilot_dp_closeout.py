"""BEAAA-4968 pilot-DP telemetry closeout evidence generator.

Runs the Day-14 synthetic DP path from telemetry-api-v0.md section 4.4:
redacted-record intake -> signal envelope -> degradation trigger ->
re-pricing event + claims-trigger envelope, with signature, audit-chain, and
raw-text boundary checks. Writes a sanitized packet for the BEAAA-4968 issue.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "tools" / "portal_v1") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools" / "portal_v1"))

from portal import (  # noqa: E402
    CORPUS_VERSION,
    RUBRIC_VERSION,
    SCANNER_VERSION,
    TELEMETRY_API_VERSION,
    b64url,
    build_service,
    canonical_json,
    sha256_hex,
    unb64url,
)
from telemetry_fixtures_v0_1 import FIXTURE_CASES  # noqa: E402
from telemetry_v0_1 import EVENT_RULE_CORPUS_VERSION, PII_INTAKE_PATTERNS  # noqa: E402
from test_portal import submit_all_tasks  # noqa: E402


def make_keypair() -> tuple[ed25519.Ed25519PrivateKey, str]:
    key = ed25519.Ed25519PrivateKey.generate()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return key, public_pem


def enroll_synthetic_dp(service):
    signing_key, public_pem = make_keypair()
    invite = service.create_invite(
        "BEAAA-4968 Synthetic Pilot DP",
        "owner@example.invalid",
        "call-center",
    )
    dp = service.accept_invite(
        invite["invite_token"],
        "https://staging.example.invalid/agent",
        "cert-ref",
        "staging",
        disclosure_consent=True,
    )
    enrollment = service.enroll_connector(
        {
            "invite_token": invite["invite_token"],
            "dp_org_name": "BEAAA-4968 Synthetic Pilot DP",
            "connector_version": "0.1.0",
            "ephemeral_public_key": {"alg": "x25519", "key": b64url(b"0" * 32)},
            "key_attestation": {"type": "software", "quote": None},
            "signing_public_key_pem": public_pem,
        }
    )
    return signing_key, dp, enrollment


def redacted_record(connector_id: str, dp_id: str, agent_call_id: str, text: str) -> dict:
    return {
        "telemetry_api_version": TELEMETRY_API_VERSION,
        "connector_id": connector_id,
        "dp_id": dp_id,
        "agent_call_id": agent_call_id,
        "capture_window_start": "2026-06-13T00:00:00Z",
        "redaction_policy_signature": b64url(b"fixture-customer-signature"),
        "redaction_policy_version": "0.1",
        "redacted_output_text": text,
        "structural_fields_redacted": ["customer_email", "customer_ssn"],
        "agent_endpoint_config_hash": "sha256-fixture",
        "connector_signature": b64url(b"fixture-connector-signature"),
    }


def verify_portal_signature(service, envelope: dict) -> bool:
    body = {k: v for k, v in envelope.items() if k != "portal_signature"}
    service._counter_key().public_key().verify(
        unb64url(envelope["portal_signature"]),
        canonical_json(body),
    )
    return True


def no_raw_text_leak(service, fixture_text: str) -> dict:
    record_rows = list(service.db.execute("SELECT record_json FROM telemetry_records"))
    pii_hits: list[str] = []
    for row in record_rows:
        record = json.loads(row["record_json"])
        text = record["redacted_output_text"]
        for pattern, label in PII_INTAKE_PATTERNS:
            if re.search(pattern, text):
                pii_hits.append(label)

    phrase_hits: list[str] = []
    for table in ("signal_envelopes", "re_pricing_events", "claims_trigger_envelopes"):
        for row in service.db.execute(f"SELECT envelope_json FROM {table}"):
            body = row["envelope_json"].lower()
            if fixture_text.lower() in body:
                phrase_hits.append(table)
            if "redacted_output_text" in body:
                phrase_hits.append(f"{table}:redacted_output_text_field")
    return {
        "pii_regex_hits_in_redacted_records": pii_hits,
        "raw_text_hits_in_signal_or_downstream_envelopes": phrase_hits,
        "passed": not pii_hits and not phrase_hits,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-dir",
        default=str(REPO_ROOT / "artifacts" / "beaaa-4968"),
        help="Directory for sanitized evidence packet.",
    )
    parser.add_argument("--reset", action="store_true", help="Remove out-dir before writing.")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    if args.reset and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    data_dir = out_dir / "portal_data"
    if data_dir.exists():
        shutil.rmtree(data_dir)

    service = build_service(data_dir)
    try:
        signing_key, dp, enrollment = enroll_synthetic_dp(service)
        scan = service.trigger_scan(dp["dp_id"])
        submit_all_tasks(service, signing_key, scan, enrollment["connector_id"])
        score = service.produce_score(scan["scan_id"])

        case = next(c for c in FIXTURE_CASES if c["event_type"] == "data_leak")
        intake = service.submit_redacted_record(
            redacted_record(
                enrollment["connector_id"],
                dp["dp_id"],
                "beaaa-4968-trigger-call-1",
                case["redacted_output_text"],
            )
        )

        signal_payload = service.list_signal_envelopes(dp["dp_id"])
        repricing_payload = service.list_re_pricing_events(dp["dp_id"])
        claims_payload = service.list_claims_trigger_envelopes(dp["dp_id"])

        signal = signal_payload["signal_envelopes"][0]
        repricing = repricing_payload["re_pricing_events"][0]
        claims = claims_payload["claims_trigger_envelopes"][0]

        signature_checks = {
            "signal_envelope": verify_portal_signature(service, signal),
            "re_pricing_event": verify_portal_signature(service, repricing),
            "claims_trigger_envelope": verify_portal_signature(service, claims),
        }
        raw_text_boundary = no_raw_text_leak(service, case["redacted_output_text"])

        audit_rows = [
            {
                "at": row["at"],
                "actor": row["actor"],
                "event_type": row["event_type"],
                "entity_type": row["entity_type"],
                "entity_id_hash": sha256_hex(row["entity_id"]),
                "payload_hash": row["payload_hash"],
                "prev_hash": row["prev_hash"],
                "entry_hash": row["entry_hash"],
            }
            for row in service.db.execute("SELECT * FROM audit_log ORDER BY id ASC")
        ]

        summary = {
            "ticket": "BEAAA-4968",
            "contract": "telemetry-api-v0.md sha256 015f9ea7...1508",
            "contract_anchor": "are-scanner-api-v1@v1.0 output unchanged",
            "classification": "rule-execution: customer onboarding harness for a new customer archetype",
            "pins": {
                "scanner_version": SCANNER_VERSION,
                "corpus_version": CORPUS_VERSION,
                "rubric_version": RUBRIC_VERSION,
                "telemetry_api_version": TELEMETRY_API_VERSION,
                "event_rule_corpus_version": EVENT_RULE_CORPUS_VERSION,
                "judges": [],
            },
            "synthetic_dp": {
                "dp_id": dp["dp_id"],
                "connector_id": enrollment["connector_id"],
                "scan_id": scan["scan_id"],
                "agent_call_id": "beaaa-4968-trigger-call-1",
                "consent_overlay": "BEAAA-4971_consent_language_v0.1.md",
            },
            "intake_result": intake,
            "signal_envelope_count": signal_payload["count"],
            "re_pricing_event_count": repricing_payload["count"],
            "claims_trigger_envelope_count": claims_payload["count"],
            "signature_checks": signature_checks,
            "audit_chain_valid": service.verify_audit_chain(),
            "raw_text_boundary": raw_text_boundary,
            "score_output_projection": {
                "are_total": score["are_total"],
                "subscores": score["subscores"],
                "band": score["band"],
                "confidence": score["confidence"],
                "scanner_version": score["scanner_version"],
                "corpus_version": score["corpus_version"],
                "seed": score["seed"],
                "model_versions": score["model_versions"],
                "telemetry_fields_present": [
                    key
                    for key in ("telemetry_api_version", "event_rule_corpus_version")
                    if key in score
                ],
            },
            "ids": {
                "signal_envelope_id": signal["signal_envelope_id"],
                "re_pricing_event_id": repricing["re_pricing_event_id"],
                "claims_trigger_envelope_id": claims["claims_trigger_envelope_id"],
            },
        }

        (out_dir / "pilot_dp_summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (out_dir / "audit_log_sanitized.json").write_text(
            json.dumps(audit_rows, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        if not all(signature_checks.values()):
            raise SystemExit("signature check failed")
        if not summary["audit_chain_valid"]:
            raise SystemExit("audit chain failed")
        if not raw_text_boundary["passed"]:
            raise SystemExit("raw text boundary failed")
        if summary["score_output_projection"]["telemetry_fields_present"]:
            raise SystemExit("telemetry fields leaked into score output")

        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
