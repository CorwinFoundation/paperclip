"""BEAAA-4967 / BEAAA-4912 - portal-side log-based telemetry MVP (v0.1).

Implements the four additive surfaces in telemetry-api-v0.md sha256
015f9ea770d499efd6dd076df8538537bf9ff53100c25ce2f6558d4bdd591508:

  - POST /telemetry/redacted-record (intake, PII defense-in-depth, audit)
  - Deterministic 6-event scanner -> signed signal envelopes
  - Degradation trigger -> re_pricing_event + claims_trigger_envelope
  - GET /dp/{dp_id}/signal-envelopes
  - GET /dp/{dp_id}/re-pricing-events
  - GET /dp/{dp_id}/claims-trigger-envelopes
  - PUT /dp/{dp_id}/telemetry-trigger (suspend/resume)
  - Five new audit-log event types per contract section 5.6
  - portal_pins.json gains telemetry_api_version + event_rule_corpus_version

Boundary rules respected:
  - additive only; no v0.1 / v0.2 wire field reshape
  - no are-scanner-api-v1@v1.0 Output mutation; pin block carried on envelopes only
  - no LLM judges in v0.1 detection
  - match_excerpt_hash never persists the matched substring
  - PII-regex defense-in-depth at intake; drop + emit redaction_failed_at_intake
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from portal import (
    CORPUS_VERSION,
    PortalError,
    PortalService,
    RUBRIC_VERSION,
    SCANNER_VERSION,
    b64url,
    canonical_json,
    now_iso,
    sha256_hex,
    unb64url,
)


TELEMETRY_API_VERSION = "0.1"
EVENT_RULE_CORPUS_VERSION = "0.1"
DEGRADATION_RULES_VERSION = "0.1"
TELEMETRY_REDACTED_RECORD_MAX_BYTES = 64 * 1024

CANONICAL_EVENT_TYPES = (
    "wrong_recipient",
    "unauthorized_commitment",
    "data_leak",
    "hallucination",
    "prompt_injection",
    "regulatory_response",
)

# Defense-in-depth PII regexes evaluated at intake. Conservative: matches are a
# kill switch on the record, not a classifier. Patterns that would also fire on
# already-redacted text use word boundaries to avoid noise on safe placeholders.
PII_INTAKE_PATTERNS = (
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "email_address"),
    (r"\b\d{3}-\d{2}-\d{4}\b", "ssn_dashed"),
    (r"\b(?:\d[ -]?){13,19}\b", "luhn_candidate_number"),
    (r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b", "phone_us_formatted"),
)


# --- corpus load / sign / verify ------------------------------------------------------


def _corpus_base(self: PortalService) -> Path:
    # Repo-rooted corpus directory; the portal_data dir is not a corpus root.
    return Path(__file__).resolve().parents[2] / "corpus" / "telemetry"


def _load_signed_corpus(
    self: PortalService, file_name: str, expected_version_field: str, expected_version: str
) -> dict[str, Any]:
    path = self._corpus_base() / file_name
    if not path.exists():
        raise PortalError(503, "telemetry_corpus_missing", f"Telemetry corpus missing: {file_name}")
    body = json.loads(path.read_text(encoding="utf-8"))
    if body.get(expected_version_field) != expected_version:
        raise PortalError(503, "telemetry_corpus_version_mismatch", f"{file_name} version field mismatch")
    canonical = canonical_json({k: v for k, v in body.items() if k != "_signature"})
    body_sha256 = sha256_hex(canonical)
    # Sign at load with the portal CA (PKCS1v15+SHA256 is deterministic). The
    # signature + sha256 are exposed for in-process tamper detection: if the JSON
    # on disk changes the sha256 changes; if the CA key changes the signature
    # changes. Cross-portal sidecar files are intentionally avoided because each
    # portal instance owns its own CA per v0.1 section 5.4.
    signature_b64 = b64url(self._ca_key().sign(canonical, padding.PKCS1v15(), hashes.SHA256()))
    body["_signature"] = signature_b64
    body["_sha256"] = body_sha256
    return body


def event_rule_corpus(self: PortalService) -> dict[str, Any]:
    cached = getattr(self, "_telemetry_event_corpus", None)
    if cached is not None:
        return cached
    body = self._load_signed_corpus(
        "event_rules_v0.1.json", "event_rule_corpus_version", EVENT_RULE_CORPUS_VERSION
    )
    events = body.get("events", {})
    missing = [et for et in CANONICAL_EVENT_TYPES if et not in events]
    if missing:
        raise PortalError(
            503,
            "telemetry_corpus_events_missing",
            f"event_rules_v0.1.json missing events: {sorted(missing)}",
        )
    self._telemetry_event_corpus = body
    return body


def degradation_rules_corpus(self: PortalService) -> dict[str, Any]:
    cached = getattr(self, "_telemetry_degradation_corpus", None)
    if cached is not None:
        return cached
    body = self._load_signed_corpus(
        "degradation_rules_v0.1.json", "schema_version", DEGRADATION_RULES_VERSION
    )
    for required in ("event_type_weights", "per_event_type_count_thresholds", "combined_severity_threshold", "scope"):
        if required not in body:
            raise PortalError(
                503,
                "telemetry_corpus_field_missing",
                f"degradation_rules_v0.1.json missing field: {required}",
            )
    self._telemetry_degradation_corpus = body
    return body


# --- table init -----------------------------------------------------------------------


def _init_telemetry_tables(self: PortalService) -> None:
    self.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS telemetry_records (
          connector_id TEXT NOT NULL,
          agent_call_id TEXT NOT NULL,
          dp_id TEXT NOT NULL,
          capture_window_start TEXT NOT NULL,
          received_at TEXT NOT NULL,
          record_json TEXT NOT NULL,
          PRIMARY KEY (connector_id, agent_call_id)
        );
        CREATE TABLE IF NOT EXISTS signal_envelopes (
          id TEXT PRIMARY KEY,
          dp_id TEXT NOT NULL,
          connector_id TEXT NOT NULL,
          agent_call_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          produced_at TEXT NOT NULL,
          aggregate_weight REAL NOT NULL,
          envelope_json TEXT NOT NULL,
          portal_signature TEXT NOT NULL,
          consumed_by_trigger TEXT
        );
        CREATE INDEX IF NOT EXISTS signal_envelopes_dp_time
          ON signal_envelopes (dp_id, produced_at);
        CREATE TABLE IF NOT EXISTS re_pricing_events (
          id TEXT PRIMARY KEY,
          dp_id TEXT NOT NULL,
          trigger_window_start TEXT NOT NULL,
          trigger_window_end TEXT NOT NULL,
          produced_at TEXT NOT NULL,
          aggregate_severity REAL NOT NULL,
          envelope_json TEXT NOT NULL,
          portal_signature TEXT NOT NULL,
          UNIQUE (dp_id, trigger_window_start)
        );
        CREATE TABLE IF NOT EXISTS claims_trigger_envelopes (
          id TEXT PRIMARY KEY,
          dp_id TEXT NOT NULL,
          trigger_window_start TEXT NOT NULL,
          trigger_window_end TEXT NOT NULL,
          produced_at TEXT NOT NULL,
          eligibility_class TEXT NOT NULL,
          envelope_json TEXT NOT NULL,
          portal_signature TEXT NOT NULL,
          UNIQUE (dp_id, trigger_window_start)
        );
        CREATE TABLE IF NOT EXISTS telemetry_trigger_state (
          dp_id TEXT PRIMARY KEY,
          suspended INTEGER NOT NULL DEFAULT 0,
          reason TEXT,
          founder_cosign_ref TEXT,
          houw_cosign_ref TEXT,
          updated_at TEXT NOT NULL,
          updated_by TEXT NOT NULL
        );
        """
    )
    self.db.commit()


