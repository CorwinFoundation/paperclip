from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from . import CONNECTOR_VERSION, ENVELOPE_VERSION
from .canonical import canonical_json
from .crypto import EnvelopeSigner
from .redaction import RedactionPolicy, build_assertions, error_hash, redacted_excerpts


def build_envelope(
    scan_id: str,
    task_id: str,
    endpoint_url: str,
    auth_method: str,
    policy: RedactionPolicy,
    signer: EnvelopeSigner,
    expected_assertions: list[str],
    response_text: str | None,
    tool_name: str | None = None,
    response_assertions: dict[str, Any] | None = None,
    error_class: str | None = None,
    error_detail: str | None = None,
) -> dict[str, Any]:
    assertions = build_assertions(response_text, expected_assertions, tool_name)
    if response_assertions and assertions.get("response_received") is True:
        for key in expected_assertions:
            if key in response_assertions:
                assertions[key] = response_assertions[key]
        for key in ("schema_match", "refusal_signal", "hallucination_signal", "tool_called"):
            if key in response_assertions:
                assertions[key] = response_assertions[key]
    envelope: dict[str, Any] = {
        "envelope_version": ENVELOPE_VERSION,
        "scan_id": scan_id,
        "task_id": task_id,
        "connector_version": CONNECTOR_VERSION,
        "redaction_policy_version": policy.version,
        "timestamp": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "endpoint_called": {
            "url_hash": hashlib.sha256(endpoint_url.encode("utf-8")).hexdigest(),
            "auth_method_hash": hashlib.sha256(auth_method.encode("utf-8")).hexdigest(),
        },
        "assertions": assertions,
        "redacted_excerpts": redacted_excerpts(response_text, policy),
    }
    if assertions.get("response_received") is False:
        envelope["error"] = {
            "class": error_class or "other",
            "detail_hash": error_hash(error_detail or error_class or "unknown"),
        }
        if error_class == "oversized_response" and error_detail:
            envelope["error"]["detail"] = error_detail
        envelope["redacted_excerpts"] = []

    signature_payload = canonical_json(envelope)
    envelope["signature"] = signer.sign_b64(signature_payload)
    envelope["signature_chain"] = []
    return envelope
