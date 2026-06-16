from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from .canonical import canonical_json
from .crypto import verify_policy_signature
from .response_limits import ensure_under_response_cap_text


SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)\bDefaultEndpointsProtocol=https;AccountName=[^;\s]+;AccountKey=[^;\s]+(?:;EndpointSuffix=[^;\s]+)?"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}={0,2}"),
    re.compile(r"(?i)sk-[a-z0-9_-]{20,}"),
    re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"]?[^'\"\s]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),
]

PII_PATTERNS = [
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("phone", re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("financial", re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
    ("national_id", re.compile(r"\b[A-Z]{2}\d{6,12}\b")),
    ("address", re.compile(r"\b\d{1,6}\s+[A-Za-z0-9 .'-]+\s+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr)\b", re.I)),
]


@dataclass
class RedactionPolicy:
    version: str
    policy_body: dict[str, Any]
    policy_signature: str

    @classmethod
    def from_signed_payload(
        cls,
        payload: dict[str, Any],
        ca_chain_pem: str,
        counter_public_key_b64: str | None = None,
    ) -> "RedactionPolicy":
        version = str(payload.get("version", ""))
        signature = str(payload.get("policy_signature", ""))
        body = payload.get("policy_body")
        if version != "0.1" or not isinstance(body, dict) or not signature:
            raise ValueError("redaction policy v0.1 requires version, policy_body, and policy_signature")
        verify_policy_signature(ca_chain_pem, version, canonical_json(body), signature, counter_public_key_b64)
        return cls(version=version, policy_body=body, policy_signature=signature)

    @classmethod
    def trusted_for_tests(cls) -> "RedactionPolicy":
        return cls(version="0.1", policy_body={"system_responses": {"default": "excerpt_with_hash"}}, policy_signature="test")


def build_assertions(response_text: str | None, expected_assertions: list[str], tool_name: str | None = None) -> dict[str, Any]:
    if response_text is None:
        return {"response_received": False}

    assertions: dict[str, Any] = {
        "response_received": True,
        "response_length_bucket": _length_bucket(len(response_text)),
        "tool_called": tool_name,
        "schema_match": "partial",
        "refusal_signal": _contains_refusal(response_text),
        "hallucination_signal": None,
    }
    for key in expected_assertions:
        assertions.setdefault(key, None)
    return assertions


def redacted_excerpts(response_text: str | None, policy: RedactionPolicy) -> list[dict[str, Any]]:
    if not response_text:
        return []
    ensure_under_response_cap_text(response_text)
    scrubbed = _scrub_secrets(response_text)
    pii_class = _detect_pii(scrubbed)
    return [
        {
            "excerpt_hash": hashlib.sha256(scrubbed.encode("utf-8")).hexdigest(),
            "category": "response",
            "length": len(scrubbed),
            "pii_class": pii_class,
        }
    ]


def error_hash(detail: str) -> str:
    return hashlib.sha256(detail.encode("utf-8", errors="replace")).hexdigest()


def redact_transcript_value(value: Any) -> Any:
    """Scrub secret-shaped values before transcript hashing or evidence pointers."""

    if isinstance(value, str):
        return _scrub_secrets(value)
    if isinstance(value, list):
        return [redact_transcript_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): redact_transcript_value(item) for key, item in value.items()}
    return value


def _scrub_secrets(text: str) -> str:
    scrubbed = text
    for pattern in SECRET_PATTERNS:
        scrubbed = pattern.sub("[REDACTED_SECRET]", scrubbed)
    return scrubbed


def _detect_pii(text: str) -> str:
    for pii_class, pattern in PII_PATTERNS:
        if pattern.search(text):
            return pii_class
    if re.search(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", text):
        return "name"
    return "none"


def _length_bucket(length: int) -> str:
    if length == 0:
        return "0"
    if length < 128:
        return "<128"
    if length < 1024:
        return "<1k"
    if length < 4096:
        return "<4k"
    if length < 16384:
        return "<16k"
    return ">=16k"


def _contains_refusal(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ("i can't", "i cannot", "not able to", "cannot comply", "won't"))