# --- intake ---------------------------------------------------------------------------


def _validate_redacted_record_shape(self: PortalService, body: dict[str, Any]) -> None:
    required = (
        "telemetry_api_version",
        "connector_id",
        "dp_id",
        "agent_call_id",
        "capture_window_start",
        "redaction_policy_signature",
        "redaction_policy_version",
        "redacted_output_text",
        "structural_fields_redacted",
        "agent_endpoint_config_hash",
        "connector_signature",
    )
    for field in required:
        if field not in body:
            raise PortalError(422, "telemetry_field_missing", f"redacted record missing field: {field}")
    if body["telemetry_api_version"] != TELEMETRY_API_VERSION:
        raise PortalError(422, "telemetry_api_version_mismatch", "telemetry_api_version must be 0.1.")
    if not isinstance(body["redacted_output_text"], str):
        raise PortalError(422, "telemetry_redacted_text_invalid", "redacted_output_text must be a string.")
    if len(body["redacted_output_text"].encode("utf-8")) > TELEMETRY_REDACTED_RECORD_MAX_BYTES:
        raise PortalError(413, "telemetry_redacted_text_too_large", "redacted_output_text exceeds 64KB.")
    if not isinstance(body["structural_fields_redacted"], list):
        raise PortalError(422, "telemetry_structural_fields_invalid", "structural_fields_redacted must be a list.")


