"""BEAAA-6165 policy-system claim auto-open adapter.

Implements the BEAAA-6152 v0.1 MVP write path inside the local Portal v1
policy-system harness. This is additive policy-system plumbing; it does not
mutate BackBond Scanner output under are-scanner-api-v1@v1.0.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from portal import PortalError, PortalService, now_iso, sha256_hex


POLICY_AUTO_OPEN_VERSION = "0.1"
CLAIM_RECORD_STATUS_OPEN = "intake_open"
COVERAGE_STATE_DEFAULT = "unreviewed"
RESERVE_STATE_DEFAULT = "not_set"
PAYMENT_STATE_DEFAULT = "not_approved"
ASSIGNMENT_QUEUE = "claims_architect_and_underwriter"
CUSTOMER_LOSS_TYPES = frozenset(
    {"financial_loss", "third_party_demand", "regulatory_notice", "data_incident", "other"}
)
OPEN_TELEMETRY_SEVERITIES = frozenset({"S3", "S4"})
OPEN_CLAIM_ELIGIBILITY_CLASSES = frozenset({"interim_payout_eligible", "review_required"})


def _init_policy_auto_open_tables(self: PortalService) -> None:
    self.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS claim_records (
          claim_record_id TEXT PRIMARY KEY,
          policy_id TEXT NOT NULL,
          insured_agent_id TEXT NOT NULL,
          intake_class TEXT NOT NULL,
          status TEXT NOT NULL,
          coverage_state TEXT NOT NULL,
          reserve_state TEXT NOT NULL,
          payment_state TEXT NOT NULL,
          trigger_source TEXT NOT NULL,
          trigger_source_id TEXT NOT NULL,
          auto_open_fingerprint TEXT NOT NULL,
          opened_at TEXT NOT NULL,
          event_occurred_at TEXT,
          severity_tier TEXT,
          peril_code TEXT,
          threshold_policy_ref TEXT,
          threshold_policy_governed INTEGER NOT NULL,
          telemetry_manual_review_only INTEGER NOT NULL,
          wording_section_candidates_json TEXT NOT NULL,
          evidence_refs_json TEXT NOT NULL,
          customer_notice_present INTEGER NOT NULL,
          assignment_queue TEXT NOT NULL,
          sla_due_at TEXT NOT NULL,
          summary TEXT
        );
        CREATE INDEX IF NOT EXISTS claim_records_fingerprint_opened
          ON claim_records (auto_open_fingerprint, opened_at);
        CREATE TABLE IF NOT EXISTS claim_record_activity (
          id TEXT PRIMARY KEY,
          claim_record_id TEXT NOT NULL,
          source_type TEXT NOT NULL,
          source_id TEXT NOT NULL,
          attached_at TEXT NOT NULL,
          attachment_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS claim_record_activity_claim
          ON claim_record_activity (claim_record_id, attached_at);
        CREATE TABLE IF NOT EXISTS intake_audit_events (
          id TEXT PRIMARY KEY,
          rejection_reason TEXT NOT NULL,
          source_event_id TEXT,
          policy_id TEXT,
          timestamp TEXT NOT NULL,
          event_json TEXT NOT NULL
        );
        """
    )
    self.db.commit()


def _resolve_bound_policy(self: PortalService, policy_id: Any) -> dict[str, Any] | None:
    if not isinstance(policy_id, str) or not policy_id:
        return None
    prefix = "synthetic-policy:"
    if not policy_id.startswith(prefix):
        return None
    dp_id = policy_id[len(prefix) :]
    row = self.db.execute(
        "SELECT id, email FROM design_partners WHERE id = ?",
        (dp_id,),
    ).fetchone()
    if not row:
        return None
    return {"policy_id": policy_id, "insured_agent_id": row["id"], "reporter_emails": {row["email"].lower()}}


