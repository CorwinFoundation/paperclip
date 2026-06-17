"""Write pipeline skeleton for Log Tracker v0.

BEAAA-6157 child A. Rule-execution: this module validates and appends
tracker events under ``log-tracker-v0@v0.1.2``. It intentionally does not
import BackBond Scanner scoring, rubric, band, judge, or pricing modules.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


CONTRACT_VERSION = "log-tracker-v0@v0.1.2"
ARE_SCANNER_SCHEMA_VERSION = "are-scanner-api-v1@v1.0"
LOG_EVENT_SCHEMA_VERSION = "log-event-schema-v1@v1.0.1"
REDACTION_CLASSES = {"clear", "hash", "redact-on-receipt", "drop"}
SECRET_FAMILY_RE = re.compile(
    r"(?i)(sk-[a-z0-9_-]{12,}|AKIA[0-9A-Z]{16}|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
)


class WritePipelineError(ValueError):
    """Raised when the tracker write pipeline rejects an envelope."""


class Signature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alg: str
    kid: str
    payload_hash: str
    value: str


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rubric_point_id: str
    test_id: str | None = None
    artifact_uri: str | None = None
    verdict: Literal["pass", "partial", "fail", "n/a"]
    weight: float = Field(ge=0, le=1)
    redaction_class: dict[str, str] = Field(default_factory=dict)


class ScannerRunPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scan_id: str
    agent_id: str
    customer_id: str
    purpose: Literal["pre_bind", "mid_term", "claim_review"]
    are_total: int = Field(ge=0, le=100)
    subscores: dict[str, float]
    band: Literal["A", "B", "C", "D"]
    confidence: float = Field(ge=0, le=1)
    scanner_version: str
    corpus_version: str
    model_versions: dict[str, Any]
    seed: int
    run_timestamp: str
    uncovered_dimensions: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef]

    @field_validator("run_timestamp")
    @classmethod
    def _valid_run_timestamp(cls, value: str) -> str:
        _validate_iso8601(value, "run_timestamp")
        return value

    @model_validator(mode="after")
    def _subscores_match_contract(self) -> "ScannerRunPayload":
        expected = {"GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS"}
        if set(self.subscores) != expected:
            raise ValueError("subscores_contract_keys_invalid")
        for name, score in self.subscores.items():
            if not isinstance(score, (int, float)) or score < 0 or score > 25:
                raise ValueError(f"subscore_invalid:{name}")
        return self


class PolicyEventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_event_id: str
    policy_id: str
    agent_id: str
    customer_id: str
    event_kind: Literal[
        "bind",
        "renewal",
        "mid_term_rescore",
        "mid_term_reprice",
        "cancel",
        "non_renewal",
    ]
    scan_id_priced: str | None = None
    event_t: str
    premium_band: str | None = None
    wording_version: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_t")
    @classmethod
    def _valid_event_t(cls, value: str) -> str:
        _validate_iso8601(value, "event_t")
        return value

    @model_validator(mode="after")
    def _priced_events_join_to_scan(self) -> "PolicyEventPayload":
        if self.event_kind not in {"cancel", "non_renewal"} and not self.scan_id_priced:
            raise ValueError("scan_id_priced_required")
        return self


class ClaimEventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_event_id: str
    claim_id: str
    policy_id: str
    agent_id: str
    customer_id: str
    peril: Literal["P1", "P2", "P3", "P4", "P5", "P6"]
    severity_class: Literal["S0", "S1", "S2", "S3", "S4"]
    claim_kind: Literal["candidate", "submitted", "paid", "denied"]
    event_t: str
    scan_id_at_bind: str | None = None
    scan_id_at_loss: str | None = None
    evidence_refs_uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_t")
    @classmethod
    def _valid_event_t(cls, value: str) -> str:
        _validate_iso8601(value, "event_t")
        return value


Payload = ScannerRunPayload | PolicyEventPayload | ClaimEventPayload


class WriteEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["log-tracker-v0@v0.1.2"]
    hook: Literal[
        "scanner_run.completed",
        "policy_event.appended",
        "claim_event.appended",
    ]
    event_id: str
    event_t: str
    source_system: str
    source_version: str
    schema_version: str
    redaction_class_map: dict[str, str]
    payload: dict[str, Any]
    signature: Signature | None = None
    supersedes_id: str | None = None

    @field_validator("event_t")
    @classmethod
    def _valid_event_t(cls, value: str) -> str:
        _validate_iso8601(value, "event_t")
        return value

    @field_validator("redaction_class_map")
    @classmethod
    def _valid_redaction_class_map(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("redaction_class_map_required")
        for field_path, class_name in value.items():
            if not isinstance(field_path, str) or field_path == "":
                raise ValueError("redaction_class_path_invalid")
            if class_name not in REDACTION_CLASSES:
                raise ValueError(f"redaction_class_invalid:{field_path}")
        return value

    @model_validator(mode="after")
    def _valid_schema_for_hook(self) -> "WriteEnvelope":
        if self.hook == "scanner_run.completed":
            if self.schema_version != ARE_SCANNER_SCHEMA_VERSION:
                raise ValueError("scanner_schema_version_invalid")
            ScannerRunPayload.model_validate(self.payload)
        elif self.hook == "policy_event.appended":
            if self.schema_version != LOG_EVENT_SCHEMA_VERSION:
                raise ValueError("policy_schema_version_invalid")
            PolicyEventPayload.model_validate(self.payload)
        elif self.hook == "claim_event.appended":
            if self.schema_version != LOG_EVENT_SCHEMA_VERSION:
                raise ValueError("claim_schema_version_invalid")
            ClaimEventPayload.model_validate(self.payload)
        return self

    def typed_payload(self) -> Payload:
        if self.hook == "scanner_run.completed":
            return ScannerRunPayload.model_validate(self.payload)
        if self.hook == "policy_event.appended":
            return PolicyEventPayload.model_validate(self.payload)
        return ClaimEventPayload.model_validate(self.payload)


@dataclass(frozen=True)
class WriteStageRecord:
    """Sanitized envelope prepared by W1-W3 and ready for W4 append."""

    envelope: WriteEnvelope
    payload: Payload
    sanitized_payload: dict[str, Any]
    redaction_class_map: dict[str, str]
    redaction_applied: list[str]


def apply_migrations(conn: sqlite3.Connection, migrations_dir: str | Path | None = None) -> None:
    """Apply bundled SQL migrations to ``conn`` in lexical order."""

    root = Path(migrations_dir) if migrations_dir else Path(__file__).resolve().parents[2] / "migrations"
    for path in sorted(root.glob("*.sql")):
        conn.executescript(path.read_text())


def w1_validate_envelope(raw: Mapping[str, Any]) -> WriteEnvelope:
    """W1: validate contract, hook, schema version, and payload shape."""

    try:
        return WriteEnvelope.model_validate(dict(raw))
    except ValidationError as exc:
        raise WritePipelineError(str(exc)) from exc


def w2_redact_by_class(envelope: WriteEnvelope) -> WriteStageRecord:
    """W2: apply redaction class map, defaulting missing string fields."""

    payload = envelope.typed_payload()
    sanitized = json.loads(payload.model_dump_json())
    class_map = dict(envelope.redaction_class_map)
    applied: list[str] = []

    for path in sorted(_leaf_paths(sanitized)):
        class_name = _class_for_path(path, class_map)
        if class_name is None:
            value = _get_path(sanitized, path)
            if not isinstance(value, str):
                continue
            class_name = "redact-on-receipt"
            class_map[path] = class_name
            applied.append(f"{path}:default:redact-on-receipt")
        if class_name == "clear":
            continue
        if class_name == "drop":
            _drop_path(sanitized, path.split("."))
            applied.append(f"{path}:drop")
        elif class_name == "hash":
            _set_path(sanitized, path.split("."), _hash_value)
            applied.append(f"{path}:hash")
        elif class_name == "redact-on-receipt":
            _set_path(sanitized, path.split("."), _redact_on_receipt)
            applied.append(f"{path}:redact-on-receipt")
    return WriteStageRecord(
        envelope=envelope,
        payload=payload,
        sanitized_payload=drop_absent(sanitized),
        redaction_class_map=class_map,
        redaction_applied=applied,
    )


def w3_apply_secret_floor(record: WriteStageRecord) -> WriteStageRecord:
    """W3: replace secret-family strings regardless of declared class."""

    sanitized = copy.deepcopy(record.sanitized_payload)
    applied = list(record.redaction_applied)
    _secret_floor_walk(sanitized, [], applied)
    return WriteStageRecord(
        envelope=record.envelope,
        payload=record.payload,
        sanitized_payload=drop_absent(sanitized),
        redaction_class_map=record.redaction_class_map,
        redaction_applied=applied,
    )


def w4_append_only(conn: sqlite3.Connection, record: WriteStageRecord) -> str:
    """W4: append the sanitized envelope; corrections use ``supersedes_id``."""

    if record.envelope.hook == "scanner_run.completed":
        _insert_scanner_run(conn, record)
    elif record.envelope.hook == "policy_event.appended":
        _insert_policy_event(conn, record)
    elif record.envelope.hook == "claim_event.appended":
        _insert_claim_event(conn, record)
    else:
        raise WritePipelineError(f"hook_unsupported:{record.envelope.hook}")
    return record.envelope.event_id


def append_envelope(conn: sqlite3.Connection, raw: Mapping[str, Any]) -> str:
    """Run W1-W4 for one envelope."""

    envelope = w1_validate_envelope(raw)
    redacted = w2_redact_by_class(envelope)
    floored = w3_apply_secret_floor(redacted)
    return w4_append_only(conn, floored)


def _insert_scanner_run(conn: sqlite3.Connection, record: WriteStageRecord) -> None:
    payload = record.payload
    assert isinstance(payload, ScannerRunPayload)
    row_payload = record.sanitized_payload
    conn.execute(
        "INSERT INTO scanner_runs "
        "(event_id, scan_id, agent_id_hash, customer_id_hash, purpose, event_t, "
        "are_total, subscores_json, band, confidence, scanner_version, corpus_version, "
        "model_versions_json, seed, uncovered_json, redaction_class_map, "
        "redaction_applied, payload_json, supersedes_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record.envelope.event_id,
            payload.scan_id,
            _hash_identifier(payload.agent_id),
            _hash_identifier(payload.customer_id),
            payload.purpose,
            record.envelope.event_t,
            payload.are_total,
            _json(row_payload.get("subscores", {})),
            payload.band,
            payload.confidence,
            payload.scanner_version,
            payload.corpus_version,
            _json(row_payload.get("model_versions", {})),
            payload.seed,
            _json(row_payload.get("uncovered_dimensions", [])),
            _json(record.redaction_class_map),
            _json(record.redaction_applied),
            _json(row_payload),
            record.envelope.supersedes_id,
        ),
    )
    for ordinal, ref in enumerate(row_payload.get("evidence_refs", [])):
        if not isinstance(ref, Mapping):
            raise WritePipelineError("evidence_ref_sanitized_invalid")
        conn.execute(
            "INSERT INTO evidence_refs "
            "(evidence_ref_id, event_id, scan_id, ordinal, rubric_point_id, test_id, "
            "artifact_uri, verdict, weight, redaction_class_map, redaction_applied, ref_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid5(uuid.NAMESPACE_URL, f"{record.envelope.event_id}:{ordinal}")),
                record.envelope.event_id,
                payload.scan_id,
                ordinal,
                str(ref.get("rubric_point_id", "")),
                ref.get("test_id"),
                ref.get("artifact_uri"),
                ref.get("verdict"),
                float(ref.get("weight", 0)),
                _json(record.redaction_class_map),
                _json(record.redaction_applied),
                _json(ref),
            ),
        )


def _insert_policy_event(conn: sqlite3.Connection, record: WriteStageRecord) -> None:
    payload = record.payload
    assert isinstance(payload, PolicyEventPayload)
    row_payload = record.sanitized_payload
    conn.execute(
        "INSERT INTO policy_events "
        "(event_id, policy_event_id, policy_id_hash, agent_id_hash, customer_id_hash, "
        "event_kind, scan_id_priced, event_t, premium_band, wording_version, "
        "metadata_json, redaction_class_map, redaction_applied, payload_json, supersedes_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record.envelope.event_id,
            payload.policy_event_id,
            _hash_identifier(payload.policy_id),
            _hash_identifier(payload.agent_id),
            _hash_identifier(payload.customer_id),
            payload.event_kind,
            payload.scan_id_priced,
            record.envelope.event_t,
            payload.premium_band,
            payload.wording_version,
            _json(row_payload.get("metadata", {})),
            _json(record.redaction_class_map),
            _json(record.redaction_applied),
            _json(row_payload),
            record.envelope.supersedes_id,
        ),
    )


def _insert_claim_event(conn: sqlite3.Connection, record: WriteStageRecord) -> None:
    payload = record.payload
    assert isinstance(payload, ClaimEventPayload)
    row_payload = record.sanitized_payload
    conn.execute(
        "INSERT INTO claim_events "
        "(event_id, claim_event_id, claim_id_hash, policy_id_hash, agent_id_hash, "
        "customer_id_hash, peril, severity_class, claim_kind, event_t, scan_id_at_bind, "
        "scan_id_at_loss, evidence_refs_uri, metadata_json, redaction_class_map, "
        "redaction_applied, payload_json, supersedes_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record.envelope.event_id,
            payload.claim_event_id,
            _hash_identifier(payload.claim_id),
            _hash_identifier(payload.policy_id),
            _hash_identifier(payload.agent_id),
            _hash_identifier(payload.customer_id),
            payload.peril,
            payload.severity_class,
            payload.claim_kind,
            record.envelope.event_t,
            payload.scan_id_at_bind,
            payload.scan_id_at_loss,
            row_payload.get("evidence_refs_uri"),
            _json(row_payload.get("metadata", {})),
            _json(record.redaction_class_map),
            _json(record.redaction_applied),
            _json(row_payload),
            record.envelope.supersedes_id,
        ),
    )


def _validate_iso8601(value: str, field_name: str) -> None:
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{field_name}_required")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name}_invalid") from exc


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash_value(value: Any) -> str:
    if isinstance(value, str) and value.startswith("sha256:") and len(value) == 71:
        return value
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _hash_identifier(value: str) -> str:
    return _hash_value(value)


def _redact_on_receipt(value: Any) -> Any:
    if isinstance(value, str):
        return "[REDACTED:log_tracker]"
    if isinstance(value, dict):
        return {key: _redact_on_receipt(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_on_receipt(item) for item in value]
    return value


def _secret_floor_walk(value: Any, path: list[str], applied: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if isinstance(item, str):
                floored = SECRET_FAMILY_RE.sub("[REDACTED:secret_family]", item)
                if floored != item:
                    value[key] = floored
                    applied.append(f"{'.'.join(path + [key])}:secret-floor")
            else:
                _secret_floor_walk(item, path + [key], applied)
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            if isinstance(item, str):
                floored = SECRET_FAMILY_RE.sub("[REDACTED:secret_family]", item)
                if floored != item:
                    value[idx] = floored
                    applied.append(f"{'.'.join(path + [str(idx)])}:secret-floor")
            else:
                _secret_floor_walk(item, path + [str(idx)], applied)


def _leaf_paths(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for key, item in value.items():
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_leaf_paths(item, next_prefix))
        return paths
    if isinstance(value, list):
        paths = []
        for idx, item in enumerate(value):
            next_prefix = f"{prefix}.{idx}" if prefix else str(idx)
            paths.extend(_leaf_paths(item, next_prefix))
        return paths
    return [prefix] if prefix else []


def _class_for_path(path: str, class_map: Mapping[str, str]) -> str | None:
    candidates = [path, f"payload.{path}", _list_generalized_path(path)]
    for candidate in candidates:
        if candidate in class_map:
            return class_map[candidate]
    generalized = _list_generalized_path(path)
    if f"payload.{generalized}" in class_map:
        return class_map[f"payload.{generalized}"]
    return None


def _list_generalized_path(path: str) -> str:
    parts = ["[]" if part.isdigit() else part for part in path.split(".")]
    out = ""
    for part in parts:
        if part == "[]":
            out += "[]"
        else:
            out = f"{out}.{part}" if out else part
    return out


def _get_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            return None
    return current


def _drop_path(value: Any, parts: list[str]) -> Any:
    if not parts:
        return value
    if isinstance(value, dict):
        key = parts[0]
        if key in value:
            if len(parts) == 1:
                value.pop(key, None)
            else:
                value[key] = _drop_path(value[key], parts[1:])
    elif isinstance(value, list) and parts[0].isdigit():
        idx = int(parts[0])
        if 0 <= idx < len(value):
            if len(parts) == 1:
                value[idx] = None
            else:
                value[idx] = _drop_path(value[idx], parts[1:])
    return value


def _set_path(value: Any, parts: list[str], transform) -> Any:
    if not parts:
        return transform(value)
    if isinstance(value, dict):
        key = parts[0]
        if key in value:
            value[key] = _set_path(value[key], parts[1:], transform)
    elif isinstance(value, list) and parts[0].isdigit():
        idx = int(parts[0])
        if 0 <= idx < len(value):
            value[idx] = _set_path(value[idx], parts[1:], transform)
    return value


def drop_absent(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: pruned
            for key, item in value.items()
            if (pruned := drop_absent(item)) is not None
        }
    if isinstance(value, list):
        return [pruned for item in value if (pruned := drop_absent(item)) is not None]
    return value


__all__ = [
    "ARE_SCANNER_SCHEMA_VERSION",
    "CONTRACT_VERSION",
    "LOG_EVENT_SCHEMA_VERSION",
    "REDACTION_CLASSES",
    "ClaimEventPayload",
    "EvidenceRef",
    "PolicyEventPayload",
    "ScannerRunPayload",
    "Signature",
    "WriteEnvelope",
    "WritePipelineError",
    "WriteStageRecord",
    "append_envelope",
    "apply_migrations",
    "w1_validate_envelope",
    "w2_redact_by_class",
    "w3_apply_secret_floor",
    "w4_append_only",
]