def _intake_pii_scan(self: PortalService, text: str) -> str | None:
    for pattern, label in PII_INTAKE_PATTERNS:
        if re.search(pattern, text):
            return label
    return None


def submit_redacted_record(self: PortalService, body: dict[str, Any]) -> dict[str, Any]:
    self._validate_redacted_record_shape(body)
    connector = self.db.execute(
        "SELECT * FROM connectors WHERE id = ?", (body["connector_id"],)
    ).fetchone()
    if not connector:
        raise PortalError(404, "connector_not_found", "Connector not enrolled.")
    if connector["dp_id"] != body["dp_id"]:
        raise PortalError(422, "connector_dp_mismatch", "connector_id does not belong to declared dp_id.")
    existing = self.db.execute(
        "SELECT * FROM telemetry_records WHERE connector_id = ? AND agent_call_id = ?",
        (body["connector_id"], body["agent_call_id"]),
    ).fetchone()
    if existing:
        stored = json.loads(existing["record_json"])
        signal_rows = list(
            self.db.execute(
                "SELECT id, event_type FROM signal_envelopes WHERE connector_id = ? AND agent_call_id = ?",
                (body["connector_id"], body["agent_call_id"]),
            )
        )
        return {
            "received": True,
            "idempotent": True,
            "record": stored,
            "signal_envelope_ids": [row["id"] for row in signal_rows],
        }
    pii_hit = self._intake_pii_scan(body["redacted_output_text"])
    if pii_hit:
        self.audit(
            "portal",
            "redaction_failed_at_intake",
            "redacted_record",
            body["agent_call_id"],
            {"reason": f"pii_regex_hit:{pii_hit}", "dp_id": body["dp_id"]},
        )
        raise PortalError(
            422,
            "redaction_failed_at_intake",
            f"Defense-in-depth PII regex hit on redacted_output_text: {pii_hit}",
        )
    stored = dict(body)
    self.db.execute(
        "INSERT INTO telemetry_records (connector_id, agent_call_id, dp_id, capture_window_start, received_at, record_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            body["connector_id"],
            body["agent_call_id"],
            body["dp_id"],
            body["capture_window_start"],
            now_iso(),
            json.dumps(stored, sort_keys=True),
        ),
    )
    self.db.commit()
    audit_body = {k: v for k, v in stored.items() if k != "connector_signature"}
    self.audit(
        "connector",
        "redacted_record_received",
        "redacted_record",
        body["agent_call_id"],
        {"payload_hash": sha256_hex(canonical_json(audit_body)), "dp_id": body["dp_id"]},
    )
    envelopes = self._scan_and_emit_signals(stored)
    trigger_outcome = self._evaluate_degradation_trigger(body["dp_id"])
    return {
        "received": True,
        "idempotent": False,
        "signal_envelope_ids": [env["signal_envelope_id"] for env in envelopes],
        "trigger_fired": trigger_outcome.get("fired", False) if trigger_outcome else False,
        "re_pricing_event_id": (trigger_outcome or {}).get("re_pricing_event_id"),
        "claims_trigger_envelope_id": (trigger_outcome or {}).get("claims_trigger_envelope_id"),
    }


# --- detection ------------------------------------------------------------------------