def _record_intake_rejection(
    self: PortalService,
    *,
    rejection_reason: str,
    source_event_id: str | None,
    policy_id: str | None,
    event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timestamp = now_iso()
    self.db.execute(
        "INSERT INTO intake_audit_events VALUES (?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            rejection_reason,
            source_event_id,
            policy_id,
            timestamp,
            json.dumps(event or {}, sort_keys=True),
        ),
    )
    self.db.commit()
    self.audit(
        "policy_system",
        "intake_auto_open_rejected",
        "policy" if policy_id else "intake_source",
        policy_id or source_event_id or "unknown",
        {"rejection_reason": rejection_reason, "source_event_id": source_event_id},
    )
    return _adapter_response(
        claim_record_id=None,
        status="rejected",
        intake_class=None,
        opened_at=None,
        duplicate_of_claim_record_id=None,
        rejection_reason=rejection_reason,
    )


def open_claim_record(self: PortalService, auto_open_event: dict[str, Any]) -> dict[str, Any]:
    """Single policy-system write function for every auto-open source."""
    if not isinstance(auto_open_event, dict):
        raise PortalError(422, "auto_open_event_invalid", "auto_open_event must be an object.")
    source_type = _required_str(auto_open_event, "source_type")
    source_id = _required_str(auto_open_event, "source_id")
    policy_id = _required_str(auto_open_event, "policy_id")
    insured_agent_id = _required_str(auto_open_event, "insured_agent_id")
    event_occurred_at = _optional_str(auto_open_event.get("event_occurred_at"))
    severity_tier = _optional_str(auto_open_event.get("severity_tier"))
    peril_code = _optional_str(auto_open_event.get("peril_code"))
    threshold_policy_ref = _optional_str(auto_open_event.get("threshold_policy_ref"))
    threshold_policy_governed = bool(auto_open_event.get("threshold_policy_governed"))
    customer_notice_present = bool(auto_open_event.get("customer_notice_present"))
    evidence_refs = _normalize_evidence_refs(auto_open_event.get("evidence_refs"))
    wording_section_candidates = _normalize_string_list(auto_open_event.get("wording_section_candidates"))
    fingerprint = _required_str(auto_open_event, "fingerprint")
    payload_context = _pop_payload_context(self, fingerprint, source_id)
    summary = _optional_str(payload_context.get("summary"))

    policy = self._resolve_bound_policy(policy_id)
    if not policy:
        return self._record_intake_rejection(
            rejection_reason="policy_not_resolved",
            source_event_id=source_id,
            policy_id=policy_id,
            event=auto_open_event,
        )
    if policy["insured_agent_id"] != insured_agent_id:
        return self._record_intake_rejection(
            rejection_reason="insured_agent_mismatch",
            source_event_id=source_id,
            policy_id=policy_id,
            event=auto_open_event,
        )
    if not evidence_refs:
        return self._record_intake_rejection(
            rejection_reason="evidence_refs_required",
            source_event_id=source_id,
            policy_id=policy_id,
            event=auto_open_event,
        )

    intake_class = _classify_intake(auto_open_event)
    if source_type == "telemetry_threshold" and not _telemetry_event_opens(auto_open_event):
        return self._record_intake_rejection(
            rejection_reason="telemetry_event_not_open_eligible",
            source_event_id=source_id,
            policy_id=policy_id,
            event=auto_open_event,
        )
    if source_type == "customer_loss_signal" and intake_class not in {"claim_notice", "incident_watch"}:
        return self._record_intake_rejection(
            rejection_reason="customer_loss_signal_not_open_eligible",
            source_event_id=source_id,
            policy_id=policy_id,
            event=auto_open_event,
        )
    if source_type not in {"telemetry_threshold", "customer_loss_signal"}:
        return self._record_intake_rejection(
            rejection_reason="source_type_unsupported",
            source_event_id=source_id,
            policy_id=policy_id,
            event=auto_open_event,
        )

    existing = _find_duplicate(self.db, fingerprint, now_iso())
    if existing:
        self._append_claim_activity(existing["claim_record_id"], auto_open_event)
        return _adapter_response(
            claim_record_id=existing["claim_record_id"],
            status="duplicate_suppressed",
            intake_class=existing["intake_class"],
            opened_at=existing["opened_at"],
            duplicate_of_claim_record_id=existing["claim_record_id"],
            rejection_reason=None,
        )

    opened_at = now_iso()
    sla_due_at = _compute_sla_due_at(opened_at, intake_class, severity_tier)
    record = {
        "claim_record_id": str(uuid.uuid4()),
        "policy_id": policy_id,
        "insured_agent_id": insured_agent_id,
        "intake_class": intake_class,
        "status": CLAIM_RECORD_STATUS_OPEN,
        "coverage_state": COVERAGE_STATE_DEFAULT,
        "reserve_state": RESERVE_STATE_DEFAULT,
        "payment_state": PAYMENT_STATE_DEFAULT,
        "trigger_source": source_type,
        "trigger_source_id": source_id,
        "auto_open_fingerprint": fingerprint,
        "opened_at": opened_at,
        "event_occurred_at": event_occurred_at,
        "severity_tier": severity_tier,
        "peril_code": peril_code,
        "threshold_policy_ref": threshold_policy_ref,
        "threshold_policy_governed": threshold_policy_governed,
        "telemetry_manual_review_only": not threshold_policy_governed,
        "wording_section_candidates": wording_section_candidates,
        "evidence_refs": evidence_refs,
        "customer_notice_present": customer_notice_present,
        "assignment_queue": ASSIGNMENT_QUEUE,
        "sla_due_at": sla_due_at,
        "summary": summary,
    }
    _assert_claim_record_guards(record)
    self.db.execute(
        "INSERT INTO claim_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record["claim_record_id"],
            record["policy_id"],
            record["insured_agent_id"],
            record["intake_class"],
            record["status"],
            record["coverage_state"],
            record["reserve_state"],
            record["payment_state"],
            record["trigger_source"],
            record["trigger_source_id"],
            record["auto_open_fingerprint"],
            record["opened_at"],
            record["event_occurred_at"],
            record["severity_tier"],
            record["peril_code"],
            record["threshold_policy_ref"],
            int(record["threshold_policy_governed"]),
            int(record["telemetry_manual_review_only"]),
            json.dumps(record["wording_section_candidates"], sort_keys=True),
            json.dumps(record["evidence_refs"], sort_keys=True),
            int(record["customer_notice_present"]),
            record["assignment_queue"],
            record["sla_due_at"],
            record["summary"],
        ),
    )
    self.db.commit()
    self.audit(
        "policy_system",
        "claim_record_auto_opened",
        "claim_record",
        record["claim_record_id"],
        {
            "policy_id": policy_id,
            "trigger_source": source_type,
            "trigger_source_id": source_id,
            "intake_class": intake_class,
            "payload_hash": sha256_hex(json.dumps(record, sort_keys=True)),
        },
    )
    return _adapter_response(
        claim_record_id=record["claim_record_id"],
        status="opened",
        intake_class=intake_class,
        opened_at=opened_at,
        duplicate_of_claim_record_id=None,
        rejection_reason=None,
    )


