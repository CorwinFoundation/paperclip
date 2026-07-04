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

import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

# Verbatim T7 / BEAAA-546 alias — no fork, no copy.
from api.ci_scan_ext import (
    DISPOSITION_GATE_FAIL,
    DISPOSITION_GATE_PASS,
    DISPOSITION_INFRA_RETRY,
    ci_plugin_result,
    compute_probe_set_hash,
    gate_disposition,
)
from api.handlers import HandlerError, verify_tenant_isolation
from api.scan_log import ScanLog
from api.sla import (
    Clock,
    DegradationDecision,
    RoutingEventSink,
    SlaRegistry,
    evaluate_degradation,
    pillars_from_dim_results,
)
from harness import egress
from harness.runner import RunnerError
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


_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$", re.IGNORECASE)
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*$"
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
        run_timestamp_factory: Callable[[], str] | None = None,
        forensic_bundle_uri_factory: Callable[[str, str], str] | None = None,
        score_envelope_factory: ScoreEnvelopeFactory | None = None,
        in_memory_state_ttl_seconds: float = DEFAULT_IN_MEMORY_SCAN_STATE_TTL_SECONDS,
        monotonic_clock: Callable[[], float] | None = None,
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
        prior_seen_at = self._now()
        self._agent_ids_with_prior_scan: dict[str, float] = {
            str(agent_id): prior_seen_at for agent_id in prior_agent_ids
        }
        # BEAAA-1273 (WS3) — persistence wiring + chain readback.
        self._scan_log = scan_log
        # BEAAA-6159 - additive Log Tracker v0 scanner-run write hook. Left
        # unbound by default so legacy /scans and /rescans response bytes are
        # unchanged unless production wiring explicitly opts in.
        self._scanner_run_tracker = scanner_run_tracker
        self._run_timestamp_factory = run_timestamp_factory or _default_run_timestamp
        self._forensic_bundle_uri_factory = (
            forensic_bundle_uri_factory or _default_forensic_bundle_uri
        )
        self._score_envelope_factory = score_envelope_factory
        # Tracks the (agent_id, run_timestamp) of the current kickoff so the
        # handler can read the just-persisted row back without a second
        # scan_log scan (the row is keyed by that pair, not by scan_id).
        self._kickoff_keys: dict[str, tuple[str, str]] = {}
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

    def _purge_expired_in_memory_state(self) -> None:
        cutoff = self._now() - self._in_memory_state_ttl_seconds
        expired_scan_ids = [
            scan_id
            for scan_id, created_at in self._scan_state_created_at.items()
            if created_at < cutoff
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
        result = self._run_scan_fn(scan_request, secret)
        self._results[scan_request.scan_id] = result
        if agent_id is not None:
            self._agent_ids_with_prior_scan[agent_id] = self._now()
            if (
                self._scan_log is not None
                and self._score_envelope_factory is not None
            ):
                run_timestamp = self._run_timestamp_factory()
                forensic_bundle_uri = self._forensic_bundle_uri_factory(
                    agent_id, scan_request.scan_id
                )
                score_envelope = self._score_envelope_factory(
                    result, agent_id, run_timestamp
                )
                # BEAAA-11726 item 4 — compute probe_set_hash from the
                # dim_ids that ran and persist it write-once with the row.
                _psh = compute_probe_set_hash(
                    [d.dim_id for d in result.dim_results]
                ) if result.dim_results else None
                self._scan_log.append_scan(
                    agent_id=agent_id,
                    run_timestamp=run_timestamp,
                    scan_id=scan_request.scan_id,
                    scanner_version=result.scanner_version,
                    corpus_version=result.corpus_version,
                    score_json=score_envelope,
                    forensic_bundle_uri=forensic_bundle_uri,
                    probe_set_hash=_psh,
                )
                self._kickoff_keys[scan_request.scan_id] = (agent_id, run_timestamp)
                if self._scanner_run_tracker is not None:
                    self._scanner_run_tracker.emit_scanner_run_completed(
                        agent_id=agent_id,
                        scan_id=scan_request.scan_id,
                        run_timestamp=run_timestamp,
                        score_json=score_envelope,
                    )
        return result

    def fetch(self, scan_id: str) -> ScanResult | None:
        self._purge_expired_in_memory_state()
        return self._results.get(scan_id)

    # ----- BEAAA-1265 (WS1) agent-scan-log shim -----
    def register_prior_scan(self, agent_id: str) -> None:
        """Record that a prior /scans row exists for ``agent_id``.

        WS2 added the durable scan_log; WS3 keeps the in-memory set
        co-populated by ``kickoff`` so the 422 ``agent_id_no_prior_scan``
        guard works whether the call site registered the agent
        explicitly (test bootstrap) or implicitly (live scan path).
        """
        self._purge_expired_in_memory_state()
        self._agent_ids_with_prior_scan[agent_id] = self._now()

    def has_prior_scan_for_agent(self, agent_id: str) -> bool:
        self._purge_expired_in_memory_state()
        if self._scan_log is not None and self._scan_log.list_for_agent(agent_id):
            return True
        return agent_id in self._agent_ids_with_prior_scan

    # ----- BEAAA-2175 (PR-B) per-scan registry readback -----
    def current_scan_request(self, scan_id: str) -> ScanRequest | None:
        """Return the ``ScanRequest`` that initiated the kickoff for
        ``scan_id``, or ``None`` if no kickoff has run for that id yet.
        The envelope factory closure reads this to recover ``seed`` and
        the request-side version pins for the envelope projection without
        a factory-typedef change.
        """
        self._purge_expired_in_memory_state()
        return self._scan_requests.get(scan_id)

    def current_scan_invocation_context(
        self, scan_id: str
    ) -> dict[str, Any] | None:
        """Return the ``scanner_invocation_context`` supplied to
        ``kickoff`` for ``scan_id`` (carries ``purpose``), or ``None`` if
        the caller did not supply one (e.g., legacy ``/scans`` calls — the
        ``purpose`` field is only meaningful on the rescan path).
        """
        self._purge_expired_in_memory_state()
        return self._scan_invocation_contexts.get(scan_id)

    # ----- BEAAA-1273 (WS3) chain readback -----
    def current_kickoff_row(self, scan_id: str) -> dict[str, Any] | None:
        """Return the scan_log row written by the kickoff that produced
        ``scan_id``, or ``None`` if persistence was not wired for that
        call (no ``scan_log``, no ``score_envelope_factory``, or no
        ``agent_id``).
        """
        self._purge_expired_in_memory_state()
        if self._scan_log is None or scan_id not in self._kickoff_keys:
            return None
        agent_id, run_timestamp = self._kickoff_keys[scan_id]
        return self._scan_log.get(agent_id, run_timestamp)

    def prior_scan_rows(
        self,
        agent_id: str,
        *,
        exclude_scan_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return scan_log rows for ``agent_id`` ordered asc by
        ``run_timestamp``, excluding the row whose ``scan_id`` matches
        ``exclude_scan_id`` (i.e., the current rescan is NOT in its own
        ``prior_scan_refs[]`` — per ADR-0017 §2.1 + AC #4).
        """
        if self._scan_log is None:
            return []
        rows = self._scan_log.list_for_agent(agent_id)
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
            execution_environment=os.environ.get(
                "BACKBOND_EXECUTION_ENVIRONMENT",
                "scanner-runtime",
            ),
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


def _validate_agent_endpoint(agent_endpoint: Any) -> dict[str, str]:
    """Validate ``agent_endpoint`` shape: requires ``host`` (string,
    syntactically-valid hostname). Optional ``transport`` field passes through.
    """
    if not isinstance(agent_endpoint, dict):
        raise HandlerError(422, "agent_endpoint_invalid")
    host = agent_endpoint.get("host")
    if not isinstance(host, str) or not _HOSTNAME_RE.match(host):
        raise HandlerError(422, "agent_endpoint_host_invalid")
    return {
        "host": host,
        "transport": str(agent_endpoint.get("transport", "rest")),
    }


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
    prior_rows = runtime.prior_scan_rows(agent_id, exclude_scan_id=scan_id)
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

    tenant_id = _require(body, "tenant_id")
    verify_tenant_isolation(tenant_id, tenant_header)

    agent_endpoint = _validate_agent_endpoint(_require(body, "agent_endpoint"))
    credential_ref = _validate_credential_ref(_require(body, "credential_ref"))
    credential_secret = _require(body, "credential_secret")
    if not isinstance(credential_secret, str) or not credential_secret:
        raise HandlerError(422, "credential_secret_invalid")
    # BEAAA-11726 item 1 — scan_tier: smoke|full, default full.
    # ARE_SCAN_TIER=off disables smoke tier (Tier-1 rollback).
    _scan_tier_enabled = os.environ.get(SCAN_TIER_ENV, "on") != "off"
    raw_scan_tier = body.get("scan_tier") if _scan_tier_enabled else None
    if raw_scan_tier is not None and raw_scan_tier not in ("smoke", "full"):
        raise HandlerError(422, "scan_tier_invalid")
    scan_tier = raw_scan_tier or "full"

    # corpus_snapshot_hash: required for full, can default to pinned for smoke.
    if "corpus_snapshot_hash" not in body and scan_tier == "smoke":
        _pinned = os.environ.get(PINNED_CORPUS_SNAPSHOT_HASH_ENV, "")
        if not _pinned:
            raise HandlerError(422, "pinned_corpus_hash_unconfigured")
        corpus_snapshot_hash = _validate_corpus_snapshot_hash(_pinned)
    else:
        corpus_snapshot_hash = _validate_corpus_snapshot_hash(_require(body, "corpus_snapshot_hash"))

    scanner_version = _require(body, "scanner_version")
    corpus_version = _require(body, "corpus_version")

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
    _webhook_enabled = os.environ.get(SCAN_WEBHOOK_ENV, "on") != "off"
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
    try:
        scan_request = ScanRequest(
            scan_id=scan_id,
            tenant_id=tenant_id,
            agent_endpoint=f"https://{agent_endpoint['host']}",
            credential_ref=credential_ref,
            corpus_snapshot_hash=corpus_snapshot_hash,
            scanner_version=scanner_version,
            corpus_version=corpus_version,
            seed=seed,
            requested_pillars=requested_pillars,
            scan_mode=scan_mode,
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
        response["seed"] = seed
        response["corpus_snapshot_hash"] = corpus_snapshot_hash

    # BEAAA-11726 item 2 — fire webhook delivery if configured (best-effort;
    # delivery failure NEVER blocks the 202 — the scan is already complete).
    if _webhook_cfg is not None and webhook_http_poster is not None:
        _fire_webhook(
            webhook_cfg=_webhook_cfg,
            payload=dict(response),
            resolver=webhook_secret_resolver,
            http_poster=webhook_http_poster,
        )

    return 202, response


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
    if not runtime.has_prior_scan_for_agent(agent_id):
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
    """
    if not isinstance(scan_id, str) or not scan_id:
        raise HandlerError(422, "scan_id_required")
    result = runtime.fetch(scan_id)
    if result is None:
        raise HandlerError(404, "scan_not_found")
    if audience == "uw":
        return 200, _uw_payload(result)
    if audience == "customer":
        return 200, _customer_payload(result)
    if audience == "ci_plugin":
        return 200, _ci_plugin_payload(
            scan_id,
            result,
            runtime,
            ci_threshold_lookup=ci_threshold_lookup,
            dashboard_base_url=dashboard_base_url,
        )
    raise HandlerError(422, "audience_invalid")


def _ci_plugin_payload(
    scan_id: str,
    result: ScanResult,
    runtime: ScanRuntime,
    *,
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
                result.scan_id if hasattr(result, "scan_id") else scan_id
            )
            if fail_below is not None and isinstance(are_total, (int, float)):
                gate_passed = float(are_total) >= float(fail_below)
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


__all__ = [
    "PRIOR_SCAN_REFS_MAX",
    "RESCAN_PURPOSES",
    "ScanRuntime",
    "ScoreEnvelopeFactory",
    "rescan_handler",
    "scan_handler",
    "scan_status_handler",
]