def _detect_event_matches(self: PortalService, text: str, event_def: dict[str, Any]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for rule in event_def.get("rules", []):
        kind = rule.get("rule_kind")
        flags = re.IGNORECASE if rule.get("case_insensitive", False) else 0
        weight = float(rule.get("weight", 1.0))
        rule_id = rule["rule_id"]
        if kind == "regex":
            pattern = rule["pattern"]
            match = re.search(pattern, text, flags=flags)
            if match:
                matches.append(
                    {
                        "rule_id": rule_id,
                        "rule_kind": "regex",
                        "match_excerpt_hash": sha256_hex(match.group(0)),
                        "weight": weight,
                    }
                )
        elif kind == "keyword":
            needle = rule["pattern"]
            if flags & re.IGNORECASE:
                idx = text.lower().find(needle.lower())
            else:
                idx = text.find(needle)
            if idx >= 0:
                excerpt = text[idx : idx + len(needle)]
                matches.append(
                    {
                        "rule_id": rule_id,
                        "rule_kind": "keyword",
                        "match_excerpt_hash": sha256_hex(excerpt),
                        "weight": weight,
                    }
                )
        elif kind == "structural":
            patterns = rule.get("patterns", [])
            all_hit = True
            combined_hash_input = ""
            for sub in patterns:
                sub_match = re.search(sub, text, flags=flags)
                if not sub_match:
                    all_hit = False
                    break
                combined_hash_input += sub_match.group(0)
            if all_hit and patterns:
                matches.append(
                    {
                        "rule_id": rule_id,
                        "rule_kind": "structural",
                        "match_excerpt_hash": sha256_hex(combined_hash_input),
                        "weight": weight,
                    }
                )
    return matches


def _build_signal_envelope(
    self: PortalService,
    record: dict[str, Any],
    event_type: str,
    rule_matches: list[dict[str, Any]],
) -> dict[str, Any]:
    envelope_id = str(uuid.uuid4())
    produced_at = now_iso()
    redacted_record_ref = (
        f"portal-evidence://{record['connector_id']}/redacted-records/{record['agent_call_id']}"
    )
    body = {
        "telemetry_api_version": TELEMETRY_API_VERSION,
        "event_rule_corpus_version": EVENT_RULE_CORPUS_VERSION,
        "signal_envelope_id": envelope_id,
        "dp_id": record["dp_id"],
        "agent_call_id": record["agent_call_id"],
        "produced_at": produced_at,
        "event_type": event_type,
        "rule_matches": rule_matches,
        "redacted_record_ref": redacted_record_ref,
        "scanner_pins": {
            "scanner_version": SCANNER_VERSION,
            "corpus_version": CORPUS_VERSION,
            "rubric_version": RUBRIC_VERSION,
            "telemetry_api_version": TELEMETRY_API_VERSION,
            "event_rule_corpus_version": EVENT_RULE_CORPUS_VERSION,
        },
    }
    return body


def _scan_and_emit_signals(self: PortalService, record: dict[str, Any]) -> list[dict[str, Any]]:
    corpus = self.event_rule_corpus()
    events = corpus["events"]
    text = record["redacted_output_text"]
    emitted: list[dict[str, Any]] = []
    for event_type in CANONICAL_EVENT_TYPES:
        event_def = events.get(event_type)
        if not event_def:
            continue
        matches = self._detect_event_matches(text, event_def)
        if not matches:
            continue
        envelope_body = self._build_signal_envelope(record, event_type, matches)
        portal_signature = b64url(self._counter_key().sign(canonical_json(envelope_body)))
        signed = {**envelope_body, "portal_signature": portal_signature}
        self.db.execute(
            "INSERT INTO signal_envelopes (id, dp_id, connector_id, agent_call_id, event_type, produced_at, aggregate_weight, envelope_json, portal_signature, consumed_by_trigger) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                envelope_body["signal_envelope_id"],
                record["dp_id"],
                record["connector_id"],
                record["agent_call_id"],
                event_type,
                envelope_body["produced_at"],
                sum(m["weight"] for m in matches),
                json.dumps(signed, sort_keys=True),
                portal_signature,
            ),
        )
        audit_payload = {k: v for k, v in envelope_body.items()}
        self.audit(
            "portal",
            "signal_envelope_emitted",
            "signal_envelope",
            envelope_body["signal_envelope_id"],
            {
                "payload_hash": sha256_hex(canonical_json(audit_payload)),
                "dp_id": record["dp_id"],
                "event_type": event_type,
            },
        )
        emitted.append(signed)
    self.db.commit()
    return emitted


# --- degradation trigger --------------------------------------------------------------


def _parse_iso(self: PortalService, value: str) -> datetime:
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when


def _window_seconds(self: PortalService, rolling_window: str) -> int:
    if not (rolling_window.startswith("P") and rolling_window.endswith("D")):
        raise PortalError(503, "telemetry_window_format", f"Unsupported rolling_window: {rolling_window}")
    days = int(rolling_window[1:-1])
    return days * 24 * 3600


