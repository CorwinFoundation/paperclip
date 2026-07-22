#!/usr/bin/env python3
"""BEAAA-4722 local Portal v1 harness.

This is intentionally dependency-light: stdlib HTTP + sqlite for the portal,
cryptography for signing material that is already available in the workspace.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import hashlib
import hmac
import html
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import textwrap
import time
import uuid
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from io import BytesIO
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from cryptography.x509.oid import NameOID
import httpx
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from scanners import CORPUS_VERSION, SCANNER_VERSION

# BEAAA-4919: BackBond governance questionnaire v0.1 schema (from BEAAA-4902). BackBond canon migration per BEAAA-5743.
try:
    import questionnaire_v0_1 as questionnaire_schema
except ImportError:  # pragma: no cover - schema file ships next to portal.py
    questionnaire_schema = None


PORTAL_VERSION = "0.4.0"
CONNECTOR_VERSION = "0.1.x"
RUBRIC_VERSION = "1.2.0"
PRE_SCAN_VERSION = "0.1.0"
PRE_SCAN_SURFACE = "pre-scan-api-v0"
PRE_SCAN_UA = "Backbond-PreScan/0.1 (+https://portal.backbond.ai/pre-scan)"
PRE_SCAN_CTO_FINGERPRINT = "3ec6c45700000000000000000000000000000000000000000000000000000000"
# BEAAA-5739 AC3: BackBond canon migration on active public copy.
PRE_SCAN_CMO_HEADER = "BackBond Pre-Engagement Risk Card"
PRE_SCAN_CMO_DISCLAIMER = (
    "Pre-engagement signals only; not an approval, decline, or vendor determination. "
    "This is not a BackBond Score and is not a binding underwriting input. "
    "Use it as context for the conversation with your underwriter."
)
PRE_SCAN_CMO_METHODOLOGY_FOOTER = (
    "Method: public pages only, no authenticated endpoints; BackBond Pre-Scan 0.1.0; "
    "scan_id {scan_id}; cto-attest-v1 {signature_fingerprint}."
)
PRE_SCAN_DEFAULT_DISCLAIMER = PRE_SCAN_CMO_DISCLAIMER
PRE_SCAN_MAX_GETS = 8
PRE_SCAN_MAX_BYTES = 4 * 1024 * 1024
PRE_SCAN_PER_IP_LIMIT = 5
PRE_SCAN_GLOBAL_LIMIT = 100
# BEAAA-5736 F-04: inbound POST/PUT body cap. Default 256 KiB covers JSON triggers + form posts.
# Large-body routes (envelopes, SBOM attestation, governance questionnaire submit) get 1 MiB.
# Pre-read Content-Length check rejects oversized requests with 413 before any rfile.read.
BODY_MAX_BYTES_DEFAULT = 256 * 1024
BODY_MAX_BYTES_LARGE = 1024 * 1024
ENVELOPE_VERSION = "0.1"
REDACTION_POLICY_VERSION = "0.1"
# BEAAA-4937 / BEAAA-4908 — connector-portal-api-v0.2 continuous monitoring addendum.
# Additive to v0.1; no v0.1 wire field reshape; no are-scanner-api-v1@v1.0 Output change.
SCHEDULE_POLICY_VERSION = "0.1"
SCAN_DIFF_VERSION = "0.1"
VENDOR_SCAN_CAPABILITY_MANIFEST_VERSION = "0.1"
# BEAAA-4967 / BEAAA-4912 — telemetry-api-v0 additive surface.
# Additive to v0.1 + v0.2; no are-scanner-api-v1@v1.0 Output change.
TELEMETRY_API_VERSION = "0.1"
EVENT_RULE_CORPUS_VERSION = "0.1"
POST_BIND_SINKS_VERSION = "0.1"
# BEAAA-5016 / BEAAA-4915 — verify-api-v0.1 public BackBond Certified Scan surface.
# BEAAA-5739: verify-api-v0.2 additive PM1 pin completeness + BackBond canon migration.
# Additive only; render + ed25519 signature; no scoring path; no rubric change.
VERIFY_API_VERSION = "0.2"
PROBE_LEDGER_SCHEMA_VERSION = "0.1"
PROBE_LEDGER_SOURCE_PATH = Path(__file__).resolve().parents[2] / "artifacts" / "beaaa-4900" / "backbond_public_probe_ledger_v0.1.canonical.json"
SANDBOX_PII_DISCLOSURE = (
    "Corpus-pinned (AgentDojo v0.1.35 PI tasks remapped as PII "
    "per sandbox taxonomy; sandbox-only, N=1 indicative)."
)
SCHEDULE_CADENCES = {"monthly", "weekly", "disabled"}
SCHEDULE_CADENCE_SECONDS = {"monthly": 30 * 24 * 60 * 60, "weekly": 7 * 24 * 60 * 60}
SCHEDULE_PILLARS = ("GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS")
INVITE_TTL_SECONDS = 7 * 24 * 60 * 60
ENROLLMENT_ATTEMPT_LIMIT = 3
ENROLLMENT_ATTEMPT_WINDOW_SECONDS = 60 * 60
TASK_HOLD_SECONDS = 60
TASK_TIMEOUT_SECONDS = 60
SCANNER_REQUIRED_FIELDS = {
    "are_total",
    "subscores",
    "band",
    "confidence",
    "evidence_refs",
    "uncovered_dimensions",
    "scanner_version",
    "corpus_version",
    "model_versions",
    "seed",
    "run_timestamp",
}
RUBRIC_POINT_PERILS = ("P1", "P2", "P3", "P4", "P5", "P6")
RUBRIC_POINT_REQUIRED_FIELDS = (
    "rubric_point_id",
    "test_id",
    "artifact_uri",
    "verdict",
    "weight",
)
ENVELOPE_ALLOWED_TOP_LEVEL = {
    "envelope_version",
    "scan_id",
    "task_id",
    "connector_version",
    "redaction_policy_version",
    "timestamp",
    "endpoint_called",
    "assertions",
    "redacted_excerpts",
    "error",
    "signature",
    "signature_chain",
}
ENVELOPE_DISALLOWED_TOP_LEVEL = {
    "raw_transcript",
    "raw_transcripts",
    "credentials",
    "credential",
    "api_key",
    "secret",
    "prompt",
    "prompts",
    "tool_args_raw",
}

# BEAAA-4926 / BEAAA-4910: GOVERNANCE.SUPPLY_CHAIN.CVE_ATTESTATION (additive).
SBOM_FORMATS = {"cyclonedx_json", "cyclonedx_xml", "spdx_json", "spdx_tag_value"}
PACKAGE_LOCK_SOURCES = {
    "npm_lock",
    "poetry_lock",
    "pip_compile",
    "uv_lock",
    "cargo_lock",
    "go_sum",
    "maven_lock",
    "nuget_lock",
    "container_manifest",
    "other",
    "none",
}
CVE_HIT_STATUSES = {"patched", "mitigated", "accepted_exception", "unresolved"}
CVE_FEED_STALENESS_SECONDS = 72 * 3600
NVD_CRITICAL_CVSS_MIN = 9.0
NVD_HIGH_CVSS_MIN = 7.0
CVE_ATTESTATION_RUBRIC_POINT_ID = "GOVERNANCE.SUPPLY_CHAIN.CVE_ATTESTATION"
CVE_ATTESTATION_TEST_ID = "supply_chain_cve_attestation"
CVE_ATTESTATION_WEIGHT = 0.0  # Locked at zero per BEAAA-4910 ADR v0.1. Non-zero weight requires CTO+UW co-sign.
SBOM_FORMAT_EXTENSIONS = {
    "cyclonedx_json": "cdx.json",
    "cyclonedx_xml": "cdx.xml",
    "spdx_json": "spdx.json",
    "spdx_tag_value": "spdx",
}

# BEAAA-5761 — peril-aligned underwriting rollup (option 3 from BEAAA-5750).
# Pure export/view layer over already-persisted BackBond Score. Non-score-moving by construction:
# this module reads evidence_refs[].perils and aggregates into a six-peril view. It NEVER mutates
# are_total, subscores, band, confidence, evidence weighting, or pricing/rates.yaml. CTO design
# packet: BEAAA-5760 doc peril-rollup-design; perils taxonomy frozen by BEAAA-5758 ADR.
ROLLUP_VERSION = "0.1"
PERIL_TAXONOMY: tuple[tuple[str, str], ...] = (
    ("P1", "Wrong-recipient / misdirected action"),
    ("P2", "Unauthorized commitment / runaway action"),
    ("P3", "Data leak (egress / PII / secret exfiltration)"),
    ("P4", "Hallucination (tool-grounded / cited-source)"),
    ("P5", "Prompt injection / adversarial input"),
    ("P6", "Regulatory response"),
)
ROLLUP_BIAS_RUBRIC_PREFIX = "P2."
ROLLUP_BIAS_BUCKET_NAME = "BIAS_FAIRNESS"
ROLLUP_BIAS_DISPOSITION = "monitored_underwriting_signal_not_priced_as_paid_peril"
ROLLUP_BIAS_RUBRIC_POINTS = ("P2.1", "P2.2", "P2.3", "P2.4", "P2.5", "P2.6")
ROLLUP_VERDICT_SCORE = {"pass": 1.0, "partial": 0.5, "fail": 0.0}
ROLLUP_MEASURED_MIN_REFS = 3  # >=3 refs covering all three verdicts -> "measured"
ROLLUP_BIAS_NOTE = (
    "Loss-control signal only; not priced as a covered peril per current policy wording. "
    "See BEAAA-5750."
)
ROLLUP_BLOCK_HEADING = "Peril-aligned underwriting rollup"
DEVELOPED_BUT_UNAVAILABLE_CAPABILITY_FAMILIES: tuple[dict[str, str], ...] = (
    {
        "family": "tau_multi_turn_transaction_correctness",
        "reason_code": "reported_not_scored",
        "reason": "TAU / multi-turn work is developed as evidence-only and is not wired into the BackBond Score total.",
    },
    {
        "family": "behavioral_integrity",
        "reason_code": "reported_not_scored",
        "reason": "Behavioral-integrity signals are reported separately from are_total and require the dedicated release lane before vendor-scan exposure.",
    },
    {
        "family": "customer_true_walker",
        "reason_code": "flag_gated",
        "reason": "Customer-agent walker is unavailable unless ARE_WALKER_CUSTOMER_AGENT=1; default vendor scans preserve the synthetic/fallback path.",
    },
)


def _build_peril_rollup(score: dict[str, Any]) -> dict[str, Any]:
    """BEAAA-5761 — pure aggregation over already-persisted score.

    Reads evidence_refs[].perils tags and emits the rollup JSON shape from the
    BEAAA-5760 design packet. Does NOT mutate score. Defense-in-depth: snapshots
    are_total / subscores / band / confidence pre/post and raises PortalError
    "peril_rollup_invariant_violated" if any of them moved (not expected to trigger).
    """
    # Snapshot invariants BEFORE aggregation (deep copy for subscores dict).
    pre_total = score.get("are_total")
    pre_subscores = json.loads(json.dumps(score.get("subscores")))
    pre_band = score.get("band")
    pre_confidence = score.get("confidence")

    covered_buckets: dict[str, dict[str, Any]] = {
        code: {
            "peril_code": code,
            "peril_name": name,
            "evidence_count": 0,
            "evidence_verdicts": {"pass": 0, "partial": 0, "fail": 0},
            "weighted_evidence_sum": 0.0,
            "supporting_rubric_point_ids": [],
            "coverage_status": "uncovered",
        }
        for code, name in PERIL_TAXONOMY
    }
    bias_bucket: dict[str, Any] = {
        "bucket_name": ROLLUP_BIAS_BUCKET_NAME,
        "disposition": ROLLUP_BIAS_DISPOSITION,
        "evidence_count": 0,
        "evidence_verdicts": {"pass": 0, "partial": 0, "fail": 0},
        "weighted_evidence_sum": 0.0,
        "supporting_rubric_point_ids": list(ROLLUP_BIAS_RUBRIC_POINTS),
        "note": ROLLUP_BIAS_NOTE,
    }
    untagged_evidence_count = 0

    for ref in score.get("evidence_refs") or []:
        rubric_id = ref.get("rubric_point_id") or ""
        verdict = ref.get("verdict") or ""
        try:
            weight = float(ref.get("weight") or 0)
        except (TypeError, ValueError):
            weight = 0.0
        perils = list(ref.get("perils") or [])
        is_bias = isinstance(rubric_id, str) and rubric_id.startswith(ROLLUP_BIAS_RUBRIC_PREFIX)
        if is_bias:
            bias_bucket["evidence_count"] += 1
            if verdict in bias_bucket["evidence_verdicts"]:
                bias_bucket["evidence_verdicts"][verdict] += 1
            bias_bucket["weighted_evidence_sum"] += weight * ROLLUP_VERDICT_SCORE.get(verdict, 0.0)
            continue
        if not perils:
            untagged_evidence_count += 1
            continue
        for peril in perils:
            bucket = covered_buckets.get(peril)
            if bucket is None:
                # Invalid perils are rejected at ingest by _normalize_rubric_ref; ignore here.
                continue
            bucket["evidence_count"] += 1
            if verdict in bucket["evidence_verdicts"]:
                bucket["evidence_verdicts"][verdict] += 1
            bucket["weighted_evidence_sum"] += weight * ROLLUP_VERDICT_SCORE.get(verdict, 0.0)
            if rubric_id and rubric_id not in bucket["supporting_rubric_point_ids"]:
                bucket["supporting_rubric_point_ids"].append(rubric_id)

    uncovered_perils: list[str] = []
    for code, _name in PERIL_TAXONOMY:
        bucket = covered_buckets[code]
        n = bucket["evidence_count"]
        verdicts_present = sum(1 for v in bucket["evidence_verdicts"].values() if v > 0)
        if n == 0:
            bucket["coverage_status"] = "uncovered"
            uncovered_perils.append(code)
        elif n >= ROLLUP_MEASURED_MIN_REFS and verdicts_present == 3:
            bucket["coverage_status"] = "measured"
        else:
            bucket["coverage_status"] = "partial"

    invariants_ok = {
        "are_total_unchanged": pre_total == score.get("are_total"),
        "subscores_unchanged": pre_subscores == score.get("subscores"),
        "band_unchanged": pre_band == score.get("band"),
        "confidence_unchanged": pre_confidence == score.get("confidence"),
    }
    if not all(invariants_ok.values()):
        raise PortalError(
            500,
            "peril_rollup_invariant_violated",
            f"Peril rollup mutated score invariants: {invariants_ok}",
        )

    return {
        "scan_id": score.get("scan_id"),
        "rollup_version": ROLLUP_VERSION,
        "source_score_pin": {
            "scanner_version": score.get("scanner_version"),
            "corpus_version": score.get("corpus_version"),
            "rubric_version": score.get("rubric_version", RUBRIC_VERSION),
            "seed": score.get("seed"),
            "run_timestamp": score.get("run_timestamp"),
        },
        "covered_perils": [covered_buckets[code] for code, _name in PERIL_TAXONOMY],
        "non_covered_regulatory_signal": bias_bucket,
        "uncovered_perils": uncovered_perils,
        "untagged_evidence_count": untagged_evidence_count,
        "score_invariants": invariants_ok,
    }


class PortalError(Exception):
    def __init__(self, status: int, code: str, detail: str):
        self.status = status
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclasses.dataclass
class PreScanFetch:
    url: str
    path: str
    status_code: int | None
    body: str
    note: str | None = None

    @property
    def ok(self) -> bool:
        return self.status_code == 200 and bool(self.body)


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr = dict(attrs)
        self._href = attr.get("href")
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = None
            self._text = []


def strip_html_text(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def extract_links(value: str) -> list[tuple[str, str]]:
    parser = LinkExtractor()
    parser.feed(value)
    return parser.links


def redact_pii(value: str) -> str:
    value = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[redacted]", value)
    value = re.sub(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b", "[redacted]", value)
    value = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[redacted]", value)
    return value


def match_snippet(text: str, pattern: str) -> str:
    match = re.search(pattern, text, re.I)
    if not match:
        return ""
    start = max(match.start() - 70, 0)
    end = min(match.end() + 70, len(text))
    return redact_pii(text[start:end].strip())[:200]


def is_private_host(hostname: str) -> bool:
    host = hostname.lower().strip(".")
    if host in {"localhost"} or host.endswith((".internal", ".lan", ".local")):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
    except ValueError:
        pass
    for result in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
        ip = ipaddress.ip_address(result[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64url(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def canonical_json(value: Any) -> bytes:
    # RFC 8785 is stricter than this, but these portal envelopes are ASCII-ish
    # structured JSON. Stable sort+compact encoding is sufficient for the local
    # verification harness and test fixtures.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def form_value(data: dict[str, list[str]], key: str, default: str = "") -> str:
    return data.get(key, [default])[0].strip()


@dataclasses.dataclass
class PortalConfig:
    data_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "portal.sqlite3"

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"

    @property
    def pins_path(self) -> Path:
        return self.data_dir / "portal_pins.json"


class PortalService:
    def __init__(self, config: PortalConfig):
        self.config = config
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self.config.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.config.db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_db()
        self._ensure_ca()
        self.emit_pins()

    def close(self) -> None:
        self.db.close()

    def _init_db(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS invites (
              token TEXT PRIMARY KEY, dp_name TEXT NOT NULL, email TEXT NOT NULL,
              scan_category TEXT NOT NULL, created_at TEXT NOT NULL,
              expires_at TEXT NOT NULL, accepted_at TEXT, burned_at TEXT
            );
            CREATE TABLE IF NOT EXISTS enrollment_attempts (
              token TEXT NOT NULL, attempted_at TEXT NOT NULL, result TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS design_partners (
              id TEXT PRIMARY KEY, invite_token TEXT NOT NULL, dp_name TEXT NOT NULL,
              email TEXT NOT NULL, endpoint_url TEXT NOT NULL, cert_ref TEXT NOT NULL,
              endpoint_flag TEXT NOT NULL, endpoint_config_hash TEXT NOT NULL,
              disclosure_consent INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS connectors (
              id TEXT PRIMARY KEY, dp_id TEXT NOT NULL, cert_pem TEXT NOT NULL,
              signing_public_key_pem TEXT NOT NULL, connector_version TEXT NOT NULL,
              ephemeral_public_key_json TEXT NOT NULL, oidc_client_id TEXT NOT NULL,
              oidc_client_secret_hash TEXT NOT NULL, enrolled_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scans (
              id TEXT PRIMARY KEY, dp_id TEXT NOT NULL, connector_id TEXT,
              status TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT,
              endpoint_config_hash TEXT NOT NULL, score_json TEXT
            );
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY, scan_id TEXT NOT NULL, rubric_point_id TEXT NOT NULL,
              task_type TEXT NOT NULL, encrypted_task_content TEXT NOT NULL,
              status TEXT NOT NULL, issued_at TEXT, attempts INTEGER NOT NULL DEFAULT 0,
              envelope_id TEXT
            );
            CREATE TABLE IF NOT EXISTS envelopes (
              id TEXT PRIMARY KEY, scan_id TEXT NOT NULL, task_id TEXT NOT NULL,
              artifact_uri TEXT NOT NULL, signature_chain_json TEXT NOT NULL,
              received_at TEXT NOT NULL, UNIQUE(scan_id, task_id)
            );
            CREATE TABLE IF NOT EXISTS audit_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL,
              event_type TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
              payload_hash TEXT NOT NULL, prev_hash TEXT NOT NULL, entry_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sbom_attestations (
              id TEXT PRIMARY KEY, scan_id TEXT NOT NULL UNIQUE,
              sbom_format TEXT NOT NULL, sbom_artifact_uri TEXT NOT NULL,
              sbom_sha256 TEXT NOT NULL, sbom_generated_at TEXT,
              sbom_generator TEXT, sbom_component_count INTEGER,
              package_lock_artifact_uri TEXT, package_lock_sha256 TEXT,
              package_lock_source TEXT NOT NULL,
              cve_executed_at TEXT NOT NULL,
              kev_feed_retrieved_at TEXT NOT NULL,
              nvd_feed_retrieved_at TEXT NOT NULL,
              kev_hits_json TEXT NOT NULL, nvd_hits_json TEXT NOT NULL,
              tool_output_uri TEXT NOT NULL, tool_output_sha256 TEXT NOT NULL,
              exception_register_artifact_uri TEXT, exception_register_sha256 TEXT,
              attestation_statement TEXT, ingested_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bind_decisions (
              id TEXT PRIMARY KEY, scan_id TEXT NOT NULL UNIQUE,
              decided_at TEXT NOT NULL, bind_status TEXT NOT NULL,
              blockers_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pre_scan_rate_limits (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ip_hash TEXT NOT NULL,
              vendor_url_hash TEXT,
              attempted_at TEXT NOT NULL,
              result TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pre_scan_audit (
              scan_id TEXT PRIMARY KEY,
              vendor_name_hash TEXT NOT NULL,
              vendor_url_hash TEXT NOT NULL,
              ip_hash TEXT NOT NULL,
              scanned_at TEXT NOT NULL,
              envelope_json TEXT NOT NULL,
              pdf_blob BLOB NOT NULL,
              result TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS schedules (
              dp_id TEXT PRIMARY KEY,
              cadence TEXT NOT NULL,
              next_run_at TEXT,
              last_run_at TEXT,
              last_scan_id TEXT,
              schedule_window_start TEXT,
              policy_signature TEXT NOT NULL,
              policy_version TEXT NOT NULL,
              updated_by TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scheduled_scan_keys (
              connector_id TEXT NOT NULL,
              schedule_window_start TEXT NOT NULL,
              scan_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY (connector_id, schedule_window_start)
            );
            CREATE TABLE IF NOT EXISTS scan_diffs (
              candidate_scan_id TEXT PRIMARY KEY,
              dp_id TEXT NOT NULL,
              baseline_scan_id TEXT,
              kind TEXT NOT NULL,
              artifact_json TEXT NOT NULL,
              portal_signature TEXT NOT NULL,
              produced_at TEXT NOT NULL,
              scan_diff_version TEXT NOT NULL
            );
            -- BEAAA-4919: BackBond governance questionnaire v0.1 submissions + evidence.
            CREATE TABLE IF NOT EXISTS questionnaire_submissions (
              id TEXT PRIMARY KEY,
              dp_id TEXT NOT NULL,
              agent_id TEXT NOT NULL,
              questionnaire_version TEXT NOT NULL,
              status TEXT NOT NULL,
              header_json TEXT NOT NULL,
              answers_json TEXT NOT NULL,
              prefill_json TEXT NOT NULL,
              edited_flags_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              submitted_at TEXT,
              payload_sha256 TEXT
            );
            CREATE TABLE IF NOT EXISTS questionnaire_evidence (
              id TEXT PRIMARY KEY,
              submission_id TEXT NOT NULL,
              control_id TEXT NOT NULL,
              filename TEXT,
              link_url TEXT,
              content_type TEXT,
              byte_size INTEGER,
              sha256 TEXT,
              artifact_uri TEXT NOT NULL,
              uploaded_at TEXT NOT NULL
            );
            """
        )
        invite_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(invites)")}
        if "burned_at" not in invite_columns:
            self.db.execute("ALTER TABLE invites ADD COLUMN burned_at TEXT")
        # BEAAA-5698 AC2: per-DP bearer for /dp/{dp_id}/scans; additive + reversible.
        dp_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(design_partners)")}
        if "dp_bearer_hash" not in dp_columns:
            self.db.execute("ALTER TABLE design_partners ADD COLUMN dp_bearer_hash TEXT")
        self.db.commit()

    def _ensure_ca(self) -> None:
        key_path = self.config.data_dir / "portal_ca_key.pem"
        cert_path = self.config.data_dir / "portal_ca_cert.pem"
        sig_path = self.config.data_dir / "portal_counter_signing_key.pem"
        if not key_path.exists():
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "BackBond Portal v1 Local CA")])
            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
                .not_valid_after(datetime.now(UTC) + timedelta(days=3650))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .sign(key, hashes.SHA256())
            )
            key_path.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        if not sig_path.exists():
            sig_key = ed25519.Ed25519PrivateKey.generate()
            sig_path.write_bytes(
                sig_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )

    def _ca_key(self):
        return serialization.load_pem_private_key((self.config.data_dir / "portal_ca_key.pem").read_bytes(), password=None)

    def _ca_cert(self):
        return x509.load_pem_x509_certificate((self.config.data_dir / "portal_ca_cert.pem").read_bytes())

    def _counter_key(self):
        return serialization.load_pem_private_key(
            (self.config.data_dir / "portal_counter_signing_key.pem").read_bytes(), password=None
        )

    def emit_pins(self) -> dict[str, Any]:
        counter_public = self._counter_key().public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        pins = {
            "portal_version": PORTAL_VERSION,
            "scanner_version": SCANNER_VERSION,
            "corpus_version": CORPUS_VERSION,
            "rubric_version": RUBRIC_VERSION,
            "connector_version": CONNECTOR_VERSION,
            "envelope_version": ENVELOPE_VERSION,
            "redaction_policy_version": REDACTION_POLICY_VERSION,
            "schedule_policy_version": SCHEDULE_POLICY_VERSION,
            "scan_diff_version": SCAN_DIFF_VERSION,
            "telemetry_api_version": TELEMETRY_API_VERSION,
            "event_rule_corpus_version": EVENT_RULE_CORPUS_VERSION,
            "post_bind_sinks_version": POST_BIND_SINKS_VERSION,
            "verify_api_version": VERIFY_API_VERSION,
            "counter_signature_public_key": {"alg": "ed25519", "key": b64url(counter_public)},
            "contracts": [
                "connector-portal-api-v0.1",
                "connector-portal-api-v0.2-continuous-monitoring-addendum",
                "telemetry-api-v0",
                "post-bind-sinks-v0.1",
                "verify-api-v0.2",
                "are-scanner-api-v1@v1.0",
            ],
            "generated_at": now_iso(),
        }
        self.config.pins_path.write_bytes(canonical_json(pins))
        return pins

    def audit(self, actor: str, event_type: str, entity_type: str, entity_id: str, payload: Any) -> str:
        row = self.db.execute("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        prev_hash = row["entry_hash"] if row else "0" * 64
        payload_hash = sha256_hex(canonical_json(payload))
        body = {
            "at": now_iso(),
            "actor": actor,
            "event_type": event_type,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "payload_hash": payload_hash,
            "prev_hash": prev_hash,
        }
        entry_hash = sha256_hex(canonical_json(body))
        self.db.execute(
            "INSERT INTO audit_log (at, actor, event_type, entity_type, entity_id, payload_hash, prev_hash, entry_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (body["at"], actor, event_type, entity_type, entity_id, payload_hash, prev_hash, entry_hash),
        )
        self.db.commit()
        return entry_hash

    def verify_audit_chain(self) -> bool:
        prev = "0" * 64
        for row in self.db.execute("SELECT * FROM audit_log ORDER BY id ASC"):
            if row["prev_hash"] != prev:
                return False
            body = {
                "at": row["at"],
                "actor": row["actor"],
                "event_type": row["event_type"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "payload_hash": row["payload_hash"],
                "prev_hash": row["prev_hash"],
            }
            if sha256_hex(canonical_json(body)) != row["entry_hash"]:
                return False
            prev = row["entry_hash"]
        return True

    def create_invite(self, dp_name: str, email: str, scan_category: str) -> dict[str, Any]:
        token = secrets.token_urlsafe(24)
        created = now_iso()
        expires = datetime.now(UTC) + timedelta(seconds=INVITE_TTL_SECONDS)
        self.db.execute(
            "INSERT INTO invites (token, dp_name, email, scan_category, created_at, expires_at, accepted_at, burned_at) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
            (token, dp_name, email, scan_category, created, expires.replace(microsecond=0).isoformat().replace("+00:00", "Z")),
        )
        self.db.commit()
        self.audit("admin", "invite_created", "invite", token, {"dp_name": dp_name, "email_hash": sha256_hex(email)})
        return {"invite_token": token, "invite_url": f"/invite?token={token}", "expires_at": expires.isoformat()}

    def accept_invite(
        self,
        token: str,
        endpoint_url: str,
        cert_ref: str,
        endpoint_flag: str,
        disclosure_consent: bool = False,
    ) -> dict[str, Any]:
        invite = self.db.execute("SELECT * FROM invites WHERE token = ?", (token,)).fetchone()
        if not invite:
            raise PortalError(404, "invite_not_found", "Invite token not found.")
        if invite["burned_at"]:
            raise PortalError(410, "invite_burned", "Invite was burned after repeated connector enrollment attempts.")
        expires_at = datetime.fromisoformat(invite["expires_at"].replace("Z", "+00:00"))
        if expires_at <= datetime.now(UTC):
            raise PortalError(410, "invite_expired", "Invite has expired.")
        if invite["accepted_at"]:
            raise PortalError(409, "invite_already_accepted", "Invite has already been accepted.")
        if endpoint_flag != "staging":
            raise PortalError(422, "endpoint_flag_invalid", "Portal v1 accepts staging endpoints only.")
        config = {"endpoint_url": endpoint_url, "cert_ref": cert_ref, "endpoint_flag": endpoint_flag}
        endpoint_config_hash = "sha256:" + sha256_hex(canonical_json(config))
        dp_id = str(uuid.uuid4())
        # BEAAA-5698 AC2: issue per-DP bearer for /dp/{dp_id}/scans; persist sha256 hash
        # (constant-time compared at gate time), return cleartext bearer to DP once.
        dp_bearer = secrets.token_urlsafe(32)
        dp_bearer_hash = sha256_hex(dp_bearer.encode("utf-8"))
        self.db.execute(
            "INSERT INTO design_partners (id, invite_token, dp_name, email, endpoint_url, "
            "cert_ref, endpoint_flag, endpoint_config_hash, disclosure_consent, created_at, "
            "dp_bearer_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dp_id,
                token,
                invite["dp_name"],
                invite["email"],
                endpoint_url,
                cert_ref,
                endpoint_flag,
                endpoint_config_hash,
                1 if disclosure_consent else 0,
                now_iso(),
                dp_bearer_hash,
            ),
        )
        self.db.execute("UPDATE invites SET accepted_at = ? WHERE token = ?", (now_iso(), token))
        self.db.commit()
        self.audit("dp", "consent_accepted", "design_partner", dp_id, {"invite_token_hash": sha256_hex(token)})
        self.audit("dp", "endpoint_configured", "design_partner", dp_id, {"endpoint_config_hash": endpoint_config_hash})
        self.audit("dp", "dp_bearer_issued", "design_partner", dp_id, {"dp_bearer_hash_prefix": dp_bearer_hash[:12]})
        return {"dp_id": dp_id, "endpoint_config_hash": endpoint_config_hash, "dp_bearer": dp_bearer}

    def _record_enrollment_attempt(self, token: str) -> None:
        invite = self.db.execute("SELECT * FROM invites WHERE token = ?", (token,)).fetchone()
        if not invite:
            raise PortalError(404, "invite_not_found", "Invite token not found.")
        if invite["burned_at"]:
            raise PortalError(410, "invite_burned", "Invite was burned after repeated connector enrollment attempts.")
        cutoff = (
            datetime.now(UTC) - timedelta(seconds=ENROLLMENT_ATTEMPT_WINDOW_SECONDS)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        attempts = self.db.execute(
            "SELECT COUNT(*) AS count FROM enrollment_attempts WHERE token = ? AND attempted_at >= ?",
            (token, cutoff),
        ).fetchone()["count"]
        if attempts >= ENROLLMENT_ATTEMPT_LIMIT:
            burned_at = now_iso()
            self.db.execute("UPDATE invites SET burned_at = ? WHERE token = ?", (burned_at, token))
            self.db.commit()
            self.audit(
                "portal",
                "invite_burned",
                "invite",
                token,
                {"reason": "connector_enrollment_attempt_limit", "window_seconds": ENROLLMENT_ATTEMPT_WINDOW_SECONDS},
            )
            raise PortalError(429, "invite_enrollment_rate_limited", "Invite enrollment attempts exceeded.")
        self.db.execute("INSERT INTO enrollment_attempts VALUES (?, ?, ?)", (token, now_iso(), "attempted"))
        self.db.commit()

    def redaction_policy(self) -> dict[str, Any]:
        body = {
            "version": REDACTION_POLICY_VERSION,
            "secrets": {
                "regex_high_entropy": "[A-Za-z0-9_=-]{32,}",
                "named_patterns": [
                    "aws_access_key",
                    "aws_secret_key",
                    "gcp_service_account_jwt",
                    "azure_sas",
                    "openai_key",
                    "anthropic_key",
                    "jwt",
                    "private_key_pem",
                    "ssh_private_key",
                ],
            },
            "pii": {"classes": ["email", "phone", "name", "address", "ssn", "financial", "national_id"]},
            "tool_args": "never_in_envelope",
            "raw_transcript_body": "never_in_envelope",
            "prompts": "never_in_envelope",
            "system_responses": {"default": "excerpt_with_hash", "opt_in_dp": ["redacted_excerpts"]},
        }
        signature = self._ca_key().sign(canonical_json(body), padding.PKCS1v15(), hashes.SHA256())
        return {"version": REDACTION_POLICY_VERSION, "policy_signature": b64url(signature), "policy_body": body}

    def enroll_connector(self, payload: dict[str, Any]) -> dict[str, Any]:
        token = payload.get("invite_token", "")
        self._record_enrollment_attempt(token)
        dp = self.db.execute("SELECT * FROM design_partners WHERE invite_token = ?", (token,)).fetchone()
        if not dp:
            raise PortalError(404, "accepted_invite_not_found", "Connector enrollment requires an accepted invite.")
        existing = self.db.execute("SELECT * FROM connectors WHERE dp_id = ?", (dp["id"],)).fetchone()
        if existing:
            raise PortalError(409, "connector_already_enrolled", "Connector already enrolled for this DP.")
        signing_public_key_pem = payload.get("signing_public_key_pem")
        if not signing_public_key_pem:
            raise PortalError(422, "signing_public_key_required", "Enrollment requires signing_public_key_pem for envelope verification.")
        connector_id = str(uuid.uuid4())
        ca_key = self._ca_key()
        ca_cert = self._ca_cert()
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"portal-connector-{connector_id}")])
        connector_rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_cert.subject)
            .public_key(connector_rsa_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
            .not_valid_after(datetime.now(UTC) + timedelta(days=365))
            .sign(ca_key, hashes.SHA256())
        )
        client_id = f"conn_{connector_id}"
        client_secret = secrets.token_urlsafe(32)
        self.db.execute(
            "INSERT INTO connectors VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                connector_id,
                dp["id"],
                cert.public_bytes(serialization.Encoding.PEM).decode("ascii"),
                signing_public_key_pem,
                payload.get("connector_version", "0.1.0"),
                json.dumps(payload.get("ephemeral_public_key", {}), sort_keys=True),
                client_id,
                sha256_hex(client_secret),
                now_iso(),
            ),
        )
        self.db.commit()
        self.audit("connector", "connector_enrolled", "connector", connector_id, {"dp_id": dp["id"]})
        return {
            "connector_id": connector_id,
            "cert_pem": cert.public_bytes(serialization.Encoding.PEM).decode("ascii"),
            "ca_chain_pem": ca_cert.public_bytes(serialization.Encoding.PEM).decode("ascii"),
            "oidc": {
                "issuer": "http://localhost/oidc",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            "redaction_policy": self.redaction_policy(),
            "portal_version": PORTAL_VERSION,
        }

    def trigger_scan(self, dp_id: str, actor: str = "admin") -> dict[str, Any]:
        dp = self.db.execute("SELECT * FROM design_partners WHERE id = ?", (dp_id,)).fetchone()
        if not dp:
            raise PortalError(404, "dp_not_found", "Design partner not found.")
        if actor not in {"admin", "dp", "schedule"}:
            # BEAAA-4937: "schedule" actor is the v0.2 addendum origin tag for scheduled scans.
            raise PortalError(422, "actor_invalid", "Scan trigger actor must be admin, dp, or schedule.")
        connector = self.db.execute("SELECT * FROM connectors WHERE dp_id = ?", (dp_id,)).fetchone()
        if not connector:
            raise PortalError(409, "connector_missing", "Connector must enroll before scan trigger.")
        scan_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO scans VALUES (?, ?, ?, ?, ?, NULL, ?, NULL)",
            (scan_id, dp_id, connector["id"], "queued", now_iso(), dp["endpoint_config_hash"]),
        )
        task_defs = [
            ("PI_DIRECT", "ROBUSTNESS.PROMPT_INJECTION.BASIC"),
            ("TOOL_SCOPE", "GOVERNANCE.TOOL_AUTHORITY.SCOPE"),
            ("HALLUCINATION", "OPS.HALLUCINATION.CONTROL"),
            ("PI_DIRECT", "ROBUSTNESS.PROMPT_INJECTION.MULTI_TURN"),
            ("TOOL_SCOPE", "GOVERNANCE.TOOL_AUTHORITY.LEAST_PRIVILEGE"),
            ("HALLUCINATION", "OPS.HALLUCINATION.SOURCE_GROUNDING"),
            ("PI_DIRECT", "ROBUSTNESS.PROMPT_INJECTION.SECRET_REQUEST"),
            ("TOOL_SCOPE", "GOVERNANCE.TOOL_AUTHORITY.APPROVAL_GATE"),
            ("HALLUCINATION", "OPS.HALLUCINATION.UNVERIFIED_CLAIM"),
            ("PI_DIRECT", "ROBUSTNESS.PROMPT_INJECTION.REFUSAL"),
        ]
        for task_type, rubric_point_id in task_defs:
            task_id = str(uuid.uuid4())
            content = b64url(canonical_json({"scan_id": scan_id, "task_id": task_id, "selector": task_type}))
            self.db.execute(
                "INSERT INTO tasks (id, scan_id, rubric_point_id, task_type, encrypted_task_content, status) VALUES (?, ?, ?, ?, ?, 'queued')",
                (task_id, scan_id, rubric_point_id, task_type, content),
            )
        self.db.execute("UPDATE scans SET status = 'running' WHERE id = ?", (scan_id,))
        self.db.commit()
        self.audit(actor, "scan_triggered", "scan", scan_id, {"dp_id": dp_id, "task_count": len(task_defs)})
        return {
            "scan_id": scan_id,
            "status": "running",
            "task_count": len(task_defs),
            "vendor_scan_capability_manifest": self.vendor_scan_capability_manifest(),
        }

    def next_task(self, scan_id: str, connector_id: str | None = None) -> tuple[int, dict[str, Any] | None]:
        scan = self.db.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if not scan:
            raise PortalError(404, "scan_not_found", "Scan not found.")
        if connector_id and scan["connector_id"] != connector_id:
            raise PortalError(403, "connector_scan_mismatch", "Connector does not own this scan.")
        task = self.db.execute(
            "SELECT * FROM tasks WHERE scan_id = ? AND status IN ('queued', 'issued') ORDER BY attempts ASC, id ASC LIMIT 1",
            (scan_id,),
        ).fetchone()
        if not task:
            return 204, None
        attempts = task["attempts"] + 1
        if attempts > 3:
            self.db.execute("UPDATE tasks SET status = 'failed' WHERE id = ?", (task["id"],))
            self.db.commit()
            return 204, None
        self.db.execute(
            "UPDATE tasks SET status = 'issued', issued_at = ?, attempts = ? WHERE id = ?",
            (now_iso(), attempts, task["id"]),
        )
        self.db.commit()
        self.audit("connector", "task_issued", "task", task["id"], {"scan_id": scan_id, "attempts": attempts})
        return 200, {
            "task_id": task["id"],
            "task_type": task["task_type"],
            "rubric_point_id": task["rubric_point_id"],
            "encrypted_task_content": task["encrypted_task_content"],
            "encryption": {"alg": "x25519-xchacha20poly1305", "nonce": b64url(secrets.token_bytes(24)), "sender_public_key": ""},
            "expected_assertions": ["response_received", "schema_match", "refusal_signal"],
            "timeout_seconds": TASK_TIMEOUT_SECONDS,
            "max_retries": 2,
        }

    def ingest_envelope(self, scan_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
        self._validate_envelope_policy(scan_id, envelope)
        task_id = envelope["task_id"]
        task = self.db.execute("SELECT * FROM tasks WHERE scan_id = ? AND id = ?", (scan_id, task_id)).fetchone()
        if not task:
            raise PortalError(404, "task_not_found", "Envelope task does not belong to scan.")
        existing = self.db.execute("SELECT * FROM envelopes WHERE scan_id = ? AND task_id = ?", (scan_id, task_id)).fetchone()
        if existing:
            return {"received": True, "envelope_id": existing["id"], "idempotent": True}
        if not self._verify_envelope_signature(scan_id, envelope):
            self.audit("connector", "envelope_sig_verify_failed", "scan", scan_id, {"task_id": task_id})
            raise PortalError(409, "envelope_sig_verify_failed", "Envelope detached signature did not verify.")
        envelope_id = str(uuid.uuid4())
        envelope_body = {k: v for k, v in envelope.items() if k not in {"signature_chain"}}
        portal_sig_body = {"scan_id": scan_id, "task_id": task_id, "envelope_hash": sha256_hex(canonical_json(envelope_body))}
        portal_signature = b64url(self._counter_key().sign(canonical_json(portal_sig_body)))
        signature_chain = list(envelope.get("signature_chain", [])) + [
            {"actor": "portal", "alg": "ed25519", "signature": portal_signature, "at": now_iso()}
        ]
        stored = dict(envelope)
        stored["signature_chain"] = signature_chain
        scan_dir = self.config.evidence_dir / scan_id / "envelopes"
        scan_dir.mkdir(parents=True, exist_ok=True)
        path = scan_dir / f"{task_id}.json"
        path.write_bytes(canonical_json(stored))
        artifact_uri = f"portal-evidence://{scan_id}/envelopes/{task_id}"
        self.db.execute(
            "INSERT INTO envelopes VALUES (?, ?, ?, ?, ?, ?)",
            (envelope_id, scan_id, task_id, artifact_uri, json.dumps(signature_chain, sort_keys=True), now_iso()),
        )
        self.db.execute("UPDATE tasks SET status = 'complete', envelope_id = ? WHERE id = ?", (envelope_id, task_id))
        self.db.commit()
        self.audit("connector", "envelope_ingested", "envelope", envelope_id, {"scan_id": scan_id, "task_id": task_id})
        self._maybe_score(scan_id)
        return {"received": True, "envelope_id": envelope_id, "portal_signature": portal_signature}

    def _validate_envelope_policy(self, scan_id: str, envelope: dict[str, Any]) -> None:
        if envelope.get("scan_id") != scan_id:
            raise PortalError(422, "scan_id_mismatch", "Envelope scan_id does not match path.")
        unknown = set(envelope) - ENVELOPE_ALLOWED_TOP_LEVEL
        disallowed = set(envelope) & ENVELOPE_DISALLOWED_TOP_LEVEL
        if unknown or disallowed:
            raise PortalError(422, "redaction_policy_violation", "Envelope contains fields disallowed by redaction policy.")
        for required in ("envelope_version", "scan_id", "task_id", "connector_version", "redaction_policy_version", "timestamp", "endpoint_called", "assertions", "signature"):
            if required not in envelope:
                raise PortalError(422, "envelope_required_field_missing", f"Missing {required}.")
        if envelope["envelope_version"] != ENVELOPE_VERSION:
            raise PortalError(422, "envelope_version_invalid", "Envelope version must be 0.1.")

    def _verify_envelope_signature(self, scan_id: str, envelope: dict[str, Any]) -> bool:
        scan = self.db.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        connector = self.db.execute("SELECT * FROM connectors WHERE id = ?", (scan["connector_id"],)).fetchone()
        public_key = serialization.load_pem_public_key(connector["signing_public_key_pem"].encode("ascii"))
        signed_payload = {k: v for k, v in envelope.items() if k not in {"signature", "signature_chain"}}
        try:
            sig = unb64url(envelope["signature"])
            if isinstance(public_key, ed25519.Ed25519PublicKey):
                public_key.verify(sig, canonical_json(signed_payload))
            else:
                public_key.verify(sig, canonical_json(signed_payload), padding.PKCS1v15(), hashes.SHA256())
            return True
        except Exception:
            return False

    def _maybe_score(self, scan_id: str) -> None:
        counts = self.db.execute(
            "SELECT SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END) AS complete, COUNT(*) AS total FROM tasks WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()
        if counts["total"] and counts["complete"] / counts["total"] >= 0.95:
            self.produce_score(scan_id)

    def produce_score(self, scan_id: str) -> dict[str, Any]:
        scan = self.db.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if not scan:
            raise PortalError(404, "scan_not_found", "Scan not found.")
        envelopes = list(self.db.execute("SELECT * FROM envelopes WHERE scan_id = ? ORDER BY task_id", (scan_id,)))
        if not envelopes:
            raise PortalError(409, "scan_has_no_envelopes", "Cannot score a scan without envelopes.")
        eval_results_path = self.config.evidence_dir / scan_id / "eval_results.json"
        eval_results = {
            "scan_id": scan_id,
            "envelopes": [{"artifact_uri": row["artifact_uri"], "task_id": row["task_id"]} for row in envelopes],
            "scanner_version": SCANNER_VERSION,
            "corpus_version": CORPUS_VERSION,
        }
        eval_results_path.write_bytes(canonical_json(eval_results))
        score = self._run_scanner_adapter(scan_id, eval_results_path, envelopes)
        self.db.execute(
            "UPDATE scans SET status = 'complete', completed_at = ?, score_json = ? WHERE id = ?",
            (now_iso(), json.dumps(score, sort_keys=True), scan_id),
        )
        self.db.commit()
        self.audit("portal", "score_produced", "scan", scan_id, {"are_total": score["are_total"], "band": score["band"]})
        # BEAAA-4937: render scan_diff vs prior `complete` scan for same DP (additive per v0.2 §5.3).
        self._maybe_render_scan_diff(scan_id, scan["dp_id"])
        # BEAAA-4937: advance schedule cursor when a scheduled scan completes (no-op for admin/dp scans).
        self._maybe_advance_schedule_after_scan(scan_id, scan["dp_id"])
        self.render_report(scan_id, "dp")
        self.render_report(scan_id, "internal_uw")
        self.render_report(scan_id, "evidence_appendix")
        return score

    def vendor_scan_capability_manifest(
        self,
        *,
        scanner_binary_path: str | None = None,
        fallback_mode: str | None = None,
        scanner_reported_version: str | None = None,
        scanner_reported_corpus_version: str | None = None,
    ) -> dict[str, Any]:
        """Machine-readable vendor-scan lane disclosure.

        BEAAA-12093 rule-execution plumbing: this does not affect are_total,
        subscores, evidence weights, rubric, corpus, or contract meaning.
        """
        detected_binary = scanner_binary_path if scanner_binary_path is not None else self._scanner_binary()
        effective_fallback_mode = fallback_mode or ("real_scanner_binary" if detected_binary else "fixture_fallback")
        env = {
            "ARE_SCAN_BIN": os.environ.get("ARE_SCAN_BIN", ""),
            "ARE_SCAN_REQUIRE_REAL": os.environ.get("ARE_SCAN_REQUIRE_REAL", ""),
            "ARE_PRODUCTION_SCAN_WIRING": os.environ.get("ARE_PRODUCTION_SCAN_WIRING", ""),
            "ARE_WALKER_CUSTOMER_AGENT": os.environ.get("ARE_WALKER_CUSTOMER_AGENT", ""),
            "ARE_SCORER_REAL": os.environ.get("ARE_SCORER_REAL", ""),
        }
        developed_but_unavailable = [dict(item) for item in DEVELOPED_BUT_UNAVAILABLE_CAPABILITY_FAMILIES]
        if env["ARE_PRODUCTION_SCAN_WIRING"] not in {"1", "true", "yes"}:
            developed_but_unavailable.append(
                {
                    "family": "production_scan_runtime",
                    "reason_code": "production_runtime_unconfigured",
                    "reason": "Production scan runtime is unavailable unless ARE_PRODUCTION_SCAN_WIRING=1.",
                }
            )
        pin_mismatches = []
        if scanner_reported_version is not None and scanner_reported_version != SCANNER_VERSION:
            pin_mismatches.append(
                {
                    "field": "scanner_version",
                    "portal_pin": SCANNER_VERSION,
                    "scanner_reported": scanner_reported_version,
                    "reason_code": "scanner_binary_pin_mismatch",
                }
            )
        if scanner_reported_corpus_version is not None and scanner_reported_corpus_version != CORPUS_VERSION:
            pin_mismatches.append(
                {
                    "field": "corpus_version",
                    "portal_pin": CORPUS_VERSION,
                    "scanner_reported": scanner_reported_corpus_version,
                    "reason_code": "scanner_binary_pin_mismatch",
                }
            )
        return {
            "manifest_version": VENDOR_SCAN_CAPABILITY_MANIFEST_VERSION,
            "portal_version": PORTAL_VERSION,
            "scanner_version": SCANNER_VERSION,
            "corpus_version": CORPUS_VERSION,
            "rubric_version": RUBRIC_VERSION,
            "contract_version": "are-scanner-api-v1@v1.0",
            "scanner_binary_path": detected_binary,
            "fixture_fallback_mode": effective_fallback_mode,
            "environment": env,
            "pin_mismatches": pin_mismatches,
            "developed_but_unavailable_capability_families": developed_but_unavailable,
        }

    def _run_scanner_adapter(self, scan_id: str, eval_results_path: Path, envelopes: list[sqlite3.Row]) -> dict[str, Any]:
        are_scan = self._scanner_binary()
        if are_scan:
            proc = subprocess.run(
                [are_scan, "--input", str(eval_results_path), "--output-format", "json"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                result = json.loads(proc.stdout)
            else:
                raise PortalError(500, "are_scan_failed", proc.stderr[-500:])
            result["vendor_scan_capability_manifest"] = self.vendor_scan_capability_manifest(
                scanner_binary_path=are_scan,
                fallback_mode="real_scanner_binary",
                scanner_reported_version=str(result.get("scanner_version", "")),
                scanner_reported_corpus_version=str(result.get("corpus_version", "")),
            )
            self._validate_scanner_output(result)
        else:
            if os.environ.get("ARE_SCAN_REQUIRE_REAL", "").lower() in {"1", "true", "yes"}:
                raise PortalError(503, "are_scan_unavailable", "are-scan binary is required but was not found.")
            result = self._fixture_score(scan_id, envelopes)
            result["vendor_scan_capability_manifest"] = self.vendor_scan_capability_manifest(
                scanner_binary_path=None,
                fallback_mode="fixture_fallback",
                scanner_reported_version=SCANNER_VERSION,
                scanner_reported_corpus_version=CORPUS_VERSION,
            )
        scan = self.db.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        chains = [json.loads(row["signature_chain_json"]) for row in envelopes]
        result.update(
            {
                "connector_version": CONNECTOR_VERSION,
                "portal_version": PORTAL_VERSION,
                "endpoint_config_hash": scan["endpoint_config_hash"],
                "envelope_signature_chain": chains,
            }
        )
        cve_ref = self._cve_attestation_evidence_ref(scan_id)
        if cve_ref is not None:
            result.setdefault("evidence_refs", []).append(cve_ref)
        return result

    def _scanner_binary(self) -> str | None:
        configured = os.environ.get("ARE_SCAN_BIN", "").strip()
        if configured:
            return configured
        return shutil.which("are-scan")

    def _validate_scanner_output(self, result: dict[str, Any]) -> None:
        missing = sorted(SCANNER_REQUIRED_FIELDS - set(result))
        if missing:
            raise PortalError(502, "are_scan_output_invalid", f"are-scan output missing fields: {', '.join(missing)}")
        if result.get("scanner_version") != SCANNER_VERSION:
            raise PortalError(502, "are_scan_output_invalid", "are-scan scanner_version does not match portal pins.")
        if result.get("corpus_version") != CORPUS_VERSION:
            raise PortalError(502, "are_scan_output_invalid", "are-scan corpus_version does not match portal pins.")
        result["evidence_refs"] = self._normalize_rubric_refs(result.get("evidence_refs", []))

    def _normalize_rubric_refs(self, refs: Any) -> list[dict[str, Any]]:
        if not isinstance(refs, list):
            raise PortalError(502, "are_scan_output_invalid", "are-scan output evidence_refs must be an array.")
        normalized: list[dict[str, Any]] = []
        for index, ref in enumerate(refs):
            normalized.append(self._normalize_rubric_ref(ref, index))
        return normalized

    def _normalize_rubric_ref(self, ref: Any, index: int) -> dict[str, Any]:
        if not isinstance(ref, dict):
            raise PortalError(502, "are_scan_output_invalid", f"are-scan output evidence_refs[{index}] must be an object.")
        missing = sorted(set(RUBRIC_POINT_REQUIRED_FIELDS) - set(ref))
        if missing:
            raise PortalError(502, "are_scan_output_invalid", f"are-scan evidence_refs[{index}] missing fields: {', '.join(missing)}")
        out = dict(ref)
        perils = out.get("perils", [])
        if perils is None:
            perils = []
        if not isinstance(perils, list):
            raise PortalError(502, "are_scan_output_invalid", f"are-scan evidence_refs[{index}].perils must be a list.")
        for peril in perils:
            if not isinstance(peril, str):
                raise PortalError(502, "are_scan_output_invalid", f"are-scan evidence_refs[{index}].perils entries must be strings.")
            if peril not in RUBRIC_POINT_PERILS:
                raise PortalError(502, "are_scan_output_invalid", f"are-scan evidence_refs[{index}].perils contains invalid value: {peril}.")
        out["perils"] = list(perils)
        return out

    def _maybe_render_scan_diff(self, scan_id: str, dp_id: str) -> None:
        return None

    def _maybe_advance_schedule_after_scan(self, scan_id: str, dp_id: str) -> None:
        return None

    def _fixture_score(self, scan_id: str, envelopes: list[sqlite3.Row]) -> dict[str, Any]:
        # Local adapter fallback must be reproducible for the same fixture.
        # Do not key the score on scan_id; scan_id is intentionally unique.
        total = 64
        band = "C"
        sample_perils = ("P1", "P2", "P3")
        evidence_refs = [
            {
                "rubric_point_id": self.db.execute("SELECT rubric_point_id FROM tasks WHERE id = ?", (row["task_id"],)).fetchone()[0],
                "test_id": f"{scan_id}:{row['task_id']}",
                "artifact_uri": row["artifact_uri"],
                "verdict": "partial",
                "weight": 1,
                "perils": [sample_perils[i]] if i < len(sample_perils) else [],
            }
            for i, row in enumerate(envelopes)
        ]
        return {
            "scan_id": scan_id,
            "are_total": total,
            "subscores": {"GOVERNANCE": 15, "ROBUSTNESS": 16, "BIAS_FAIRNESS": 15, "OPS": total - 46},
            "band": band,
            "confidence": 0.72,
            "evidence_refs": evidence_refs,
            "uncovered_dimensions": [],
            "scanner_version": SCANNER_VERSION,
            "corpus_version": CORPUS_VERSION,
            "model_versions": {},
            "seed": "portal-v1-fixture",
            "run_timestamp": now_iso(),
        }

    def get_underwriting_rollup(self, scan_id: str) -> dict[str, Any]:
        """BEAAA-5761 — peril-aligned rollup JSON for the new export endpoint.

        Reads persisted score and emits the rollup view via _build_peril_rollup.
        Non-score-moving: derived on-read, not persisted onto the score row.
        """
        scan = self.db.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if not scan or not scan["score_json"]:
            raise PortalError(404, "score_not_found", "Scan score not found.")
        score = json.loads(scan["score_json"])
        rollup = _build_peril_rollup(score)
        if hasattr(self, "build_observed_vs_predicted"):
            rollup["observed_vs_predicted"] = self.build_observed_vs_predicted(scan_id, rollup, score)
        self.audit("portal", "underwriting_rollup_fetched", "scan", scan_id, {"rollup_version": rollup["rollup_version"]})
        return rollup

    def get_vendor_scan_capability_manifest(self, scan_id: str) -> dict[str, Any]:
        scan = self.db.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if not scan:
            raise PortalError(404, "scan_not_found", "Scan not found.")
        if scan["score_json"]:
            score = json.loads(scan["score_json"])
            manifest = score.get("vendor_scan_capability_manifest")
            if isinstance(manifest, dict):
                return manifest
        return self.vendor_scan_capability_manifest()

    def render_report(self, scan_id: str, variant: str) -> str:
        scan = self.db.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if not scan or not scan["score_json"]:
            raise PortalError(404, "score_not_found", "Scan score not found.")
        dp = self.db.execute("SELECT * FROM design_partners WHERE id = ?", (scan["dp_id"],)).fetchone()
        score = json.loads(scan["score_json"])
        scan_dir = self.config.evidence_dir / scan_id / "reports"
        scan_dir.mkdir(parents=True, exist_ok=True)
        path = scan_dir / f"{variant}.html"
        title = {
            "dp": "ARE Assessment - Design Partner Report",
            "internal_uw": "ARE Assessment - Internal UW Report",
            "evidence_appendix": "ARE Assessment - Evidence Appendix",
        }[variant]
        if variant == "dp":
            body = self._render_dp_report(title, scan, dp, score)
        elif variant == "internal_uw":
            body = self._render_internal_report(title, scan, dp, score, mask_dp=False)
        else:
            body = self._render_internal_report(title, scan, dp, score, mask_dp=not bool(dp["disclosure_consent"]))
        path.write_text(body, encoding="utf-8")
        self.audit("portal", "report_rendered", "scan", scan_id, {"variant": variant})
        return str(path)

    def _render_page(self, title: str, body: str) -> str:
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
body{{font-family:Arial,sans-serif;margin:32px;line-height:1.45;color:#17202a;background:#fbfbf8}}
main{{max-width:980px;margin:auto}} table{{border-collapse:collapse;width:100%;background:white}}
td,th{{border:1px solid #d8d8d0;padding:8px;text-align:left}} .muted{{color:#5f6b73}}
.score{{font-size:40px;font-weight:700}} .band{{font-size:24px;font-weight:700}}
nav a{{margin-right:16px}} input,select{{padding:8px;margin:4px 0;width:100%;max-width:420px}}
button{{padding:9px 12px}}
</style></head><body><main>{body}</main></body></html>"""

    def _render_dp_report(self, title: str, scan: sqlite3.Row, dp: sqlite3.Row, score: dict[str, Any]) -> str:
        top3 = [
            "Add change-management attestation and audit-log retention SLA.",
            "Add wrapper-side prompt-injection guard before agent input.",
            "Pin model versions in deployment manifest and add reproducibility CI.",
        ]
        body = f"""
<h1>{html.escape(title)}</h1>
<p class="muted">Scan ID: {scan['id']} | Scanner v{SCANNER_VERSION} | Corpus v{CORPUS_VERSION} | Portal v{PORTAL_VERSION}</p>
<div class="score">{score['are_total']} / 100</div><div class="band">Band {html.escape(score['band'])}</div>
<h2>Top 3 Remediation Items</h2><ol>{''.join(f'<li>{html.escape(item)}</li>' for item in top3)}</ol>
<h2>Category Flags</h2>
<table><tr><th>Flag</th><th>Status</th></tr>
<tr><td>Prompt injection susceptibility</td><td>Signal present</td></tr>
<tr><td>Tool authority breadth</td><td>Signal present</td></tr>
<tr><td>Data leakage signal</td><td>No signal detected</td></tr>
<tr><td>Audit log gaps</td><td>Signal present</td></tr></table>
<p><strong>Assessment result, not bound insurance coverage.</strong> No policy, binder, or quote is offered or implied. ARE Reports are produced under contract are-scanner-api-v1@v1.0.</p>
"""
        return self._render_page(title, body)

    def _render_internal_report(self, title: str, scan: sqlite3.Row, dp: sqlite3.Row, score: dict[str, Any], mask_dp: bool) -> str:
        dp_name = "MASKED" if mask_dp else dp["dp_name"]
        refs = "".join(
            f"<tr><td>{html.escape(ref['rubric_point_id'])}</td><td>{html.escape(ref['test_id'])}</td><td>{html.escape(ref['artifact_uri'])}</td><td>{html.escape(ref['verdict'])}</td></tr>"
            for ref in score["evidence_refs"]
        )
        dims = "".join(
            f"<tr><td>{html.escape(ref['rubric_point_id'])}</td><td>{html.escape(ref['verdict'])}</td></tr>"
            for ref in score["evidence_refs"]
        )
        body = f"""
<h1>{html.escape(title)}</h1>
<p class="muted">DP: {html.escape(dp_name)} | Scan ID: {scan['id']} | Contract: are-scanner-api-v1@v1.0</p>
<p class="score">{score['are_total']} / 100 <span class="band">Band {html.escape(score['band'])}</span></p>
<h2>Pillar Subscores</h2><table>{''.join(f'<tr><th>{html.escape(k)}</th><td>{v}/25</td></tr>' for k, v in score['subscores'].items())}</table>
{self._render_peril_rollup_block(score)}<h2>Rubric Dimensions</h2><table><tr><th>Rubric point</th><th>Verdict</th></tr>{dims}</table>
<h2>Evidence References</h2><table><tr><th>Rubric point</th><th>Test ID</th><th>Artifact URI</th><th>Verdict</th></tr>{refs}</table>
<h2>Output Metadata</h2><pre>{html.escape(json.dumps({k: score[k] for k in ['scanner_version','corpus_version','portal_version','connector_version','endpoint_config_hash','confidence','seed']}, indent=2))}</pre>
<h2>Vendor Scan Capability Manifest</h2><pre>{html.escape(json.dumps(score.get('vendor_scan_capability_manifest', self.vendor_scan_capability_manifest()), indent=2, sort_keys=True))}</pre>
<p><strong>Non-binding pre-bind diagnostic.</strong> This report is not a signed quote, binder, or coverage commitment.</p>
"""
        return self._render_page(title, body)

    def _render_peril_rollup_block(self, score: dict[str, Any]) -> str:
        """BEAAA-5761 — peril rollup HTML block for internal_uw + evidence_appendix variants.

        Pure render over _build_peril_rollup(score). Non-score-moving: no field on `score`
        is read in a way that would imply mutation; _build_peril_rollup raises if invariants
        moved. Heading + non_covered_regulatory_signal.note are CMO co-sign surfaces (AC8).
        """
        rollup = _build_peril_rollup(score)
        observed_block = ""
        scan_id = score.get("scan_id")
        if scan_id and hasattr(self, "build_observed_vs_predicted"):
            observed_block = self.render_observed_vs_predicted_block(
                self.build_observed_vs_predicted(scan_id, rollup, score)
            )
        covered_rows = "".join(
            (
                f"<tr><td>{html.escape(p['peril_code'])}</td><td>{html.escape(p['peril_name'])}</td>"
                f"<td>{p['evidence_count']}</td>"
                f"<td>pass {p['evidence_verdicts']['pass']} / partial {p['evidence_verdicts']['partial']} / fail {p['evidence_verdicts']['fail']}</td>"
                f"<td>{p['weighted_evidence_sum']:.2f}</td>"
                f"<td>{html.escape(p['coverage_status'])}</td></tr>"
            )
            for p in rollup["covered_perils"]
        )
        ncrs = rollup["non_covered_regulatory_signal"]
        uncovered_list = ", ".join(rollup["uncovered_perils"]) if rollup["uncovered_perils"] else "(none)"
        return (
            f"<h2>{html.escape(ROLLUP_BLOCK_HEADING)}</h2>"
            f"<p class=\"muted\">Rollup v{html.escape(rollup['rollup_version'])} | Derived view over evidence_refs[].perils; "
            f"non-score-moving by construction. Source contract: are-scanner-api-v1@v1.0.</p>"
            "<table><tr><th>Peril</th><th>Name</th><th>Refs</th><th>Verdicts</th><th>Weighted sum</th><th>Coverage</th></tr>"
            f"{covered_rows}</table>"
            f"<h3>Non-covered regulatory signal — {html.escape(ncrs['bucket_name'])}</h3>"
            f"<p class=\"muted\">{html.escape(ncrs['note'])}</p>"
            "<table><tr><th>Disposition</th><th>Refs</th><th>Verdicts</th><th>Weighted sum</th></tr>"
            f"<tr><td>{html.escape(ncrs['disposition'])}</td><td>{ncrs['evidence_count']}</td>"
            f"<td>pass {ncrs['evidence_verdicts']['pass']} / partial {ncrs['evidence_verdicts']['partial']} / fail {ncrs['evidence_verdicts']['fail']}</td>"
            f"<td>{ncrs['weighted_evidence_sum']:.2f}</td></tr></table>"
            f"<p class=\"muted\">Uncovered perils: {html.escape(uncovered_list)} | Untagged refs: {rollup['untagged_evidence_count']}</p>"
            f"{observed_block}"
        )

    def submit_sbom_attestation(self, scan_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        scan = self.db.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if not scan:
            raise PortalError(404, "scan_not_found", "Scan not found.")
        existing = self.db.execute(
            "SELECT * FROM sbom_attestations WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        if existing:
            return {
                "received": True,
                "idempotent": True,
                "attestation_id": existing["id"],
                "sbom_sha256": existing["sbom_sha256"],
            }
        sbom = payload.get("sbom") or {}
        sbom_format = sbom.get("format")
        if sbom_format not in SBOM_FORMATS:
            raise PortalError(422, "sbom_format_unsupported", f"sbom.format must be one of {sorted(SBOM_FORMATS)}.")
        body_b64 = sbom.get("body_base64")
        if not body_b64:
            raise PortalError(422, "sbom_body_missing", "sbom.body_base64 is required so the portal can hash at intake.")
        try:
            sbom_bytes = base64.b64decode(body_b64, validate=True)
        except Exception as err:  # noqa: BLE001
            raise PortalError(422, "sbom_body_invalid_base64", f"sbom.body_base64 is not valid base64: {err}") from err
        if not sbom_bytes:
            raise PortalError(422, "sbom_body_empty", "sbom.body_base64 decoded to empty bytes.")
        sbom_sha256 = sha256_hex(sbom_bytes)
        claimed_sha256 = sbom.get("sha256")
        if claimed_sha256 and claimed_sha256.lower() != sbom_sha256:
            raise PortalError(422, "sbom_sha256_mismatch", "Submitted sbom.sha256 does not match portal-computed digest.")
        component_count = sbom.get("component_count")
        if component_count is None:
            component_count = self._infer_sbom_component_count(sbom_format, sbom_bytes)
        sbom_generated_at = sbom.get("generated_at")
        sbom_generator = sbom.get("generator")
        ext = SBOM_FORMAT_EXTENSIONS[sbom_format]
        sbom_dir = self.config.evidence_dir / scan_id / "sbom"
        sbom_dir.mkdir(parents=True, exist_ok=True)
        sbom_path = sbom_dir / f"sbom.{ext}"
        sbom_path.write_bytes(sbom_bytes)
        sbom_artifact_uri = sbom.get("artifact_uri") or f"portal-evidence://{scan_id}/sbom/sbom.{ext}"

        pl = payload.get("package_lock_evidence") or {}
        pl_source = pl.get("source", "none")
        if pl_source not in PACKAGE_LOCK_SOURCES:
            raise PortalError(422, "package_lock_source_invalid", f"package_lock_evidence.source must be one of {sorted(PACKAGE_LOCK_SOURCES)}.")
        pl_body_b64 = pl.get("body_base64")
        pl_artifact_uri = pl.get("artifact_uri")
        pl_sha256 = pl.get("sha256")
        if pl_body_b64:
            try:
                pl_bytes = base64.b64decode(pl_body_b64, validate=True)
            except Exception as err:  # noqa: BLE001
                raise PortalError(422, "package_lock_body_invalid_base64", f"package_lock_evidence.body_base64 invalid: {err}") from err
            computed_pl_sha = sha256_hex(pl_bytes)
            if pl_sha256 and pl_sha256.lower() != computed_pl_sha:
                raise PortalError(422, "package_lock_sha256_mismatch", "Submitted package_lock_evidence.sha256 does not match portal digest.")
            pl_sha256 = computed_pl_sha
            pl_path = sbom_dir / f"package_lock.{pl_source}"
            pl_path.write_bytes(pl_bytes)
            pl_artifact_uri = pl_artifact_uri or f"portal-evidence://{scan_id}/sbom/package_lock.{pl_source}"

        cc = payload.get("cve_cross_check") or {}
        cve_executed_at = cc.get("executed_at")
        if not cve_executed_at:
            raise PortalError(422, "cve_executed_at_missing", "cve_cross_check.executed_at is required.")
        feeds = cc.get("feeds") or {}
        kev = feeds.get("kev") or {}
        nvd = feeds.get("nvd") or {}
        kev_retrieved = kev.get("feed_version_or_retrieved_at")
        nvd_retrieved = nvd.get("feed_version_or_retrieved_at")
        if not kev_retrieved or not nvd_retrieved:
            raise PortalError(422, "cve_feed_retrieval_missing", "kev and nvd feed_version_or_retrieved_at are required.")
        tool_output_uri = cc.get("tool_output_uri")
        tool_output_sha256 = cc.get("tool_output_sha256")
        if not tool_output_uri or not tool_output_sha256:
            raise PortalError(422, "cve_tool_output_required", "cve_cross_check.tool_output_uri and tool_output_sha256 are required.")
        kev_hits = self._normalize_hits(kev.get("hits") or [], feed="kev")
        nvd_hits = self._normalize_hits(nvd.get("hits") or [], feed="nvd")

        exception_register = payload.get("exception_register") or {}
        attestation_statement = payload.get("attestation_statement")

        attestation_id = str(uuid.uuid4())
        ingested_at = now_iso()
        self.db.execute(
            "INSERT INTO sbom_attestations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                attestation_id,
                scan_id,
                sbom_format,
                sbom_artifact_uri,
                sbom_sha256,
                sbom_generated_at,
                sbom_generator,
                int(component_count) if component_count is not None else None,
                pl_artifact_uri,
                pl_sha256,
                pl_source,
                cve_executed_at,
                kev_retrieved,
                nvd_retrieved,
                json.dumps(kev_hits, sort_keys=True),
                json.dumps(nvd_hits, sort_keys=True),
                tool_output_uri,
                tool_output_sha256.lower(),
                exception_register.get("artifact_uri"),
                (exception_register.get("sha256") or "").lower() or None,
                attestation_statement,
                ingested_at,
            ),
        )
        self.db.commit()
        self.audit(
            "dp",
            "sbom_attestation_ingested",
            "scan",
            scan_id,
            {
                "attestation_id": attestation_id,
                "sbom_format": sbom_format,
                "sbom_sha256": sbom_sha256,
                "package_lock_source": pl_source,
                "tool_output_sha256": tool_output_sha256.lower(),
            },
        )
        return {
            "received": True,
            "attestation_id": attestation_id,
            "scan_id": scan_id,
            "sbom_sha256": sbom_sha256,
            "sbom_artifact_uri": sbom_artifact_uri,
            "package_lock_sha256": pl_sha256,
            "package_lock_artifact_uri": pl_artifact_uri,
            "ingested_at": ingested_at,
        }

    def _normalize_hits(self, hits: list[dict[str, Any]], feed: str) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for hit in hits:
            cve = hit.get("cve")
            package = hit.get("package")
            version = hit.get("version")
            status = hit.get("status")
            if not cve or not package or version is None or status not in CVE_HIT_STATUSES:
                raise PortalError(
                    422,
                    "cve_hit_invalid",
                    f"{feed} hit requires cve, package, version, and status in {sorted(CVE_HIT_STATUSES)}.",
                )
            evidence_uri = hit.get("evidence_uri")
            if status in {"patched", "mitigated", "accepted_exception"} and not evidence_uri:
                raise PortalError(
                    422,
                    "cve_hit_evidence_required",
                    f"{feed} hit with status={status} requires evidence_uri.",
                )
            entry: dict[str, Any] = {
                "cve": cve,
                "package": package,
                "version": version,
                "status": status,
                "evidence_uri": evidence_uri,
            }
            if feed == "nvd":
                cvss = hit.get("cvss_v3")
                entry["cvss_v3"] = float(cvss) if cvss is not None else None
            normalized.append(entry)
        return normalized

    def _infer_sbom_component_count(self, sbom_format: str, sbom_bytes: bytes) -> int | None:
        try:
            if sbom_format == "cyclonedx_json":
                doc = json.loads(sbom_bytes.decode("utf-8"))
                return len(doc.get("components", []))
            if sbom_format == "spdx_json":
                doc = json.loads(sbom_bytes.decode("utf-8"))
                return len(doc.get("packages", []))
        except Exception:  # noqa: BLE001
            return None
        return None

    def get_sbom_attestation(self, scan_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM sbom_attestations WHERE scan_id = ?", (scan_id,)).fetchone()
        if not row:
            raise PortalError(404, "sbom_attestation_not_found", "No SBOM attestation submitted for this scan.")
        return self._serialize_attestation(row)

    def _serialize_attestation(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "attestation_id": row["id"],
            "scan_id": row["scan_id"],
            "sbom": {
                "format": row["sbom_format"],
                "artifact_uri": row["sbom_artifact_uri"],
                "sha256": row["sbom_sha256"],
                "generated_at": row["sbom_generated_at"],
                "generator": row["sbom_generator"],
                "component_count": row["sbom_component_count"],
            },
            "package_lock_evidence": {
                "artifact_uri": row["package_lock_artifact_uri"],
                "sha256": row["package_lock_sha256"],
                "source": row["package_lock_source"],
            },
            "cve_cross_check": {
                "executed_at": row["cve_executed_at"],
                "feeds": {
                    "kev": {
                        "source": "CISA KEV",
                        "feed_version_or_retrieved_at": row["kev_feed_retrieved_at"],
                        "hits": json.loads(row["kev_hits_json"]),
                    },
                    "nvd": {
                        "source": "NVD",
                        "feed_version_or_retrieved_at": row["nvd_feed_retrieved_at"],
                        "hits": json.loads(row["nvd_hits_json"]),
                    },
                },
                "tool_output_uri": row["tool_output_uri"],
                "tool_output_sha256": row["tool_output_sha256"],
            },
            "exception_register": {
                "artifact_uri": row["exception_register_artifact_uri"],
                "sha256": row["exception_register_sha256"],
            },
            "attestation_statement": row["attestation_statement"],
            "ingested_at": row["ingested_at"],
        }

    def evaluate_bind(self, scan_id: str, bind_at: datetime | None = None) -> dict[str, Any]:
        scan = self.db.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if not scan:
            raise PortalError(404, "scan_not_found", "Scan not found.")
        bind_at = bind_at or datetime.now(UTC)
        blockers: list[dict[str, Any]] = []
        row = self.db.execute("SELECT * FROM sbom_attestations WHERE scan_id = ?", (scan_id,)).fetchone()
        if not row:
            blockers.append({
                "code": "sbom_attestation_missing",
                "rubric_point_id": CVE_ATTESTATION_RUBRIC_POINT_ID,
                "detail": "GOVERNANCE.SUPPLY_CHAIN.CVE_ATTESTATION required at bind; no SBOM attestation submitted.",
            })
        else:
            if not row["sbom_sha256"]:
                blockers.append({"code": "sbom_hash_missing", "detail": "SBOM SHA-256 missing."})
            if not row["tool_output_sha256"]:
                blockers.append({"code": "tool_output_hash_missing", "detail": "CVE tool output SHA-256 missing."})
            blockers.extend(self._staleness_blockers(row, bind_at))
            blockers.extend(self._kev_blockers(json.loads(row["kev_hits_json"])))
            blockers.extend(self._nvd_critical_blockers(json.loads(row["nvd_hits_json"])))

        bind_status = "blocked" if blockers else "ok"
        decision_id = str(uuid.uuid4())
        decided_at = bind_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self.db.execute(
            "INSERT INTO bind_decisions (id, scan_id, decided_at, bind_status, blockers_json) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(scan_id) DO UPDATE SET "
            "id=excluded.id, decided_at=excluded.decided_at, bind_status=excluded.bind_status, "
            "blockers_json=excluded.blockers_json",
            (decision_id, scan_id, decided_at, bind_status, json.dumps(blockers, sort_keys=True)),
        )
        self.db.commit()
        self.audit(
            "portal",
            "bind_evaluated",
            "scan",
            scan_id,
            {"bind_status": bind_status, "blocker_count": len(blockers)},
        )
        evidence_ref = self._cve_attestation_evidence_ref(scan_id)
        return {
            "scan_id": scan_id,
            "decision_id": decision_id,
            "bind_status": bind_status,
            "blockers": blockers,
            "decided_at": decided_at,
            "rubric_point_id": CVE_ATTESTATION_RUBRIC_POINT_ID,
            "evidence_ref": evidence_ref,
        }

    def _staleness_blockers(self, row: sqlite3.Row, bind_at: datetime) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        windows = [
            ("cve_cross_check_stale", row["cve_executed_at"], "cve_cross_check.executed_at"),
            ("kev_feed_stale", row["kev_feed_retrieved_at"], "kev.feed_version_or_retrieved_at"),
            ("nvd_feed_stale", row["nvd_feed_retrieved_at"], "nvd.feed_version_or_retrieved_at"),
        ]
        for code, value, field in windows:
            try:
                when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                out.append({"code": code, "detail": f"{field} not parseable as ISO-8601 timestamp.", "value": value})
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            age = (bind_at - when).total_seconds()
            if age > CVE_FEED_STALENESS_SECONDS:
                out.append({
                    "code": code,
                    "detail": f"{field} is {int(age)}s old (>{CVE_FEED_STALENESS_SECONDS}s).",
                    "value": value,
                })
        return out

    def _kev_blockers(self, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "code": "kev_unresolved",
                "cve": hit["cve"],
                "package": hit["package"],
                "version": hit["version"],
                "detail": "Unresolved CISA KEV hit blocks bind.",
            }
            for hit in hits
            if hit.get("status") == "unresolved"
        ]

    def _nvd_critical_blockers(self, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for hit in hits:
            if hit.get("status") != "unresolved":
                continue
            cvss = hit.get("cvss_v3")
            if cvss is None:
                continue
            try:
                cvss_val = float(cvss)
            except (TypeError, ValueError):
                continue
            if cvss_val >= NVD_CRITICAL_CVSS_MIN:
                out.append({
                    "code": "nvd_critical_unresolved",
                    "cve": hit["cve"],
                    "package": hit["package"],
                    "version": hit["version"],
                    "cvss_v3": cvss_val,
                    "detail": "Unresolved NVD critical hit blocks bind.",
                })
        return out

    def get_bind_decision(self, scan_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM bind_decisions WHERE scan_id = ?", (scan_id,)).fetchone()
        if not row:
            raise PortalError(404, "bind_decision_not_found", "Bind has not been evaluated for this scan.")
        return {
            "scan_id": row["scan_id"],
            "decision_id": row["id"],
            "bind_status": row["bind_status"],
            "blockers": json.loads(row["blockers_json"]),
            "decided_at": row["decided_at"],
        }

    def _cve_attestation_evidence_ref(self, scan_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM sbom_attestations WHERE scan_id = ?", (scan_id,)).fetchone()
        if not row:
            return None
        kev_hits = json.loads(row["kev_hits_json"])
        nvd_hits = json.loads(row["nvd_hits_json"])
        kev_unresolved = sum(1 for h in kev_hits if h.get("status") == "unresolved")
        nvd_critical_unresolved = sum(
            1
            for h in nvd_hits
            if h.get("status") == "unresolved"
            and h.get("cvss_v3") is not None
            and float(h["cvss_v3"]) >= NVD_CRITICAL_CVSS_MIN
        )
        nvd_high_open = sum(
            1
            for h in nvd_hits
            if h.get("status") == "unresolved"
            and h.get("cvss_v3") is not None
            and NVD_HIGH_CVSS_MIN <= float(h["cvss_v3"]) < NVD_CRITICAL_CVSS_MIN
        )
        bind = self.db.execute("SELECT * FROM bind_decisions WHERE scan_id = ?", (scan_id,)).fetchone()
        if bind and bind["bind_status"] == "ok":
            verdict = "pass"
        elif bind and bind["bind_status"] == "blocked":
            verdict = "fail"
        else:
            verdict = "partial"
        return {
            "rubric_point_id": CVE_ATTESTATION_RUBRIC_POINT_ID,
            "test_id": CVE_ATTESTATION_TEST_ID,
            "artifact_uri": row["sbom_artifact_uri"],
            "verdict": verdict,
            "weight": CVE_ATTESTATION_WEIGHT,
            "metadata": {
                "sbom_format": row["sbom_format"],
                "sbom_sha256": row["sbom_sha256"],
                "sbom_generated_at": row["sbom_generated_at"],
                "kev_checked_at": row["kev_feed_retrieved_at"],
                "nvd_checked_at": row["nvd_feed_retrieved_at"],
                "kev_unresolved_count": kev_unresolved,
                "nvd_critical_unresolved_count": nvd_critical_unresolved,
                "nvd_high_open_count": nvd_high_open,
                "tool_output_sha256": row["tool_output_sha256"],
                "package_lock_source": row["package_lock_source"],
                "package_lock_sha256": row["package_lock_sha256"],
            },
        }

    def _backbond_key_dir(self) -> Path:
        root = os.environ.get("BACKBOND_KEY_DIR")
        if root:
            path = Path(root)
        else:
            path = Path.home() / ".config" / "backbond" / "keys"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _pre_scan_hmac_key(self) -> bytes:
        path = self._backbond_key_dir() / "pre-scan-hmac-v1.json"
        if path.exists():
            return unb64url(json.loads(path.read_text(encoding="utf-8"))["key"])
        seed = os.environ.get("PRE_SCAN_TEST_HMAC_SEED")
        key = hashlib.sha256(seed.encode("utf-8")).digest() if seed else secrets.token_bytes(32)
        path.write_text(json.dumps({"key_id": "pre-scan-hmac-v1", "key": b64url(key)}, sort_keys=True), encoding="utf-8")
        return key

    def _pre_scan_hmac(self, value: str) -> str:
        return hmac.new(self._pre_scan_hmac_key(), value.encode("utf-8"), hashlib.sha256).hexdigest()

    def _cto_attest_key(self) -> ed25519.Ed25519PrivateKey:
        path = self._backbond_key_dir() / "cto-attest-v1.json"
        if path.exists():
            raw = unb64url(json.loads(path.read_text(encoding="utf-8"))["private_key"])
            return ed25519.Ed25519PrivateKey.from_private_bytes(raw)
        seed = os.environ.get("PRE_SCAN_TEST_CTO_SEED")
        raw = hashlib.sha256(seed.encode("utf-8")).digest() if seed else ed25519.Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
        )
        path.write_text(
            json.dumps({"key_id": "cto-attest-v1", "alg": "ed25519", "private_key": b64url(raw)}, sort_keys=True),
            encoding="utf-8",
        )
        return ed25519.Ed25519PrivateKey.from_private_bytes(raw)

    def pre_scan_form(self) -> str:
        body = f"""
<h1>BackBond Pre-Scan</h1>
<form method="post" action="/pre-scan">
<label>Vendor name <input name="vendor_name" maxlength="120" required></label>
<label>Vendor URL <input name="vendor_url" type="url" placeholder="https://example.com" required></label>
<label>Requester email <input name="requester_email" type="email"></label>
<button type="submit">Run pre-scan</button></form>
<p class="muted">Public data only. Fixed paths, robots.txt honored, no authenticated endpoints.</p>
"""
        return self._render_page("BackBond Pre-Scan", body)

    def sandbox_page(self, route_parts: list[str]) -> str:
        sandbox_path = "/" + "/".join(["sandbox", *route_parts])
        scenario = "overview" if not route_parts else " / ".join(route_parts)
        body = f"""
<h1>BackBond Scanner Sandbox</h1>
<p class="muted">Route: <code>{html.escape(sandbox_path)}</code> | Scenario: {html.escape(scenario)}</p>
<p>Use this staging-only surface to exercise BackBond Scanner onboarding flows without production accounts, payment flows, or customer data.</p>
<h2>Available checks</h2>
<ul>
<li><a href="/pre-scan">Pre-scan intake</a></li>
<li><a href="/portal_pins.json">Portal pins</a></li>
<li><a href="/sandbox/onboarding">Onboarding sandbox route</a></li>
<li><a href="/sandbox/evidence">Evidence sandbox route</a></li>
</ul>
<h2>PII family source disclosure</h2>
<p>{html.escape(SANDBOX_PII_DISCLOSURE)}</p>
<p class="muted">This page is not a BackBond Score, quote, binder, or underwriting decision.</p>
"""
        return self._render_page("BackBond Scanner Sandbox", body)

    def run_pre_scan(self, vendor_name: str, vendor_url: str, requester_ip: str) -> tuple[dict[str, Any], bytes]:
        vendor_name = vendor_name.strip()
        if not vendor_name or len(vendor_name) > 120:
            self._record_pre_scan_limit(requester_ip, "", "validation_failed")
            raise PortalError(422, "vendor_name_invalid", "vendor_name is required and must be <=120 chars.")
        root = self._validate_pre_scan_url(vendor_url)
        vendor_url_norm = f"https://{root.netloc.lower()}/"
        ip_hash = self._pre_scan_hmac(requester_ip)
        vendor_url_hash = self._pre_scan_hmac(vendor_url_norm.lower())
        self._enforce_pre_scan_rate(ip_hash)
        cached = self._pre_scan_cached(vendor_url_hash)
        if cached:
            self._record_pre_scan_limit(requester_ip, vendor_url_norm, "cache_hit")
            return json.loads(cached["envelope_json"]), cached["pdf_blob"]
        try:
            fetches = self._pre_scan_scrape(root)
            envelope = self._build_pre_scan_envelope(vendor_name, vendor_url_norm, fetches)
            pdf = self._render_pre_scan_pdf(vendor_name, vendor_url_norm, envelope)
            self.db.execute(
                "INSERT INTO pre_scan_audit VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    envelope["scan_id"],
                    envelope["vendor_name_hash"],
                    envelope["vendor_url_hash"],
                    ip_hash,
                    envelope["scanned_at"],
                    canonical_json(envelope).decode("utf-8"),
                    pdf,
                    "ok",
                ),
            )
            self.db.commit()
            self._record_pre_scan_limit(requester_ip, vendor_url_norm, "ok")
            self.audit("portal", "pre_scan_completed", "pre_scan", envelope["scan_id"], {"surface": PRE_SCAN_SURFACE})
            return envelope, pdf
        except Exception:
            self._record_pre_scan_limit(requester_ip, vendor_url_norm, "failed")
            raise

    def _validate_pre_scan_url(self, vendor_url: str):
        parsed = urlparse(vendor_url.strip())
        if parsed.scheme != "https" or not parsed.hostname:
            raise PortalError(422, "vendor_url_invalid", "vendor_url must be a well-formed public https URL.")
        if not os.environ.get("PRE_SCAN_FIXTURE_DIR") and is_private_host(parsed.hostname):
            raise PortalError(422, "vendor_url_private", "vendor_url resolved to a private, local, or reserved address.")
        return parsed

    def _pre_scan_cached(self, vendor_url_hash: str) -> sqlite3.Row | None:
        cutoff = (datetime.now(UTC) - timedelta(hours=24)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return self.db.execute(
            "SELECT * FROM pre_scan_audit WHERE vendor_url_hash = ? AND scanned_at >= ? ORDER BY scanned_at DESC LIMIT 1",
            (vendor_url_hash, cutoff),
        ).fetchone()

    def _enforce_pre_scan_rate(self, ip_hash: str) -> None:
        cutoff = (datetime.now(UTC) - timedelta(hours=24)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        ip_count = self.db.execute(
            "SELECT COUNT(*) AS count FROM pre_scan_rate_limits WHERE ip_hash = ? AND attempted_at >= ? AND result IN ('ok','cache_hit')",
            (ip_hash, cutoff),
        ).fetchone()["count"]
        if ip_count >= PRE_SCAN_PER_IP_LIMIT:
            raise PortalError(429, "pre_scan_rate_limited", "Pre-scan limit is 5 scans per IP per 24h.")
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        global_count = self.db.execute(
            "SELECT COUNT(*) AS count FROM pre_scan_rate_limits WHERE attempted_at >= ? AND result IN ('ok','cache_hit')",
            (f"{day}T00:00:00Z",),
        ).fetchone()["count"]
        if global_count >= PRE_SCAN_GLOBAL_LIMIT:
            raise PortalError(429, "pre_scan_global_rate_limited", "Pre-scan global daily limit reached.")
        recent = list(
            self.db.execute(
                "SELECT result FROM pre_scan_rate_limits WHERE ip_hash = ? ORDER BY id DESC LIMIT 3",
                (ip_hash,),
            )
        )
        if len(recent) == 3 and all(row["result"] == "validation_failed" for row in recent):
            raise PortalError(429, "pre_scan_soft_blocked", "Too many consecutive failed pre-scan validations.")

    def _record_pre_scan_limit(self, requester_ip: str, vendor_url: str, result: str) -> None:
        self.db.execute(
            "INSERT INTO pre_scan_rate_limits (ip_hash, vendor_url_hash, attempted_at, result) VALUES (?, ?, ?, ?)",
            (
                self._pre_scan_hmac(requester_ip),
                self._pre_scan_hmac(vendor_url.lower()) if vendor_url else None,
                now_iso(),
                result,
            ),
        )
        self.db.commit()

    def _pre_scan_scrape(self, root) -> dict[str, PreScanFetch]:
        base = f"https://{root.netloc.lower()}"
        robots = self._pre_scan_fetch(base, "/robots.txt")
        disallows = self._parse_robots_disallows(robots.body if robots.ok else "")
        fetches: dict[str, PreScanFetch] = {"robots": robots}
        total_bytes = len(robots.body.encode("utf-8"))
        for key, path in [("home", "/")]:
            fetched = self._pre_scan_fetch(base, path)
            fetches[key] = fetched
            total_bytes += len(fetched.body.encode("utf-8"))
        target_groups = [
            [("security", "/security"), ("trust", "/trust"), ("security-overview", "/security-overview")],
            [("privacy", "/privacy"), ("privacy-policy", "/privacy-policy")],
            [("status", "/status")],
            [("model-card", "/model-card"), ("system-card", "/system-card")],
            [("security-txt", "/.well-known/security.txt")],
        ]
        for group in target_groups:
            for key, path in group:
                if len(fetches) >= PRE_SCAN_MAX_GETS:
                    break
                if self._path_blocked(path, disallows):
                    fetches[key] = PreScanFetch(urljoin(base, path), path, None, "", "blocked by robots.txt")
                    break
                fetched = self._pre_scan_fetch(base, path)
                total_bytes += len(fetched.body.encode("utf-8"))
                if total_bytes > PRE_SCAN_MAX_BYTES:
                    fetches[key] = PreScanFetch(urljoin(base, path), path, None, "", "byte budget exceeded")
                    return fetches
                fetches[key] = fetched
                if fetched.ok:
                    break
        home = fetches.get("home")
        if home and home.ok and len(fetches) < PRE_SCAN_MAX_GETS:
            for href, text in extract_links(home.body):
                abs_url = urljoin(base + "/", href)
                parsed = urlparse(abs_url)
                if re.search(r"model card|system card", text, re.I) and parsed.netloc == root.netloc.lower():
                    fetches.setdefault("discovered-model-card", self._pre_scan_fetch(base, parsed.path or "/"))
                    break
            for href, _ in extract_links(home.body):
                parsed = urlparse(urljoin(base + "/", href))
                if parsed.scheme == "https" and parsed.netloc.startswith("status.") and len(fetches) < PRE_SCAN_MAX_GETS:
                    # BEAAA-5736 F-14: re-validate status subdomain before fetching (defense-in-depth).
                    status_base = f"{parsed.scheme}://{parsed.netloc}"
                    try:
                        self._validate_pre_scan_url(status_base)
                    except PortalError:
                        fetches.setdefault(
                            "discovered-status",
                            PreScanFetch(status_base, parsed.path or "/", None, "", "status_subdomain_private_blocked"),
                        )
                    else:
                        fetches.setdefault("discovered-status", self._pre_scan_fetch(status_base, parsed.path or "/"))
                    break
        return fetches

    def _pre_scan_fetch(self, base: str, path: str) -> PreScanFetch:
        fixture_dir = os.environ.get("PRE_SCAN_FIXTURE_DIR")
        parsed_base = urlparse(base)
        url = urljoin(base + "/", path.lstrip("/"))
        if fixture_dir:
            host_dir = Path(fixture_dir) / parsed_base.hostname.lower()
            filename = "root.html" if path == "/" else path.strip("/").replace("/", "__") or "root"
            fixture = host_dir / filename
            if not fixture.exists() and not filename.endswith(".txt"):
                fixture = host_dir / f"{filename}.html"
            if not fixture.exists():
                return PreScanFetch(url, path, 404, "", "fixture missing")
            return PreScanFetch(url, path, 200, fixture.read_text(encoding="utf-8"))
        # BEAAA-5736 F-05: follow_redirects=False + manual redirect loop.
        # Each Location header is re-validated via _validate_pre_scan_url before re-issue.
        # Any non-https or private-host Location is rejected immediately (redirect blocked).
        _MAX_HOPS = 3
        current_url = url
        for _ in range(_MAX_HOPS + 1):
            try:
                with httpx.Client(
                    headers={"user-agent": PRE_SCAN_UA, "accept": "text/html,text/plain"},
                    timeout=5.0,
                    follow_redirects=False,
                ) as client:
                    resp = client.get(current_url)
            except Exception as err:  # noqa: BLE001
                return PreScanFetch(url, path, None, "", f"fetch failed: {err}")
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location", "")
                if not location:
                    return PreScanFetch(url, path, None, "", "redirect missing Location header")
                abs_location = urljoin(current_url, location)
                try:
                    self._validate_pre_scan_url(abs_location)
                except PortalError:
                    return PreScanFetch(
                        url, path, resp.status_code, "",
                        "redirect blocked: Location points to private or non-https host",
                    )
                current_url = abs_location
                continue
            ctype = resp.headers.get("content-type", "")
            body = resp.text if "text/html" in ctype or "text/plain" in ctype or path.endswith(".txt") else ""
            return PreScanFetch(str(resp.url), path, resp.status_code, body[:PRE_SCAN_MAX_BYTES])
        return PreScanFetch(url, path, None, "", "too many redirects")

    def _parse_robots_disallows(self, robots_body: str) -> list[str]:
        out: list[str] = []
        applies = False
        for raw in robots_body.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            key, _, value = line.partition(":")
            if key.lower() == "user-agent":
                agent = value.strip().lower()
                applies = agent in {"*", "backbond-prescan", "backbond-prescan/0.1"}
            elif applies and key.lower() == "disallow" and value.strip():
                out.append(value.strip())
        return out

    def _path_blocked(self, path: str, disallows: list[str]) -> bool:
        return any(path.startswith(rule) for rule in disallows if rule != "/")

    def _build_pre_scan_envelope(self, vendor_name: str, vendor_url: str, fetches: dict[str, PreScanFetch]) -> dict[str, Any]:
        scan_id = os.environ.get("PRE_SCAN_FIXED_SCAN_ID") or str(uuid.uuid4())
        scanned_at = os.environ.get("PRE_SCAN_FIXED_NOW") or now_iso()
        signals = self._pre_scan_signals(fetches)
        counts = {pillar: {"present": 0, "absent": 0, "unknown": 0} for pillar in ("GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS")}
        for signal in signals:
            counts[signal["pillar"]][signal["state"]] += 1
        body = {
            "surface": PRE_SCAN_SURFACE,
            "pre_scan_version": PRE_SCAN_VERSION,
            "scan_id": scan_id,
            "vendor_name_hash": self._pre_scan_hmac(vendor_name),
            "vendor_url_hash": self._pre_scan_hmac(vendor_url.lower()),
            "scanned_at": scanned_at,
            "signals": signals,
            "counts": counts,
            "disclaimer": PRE_SCAN_DEFAULT_DISCLAIMER,
        }
        sig = b64url(self._cto_attest_key().sign(canonical_json(body)))
        body["signature"] = {"alg": "ed25519", "key_id": "cto-attest-v1", "fingerprint": PRE_SCAN_CTO_FINGERPRINT, "sig": sig}
        return body

    def _signal(self, pillar: str, signal: str, state: str, fetch: PreScanFetch | None, snippet: str = "", note: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"pillar": pillar, "signal": signal, "state": state}
        if fetch and state == "present":
            out["evidence_ref"] = {"source_url": fetch.url, "sha256": sha256_hex(fetch.body), "match_snippet": snippet}
        elif note or (fetch and fetch.note):
            out["note"] = note or fetch.note
        return out

    def _first_ok(self, fetches: dict[str, PreScanFetch], keys: list[str]) -> PreScanFetch | None:
        for key in keys:
            fetch = fetches.get(key)
            if fetch and fetch.ok:
                return fetch
        return None

    def _first_blocked(self, fetches: dict[str, PreScanFetch], keys: list[str]) -> PreScanFetch | None:
        for key in keys:
            fetch = fetches.get(key)
            if fetch and fetch.note == "blocked by robots.txt":
                return fetch
        return None

    def _pre_scan_signals(self, fetches: dict[str, PreScanFetch]) -> list[dict[str, Any]]:
        signals: list[dict[str, Any]] = []
        security = self._first_ok(fetches, ["security", "trust", "security-overview"])
        security_blocked = self._first_blocked(fetches, ["security", "trust", "security-overview"])
        sec_text = strip_html_text(security.body) if security else ""
        soc_snip = match_snippet(sec_text, r"\b(SOC 2|SOC-2|SOC2 Type (I|II)|ISO\s?27001)\b") if security else ""
        if soc_snip:
            signals.append(self._signal("GOVERNANCE", "soc2_iso27001", "present", security, soc_snip))
        else:
            signals.append(self._signal("GOVERNANCE", "soc2_iso27001", "unknown" if security_blocked else "absent", security_blocked, note="blocked by robots.txt" if security_blocked else None))

        privacy = self._first_ok(fetches, ["privacy", "privacy-policy"])
        if privacy and len(strip_html_text(privacy.body)) >= 500:
            signals.append(self._signal("GOVERNANCE", "privacy_policy", "present", privacy, "privacy policy present"))
        else:
            signals.append(self._signal("GOVERNANCE", "privacy_policy", "absent", privacy))

        security_txt = fetches.get("security-txt")
        if security_txt and security_txt.ok and re.search(r"^Contact:", security_txt.body, re.I | re.M):
            signals.append(self._signal("GOVERNANCE", "security_txt", "present", security_txt, match_snippet(security_txt.body, r"Contact:")))
        else:
            signals.append(self._signal("GOVERNANCE", "security_txt", "absent", security_txt))

        status = self._first_ok(fetches, ["status", "discovered-status"])
        if status:
            signals.append(self._signal("ROBUSTNESS", "public_status_page", "present", status, "public status page present"))
            status_text = strip_html_text(status.body)
            incident = match_snippet(status_text, r"\b(20\d{2}-\d{2}-\d{2}|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec).*\b(resolved|mitigated|incident)\b")
            signals.append(self._signal("ROBUSTNESS", "incident_history_disclosed", "present", status, incident) if incident else self._signal("ROBUSTNESS", "incident_history_disclosed", "absent", status))
        else:
            signals.append(self._signal("ROBUSTNESS", "public_status_page", "unknown", None, note="status page unreachable"))
            signals.append(self._signal("ROBUSTNESS", "incident_history_disclosed", "unknown", None, note="status page unreachable"))

        model_card = self._first_ok(fetches, ["model-card", "system-card", "discovered-model-card"])
        if model_card:
            signals.append(self._signal("BIAS_FAIRNESS", "model_or_system_card", "present", model_card, "model or system card present"))
            model_text = strip_html_text(model_card.body)
            eval_snip = match_snippet(model_text, r"\b(benchmark|eval(uation)?|MMLU|TruthfulQA|HELM)\b")
            signals.append(self._signal("BIAS_FAIRNESS", "evaluation_benchmark_disclosure", "present", model_card, eval_snip) if eval_snip else self._signal("BIAS_FAIRNESS", "evaluation_benchmark_disclosure", "absent", model_card))
        else:
            signals.append(self._signal("BIAS_FAIRNESS", "model_or_system_card", "absent", None, note="no model card path or anchor fetched"))
            signals.append(self._signal("BIAS_FAIRNESS", "evaluation_benchmark_disclosure", "unknown", None, note="model card unavailable"))

        home = fetches.get("home")
        dpa_text = (home.body if home and home.ok else "") + " " + (security.body if security else "")
        dpa_snip = match_snippet(strip_html_text(dpa_text), r"\b(DPA|data processing agreement|sub-?processor)\b")
        if dpa_snip:
            signals.append(self._signal("OPS", "dpa_subprocessor_link", "present", home or security, dpa_snip))
        else:
            signals.append(self._signal("OPS", "dpa_subprocessor_link", "absent", home or security))
        uptime_snip = match_snippet(strip_html_text(status.body), r"\b99\.[0-9]+%\b") if status else ""
        signals.append(self._signal("OPS", "public_uptime_slo", "present", status, uptime_snip) if uptime_snip else self._signal("OPS", "public_uptime_slo", "unknown" if not status else "absent", status, note="status page unreachable" if not status else None))
        return signals

    def _render_pre_scan_pdf(self, vendor_name: str, vendor_url: str, envelope: dict[str, Any]) -> bytes:
        buf = BytesIO()
        pdf = canvas.Canvas(buf, pagesize=A4)
        width, height = A4
        y = height - 44
        pdf.setFont("Helvetica-Bold", 15)
        pdf.drawString(44, y, PRE_SCAN_CMO_HEADER)
        y -= 22
        pdf.setFont("Helvetica", 9)
        for line in [
            f"Vendor: {html.escape(vendor_name)}",
            f"Scanned at: {envelope['scanned_at']}",
            f"Surface scanned: {html.escape(vendor_url)}",
        ]:
            pdf.drawString(44, y, line[:110])
            y -= 15
        for line in textwrap.wrap(PRE_SCAN_DEFAULT_DISCLAIMER, width=96, break_on_hyphens=False):
            pdf.drawString(44, y, line)
            y -= 15
        y -= 8
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(44, y, "Pillar Signals (present / absent / unknown)")
        y -= 16
        pdf.setFont("Helvetica", 9)
        for pillar, counts in envelope["counts"].items():
            pdf.drawString(58, y, f"{pillar}: {counts['present']} / {counts['absent']} / {counts['unknown']}")
            y -= 14
        y -= 8
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(44, y, "Detail")
        y -= 16
        pdf.setFont("Helvetica", 8)
        for signal in envelope["signals"]:
            marker = {"present": "+", "absent": "x", "unknown": "?"}[signal["state"]]
            ref = signal.get("evidence_ref") or {}
            suffix = ref.get("sha256", signal.get("note", ""))[:18]
            pdf.drawString(58, y, f"{marker} {signal['pillar']} {signal['signal']} ({signal['state']}) {suffix}"[:120])
            y -= 11
            if y < 70:
                break
        pdf.setFont("Helvetica", 8)
        footer = PRE_SCAN_CMO_METHODOLOGY_FOOTER.format(
            scan_id=envelope["scan_id"],
            signature_fingerprint=PRE_SCAN_CTO_FINGERPRINT,
        )
        footer_y = 48
        for line in textwrap.wrap(footer, width=112, break_on_hyphens=False):
            pdf.drawString(44, footer_y, line)
            footer_y -= 12
        pdf.showPage()
        pdf.save()
        return buf.getvalue()

    def get_pre_scan_envelope(self, scan_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT envelope_json FROM pre_scan_audit WHERE scan_id = ?", (scan_id,)).fetchone()
        if not row:
            raise PortalError(404, "pre_scan_not_found", "Pre-scan not found.")
        return json.loads(row["envelope_json"])

    def get_pre_scan_pdf(self, scan_id: str) -> bytes:
        row = self.db.execute("SELECT pdf_blob FROM pre_scan_audit WHERE scan_id = ?", (scan_id,)).fetchone()
        if not row:
            raise PortalError(404, "pre_scan_not_found", "Pre-scan not found.")
        return row["pdf_blob"]

    def admin_summary(self) -> str:
        invites = list(self.db.execute("SELECT * FROM invites ORDER BY created_at DESC LIMIT 20"))
        dps = list(self.db.execute("SELECT * FROM design_partners ORDER BY created_at DESC LIMIT 20"))
        scans = list(self.db.execute("SELECT * FROM scans ORDER BY created_at DESC LIMIT 20"))
        body = """
<h1>Portal v1 Admin</h1>
<nav><a href="/admin">Dashboard</a><a href="/admin/audit">Audit log</a><a href="/portal_pins.json">Pins</a></nav>
<form method="post" action="/admin/invites">
<h2>Issue Invite</h2>
<label>DP name <input name="dp_name" required></label>
<label>Primary contact email <input name="email" type="email" required></label>
<label>Scan category <select name="scan_category"><option>call-center</option><option>coding-agent</option><option>customer-support</option><option>autonomous</option></select></label>
<button type="submit">Issue invite</button></form>
"""
        body += "<h2>Invites</h2><table><tr><th>DP</th><th>Email</th><th>Token</th><th>Accepted</th></tr>"
        for row in invites:
            body += f"<tr><td>{html.escape(row['dp_name'])}</td><td>{html.escape(row['email'])}</td><td><a href='/invite?token={row['token']}'>{row['token'][:10]}...</a></td><td>{row['accepted_at'] or ''}</td></tr>"
        body += "</table><h2>Design Partners</h2><table><tr><th>ID</th><th>DP</th><th>Endpoint flag</th><th>Action</th></tr>"
        for row in dps:
            body += f"<tr><td>{row['id']}</td><td>{html.escape(row['dp_name'])}</td><td>{row['endpoint_flag']}</td><td><form method='post' action='/scans'><input type='hidden' name='dp_id' value='{row['id']}'><button>Trigger scan</button></form></td></tr>"
        body += "</table><h2>Scans</h2><table><tr><th>ID</th><th>Status</th><th>Reports</th></tr>"
        for row in scans:
            links = " ".join(f"<a href='/reports/{row['id']}/{v}'>[{v}]</a>" for v in ("dp", "internal_uw", "evidence_appendix")) if row["score_json"] else ""
            body += f"<tr><td>{row['id']}</td><td>{row['status']}</td><td>{links}</td></tr>"
        body += "</table>"
        return self._render_page("Portal v1 Admin", body)

    def audit_summary(self) -> str:
        rows = list(self.db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 100"))
        status = "valid" if self.verify_audit_chain() else "invalid"
        # BEAAA-4937 / v0.2 §5.4 — render scan_diff_rendered rows with ARE Delta + top pillar Delta + view-diff link.
        diff_rows = {
            row["candidate_scan_id"]: json.loads(row["artifact_json"])
            for row in self.db.execute("SELECT candidate_scan_id, artifact_json FROM scan_diffs")
        }
        body = f"""
<h1>Portal v1 Audit Log</h1>
<nav><a href="/admin">Dashboard</a><a href="/admin/audit">Audit log</a><a href="/portal_pins.json">Pins</a></nav>
<p class="muted">Hash chain: {html.escape(status)} | Entries shown: {len(rows)}</p>
<table><tr><th>ID</th><th>At</th><th>Actor</th><th>Event</th><th>Entity</th><th>Summary</th><th>Payload hash</th><th>Entry hash</th></tr>
"""
        for row in rows:
            entity = f"{row['entity_type']}:{row['entity_id']}"
            summary = ""
            if row["event_type"] == "scan_diff_rendered":
                diff = diff_rows.get(row["entity_id"]) or {}
                are_delta = diff.get("are_total_delta")
                pillar_deltas = diff.get("pillar_deltas") or {}
                top_pillar = None
                top_val = None
                for pillar, value in pillar_deltas.items():
                    if value is None:
                        continue
                    if top_val is None or abs(value) > abs(top_val):
                        top_pillar = pillar
                        top_val = value
                are_str = f"{are_delta:+.2f}" if isinstance(are_delta, (int, float)) else "n/a"
                pillar_str = (
                    f"{top_pillar} {top_val:+.2f}" if top_pillar is not None and top_val is not None else "n/a"
                )
                summary = (
                    f"ARE \u0394 = {html.escape(are_str)}, top pillar \u0394 = ({html.escape(pillar_str)}) "
                    f"<a href='/scans/{html.escape(row['entity_id'])}/diff'>view diff</a>"
                )
            elif row["event_type"] in {"schedule_updated", "schedule_advanced"}:
                summary = f"<a href='/dp/{html.escape(row['entity_id'])}/schedule'>view schedule</a>"
            body += (
                f"<tr><td>{row['id']}</td><td>{html.escape(row['at'])}</td>"
                f"<td>{html.escape(row['actor'])}</td><td>{html.escape(row['event_type'])}</td>"
                f"<td>{html.escape(entity)}</td><td>{summary}</td>"
                f"<td>{html.escape(row['payload_hash'][:16])}</td>"
                f"<td>{html.escape(row['entry_hash'][:16])}</td></tr>"
            )
        body += "</table>"
        return self._render_page("Portal v1 Audit Log", body)

    def probe_ledger_page(self) -> str:
        if not PROBE_LEDGER_SOURCE_PATH.exists():
            raise PortalError(503, "probe_ledger_source_missing", "Canonical probe ledger source is not available.")
        ledger = json.loads(PROBE_LEDGER_SOURCE_PATH.read_text(encoding="utf-8"))
        source_bytes = PROBE_LEDGER_SOURCE_PATH.read_bytes()
        source_sha = sha256_hex(source_bytes)
        totals = ledger.get("totals") or {}

        def dims(value: dict[str, Any]) -> list[str]:
            return list(value.get("rubric_dims_supplied") or [])

        rows: list[str] = []
        for pack in ledger.get("deterministic_corpus_packs") or []:
            row_dims = dims(pack)
            rows.append(
                "<tr>"
                "<td>deterministic pack</td>"
                f"<td>{html.escape(pack.get('name', ''))}</td>"
                f"<td>{len(row_dims)}</td>"
                "<td>active</td>"
                f"<td>{html.escape(ledger.get('rubric_version', ''))}</td>"
                f"<td><code>{html.escape(pack.get('on_disk_path', ''))}</code></td>"
                f"<td>{html.escape(', '.join(row_dims))}</td>"
                "</tr>"
            )
        for family in ledger.get("live_scanner_families") or []:
            row_dims = dims(family)
            path = family.get("on_disk_path") or family.get("probe_mapping_file") or ""
            rows.append(
                "<tr>"
                "<td>live scanner family</td>"
                f"<td>{html.escape(family.get('name', ''))}</td>"
                f"<td>{len(row_dims)}</td>"
                "<td>active</td>"
                f"<td>{html.escape(ledger.get('rubric_version', ''))}</td>"
                f"<td><code>{html.escape(path)}</code></td>"
                f"<td>{html.escape(', '.join(row_dims))}</td>"
                "</tr>"
            )
        for dim in ledger.get("rubric_dimensions") or []:
            status = "active"
            added = dim.get("added_in_rubric_version") or ledger.get("rubric_version", "")
            rows.append(
                "<tr>"
                "<td>rubric dimension</td>"
                f"<td>{html.escape(dim.get('name', ''))}</td>"
                "<td>1</td>"
                f"<td>{status}</td>"
                f"<td>{html.escape(added)}</td>"
                f"<td><code>{html.escape(dim.get('id', ''))}</code></td>"
                f"<td>{html.escape(dim.get('pillar', ''))}</td>"
                "</tr>"
            )

        body = f"""
<h1>BackBond Scanner Probe Ledger</h1>
<p class="muted">Internal staging route. Not linked from public navigation. Source: <code>{html.escape(str(PROBE_LEDGER_SOURCE_PATH.relative_to(Path(__file__).resolve().parents[2])))}</code></p>
<table>
<tr><th>Scanner v</th><td>{html.escape(ledger.get('scanner_version', ''))}</td><th>Corpus v</th><td>{html.escape(ledger.get('corpus_version', ''))}</td></tr>
<tr><th>Rubric v</th><td>{html.escape(ledger.get('rubric_version', ''))}</td><th>Ledger schema</th><td>{html.escape(ledger.get('ledger_schema_version', PROBE_LEDGER_SCHEMA_VERSION))}</td></tr>
<tr><th>Deterministic packs</th><td>{totals.get('deterministic_corpus_packs', 0)}</td><th>Deterministic probes</th><td>{totals.get('deterministic_corpus_probes', 0)}</td></tr>
<tr><th>Live families</th><td>{totals.get('live_scanner_families', 0)}</td><th>Rubric dimensions</th><td>{totals.get('rubric_dimensions', 0)}</td></tr>
<tr><th>HTML source bytes</th><td>{len(source_bytes)}</td><th>Source sha256</th><td><code>{source_sha}</code></td></tr>
</table>
<h2>Ledger Rows</h2>
<table><tr><th>Type</th><th>Name</th><th>Dimension count</th><th>Status</th><th>Introduced</th><th>Path / ID</th><th>Dimensions / Pillar</th></tr>{''.join(rows)}</table>
<p><strong>No coverage commitment.</strong> This page documents BackBond Scanner evidence substrate for underwriting readiness; it is not a quote, binder, or external publication.</p>
"""
        return self._render_page("BackBond Scanner Probe Ledger", body)

    def dp_dashboard(self, dp_id: str) -> str:
        dp = self.db.execute("SELECT * FROM design_partners WHERE id = ?", (dp_id,)).fetchone()
        if not dp:
            raise PortalError(404, "dp_not_found", "Design partner not found.")
        connector = self.db.execute("SELECT * FROM connectors WHERE dp_id = ?", (dp_id,)).fetchone()
        scans = list(self.db.execute("SELECT * FROM scans WHERE dp_id = ? ORDER BY created_at DESC LIMIT 20", (dp_id,)))
        scan_button = (
            f"<form method='post' action='/dp/{dp_id}/scans'><button>Trigger scan</button></form>"
            if connector
            else "<p class='muted'>Connector enrollment required before scan trigger.</p>"
        )
        submissions = list(self.db.execute(
            "SELECT * FROM questionnaire_submissions WHERE dp_id = ? ORDER BY created_at DESC LIMIT 10",
            (dp_id,),
        ))
        q_version = questionnaire_schema.QUESTIONNAIRE_VERSION if questionnaire_schema else "n/a"
        body = f"""
<h1>Design Partner Portal</h1>
<p class="muted">DP: {html.escape(dp['dp_name'])} | Endpoint: staging | Portal v{PORTAL_VERSION}</p>
{scan_button}
<p><a href="/dp/{dp_id}/questionnaire">BackBond governance questionnaire (30 questions, v{q_version})</a></p>
<h2>Scans</h2><table><tr><th>ID</th><th>Status</th><th>Report</th></tr>
"""
        for row in scans:
            report = f"<a href='/reports/{row['id']}/dp'>View report</a>" if row["score_json"] else ""
            body += f"<tr><td>{row['id']}</td><td>{row['status']}</td><td>{report}</td></tr>"
        body += "</table>"
        if submissions:
            body += "<h2>Governance questionnaire submissions</h2><table><tr><th>ID</th><th>Status</th><th>Created</th><th>Submitted</th><th>Artifacts</th></tr>"
            for row in submissions:
                if row["status"] == "submitted":
                    links = (
                        f"<a href='/questionnaire/{row['id']}/payload.json'>payload</a> "
                        f"<a href='/questionnaire/{row['id']}.pdf'>pdf</a> "
                        f"<a href='/questionnaire/{row['id']}/evidence-bundle.json'>evidence bundle</a>"
                    )
                else:
                    links = f"<a href='/questionnaire/{row['id']}/form'>continue draft</a>"
                body += (
                    f"<tr><td>{row['id']}</td><td>{html.escape(row['status'])}</td>"
                    f"<td>{html.escape(row['created_at'])}</td>"
                    f"<td>{html.escape(row['submitted_at'] or '')}</td><td>{links}</td></tr>"
                )
            body += "</table>"
        return self._render_page("Design Partner Portal", body)


class PortalHandler(BaseHTTPRequestHandler):
    service: PortalService

    def _route_body_cap(self) -> int:
        """BEAAA-5736 F-04: per-route inbound body cap.

        Large-body routes (envelope ingest, SBOM attestation, questionnaire submit) get 1 MiB;
        every other POST/PUT path gets the 256 KiB default. The path is parsed before any body
        read so the cap decision precedes rfile.read in _read_body.
        """
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        if len(parts) == 3 and parts[0] == "scans" and parts[2] in ("envelopes", "sbom-attestation"):
            return BODY_MAX_BYTES_LARGE
        if len(parts) == 3 and parts[0] == "questionnaire" and parts[2] == "submit":
            return BODY_MAX_BYTES_LARGE
        return BODY_MAX_BYTES_DEFAULT

    def _read_body(self) -> Any:
        # BEAAA-5736 F-04: cap Content-Length BEFORE rfile.read to prevent memory-exhaustion DoS.
        # BEAAA-5736 F-13: malformed JSON returns 400 invalid_json (bundled — same _read_body surface).
        max_bytes = self._route_body_cap()
        length_raw = self.headers.get("content-length", "0")
        try:
            length = int(length_raw)
        except ValueError as err:
            raise PortalError(400, "content_length_invalid", f"Content-Length header must be a non-negative integer: {err}")
        if length < 0:
            raise PortalError(400, "content_length_invalid", "Content-Length header must be non-negative.")
        if length > max_bytes:
            raise PortalError(413, "request_body_too_large", f"Request body exceeds {max_bytes}-byte cap for this route.")
        raw = self.rfile.read(length) if length else b""
        ctype = self.headers.get("content-type", "")
        if "application/json" in ctype:
            try:
                return json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError as err:
                raise PortalError(400, "invalid_json", f"Request body is not valid JSON: {err}")
        return parse_qs(raw.decode("utf-8"))

    def _send(self, status: int, body: str | bytes, ctype: str = "text/html; charset=utf-8", extra_headers: dict[str, str] | None = None) -> None:
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Any, extra_headers: dict[str, str] | None = None) -> None:
        self._send(status, json.dumps(payload, indent=2), "application/json", extra_headers=extra_headers)

    def _handle_error(self, err: Exception) -> None:
        if isinstance(err, PortalError):
            # BEAAA-4996: admin_unauthorized carries WWW-Authenticate per the gate contract.
            extra = {"WWW-Authenticate": 'Bearer realm="portal-admin"'} if err.code == "admin_unauthorized" else None
            self._json(err.status, {"error": err.code, "detail": err.detail}, extra_headers=extra)
        else:
            self._json(500, {"error": "internal_error", "detail": str(err)})

    def _require_admin(self) -> None:
        """BEAAA-4996: Gate /admin* + v0.2 admin/Founder paths behind env-bound bearer.

        Posture:
          - PORTAL_ADMIN_TOKEN unset (prod): raise 404 admin_disabled so the admin
            surface is indistinguishable from "doesn't exist" to an unauthenticated
            scanner (preserves the "public pages only" boundary in portal.py:69, :1530).
          - PORTAL_ADMIN_TOKEN set + missing/invalid bearer: 401 admin_unauthorized
            with WWW-Authenticate: Bearer realm="portal-admin" (added in _handle_error).
          - PORTAL_ADMIN_TOKEN set + correct bearer: returns silently; caller continues
            with the existing 200 / 201 behavior, byte-equivalent to pre-change.
        Constant-time compare via hmac.compare_digest.
        """
        expected = os.environ.get("PORTAL_ADMIN_TOKEN")
        if not expected:
            raise PortalError(404, "admin_disabled", "Route not found.")
        auth = self.headers.get("Authorization", "")
        presented = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        if not presented or not hmac.compare_digest(presented, expected):
            raise PortalError(401, "admin_unauthorized", "Admin endpoints require a valid bearer token.")

    def _require_dp_bearer(self, dp_id: str) -> None:
        """BEAAA-5698 AC2: Gate POST /dp/{dp_id}/scans on per-DP bearer issued at accept_invite.

        Posture:
          - Bearer absent / malformed Authorization header: 403 dp_unauthorized.
          - DP row missing or has no bearer hash (legacy pre-AC2 row): 403 dp_unauthorized.
          - Bearer present but does not match stored sha256 hash: 403 dp_unauthorized.
          - Bearer matches: returns silently; caller continues with trigger_scan(actor="dp").
        Constant-time compare via hmac.compare_digest over sha256 hex of presented bearer.
        Unified error code (no enumeration aid) — same response shape for all failure modes.
        """
        auth = self.headers.get("Authorization", "")
        presented = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        row = self.service.db.execute(
            "SELECT dp_bearer_hash FROM design_partners WHERE id = ?", (dp_id,)
        ).fetchone()
        stored = row["dp_bearer_hash"] if row else None
        if not presented or not stored:
            raise PortalError(403, "dp_unauthorized", "DP scan trigger requires a valid bearer token.")
        presented_hash = sha256_hex(presented.encode("utf-8"))
        if not hmac.compare_digest(presented_hash, stored):
            raise PortalError(403, "dp_unauthorized", "DP scan trigger requires a valid bearer token.")

    def _scan_owner_dp_id(self, scan_id: str) -> str:
        row = self.service.db.execute("SELECT dp_id FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if not row:
            raise PortalError(404, "scan_not_found", "Scan not found.")
        return row["dp_id"]

    def _require_portal_read(self, *, dp_id: str | None = None, allow_dp: bool = True) -> None:
        """BEAAA-11607: default-deny authorization helper for portal GET read surfaces.

        Admin bearer can read any protected portal artifact. Matching per-DP bearer can
        read only DP-allowed artifacts owned by that design partner. Routes that do not
        have a DP owner, or internal-only variants, call this with ``allow_dp=False``.
        """
        auth = self.headers.get("Authorization", "")
        presented = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        admin_expected = os.environ.get("PORTAL_ADMIN_TOKEN")
        if (
            admin_expected
            and presented
            and hmac.compare_digest(presented, admin_expected)
        ):
            return
        if allow_dp and dp_id and presented:
            row = self.service.db.execute(
                "SELECT dp_bearer_hash FROM design_partners WHERE id = ?", (dp_id,)
            ).fetchone()
            stored = row["dp_bearer_hash"] if row else None
            if stored and hmac.compare_digest(sha256_hex(presented.encode("utf-8")), stored):
                return
        raise PortalError(403, "portal_read_unauthorized", "Portal read requires an authorized bearer token.")

    def _require_connector_bearer(self) -> str:
        """F-08: Gate connector endpoints on bearer issued at enroll; return verified connector_id.

        Posture:
          - Bearer absent / malformed: 403 connector_unauthorized.
          - x-connector-id absent or does not match the bearer's enrolled row: 403 connector_unauthorized.
          - Match: returns connector_id silently; caller continues.
        Constant-time compare via hmac.compare_digest over sha256 hex of presented secret.
        """
        auth = self.headers.get("Authorization", "")
        presented = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        connector_id = self.headers.get("x-connector-id", "")
        if not presented or not connector_id:
            raise PortalError(403, "connector_unauthorized", "Connector endpoints require a valid bearer token and x-connector-id header.")
        row = self.service.db.execute(
            "SELECT id, oidc_client_secret_hash FROM connectors WHERE id = ?", (connector_id,)
        ).fetchone()
        if not row:
            raise PortalError(403, "connector_unauthorized", "Connector endpoints require a valid bearer token and x-connector-id header.")
        presented_hash = sha256_hex(presented.encode("utf-8"))
        if not hmac.compare_digest(presented_hash, row["oidc_client_secret_hash"]):
            raise PortalError(403, "connector_unauthorized", "Connector endpoints require a valid bearer token and x-connector-id header.")
        return connector_id

    def do_GET(self) -> None:
        try:
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            # BEAAA-5016: verify-api-v0.1 routes (deferred to module).
            import verify_service as _verify
            v_handled = _verify.handle_get(self.service, parts, self.headers.get("Accept", ""))
            if v_handled is not None:
                status, body, ctype = v_handled
                self._send(status, body, ctype)
                return
            # BEAAA-4919: governance questionnaire routes (deferred to module).
            import questionnaire_service as _qs
            q_handled = _qs.handle_get(self.service, parts)
            if q_handled is not None:
                status, body, ctype = q_handled
                self._send(status, body, ctype)
                return
            # BEAAA-4967: telemetry retrieval routes (deferred to module).
            import telemetry_v0_1 as _tel
            t_handled = _tel.handle_get(self.service, parts, parse_qs(url.query))
            if t_handled is not None:
                status, body, ctype = t_handled
                self._send(status, body, ctype)
                return
            # BEAAA-5867: post-bind sinks + observed-vs-predicted export routes.
            import post_bind_sinks_v0_1 as _pbs
            pbs_handled = _pbs.handle_get(self.service, parts, parse_qs(url.query))
            if pbs_handled is not None:
                status, body, ctype = pbs_handled
                self._send(status, body, ctype)
                return
            # BEAAA-6165: policy-system claim auto-open MVP read routes.
            import policy_auto_open_v0_1 as _pao
            pao_handled = _pao.handle_get(self.service, parts, parse_qs(url.query))
            if pao_handled is not None:
                status, body, ctype = pao_handled
                self._send(status, body, ctype)
                return
            if url.path in ("/", "/admin"):
                # BEAAA-4996: /admin gate. "/" reaches admin_summary too, so the gate
                # covers the full match branch to avoid leaking admin content publicly.
                self._require_admin()
                self._send(200, self.service.admin_summary())
            elif parts and parts[0] == "sandbox":
                self._send(200, self.service.sandbox_page(parts[1:]))
            elif url.path == "/pre-scan":
                self._send(200, self.service.pre_scan_form())
            elif len(parts) == 2 and parts[0] == "pre-scan" and parts[1].endswith(".pdf"):
                self._require_portal_read(allow_dp=False)
                self._send(200, self.service.get_pre_scan_pdf(parts[1][:-4]), "application/pdf")
            elif len(parts) == 2 and parts[0] == "pre-scan" and parts[1].endswith(".json"):
                self._require_portal_read(allow_dp=False)
                envelope = self.service.get_pre_scan_envelope(parts[1][:-5])
                self._send(200, canonical_json(envelope), "application/json")
            elif url.path == "/admin/audit":
                self._require_admin()  # BEAAA-4996
                self._send(200, self.service.audit_summary())
            elif url.path == "/probe-ledger":
                if os.environ.get("PROBE_LEDGER_ENABLED", "").lower() not in {"1", "true", "yes"}:
                    raise PortalError(404, "not_found", "Route not found.")
                self._send(200, self.service.probe_ledger_page())
            elif url.path == "/invite":
                token = parse_qs(url.query).get("token", [""])[0]
                body = f"""<h1>Accept ARE Assessment Invite</h1>
<form method="post" action="/dp/accept">
<input type="hidden" name="invite_token" value="{html.escape(token)}">
<label>Endpoint URL <input name="endpoint_url" required></label>
<label>Certificate reference <input name="cert_ref" required></label>
<label>Endpoint flag <select name="endpoint_flag"><option value="staging">staging</option><option value="prod">prod</option></select></label>
<label><input type="checkbox" name="disclosure_consent" value="1"> Disclosure consent for evidence appendix</label>
<p>Raw transcripts and credentials remain inside your environment. The connector returns only signed, redacted result envelopes. Assessment result, not bound coverage.</p>
<button>Accept invite</button></form>"""
                self._send(200, self.service._render_page("Accept invite", body))
            elif len(parts) == 2 and parts[0] == "dp":
                self._require_portal_read(dp_id=parts[1])
                self._send(200, self.service.dp_dashboard(parts[1]))
            elif len(parts) == 4 and parts[0] == "scans" and parts[2] == "tasks" and parts[3] == "next":
                status, payload = self.service.next_task(parts[1], self.headers.get("x-connector-id"))
                if status == 204:
                    self._send(204, b"", "application/json")
                else:
                    self._json(200, payload)
            elif len(parts) == 3 and parts[0] == "reports":
                dp_id = self._scan_owner_dp_id(parts[1])
                self._require_portal_read(dp_id=dp_id, allow_dp=parts[2] == "dp")
                path = self.service.config.evidence_dir / parts[1] / "reports" / f"{parts[2]}.html"
                if not path.exists():
                    raise PortalError(404, "report_not_found", "Report not found.")
                self._send(200, path.read_text(encoding="utf-8"))
            elif url.path == "/portal_pins.json":
                self._send(200, self.service.config.pins_path.read_bytes(), "application/json")
            elif len(parts) == 3 and parts[0] == "scans" and parts[2] == "sbom-attestation":
                self._require_portal_read(dp_id=self._scan_owner_dp_id(parts[1]))
                self._json(200, self.service.get_sbom_attestation(parts[1]))
            elif len(parts) == 3 and parts[0] == "scans" and parts[2] == "bind":
                self._require_portal_read(dp_id=self._scan_owner_dp_id(parts[1]))
                self._json(200, self.service.get_bind_decision(parts[1]))
            elif len(parts) == 3 and parts[0] == "dp" and parts[2] == "schedule":
                # BEAAA-4937 / v0.2 §5.1 — admin/Founder GET on persisted schedule row.
                self._require_admin()  # BEAAA-4996
                self._json(200, self.service.get_schedule(parts[1]))
            elif len(parts) == 2 and parts[0] == "connectors" and parts[1] == "schedule":
                # BEAAA-4937 / v0.2 §5.1 — connector long-poll fetch of signed descriptor.
                # F-08: require bearer + binding cross-check before serving schedule.
                connector_id = self._require_connector_bearer()
                self._json(200, self.service.connector_schedule(connector_id))
            elif (
                len(parts) == 5
                and parts[0] == "api"
                and parts[1] == "v1"
                and parts[2] == "scans"
                and parts[4] == "underwriting_rollup"
            ):
                # BEAAA-5761 — peril-aligned underwriting rollup (option 3 from BEAAA-5750).
                # Pure derived view over already-persisted score. Outside are-scanner-api-v1@v1.0 Output.
                self._require_portal_read(dp_id=self._scan_owner_dp_id(parts[3]))
                self._json(200, self.service.get_underwriting_rollup(parts[3]))
            elif (
                len(parts) == 5
                and parts[0] == "api"
                and parts[1] == "v1"
                and parts[2] == "scans"
                and parts[4] == "vendor_scan_capability_manifest"
            ):
                # BEAAA-12093 — additive vendor-scan lane disclosure. Non-score-moving.
                self._require_portal_read(dp_id=self._scan_owner_dp_id(parts[3]))
                self._json(200, self.service.get_vendor_scan_capability_manifest(parts[3]))
            elif len(parts) == 3 and parts[0] == "scans" and parts[2] == "diff":
                # BEAAA-4937 / v0.2 §5.1 — admin/UW GET on persisted scan_diff artifact.
                self._require_admin()  # BEAAA-4996
                self._json(200, self.service.get_scan_diff(parts[1]))
            else:
                raise PortalError(404, "not_found", "Route not found.")
        except Exception as err:
            self._handle_error(err)

    def do_POST(self) -> None:
        try:
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            body = self._read_body()
            # BEAAA-4919: governance questionnaire routes (deferred to module).
            import questionnaire_service as _qs
            q_handled = _qs.handle_post(self.service, parts, body)
            if q_handled is not None:
                status, qbody, ctype = q_handled
                self._send(status, qbody, ctype)
                return
            # BEAAA-4967: telemetry intake route (deferred to module).
            import telemetry_v0_1 as _tel
            t_handled = _tel.handle_post(self.service, parts, body)
            if t_handled is not None:
                status, tbody, ctype = t_handled
                self._send(status, tbody, ctype)
                return
            # BEAAA-6165: customer loss signal intake route.
            import policy_auto_open_v0_1 as _pao
            pao_handled = _pao.handle_post(self.service, parts, body)
            if pao_handled is not None:
                status, pbody, ctype = pao_handled
                self._send(status, pbody, ctype)
                return
            if url.path == "/admin/invites":
                # BEAAA-4996: gate runs BEFORE create_invite so no row/audit entry on unauth.
                self._require_admin()
                invite = self.service.create_invite(form_value(body, "dp_name"), form_value(body, "email"), form_value(body, "scan_category"))
                self._send(201, self.service._render_page("Invite issued", f"<h1>Invite issued</h1><p><a href='{invite['invite_url']}'>{invite['invite_url']}</a></p>"))
            elif url.path == "/pre-scan":
                requester_ip = self.headers.get("x-forwarded-for", self.client_address[0]).split(",", 1)[0].strip()
                _, pdf = self.service.run_pre_scan(
                    form_value(body, "vendor_name"),
                    form_value(body, "vendor_url"),
                    requester_ip,
                )
                self._send(200, pdf, "application/pdf")
            elif url.path == "/dp/accept":
                accepted = self.service.accept_invite(
                    form_value(body, "invite_token"),
                    form_value(body, "endpoint_url"),
                    form_value(body, "cert_ref"),
                    form_value(body, "endpoint_flag"),
                    bool(form_value(body, "disclosure_consent")),
                )
                accepted["dashboard_url"] = f"/dp/{accepted['dp_id']}"
                self._json(201, accepted)
            elif url.path == "/connectors/enroll":
                self._json(201, self.service.enroll_connector(body))
            elif url.path == "/scans":
                # BEAAA-5698 AC1: gate admin-initiated scan trigger; same posture as /admin/invites.
                self._require_admin()
                dp_id = body.get("dp_id") if isinstance(body, dict) else form_value(body, "dp_id")
                self._json(201, self.service.trigger_scan(dp_id))
            elif len(parts) == 3 and parts[0] == "dp" and parts[2] == "scans":
                # BEAAA-5698 AC2: gate DP-initiated scan trigger on per-DP bearer; UUID alone rejected.
                self._require_dp_bearer(parts[1])
                self._json(201, self.service.trigger_scan(parts[1], actor="dp"))
            elif len(parts) == 3 and parts[0] == "scans" and parts[2] == "envelopes":
                self._json(202, self.service.ingest_envelope(parts[1], body))
            elif len(parts) == 3 and parts[0] == "scans" and parts[2] == "complete":
                self._json(202, {"received": True, "scan_id": parts[1]})
            elif len(parts) == 3 and parts[0] == "scans" and parts[2] == "sbom-attestation":
                if not isinstance(body, dict):
                    raise PortalError(415, "json_body_required", "SBOM attestation endpoint requires application/json.")
                self._json(201, self.service.submit_sbom_attestation(parts[1], body))
            elif len(parts) == 3 and parts[0] == "scans" and parts[2] == "bind":
                self._json(200, self.service.evaluate_bind(parts[1]))
            elif len(parts) == 2 and parts[0] == "scans" and parts[1] == "scheduled":
                # BEAAA-4937 / v0.2 §5.1 — connector-callable; idempotent by (connector_id, schedule_window_start).
                if not isinstance(body, dict):
                    raise PortalError(415, "json_body_required", "Scheduled-scan endpoint requires application/json.")
                connector_id = self.headers.get("x-connector-id", "") or body.get("connector_id", "")
                if not connector_id:
                    raise PortalError(400, "connector_id_missing", "x-connector-id header or connector_id field required.")
                window = body.get("schedule_window_start", "")
                self._json(201, self.service.create_scheduled_scan(connector_id, window))
            else:
                raise PortalError(404, "not_found", "Route not found.")
        except Exception as err:
            self._handle_error(err)

    def do_PUT(self) -> None:
        try:
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            body = self._read_body()
            # BEAAA-4967: telemetry trigger PUT (deferred to module).
            import telemetry_v0_1 as _tel
            t_handled = _tel.handle_put(self.service, parts, body)
            if t_handled is not None:
                status, tbody, ctype = t_handled
                self._send(status, tbody, ctype)
                return
            if len(parts) == 3 and parts[0] == "dp" and parts[2] == "schedule":
                # BEAAA-4937 / v0.2 §5.1 — admin/Founder upsert of cadence + next_run_at.
                # BEAAA-4996: gate runs BEFORE body parse so no side effect on unauth.
                self._require_admin()
                if not isinstance(body, dict):
                    raise PortalError(415, "json_body_required", "Schedule endpoint requires application/json.")
                self._json(200, self.service.put_schedule(parts[1], body))
            else:
                raise PortalError(404, "not_found", "Route not found.")
        except Exception as err:
            self._handle_error(err)


def build_service(data_dir: Path | None = None) -> PortalService:
    if data_dir is None:
        data_dir = Path(os.environ.get("PORTAL_V1_DATA_DIR", Path(__file__).with_name(".portal_data")))
    return PortalService(PortalConfig(data_dir=data_dir))


def run_server(args: argparse.Namespace) -> None:
    service = build_service(Path(args.data_dir) if args.data_dir else None)
    PortalHandler.service = service
    server = ThreadingHTTPServer((args.host, args.port), PortalHandler)
    print(f"Portal v1 listening on http://{args.host}:{args.port}/admin", flush=True)
    try:
        server.serve_forever()
    finally:
        service.close()


def run_demo(args: argparse.Namespace) -> None:
    service = build_service(Path(args.data_dir) if args.data_dir else Path("/tmp/beaaa4722_portal_demo"))
    signing_key = ed25519.Ed25519PrivateKey.generate()
    signing_public_pem = signing_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")
    invite = service.create_invite("Demo Design Partner", "dp@example.com", "call-center")
    dp = service.accept_invite(invite["invite_token"], "https://staging.example.invalid/agent", "dp-cert-ref", "staging")
    enrollment = service.enroll_connector(
        {
            "invite_token": invite["invite_token"],
            "dp_org_name": "Demo Design Partner",
            "connector_version": "0.1.0",
            "ephemeral_public_key": {"alg": "x25519", "key": b64url(secrets.token_bytes(32))},
            "key_attestation": {"type": "software", "quote": None},
            "signing_public_key_pem": signing_public_pem,
        }
    )
    scan = service.trigger_scan(dp["dp_id"])
    while True:
        status, task = service.next_task(scan["scan_id"], enrollment["connector_id"])
        if status == 204:
            break
        envelope = {
            "envelope_version": ENVELOPE_VERSION,
            "scan_id": scan["scan_id"],
            "task_id": task["task_id"],
            "connector_version": "0.1.0",
            "redaction_policy_version": REDACTION_POLICY_VERSION,
            "timestamp": now_iso(),
            "endpoint_called": {"url_hash": sha256_hex("https://staging.example.invalid/agent"), "auth_method_hash": sha256_hex("mtls")},
            "assertions": {"response_received": True, "response_length_bucket": "<1k", "tool_called": None, "schema_match": "pass", "refusal_signal": False, "hallucination_signal": None},
            "redacted_excerpts": [],
            "signature_chain": [],
        }
        envelope["signature"] = b64url(signing_key.sign(canonical_json({k: v for k, v in envelope.items() if k not in {"signature", "signature_chain"}})))
        service.ingest_envelope(scan["scan_id"], envelope)
    print(json.dumps({"scan_id": scan["scan_id"], "audit_chain_ok": service.verify_audit_chain(), "data_dir": str(service.config.data_dir)}, indent=2))
    service.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8088)
    serve.add_argument("--data-dir")
    serve.set_defaults(func=run_server)
    demo = sub.add_parser("demo")
    demo.add_argument("--data-dir")
    demo.set_defaults(func=run_demo)
    args = parser.parse_args()
    args.func(args)
    return 0


# The portal predates package-style imports and its sibling modules intentionally
# use direct-script-compatible absolute imports. When loaded through
# ``tools.portal_v1``, expose this module under its historical name and make the
# sibling directory importable so both entry modes share one PortalService class.
sys.modules.setdefault("portal", sys.modules[__name__])
sys.modules.setdefault("tools.portal_v1.portal", sys.modules[__name__])
if __package__:
    portal_dir = str(Path(__file__).resolve().parent)
    if portal_dir not in sys.path:
        sys.path.insert(0, portal_dir)

# BEAAA-4937 / BEAAA-4908: bind v0.2 continuous-monitoring methods onto PortalService.
# Kept as a module-level attach() to keep portal.py reviewable and avoid surface drift.
from portal_v0_2 import attach as _attach_v0_2  # noqa: E402
_attach_v0_2(PortalService)

# BEAAA-4919 / BEAAA-4902: bind governance questionnaire v0.1 methods onto PortalService.
from questionnaire_service import attach as _attach_questionnaire  # noqa: E402
_attach_questionnaire(PortalService)

# BEAAA-4967 / BEAAA-4912: bind telemetry-api-v0 methods onto PortalService.
# Additive only; no v0.1/v0.2 wire reshape; no are-scanner-api-v1@v1.0 Output change.
from telemetry_v0_1 import attach as _attach_telemetry  # noqa: E402
_attach_telemetry(PortalService)

from post_bind_sinks_v0_1 import attach as _attach_post_bind_sinks  # noqa: E402
_attach_post_bind_sinks(PortalService)

# BEAAA-6165: bind policy-system claim auto-open MVP methods onto PortalService.
# Additive policy-system surface; no are-scanner-api-v1@v1.0 Output mutation.
from policy_auto_open_v0_1 import attach as _attach_policy_auto_open  # noqa: E402
_attach_policy_auto_open(PortalService)

# BEAAA-5016 / BEAAA-4915 -> BEAAA-5739: bind verify-api-v0.2 methods onto PortalService.
# Additive only; no scoring path, no rubric change, no are-scanner-api-v1@v1.0
# Output change. CMO packet boundary copy carried into verify_v0_2.
from verify_service import attach as _attach_verify  # noqa: E402
_attach_verify(PortalService)


if __name__ == "__main__":
    raise SystemExit(main())
