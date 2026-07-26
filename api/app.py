"""FastAPI wiring for /score and /forensic_bundle/{bundleId} (Task A).

Thin adapter over api.handlers — keeps red-line semantics framework-agnostic
and lets unittest target handlers directly without booting an ASGI app.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.webhook_outbox import WebhookOutbox

from fastapi import FastAPI, Header, HTTPException, Request
from release_identity import RELEASE_IDENTITY
from fastapi.responses import JSONResponse

from pricing.quote import Rates
from rubric.scoring import Rubric
from harness.secret_redaction import redact_secret_obj

from .bundle import BundleStore
from .config import ApiConfig
from .edge_gateway import AuthError, EdgeGateway, RateLimited
from .handlers import (
    HandlerError,
    forensic_bundle_handler,
    quote_handler,
    score_handler,
    validate_quote_tenant_context,
)
from .intake_guard import EvalProvenanceVerifier
from .runtime_guards import (
    bundle_store_from_env,
    current_runtime_profile,
    validate_runtime_environment,
)
from .release_identity import ReleaseIdentityError, read_release_identity
from .scan_handlers import (
    RESCAN_PURPOSES,
    ScanRuntime,
    rescan_handler,
    scan_handler,
    scan_status_handler,
)
from .sla import RoutingEventSink, SlaRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO_ROOT / "rubric" / "rubric.yaml"
RATES_PATH = REPO_ROOT / "pricing" / "rates.yaml"
LOGGER = logging.getLogger(__name__)

#: Env var that opts into the BEAAA-553 (T-CLOSE) production scan wiring.
#: Default-off so the offline ``/score`` + ``/forensic_bundle/{id}`` path is
#: byte-identical to today's behavior (Hard Invariant #1 of BEAAA-553).
PRODUCTION_SCAN_WIRING_ENV: str = "ARE_PRODUCTION_SCAN_WIRING"

#: BEAAA-1265 (WS1) env var gating the measured /scanner/v1/rescans shim per
#: scope-beta-design rev 2 §7 Rollback. v1.2.0 cut at WS7 close (BEAAA-1283)
#: flipped the default to **on**: the route returns the v1.2.0 measured
#: envelope (prior_scan_refs[], forensic_bundle_uri, snapshot_hash,
#: partial_completion, confidence_below_adjudication_threshold) unless the
#: env var is explicitly set to ``"off"``. Setting ``ARE_RESCANS_MEASURED=off``
#: is rollback Path 1 (≤10 min Tier-2 flip per ADR-0017 §6); it reverts the
#: route to the legacy doc-only response shape with no code change or
#: redeploy. Existing forensic bundles and snapshot_hash values remain
#: valid under rollback.
RESCANS_MEASURED_ENV: str = "ARE_RESCANS_MEASURED"

#: Rollback Path 1 sentinel. Any value other than ``"off"`` (including
#: unset, empty string, or ``"on"``) means the measured route is active.
RESCANS_MEASURED_OFF: str = "off"
API_AUTH_TOKENS_ENV: str = "ARE_API_AUTH_TOKENS_JSON"

#: BEAAA-11858 — allowlist (JSON array of tenant_ids) of callers entitled to
#: request the pricing-internal ``?audience=uw`` payload on GET /scan/{scan_id}.
#: Unset/empty ⇒ no tenant is uw-entitled (fail closed): vendor tokens cannot
#: self-elevate to the UW struct. Only consulted when bearer auth is active.
UW_AUDIENCE_TENANTS_ENV: str = "ARE_UW_AUDIENCE_TENANTS_JSON"

#: BEAAA-14394 (fix 1) — the CI-gate threshold source. A JSON object mapping
#: ``tenant_id`` → ``{"policy_band": "B", "fail_below_score": 72}`` that models
#: the vendor's contracted BackBond account band (CEO ruling BEAAA-11725:
#: server-side single source of truth, so a vendor cannot loosen the gate
#: locally). Unset/empty ⇒ no source is wired and the ci_plugin audience
#: reports ``infra_retry`` (fail-closed: never fabricate a pass). Parsed once
#: at startup so a malformed config fails fast rather than per-request.
CI_THRESHOLD_BANDS_ENV: str = "ARE_CI_THRESHOLD_BANDS_JSON"


def _ci_threshold_source_from_env() -> (
    "Any | None"
):
    """Build the tenant-keyed CI-gate threshold source from the environment.

    Returns a callable ``tenant_id -> (policy_band | None, fail_below_score |
    None)`` when ``ARE_CI_THRESHOLD_BANDS_JSON`` is configured, else ``None``.
    Unknown tenants resolve to ``(None, None)`` — no contracted band on file,
    which the handler treats as ``infra_retry`` (fail-closed).
    """
    raw = os.environ.get(CI_THRESHOLD_BANDS_ENV)
    if not raw:
        return None
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("ci_threshold_bands_invalid") from exc
    if not isinstance(mapping, dict):
        raise ValueError("ci_threshold_bands_invalid")

    def _lookup(tenant_id: str) -> tuple[str | None, float | None]:
        entry = mapping.get(tenant_id)
        if not isinstance(entry, dict):
            return None, None
        band = entry.get("policy_band")
        fail_below = entry.get("fail_below_score")
        band_out = band if isinstance(band, str) and band else None
        fail_out = (
            float(fail_below)
            if isinstance(fail_below, (int, float)) and not isinstance(fail_below, bool)
            else None
        )
        return band_out, fail_out

    return _lookup


def _http_error(status_code: int, detail: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail=redact_secret_obj(detail))


def _api_auth_required(runtime_profile: str) -> bool:
    return ApiConfig.from_env().api_auth_required(runtime_profile)


def _bearer_token(request: Request) -> str:
    """Extract the bearer token or raise 401. Shared by the legacy flat-dict
    path and the BEAAA-11867 key-store path."""
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise _http_error(401, "authorization_bearer_required")
    return authorization.removeprefix("Bearer ").strip()


def _authenticated_tenant_id(request: Request, runtime_profile: str) -> str | None:
    """Return tenant id bound to the bearer token, or None on local dev bypass.

    BEAAA-11867 — when the edge gateway is active (``ARE_EDGE_GATEWAY=on``),
    identity is resolved through the mutable key store: a revoked or expired
    key resolves to ``401`` at the edge within the store's cache-TTL, no
    process restart. When the gateway is absent, the historical flat
    ``ARE_API_AUTH_TOKENS_JSON`` path is preserved byte-for-byte: it is a JSON
    object mapping bearer token to tenant_id, required in pilot/prod, and in
    local tests with no mapping routes keep the no-auth path.
    """
    gateway: EdgeGateway | None = getattr(request.app.state, "edge_gateway", None)
    if gateway is not None:
        token = _bearer_token(request)
        try:
            return gateway.resolve_tenant(token)
        except AuthError as exc:
            raise _http_error(401, exc.code) from exc

    raw_mapping = ApiConfig.from_env().api_auth_tokens_raw
    required = _api_auth_required(runtime_profile)
    if not raw_mapping and not required:
        return None
    if not raw_mapping:
        raise _http_error(500, "api_auth_tokens_unconfigured")
    try:
        mapping = json.loads(raw_mapping)
    except json.JSONDecodeError as exc:
        raise _http_error(500, "api_auth_tokens_invalid") from exc
    if not isinstance(mapping, dict) or not mapping:
        raise _http_error(500, "api_auth_tokens_invalid")
    token = _bearer_token(request)
    tenant_id = mapping.get(token)
    if not isinstance(tenant_id, str) or not tenant_id:
        raise _http_error(401, "authorization_invalid")
    return tenant_id


def _rate_limited_response(exc: RateLimited) -> JSONResponse:
    """429 body + Retry-After for a tenant over a scans/min or concurrent cap."""
    return JSONResponse(
        status_code=429,
        content={"error": "rate_limited", "reason": exc.reason, "retry_after": exc.retry_after},
        headers={"Retry-After": str(exc.retry_after)},
    )


def _admit_scan_or_none(request: Request):
    """Resolve the caller's key record and admit one scan through the D3
    limiter, returning a :class:`ConcurrencyLease` (to release when the scan
    is terminal). Returns ``None`` when the gateway is disabled. Raises 401
    if the key vanished (expiry race) and :class:`RateLimited` when over a cap.
    """
    gateway: EdgeGateway | None = getattr(request.app.state, "edge_gateway", None)
    if gateway is None:
        return None
    record = gateway.resolve_record(_bearer_token(request))
    if record is None:
        raise _http_error(401, "authorization_invalid")
    return gateway.admit_scan(record)


def _uw_audience_entitled(authenticated_tenant_id: str | None) -> bool:
    """Whether the caller may request the ``?audience=uw`` payload (BEAAA-11858).

    When no bearer-auth mapping is configured (local/test bypass —
    ``authenticated_tenant_id is None``) the historical no-auth path is
    preserved and the gate is open. In pilot/prod, entitlement is an explicit
    allowlist of tenant_ids in ``ARE_UW_AUDIENCE_TENANTS_JSON`` (a JSON array).
    Fail closed: unset/empty ⇒ no tenant is uw-entitled, so vendor tokens
    cannot obtain the pricing-internal UW payload.
    """
    if authenticated_tenant_id is None:
        return True
    raw = ApiConfig.from_env().uw_audience_tenants_raw
    if not raw:
        return False
    try:
        allow = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _http_error(500, "uw_audience_tenants_invalid") from exc
    if not isinstance(allow, list):
        raise _http_error(500, "uw_audience_tenants_invalid")
    return authenticated_tenant_id in {str(t) for t in allow}


def _tenant_context(
    request: Request,
    runtime_profile: str,
    x_tenant_id: str | None,
) -> str | None:
    authenticated = _authenticated_tenant_id(request, runtime_profile)
    if authenticated is None:
        return x_tenant_id
    if x_tenant_id and x_tenant_id != authenticated:
        raise _http_error(409, "tenant mismatch between auth context and X-Tenant-Id")
    return authenticated


def create_app(
    *,
    rubric: Rubric | None = None,
    bundles: BundleStore | None = None,
    rates: Rates | None = None,
    scan_runtime: ScanRuntime | None = None,
    sla_registry: SlaRegistry | None = None,
    routing_event_sink: RoutingEventSink | None = None,
    provenance_verifier: EvalProvenanceVerifier | None = None,
    webhook_outbox: "WebhookOutbox | None" = None,
    ci_threshold_source: "Any | None" = None,
) -> FastAPI:
    runtime_profile = current_runtime_profile()
    # BEAAA-12021 — build + validate the typed config once at startup.
    config = ApiConfig.from_env()
    config.require_production_signing_key()
    app = FastAPI(
        title="ARE Scanner ⇄ Underwriter API",
        version=RELEASE_IDENTITY.api_app_version,
    )

    @app.get("/version", include_in_schema=False)
    def deployed_version() -> dict[str, str]:
        """Return the immutable source revision baked into this image."""
        return {"git_sha": os.environ.get("BACKBOND_BUILD_SHA", "unknown")}

    app.state.rubric = rubric or Rubric.load(RUBRIC_PATH)
    app.state.runtime_profile = runtime_profile
    app.state.bundles = bundles or bundle_store_from_env()
    app.state.rates = rates or Rates.load(RATES_PATH)
    # BEAAA-553 (T-CLOSE) — explicit override wins; otherwise opt-in via env
    # var. Default (no override, no env var) keeps ``POST /scan`` on the
    # 503 path so the offline ``/score`` path is untouched. Lazy-import the
    # factory so test setups that never construct a real ``ScanRuntime``
    # don't pay the multiprocessing import cost.
    if scan_runtime is None and config.production_scan_wiring:
        from .scan_runtime_factory import make_default_production_scan_runtime
        scan_runtime = make_default_production_scan_runtime()
    app.state.scan_runtime = scan_runtime
    validate_runtime_environment(
        profile=runtime_profile,
        bundles=app.state.bundles,
        scan_runtime=app.state.scan_runtime,
    )
    # BEAAA-14400 (P1#5 fix 2) — durable signed-webhook outbox. Explicit
    # injection wins (tests); otherwise build the production outbox (env-backed
    # fail-closed secret resolver + httpx poster) on the same
    # ``production_scan_wiring`` gate as the scan runtime, so the default
    # (unwired) boot keeps ``POST /scan`` byte-identical with no delivery.
    # The lazy import keeps httpx off the import path for callers that never
    # deliver a webhook.
    if webhook_outbox is None and config.production_scan_wiring:
        from .webhook_outbox import make_default_webhook_outbox
        webhook_outbox = make_default_webhook_outbox()
    app.state.webhook_outbox = webhook_outbox
    # Thread the outbox onto the runtime so the terminal chokepoint
    # ``ScanRuntime._record_completed_result`` can deliver on completion (sync
    # AND async background paths), keyed by scan_id — never at queue time.
    if app.state.scan_runtime is not None:
        app.state.scan_runtime.attach_webhook_outbox(app.state.webhook_outbox)
    # Restart-recovery drain: replay any deliveries left ``pending`` by a prior
    # process. A no-op with the in-memory default; composes with fix 4
    # (BEAAA-14401), which injects a durable store whose ``pending()`` survives
    # a restart. ``delivered`` records are skipped, so recovery never dupes.
    if app.state.webhook_outbox is not None:
        app.state.webhook_outbox.deliver_pending()
    # BEAAA-1274 (WS4) — per-purpose SLA metric registry + routing-event sink.
    # Default sinks survive the lifetime of the app instance so the rolling
    # window observed by ``GET /scanner/v1/sla`` matches the window the
    # rescan handler is feeding samples into.
    app.state.sla_registry = sla_registry or SlaRegistry()
    app.state.routing_event_sink = routing_event_sink or RoutingEventSink()
    # BEAAA-11867 (Child B) — D4 key store + D3 per-tenant rate limit.
    # ``from_env`` returns None unless ``ARE_EDGE_GATEWAY=on``, so the default
    # boot keeps the legacy flat-dict auth path with no rate limiting. Rollback
    # is config-only: unset the flag.
    app.state.edge_gateway = EdgeGateway.from_env()
    # BEAAA-10945 (SEC-6 live edge) — the intake provenance verifier. Explicit
    # injection wins (tests); otherwise build from ``ARE_EVAL_PROVENANCE_KEY``.
    # ``from_env`` returns None when the key is absent, which leaves the /score
    # provenance check inert (offline replay / golden N=5). On the live edge the
    # key is mandatory: ``validate_runtime_environment`` above already refuses to
    # start a pilot/prod runtime without it, so a keyless live edge cannot boot.
    app.state.provenance_verifier = (
        provenance_verifier
        if provenance_verifier is not None
        else EvalProvenanceVerifier.from_env()
    )
    # BEAAA-14394 (fix 1) — tenant-keyed CI-gate threshold source. Explicit
    # injection wins (tests); otherwise build from ``ARE_CI_THRESHOLD_BANDS_JSON``.
    # ``None`` leaves the ci_plugin audience reporting ``infra_retry`` (no gate
    # source wired), which is the fail-closed default.
    app.state.ci_threshold_source = (
        ci_threshold_source
        if ci_threshold_source is not None
        else _ci_threshold_source_from_env()
    )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        correlation_id = str(uuid.uuid4())
        LOGGER.exception(
            "Unhandled portal exception correlation_id=%s path=%s",
            correlation_id,
            request.url.path,
            exc_info=exc,
        )
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "correlation_id": correlation_id},
            headers={"X-Correlation-Id": correlation_id},
        )

    @app.post("/score")
    async def post_score(
        request: Request,
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    ) -> Any:
        body = await request.json()
        tenant_context = _tenant_context(request, runtime_profile, x_tenant_id)
        try:
            status, payload = score_handler(
                body,
                tenant_header=tenant_context,
                rubric=app.state.rubric,
                bundles=app.state.bundles,
                provenance_verifier=app.state.provenance_verifier,
            )
        except HandlerError as exc:
            raise _http_error(exc.status, exc.message) from exc
        if status != 200:
            raise _http_error(status, payload)
        return payload

    @app.get("/forensic_bundle/{bundle_id}")
    async def get_bundle(
        request: Request,
        bundle_id: str,
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    ) -> Any:
        tenant_context = _tenant_context(request, runtime_profile, x_tenant_id)
        try:
            status, payload = forensic_bundle_handler(
                bundle_id,
                tenant_header=tenant_context,
                bundles=app.state.bundles,
            )
        except HandlerError as exc:
            raise _http_error(exc.status, exc.message) from exc
        if status != 200:
            raise _http_error(status, payload)
        return payload

    async def _post_scan_request(
        request: Request,
        x_tenant_id: str | None,
    ) -> Any:
        tenant_context = _tenant_context(request, runtime_profile, x_tenant_id)
        # BEAAA-11867 D3 — admit through the per-tenant limiter (no-op when the
        # gateway is disabled). The lease models one in-flight scan.
        # BEAAA-14399 (P1#5 fix 3) — after the async full-scan cutover the scan
        # keeps running on a daemon worker past the 202, so the lease must be
        # held for the *actual execution*. We hand ``lease.release`` to the
        # runtime as ``on_terminal``; it fires exactly once on the terminal
        # transition (inline for the synchronous path, on the worker for the
        # async path). The handler only releases the lease itself when it never
        # reached the runtime (validation/503/JSON errors) so the admission slot
        # is not leaked. ``ConcurrencyLease.release`` is idempotent, so a
        # belt-and-braces double release is safe.
        try:
            lease = _admit_scan_or_none(request)
        except RateLimited as exc:
            return _rate_limited_response(exc)
        lease_transferred = False
        try:
            if app.state.scan_runtime is None:
                raise _http_error(503, "scan_runtime_unconfigured")
            body = await request.json()
            try:
                status, payload = scan_handler(
                    body,
                    tenant_header=tenant_context,
                    runtime=app.state.scan_runtime,
                    on_terminal=(lease.release if lease is not None else None),
                )
            except HandlerError as exc:
                raise _http_error(exc.status, exc.message) from exc
            if status not in (200, 202):
                raise _http_error(status, payload)
            # The runtime now owns the lease's terminal release.
            lease_transferred = True
            # Preserve the handler's status code (202 for the live-probe accept
            # path per contract v1.1.0). Returning the dict directly would let
            # FastAPI's default 200 win, dropping the contracted Accepted status.
            return JSONResponse(status_code=status, content=payload)
        finally:
            if lease is not None and not lease_transferred:
                lease.release()

    @app.post("/scanner/v1/scans")
    async def post_scanner_v1_scans(
        request: Request,
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    ) -> Any:
        return await _post_scan_request(request, x_tenant_id)

    @app.post("/scan", deprecated=True)
    async def post_scan(
        request: Request,
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    ) -> Any:
        return await _post_scan_request(request, x_tenant_id)

    @app.post("/scanner/v1/rescans")
    async def post_rescan(
        request: Request,
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    ) -> Any:
        # BEAAA-1265 (WS1) — /scanner/v1/rescans shim. v1.2.0 default
        # (post-WS7 merge on BEAAA-1283) is ``on``: any value other than
        # ``ARE_RESCANS_MEASURED=off`` activates the measured response
        # envelope. Rollback Path 1 per ADR-0017 §6 is to set the env var
        # to ``off`` explicitly.
        body = await request.json()
        tenant_context = _tenant_context(request, runtime_profile, x_tenant_id)
        if ApiConfig.from_env().rescans_measured_off:
            return JSONResponse(
                status_code=200,
                content={
                    "status": "doc_only",
                    "agent_id": (body or {}).get("agent_id") if isinstance(body, dict) else None,
                    "purpose": (
                        ((body or {}).get("scanner_invocation_context") or {}).get("purpose")
                        if isinstance(body, dict)
                        and isinstance(body.get("scanner_invocation_context"), dict)
                        else None
                    ),
                    "note": (
                        "/scanner/v1/rescans measurement is gated behind "
                        f"{RESCANS_MEASURED_ENV}=on; see ADR-0017 + BEAAA-1255."
                    ),
                },
            )
        if app.state.scan_runtime is None:
            raise _http_error(503, "scan_runtime_unconfigured")
        # BEAAA-11867 D3 — /scanner/v1/rescans is scan-creating on the measured
        # path, so it is rate-limited too (the doc_only branch above creates no
        # scan and is not limited).
        try:
            lease = _admit_scan_or_none(request)
        except RateLimited as exc:
            return _rate_limited_response(exc)
        # BEAAA-14399 — same admission-lease terminal handoff as POST /scan.
        lease_transferred = False
        try:
            try:
                status, payload = rescan_handler(
                    body,
                    tenant_header=tenant_context,
                    runtime=app.state.scan_runtime,
                    sla_registry=app.state.sla_registry,
                    routing_event_sink=app.state.routing_event_sink,
                    on_terminal=(lease.release if lease is not None else None),
                )
            except HandlerError as exc:
                raise _http_error(exc.status, exc.message) from exc
            if status not in (200, 202):
                raise _http_error(status, payload)
            lease_transferred = True
            # Preserve the handler's 202 accept status to match /scan parity.
            return JSONResponse(status_code=status, content=payload)
        finally:
            if lease is not None and not lease_transferred:
                lease.release()

    @app.get("/scanner/v1/sla")
    async def get_sla() -> Any:
        # BEAAA-1274 (WS4) — observable per-purpose rolling p50/p95/p99 from
        # the SLA registry the rescan handler is feeding. Returns the empty
        # registry shape ``{}`` before the first /rescans call.
        return app.state.sla_registry.snapshot()

    @app.get("/scanner/v1/release-identity")
    async def get_release_identity() -> Any:
        try:
            return read_release_identity()
        except ReleaseIdentityError as exc:
            raise _http_error(503, exc.code) from exc

    @app.get("/scan/{scan_id}")
    async def get_scan(request: Request, scan_id: str, audience: str = "customer") -> Any:
        # BEAAA-11858 — capture (do not discard) the authenticated tenant so
        # the handler can enforce tenant isolation, and resolve uw-audience
        # entitlement at the edge where the env allowlist lives.
        authenticated_tenant_id = _authenticated_tenant_id(request, runtime_profile)
        if app.state.scan_runtime is None:
            raise _http_error(503, "scan_runtime_unconfigured")
        try:
            status, payload = scan_status_handler(
                scan_id,
                runtime=app.state.scan_runtime,
                audience=audience,
                authenticated_tenant_id=authenticated_tenant_id,
                uw_entitled=_uw_audience_entitled(authenticated_tenant_id),
                ci_threshold_lookup=app.state.ci_threshold_source,
            )
        except HandlerError as exc:
            raise _http_error(exc.status, exc.message) from exc
        if status != 200:
            raise _http_error(status, payload)
        return payload

    @app.post("/quote")
    async def post_quote(request: Request) -> Any:
        body = await request.json()
        authenticated_tenant_id = _authenticated_tenant_id(request, runtime_profile)
        try:
            validate_quote_tenant_context(body, authenticated_tenant_id=authenticated_tenant_id)
            status, payload = quote_handler(body, rates=app.state.rates)
        except HandlerError as exc:
            raise _http_error(exc.status, exc.message) from exc
        if status != 200:
            raise _http_error(status, payload)
        return payload

    return app


app = create_app()