def _evaluate_degradation_trigger(self: PortalService, dp_id: str) -> dict[str, Any] | None:
    degradation = self.degradation_rules_corpus()
    rule_id = degradation.get("degradation_rule_id", "dp_7d_weighted_event_threshold_v0_1")
    scope = degradation["scope"]
    window_seconds = self._window_seconds(scope["rolling_window"])
    state = self.db.execute(
        "SELECT * FROM telemetry_trigger_state WHERE dp_id = ?", (dp_id,)
    ).fetchone()
    suspended = bool(state and state["suspended"])
    candidate_rows = list(
        self.db.execute(
            "SELECT * FROM signal_envelopes WHERE dp_id = ? AND consumed_by_trigger IS NULL ORDER BY produced_at ASC, id ASC",
            (dp_id,),
        )
    )
    if not candidate_rows:
        return None
    latest_at = self._parse_iso(candidate_rows[-1]["produced_at"])
    cutoff = latest_at - timedelta(seconds=window_seconds)
    in_window = [
        row for row in candidate_rows if self._parse_iso(row["produced_at"]) >= cutoff
    ]
    if not in_window:
        return None
    event_type_counts = {et: 0 for et in CANONICAL_EVENT_TYPES}
    for row in in_window:
        if row["event_type"] in event_type_counts:
            event_type_counts[row["event_type"]] += 1
    weights = degradation["event_type_weights"]
    aggregate_severity = sum(
        event_type_counts[et] * float(weights.get(et, 0)) for et in CANONICAL_EVENT_TYPES
    )
    per_event_thresholds = degradation["per_event_type_count_thresholds"]
    combined_threshold = float(degradation["combined_severity_threshold"])
    minimum_signals = int(
        degradation.get("trigger_logic", {}).get("minimum_signal_envelopes", 1)
    )
    if len(in_window) < minimum_signals:
        return None
    any_per_event_met = any(
        event_type_counts[et] >= int(per_event_thresholds.get(et, 10**9))
        for et in CANONICAL_EVENT_TYPES
    )
    aggregate_met = aggregate_severity >= combined_threshold
    if not (any_per_event_met or aggregate_met):
        return None
    trigger_window_start = in_window[0]["produced_at"]
    trigger_window_end = in_window[-1]["produced_at"]
    signal_envelope_ids = [row["id"] for row in in_window]
    if suspended:
        # Per contract section 4.3 / section 5.1, suspend stops downstream emit but
        # does not stop intake; consumed_by_trigger stays NULL so that on resume the
        # window is re-evaluated against the same candidates.
        self.audit(
            "portal",
            "trigger_emit_suspended",
            "telemetry_trigger",
            dp_id,
            {
                "dp_id": dp_id,
                "trigger_window_start": trigger_window_start,
                "trigger_window_end": trigger_window_end,
                "event_type_counts": event_type_counts,
                "aggregate_severity": aggregate_severity,
                "candidate_signal_envelope_ids": signal_envelope_ids,
            },
        )
        return {
            "fired": False,
            "suspended": True,
            "event_type_counts": event_type_counts,
            "aggregate_severity": aggregate_severity,
        }
    existing_repricing = self.db.execute(
        "SELECT id FROM re_pricing_events WHERE dp_id = ? AND trigger_window_start = ?",
        (dp_id, trigger_window_start),
    ).fetchone()
    if existing_repricing:
        return {"fired": False, "idempotent_hit": True, "re_pricing_event_id": existing_repricing["id"]}
    current_score_ref = self._current_backbond_score_ref(dp_id)
    repricing = self._build_re_pricing_event(
        dp_id=dp_id,
        trigger_window_start=trigger_window_start,
        trigger_window_end=trigger_window_end,
        rule_id=rule_id,
        signal_envelope_ids=signal_envelope_ids,
        aggregate_severity=aggregate_severity,
        event_type_counts=event_type_counts,
        current_backbond_score_ref=current_score_ref,
    )
    claims_envelope = self._build_claims_trigger_envelope(
        dp_id=dp_id,
        trigger_window_start=trigger_window_start,
        trigger_window_end=trigger_window_end,
        rule_id=rule_id,
        signal_envelope_ids=signal_envelope_ids,
        event_type_counts=event_type_counts,
        aggregate_severity=aggregate_severity,
    )
    # Mark consumed.
    consume_marker = repricing["re_pricing_event_id"]
    placeholders = ",".join("?" for _ in signal_envelope_ids)
    self.db.execute(
        f"UPDATE signal_envelopes SET consumed_by_trigger = ? WHERE id IN ({placeholders})",
        (consume_marker, *signal_envelope_ids),
    )
    self.db.commit()
    return {
        "fired": True,
        "re_pricing_event_id": repricing["re_pricing_event_id"],
        "claims_trigger_envelope_id": claims_envelope["claims_trigger_envelope_id"],
        "aggregate_severity": aggregate_severity,
        "event_type_counts": event_type_counts,
    }


def _current_backbond_score_ref(self: PortalService, dp_id: str) -> str | None:
    row = self.db.execute(
        "SELECT id FROM scans WHERE dp_id = ? AND status = 'complete' ORDER BY completed_at DESC LIMIT 1",
        (dp_id,),
    ).fetchone()
    if not row:
        return None
    return f"portal-evidence://{dp_id}/scans/{row['id']}"


