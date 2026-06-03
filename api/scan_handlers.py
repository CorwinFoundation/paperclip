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

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

# Verbatim T7 / BEAAA-546 alias — no fork, no copy.
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
from scanners.live_probe_orchestrator import (
    ALLOWED_K_REPLICATES,
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
        run_timestamp_factory: Callable[[], str] | None = None,
        forensic_bundle_uri_factory: Callable[[str, str], str] | None = None,
        score_envelope_factory: ScoreEnvelopeFactory | None = None,
    ) -> None:
        self._run_scan_fn = run_scan_fn
        self._results: dict[str, ScanResult] = {}
        # BEAAA-1265 (WS1) — minimal agent-scan-log shim. WS2 added the
        # durable scan_log keyed by ``(agent_id, run_timestamp)``; WS3 keeps
        # both sources truth-consistent: the in-memory set is populated from
        # ``prior_agent_ids`` (test bootstrap) AND from every successful
        # ``kickoff`` that names an ``agent_id``, while the scan_log
        # provides the ordered chain for ``prior_scan_refs[]``.
        self._agent_ids_with_prior_scan: set[str] = set(prior_agent_ids)
        # BEAAA-1273 (WS3) — persistence wiring + chain readback.
        self._scan_log = scan_log
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
        result = self._run_scan_fn(scan_request, secret)
        self._results[scan_request.scan_id] = result
        # BEAAA-2175 (PR-B) — populate per-scan registries before invoking the
        # envelope factory so the factory closure can read them via
        # ``current_scan_request`` / ``current_scan_invocation_context``.
        self._scan_requests[scan_request.scan_id] = scan_request
        if scanner_invocation_context is not None:
            self._scan_invocation_contexts[scan_request.scan_id] = dict(
                scanner_invocation_context
            )
        if agent_id is not None:
            self._agent_ids_with_prior_scan.add(agent_id)
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
                self._scan_log.append_scan(
                    agent_id=agent_id,
                    run_timestamp=run_timestamp,
                    scan_id=scan_request.scan_id,
                    scanner_version=result.scanner_version,
                    corpus_version=result.corpus_version,
                    score_json=score_envelope,
                    forensic_bundle_uri=forensic_bundle_uri,
                )
                self._kickoff_keys[scan_request.scan_id] = (agent_id, run_timestamp)
        return result

    def fetch(self, scan_id: str) -> ScanResult | None:
        return self._results.get(scan_id)

    # ----- BEAAA-1265 (WS1) agent-scan-log shim -----
    def register_prior_scan(self, agent_id: str) -> None:
        """Record that a prior /scans row exists for ``agent_id``.

        WS2 added the durable scan_log; WS3 keeps the in-memory set
        co-populated by ``kickoff`` so the 422 ``agent_id_no_prior_scan``
        guard works whether the call site registered the agent
        explicitly (test bootstrap) or implicitly (live scan path).
        """
        self._agent_ids_with_prior_scan.add(agent_id)

    def has_prior_scan_for_agent(self, agent_id: str) -> bool:
        return agent_id in self._agent_ids_with_prior_scan

    # ----- BEAAA-2175 (PR-B) per-scan registry readback -----
    def current_scan_request(self, scan_id: str) -> ScanRequest | None:
        """Return the ``ScanRequest`` that initiated the kickoff for
        ``scan_id``, or ``None`` if no kickoff has run for that id yet.
        The envelope factory closure reads this to recover ``seed`` and
        the request-side version pins for the envelope projection without
        a factory-typedef change.
        """
        return self._scan_requests.get(scan_id)

    def current_scan_invocation_context(
        self, scan_id: str
    ) -> dict[str, Any] | None:
        """Return the ``scanner_invocation_context`` supplied to
        ``kickoff`` for ``scan_id`` (carries ``purpose``), or ``None`` if
        the caller did not supply one (e.g., legacy ``/scans`` calls — the
        ``purpose`` field is only meaningful on the rescan path).
        """
        return self._scan_invocation_contexts.get(scan_id)

    # ----- BEAAA-1273 (WS3) chain readback -----
    def current_kickoff_row(self, scan_id: str) -> dict[str, Any] | None:
        """Return the scan_log row written by the kickoff that produced
        ``scan_id``, or ``None`` if persistence was not wired for that
        call (no ``scan_log``, no ``score_envelope_factory``, or no
        ``agent_id``).
        """
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
    corpus_snapshot_hash = _validate_corpus_snapshot_hash(_require(body, "corpus_snapshot_hash"))
    scanner_version = _require(body, "scanner_version")
    corpus_version = _require(body, "corpus_version")
    seed = body.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise HandlerError(422, "seed_required")

    scan_mode = body.get("scan_mode", "live_probe")
    _validate_scan_mode(scan_mode)
    k_replicates = _validate_k_replicates(body.get("k_replicates"))
    requested_pillars = tuple(body.get("requested_pillars", ()))

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
        )
    except InvalidKReplicatesError as exc:
        raise HandlerError(422, "k_replicates_invalid") from exc

    try:
        runtime.kickoff(
            scan_request,
            credential_secret,
            agent_id=agent_id,
            scanner_invocation_context=scanner_invocation_context,
        )
    except CorpusSnapshotMismatchError as exc:
        raise HandlerError(422, "corpus_snapshot_mismatch") from exc
    except CredentialResolveError as exc:
        raise HandlerError(500, "credential_resolve_error") from exc
    except OrchestratorError as exc:
        raise HandlerError(500, "orchestrator_error") from exc
    finally:
        # Hard Invariant #4: the local secret is dropped as the function
        # returns; do not log it, do not place it in any persistent struct.
        del credential_secret

    # BEAAA-1274 (WS4) additive flags. Default ``false`` on every accept
    # response (per AC #5) — the rescan-side degradation path is the only
    # surface that may flip them to ``true``.
    return 202, {
        "scan_id": scan_id,
        "status": "accepted",
        "partial_completion": False,
        "confidence_below_adjudication_threshold": False,
    }


def scan_handler(
    body: dict[str, Any],
    *,
    tenant_header: str | None,
    runtime: ScanRuntime,
    scan_id_factory: Callable[[], str] | None = None,
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
) -> tuple[int, dict[str, Any]]:
    """GET /scan/{scan_id} handler.

    Returns the scan's current state. ``audience`` selects the payload variant:

    * ``"customer"`` (default) — strips ``live_probe_variance_band`` per Δ4.
    * ``"uw"`` — retains every field for the Underwriter pricing engine.
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
    raise HandlerError(422, "audience_invalid")


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
