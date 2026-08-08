"""Internal context envelope v0 for report-only Scanner interpretation.

BEAAA-21158.  This module deliberately has no imports from probe execution or
scoring.  Its output is a sidecar: callers may persist and render it, but must
execute the unchanged full probe set.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from typing import Any, Mapping


SCHEMA_VERSION = "backbond-context-envelope@0.1.0"
MAPPING_VERSION = "backbond-context-applicability@0.1.0"
PROVENANCE = frozenset(
    {"customer_declared", "artifact_derived", "agent_claimed", "scanner_observed"}
)
ENUMS: dict[str, frozenset[str]] = {
    "use_case": frozenset({"voice_support", "text_support", "crm", "workflow"}),
    "operating_environment": frozenset({"development", "staging", "production", "mixed"}),
    "agent_role": frozenset({"assistant", "support", "sales", "operations", "decision_support"}),
    "user_population": frozenset({"employees", "customers", "partners", "public", "mixed"}),
    "channel": frozenset({"voice", "chat", "email", "api", "multichannel"}),
    "tool_access": frozenset({"none", "read_only", "bounded_write", "privileged_write"}),
    "data_access": frozenset({"public", "internal", "personal", "sensitive", "mixed"}),
    "presentation": frozenset({"human_like", "clearly_ai", "not_applicable"}),
    "impersonation_policy": frozenset({"prohibited", "permitted_disclosed", "unknown"}),
    "human_disclosure": frozenset({"required", "optional", "unknown"}),
}
TEXT_FIELDS = frozenset({
    "company_name", "company_description", "intended_use", "constraints",
    "success_expectations", "failure_expectations",
})
ASSESSMENT_FIELDS = frozenset({
    "company_name", "intended_use", "operating_environment", "agent_role",
    "user_population", "channel", "tool_access", "data_access", "constraints",
    "success_expectations", "failure_expectations",
})
MAX_TEXT_LENGTH = 512
MAX_EVIDENCE_REF_LENGTH = 512
_PII = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    re.compile(r"(?<!\d)(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}(?!\d)"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
)

# Values can recommend additional attention only.  There is intentionally no
# representation for probe suppression and no free-text key in this map.
APPLICABILITY_ALLOWLIST: dict[tuple[str, str], tuple[str, ...]] = {
    ("use_case", "voice_support"): ("voice.presentation", "voice.impersonation"),
    ("presentation", "human_like"): ("voice.presentation",),
    ("impersonation_policy", "prohibited"): ("voice.impersonation",),
    ("human_disclosure", "required"): ("voice.presentation",),
    ("channel", "voice"): ("voice.presentation", "voice.impersonation"),
    ("channel", "multichannel"): ("channel.boundary",),
    ("tool_access", "bounded_write"): ("tools.authorization",),
    ("tool_access", "privileged_write"): ("tools.authorization", "tools.scope_escape"),
    ("data_access", "personal"): ("data.pii_handling",),
    ("data_access", "sensitive"): ("data.pii_handling", "data.exfiltration"),
    ("data_access", "mixed"): ("data.pii_handling",),
}


class ContextEnvelopeError(ValueError):
    """Malformed context is rejected before persistence or interpretation."""


def _clean_text(value: Any, *, limit: int, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContextEnvelopeError(f"{label} must be a non-empty string")
    if len(value) > limit:
        raise ContextEnvelopeError(f"{label} exceeds {limit} characters")
    cleaned = value.strip()
    for pattern in _PII:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned


def validate_context_envelope(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate, redact, canonicalize, and derive field conflict state.

    Each field retains every source assertion.  A field is corroborated when
    two or more distinct sources agree and conflicted when accepted assertions
    contain multiple canonical values.  Conflicts are never resolved here.
    """
    if not isinstance(raw, Mapping):
        raise ContextEnvelopeError("context envelope must be an object")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ContextEnvelopeError("unknown context schema_version")
    fields = raw.get("fields")
    if not isinstance(fields, Mapping) or not fields:
        raise ContextEnvelopeError("fields must be a non-empty object")
    unknown = set(fields) - set(ENUMS) - set(TEXT_FIELDS)
    if unknown:
        raise ContextEnvelopeError(f"unknown context fields: {sorted(unknown)}")

    normalized: dict[str, Any] = {}
    envelope_conflicted = False
    for name in sorted(fields):
        assertions = fields[name]
        if not isinstance(assertions, list) or not assertions or len(assertions) > 16:
            raise ContextEnvelopeError(f"{name} assertions must contain 1..16 entries")
        clean_assertions = []
        for assertion in assertions:
            if not isinstance(assertion, Mapping) or set(assertion) != {
                "source", "value", "evidence_ref"
            }:
                raise ContextEnvelopeError(f"malformed assertion for {name}")
            source = assertion["source"]
            if source not in PROVENANCE:
                raise ContextEnvelopeError(f"unknown provenance for {name}")
            evidence_ref = _clean_text(
                assertion["evidence_ref"], limit=MAX_EVIDENCE_REF_LENGTH,
                label=f"{name}.evidence_ref",
            )
            value = assertion["value"]
            if name in ENUMS:
                if not isinstance(value, str) or value not in ENUMS[name]:
                    raise ContextEnvelopeError(f"unknown enum value for {name}")
                clean_value = value
            else:
                clean_value = _clean_text(value, limit=MAX_TEXT_LENGTH, label=name)
            clean_assertions.append(
                {"source": source, "value": clean_value, "evidence_ref": evidence_ref}
            )

        values = {json.dumps(a["value"], sort_keys=True) for a in clean_assertions}
        sources_by_value: dict[str, set[str]] = {}
        for assertion in clean_assertions:
            key = json.dumps(assertion["value"], sort_keys=True)
            sources_by_value.setdefault(key, set()).add(assertion["source"])
        conflicted = len(values) > 1
        corroborated = any(len(sources) > 1 for sources in sources_by_value.values())
        envelope_conflicted |= conflicted
        normalized[name] = {
            "assertions": clean_assertions,
            "status": "conflicted" if conflicted else ("corroborated" if corroborated else "single_source"),
            "corroborated": corroborated,
            "conflicted": conflicted,
        }

    missing_fields = sorted(ASSESSMENT_FIELDS - set(normalized))
    result = {
        "schema_version": SCHEMA_VERSION,
        "fields": normalized,
        "missing_fields": missing_fields,
        "status": "conflicted" if envelope_conflicted else ("incomplete" if missing_fields else "valid"),
    }
    result["context_hash"] = "sha256:" + hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return result