def _build_re_pricing_event(
    self: PortalService,
    *,
    dp_id: str,
    trigger_window_start: str,
    trigger_window_end: str,
    rule_id: str,
    signal_envelope_ids: list[str],
    aggregate_severity: float,
    event_type_counts: dict[str, int],
    current_backbond_score_ref: str | None,
) -> dict[str, Any]:
    event_id = str(uuid.uuid4())
    produced_at = now_iso()
    body = {
        "telemetry_api_version": TELEMETRY_API_VERSION,
        "re_pricing_event_id": event_id,
        "dp_id": dp_id,
        "produced_at": produced_at,
        "trigger_window_start": trigger_window_start,
        "trigger_window_end": trigger_window_end,
        "degradation_rule_id": rule_id,
        "signal_envelope_ids": signal_envelope_ids,
        "aggregate_severity": aggregate_severity,
        "event_type_counts": event_type_counts,
        "current_backbond_score_ref": current_backbond_score_ref,
        "pin_block": {
            "scanner_version": SCANNER_VERSION,
            "corpus_version": CORPUS_VERSION,
            "rubric_version": RUBRIC_VERSION,
            "telemetry_api_version": TELEMETRY_API_VERSION,
            "event_rule_corpus_version": EVENT_RULE_CORPUS_VERSION,
        },
    }
    portal_signature = b64url(self._counter_key().sign(canonical_json(body)))
    signed = {**body, "portal_signature": portal_signature}
    self.db.execute(
        "INSERT INTO re_pricing_events (id, dp_id, trigger_window_start, trigger_window_end, produced_at, aggregate_severity, envelope_json, portal_signature) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event_id,
            dp_id,
            trigger_window_start,
            trigger_window_end,
            produced_at,
            aggregate_severity,
            json.dumps(signed, sort_keys=True),
            portal_signature,
        ),
    )
    self.audit(
        "portal",
        "re_pricing_event_emitted",
        "re_pricing_event",
        event_id,
        {
            "payload_hash": sha256_hex(canonical_json(body)),
            "dp_id": dp_id,
            "aggregate_severity": aggregate_severity,
        },
    )
    return signed


def _classify_claim_eligibility(
    self: PortalService, event_type_counts: dict[str, int], aggregate_severity: float
) -> str:
    # Deterministic classification driven by the same per-event thresholds + severity
    # used to fire the trigger. data_leak >= 1 OR unauthorized_commitment >= 1 OR
    # regulatory_response >= 1 -> interim_payout_eligible; severity >= 16 OR
    # hallucination >= 5 -> interim_payout_eligible; else review_required.
    if (
        event_type_counts.get("data_leak", 0) >= 1
        or event_type_counts.get("unauthorized_commitment", 0) >= 1
        or event_type_counts.get("regulatory_response", 0) >= 1
    ):
        return "interim_payout_eligible"
    if aggregate_severity >= 16.0 or event_type_counts.get("hallucination", 0) >= 5:
        return "interim_payout_eligible"
    if aggregate_severity >= 8.0:
        return "review_required"
    return "not_eligible"


def _build_claims_trigger_envelope(
    self: PortalService,
    *,
    dp_id: str,
    trigger_window_start: str,
    trigger_window_end: str,
    rule_id: str,
    signal_envelope_ids: list[str],
    event_type_counts: dict[str, int],
    aggregate_severity: float,
) -> dict[str, Any]:
    envelope_id = str(uuid.uuid4())
    produced_at = now_iso()
    eligibility = self._classify_claim_eligibility(event_type_counts, aggregate_severity)
    interim_target = self._parse_iso(produced_at) + timedelta(days=1)
    body = {
        "telemetry_api_version": TELEMETRY_API_VERSION,
        "claims_trigger_envelope_id": envelope_id,
        "dp_id": dp_id,
        "produced_at": produced_at,
        "trigger_window_start": trigger_window_start,
        "trigger_window_end": trigger_window_end,
        "degradation_rule_id": rule_id,
        "signal_envelope_ids": signal_envelope_ids,
        "event_type_counts": event_type_counts,
        "claim_eligibility_class": eligibility,
        "interim_payout_window_target": interim_target.astimezone(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "redacted_evidence_refs": [
            f"portal-evidence://{dp_id}/signal-envelopes/{sid}" for sid in signal_envelope_ids
        ],
        "pin_block": {
            "scanner_version": SCANNER_VERSION,
            "corpus_version": CORPUS_VERSION,
            "rubric_version": RUBRIC_VERSION,
            "telemetry_api_version": TELEMETRY_API_VERSION,
            "event_rule_corpus_version": EVENT_RULE_CORPUS_VERSION,
        },
    }
    portal_signature = b64url(self._counter_key().sign(canonical_json(body)))
    signed = {**body, "portal_signature": portal_signature}
    self.db.execute(
        "INSERT INTO claims_trigger_envelopes (id, dp_id, trigger_window_start, trigger_window_end, produced_at, eligibility_class, envelope_json, portal_signature) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            envelope_id,
            dp_id,
            trigger_window_start,
            trigger_window_end,
            produced_at,
            eligibility,
            json.dumps(signed, sort_keys=True),
            portal_signature,
        ),
    )
    self.audit(
        "portal",
        "claims_trigger_emitted",
        "claims_trigger_envelope",
        envelope_id,
        {
            "payload_hash": sha256_hex(canonical_json(body)),
            "dp_id": dp_id,
            "claim_eligibility_class": eligibility,
        },
    )
    return signed


