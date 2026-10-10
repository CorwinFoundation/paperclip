"""POST /scan + GET /scan/{scan_id} handlers — BEAAA-547 / ADR-0009 §5 #1.

Rule-change per `contracts/scanner-implementation-supervision-v1.md@v1.0`.
``cto_cosign_ref``: BEAAA-531 (CTO ACCEPT) + BEAAA-535 (HoUW countersign on
contract v1.1.0).

Framework-agnostic — handlers accept dicts + a tenant context and return
``(status, body)`` tuples. The FastAPI app in ``api/app.py`` is a thin adapter
matching the pattern in ``api/handlers.py``.

Hard Invariants (per BEAAA-533):

#1 — `bin/are-scan` + `harness/cli_runner.py` are NOT modified.
#2 — Offline `/score` path is byte-identical pre/post. The offline-path
     ``score_handler`` symbol from ``api.handlers`` is intentionally NOT
     re-bound here (only the tenant-isolation alias is reused).
#3 — IPC validator wrap verbatim (orchestrator handles this).
#4 — Credential vault read path is the orchestrator only; this module
     accepts the secret string from the request and passes it directly to
     ``vault.register`` then drops the local. No logging of the secret.
#5 — Tamper resistance preserved.

Δ4 incorporation
----------------
The orchestrator's ``ScanResult`` carries ``live_probe_variance_band`` for
the Underwriter pricing engine. The customer-facing response built by
``_customer_payload`` strips that field per CTO Δ4 disposition (see
BEAAA-533#7a9e206a). The UW-facing payload built by ``_uw_payload``
retains it. ``GET /scan/{scan_id}`` selects the payload variant via a
``?audience=uw`` query parameter (default = customer).
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit

# Verbatim T7 / BEAAA-546 alias — no fork, no copy.
from api.ci_scan_ext import (
    DISPOSITION_GATE_FAIL,
    DISPOSITION_GATE_PASS,
    DISPOSITION_INFRA_RETRY,
    ci_plugin_result,
    compute_probe_set_hash,
    gate_disposition,
    resolve_gate_threshold,
)
from api.config import ApiConfig
from api.evidence_receipt import project as project_evidence_receipt
from api.bundle import sign_manifest
from api.full_scan_executor import (
    FrameworkWorker,
    FullScanJobStore,
    dispatch_registry_attempt,
    dispatch_variable_form_attempt,
    registry_cases_from_rows,
)
from api.full_suite_manifest import FullSuiteCase, build_full_suite_manifest
from api.accepted_scan_store import AcceptedScanStore
from api.handlers import HandlerError, verify_tenant_isolation
from api import production_controls
from api.bundle import BundleStore, new_opaque_bundle_id
from api.scan_log import ScanLog
from api.sla import (
    Clock,
    DegradationDecision,
    RoutingEventSink,
    SlaRegistry,
    evaluate_degradation,
    pillars_from_dim_results,
)
from api.live_probe_context import serialize_live_probe_evidence
from harness import egress
from harness.context_envelope import (
    ContextEnvelopeError,
    ContextEnvelopeStore,
    render_interpretation_annotations,
    validate_context_envelope,
)
from harness.runner import RunnerError
from harness.customer_runner import (
    CustomerEndpointHTTPError,
    customer_agent_http_runner_factory,
)
from framework_assessment import (
    build_framework_envelope,
    enabled as framework_envelope_enabled,
)
from scanners.live_probe_orchestrator import (
    ALLOWED_K_REPLICATES,
    CorpusProvenanceMismatchError,
    CorpusSnapshotMismatchError,
    CredentialResolveError,
    DEFAULT_K_REPLICATES,
    InvalidKReplicatesError,
    OrchestratorError,
    ScanRequest,
    ScanResult,
)
from scanners.starter_pack import (
    STARTER_PACK_CORPUS_SNAPSHOT_HASH,
    STARTER_PACK_CORPUS_VERSION,
    STARTER_PACK_ID,
    STARTER_PACK_TEST_COUNT,
)
from scanners.starter_judge import starter_pack_id_for_rows, starter_score
from scanners.starter_pack_v2 import STARTER_PACK_V2_ID, STARTER_PACK_V2_TEST_COUNT
from scanners.starter_pack_v3 import STARTER_PACK_V3_ID, STARTER_PACK_V3_TEST_COUNT
from scanners.starter_pack_v4 import STARTER_PACK_V4_ID, STARTER_PACK_V4_TEST_COUNT
from scanners.starter_voice_pack import STARTER_VOICE_PACK_ID, STARTER_VOICE_TEST_COUNT

# New Starter intakes use variable @4 forms, including clients naming older
# packs. Historical results retain their original identities. The existing
# operator rollback flag can still select an explicitly requested legacy pack.
_STARTER_PACK_IDS = (STARTER_PACK_ID, STARTER_PACK_V2_ID, STARTER_PACK_V3_ID, STARTER_PACK_V4_ID, STARTER_VOICE_PACK_ID)
_STARTER_PACK_TEST_COUNTS = {
    STARTER_PACK_ID: STARTER_PACK_TEST_COUNT,
    STARTER_PACK_V2_ID: STARTER_PACK_V2_TEST_COUNT,
    STARTER_PACK_V3_ID: STARTER_PACK_V3_TEST_COUNT,
    STARTER_PACK_V4_ID: STARTER_PACK_V4_TEST_COUNT,
    STARTER_VOICE_PACK_ID: STARTER_VOICE_TEST_COUNT,
}


def _effective_starter_pack_id(pack_id: str) -> str:
    if pack_id in _STARTER_PACK_IDS and pack_id != STARTER_VOICE_PACK_ID and os.environ.get(
        "BACKBOND_STARTER_PACK_LEGACY_V1", ""
    ).strip() != "1":
        return STARTER_PACK_V4_ID
    return pack_id


def _starter_score_fields(rows: Any) -> dict[str, Any]:
    rows = [dict(row) for row in rows or ()]
    if not rows or not any("reason" in row for row in rows):
        return {}
    return {
        "starter_pack_id": starter_pack_id_for_rows(rows),
        "starter_score": starter_score(rows),
    }

LOGGER = logging.getLogger(__name__)
CONNECTIVITY_PROMPT = "Reply with exactly OK."


_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)")
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*$"
)


def _scrub_live_probe_evidence(
    records: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Redact common direct identifiers without retaining proprietary prompts."""

    def scrub(value: Any) -> Any:
        if isinstance(value, str):
            return _PHONE_RE.sub("<redacted-phone>", _EMAIL_RE.sub("<redacted-email>", value))
        if isinstance(value, Mapping):
            return {str(key): scrub(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [scrub(item) for item in value]
        return value

    return [scrub(serialize_live_probe_evidence(record)) for record in records]


def _append_report_only_manifest_evidence(
    score_envelope: dict[str, Any],
    manifest: Mapping[str, Any] | None,
    *,
    scan_id: str,
) -> None:
    """Project completed report-only attempts into the existing evidence list.

    This is an evidence projection only. It neither assigns a numeric weight nor
    changes the score envelope's field set.
    """
    if manifest is None:
        return
    refs = score_envelope.get("evidence_refs")
    if not isinstance(refs, list):
        refs = []
        score_envelope["evidence_refs"] = refs
    for row in manifest.get("evidence_rows", ()):
        if (
            not isinstance(row, Mapping)
            or row.get("disposition") != "report-only"
            or row.get("test_id") != "tau_support_crm"
        ):
            continue
        refs.append(
            {
                "scan_id": scan_id,
                "test_id": str(row.get("test_id") or ""),
                "case_id": str(row.get("probe_id") or ""),
                "rubric_point_id": str(row.get("rubric_point_id") or ""),
                "artifact_uri": row.get("evidence_ref"),
                "verdict": str(row.get("verdict") or "unresolved"),
                "weight": 0.0,
                "score_contribution": False,
                "disposition": "report-only",
            }
        )
_CRED_REF_SHAPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-:.]{0,127}$")
# Aligned with harness.cli_runner._AGENT_ID_RE (same regex; not imported to keep
# the api/ module self-contained per existing dependency direction).
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
_CUSTOMER_ENDPOINT_HTTP_RE = re.compile(
    r"^(?:customer endpoint (?:(?:http error: )?HTTP)|"
    r"syllable (?:start_session|send_message) HTTP) (?P<status>\d{3})(?::|$)",
    re.IGNORECASE,
)
_SYLLABLE_REQUEST_FAILED_RE = re.compile(
    r"^syllable (?:start_session|send_message) request failed:",
    re.IGNORECASE,
)
_SYLLABLE_SCAN_DEADLINE_RE = re.compile(
    r"^(?:syllable|customer endpoint) scan deadline exceeded$",
    re.IGNORECASE,
)

# BEAAA-1265 (WS1) — purpose values accepted on POST /scanner/v1/rescans.
# Carried via the request body's ``scanner_invocation_context.purpose`` field
# (already in the v1.0 input shape per harness.cli_runner). Behavior under
# each purpose is identical at WS1 (rule-execution: pipeline parity is the
# design intent); WS4 wires the SLA-degradation paths.
RESCAN_PURPOSES: frozenset[str] = frozenset({"pre_bind", "mid_term", "claim_review"})

# Fields the orchestrator's ScanResult carries that are UW-only (Δ4).
_UW_ONLY_FIELDS: frozenset[str] = frozenset({"live_probe_variance_band"})

# BEAAA-1273 (WS3) — `prior_scan_refs[]` pagination cap per ADR-0017 §7 Q2
# resolution. >100 returns the most-recent 100 (truncated from the head end,
# keeping the most-recent tail by ``run_timestamp``) + a non-breaking sentinel
# ``prior_scan_refs_truncated: true``. Separate
# ``GET /scanner/v1/agents/{agent_id}/scans`` history endpoint is deferred to
# v1.3.0. Carried to CTO at WS7 ratification as the §7 Q2 resolution.
PRIOR_SCAN_REFS_MAX: int = 100

# BEAAA-11609 / BEAAA-11631 — bounded lifetime for the compatibility
# in-process scan state used by GET /scan/{scan_id} and score-envelope factory
# readback. Durable rescan history remains owned by scan_log; this TTL only
# limits residual process-local request/result state.
DEFAULT_IN_MEMORY_SCAN_STATE_TTL_SECONDS: float = 15 * 60

# BEAAA-38869 — restart survival. A credential is never persisted, so an
# ``auth: none`` scan is re-run with this public marker (the no-auth adapter
# never sends it; the vault only requires a non-empty value).
RESUMED_NO_AUTH_SECRET: str = "no-auth-resumed-scan"
# A scan whose endpoint needs a credential cannot be re-run after a restart.
RESTART_CREDENTIAL_NOT_RETAINED_REASON: str = "scanner_restarted_credential_not_retained"
# A credential-free scan re-run this many times without finishing is failed.
MAX_RESTART_RESUMES: int = 3
RESTART_RESUME_EXHAUSTED_REASON: str = "scanner_restart_resume_exhausted"
# Retry tick for terminal webhooks whose delivery failed (durable mode only).
WEBHOOK_RETRY_INTERVAL_SECONDS: float = 60.0

# BEAAA-11726 item 1 — scan_tier feature flag + reproducibility defaults.
# ARE_SCAN_TIER=off forces full tier (Tier-1 rollback per ADR-0042 §Rollback).
SCAN_TIER_ENV: str = "ARE_SCAN_TIER"
# Fixed canonical seed (ADR-0042 §1 reproducibility guardrail — NOT random).
CANONICAL_SEED: int = 0
# Env var holding the currently-pinned corpus snapshot hash for smoke-tier
# defaulting. Must be set when smoke tier is enabled; 422 if absent.
PINNED_CORPUS_SNAPSHOT_HASH_ENV: str = "ARE_PINNED_CORPUS_SNAPSHOT_HASH"

# BEAAA-11726 item 2 — webhook delivery feature flag.
# ARE_SCAN_WEBHOOK=off disables delivery (Tier-1 rollback per ADR-0042 §Rollback).
SCAN_WEBHOOK_ENV: str = "ARE_SCAN_WEBHOOK"


def _exception_chain(exc: BaseException) -> Iterable[BaseException]:
    """Yield ``exc`` and its explicit/implicit causes without looping."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _customer_endpoint_handler_error(exc: RunnerError) -> HandlerError | None:
    """Return the API error for customer-supplied endpoint failures.

    Generic ``RunnerError`` is also used by internal SDK adapters, so only
    errors emitted by ``harness.customer_runner`` are translated out of the
    generic ``orchestrator_error`` bucket.
    """
    message = str(exc)

    http_match = _CUSTOMER_ENDPOINT_HTTP_RE.match(message)
    if http_match is not None:
        if http_match.group("status") == "404":
            return HandlerError(422, "customer_endpoint_route_not_found")
        return HandlerError(502, "customer_endpoint_http_error")

    syllable_request_failed = _SYLLABLE_REQUEST_FAILED_RE.match(message) is not None
    scan_deadline_exceeded = _SYLLABLE_SCAN_DEADLINE_RE.match(message) is not None
    if (
        not message.startswith("customer endpoint ")
        and not syllable_request_failed
        and not scan_deadline_exceeded
    ):
        return None

    lowered = message.lower()
    if scan_deadline_exceeded:
        return HandlerError(504, "customer_endpoint_unreachable_transport")

    if (
        message.startswith("customer endpoint timeout ")
        or (
            message.startswith("customer endpoint transport error:")
            and any(
                token in lowered
                for token in (
                    "timeout",
                    "timed out",
                    "readtimeout",
                    "connecttimeout",
                    "pooltimeout",
                )
            )
        )
    ):
        return HandlerError(504, "customer_endpoint_unreachable_timeout")

    if message.startswith("customer endpoint transport error:") and any(
        token in lowered
        for token in ("connection reset", "tls", "ssl")
    ):
        return HandlerError(504, "customer_endpoint_unreachable_transport")

    if syllable_request_failed:
        if any(
            token in lowered
            for token in (
                "522",
                "502",
                "503",
                "unexpected_eof",
                "eof occurred in violation of protocol",
                "connection reset",
                "tls",
                "ssl",
                "timeout",
                "timed out",
            )
        ):
            return HandlerError(504, "customer_endpoint_unreachable_transport")
        return HandlerError(422, "customer_endpoint_unreachable")

    return HandlerError(422, "customer_endpoint_unreachable")


def _handler_error_from_orchestrator_error(exc: OrchestratorError) -> HandlerError:
    """Preserve typed 4xx causes wrapped by an ``OrchestratorError``."""
    for chain_exc in _exception_chain(exc):
        if isinstance(chain_exc, CorpusSnapshotMismatchError):
            return HandlerError(422, "corpus_snapshot_mismatch")
        if isinstance(chain_exc, RunnerError):
            customer_error = _customer_endpoint_handler_error(chain_exc)
            if customer_error is not None:
                return customer_error
    message = str(exc)
    if message.startswith("starter_result_rows_count_mismatch:"):
        return HandlerError(500, "starter_result_rows_count_mismatch")
    if message.startswith("prober subprocess raised CorpusSnapshotMismatchError:"):
        return HandlerError(422, "corpus_snapshot_mismatch")
    if message.startswith("prober subprocess raised RunnerError:"):
        runner_message = message.removeprefix(
            "prober subprocess raised RunnerError:"
        ).strip()
        customer_error = _customer_endpoint_handler_error(RunnerError(runner_message))
        if customer_error is not None:
            return customer_error
    customer_error = _customer_endpoint_handler_error(RunnerError(message))
    if customer_error is not None:
        return customer_error
    return HandlerError(500, "orchestrator_error")


# ---------------------------------------------------------------------------
# Runtime — the API edge's dependency on the orchestrator + result store.
# ---------------------------------------------------------------------------


#: BEAAA-1273 (WS3) — type of the optional ``score_envelope_factory`` hook
#: that maps a finalized ``ScanResult`` into the score envelope persisted to
#: ``scan_log``. Production wiring binds this to the scoring engine output
#: (out of scope for WS3 — "no scoring path modification"); test wiring
#: supplies a stub that emits the minimum fields read back by
#: :func:`_chain_entry_from_row` (``are_total`` + ``band``). When the hook
#: is left ``None``, ``ScanRuntime.kickoff`` does NOT touch the scan_log —
#: legacy WS1/WS2 wiring is byte-identical.
ScoreEnvelopeFactory = Callable[[ScanResult, str, str], dict[str, Any]]
FullSuiteManifestUriFactory = Callable[[str | None, str], str]
ProductionSecurityFindingsSource = Callable[[], Iterable[dict[str, Any]]]
_NON_TERMINAL_INCOMPLETE_REASONS: frozenset[str] = frozenset(
    {"full_scan_registry_lease_busy"}
)
_NON_TERMINAL_STATUSES: frozenset[str] = frozenset({"queued", "running"})
_TERMINAL_FULL_SCAN_JOB_STATUSES: frozenset[str] = frozenset(
    {"complete", "incomplete", "incomplete_cost_cap", "failed", "cancelled"}
)


class ScanRuntime:
    """Pluggable orchestrator wiring used by the handler.

    Test setups instantiate this with a synchronous ``run_scan_fn`` so
    ``POST /scan`` returns 202 with an immediately-resolvable ``scan_id``.
    Production wiring (deferred to T-CLOSE) will spawn the Prober subprocess
    and the runtime will surface the result asynchronously.

    BEAAA-1273 (WS3) wires the optional ``scan_log`` /
    ``score_envelope_factory`` pair so the rescan response envelope can
    quote the chain (``prior_scan_refs[]``) and the current scan's
    forensic pins (``forensic_bundle_uri`` + ``snapshot_hash``) without a
    second round-trip. Leaving either ``None`` keeps the legacy /scan +
    /rescans 202 envelope byte-identical to WS1/WS2 — the additive fields
    are only emitted when the runtime has a persisted row to quote from
    (read-only chain query + write-time pin propagation per AC #8).
    """

    def __init__(
        self,
        *,
        run_scan_fn: Callable[[ScanRequest, str], ScanResult],
        prior_agent_ids: Iterable[str] = (),
        scan_log: ScanLog | None = None,
        scanner_run_tracker: Any | None = None,
        full_suite_registry_cases: Iterable[FullSuiteCase] = (),
        full_suite_manifest_uri_factory: FullSuiteManifestUriFactory | None = None,
        full_scan_registry_rows: Iterable[Mapping[str, Any]] = (),
        full_scan_workers: Mapping[str, FrameworkWorker] | None = None,
        full_scan_workers_factory: (
            Callable[[ScanRequest, str], Mapping[str, FrameworkWorker]] | None
        ) = None,
        full_scan_job_store: FullScanJobStore | None = None,
        full_scan_form_store: Any | None = None,
        webhook_outbox: "WebhookOutbox | None" = None,
        run_timestamp_factory: Callable[[], str] | None = None,
        forensic_bundle_uri_factory: Callable[[str, str], str] | None = None,
        score_envelope_factory: ScoreEnvelopeFactory | None = None,
        bundle_store: BundleStore | None = None,
        production_control_ledger: production_controls.ProductionControlLedger | None = None,
        production_security_findings_source: ProductionSecurityFindingsSource | None = None,
        in_memory_state_ttl_seconds: float = DEFAULT_IN_MEMORY_SCAN_STATE_TTL_SECONDS,
        monotonic_clock: Callable[[], float] | None = None,
        context_envelope_store: ContextEnvelopeStore | None = None,
        accepted_scan_store: AcceptedScanStore | None = None,
    ) -> None:
        if (
            isinstance(in_memory_state_ttl_seconds, bool)
            or not isinstance(in_memory_state_ttl_seconds, (int, float))
            or in_memory_state_ttl_seconds <= 0
        ):
            raise ValueError("in_memory_state_ttl_seconds must be positive")
        self._run_scan_fn = run_scan_fn
        self._in_memory_state_ttl_seconds = float(in_memory_state_ttl_seconds)
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._results: dict[str, ScanResult] = {}
        self._scan_state_created_at: dict[str, float] = {}
        # BEAAA-1265 (WS1) — minimal agent-scan-log shim. WS2 added the
        # durable scan_log keyed by ``(agent_id, run_timestamp)``; WS3 keeps
        # both sources truth-consistent: the in-memory set is populated from
        # ``prior_agent_ids`` (test bootstrap) AND from every successful
        # ``kickoff`` that names an ``agent_id``, while the scan_log
        # provides the ordered chain for ``prior_scan_refs[]``.
        #
        # BEAAA-38869: keyed ``(tenant_id, agent_id)`` — ``agent_id`` is
        # caller-supplied, so one tenant's scan must not satisfy another
        # tenant's rescan guard. ``prior_agent_ids`` is a test/bootstrap seed
        # with no tenant; those entries are stored under tenant ``None`` and
        # honoured for any tenant (production never seeds it — the live path
        # registers via ``kickoff`` with the request's tenant).
        prior_seen_at = self._now()
        self._agent_ids_with_prior_scan: dict[tuple[str | None, str], float] = {
            (None, str(agent_id)): prior_seen_at for agent_id in prior_agent_ids
        }
        # BEAAA-1273 (WS3) — persistence wiring + chain readback.
        self._scan_log = scan_log
        # BEAAA-6159 - additive Log Tracker v0 scanner-run write hook. Left
        # unbound by default so legacy /scans and /rescans response bytes are
        # unchanged unless production wiring explicitly opts in.
        self._scanner_run_tracker = scanner_run_tracker
        self._full_suite_registry_cases = tuple(full_suite_registry_cases)
        self._full_suite_manifest_uri_factory = (
            full_suite_manifest_uri_factory or _default_full_suite_manifest_uri
        )
        self._full_scan_registry_rows = tuple(dict(row) for row in full_scan_registry_rows)
        self._full_scan_workers = dict(full_scan_workers or {})
        self._full_scan_workers_factory = full_scan_workers_factory
        self._full_scan_job_store = full_scan_job_store or FullScanJobStore()
        self._full_scan_form_store = full_scan_form_store
        self._full_scan_job_ids: dict[str, str] = {}
        self._webhook_outbox = webhook_outbox
        self._webhook_cfgs: dict[str, dict[str, str]] = {}
        self._state_lock = threading.RLock()
        self._full_scan_semaphore = threading.BoundedSemaphore(value=1)
        self._background_threads: dict[str, threading.Thread] = {}
        self._run_timestamp_factory = run_timestamp_factory or _default_run_timestamp
        self._forensic_bundle_uri_factory = (
            forensic_bundle_uri_factory or _default_forensic_bundle_uri
        )
        self._score_envelope_factory = score_envelope_factory
        self._bundle_store = bundle_store
        self._production_control_ledger = production_control_ledger
        self._production_security_findings_source = production_security_findings_source
        self._context_envelope_store = context_envelope_store
        # BEAAA-38869 — durable accepted-scan journal. ``None`` keeps the
        # historical in-memory, synchronous behavior byte-identical.
        self._accepted_scan_store = accepted_scan_store
        self._accepted_scan_semaphore = threading.BoundedSemaphore(value=1)
        self._webhook_retry_thread: threading.Thread | None = None
        # Tracks the (tenant_id, agent_id, run_timestamp) of the current
        # kickoff so the handler can read the just-persisted row back without
        # a second scan_log scan (the row is keyed by that triple, not by
        # scan_id — BEAAA-38869 added tenant_id).
        self._kickoff_keys: dict[str, tuple[str, str, str]] = {}
        # BEAAA-2175 (PR-B) — per-scan registries so the
        # ``score_envelope_factory`` closure can recover the originating
        # ``ScanRequest`` (carries ``seed``, version pins, etc.) and the
        # rescan-specific ``scanner_invocation_context`` (carries
        # ``purpose``). Keeps the factory typedef
        # ``(ScanResult, agent_id, run_timestamp) → dict`` unchanged —
        # the factory looks up these per-scan inputs by ``scan_id`` via
        # the runtime reference it already closed over for ``prior_scan_refs``.
        self._scan_requests: dict[str, ScanRequest] = {}
        self._scan_invocation_contexts: dict[str, dict[str, Any]] = {}
        self.runtime_guard_flags = {
            "uses_in_memory_scan_state": True,
            "in_memory_scan_state_ttl_seconds": self._in_memory_state_ttl_seconds,
        }

    def _now(self) -> float:
        return float(self._monotonic_clock())

    def attach_webhook_outbox(self, webhook_outbox: "WebhookOutbox | None") -> None:
        """Attach the process-wide terminal webhook outbox."""
        self._webhook_outbox = webhook_outbox

    def bind_bundle_store(self, bundle_store: BundleStore) -> None:
        """Bind the API-owned evidence store before accepting live scans."""
        self._bundle_store = bundle_store

    def _purge_expired_in_memory_state(self) -> None:
        cutoff = self._now() - self._in_memory_state_ttl_seconds
        expired_scan_ids = [
            scan_id
            for scan_id, created_at in self._scan_state_created_at.items()
            if created_at < cutoff
            and (
                self._results.get(scan_id) is None
                or self._results[scan_id].status not in {"queued", "running"}
            )
        ]
        for scan_id in expired_scan_ids:
            self._scan_state_created_at.pop(scan_id, None)
            self._results.pop(scan_id, None)
            self._kickoff_keys.pop(scan_id, None)
            self._scan_requests.pop(scan_id, None)
            self._scan_invocation_contexts.pop(scan_id, None)

        expired_agent_ids = [
            agent_id
            for agent_id, seen_at in self._agent_ids_with_prior_scan.items()
            if seen_at < cutoff
        ]
        for agent_id in expired_agent_ids:
            self._agent_ids_with_prior_scan.pop(agent_id, None)

    def kickoff(
        self,
        scan_request: ScanRequest,
        secret: str,
        *,
        agent_id: str | None = None,
        scanner_invocation_context: dict[str, Any] | None = None,
        webhook_cfg: dict[str, str] | None = None,
        on_terminal: Callable[[], None] | None = None,
    ) -> ScanResult:
        """Run the scan now; record the result by scan_id.

        BEAAA-1273 (WS3) — when ``agent_id`` is supplied AND both
        ``scan_log`` and ``score_envelope_factory`` are configured, the
        runtime persists a finalized scan_log row in the same call
        (write-once ``forensic_bundle_uri`` + at-write-time
        ``snapshot_hash`` per ADR-0017 §2.2/§2.3 — implementation lives in
        WS2's ``ScanLog.append_scan`` which pins both fields in one
        ``BEGIN IMMEDIATE`` transaction). The ``agent_id`` is also
        recorded in the WS1 in-memory shim so a subsequent /rescans on
        the same id passes the ``has_prior_scan_for_agent`` 422 guard.
        """
        # BEAAA-2175 (PR-B) — populate per-scan registries before invoking the
        # run_scan_fn closure so scorer paths can read the request and
        # invocation context during kickoff. The envelope factory reads the
        # same registries after result construction.
        self._purge_expired_in_memory_state()
        created_at = self._now()
        self._scan_requests[scan_request.scan_id] = scan_request
        self._scan_state_created_at[scan_request.scan_id] = created_at
        if scanner_invocation_context is not None:
            self._scan_invocation_contexts[scan_request.scan_id] = dict(
                scanner_invocation_context
            )
        if webhook_cfg is not None:
            self._webhook_cfgs[scan_request.scan_id] = dict(webhook_cfg)

        security_findings = self._production_security_findings()

        # BEAAA-38869 — journal the accepted scan before any work starts so a
        # restart can finish it (or fail it explicitly) instead of losing it.
        if self._accepted_scan_store is not None:
            self._accepted_scan_store.accept(
                scan_request,
                agent_id=agent_id,
                invocation_context=scanner_invocation_context,
                webhook_cfg=webhook_cfg,
            )

        # A production full analysis is a registry job, not the historical
        # four-prompt scoring walker. Return the scan id immediately and run
        # the evidence-producing suite on a daemon worker.
        if self._uses_full_scan_registry(scan_request):
            queued = ScanResult(
                scan_id=scan_request.scan_id,
                status="queued",
                scanner_version=str(scan_request.scanner_version),
                corpus_version=str(scan_request.corpus_version),
                rows_emitted=0,
                k_replicates=scan_request.k_replicates,
            )
            with self._state_lock:
                self._results[scan_request.scan_id] = queued
            self._start_background_scan(
                self._run_full_scan_background,
                scan_request=scan_request,
                secret=secret,
                agent_id=agent_id,
                on_terminal=on_terminal,
            )
            return queued

        # BEAAA-38869 — with a durable journal the scan runs after the 202 so
        # the caller always learns the scan_id, even if a deploy restarts the
        # scanner before the scan finishes.
        if self._accepted_scan_store is not None:
            queued = ScanResult(
                scan_id=scan_request.scan_id,
                status="queued",
                scanner_version=str(scan_request.scanner_version),
                corpus_version=str(scan_request.corpus_version),
                rows_emitted=0,
                k_replicates=scan_request.k_replicates,
            )
            with self._state_lock:
                self._results[scan_request.scan_id] = queued
            self._start_background_scan(
                self._run_accepted_scan_background,
                scan_request=scan_request,
                secret=secret,
                agent_id=agent_id,
                on_terminal=on_terminal,
            )
            return queued

        try:
            result = self._run_scan_fn(scan_request, secret)
            result = self._apply_registry_dispatch_guard(
                scan_request, result, agent_id=agent_id, secret=secret
            )
            result = self._apply_full_suite_manifest_guard(
                scan_request, result, agent_id=agent_id
            )
            return self._record_completed_result(
                scan_request,
                result,
                agent_id=agent_id,
                production_security_findings=security_findings,
            )
        finally:
            if on_terminal is not None:
                on_terminal()

    def _uses_full_scan_registry(self, scan_request: ScanRequest) -> bool:
        return bool(
            self._full_scan_registry_rows
            and self._full_scan_workers_factory is not None
            and scan_request.scan_tier not in {"smoke", "dev"}
        )

    def _start_background_scan(
        self,
        target: Callable[..., None],
        *,
        scan_request: ScanRequest,
        secret: str,
        agent_id: str | None,
        on_terminal: Callable[[], None] | None,
    ) -> threading.Thread:
        thread = threading.Thread(
            target=target,
            kwargs={
                "scan_request": scan_request,
                "secret": secret,
                "agent_id": agent_id,
                "on_terminal": on_terminal,
            },
            name=f"are-full-scan-{scan_request.scan_id[:8]}",
            daemon=True,
        )
        self._background_threads[scan_request.scan_id] = thread
        try:
            thread.start()
        except BaseException:
            self._background_threads.pop(scan_request.scan_id, None)
            if on_terminal is not None:
                on_terminal()
            raise
        return thread

    def _run_accepted_scan_background(
        self,
        *,
        scan_request: ScanRequest,
        secret: str,
        agent_id: str | None,
        on_terminal: Callable[[], None] | None = None,
    ) -> None:
        """Durable-mode worker for scans that are not registry jobs.

        Runs the same pipeline the synchronous ``kickoff`` path runs; only
        the thread differs. Failures become an explicit terminal result.
        """
        try:
            with self._accepted_scan_semaphore:
                running = ScanResult(
                    scan_id=scan_request.scan_id,
                    status="running",
                    scanner_version=str(scan_request.scanner_version),
                    corpus_version=str(scan_request.corpus_version),
                    rows_emitted=0,
                    k_replicates=scan_request.k_replicates,
                )
                with self._state_lock:
                    self._results[scan_request.scan_id] = running
                if self._accepted_scan_store is not None:
                    self._accepted_scan_store.mark_running(scan_request.scan_id)
                LOGGER.info("accepted_scan_running scan_id=%s", scan_request.scan_id)
                callback = None
                if self._accepted_scan_store is not None:
                    callback = lambda **event: self._accepted_scan_store.record_case_progress(
                        scan_request.scan_id, **event
                    )
                    object.__setattr__(scan_request, "case_progress_callback", callback)
                try:
                    result = self._run_scan_fn(scan_request, secret)
                finally:
                    if callback is not None:
                        object.__delattr__(scan_request, "case_progress_callback")
                result = self._apply_registry_dispatch_guard(
                    scan_request, result, agent_id=agent_id, secret=secret
                )
                result = self._apply_full_suite_manifest_guard(
                    scan_request, result, agent_id=agent_id
                )
                self._record_completed_result(
                    scan_request,
                    result,
                    agent_id=agent_id,
                    production_security_findings=self._production_security_findings(),
                )
                LOGGER.info(
                    "accepted_scan_terminal scan_id=%s status=%s",
                    scan_request.scan_id,
                    result.status,
                )
        except Exception as exc:  # never expose credential or response bodies
            reason = (
                _handler_error_from_orchestrator_error(exc).message
                if isinstance(exc, OrchestratorError)
                else type(exc).__name__
            )
            LOGGER.error(
                "accepted_scan_worker_error scan_id=%s error_type=%s",
                scan_request.scan_id,
                type(exc).__name__,
            )
            self._record_failed_result(
                scan_request, reason=f"scan_worker_error:{reason}"
            )
        finally:
            self._background_threads.pop(scan_request.scan_id, None)
            if on_terminal is not None:
                on_terminal()

    def _record_failed_result(self, scan_request: ScanRequest, *, reason: str) -> ScanResult:
        failed = ScanResult(
            scan_id=scan_request.scan_id,
            status="failed",
            scanner_version=str(scan_request.scanner_version),
            corpus_version=str(scan_request.corpus_version),
            rows_emitted=0,
            k_replicates=scan_request.k_replicates,
            incomplete=True,
            incomplete_reason=reason,
        )
        with self._state_lock:
            self._results[scan_request.scan_id] = failed
            self._scan_state_created_at[scan_request.scan_id] = self._now()
        if self._accepted_scan_store is not None:
            self._accepted_scan_store.mark_terminal(scan_request.scan_id, failed)
            self._maybe_deliver_terminal_webhook(scan_request.scan_id, failed)
        return failed

    def _run_full_scan_background(
        self,
        *,
        scan_request: ScanRequest,
        secret: str,
        agent_id: str | None,
        on_terminal: Callable[[], None] | None = None,
    ) -> None:
        try:
            with self._full_scan_semaphore:
                base = ScanResult(
                    scan_id=scan_request.scan_id,
                    status="running",
                    scanner_version=str(scan_request.scanner_version),
                    corpus_version=str(scan_request.corpus_version),
                    rows_emitted=0,
                    k_replicates=scan_request.k_replicates,
                )
                with self._state_lock:
                    self._results[scan_request.scan_id] = base
                if self._accepted_scan_store is not None:
                    self._accepted_scan_store.mark_running(scan_request.scan_id)
                result = self._apply_registry_dispatch_guard(
                    scan_request,
                    base,
                    agent_id=agent_id,
                    secret=secret,
                )
                self._record_completed_result(
                    scan_request,
                    result,
                    agent_id=agent_id,
                    production_security_findings=self._production_security_findings(),
                )
        except Exception as exc:  # never expose credential or response bodies
            LOGGER.error(
                "full_scan_background_worker_error scan_id=%s error_type=%s",
                scan_request.scan_id,
                type(exc).__name__,
            )
            self._record_failed_result(
                scan_request,
                reason=f"full_scan_worker_error:{type(exc).__name__}",
            )
        finally:
            self._background_threads.pop(scan_request.scan_id, None)
            if on_terminal is not None:
                on_terminal()

    def _record_completed_result(
        self,
        scan_request: ScanRequest,
        result: ScanResult,
        *,
        agent_id: str | None,
        production_security_findings: dict[str, Any] | None = None,
    ) -> ScanResult:
        if (
            scan_request.include_result_rows
            and scan_request.pack_id in _STARTER_PACK_IDS
            and _scan_reached_terminal(result)
            and result.status != "failed"
            and len(result.starter_result_rows) != result.rows_emitted
        ):
            raise OrchestratorError(
                "starter_result_rows_count_mismatch:"
                f"expected={result.rows_emitted}:actual={len(result.starter_result_rows)}"
            )
        with self._state_lock:
            self._results[scan_request.scan_id] = result
            self._scan_state_created_at[scan_request.scan_id] = self._now()
        if agent_id is not None:
            self._agent_ids_with_prior_scan[
                (scan_request.tenant_id, agent_id)
            ] = self._now()
            if (
                self._scan_log is not None
                and self._score_envelope_factory is not None
            ):
                run_timestamp = self._run_timestamp_factory()
                score_envelope = self._score_envelope_factory(
                    result, agent_id, run_timestamp
                )
                _append_report_only_manifest_evidence(
                    score_envelope,
                    result.full_suite_manifest,
                    scan_id=scan_request.scan_id,
                )
                forensic_bundle_uri = self._forensic_bundle_uri_factory(
                    agent_id, scan_request.scan_id
                )
                if self._bundle_store is not None and result.evidence_records:
                    bundle_id = new_opaque_bundle_id()
                    forensic_bundle_uri = f"/forensic_bundle/{bundle_id}"
                    evidence = _scrub_live_probe_evidence(result.evidence_records)
                    self._bundle_store.build(
                        bundle_id=bundle_id,
                        tenant_id=scan_request.tenant_id,
                        snapshot_hash=scan_request.corpus_snapshot_hash,
                        evidence=evidence,
                        scan_request={
                            "scan_id": scan_request.scan_id,
                            "tenant_id": scan_request.tenant_id,
                            "credential_ref": scan_request.credential_ref,
                            "scanner_version": scan_request.scanner_version,
                            "corpus_version": scan_request.corpus_version,
                            "seed": scan_request.seed,
                        },
                        prober_version=result.scanner_version,
                        target_endpoint_url=scan_request.agent_endpoint,
                    )
                    if (
                        self._bundle_store.get(
                            bundle_id, tenant_id=scan_request.tenant_id
                        )
                        is None
                    ):
                        raise OrchestratorError(
                            "live_probe_evidence_readback_failed"
                        )
                    for ref in score_envelope.get("evidence_refs", []):
                        if isinstance(ref, dict):
                            ref["artifact_uri"] = forensic_bundle_uri
                # BEAAA-11726 item 4 — compute probe_set_hash from the
                # dim_ids that ran and persist it write-once with the row.
                _psh = (
                    compute_probe_set_hash([d.dim_id for d in result.dim_results])
                    if result.dim_results
                    else str(result.full_suite_manifest.get("manifest_hash"))
                    if result.full_suite_manifest is not None
                    else None
                )
                self._scan_log.append_scan(
                    tenant_id=scan_request.tenant_id,
                    agent_id=agent_id,
                    run_timestamp=run_timestamp,
                    scan_id=scan_request.scan_id,
                    scanner_version=result.scanner_version,
                    corpus_version=result.corpus_version,
                    score_json=score_envelope,
                    forensic_bundle_uri=forensic_bundle_uri,
                    probe_set_hash=_psh,
                )
                self._kickoff_keys[scan_request.scan_id] = (
                    scan_request.tenant_id,
                    agent_id,
                    run_timestamp,
                )
                if self._scanner_run_tracker is not None:
                    self._scanner_run_tracker.emit_scanner_run_completed(
                        agent_id=agent_id,
                        scan_id=scan_request.scan_id,
                        run_timestamp=run_timestamp,
                        score_json=score_envelope,
                    )
        self._append_production_control_result_record(
            scan_request,
            result,
            agent_id=agent_id,
            production_security_findings=production_security_findings,
        )
        # Keep the durable progress projection useful even when the worker
        # reaches terminal state before the next progress poll.
        if self._accepted_scan_store is not None:
            self._accepted_scan_store.note_activity(
                scan_request.scan_id,
                completed_test_count=max(0, int(result.rows_emitted or 0)),
            )
        if self._accepted_scan_store is not None and _scan_reached_terminal(result):
            self._accepted_scan_store.mark_terminal(
                scan_request.scan_id,
                result,
                kickoff_key=self._kickoff_keys.get(scan_request.scan_id),
            )
        self._maybe_deliver_terminal_webhook(scan_request.scan_id, result)
        return result

    def _maybe_deliver_terminal_webhook(
        self, scan_id: str, result: ScanResult
    ) -> None:
        if self._webhook_outbox is None:
            return
        webhook_cfg = self._webhook_cfgs.get(scan_id)
        if webhook_cfg is None or not _scan_reached_terminal(result):
            return
        self._webhook_cfgs.pop(scan_id, None)
        _deliver_terminal_webhook(
            outbox=self._webhook_outbox,
            webhook_cfg=webhook_cfg,
            scan_id=scan_id,
            payload=_customer_result_response(scan_id, result, self),
        )

    def _production_security_findings(self) -> dict[str, Any] | None:
        if self._production_security_findings_source is None:
            return None
        findings = self._production_security_findings_source()
        result = production_controls.security_findings_result(findings)
        if not result["pass"]:
            blocking_ids = [
                str(item.get("id", "unknown"))
                for item in result.get("blocking_findings", [])
            ]
            raise OrchestratorError(
                "production_control_security_findings_blocked:"
                + ",".join(blocking_ids)
            )
        return result

    def _append_production_control_result_record(
        self,
        scan_request: ScanRequest,
        result: ScanResult,
        *,
        agent_id: str | None,
        production_security_findings: dict[str, Any] | None,
    ) -> None:
        if self._production_control_ledger is None:
            return
        evidence_ref = _production_control_evidence_ref(result, scan_request.scan_id)
        self._production_control_ledger.append(
            {
                "event_type": "result_record",
                "design_partner_ref": scan_request.vendor_id
                or agent_id
                or scan_request.agent_id
                or scan_request.tenant_id,
                "scan_id": scan_request.scan_id,
                "evidence_ref": evidence_ref,
                "payload": {
                    "scanner_version": result.scanner_version,
                    "corpus_version": result.corpus_version,
                    "seed": scan_request.seed,
                    "status": result.status,
                    "rows_emitted": result.rows_emitted,
                    "security_findings_pass": (
                        production_security_findings.get("pass")
                        if production_security_findings is not None
                        else None
                    ),
                },
            }
        )

    def _apply_registry_dispatch_guard(
        self,
        scan_request: ScanRequest,
        result: ScanResult,
        *,
        agent_id: str | None,
        secret: str = "",
    ) -> ScanResult:
        """Dispatch active executable registry rows for full underwriting scans.

        This is the BEAAA-13763 bridge from the standard API launch path to
        the async full-scan attempt ledger. The legacy four-prompt walker may
        still run first, but it cannot make a full-tier scan complete unless
        the registry dispatcher also returns complete terminal evidence for
        every active executable registry case.
        """
        if not self._full_scan_registry_rows:
            return result
        if scan_request.scan_tier in {"smoke", "dev"}:
            return result

        workers = self._full_scan_workers
        if self._full_scan_workers_factory is not None:
            workers = dict(
                self._full_scan_workers_factory(scan_request, secret)
            )

        first_worker = next(iter(workers.values()), None)
        metadata_factory = getattr(first_worker, "manifest_metadata", None)
        worker_metadata = (
            dict(metadata_factory()) if callable(metadata_factory) else {}
        )
        model_versions: dict[str, Any] = {
            "primary": str(
                worker_metadata.get("target_model") or "vendor-target:unversioned"
            ),
            "judges": list(worker_metadata.get("judge_models") or []),
        }
        if worker_metadata.get("execution_budget"):
            model_versions["execution_budget"] = worker_metadata["execution_budget"]
        variable_form_pack = scan_request.pack_id == "full-scan-conv-en@1.0"
        registry_cases = (
            ()
            if variable_form_pack
            else registry_cases_from_rows(self._full_scan_registry_rows)
        )
        webhook_cfg = self._webhook_cfgs.get(scan_request.scan_id)
        job = self._full_scan_job_store.submit(
            agent_id=agent_id or scan_request.agent_id or scan_request.tenant_id,
            scanner_version=str(scan_request.scanner_version),
            corpus_version=str(scan_request.corpus_version),
            model_versions=model_versions,
            seed=scan_request.seed,
            registry_cases=registry_cases,
            scan_id=scan_request.scan_id,
            idempotency_key=f"{scan_request.tenant_id}:{scan_request.scan_id}:full-registry",
            terminal_webhook=(
                {
                    "webhook_cfg": dict(webhook_cfg),
                    "response_base": {
                        "scan_id": scan_request.scan_id,
                        "status": "accepted",
                        "partial_completion": False,
                        "confidence_below_adjudication_threshold": False,
                    },
                }
                if webhook_cfg is not None
                else None
            ),
            tenant_id=scan_request.tenant_id,
            pack_id=(
                scan_request.pack_id if variable_form_pack else None
            ),
            form_store=(
                self._full_scan_form_store if variable_form_pack else None
            ),
            form_bank_version=(
                "full-scan-conv-en@1.0" if variable_form_pack else None
            ),
        )
        self._full_scan_job_ids[scan_request.scan_id] = job.job_id
        if self._full_scan_workers_factory is None:
            return replace(
                result,
                status="accepted",
                rows_emitted=0,
                full_suite_manifest=None,
                incomplete=False,
                incomplete_reason=None,
            )
        if job.status in _TERMINAL_FULL_SCAN_JOB_STATUSES:
            # BEAAA-38869 — a resumed scan whose durable job already finished
            # before the restart: reuse the terminal job, do not re-lease it.
            return self._full_scan_job_result(result, job, first_worker)
        case_concurrency = (
            1
            if scan_request.pack_id == "starter-voice-50@1"
            else 5
            if job.pack_id
            else 3
            if job.tenant_id
            else 8
        )
        lease = self._full_scan_job_store.lease(job.job_id)
        attempt_id = lease.get("attempt_id") or lease.get("active_attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            finalized = self._full_scan_job_store.finalize(job.job_id)
        else:
            if job.form_id:
                dispatch_variable_form_attempt(
                    self._full_scan_job_store,
                    job.job_id,
                    attempt_id=attempt_id,
                    form_store=self._full_scan_form_store,
                    workers=workers,
                    max_workers=case_concurrency,
                )
            else:
                dispatch_registry_attempt(
                    self._full_scan_job_store,
                    job.job_id,
                    attempt_id=attempt_id,
                    registry_rows=self._full_scan_registry_rows,
                    workers=workers,
                    max_workers=case_concurrency,
                )
            finalized = self._full_scan_job_store.finalize(job.job_id)
        return self._full_scan_job_result(result, finalized, first_worker)

    def _full_scan_job_result(
        self, result: ScanResult, finalized: Any, first_worker: Any
    ) -> ScanResult:
        manifest = finalized.final_manifest
        if manifest is not None:
            manifest_writer = getattr(first_worker, "write_manifest", None)
            if callable(manifest_writer):
                persisted_ref = manifest_writer(manifest)
                if persisted_ref != manifest.get("manifest_evidence_ref"):
                    raise RuntimeError(
                        "persisted full-scan manifest reference mismatch"
                    )
        if finalized.status == "complete":
            return replace(
                result,
                status="complete",
                rows_emitted=len(finalized.evidence_rows),
                full_suite_manifest=manifest,
            )
        return replace(
            result,
            status="incomplete",
            rows_emitted=len(finalized.evidence_rows),
            incomplete=True,
            incomplete_reason="full_scan_registry_incomplete",
            full_suite_manifest=manifest,
        )

    def _apply_full_suite_manifest_guard(
        self,
        scan_request: ScanRequest,
        result: ScanResult,
        *,
        agent_id: str | None,
    ) -> ScanResult:
        """Attach the full-suite manifest and fail closed for full scans.

        Smoke/dev tiers are explicitly non-underwriting and may sample. Full
        underwriting scans must carry every active registry case to completion
        or return an incomplete result with evidence references in the
        manifest.
        """
        if self._full_scan_registry_rows:
            return result
        if not self._full_suite_registry_cases:
            return result
        if scan_request.scan_tier in {"smoke", "dev"}:
            return result

        manifest_uri = self._full_suite_manifest_uri_factory(
            agent_id or scan_request.agent_id,
            scan_request.scan_id,
        )
        manifest, findings = build_full_suite_manifest(
            scan_request=scan_request,
            scan_result=result,
            registry_cases=self._full_suite_registry_cases,
            signer=sign_manifest,
            manifest_uri=manifest_uri,
        )
        if not findings:
            return replace(result, full_suite_manifest=manifest)
        return replace(
            result,
            status="incomplete",
            incomplete=True,
            incomplete_reason="full_suite_manifest_incomplete",
            full_suite_manifest=manifest,
        )

    def fetch(self, scan_id: str) -> ScanResult | None:
        self._purge_expired_in_memory_state()
        result = self._results.get(scan_id)
        if result is None:
            result = self._load_durable_scan(scan_id)
        job_id = self._full_scan_job_ids.get(scan_id)
        if result is not None and job_id is not None:
            job = self._full_scan_job_store.status(job_id)
            if job.status in _TERMINAL_FULL_SCAN_JOB_STATUSES:
                refreshed = replace(
                    result,
                    status=job.status,
                    rows_emitted=len(job.evidence_rows),
                    incomplete=job.status != "complete",
                    incomplete_reason=(
                        None
                        if job.status == "complete"
                        else "full_scan_registry_incomplete"
                    ),
                    full_suite_manifest=job.final_manifest,
                )
                with self._state_lock:
                    self._results[scan_id] = refreshed
                    self._scan_state_created_at[scan_id] = self._now()
                return refreshed
        return result

    def progress(self, scan_id: str, *, tenant_id: str) -> dict[str, Any] | None:
        try:
            return self._full_scan_job_store.progress_for_scan(scan_id, tenant_id=tenant_id)
        except KeyError:
            if self._accepted_scan_store is None:
                return None
            try:
                return self._accepted_scan_store.case_progress(scan_id, tenant_id=tenant_id)
            except KeyError:
                return None

    # ----- BEAAA-38869 durable accepted scans -----
    def webhook_outbox_store(self) -> Any | None:
        """Durable outbox store sharing the accepted-scan DB, or ``None``."""
        if self._accepted_scan_store is None:
            return None
        return self._accepted_scan_store.outbox_store()

    def accepted_scan_progress(self, scan_id: str) -> dict[str, Any]:
        if self._accepted_scan_store is None:
            return {"last_activity_at": None, "completed_test_count": 0}
        row = self._accepted_scan_store.get(scan_id)
        if row is None:
            return {"last_activity_at": None, "completed_test_count": 0}
        return {
            "last_activity_at": row["last_activity_at"],
            "completed_test_count": row["completed_test_count"],
        }

    def _restore_scan_state(self, row: Mapping[str, Any]) -> None:
        scan_id = row["scan_id"]
        with self._state_lock:
            self._scan_requests.setdefault(scan_id, row["request"])
            if row.get("invocation_context") is not None:
                self._scan_invocation_contexts.setdefault(
                    scan_id, dict(row["invocation_context"])
                )
            if row.get("kickoff_key"):
                self._kickoff_keys.setdefault(scan_id, tuple(row["kickoff_key"]))
            self._scan_state_created_at[scan_id] = self._now()

    def _load_durable_scan(self, scan_id: str) -> ScanResult | None:
        """Re-hydrate a scan the in-memory TTL (or a restart) dropped."""
        if self._accepted_scan_store is None:
            return None
        row = self._accepted_scan_store.get(scan_id)
        if row is None:
            return None
        self._restore_scan_state(row)
        result = row["result"]
        if result is None:
            request = row["request"]
            result = ScanResult(
                scan_id=scan_id,
                status="running" if row["state"] == "running" else "queued",
                scanner_version=str(request.scanner_version),
                corpus_version=str(request.corpus_version),
                rows_emitted=0,
                k_replicates=request.k_replicates,
            )
            return result
        with self._state_lock:
            self._results[scan_id] = result
        return result

    def resume_accepted_scans(self, *, start_webhook_retry: bool = True) -> dict[str, list[str]]:
        """Startup sweep: finish every scan a previous process accepted.

        * A non-terminal scan against an ``auth: none`` endpoint is re-run
          (bounded by ``MAX_RESTART_RESUMES``).
        * A non-terminal scan whose endpoint needs a credential cannot be
          re-run (the credential was never persisted); it is finished with an
          explicit ``failed`` result so the caller's webhook still fires.
        * A terminal scan whose webhook was never enqueued (the process died
          between the result and the enqueue) is enqueued now. The outbox is
          keyed by ``scan_id``, so a delivered webhook is never re-sent.
        """
        summary: dict[str, list[str]] = {"resumed": [], "failed": [], "redelivered": []}
        store = self._accepted_scan_store
        if store is None:
            return summary
        store.purge_expired()
        self._full_scan_job_store.recover()
        for row in store.unfinished():
            scan_id = row["scan_id"]
            request = row["request"]
            self._restore_scan_state(row)
            if row.get("webhook_cfg") is not None:
                self._webhook_cfgs[scan_id] = dict(row["webhook_cfg"])
            if not row["resumable"]:
                LOGGER.warning(
                    "accepted_scan_restart_failed scan_id=%s reason=%s",
                    scan_id,
                    RESTART_CREDENTIAL_NOT_RETAINED_REASON,
                )
                self._record_failed_result(
                    request, reason=RESTART_CREDENTIAL_NOT_RETAINED_REASON
                )
                summary["failed"].append(scan_id)
                continue
            if store.note_resume(scan_id) > MAX_RESTART_RESUMES:
                LOGGER.warning(
                    "accepted_scan_restart_failed scan_id=%s reason=%s",
                    scan_id,
                    RESTART_RESUME_EXHAUSTED_REASON,
                )
                self._record_failed_result(request, reason=RESTART_RESUME_EXHAUSTED_REASON)
                summary["failed"].append(scan_id)
                continue
            LOGGER.info("accepted_scan_resumed scan_id=%s", scan_id)
            with self._state_lock:
                self._results[scan_id] = ScanResult(
                    scan_id=scan_id,
                    status="queued",
                    scanner_version=str(request.scanner_version),
                    corpus_version=str(request.corpus_version),
                    rows_emitted=0,
                    k_replicates=request.k_replicates,
                )
            target = (
                self._run_full_scan_background
                if self._uses_full_scan_registry(request)
                else self._run_accepted_scan_background
            )
            self._start_background_scan(
                target,
                scan_request=request,
                secret=RESUMED_NO_AUTH_SECRET,
                agent_id=row.get("agent_id"),
                on_terminal=None,
            )
            summary["resumed"].append(scan_id)
        if self._webhook_outbox is not None:
            for row in store.terminal_with_webhook():
                scan_id = row["scan_id"]
                if self._webhook_outbox.get(scan_id) is not None:
                    continue
                self._restore_scan_state(row)
                _deliver_terminal_webhook(
                    outbox=self._webhook_outbox,
                    webhook_cfg=row["webhook_cfg"],
                    scan_id=scan_id,
                    payload=_customer_result_response(scan_id, row["result"], self),
                )
                summary["redelivered"].append(scan_id)
            if start_webhook_retry:
                self._start_webhook_retry_loop()
        LOGGER.info(
            "accepted_scan_restart_sweep resumed=%d failed=%d redelivered=%d",
            len(summary["resumed"]),
            len(summary["failed"]),
            len(summary["redelivered"]),
        )
        return summary

    def _start_webhook_retry_loop(self) -> None:
        if self._webhook_retry_thread is not None or self._webhook_outbox is None:
            return
        outbox = self._webhook_outbox

        def loop() -> None:
            while True:
                time.sleep(WEBHOOK_RETRY_INTERVAL_SECONDS)
                try:
                    outbox.deliver_pending()
                except Exception as exc:  # noqa: BLE001 - keep retrying
                    LOGGER.error("webhook_retry_error error_type=%s", type(exc).__name__)

        thread = threading.Thread(target=loop, name="are-webhook-retry", daemon=True)
        self._webhook_retry_thread = thread
        thread.start()

    def persist_context_envelope(self, scan_id: str, raw_context: Mapping[str, Any]) -> None:
        """Validate and persist the report-only sidecar after execution."""
        if self._context_envelope_store is None:
            raise ContextEnvelopeError("context envelope store is not configured")
        self._context_envelope_store.put(scan_id, validate_context_envelope(raw_context))

    def context_report(self, scan_id: str) -> dict[str, Any] | None:
        if self._context_envelope_store is None:
            return None
        saved = self._context_envelope_store.get(scan_id)
        if saved is None:
            return None
        return {
            "scan_id": scan_id,
            "mode": "report_only_shadow",
            "shadow_recommendation": saved["shadow_recommendation"],
            "interpretation_annotations": render_interpretation_annotations(saved["envelope"]),
        }

    # ----- BEAAA-1265 (WS1) agent-scan-log shim -----
    def register_prior_scan(
        self, agent_id: str, *, tenant_id: str | None = None
    ) -> None:
        """Record that a prior /scans row exists for ``agent_id``.

        WS2 added the durable scan_log; WS3 keeps the in-memory set
        co-populated by ``kickoff`` so the 422 ``agent_id_no_prior_scan``
        guard works whether the call site registered the agent
        explicitly (test bootstrap) or implicitly (live scan path).

        BEAAA-38869: pass ``tenant_id`` to scope the registration; omitting it
        is a tenant-less bootstrap seed honoured for any tenant (tests only).
        """
        self._purge_expired_in_memory_state()
        self._agent_ids_with_prior_scan[(tenant_id, agent_id)] = self._now()

    def has_prior_scan_for_agent(self, agent_id: str, *, tenant_id: str) -> bool:
        """Whether ``tenant_id`` has a prior scan of ``agent_id``.

        BEAAA-38869: tenant-scoped — another tenant's scan of the same
        caller-supplied ``agent_id`` does not count.
        """
        self._purge_expired_in_memory_state()
        if self._scan_log is not None and self._scan_log.list_for_agent(
            agent_id, tenant_id=tenant_id
        ):
            return True
        return (
            (tenant_id, agent_id) in self._agent_ids_with_prior_scan
            or (None, agent_id) in self._agent_ids_with_prior_scan
        )

    # ----- BEAAA-2175 (PR-B) per-scan registry readback -----
    def current_scan_request(self, scan_id: str) -> ScanRequest | None:
        """Return the ``ScanRequest`` that initiated the kickoff for
        ``scan_id``, or ``None`` if no kickoff has run for that id yet.
        The envelope factory closure reads this to recover ``seed`` and
        the request-side version pins for the envelope projection without
        a factory-typedef change.
        """
        self._purge_expired_in_memory_state()
        request = self._scan_requests.get(scan_id)
        if request is None and self._load_durable_scan(scan_id) is not None:
            request = self._scan_requests.get(scan_id)
        return request

    def current_scan_invocation_context(
        self, scan_id: str
    ) -> dict[str, Any] | None:
        """Return the ``scanner_invocation_context`` supplied to
        ``kickoff`` for ``scan_id`` (carries ``purpose``), or ``None`` if
        the caller did not supply one (e.g., legacy ``/scans`` calls — the
        ``purpose`` field is only meaningful on the rescan path).
        """
        self._purge_expired_in_memory_state()
        if scan_id not in self._scan_requests:
            self._load_durable_scan(scan_id)
        return self._scan_invocation_contexts.get(scan_id)

    # ----- BEAAA-1273 (WS3) chain readback -----
    def current_kickoff_row(self, scan_id: str) -> dict[str, Any] | None:
        """Return the scan_log row written by the kickoff that produced
        ``scan_id``, or ``None`` if persistence was not wired for that
        call (no ``scan_log``, no ``score_envelope_factory``, or no
        ``agent_id``).
        """
        self._purge_expired_in_memory_state()
        if scan_id not in self._kickoff_keys:
            self._load_durable_scan(scan_id)
        if self._scan_log is None or scan_id not in self._kickoff_keys:
            return None
        tenant_id, agent_id, run_timestamp = self._kickoff_keys[scan_id]
        return self._scan_log.get(agent_id, run_timestamp, tenant_id=tenant_id)

    def prior_scan_rows(
        self,
        agent_id: str,
        *,
        tenant_id: str,
        exclude_scan_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return ``tenant_id``'s scan_log rows for ``agent_id`` ordered asc
        by ``run_timestamp``, excluding the row whose ``scan_id`` matches
        ``exclude_scan_id`` (i.e., the current rescan is NOT in its own
        ``prior_scan_refs[]`` — per ADR-0017 §2.1 + AC #4).

        BEAAA-38869: the chain is scoped to the caller's tenant.
        """
        if self._scan_log is None:
            return []
        rows = self._scan_log.list_for_agent(agent_id, tenant_id=tenant_id)
        if exclude_scan_id is None:
            return rows
        return [row for row in rows if row["scan_id"] != exclude_scan_id]


# ---------------------------------------------------------------------------
# Validation helpers — produce HandlerError with v1.1.0 error codes.
# ---------------------------------------------------------------------------


def _require(body: Mapping[str, Any], field_name: str) -> Any:
    value = body.get(field_name)
    if value is None or value == "":
        raise HandlerError(422, f"{field_name}_required")
    return value


def _validate_scan_mode(scan_mode: str) -> None:
    if scan_mode != "live_probe":
        # offline_replay must route through POST /score (existing endpoint).
        raise HandlerError(422, "scan_mode_invalid")


def _enforce_no_residential_proxy_for_live_probe() -> None:
    """Fail closed when a live customer probe would run with resi proxy config.

    BEAAA-7910 removes residential-proxy transit from the live-bind path. The
    generic egress adapter still supports the env var for non-bind callers, but
    this shared `/scan` + `/rescans` entrypoint refuses before runtime kickoff.
    """
    if egress.is_enabled():
        raise HandlerError(422, "residential_proxy_forbidden_for_live_probe")


def _verify_live_egress_fail_closed(
    *,
    scanner_version: str,
    corpus_version: str,
    seed: int,
) -> None:
    """Prove live-probe egress is default-deny before runtime kickoff."""
    try:
        egress.verify_live_egress_fail_closed(
            scanner_version=str(scanner_version),
            corpus_version=str(corpus_version),
            seed=seed,
            execution_environment=ApiConfig.from_env().execution_environment,
        )
    except egress.LiveEgressPostureError as exc:
        raise HandlerError(422, "live_egress_default_deny_unverified") from exc


def _validate_k_replicates(k: Any) -> int:
    if k is None:
        return DEFAULT_K_REPLICATES
    if isinstance(k, bool) or not isinstance(k, int):
        raise HandlerError(422, "k_replicates_invalid")
    if k not in ALLOWED_K_REPLICATES:
        raise HandlerError(422, "k_replicates_invalid")
    return k


def _validate_credential_ref(credential_ref: Any) -> str:
    if not isinstance(credential_ref, str) or not _CRED_REF_SHAPE_RE.match(credential_ref):
        raise HandlerError(422, "credential_ref_invalid")
    return credential_ref


def _validate_corpus_snapshot_hash(value: Any) -> str:
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise HandlerError(422, "corpus_snapshot_hash_invalid")
    return value


def _is_private_only_literal(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return (
        ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_unspecified
        and not ip.is_reserved
    )


def _validate_https_url_or_host_safety(
    value: str,
    *,
    error_code: str,
    allow_private_ip_literals: bool = False,
    require_https_url: bool = False,
) -> str:
    """Validate literal URL/host boundaries without resolving DNS."""
    host = value
    if "://" in value or require_https_url:
        try:
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.hostname:
                raise HandlerError(422, error_code)
            if parsed.username is not None or parsed.password is not None:
                raise HandlerError(422, error_code)
            host = parsed.hostname
        except ValueError as exc:
            raise HandlerError(422, error_code) from exc

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if allow_private_ip_literals and _is_private_only_literal(ip):
        return host
    if egress._is_forbidden_resolved_ip(str(ip)):
        raise HandlerError(422, error_code)
    return host


def _validate_agent_endpoint(agent_endpoint: Any) -> dict[str, Any]:
    """Validate ``agent_endpoint`` shape: requires ``host`` (string,
    syntactically-valid hostname). Optional ``transport`` field passes through.
    """
    if not isinstance(agent_endpoint, dict):
        raise HandlerError(422, "agent_endpoint_invalid")
    raw_url = agent_endpoint.get("url")
    host = agent_endpoint.get("host")
    if raw_url is not None:
        if not isinstance(raw_url, str):
            raise HandlerError(422, "agent_endpoint_url_invalid")
        parsed = urlsplit(raw_url)
        if (
            parsed.scheme != "https" or not parsed.hostname or parsed.fragment
            or parsed.username is not None or parsed.password is not None
        ):
            raise HandlerError(422, "agent_endpoint_url_invalid")
        endpoint_url = raw_url
        endpoint_host = parsed.hostname
    else:
        if not isinstance(host, str) or not _HOSTNAME_RE.match(host):
            raise HandlerError(422, "agent_endpoint_host_invalid")
        endpoint_url = f"https://{host}"
        endpoint_host = host
    _validate_https_url_or_host_safety(
        endpoint_url,
        error_code="agent_endpoint_host_forbidden",
        allow_private_ip_literals=True,
    )
    transport = agent_endpoint.get("transport", "rest")
    if transport not in {"rest", "openai_chat", "json_map", "kaltura_genie", "kaltura_genie_browser"}:
        raise HandlerError(422, "agent_endpoint_transport_invalid")
    auth = agent_endpoint.get("auth", {"type": "bearer"})
    if not isinstance(auth, dict) or auth.get("type", "bearer") not in {
        "bearer", "custom_header", "none",
    }:
        raise HandlerError(422, "agent_endpoint_auth_invalid")
    if auth.get("type") == "custom_header":
        header_name = auth.get("header_name")
        if not isinstance(header_name, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", header_name):
            raise HandlerError(422, "agent_endpoint_auth_header_invalid")
    config: dict[str, Any] = {
        "url": endpoint_url,
        "host": endpoint_host,
        "transport": transport,
        "auth": dict(auth),
    }
    for key in ("model", "system_prompt", "request_template", "response_path"):
        if key in agent_endpoint:
            config[key] = agent_endpoint[key]
    if transport == "openai_chat" and not isinstance(config.get("model"), str):
        raise HandlerError(422, "agent_endpoint_model_required")
    if transport == "json_map":
        if not isinstance(config.get("request_template"), dict):
            raise HandlerError(422, "agent_endpoint_request_template_required")
        from api.scan_request_validation import _prompt_placeholder_count
        if _prompt_placeholder_count(config["request_template"]) != 1:
            raise HandlerError(422, "agent_endpoint_prompt_placeholder_invalid")
        if not isinstance(config.get("response_path"), str):
            raise HandlerError(422, "agent_endpoint_response_path_required")
    if transport == "kaltura_genie":
        if not re.fullmatch(r"genie\.[a-z0-9-]+\.ovp\.kaltura\.com", endpoint_host):
            raise HandlerError(422, "kaltura_genie_host_invalid")
        if urlsplit(endpoint_url).path.rstrip("/") != "/assistant/converse":
            raise HandlerError(422, "kaltura_genie_path_invalid")
    if transport == "kaltura_genie_browser":
        if endpoint_url != "https://learning.kaltura.com/":
            raise HandlerError(422, "kaltura_genie_browser_url_invalid")
        if auth.get("type") != "none":
            raise HandlerError(422, "kaltura_genie_browser_auth_invalid")
    return config


# ---------------------------------------------------------------------------
# Audience-specific response payloads — Δ4 incorporation.
# ---------------------------------------------------------------------------


def _scan_result_to_dict(result: ScanResult) -> dict[str, Any]:
    """Project the orchestrator's ScanResult to a JSON-serialisable dict.

    Includes every field by default. Audience-specific adapters then strip
    per Δ4.
    """
    return {
        "scan_id": result.scan_id,
        "status": result.status,
        "scanner_version": result.scanner_version,
        "corpus_version": result.corpus_version,
        "rows_emitted": result.rows_emitted,
        "k_replicates": result.k_replicates,
        "live_probe_variance_band": result.live_probe_variance_band,
        "spread_per_dim": dict(result.spread_per_dim),
        "pillar_spread": dict(result.pillar_spread),
        "total_spread": result.total_spread,
        "incomplete": result.incomplete,
        "incomplete_reason": result.incomplete_reason,
        "full_suite_manifest": result.full_suite_manifest,
        "result_rows": [
            dict(row) for row in result.starter_result_rows
        ],
        **_starter_score_fields(result.starter_result_rows),
        "anti_gaming": result.anti_gaming.to_dict(),
        "dim_results": [
            {
                "dim_id": d.dim_id,
                "raw": d.raw,
                "spread": d.spread,
                "replicates": list(d.replicates),
            }
            for d in result.dim_results
        ],
    }


def _production_control_evidence_ref(result: ScanResult, scan_id: str) -> str:
    manifest = result.full_suite_manifest or {}
    manifest_ref = manifest.get("manifest_evidence_ref")
    if isinstance(manifest_ref, str) and manifest_ref:
        return manifest_ref
    manifest_hash = manifest.get("manifest_hash")
    if isinstance(manifest_hash, str) and manifest_hash:
        return f"scanner://full-suite-manifests/{scan_id}#{manifest_hash}"
    return f"scanner://scan-results/{scan_id}"


def _uw_payload(result: ScanResult) -> dict[str, Any]:
    """UW-facing response — retains ``live_probe_variance_band``."""
    return _scan_result_to_dict(result)


def _customer_payload(result: ScanResult) -> dict[str, Any]:
    """Customer-facing response — strips ``live_probe_variance_band`` per Δ4.

    The customer does not see the variance band parameter; pricing decisions
    flow back via the report card / quote. The Underwriter still gets the
    full struct via ``?audience=uw``.
    """
    payload = _scan_result_to_dict(result)
    for field_name in _UW_ONLY_FIELDS:
        payload.pop(field_name, None)
    return payload

_EXTERNAL_ENVELOPE_FIELDS: tuple[str, ...] = (
    "are_total",
    "subscores",
    "band",
    "confidence",
    "evidence_refs",
    "uncovered_dimensions",
)

_EXTERNAL_ENVELOPE_PROVENANCE: tuple[str, ...] = (
    "scanner_version",
    "corpus_version",
    "model_versions",
    "seed",
)


def _band_letter(score_json: Mapping[str, Any]) -> Any:
    """Project the partner-facing letter band from a score envelope."""
    band_letter = score_json.get("band_letter")
    if isinstance(band_letter, str) and band_letter:
        return band_letter
    band_value = score_json.get("band")
    if isinstance(band_value, Mapping):
        return band_value.get("display") or band_value.get("letter")
    return band_value


def _external_result_envelope(
    result: ScanResult, score_json: Mapping[str, Any]
) -> dict[str, Any]:
    """Build the documented public completed-scan envelope."""
    envelope: dict[str, Any] = {
        "scan_id": result.scan_id,
        "status": result.status,
    }
    for field_name in _EXTERNAL_ENVELOPE_FIELDS:
        if field_name == "band":
            envelope["band"] = _band_letter(score_json)
        elif field_name == "uncovered_dimensions":
            envelope["uncovered_dimensions"] = (
                score_json.get("uncovered_dimensions") or []
            )
        else:
            envelope[field_name] = score_json.get(field_name)
    for field_name in _EXTERNAL_ENVELOPE_PROVENANCE:
        envelope[field_name] = score_json.get(field_name)
    envelope["partial_completion"] = bool(
        score_json.get("partial_completion") or result.incomplete
    )
    envelope["confidence_below_adjudication_threshold"] = bool(
        score_json.get("confidence_below_adjudication_threshold")
        or result.incomplete
    )
    if framework_envelope_enabled():
        envelope.update(build_framework_envelope(score_json))
    # BEAAA-38253 — opted-in starter rows ride the persisted-score envelope
    # too. Rows exist only when the request set include_result_rows, so a
    # default request stays summary-only.
    starter_rows = getattr(result, "starter_result_rows", ())
    if starter_rows:
        envelope["result_rows"] = [dict(row) for row in starter_rows]
        envelope.update(_starter_score_fields(starter_rows))
    return envelope


def _customer_result_response(
    scan_id: str, result: ScanResult, runtime: "ScanRuntime"
) -> dict[str, Any]:
    """Return the public score when persisted, else the legacy projection.

    BEAAA-18825: behind the ``BACKBOND_EVIDENCE_RECEIPT_V1`` gate this also
    surfaces the PII-free ``evidence_receipt`` object (evidence-receipt-v1 §2 /
    ADR-0067). The receipt is minted by the BackBond receipt service (Child 2,
    BEAAA-18819) and carried on the persisted kickoff row; this path only
    projects it. Gate off -> byte-identical prior response.
    """
    score_row = runtime.current_kickoff_row(scan_id)
    score_json = score_row.get("score_json") if score_row else None
    if isinstance(score_json, Mapping) and score_json:
        response = _external_result_envelope(result, score_json)
    else:
        response = _customer_payload(result)
    receipt = score_row.get("evidence_receipt") if isinstance(score_row, Mapping) else None
    response.update(project_evidence_receipt(receipt))
    return response



# ---------------------------------------------------------------------------
# BEAAA-1273 (WS3) — `prior_scan_refs[]` projection + pagination cap.
# ---------------------------------------------------------------------------


def _default_run_timestamp() -> str:
    """UTC ISO-8601 ``run_timestamp`` factory used when the caller does not
    inject one. Production wiring will override this with a clock-injected
    factory so backfill/replay paths can stamp historical timestamps.
    """
    return datetime.now(timezone.utc).isoformat()


def _default_forensic_bundle_uri(agent_id: str, scan_id: str) -> str:
    """Default URI scheme stamped on the WS2 ``forensic_bundle_uri`` pin —
    matches the placeholder shape in ADR-0017 §2.2 + the contract patch
    example (``s3://ifa-forensic-bundles/agent-<id>/scan-<uuid>.json``).
    Production wiring will override this with the real cold-storage
    bucket / object-lock URI factory.
    """
    return f"s3://ifa-forensic-bundles/agent-{agent_id}/scan-{scan_id}.json"


def _default_full_suite_manifest_uri(agent_id: str | None, scan_id: str) -> str:
    agent_segment = agent_id or "unknown-agent"
    return f"scanner://full-suite-manifests/{agent_segment}/{scan_id}.json"


def _chain_entry_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Project a scan_log row to the 4-field ``prior_scan_refs[]`` entry
    shape per ADR-0017 §2.1: ``{scan_id, run_timestamp, are_total, band}``.

    The ``are_total`` and ``band`` values are read straight from the
    persisted ``score_json`` envelope — *as-committed at the time of the
    prior scan* per ADR-0017 §2.1 + AC #5 (immutability of past entries
    even if the rubric changes between rescans). The chain extractor does
    NOT recompute either field.

    The current scoring engine emits the legacy ``bmi`` key alongside a
    structured ``band: {letter, modifier, ...}`` block (``rubric/scoring.py``
    line 328). The chain entry must surface the contract names
    (``are_total`` + letter-only ``band``) regardless of which on-disk
    spelling the score_json uses, so the chain reader bridges both.
    Underlying ``score_json`` field names are out of scope for WS3 (Scanner
    Engineer OS — "no scoring path modification").
    """
    score = row.get("score_json") or {}
    are_total = score.get("are_total")
    if are_total is None:
        are_total = score.get("bmi")
    band_value = score.get("band")
    if isinstance(band_value, dict):
        band_letter = band_value.get("letter")
    else:
        band_letter = band_value
    return {
        "scan_id": row["scan_id"],
        "run_timestamp": row["run_timestamp"],
        "are_total": are_total,
        "band": band_letter,
    }


def _apply_prior_scan_cap(
    rows: list[dict[str, Any]],
    *,
    cap: int = PRIOR_SCAN_REFS_MAX,
) -> tuple[list[dict[str, Any]], bool]:
    """Apply the ADR-0017 §7 Q2 pagination cap.

    ``rows`` must be ordered ascending by ``run_timestamp``. When the row
    count exceeds ``cap`` the most-recent ``cap`` entries are kept
    (truncated from the head end ascending) and the second element of the
    return tuple is ``True``. The non-breaking sentinel
    ``prior_scan_refs_truncated: true`` is the API-edge consequence; this
    helper is its source of truth.
    """
    if len(rows) <= cap:
        return rows, False
    return rows[-cap:], True


def _augment_with_chain_envelope(
    payload: dict[str, Any],
    *,
    runtime: ScanRuntime,
    agent_id: str,
    tenant_id: str,
    scan_id: str,
) -> dict[str, Any]:
    """Add the BEAAA-1273 (WS3) additive response fields to a 202 envelope.

    Pure write-time pin propagation + read-only chain query per AC #8 —
    the scoring engine, rubric, judge, and probe orchestrator are not
    re-entered. The runtime's persisted scan_log is the sole source of
    truth.

    Wire-shape contract:

    * ``prior_scan_refs`` is ALWAYS present on the /rescans response
      envelope — empty array when no prior scans are known, populated
      when the runtime's scan_log carries them. This makes the wire shape
      unambiguous for callers; ADR-0017 §2.1 allows either form ("MAY be
      empty array or omitted"), and this impl picks "always emit".
    * ``forensic_bundle_uri`` + ``snapshot_hash`` are emitted ONLY when
      the runtime persisted a row for the current scan during kickoff.
      In unwired runtimes (legacy WS1/WS2 fixtures, production paths
      that haven't bound persistence yet), these are omitted — they
      cannot be fabricated without the WS2 storage layer (AC #2: "no
      live re-touch"; per ADR-0017 §2.2/§2.3 they are at-write-time pins).
    """
    prior_rows = runtime.prior_scan_rows(
        agent_id, tenant_id=tenant_id, exclude_scan_id=scan_id
    )
    prior_capped, truncated = _apply_prior_scan_cap(prior_rows)
    payload["prior_scan_refs"] = [_chain_entry_from_row(r) for r in prior_capped]
    if truncated:
        payload["prior_scan_refs_truncated"] = True

    current = runtime.current_kickoff_row(scan_id)
    if current is not None:
        forensic_bundle_uri = current.get("forensic_bundle_uri")
        snapshot_hash = current.get("snapshot_hash")
        if forensic_bundle_uri is not None:
            payload["forensic_bundle_uri"] = forensic_bundle_uri
        if snapshot_hash is not None:
            payload["snapshot_hash"] = snapshot_hash
    return payload


# ---------------------------------------------------------------------------
# Public handlers.
# ---------------------------------------------------------------------------


def connectivity_check_handler(
    body: dict[str, Any],
    *,
    runner_factory: Callable[..., Any] = customer_agent_http_runner_factory,
) -> tuple[int, dict[str, Any]]:
    """Send one bounded probe without retaining the credential or response."""
    if not isinstance(body, dict):
        raise HandlerError(422, "body_invalid")
    endpoint = dict(_validate_agent_endpoint(_require(body, "agent_endpoint")))
    endpoint.setdefault("url", f"https://{endpoint['host']}")
    endpoint.setdefault("auth", {"type": "bearer"})
    no_auth = endpoint["auth"].get("type") == "none"
    secret = body.get("credential_secret", "") if no_auth else _require(
        body, "credential_secret"
    )
    if not isinstance(secret, str) or (not secret and not no_auth):
        raise HandlerError(422, "credential_secret_invalid")
    runner = None
    try:
        runner = runner_factory(
            endpoint["url"], secret, body.get("agent_id"), endpoint_config=endpoint
        )
        runner.complete(CONNECTIVITY_PROMPT)
        return 200, {"success": True, "error": None}
    except CustomerEndpointHTTPError as exc:
        return 200, {"success": False, "error": f"endpoint_http_{exc.status_code}"}
    except RunnerError:
        return 200, {"success": False, "error": "connection_failed"}
    finally:
        if runner is not None:
            close = getattr(runner, "close", None)
            if callable(close):
                close()
        del secret


def _kickoff_live_probe_scan(
    body: dict[str, Any],
    *,
    tenant_header: str | None,
    runtime: ScanRuntime,
    scan_id_factory: Callable[[], str] | None,
    agent_id: str | None = None,
    scanner_invocation_context: dict[str, Any] | None = None,
    webhook_secret_resolver: "Callable[[str], str | bytes | None] | None" = None,
    webhook_http_poster: "Callable[[str, dict, bytes], None] | None" = None,
    on_terminal: Callable[[], None] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Shared body-validation + ScanRequest build + runtime.kickoff path.

    Single source of truth for the live-probe scoring entrypoint reached by
    both ``POST /scan`` (``scan_handler``) and the measured
    ``POST /scanner/v1/rescans`` shim (``rescan_handler``). Keeping this in
    one place satisfies the Scanner Engineer OS auto-reject on duplicating
    rubric / judge / orchestrator wiring between scan and rescan paths
    (BEAAA-1265 WS1 — rule-execution; the rescan route reuses the scan
    pipeline by construction so the N=5 calibration delta is 0 by design).
    """
    if not isinstance(body, dict):
        raise HandlerError(422, "body_invalid")

    raw_context = body.get("context_envelope")
    if raw_context is not None:
        try:
            validate_context_envelope(raw_context)
        except ContextEnvelopeError as exc:
            raise HandlerError(422, "context_envelope_invalid") from exc

    tenant_id = _require(body, "tenant_id")
    verify_tenant_isolation(tenant_id, tenant_header)

    agent_endpoint = dict(_validate_agent_endpoint(_require(body, "agent_endpoint")))
    agent_endpoint.setdefault("url", f"https://{agent_endpoint['host']}")
    agent_endpoint.setdefault("auth", {"type": "bearer"})
    credential_ref = _validate_credential_ref(_require(body, "credential_ref"))
    no_auth = agent_endpoint["auth"].get("type") == "none"
    credential_secret = body.get("credential_secret", "") if no_auth else _require(
        body, "credential_secret"
    )
    if not isinstance(credential_secret, str) or (not credential_secret and not no_auth):
        raise HandlerError(422, "credential_secret_invalid")
    pack_id = body.get("pack_id")
    if pack_id is not None and pack_id not in {
        *_STARTER_PACK_IDS,
        "full-scan-conv-en@1.0",
    }:
        raise HandlerError(422, "pack_id_invalid")
    if pack_id is not None:
        pack_id = _effective_starter_pack_id(pack_id)
    is_starter = pack_id in _STARTER_PACK_IDS
    include_result_rows = body.get("include_result_rows", False)
    if not isinstance(include_result_rows, bool):
        raise HandlerError(422, "include_result_rows_invalid")
    if include_result_rows and not is_starter:
        raise HandlerError(422, "include_result_rows_requires_starter_pack")
    if include_result_rows:
        agent_endpoint["_include_result_rows"] = True
    # BEAAA-11726 item 1 — scan_tier: smoke|full, default full.
    # ARE_SCAN_TIER=off disables smoke tier (Tier-1 rollback).
    _scan_tier_enabled = ApiConfig.from_env().scan_tier_enabled
    raw_scan_tier = body.get("scan_tier") if _scan_tier_enabled else None
    if raw_scan_tier is not None and raw_scan_tier not in ("smoke", "full"):
        raise HandlerError(422, "scan_tier_invalid")
    scan_tier = "smoke" if is_starter else (raw_scan_tier or "full")

    # corpus_snapshot_hash: required for full, can default to pinned for smoke.
    if is_starter:
        corpus_snapshot_hash = STARTER_PACK_CORPUS_SNAPSHOT_HASH
    elif "corpus_snapshot_hash" not in body and scan_tier == "smoke":
        _pinned = ApiConfig.from_env().pinned_corpus_snapshot_hash
        if not _pinned:
            raise HandlerError(422, "pinned_corpus_hash_unconfigured")
        corpus_snapshot_hash = _validate_corpus_snapshot_hash(_pinned)
    else:
        corpus_snapshot_hash = _validate_corpus_snapshot_hash(_require(body, "corpus_snapshot_hash"))

    scanner_version = _require(body, "scanner_version")
    corpus_version = (
        STARTER_PACK_CORPUS_VERSION
        if is_starter
        else _require(body, "corpus_version")
    )

    # seed: required for full, defaults to CANONICAL_SEED (0) for smoke.
    seed_raw = body.get("seed")
    if seed_raw is None and scan_tier == "smoke":
        seed = CANONICAL_SEED
    elif not isinstance(seed_raw, int) or isinstance(seed_raw, bool):
        raise HandlerError(422, "seed_required")
    else:
        seed = seed_raw

    # BEAAA-11726 item 2 — webhook config extraction.
    # ARE_SCAN_WEBHOOK=off disables delivery; unset/on enables.
    _webhook_enabled = ApiConfig.from_env().scan_webhook_enabled
    _webhook_cfg: dict[str, str] | None = None
    if _webhook_enabled:
        raw_webhook = body.get("webhook")
        if raw_webhook is not None:
            if not isinstance(raw_webhook, dict):
                raise HandlerError(422, "webhook_invalid")
            _wh_url = raw_webhook.get("url")
            _wh_ref = raw_webhook.get("secret_ref")
            if not isinstance(_wh_url, str) or not _wh_url:
                raise HandlerError(422, "webhook_url_required")
            if not isinstance(_wh_ref, str) or not _wh_ref:
                raise HandlerError(422, "webhook_secret_ref_required")
            _validate_https_url_or_host_safety(
                _wh_url,
                error_code="webhook_url_forbidden",
                require_https_url=True,
            )
            _webhook_cfg = {"url": _wh_url, "secret_ref": _wh_ref}

    scan_mode = body.get("scan_mode", "live_probe")
    _validate_scan_mode(scan_mode)
    _enforce_no_residential_proxy_for_live_probe()
    _verify_live_egress_fail_closed(
        scanner_version=str(scanner_version),
        corpus_version=str(corpus_version),
        seed=seed,
    )
    k_replicates = _validate_k_replicates(body.get("k_replicates"))
    requested_pillars = tuple(body.get("requested_pillars", ()))
    vendor_id = body.get("vendor_id")
    if vendor_id is not None and (not isinstance(vendor_id, str) or not vendor_id):
        raise HandlerError(422, "vendor_id_invalid")
    canary_hashes_raw = body.get("canary_phrasing_hashes", ())
    if canary_hashes_raw is None:
        canary_hashes_raw = ()
    if (
        not isinstance(canary_hashes_raw, (list, tuple))
        or not all(isinstance(h, str) for h in canary_hashes_raw)
    ):
        raise HandlerError(422, "canary_phrasing_hashes_invalid")
    canary_phrasing_hashes = tuple(canary_hashes_raw)

    scan_id = (scan_id_factory or _default_scan_id)()
    # BEAAA-11864 / ADR-0046 — per-run probe-surface rotation seed. STRICTLY
    # SEPARATE from the scoring `seed` above (which stays canonical). Resolved
    # AFTER scan_id so the toggle-on default can derive a fresh per-run seed
    # from the server-minted scan_id (see _resolve_surface_seed).
    surface_seed = _resolve_surface_seed(body.get("surface_seed"), scan_id)
    try:
        scan_request = ScanRequest(
            scan_id=scan_id,
            tenant_id=tenant_id,
            agent_endpoint=agent_endpoint["url"],
            credential_ref=credential_ref,
            endpoint_config=agent_endpoint,
            pack_id=pack_id,
            include_result_rows=include_result_rows,
            corpus_snapshot_hash=corpus_snapshot_hash,
            scanner_version=scanner_version,
            corpus_version=corpus_version,
            seed=seed,
            surface_seed=surface_seed,
            requested_pillars=requested_pillars,
            scan_mode=scan_mode,
            scan_tier=scan_tier,
            k_replicates=k_replicates,
            agent_id=agent_id,
            invocation_context=scanner_invocation_context,
            vendor_id=vendor_id,
            canary_phrasing_hashes=canary_phrasing_hashes,
        )
    except InvalidKReplicatesError as exc:
        raise HandlerError(422, "k_replicates_invalid") from exc

    try:
        result = runtime.kickoff(
            scan_request,
            credential_secret,
            agent_id=agent_id,
            scanner_invocation_context=scanner_invocation_context,
            webhook_cfg=_webhook_cfg,
            on_terminal=on_terminal,
        )
    except CorpusSnapshotMismatchError as exc:
        raise HandlerError(422, "corpus_snapshot_mismatch") from exc
    except CorpusProvenanceMismatchError as exc:
        raise HandlerError(422, exc.detail) from exc
    except CredentialResolveError as exc:
        raise HandlerError(500, "credential_resolve_error") from exc
    except OrchestratorError as exc:
        raise _handler_error_from_orchestrator_error(exc) from exc
    finally:
        # Hard Invariant #4: the local secret is dropped as the function
        # returns; do not log it, do not place it in any persistent struct.
        del credential_secret

    if raw_context is not None:
        try:
            runtime.persist_context_envelope(scan_id, raw_context)
        except ContextEnvelopeError as exc:
            raise HandlerError(422, "context_envelope_invalid") from exc

    # BEAAA-11726 item 4 — probe_set_hash computed from the dim_ids that ran.
    # Wired via kickoff's scan_log call below; runtime wires probe_set_hash
    # via the ScanRuntime._probe_set_hash_factory hook if configured.
    _probe_set_hash = compute_probe_set_hash(
        [d.dim_id for d in result.dim_results]
    ) if result.dim_results else None

    # BEAAA-1274 (WS4) additive flags. Default ``false`` on every accept
    # response (per AC #5) — the rescan-side degradation path is the only
    # surface that may flip them to ``true``.
    # BEAAA-11726 item 1 — smoke tier overrides partial_completion to true.
    partial_completion = bool(result.incomplete) or scan_tier == "smoke"
    response: dict[str, Any] = {
        "scan_id": scan_id,
        "status": "partial" if partial_completion else "accepted",
        "partial_completion": partial_completion,
        "confidence_below_adjudication_threshold": partial_completion,
    }
    if scan_tier == "smoke" and _scan_tier_enabled:
        # Echo defaults so the vendor can reproduce: same agent + same version
        # => same score within +/-1. Full tier is byte-identical to v1.3.0.
        response["scan_tier"] = "smoke"
        response["underwriting_eligible"] = False
        response["non_underwriting_reason"] = "smoke_tier_sampled"
        response["seed"] = seed
        response["corpus_snapshot_hash"] = corpus_snapshot_hash
        if is_starter:
            response["pack_id"] = pack_id
            response["test_count"] = _STARTER_PACK_TEST_COUNTS[pack_id]

    return 202, response


def _scan_reached_terminal(result: ScanResult) -> bool:
    if result.status in _NON_TERMINAL_STATUSES:
        return False
    if not result.incomplete:
        return True
    return (result.incomplete_reason or "") not in _NON_TERMINAL_INCOMPLETE_REASONS


def _deliver_terminal_webhook(
    *,
    outbox: "WebhookOutbox",
    webhook_cfg: dict[str, str],
    scan_id: str,
    payload: dict[str, Any],
) -> None:
    try:
        outbox.enqueue(
            idempotency_key=scan_id,
            url=webhook_cfg["url"],
            secret_ref=webhook_cfg["secret_ref"],
            payload=payload,
        )
    except Exception:
        pass


def _fire_webhook(
    *,
    webhook_cfg: dict[str, str],
    payload: dict[str, Any],
    resolver: "Callable[[str], str | bytes | None] | None",
    http_poster: "Callable[[str, dict, bytes], None]",
) -> None:
    """Build the signed delivery and POST it (best-effort, never raises).

    Fail-closed: if the secret cannot be resolved, no unsigned delivery is
    sent (WebhookSecretResolutionError from build_signed_delivery). Any
    network error is swallowed here — the 202 is already committed.
    """
    from api.ci_scan_ext import WebhookSecretResolutionError, build_signed_delivery

    if resolver is None:
        return
    try:
        headers, body_bytes = build_signed_delivery(
            payload, webhook_cfg["secret_ref"], resolver
        )
        http_poster(webhook_cfg["url"], headers, body_bytes)
    except WebhookSecretResolutionError:
        pass
    except Exception:
        pass


def scan_handler(
    body: dict[str, Any],
    *,
    tenant_header: str | None,
    runtime: ScanRuntime,
    scan_id_factory: Callable[[], str] | None = None,
    webhook_secret_resolver: "Callable[[str], str | bytes | None] | None" = None,
    webhook_http_poster: "Callable[[str, dict, bytes], None] | None" = None,
    on_terminal: Callable[[], None] | None = None,
) -> tuple[int, dict[str, Any]]:
    """POST /scan handler.

    Validates the request, registers the credential in the runtime's vault,
    kicks off the orchestrator, and returns 202 with ``scan_id``.

    The handler does NOT block on the scan completing; ``GET /scan/{scan_id}``
    fetches the result. In test wiring with a synchronous ``ScanRuntime`` the
    result is already available when POST returns; the 202 still applies so
    the contract holds across sync and async runtime backends.

    BEAAA-1273 (WS3) — ``agent_id`` is OPTIONAL on /scans (REQUIRED on
    /rescans). When supplied AND the runtime carries a ``scan_log`` +
    ``score_envelope_factory``, the kickoff persists a chain-eligible row
    so a subsequent /rescans for the same agent_id can quote it via
    ``prior_scan_refs[]``. When omitted or unwired, /scans behavior is
    byte-identical to v1.1.0 — the /scans response envelope is preserved
    at all fields (AC #3 of BEAAA-1273); ``prior_scan_refs[]`` is NOT
    populated on /scans regardless.
    """
    body_agent_id = body.get("agent_id") if isinstance(body, dict) else None
    if isinstance(body_agent_id, str) and not _AGENT_ID_RE.match(body_agent_id):
        raise HandlerError(422, "agent_id_invalid")
    agent_id_for_chain = body_agent_id if isinstance(body_agent_id, str) else None
    return _kickoff_live_probe_scan(
        body,
        tenant_header=tenant_header,
        runtime=runtime,
        scan_id_factory=scan_id_factory,
        agent_id=agent_id_for_chain,
        webhook_secret_resolver=webhook_secret_resolver,
        webhook_http_poster=webhook_http_poster,
        on_terminal=on_terminal,
    )


def rescan_handler(
    body: dict[str, Any],
    *,
    tenant_header: str | None,
    runtime: ScanRuntime,
    scan_id_factory: Callable[[], str] | None = None,
    sla_registry: SlaRegistry | None = None,
    routing_event_sink: RoutingEventSink | None = None,
    clock: Clock | None = None,
    on_terminal: Callable[[], None] | None = None,
) -> tuple[int, dict[str, Any]]:
    """POST /scanner/v1/rescans handler (measured path).

    BEAAA-1265 WS1 set up the shim that delegates to the existing /scans
    scoring pipeline. BEAAA-1274 WS4 adds:

    * Per-``purpose`` wall-clock metric emit (POST-receipt → 200-response)
      against an injectable ``SlaRegistry``. Rolling p50/p95/p99 surface at
      ``sla_registry.snapshot()`` (the /scanner/v1/sla GET endpoint reads
      from the same registry).
    * Purpose-aware degradation policy per scope-beta-design rev 2 §2.2:
      ``pre_bind`` / ``mid_term`` pass through; ``claim_review`` prefers SLA
      extension up to the 12-hr ceiling before falling back to partial. On
      partial fallback, both additive boolean response flags
      (``partial_completion``, ``confidence_below_adjudication_threshold``)
      flip ``true``, ``uncovered_dimensions[]`` is populated with
      ``{dim_id, reason}`` entries, and the ``claim_review.partial_fallback``
      routing event is emitted via ``routing_event_sink`` (hooking the
      Claims Architect's manual-review pipeline).

    Rule-execution per ``contracts/scanner-implementation-supervision-v1.md@v1.0``
    classification table row "Refactor that doesn't touch behavior" — no
    scoring math, no judge-model, no rubric touch. The wall-clock metric and
    the response-flag plumbing are additive; the scoring engine path is the
    same as /scans by construction (WS1 sentry remains green).

    Pre-validation done here (not in ``_kickoff_live_probe_scan``) because it
    is rescan-specific:

    * ``agent_id`` required and shape-checked; must have a prior /scans row
      registered in the runtime's scan log (else 422 ``agent_id_no_prior_scan``).
    * ``scanner_invocation_context.purpose`` required and ∈
      {pre_bind, mid_term, claim_review}.

    The route is gated upstream by ``ARE_RESCANS_MEASURED=on`` in
    ``api/app.py``; flag-off serves the doc-only legacy response and never
    reaches this handler.
    """
    if not isinstance(body, dict):
        raise HandlerError(422, "body_invalid")

    agent_id = _require(body, "agent_id")
    if not isinstance(agent_id, str) or not _AGENT_ID_RE.match(agent_id):
        raise HandlerError(422, "agent_id_invalid")
    # BEAAA-38869: the prior-scan guard and the chain below are scoped to the
    # caller's tenant, so resolve + isolation-check it before the lookup
    # (``_kickoff_live_probe_scan`` repeats the same check — idempotent).
    tenant_id = _require(body, "tenant_id")
    verify_tenant_isolation(tenant_id, tenant_header)
    if not runtime.has_prior_scan_for_agent(agent_id, tenant_id=tenant_id):
        raise HandlerError(422, "agent_id_no_prior_scan")

    invocation_context = body.get("scanner_invocation_context")
    if invocation_context is None or not isinstance(invocation_context, dict):
        raise HandlerError(422, "scanner_invocation_context_required")
    purpose = invocation_context.get("purpose")
    if not isinstance(purpose, str) or purpose not in RESCAN_PURPOSES:
        raise HandlerError(422, "purpose_invalid")

    sla_clock = clock or Clock()
    start = sla_clock.now()

    status, payload = _kickoff_live_probe_scan(
        body,
        tenant_header=tenant_header,
        runtime=runtime,
        scan_id_factory=scan_id_factory,
        agent_id=agent_id,
        # BEAAA-2175 (PR-B) — plumb the rescan-specific invocation context
        # (carries ``purpose``) through to the runtime so the score envelope
        # factory closure can read it and stamp ``purpose`` on the §2.2
        # envelope without changing the ScoreEnvelopeFactory typedef.
        scanner_invocation_context=invocation_context,
        on_terminal=on_terminal,
    )

    elapsed_seconds = max(0.0, sla_clock.now() - start)

    # AC #1 — wall-clock metric emitted on every /rescans call, per-purpose.
    if sla_registry is not None:
        sla_registry.record(purpose, elapsed_seconds)

    # AC #3, #4 — degradation decision for the claim_review path; pass-through
    # for pre_bind / mid_term (flags remain false, no routing event).
    scan_id = payload.get("scan_id") if isinstance(payload, dict) else None
    result = runtime.fetch(scan_id) if isinstance(scan_id, str) else None
    completed_pillars = (
        pillars_from_dim_results(result.dim_results) if result is not None else set()
    )
    incomplete = bool(result.incomplete) if result is not None else False
    incomplete_reason = result.incomplete_reason if result is not None else None

    decision = evaluate_degradation(
        purpose=purpose,
        elapsed_seconds=elapsed_seconds,
        incomplete=incomplete,
        incomplete_reason=incomplete_reason,
        completed_pillars=completed_pillars,
    )

    payload = dict(payload)
    # BEAAA-1273 (WS3) — additive ``prior_scan_refs[]`` + ``forensic_bundle_uri``
    # + ``snapshot_hash`` (+ ``prior_scan_refs_truncated`` when capped) per
    # ADR-0017 §2.1 + §7 Q2. Pure read-only chain query + write-time pin
    # propagation (AC #8); the scoring engine path is unchanged.
    if isinstance(scan_id, str):
        _augment_with_chain_envelope(
            payload,
            runtime=runtime,
            agent_id=agent_id,
            tenant_id=tenant_id,
            scan_id=scan_id,
        )
    payload["partial_completion"] = decision.partial_completion
    payload["confidence_below_adjudication_threshold"] = (
        decision.confidence_below_adjudication_threshold
    )
    if decision.partial_completion:
        # ``status`` flips from "accepted" to "partial" so the wire is
        # self-describing without callers having to parse both flags.
        payload["status"] = "partial"
        payload["uncovered_dimensions"] = [dict(u) for u in decision.uncovered_dimensions]
        if decision.routing_event_name and routing_event_sink is not None:
            payload_for_event = dict(decision.routing_event_payload)
            if isinstance(scan_id, str):
                payload_for_event["scan_id"] = scan_id
            payload_for_event["agent_id"] = agent_id
            routing_event_sink.emit(decision.routing_event_name, payload_for_event)
    else:
        # For claim_review below the 12-hour ceiling, an incomplete
        # orchestrator result is still an accepted extension path. The
        # degradation decision owns the rescan status label.
        payload["status"] = "accepted"

    # BEAAA-2139 / ADR-0017 addendum-1 §2.3 + CTO interaction e5ab2af2
    # (`ag5_resolution_path: path_a`, resolved 2026-06-03T08:45:18Z) — Path A
    # reshape: when persistence is wired (scan_log + score_envelope_factory
    # both bound and a kickoff row exists for this scan), the 202 response is
    # the §2.2 score envelope + ``forensic_bundle_uri`` + ``snapshot_hash`` +
    # ``purpose_aware_sla``. The replay invariant (AG-5) then holds byte-
    # equally: SHA-256(JCS(response \ {forensic_bundle_uri, snapshot_hash,
    # purpose_aware_sla})) == response.snapshot_hash. Unwired runtimes keep
    # the WS1/WS3 back-compat thin shape so legacy fixtures stay green; the
    # partial-fallback path also keeps the legacy shape because
    # ``uncovered_dimensions`` is not in §2.2 envelope (out-of-scope for
    # Path A; surface separately if/when claim_review partial-path replay
    # needs the §2.2 envelope shape too).
    if (
        isinstance(scan_id, str)
        and not decision.partial_completion
    ):
        persisted = runtime.current_kickoff_row(scan_id)
        if persisted is not None and persisted.get("score_json") is not None:
            envelope = dict(persisted["score_json"])
            fbu = persisted.get("forensic_bundle_uri")
            sh = persisted.get("snapshot_hash")
            if fbu is not None:
                envelope["forensic_bundle_uri"] = fbu
            if sh is not None:
                envelope["snapshot_hash"] = sh
            if sla_registry is not None:
                sla_snapshot = sla_registry.snapshot()
                if purpose in sla_snapshot:
                    envelope["purpose_aware_sla"] = sla_snapshot[purpose]
            # ``prior_scan_refs_truncated`` (ADR-0017 §7 Q2 cap sentinel) is
            # NOT carried under Path A: addendum §2.3 specifies the response
            # as exactly envelope + 3 keys, and cap-handling shifts to the
            # envelope-build side (the score_envelope_factory closure applies
            # the cap before hashing so chain immutability is preserved). The
            # WS3 cap test surface follows separately — out of scope for this
            # cut-over heartbeat.
            payload = envelope

    return status, payload


def scan_status_handler(
    scan_id: str,
    *,
    runtime: ScanRuntime,
    audience: str = "customer",
    authenticated_tenant_id: str | None = None,
    uw_entitled: bool = True,
    ci_threshold_lookup: "Callable[[str], tuple[str | None, float | None]] | None" = None,
    dashboard_base_url: str = "https://app.backbond.ai",
) -> tuple[int, dict[str, Any]]:
    """GET /scan/{scan_id} handler.

    Returns the scan's current state. ``audience`` selects the payload variant:

    * ``"customer"`` (default) — strips ``live_probe_variance_band`` per Δ4.
    * ``"uw"`` — retains every field for the Underwriter pricing engine.
    * ``"ci_plugin"`` (BEAAA-11726 item 5b) — no-prompt categorized rollup
      for the CI/CD plugin; sourced from the score_json envelope + the
      threshold-source API lookup. ``ci_threshold_lookup(tenant_id)`` must
      return ``(band_letter | None, fail_below_score | None)``; pass
      ``None`` for the callable to skip the threshold gate (returns
      ``disposition: infra_retry`` for any non-completed scan).

    BEAAA-11858 — access control (pure gating; no scoring change):

    * Tenant isolation. When ``authenticated_tenant_id`` is supplied (bearer
      auth active), the scan must be owned by that tenant. A cross-tenant read
      returns 404 ``scan_not_found`` — never 403 — so the endpoint is not an
      existence oracle for other tenants' scan_ids (which are shared with
      external CI logs by design). ``None`` preserves the historical no-auth
      path (local/test), leaving these handler-level and offline callers
      byte-stable.
    * Audience entitlement. The pricing-internal ``uw`` payload is gated on
      ``uw_entitled`` (resolved at the API edge from the caller's entitlement).
      Checked *after* tenant isolation so a cross-tenant probe with
      ``?audience=uw`` still returns 404, not a 403 that would leak existence.
    """
    if not isinstance(scan_id, str) or not scan_id:
        raise HandlerError(422, "scan_id_required")
    result = runtime.fetch(scan_id)
    if result is None:
        raise HandlerError(404, "scan_not_found")
    if authenticated_tenant_id is not None:
        owning_request = runtime.current_scan_request(scan_id)
        owner_tenant_id = (
            owning_request.tenant_id if owning_request is not None else None
        )
        if owner_tenant_id != authenticated_tenant_id:
            raise HandlerError(404, "scan_not_found")
    if audience == "uw" and not uw_entitled:
        raise HandlerError(403, "audience_forbidden")
    if audience == "uw":
        return 200, _uw_payload(result)
    if audience == "customer":
        response = _customer_result_response(scan_id, result, runtime)
        owning_request = runtime.current_scan_request(scan_id)
        if owning_request is not None and owning_request.pack_id in _STARTER_PACK_IDS:
            response.update(runtime.accepted_scan_progress(scan_id))
        return 200, response
    if audience == "ci_plugin":
        return 200, _ci_plugin_payload(
            scan_id,
            result,
            runtime,
            authenticated_tenant_id=authenticated_tenant_id,
            ci_threshold_lookup=ci_threshold_lookup,
            dashboard_base_url=dashboard_base_url,
        )
    raise HandlerError(422, "audience_invalid")


def scan_progress_handler(
    scan_id: str,
    *,
    runtime: ScanRuntime,
    authenticated_tenant_id: str | None,
) -> tuple[int, dict[str, Any]]:
    """Owner-scoped progress; absent and foreign scans share a 404 shape."""
    if not authenticated_tenant_id:
        raise HandlerError(401, "authentication_required")
    progress = runtime.progress(scan_id, tenant_id=authenticated_tenant_id)
    if progress is None:
        raise HandlerError(404, "scan_not_found")
    return 200, progress


def scan_context_report_handler(
    scan_id: str,
    *,
    runtime: ScanRuntime,
    authenticated_tenant_id: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Internal report-sidecar read path; public scan output stays unchanged."""
    if runtime.fetch(scan_id) is None:
        raise HandlerError(404, "scan_not_found")
    if authenticated_tenant_id is not None:
        owning_request = runtime.current_scan_request(scan_id)
        owner_tenant_id = (
            owning_request.tenant_id if owning_request is not None else None
        )
        if owner_tenant_id != authenticated_tenant_id:
            raise HandlerError(404, "scan_not_found")
    report = runtime.context_report(scan_id)
    if report is None:
        raise HandlerError(404, "scan_context_not_found")
    return 200, report


def _ci_plugin_payload(
    scan_id: str,
    result: ScanResult,
    runtime: ScanRuntime,
    *,
    authenticated_tenant_id: str | None,
    ci_threshold_lookup: "Callable[[str], tuple[str | None, float | None]] | None",
    dashboard_base_url: str,
) -> dict[str, Any]:
    """Build the ci_plugin audience response for GET /scan/{scan_id}.

    Reads band/are_total from the persisted score_json envelope (via
    ``current_kickoff_row``). Applies the threshold-source API lookup to
    determine gate disposition. Never emits probe prompt text (the adapter
    in ci_scan_ext.ci_plugin_result reads only dim ids/labels/raws).
    """
    scan_completed = not result.incomplete
    dim_raws = {d.dim_id: d.raw for d in result.dim_results}

    # Read band from the persisted score_json envelope. Falls back to None
    # when the runtime has no scan_log (e.g. legacy test fixtures without
    # persistence wiring) — gate_disposition then returns infra_retry.
    score_row = runtime.current_kickoff_row(scan_id)
    score_env = score_row.get("score_json", {}) if score_row else {}
    band = score_env.get("band")
    are_total = score_env.get("are_total") or score_env.get("bmi")

    # Threshold-source API lookup determines gate pass/fail.
    # CEO-ruled (BEAAA-11725): default gate = vendor's contracted policy band;
    # fail_below_score is a stricter-only override.
    gate_passed: bool | None = None
    if scan_completed and ci_threshold_lookup is not None and are_total is not None:
        try:
            _contracted_band, fail_below = ci_threshold_lookup(
                authenticated_tenant_id or ""
            )
            threshold = resolve_gate_threshold(_contracted_band, fail_below)
            if threshold is not None and isinstance(are_total, (int, float)):
                gate_passed = float(are_total) >= float(threshold)
        except Exception:
            gate_passed = None

    disposition = gate_disposition(
        scan_completed=scan_completed,
        error_class=None,
        gate_passed=gate_passed,
    )

    # dim_labels: use dim_id as both key and label (the full label lookup
    # requires the rubric which we intentionally do not re-import here;
    # production wiring may supply labels via the score_env if available).
    raw_labels: dict[str, str] = score_env.get("dim_labels", {})
    dim_labels = {dim_id: raw_labels.get(dim_id, dim_id) for dim_id in dim_raws}

    return ci_plugin_result(
        scan_id=scan_id,
        dim_labels=dim_labels,
        dim_raws=dim_raws,
        band=band,
        disposition=disposition,
        dashboard_base_url=dashboard_base_url,
    )


def _default_scan_id() -> str:
    return str(uuid.uuid4())


def _resolve_surface_seed(raw: Any, scan_id: str) -> int | None:
    """Resolve the per-run probe-surface rotation seed (BEAAA-11864 / ADR-0046).

    STRICTLY SEPARATE from the scoring ``seed``; feeds only the Prober-side
    paraphrase bank via ``ScanRequest.surface_seed``. Policy:

    * An explicit integer ``surface_seed`` in the request body is honoured
      verbatim (exact replay of a prior scan's surface).
    * When absent AND probe-surface rotation is enabled
      (``ARE_PROBE_PARAPHRASE_RANDOMIZATION``), derive a deterministic seed
      from the server-minted ``scan_id``. A vendor cannot pre-compute it
      (scan_id is server-assigned, not client-chosen) yet replay is exact
      (scan_id is recorded in the forensic bundle) — this is the anti-harvest
      guarantee behind CTO ruling A.
    * When absent AND rotation is disabled, return ``None`` — the walker emits
      the canonical surface byte-for-byte (Tier-1 rollback = toggle-off).

    The ``harness.probe_paraphrase`` import is local so the AG-A5 ``api/**``
    module-import surface check stays tight (the paraphrase module is only
    pulled in when this edge actually resolves a rotation seed).
    """
    if raw is not None:
        if not isinstance(raw, int) or isinstance(raw, bool):
            raise HandlerError(422, "surface_seed_invalid")
        return raw

    from harness.probe_paraphrase import paraphrase_enabled

    if not paraphrase_enabled():
        return None
    digest = hashlib.sha256(scan_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


# Backward-compatible facade: the extracted modules own these implementations.
from api import scan_request_validation as _request_validation
from api import scan_response_envelopes as _response_envelopes
from api import scan_side_effects as _side_effects

_require = _request_validation.require
_validate_scan_mode = _request_validation.validate_scan_mode
_validate_k_replicates = _request_validation.validate_k_replicates
_validate_credential_ref = _request_validation.validate_credential_ref
_validate_corpus_snapshot_hash = _request_validation.validate_corpus_snapshot_hash
_validate_agent_endpoint = _request_validation.validate_agent_endpoint
_fire_webhook = _side_effects.fire_webhook
_scan_result_to_dict = _response_envelopes.scan_result_to_dict
_uw_payload = _response_envelopes.uw_payload
_customer_payload = _response_envelopes.customer_payload


__all__ = [
    "PRIOR_SCAN_REFS_MAX",
    "RESCAN_PURPOSES",
    "ScanRuntime",
    "ScoreEnvelopeFactory",
    "rescan_handler",
    "scan_handler",
    "scan_status_handler",
]