def require_validated_context_envelope(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild a boundary value and verify its canonical integrity seal."""
    if not isinstance(envelope, Mapping) or not isinstance(envelope.get("fields"), Mapping):
        raise ContextEnvelopeError("validated context envelope must be an object")
    raw_fields: dict[str, Any] = {}
    for name, detail in envelope["fields"].items():
        if not isinstance(detail, Mapping) or not isinstance(detail.get("assertions"), list):
            raise ContextEnvelopeError(f"malformed validated field for {name}")
        raw_fields[str(name)] = detail["assertions"]
    rebuilt = validate_context_envelope(
        {"schema_version": envelope.get("schema_version"), "fields": raw_fields}
    )
    if dict(envelope) != rebuilt:
        raise ContextEnvelopeError("context envelope integrity mismatch")
    return rebuilt


def shadow_applicability(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deterministic, non-executing recommendation sidecar."""
    envelope = require_validated_context_envelope(envelope)
    recommendations: set[str] = set()
    reasons = []
    fail_closed = envelope.get("status") in {"conflicted", "incomplete"}
    if not fail_closed:
        for field, detail in sorted(envelope["fields"].items()):
            for assertion in detail["assertions"]:
                key = (field, assertion["value"])
                for test_id in APPLICABILITY_ALLOWLIST.get(key, ()):
                    recommendations.add(test_id)
                    reasons.append({"field": field, "value": assertion["value"], "test_id": test_id})
    return {
        "mapping_version": MAPPING_VERSION,
        "mode": "report_only_shadow",
        "context_hash": envelope["context_hash"],
        "status": "fail_closed" if fail_closed else "recommendation_only",
        "recommended_test_ids": sorted(recommendations),
        "reasons": sorted(reasons, key=lambda item: (item["test_id"], item["field"], item["value"])),
        "execution_policy": "execute_full_probe_set",
        "suppressed_probe_ids": [],
    }


def render_interpretation_annotations(envelope: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Render structured report annotations without touching score output."""
    envelope = require_validated_context_envelope(envelope)
    annotations = [
        {
            "field": field,
            "interpretation": "missing_context",
            "status": "missing",
            "sources": [],
        }
        for field in envelope.get("missing_fields", [])
    ]
    for field, detail in sorted(envelope["fields"].items()):
        kind = "context_conflict" if detail["conflicted"] else (
            "expected_behavior" if field in {"impersonation_policy", "human_disclosure"}
            else "risk_relevant_behavior"
        )
        annotations.append({
            "field": field,
            "interpretation": kind,
            "status": detail["status"],
            "sources": [dict(assertion) for assertion in detail["assertions"]],
        })
    return annotations


class ContextEnvelopeStore:
    """Additive sidecar persistence, separate from the public scan contract."""

    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        with self._lock:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS scan_context_envelopes ("
                "scan_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL, "
                "context_hash TEXT NOT NULL, envelope_json TEXT NOT NULL, "
                "recommendation_json TEXT NOT NULL)"
            )

    def put(self, scan_id: str, envelope: Mapping[str, Any]) -> None:
        clean_scan_id = _clean_text(scan_id, limit=128, label="scan_id")
        envelope = require_validated_context_envelope(envelope)
        recommendation = shadow_applicability(envelope)
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO scan_context_envelopes VALUES (?, ?, ?, ?, ?)",
                (clean_scan_id, SCHEMA_VERSION, envelope["context_hash"],
                 json.dumps(envelope, sort_keys=True, separators=(",", ":")),
                 json.dumps(recommendation, sort_keys=True, separators=(",", ":"))),
            )

    def get(self, scan_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT envelope_json, recommendation_json FROM scan_context_envelopes WHERE scan_id = ?",
                (scan_id,),
            ).fetchone()
        if row is None:
            return None
        return {"envelope": json.loads(row[0]), "shadow_recommendation": json.loads(row[1])}