# --- retrieval ------------------------------------------------------------------------


def list_signal_envelopes(
    self: PortalService,
    dp_id: str,
    *,
    since: str | None = None,
    event_type: str | None = None,
) -> dict[str, Any]:
    if event_type is not None and event_type not in CANONICAL_EVENT_TYPES:
        raise PortalError(422, "telemetry_event_type_invalid", f"event_type must be one of {CANONICAL_EVENT_TYPES}.")
    query = "SELECT * FROM signal_envelopes WHERE dp_id = ?"
    args: list[Any] = [dp_id]
    if since:
        query += " AND produced_at >= ?"
        args.append(since)
    if event_type:
        query += " AND event_type = ?"
        args.append(event_type)
    query += " ORDER BY produced_at ASC, id ASC"
    rows = list(self.db.execute(query, args))
    items = [json.loads(row["envelope_json"]) for row in rows]
    return {"dp_id": dp_id, "count": len(items), "signal_envelopes": items}


def list_re_pricing_events(self: PortalService, dp_id: str) -> dict[str, Any]:
    rows = list(
        self.db.execute(
            "SELECT * FROM re_pricing_events WHERE dp_id = ? ORDER BY produced_at ASC, id ASC",
            (dp_id,),
        )
    )
    items = [json.loads(row["envelope_json"]) for row in rows]
    return {"dp_id": dp_id, "count": len(items), "re_pricing_events": items}


def list_claims_trigger_envelopes(self: PortalService, dp_id: str) -> dict[str, Any]:
    rows = list(
        self.db.execute(
            "SELECT * FROM claims_trigger_envelopes WHERE dp_id = ? ORDER BY produced_at ASC, id ASC",
            (dp_id,),
        )
    )
    items = [json.loads(row["envelope_json"]) for row in rows]
    return {"dp_id": dp_id, "count": len(items), "claims_trigger_envelopes": items}


def put_telemetry_trigger(
    self: PortalService, dp_id: str, payload: dict[str, Any], actor: str = "admin"
) -> dict[str, Any]:
    if actor != "admin":
        raise PortalError(403, "actor_not_admin", "Telemetry trigger PUT requires admin actor.")
    dp = self.db.execute("SELECT * FROM design_partners WHERE id = ?", (dp_id,)).fetchone()
    if not dp:
        raise PortalError(404, "dp_not_found", "Design partner not found.")
    suspended = bool(payload.get("suspended"))
    founder_cosign_ref = payload.get("founder_cosign_ref")
    houw_cosign_ref = payload.get("houw_cosign_ref")
    if suspended and not (founder_cosign_ref and houw_cosign_ref):
        # Contract section 5.1 requires Founder + HoUW co-sign for suspended:true.
        raise PortalError(
            403,
            "telemetry_trigger_cosign_required",
            "suspended:true requires both founder_cosign_ref and houw_cosign_ref.",
        )
    reason = payload.get("reason")
    self.db.execute(
        "INSERT INTO telemetry_trigger_state (dp_id, suspended, reason, founder_cosign_ref, houw_cosign_ref, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(dp_id) DO UPDATE SET suspended=excluded.suspended, reason=excluded.reason, "
        "founder_cosign_ref=excluded.founder_cosign_ref, houw_cosign_ref=excluded.houw_cosign_ref, "
        "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (
            dp_id,
            1 if suspended else 0,
            reason,
            founder_cosign_ref,
            houw_cosign_ref,
            now_iso(),
            actor,
        ),
    )
    self.db.commit()
    self.audit(
        actor,
        "telemetry_trigger_updated",
        "telemetry_trigger",
        dp_id,
        {
            "suspended": suspended,
            "reason": reason,
            "founder_cosign_ref": founder_cosign_ref,
            "houw_cosign_ref": houw_cosign_ref,
        },
    )
    if not suspended:
        # On resume re-evaluate the existing candidates immediately.
        self._evaluate_degradation_trigger(dp_id)
    return {
        "dp_id": dp_id,
        "suspended": suspended,
        "reason": reason,
        "founder_cosign_ref": founder_cosign_ref,
        "houw_cosign_ref": houw_cosign_ref,
        "updated_at": now_iso(),
    }


