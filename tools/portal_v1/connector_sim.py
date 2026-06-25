#!/usr/bin/env python3
"""HTTP connector simulator for the BEAAA-4722 Portal v1 harness.

BEAAA-4937 / v0.2 addendum: continuous-monitoring cron loop added below.
Bounded in-process scheduler:
  - GET /connectors/schedule at boot + each long-poll wake
  - verify descriptor signature against the portal CA cert from enrollment
  - if now >= next_run_at and cadence != 'disabled': POST /scans/scheduled
  - re-enter normal v0.1 long-poll flow against the returned scan_id

No new outbound network surface: schedule fetch uses the portal API base URL.
No persisted timer; no external cron daemon.
"""

from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding

from portal import ENVELOPE_VERSION, REDACTION_POLICY_VERSION, b64url, canonical_json, now_iso, sha256_hex


@dataclass
class ConnectorSimConfig:
    base_url: str
    invite_token: str
    dp_org_name: str
    endpoint_url: str
    cert_ref: str
    connector_version: str = "0.1.0"


class ConnectorSimError(Exception):
    pass


def request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    form: bool = False,
) -> tuple[int, Any]:
    body = None
    request_headers = dict(headers or {})
    if payload is not None:
        if form:
            body = urllib.parse.urlencode(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
        data=body,
        method=method,
        headers=request_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            if response.status == 204:
                return response.status, None
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                return response.status, json.loads(raw.decode("utf-8"))
            return response.status, raw.decode("utf-8")
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8")
        raise ConnectorSimError(f"{method} {path} failed with {err.code}: {detail}") from err


def signed_envelope(signing_key: ed25519.Ed25519PrivateKey, scan_id: str, task: dict[str, Any], endpoint_url: str) -> dict[str, Any]:
    envelope = {
        "envelope_version": ENVELOPE_VERSION,
        "scan_id": scan_id,
        "task_id": task["task_id"],
        "connector_version": "0.1.0",
        "redaction_policy_version": REDACTION_POLICY_VERSION,
        "timestamp": now_iso(),
        "endpoint_called": {"url_hash": sha256_hex(endpoint_url), "auth_method_hash": sha256_hex("simulated-mtls")},
        "assertions": {
            "response_received": True,
            "response_length_bucket": "<1k",
            "tool_called": None,
            "schema_match": "pass",
            "refusal_signal": False,
            "hallucination_signal": None,
        },
        "redacted_excerpts": [],
        "signature_chain": [],
    }
    envelope["signature"] = b64url(
        signing_key.sign(canonical_json({key: value for key, value in envelope.items() if key not in {"signature", "signature_chain"}}))
    )
    return envelope


def run_connector_flow(config: ConnectorSimConfig) -> dict[str, Any]:
    signing_key = ed25519.Ed25519PrivateKey.generate()
    signing_public_pem = signing_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")
    _, accepted = request_json(
        config.base_url,
        "POST",
        "/dp/accept",
        {
            "invite_token": config.invite_token,
            "endpoint_url": config.endpoint_url,
            "cert_ref": config.cert_ref,
            "endpoint_flag": "staging",
        },
        form=True,
    )
    _, enrollment = request_json(
        config.base_url,
        "POST",
        "/connectors/enroll",
        {
            "invite_token": config.invite_token,
            "dp_org_name": config.dp_org_name,
            "connector_version": config.connector_version,
            "ephemeral_public_key": {"alg": "x25519", "key": b64url(b"0" * 32)},
            "key_attestation": {"type": "software", "quote": None},
            "signing_public_key_pem": signing_public_pem,
        },
    )
    # BEAAA-5698 AC2: /dp/{dp_id}/scans now requires DP-bearer issued at accept_invite.
    _, scan = request_json(
        config.base_url,
        "POST",
        f"/dp/{accepted['dp_id']}/scans",
        headers={"Authorization": f"Bearer {accepted['dp_bearer']}"},
    )
    submitted = 0
    while True:
        status, task = request_json(
            config.base_url,
            "GET",
            f"/scans/{scan['scan_id']}/tasks/next",
            headers={"x-connector-id": enrollment["connector_id"]},
        )
        if status == 204:
            break
        request_json(
            config.base_url,
            "POST",
            f"/scans/{scan['scan_id']}/envelopes",
            signed_envelope(signing_key, scan["scan_id"], task, config.endpoint_url),
        )
        submitted += 1
    report_status, _ = request_json(config.base_url, "GET", f"/reports/{scan['scan_id']}/dp")
    return {
        "dp_id": accepted["dp_id"],
        "connector_id": enrollment["connector_id"],
        "scan_id": scan["scan_id"],
        "submitted_envelopes": submitted,
        "dp_report_status": report_status,
    }


# -- BEAAA-4937 / v0.2 addendum: connector cron loop ------------------------------------


def _unb64url(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def verify_schedule_descriptor(descriptor: dict[str, Any], ca_chain_pem: str) -> bool:
    signature = descriptor.get("policy_signature")
    if not signature:
        return False
    body = {k: v for k, v in descriptor.items() if k != "policy_signature"}
    try:
        ca_cert = x509.load_pem_x509_certificate(ca_chain_pem.encode("ascii"))
        ca_cert.public_key().verify(
            _unb64url(signature),
            canonical_json(body),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False


def fetch_signed_schedule(base_url: str, connector_id: str, client_secret: str = "") -> dict[str, Any]:
    headers: dict[str, str] = {"x-connector-id": connector_id}
    if client_secret:
        headers["Authorization"] = f"Bearer {client_secret}"
    _, descriptor = request_json(
        base_url,
        "GET",
        "/connectors/schedule",
        headers=headers,
    )
    return descriptor


def schedule_should_fire(descriptor: dict[str, Any], now: datetime | None = None) -> bool:
    if descriptor.get("cadence") in (None, "disabled"):
        return False
    next_run_at = descriptor.get("next_run_at")
    if not next_run_at:
        return False
    try:
        when = datetime.fromisoformat(str(next_run_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) >= when


def start_scheduled_scan(base_url: str, connector_id: str, schedule_window_start: str) -> dict[str, Any]:
    _, payload = request_json(
        base_url,
        "POST",
        "/scans/scheduled",
        {"schedule_window_start": schedule_window_start, "connector_id": connector_id},
        headers={"x-connector-id": connector_id},
    )
    return payload


def complete_scan_envelopes(
    base_url: str,
    connector_id: str,
    scan_id: str,
    signing_key: ed25519.Ed25519PrivateKey,
    endpoint_url: str,
) -> int:
    submitted = 0
    while True:
        status, task = request_json(
            base_url,
            "GET",
            f"/scans/{scan_id}/tasks/next",
            headers={"x-connector-id": connector_id},
        )
        if status == 204:
            break
        request_json(
            base_url,
            "POST",
            f"/scans/{scan_id}/envelopes",
            signed_envelope(signing_key, scan_id, task, endpoint_url),
        )
        submitted += 1
    return submitted


def run_cron_cycle(
    base_url: str,
    connector_id: str,
    ca_chain_pem: str,
    signing_key: ed25519.Ed25519PrivateKey,
    endpoint_url: str,
    client_secret: str = "",
) -> dict[str, Any]:
    """One iteration: fetch + verify schedule, fire scheduled scan if due, return diagnostics.

    Refuse to act on unsigned or stale-signed descriptors (v0.2 §4.2 + §6).
    """
    descriptor = fetch_signed_schedule(base_url, connector_id, client_secret=client_secret)
    if not verify_schedule_descriptor(descriptor, ca_chain_pem):
        return {"schedule_sig_valid": False, "fired": False, "descriptor_cadence": descriptor.get("cadence")}
    fired = False
    scan_id: str | None = None
    submitted_envelopes = 0
    idempotent = None
    if schedule_should_fire(descriptor):
        result = start_scheduled_scan(base_url, connector_id, descriptor["schedule_window_start"])
        scan_id = result.get("scan_id")
        idempotent = result.get("idempotent")
        fired = True
        if scan_id and not idempotent:
            submitted_envelopes = complete_scan_envelopes(
                base_url, connector_id, scan_id, signing_key, endpoint_url
            )
    return {
        "schedule_sig_valid": True,
        "descriptor_cadence": descriptor.get("cadence"),
        "next_run_at": descriptor.get("next_run_at"),
        "schedule_window_start": descriptor.get("schedule_window_start"),
        "fired": fired,
        "idempotent": idempotent,
        "scheduled_scan_id": scan_id,
        "scheduled_submitted_envelopes": submitted_envelopes,
    }


def run_continuous_flow(
    config: ConnectorSimConfig,
    cron_cycles: int = 1,
    cycle_sleep_seconds: float = 0.0,
) -> dict[str, Any]:
    """Full v0.2 demo: enroll → baseline scan → cron cycle(s) → return diagnostics.

    Caller is expected to set up the schedule via admin PUT between enrollment and
    the first cron cycle. For the standalone Day-7 acceptance demo, see
    `tools/portal_v1/cm_demo.py` which orchestrates the admin PUT against the
    same portal substrate.
    """
    signing_key = ed25519.Ed25519PrivateKey.generate()
    signing_public_pem = signing_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")
    _, accepted = request_json(
        config.base_url,
        "POST",
        "/dp/accept",
        {
            "invite_token": config.invite_token,
            "endpoint_url": config.endpoint_url,
            "cert_ref": config.cert_ref,
            "endpoint_flag": "staging",
        },
        form=True,
    )
    _, enrollment = request_json(
        config.base_url,
        "POST",
        "/connectors/enroll",
        {
            "invite_token": config.invite_token,
            "dp_org_name": config.dp_org_name,
            "connector_version": config.connector_version,
            "ephemeral_public_key": {"alg": "x25519", "key": b64url(b"0" * 32)},
            "key_attestation": {"type": "software", "quote": None},
            "signing_public_key_pem": signing_public_pem,
        },
    )
    # BEAAA-5698 AC2: /dp/{dp_id}/scans now requires DP-bearer issued at accept_invite.
    _, baseline_scan = request_json(
        config.base_url,
        "POST",
        f"/dp/{accepted['dp_id']}/scans",
        headers={"Authorization": f"Bearer {accepted['dp_bearer']}"},
    )
    baseline_envelopes = complete_scan_envelopes(
        config.base_url,
        enrollment["connector_id"],
        baseline_scan["scan_id"],
        signing_key,
        config.endpoint_url,
    )
    client_secret = enrollment.get("oidc", {}).get("client_secret", "")
    cron_results = []
    for _ in range(cron_cycles):
        cron_results.append(
            run_cron_cycle(
                config.base_url,
                enrollment["connector_id"],
                enrollment["ca_chain_pem"],
                signing_key,
                config.endpoint_url,
                client_secret=client_secret,
            )
        )
        if cycle_sleep_seconds > 0:
            time.sleep(cycle_sleep_seconds)
    return {
        "dp_id": accepted["dp_id"],
        "connector_id": enrollment["connector_id"],
        "connector_client_secret": client_secret,
        "baseline_scan_id": baseline_scan["scan_id"],
        "baseline_submitted_envelopes": baseline_envelopes,
        "cron_results": cron_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--invite-token", required=True)
    parser.add_argument("--dp-org-name", default="Simulated Design Partner")
    parser.add_argument("--endpoint-url", default="https://staging.example.invalid/agent")
    parser.add_argument("--cert-ref", default="simulated-cert-ref")
    args = parser.parse_args()
    result = run_connector_flow(
        ConnectorSimConfig(
            base_url=args.base_url,
            invite_token=args.invite_token,
            dp_org_name=args.dp_org_name,
            endpoint_url=args.endpoint_url,
            cert_ref=args.cert_ref,
        )
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