def _append_claim_activity(self: PortalService, claim_record_id: str, auto_open_event: dict[str, Any]) -> None:
    payload_context = _pop_payload_context(
        self,
        _optional_str(auto_open_event.get("fingerprint")) or "",
        _optional_str(auto_open_event.get("source_id")) or "",
    )
    attachment = {
        "source_type": auto_open_event.get("source_type"),
        "source_id": auto_open_event.get("source_id"),
        "evidence_refs": _normalize_evidence_refs(auto_open_event.get("evidence_refs")),
        "customer_reference_ids": _normalize_string_list(payload_context.get("customer_reference_ids")),
        "summary": _optional_str(payload_context.get("summary")),
    }
    self.db.execute(
        "INSERT INTO claim_record_activity VALUES (?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            claim_record_id,
            str(auto_open_event.get("source_type") or ""),
            str(auto_open_event.get("source_id") or ""),
            now_iso(),
            json.dumps(attachment, sort_keys=True),
        ),
    )
    self.db.commit()
    self.audit(
        "policy_system",
        "claim_record_duplicate_activity_appended",
        "claim_record",
        claim_record_id,
        {
            "source_type": attachment["source_type"],
            "source_id": attachment["source_id"],
            "payload_hash": sha256_hex(json.dumps(attachment, sort_keys=True)),
        },
    )


