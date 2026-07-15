"""BEAAA-14087 authoritative real-agent executed-count gate."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit


DISALLOWED_TEXT_MARKERS = (
    "127.0.0.1", "localhost", "mock:", " mock ", "echo", "replay",
    "deterministic fixture", "deterministic_local", "fixture_version",
    "generated local_target_response", "local_target_response",
    "prewritten response", "terminal-only", "terminal response captured",
    "non-empty", "non_empty", "assert response_received", "captured_response",
)


@dataclass(frozen=True)
class ApprovedAgent:
    """Control-plane record; never populated from the evidence row."""

    identity: str
    https_origin: str
    evidence_hmac_key: bytes

    def __post_init__(self) -> None:
        if not self.identity.strip():
            raise ValueError("approved agent identity must not be empty")
        if _origin(self.https_origin) != self.https_origin.rstrip("/"):
            raise ValueError("approved agent origin must be a canonical public HTTPS origin")
        if len(self.evidence_hmac_key) < 32:
            raise ValueError("approved agent HMAC key must contain at least 32 bytes")


class AuthoritativeRealAgentVerifier:
    """Verify target identity against server-owned approvals and a signed envelope."""

    def __init__(self, approvals: Mapping[str, ApprovedAgent]):
        self._approvals = dict(approvals)
        if any(identity != approval.identity for identity, approval in self._approvals.items()):
            raise ValueError("approval map keys must equal approved agent identities")

    def verify(self, row: Mapping[str, Any]) -> tuple[bool, str]:
        identity = str(row.get("target_identity") or "")
        approval = self._approvals.get(identity)
        if approval is None:
            return False, "target_identity_not_control_plane_approved"
        request = _mapping(row.get("raw_request"))
        response = _mapping(row.get("raw_response"))
        if _origin(str(request.get("url") or "")) != approval.https_origin:
            return False, "request_origin_not_control_plane_approved"
        envelope = _signed_envelope(row)
        supplied = str(row.get("authority_signature") or "")
        expected = hmac.new(approval.evidence_hmac_key, envelope, hashlib.sha256).hexdigest()
        if not supplied or not hmac.compare_digest(supplied, expected):
            return False, "invalid_authority_signature"
        return True, ""

    def sign_for_test(self, row: Mapping[str, Any]) -> str:
        """Test/helper signer; production signing belongs to the execution authority."""
        approval = self._approvals[str(row["target_identity"])]
        return hmac.new(approval.evidence_hmac_key, _signed_envelope(row), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class RealAgentGateResult:
    countable: bool
    reasons: tuple[str, ...]


def evaluate_real_agent_execution(
    row: Mapping[str, Any],
    *,
    verifier: AuthoritativeRealAgentVerifier | None = None,
) -> RealAgentGateResult:
    """Fail closed unless a server-owned verifier authenticates raw evidence."""
    reasons: list[str] = []
    if _first_present(row, "target_type", "raw_target_type") != "real_agent":
        reasons.append("target_type_not_real_agent")

    raw_request = _mapping(row.get("raw_request"))
    raw_response = _mapping(row.get("raw_response"))
    if not raw_request:
        reasons.append("missing_raw_request")
    if not raw_response:
        reasons.append("missing_raw_response")
    if not _first_present(raw_request, "request_sha256"):
        reasons.append("missing_raw_request_digest")
    if not _first_present(raw_response, "body_sha256"):
        reasons.append("missing_raw_response_digest")

    if verifier is None:
        reasons.append("authoritative_verifier_unavailable")
    else:
        verified, reason = verifier.verify(row)
        if not verified:
            reasons.append(reason)

    haystack = "\n".join(
        str(value).lower()
        for value in (
            raw_request.get("url"), row.get("target_response"),
            row.get("verdict_provenance"), row.get("framework"), row.get("source"),
        )
        if value not in (None, "")
    )
    reasons.extend(
        f"disallowed_class:{marker.strip()}"
        for marker in DISALLOWED_TEXT_MARKERS
        if marker in haystack
    )
    return RealAgentGateResult(not reasons, tuple(dict.fromkeys(reasons)))


def quarantine_reason(
    row: Mapping[str, Any], *, verifier: AuthoritativeRealAgentVerifier | None = None
) -> str:
    return ";".join(evaluate_real_agent_execution(row, verifier=verifier).reasons)


def _signed_envelope(row: Mapping[str, Any]) -> bytes:
    request = _mapping(row.get("raw_request"))
    response = _mapping(row.get("raw_response"))
    payload = {
        "case_id": str(row.get("case_id") or ""),
        "target_identity": str(row.get("target_identity") or ""),
        "request_url": str(request.get("url") or ""),
        "request_sha256": str(request.get("request_sha256") or ""),
        "response_sha256": str(response.get("body_sha256") or ""),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _origin(url: str) -> str:
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        return ""
    host = hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return ""
    suffix = f":{port}" if port and port != 443 else ""
    return f"https://{host}{suffix}"


def load_authoritative_verifier(path: Path) -> AuthoritativeRealAgentVerifier:
    """Compose the verifier from a server-owned manifest and environment secrets."""
    document = json.loads(path.read_text(encoding="utf-8"))
    records = document.get("approved_agents") if isinstance(document, Mapping) else None
    if not isinstance(records, list) or not records:
        raise ValueError("approval manifest must contain non-empty approved_agents")
    approvals: dict[str, ApprovedAgent] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("approved_agents entries must be objects")
        identity = str(record.get("identity") or "")
        secret_env = str(record.get("hmac_key_env") or "")
        secret = os.environ.get(secret_env, "") if secret_env else ""
        if not secret:
            raise ValueError(f"missing server-owned HMAC secret for {identity!r}")
        approval = ApprovedAgent(
            identity=identity,
            https_origin=str(record.get("https_origin") or "").rstrip("/"),
            evidence_hmac_key=secret.encode("utf-8"),
        )
        if identity in approvals:
            raise ValueError(f"duplicate approved agent identity: {identity}")
        approvals[identity] = approval
    return AuthoritativeRealAgentVerifier(approvals)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_present(row: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return str(value)
    return ""