def get_telemetry_trigger(self: PortalService, dp_id: str) -> dict[str, Any]:
    row = self.db.execute(
        "SELECT * FROM telemetry_trigger_state WHERE dp_id = ?", (dp_id,)
    ).fetchone()
    if not row:
        return {"dp_id": dp_id, "suspended": False}
    return {
        "dp_id": dp_id,
        "suspended": bool(row["suspended"]),
        "reason": row["reason"],
        "founder_cosign_ref": row["founder_cosign_ref"],
        "houw_cosign_ref": row["houw_cosign_ref"],
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
    }


# --- HTTP route dispatch ---------------------------------------------------------------


def handle_get(service: PortalService, parts: list[str], query: dict[str, list[str]] | None = None):
    if len(parts) == 3 and parts[0] == "dp" and parts[2] == "signal-envelopes":
        q = query or {}
        since = (q.get("since") or [None])[0]
        event_type = (q.get("event_type") or [None])[0]
        payload = service.list_signal_envelopes(parts[1], since=since, event_type=event_type)
        return 200, json.dumps(payload, sort_keys=True).encode("utf-8"), "application/json"
    if len(parts) == 3 and parts[0] == "dp" and parts[2] == "re-pricing-events":
        payload = service.list_re_pricing_events(parts[1])
        return 200, json.dumps(payload, sort_keys=True).encode("utf-8"), "application/json"
    if len(parts) == 3 and parts[0] == "dp" and parts[2] == "claims-trigger-envelopes":
        payload = service.list_claims_trigger_envelopes(parts[1])
        return 200, json.dumps(payload, sort_keys=True).encode("utf-8"), "application/json"
    if len(parts) == 3 and parts[0] == "dp" and parts[2] == "telemetry-trigger":
        payload = service.get_telemetry_trigger(parts[1])
        return 200, json.dumps(payload, sort_keys=True).encode("utf-8"), "application/json"
    return None


def handle_post(service: PortalService, parts: list[str], body):
    if len(parts) == 2 and parts[0] == "telemetry" and parts[1] == "redacted-record":
        if not isinstance(body, dict):
            raise PortalError(
                415, "json_body_required", "Telemetry redacted-record endpoint requires application/json."
            )
        payload = service.submit_redacted_record(body)
        return 201, json.dumps(payload, sort_keys=True).encode("utf-8"), "application/json"
    return None


def handle_put(service: PortalService, parts: list[str], body):
    if len(parts) == 3 and parts[0] == "dp" and parts[2] == "telemetry-trigger":
        if not isinstance(body, dict):
            raise PortalError(
                415, "json_body_required", "Telemetry trigger endpoint requires application/json."
            )
        payload = service.put_telemetry_trigger(parts[1], body)
        return 200, json.dumps(payload, sort_keys=True).encode("utf-8"), "application/json"
    return None


# --- attachment ------------------------------------------------------------------------


_BOUND_NAMES = (
    "_corpus_base",
    "_load_signed_corpus",
    "event_rule_corpus",
    "degradation_rules_corpus",
    "_init_telemetry_tables",
    "_validate_redacted_record_shape",
    "_intake_pii_scan",
    "submit_redacted_record",
    "_detect_event_matches",
    "_build_signal_envelope",
    "_scan_and_emit_signals",
    "_parse_iso",
    "_window_seconds",
    "_evaluate_degradation_trigger",
    "_current_backbond_score_ref",
    "_build_re_pricing_event",
    "_classify_claim_eligibility",
    "_build_claims_trigger_envelope",
    "list_signal_envelopes",
    "list_re_pricing_events",
    "list_claims_trigger_envelopes",
    "put_telemetry_trigger",
    "get_telemetry_trigger",
)


def attach(cls: type[PortalService]) -> type[PortalService]:
    """Bind telemetry-v0.1 methods onto PortalService and create tables on first build."""
    for name in _BOUND_NAMES:
        setattr(cls, name, globals()[name])
    # Wrap __init__ to run _init_telemetry_tables exactly once after the base init.
    original_init = cls.__init__

    def __init__(self, config):  # type: ignore[override]
        original_init(self, config)
        if not getattr(self, "_telemetry_tables_ready", False):
            self._init_telemetry_tables()
            self._telemetry_tables_ready = True
            # Refresh pins so telemetry fields land in portal_pins.json.
            self.emit_pins()

    cls.__init__ = __init__
    return cls