def normalize_claims_queue_item(self: PortalService, claims_queue_item: dict[str, Any]) -> dict[str, Any]:
    policy_id = _required_str(claims_queue_item, "policy_id")
    source_signal_id = claims_queue_item.get("signal_envelope_id") or claims_queue_item.get("claims_queue_item_id")
    source_id = _required_str({"source_id": source_signal_id}, "source_id")
    return {
        "source_type": "telemetry_threshold",
        "source_id": source_id,
        "policy_id": policy_id,
        "insured_agent_id": _required_str(claims_queue_item, "agent_id"),
        "event_occurred_at": _required_str(claims_queue_item, "event_t"),
        "severity_tier": _optional_str(claims_queue_item.get("severity_tier")),
        "peril_code": _optional_str(claims_queue_item.get("peril_code")),
        "threshold_policy_ref": _optional_str(claims_queue_item.get("threshold_policy_ref")),
        "threshold_policy_governed": bool(claims_queue_item.get("threshold_policy_governed")),
        "customer_notice_present": bool(claims_queue_item.get("customer_notice_present")),
        "evidence_refs": _normalize_evidence_refs(claims_queue_item.get("evidence_refs")),
        "wording_section_candidates": lookup_wording_section_candidates(claims_queue_item.get("peril_code")),
        "fingerprint": f"telemetry:{policy_id}:{source_id}",
    }


def normalize_claims_trigger_envelope(self: PortalService, envelope: dict[str, Any]) -> dict[str, Any]:
    dp_id = _required_str(envelope, "dp_id")
    policy_id = str(envelope.get("policy_id") or f"synthetic-policy:{dp_id}")
    source_id = _required_str(envelope, "claims_trigger_envelope_id")
    severity_tier = _severity_from_claim_eligibility(_optional_str(envelope.get("claim_eligibility_class")))
    return {
        "source_type": "telemetry_threshold",
        "source_id": source_id,
        "policy_id": policy_id,
        "insured_agent_id": dp_id,
        "event_occurred_at": _optional_str(envelope.get("trigger_window_end") or envelope.get("produced_at")),
        "severity_tier": severity_tier,
        "peril_code": _peril_from_event_type_counts(envelope.get("event_type_counts")),
        "threshold_policy_ref": "BEAAA-5817_v0.3",
        "threshold_policy_governed": True,
        "customer_notice_present": bool(envelope.get("customer_notice_present")),
        "evidence_refs": _normalize_evidence_refs(envelope.get("redacted_evidence_refs")),
        "wording_section_candidates": lookup_wording_section_candidates(
            _peril_from_event_type_counts(envelope.get("event_type_counts"))
        ),
        "fingerprint": f"telemetry:{policy_id}:{source_id}",
    }


def open_claim_from_claims_queue_item(self: PortalService, claims_queue_item: dict[str, Any]) -> dict[str, Any]:
    return self.open_claim_record(self.normalize_claims_queue_item(claims_queue_item))


def open_claim_from_claims_trigger_envelope(self: PortalService, envelope: dict[str, Any]) -> dict[str, Any]:
    return self.open_claim_record(self.normalize_claims_trigger_envelope(envelope))


