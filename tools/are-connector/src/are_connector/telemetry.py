from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from .canonical import canonical_json
from .crypto import EnvelopeSigner
from .redaction import PII_PATTERNS, RedactionPolicy, SECRET_PATTERNS

TELEMETRY_API_VERSION = "0.1"
MAX_REDACTED_OUTPUT_BYTES = 64 * 1024


def build_redacted_output_record(
    connector_id: str,
    dp_id: str,
    agent_call_id: str,
    agent_output_text: str,
    policy: RedactionPolicy,
    signer: EnvelopeSigner,
    agent_endpoint_config: dict[str, Any] | str,
    capture_window_start: str | None = None,
) -> dict[str, Any]:
    redacted_output_text, structural_fields = redact_agent_output(agent_output_text, policy)
    if len(redacted_output_text.encode("utf-8")) > MAX_REDACTED_OUTPUT_BYTES:
        raise ValueError("redacted_output_text exceeds 64 KiB telemetry limit")

    record: dict[str, Any] = {
        "telemetry_api_version": TELEMETRY_API_VERSION,
        "connector_id": connector_id,
        "dp_id": dp_id,
        "agent_call_id": agent_call_id,
        "capture_window_start": capture_window_start or _now_iso(),
        "redaction_policy_signature": policy.policy_signature,
        "redaction_policy_version": policy.version,
        "redacted_output_text": redacted_output_text,
        "structural_fields_redacted": structural_fields,
        "agent_endpoint_config_hash": _config_hash(agent_endpoint_config),
    }
    record["connector_signature"] = signer.sign_b64(canonical_json(record))
    return record


def redact_agent_output(agent_output_text: str, policy: RedactionPolicy) -> tuple[str, list[str]]:
    structural_fields = _structural_fields(policy)
    redacted = _redact_structural_fields(agent_output_text, structural_fields)
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED_SECRET]", redacted)
    for pii_class, pattern in PII_PATTERNS:
        redacted = pattern.sub(f"[REDACTED_{pii_class.upper()}]", redacted)
    redacted = _redact_named_entities(redacted, policy)
    return redacted, structural_fields


def _structural_fields(policy: RedactionPolicy) -> list[str]:
    value = policy.policy_body.get("structural_fields_redacted", policy.policy_body.get("structural_fields", []))
    if not isinstance(value, list):
        return []
    fields = sorted({str(item) for item in value if isinstance(item, str) and item})
    return fields


def _redact_structural_fields(text: str, fields: list[str]) -> str:
    if not fields:
        return text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _redact_structural_text(text, fields)
    redacted = _redact_json_value(payload, set(fields))
    return json.dumps(redacted, sort_keys=True, separators=(",", ":"))


def _redact_json_value(value: Any, fields: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED_FIELD]" if key in fields else _redact_json_value(item, fields)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json_value(item, fields) for item in value]
    return value


def _redact_structural_text(text: str, fields: list[str]) -> str:
    redacted = text
    for field in fields:
        pattern = re.compile(rf'("{re.escape(field)}"\s*:\s*)"[^"]*"')
        redacted = pattern.sub(r'\1"[REDACTED_FIELD]"', redacted)
        pattern = re.compile(rf"(\b{re.escape(field)}\b\s*[:=]\s*)[^\s,;]+", re.I)
        redacted = pattern.sub(r"\1[REDACTED_FIELD]", redacted)
    return redacted


def _redact_named_entities(text: str, policy: RedactionPolicy) -> str:
    named_entities = policy.policy_body.get("named_entities", {})
    allow = set(_string_list(named_entities.get("allow") if isinstance(named_entities, dict) else None))
    deny = set(_string_list(named_entities.get("deny") if isinstance(named_entities, dict) else None))
    redacted = text
    for entity in sorted(deny, key=len, reverse=True):
        if entity not in allow:
            redacted = redacted.replace(entity, "[REDACTED_ENTITY]")
    if policy.policy_body.get("redact_person_names", True):
        redacted = re.sub(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", _name_replacer(allow), redacted)
    return redacted


def _name_replacer(allow: set[str]):
    def replace(match: re.Match[str]) -> str:
        value = match.group(0)
        return value if value in allow else "[REDACTED_NAME]"

    return replace


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _config_hash(agent_endpoint_config: dict[str, Any] | str) -> str:
    if isinstance(agent_endpoint_config, str):
        body = agent_endpoint_config.encode("utf-8")
    else:
        body = canonical_json(agent_endpoint_config)
    return hashlib.sha256(body).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