def handle_customer_loss_signal(self: PortalService, policy_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return self._record_intake_rejection(
            rejection_reason="json_body_required",
            source_event_id=None,
            policy_id=policy_id,
            event={},
        )
    missing = [field for field in _customer_required_fields() if not payload.get(field)]
    if missing:
        return self._record_intake_rejection(
            rejection_reason=f"missing_required_fields:{','.join(missing)}",
            source_event_id=_optional_str(payload.get("customer_loss_signal_id")),
            policy_id=policy_id,
            event=_safe_customer_event(payload),
        )
    loss_type = payload.get("loss_type")
    if loss_type not in CUSTOMER_LOSS_TYPES:
        return self._record_intake_rejection(
            rejection_reason="loss_type_invalid",
            source_event_id=_optional_str(payload.get("customer_loss_signal_id")),
            policy_id=policy_id,
            event=_safe_customer_event(payload),
        )
    policy = self._resolve_bound_policy(policy_id)
    reporter_email = str(payload.get("reporter_email") or "").lower()
    if not policy or reporter_email not in policy["reporter_emails"]:
        return self._record_intake_rejection(
            rejection_reason="reporter_not_enrolled_on_policy",
            source_event_id=_optional_str(payload.get("customer_loss_signal_id")),
            policy_id=policy_id,
            event=_safe_customer_event(payload),
        )
    evidence_refs = _normalize_evidence_refs(payload.get("evidence_refs"))
    customer_refs = _normalize_string_list(payload.get("customer_reference_ids"))
    if not evidence_refs and not customer_refs:
        return self._record_intake_rejection(
            rejection_reason="evidence_or_customer_reference_required",
            source_event_id=_optional_str(payload.get("customer_loss_signal_id")),
            policy_id=policy_id,
            event=_safe_customer_event(payload),
        )
    signal_id = _optional_str(payload.get("customer_loss_signal_id"))
    fingerprint = _customer_fingerprint(policy_id, payload)
    summary = _required_str(payload, "summary")
    informational_only = _is_informational_only(payload)
    auto_open_event = {
        "source_type": "customer_loss_signal",
        "source_id": signal_id or fingerprint,
        "policy_id": policy_id,
        "insured_agent_id": _required_str(payload, "affected_agent_id"),
        "event_occurred_at": _required_str(payload, "reported_event_at"),
        "severity_tier": None,
        "peril_code": _customer_peril_from_loss_type(loss_type),
        "threshold_policy_ref": None,
        "threshold_policy_governed": False,
        "customer_notice_present": not informational_only,
        "evidence_refs": evidence_refs or [f"customer-reference://{ref}" for ref in customer_refs],
        "wording_section_candidates": lookup_wording_section_candidates(_customer_peril_from_loss_type(loss_type)),
        "fingerprint": fingerprint,
    }
    _set_payload_context(
        self,
        fingerprint=fingerprint,
        source_id=signal_id or fingerprint,
        payload={"summary": summary, "customer_reference_ids": customer_refs},
    )
    return self.open_claim_record(auto_open_event)


def get_claim_record(self: PortalService, claim_record_id: str) -> dict[str, Any]:
    row = self.db.execute(
        "SELECT * FROM claim_records WHERE claim_record_id = ?",
        (claim_record_id,),
    ).fetchone()
    if not row:
        raise PortalError(404, "claim_record_not_found", "Claim record not found.")
    return _serialize_claim_record(row)


def list_claim_record_activity(self: PortalService, claim_record_id: str) -> list[dict[str, Any]]:
    rows = self.db.execute(
        "SELECT * FROM claim_record_activity WHERE claim_record_id = ? ORDER BY attached_at ASC, id ASC",
        (claim_record_id,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "claim_record_id": row["claim_record_id"],
            "source_type": row["source_type"],
            "source_id": row["source_id"],
            "attached_at": row["attached_at"],
            "attachment": json.loads(row["attachment_json"]),
        }
        for row in rows
    ]


def lookup_wording_section_candidates(peril_code: Any) -> list[str]:
    """Stub for HoUW wording schedule lookup; keep importable, not inlined."""
    if peril_code == "P4":
        return ["coverage trigger label: hallucination / false output peril"]
    if peril_code == "P6":
        return ["coverage trigger label: regulatory response / governance-control failure peril"]
    return []


def handle_get(service: PortalService, parts: list[str], query: dict[str, list[str]] | None = None):
    if len(parts) == 6 and parts[:3] == ["api", "v1", "policies"] and parts[4] == "claim-records":
        payload = service.get_claim_record(parts[5])
        payload["activity"] = service.list_claim_record_activity(parts[5])
        return 200, json.dumps(payload, sort_keys=True).encode("utf-8"), "application/json"
    return None


def handle_post(service: PortalService, parts: list[str], body):
    if (
        len(parts) == 6
        and parts[:3] == ["api", "v1", "policies"]
        and parts[4] == "claim-intake"
        and parts[5] == "customer-loss-signal"
    ):
        if not isinstance(body, dict):
            raise PortalError(415, "json_body_required", "Customer loss signal endpoint requires application/json.")
        payload = service.handle_customer_loss_signal(parts[3], body)
        return 201, json.dumps(payload, sort_keys=True).encode("utf-8"), "application/json"
    return None


_BOUND_NAMES = (
    "_init_policy_auto_open_tables",
    "_resolve_bound_policy",
    "_record_intake_rejection",
    "open_claim_record",
    "_append_claim_activity",
    "normalize_claims_queue_item",
    "normalize_claims_trigger_envelope",
    "open_claim_from_claims_queue_item",
    "open_claim_from_claims_trigger_envelope",
    "handle_customer_loss_signal",
    "get_claim_record",
    "list_claim_record_activity",
)


def attach(cls: type[PortalService]) -> type[PortalService]:
    for name in _BOUND_NAMES:
        setattr(cls, name, globals()[name])

    original_init = cls.__init__

    def __init__(self, config):  # type: ignore[override]
        original_init(self, config)
        if not getattr(self, "_policy_auto_open_tables_ready", False):
            self._init_policy_auto_open_tables()
            self._policy_auto_open_tables_ready = True

    cls.__init__ = __init__
    return cls


def _adapter_response(
    *,
    claim_record_id: str | None,
    status: str,
    intake_class: str | None,
    opened_at: str | None,
    duplicate_of_claim_record_id: str | None,
    rejection_reason: str | None,
) -> dict[str, Any]:
    return {
        "claim_record_id": claim_record_id,
        "status": status,
        "intake_class": intake_class,
        "opened_at": opened_at,
        "duplicate_of_claim_record_id": duplicate_of_claim_record_id,
        "rejection_reason": rejection_reason,
    }


def _required_str(source: dict[str, Any], field: str) -> str:
    value = source.get(field)
    if not isinstance(value, str) or not value:
        raise PortalError(422, f"{field}_required", f"{field} is required.")
    return value


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _normalize_evidence_refs(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PortalError(422, "evidence_refs_invalid", "evidence_refs must be a list.")
    refs: list[str] = []
    for item in value:
        if isinstance(item, str) and item:
            refs.append(item)
        elif isinstance(item, dict) and isinstance(item.get("artifact_uri"), str):
            refs.append(item["artifact_uri"])
        else:
            raise PortalError(422, "evidence_refs_invalid", "evidence_refs entries must be pointer strings or objects with artifact_uri.")
    return refs


def _normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PortalError(422, "string_list_invalid", "Expected a list of strings.")
    return [item for item in value if isinstance(item, str) and item]


def _find_duplicate(db: sqlite3.Connection, fingerprint: str, as_of_iso: str) -> sqlite3.Row | None:
    as_of = _parse_iso(as_of_iso)
    threshold = (as_of - timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    return db.execute(
        "SELECT * FROM claim_records WHERE auto_open_fingerprint = ? AND opened_at >= ? ORDER BY opened_at DESC LIMIT 1",
        (fingerprint, threshold),
    ).fetchone()


def _assert_claim_record_guards(record: dict[str, Any]) -> None:
    if (
        record["coverage_state"] != COVERAGE_STATE_DEFAULT
        or record["reserve_state"] != RESERVE_STATE_DEFAULT
        or record["payment_state"] != PAYMENT_STATE_DEFAULT
    ):
        raise PortalError(500, "claim_record_guard_failed", "Auto-open cannot set coverage, reserve, or payment.")


def _telemetry_event_opens(event: dict[str, Any]) -> bool:
    eligibility = event.get("claim_eligibility_class")
    if eligibility in OPEN_CLAIM_ELIGIBILITY_CLASSES:
        return True
    return bool(event.get("threshold_policy_governed")) and event.get("severity_tier") in OPEN_TELEMETRY_SEVERITIES


def _classify_intake(event: dict[str, Any]) -> str:
    if event.get("source_type") == "customer_loss_signal":
        return "claim_notice" if event.get("customer_notice_present") else "incident_watch"
    if event.get("customer_notice_present"):
        return "claim_notice"
    return "incident_watch" if event.get("severity_tier") == "S4" else "claim_notice"


def _severity_from_claim_eligibility(value: str | None) -> str | None:
    if value == "interim_payout_eligible":
        return "S4"
    if value == "review_required":
        return "S3"
    return None


def _peril_from_event_type_counts(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    mapping = {
        "hallucination": "P4",
        "regulatory_response": "P6",
        "data_leak": "P3",
        "unauthorized_commitment": "P2",
    }
    for event_type, peril in mapping.items():
        if int(value.get(event_type) or 0) > 0:
            return peril
    return None


def _customer_peril_from_loss_type(loss_type: Any) -> str | None:
    if loss_type == "regulatory_notice":
        return "P6"
    if loss_type in {"financial_loss", "third_party_demand"}:
        return "P4"
    if loss_type == "data_incident":
        return "P3"
    return None


def _customer_required_fields() -> tuple[str, ...]:
    return (
        "customer_loss_signal_id",
        "reported_at",
        "reported_event_at",
        "affected_agent_id",
        "reporter_email",
        "loss_type",
        "summary",
        "evidence_refs",
    )


def _customer_fingerprint(policy_id: str, payload: dict[str, Any]) -> str:
    signal_id = _optional_str(payload.get("customer_loss_signal_id"))
    if signal_id:
        return f"customer:{policy_id}:{signal_id}"
    reported_event_at = _required_str(payload, "reported_event_at")
    affected_agent_id = _required_str(payload, "affected_agent_id")
    loss_type = str(payload.get("loss_type") or "").lower().strip()
    summary_hash = hashlib.sha256(_normalize_summary(_required_str(payload, "summary")).encode("utf-8")).hexdigest()
    return f"customer:{policy_id}:{reported_event_at}:{affected_agent_id}:{loss_type}:{summary_hash}"


def _normalize_summary(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _is_informational_only(payload: dict[str, Any]) -> bool:
    summary = _normalize_summary(str(payload.get("summary") or ""))
    if payload.get("alleged_loss_amount_usd") not in (None, 0, "0"):
        return False
    return "informational only" in summary and "no loss" in summary and "no demand" in summary


def _set_payload_context(
    service: PortalService,
    *,
    fingerprint: str,
    source_id: str,
    payload: dict[str, Any],
) -> None:
    context = getattr(service, "_policy_auto_open_payload_context", None)
    if context is None:
        context = {}
        setattr(service, "_policy_auto_open_payload_context", context)
    context[fingerprint] = payload
    context[source_id] = payload


def _pop_payload_context(service: PortalService, fingerprint: str, source_id: str) -> dict[str, Any]:
    context = getattr(service, "_policy_auto_open_payload_context", None)
    if not isinstance(context, dict):
        return {}
    payload = context.pop(fingerprint, None) or context.pop(source_id, None) or {}
    context.pop(fingerprint, None)
    context.pop(source_id, None)
    return payload if isinstance(payload, dict) else {}


def _safe_customer_event(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "customer_loss_signal_id": payload.get("customer_loss_signal_id"),
        "reported_at": payload.get("reported_at"),
        "reported_event_at": payload.get("reported_event_at"),
        "affected_agent_id": payload.get("affected_agent_id"),
        "loss_type": payload.get("loss_type"),
        "evidence_refs": payload.get("evidence_refs"),
        "customer_reference_ids": payload.get("customer_reference_ids"),
        "summary": payload.get("summary"),
    }


def _compute_sla_due_at(opened_at: str, intake_class: str, severity_tier: str | None) -> str:
    opened = _parse_iso(opened_at)
    if intake_class == "claim_notice" or severity_tier == "S4":
        due = _add_business_hours(opened, 1)
    else:
        due = _same_business_day_close(opened)
    return due.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _is_business_time(value: datetime) -> bool:
    return value.weekday() < 5 and 9 <= value.hour < 17


def _next_business_start(value: datetime) -> datetime:
    candidate = value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    if candidate.hour >= 17:
        candidate = candidate + timedelta(days=1)
    if candidate.hour < 9:
        candidate = candidate.replace(hour=9)
    while candidate.weekday() >= 5:
        candidate = (candidate + timedelta(days=1)).replace(hour=9)
    return candidate


def _add_business_hours(start: datetime, hours: int) -> datetime:
    current = start if _is_business_time(start) else _next_business_start(start)
    remaining = hours
    while remaining > 0:
        end_of_day = current.replace(hour=17, minute=0, second=0, microsecond=0)
        available_hours = max((end_of_day - current).total_seconds() / 3600, 0)
        if available_hours >= remaining:
            return current + timedelta(hours=remaining)
        remaining -= int(available_hours)
        current = _next_business_start(end_of_day + timedelta(seconds=1))
    return current


def _same_business_day_close(start: datetime) -> datetime:
    current = start if start.weekday() < 5 and start.hour < 17 else _next_business_start(start)
    if current.hour < 9:
        current = current.replace(hour=9)
    return current.replace(hour=17, minute=0, second=0, microsecond=0)


def _serialize_claim_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "claim_record_id": row["claim_record_id"],
        "policy_id": row["policy_id"],
        "insured_agent_id": row["insured_agent_id"],
        "intake_class": row["intake_class"],
        "status": row["status"],
        "coverage_state": row["coverage_state"],
        "reserve_state": row["reserve_state"],
        "payment_state": row["payment_state"],
        "trigger_source": row["trigger_source"],
        "trigger_source_id": row["trigger_source_id"],
        "auto_open_fingerprint": row["auto_open_fingerprint"],
        "opened_at": row["opened_at"],
        "event_occurred_at": row["event_occurred_at"],
        "severity_tier": row["severity_tier"],
        "peril_code": row["peril_code"],
        "threshold_policy_ref": row["threshold_policy_ref"],
        "threshold_policy_governed": bool(row["threshold_policy_governed"]),
        "telemetry_manual_review_only": bool(row["telemetry_manual_review_only"]),
        "wording_section_candidates": json.loads(row["wording_section_candidates_json"]),
        "evidence_refs": json.loads(row["evidence_refs_json"]),
        "customer_notice_present": bool(row["customer_notice_present"]),
        "assignment_queue": row["assignment_queue"],
        "sla_due_at": row["sla_due_at"],
        "summary": row["summary"],
    }
