"""FastAPI wiring for /score and /forensic_bundle/{bundleId} (Task A).

Thin adapter over api.handlers — keeps red-line semantics framework-agnostic
and lets unittest target handlers directly without booting an ASGI app.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from pricing.quote import Rates
from rubric.scoring import Rubric

from .bundle import BundleStore
from .handlers import HandlerError, forensic_bundle_handler, quote_handler, score_handler
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


def create_app(
    *,
    rubric: Rubric | None = None,
    bundles: BundleStore | None = None,
    rates: Rates | None = None,
    scan_runtime: ScanRuntime | None = None,
    sla_registry: SlaRegistry | None = None,
    routing_event_sink: RoutingEventSink | None = None,
) -> FastAPI:
    app = FastAPI(title="ARE Scanner ⇄ Underwriter API", version="1.1.0")
    app.state.rubric = rubric or Rubric.load(RUBRIC_PATH)
    app.state.bundles = bundles or BundleStore()
    app.state.rates = rates or Rates.load(RATES_PATH)
    # BEAAA-553 (T-CLOSE) — explicit override wins; otherwise opt-in via env
    # var. Default (no override, no env var) keeps ``POST /scan`` on the
    # 503 path so the offline ``/score`` path is untouched. Lazy-import the
    # factory so test setups that never construct a real ``ScanRuntime``
    # don't pay the multiprocessing import cost.
    if scan_runtime is None and os.environ.get(PRODUCTION_SCAN_WIRING_ENV) == "1":
        from .scan_runtime_factory import make_default_production_scan_runtime
        scan_runtime = make_default_production_scan_runtime()
    app.state.scan_runtime = scan_runtime
    # BEAAA-1274 (WS4) — per-purpose SLA metric registry + routing-event sink.
    # Default sinks survive the lifetime of the app instance so the rolling
    # window observed by ``GET /scanner/v1/sla`` matches the window the
    # rescan handler is feeding samples into.
    app.state.sla_registry = sla_registry or SlaRegistry()
    app.state.routing_event_sink = routing_event_sink or RoutingEventSink()

    @app.post("/score")
    async def post_score(
        request: Request,
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    ) -> Any:
        body = await request.json()
        try:
            status, payload = score_handler(
                body,
                tenant_header=x_tenant_id,
                rubric=app.state.rubric,
                bundles=app.state.bundles,
            )
        except HandlerError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        if status != 200:
            raise HTTPException(status_code=status, detail=payload)
        return payload

    @app.get("/forensic_bundle/{bundle_id}")
    async def get_bundle(bundle_id: str) -> Any:
        try:
            status, payload = forensic_bundle_handler(bundle_id, bundles=app.state.bundles)
        except HandlerError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        if status != 200:
            raise HTTPException(status_code=status, detail=payload)
        return payload

    @app.post("/scan")
    async def post_scan(
        request: Request,
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    ) -> Any:
        if app.state.scan_runtime is None:
            raise HTTPException(status_code=503, detail="scan_runtime_unconfigured")
        body = await request.json()
        try:
            status, payload = scan_handler(
                body,
                tenant_header=x_tenant_id,
                runtime=app.state.scan_runtime,
            )
        except HandlerError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        if status not in (200, 202):
            raise HTTPException(status_code=status, detail=payload)
        # Preserve the handler's status code (202 for the live-probe accept
        # path per contract v1.1.0). Returning the dict directly would let
        # FastAPI's default 200 win, dropping the contracted Accepted status.
        return JSONResponse(status_code=status, content=payload)

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
        if os.environ.get(RESCANS_MEASURED_ENV) == RESCANS_MEASURED_OFF:
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
            raise HTTPException(status_code=503, detail="scan_runtime_unconfigured")
        try:
            status, payload = rescan_handler(
                body,
                tenant_header=x_tenant_id,
                runtime=app.state.scan_runtime,
                sla_registry=app.state.sla_registry,
                routing_event_sink=app.state.routing_event_sink,
            )
        except HandlerError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        if status not in (200, 202):
            raise HTTPException(status_code=status, detail=payload)
        # Preserve the handler's 202 accept status to match /scan parity.
        return JSONResponse(status_code=status, content=payload)

    @app.get("/scanner/v1/sla")
    async def get_sla() -> Any:
        # BEAAA-1274 (WS4) — observable per-purpose rolling p50/p95/p99 from
        # the SLA registry the rescan handler is feeding. Returns the empty
        # registry shape ``{}`` before the first /rescans call.
        return app.state.sla_registry.snapshot()

    @app.get("/scan/{scan_id}")
    async def get_scan(scan_id: str, audience: str = "customer") -> Any:
        if app.state.scan_runtime is None:
            raise HTTPException(status_code=503, detail="scan_runtime_unconfigured")
        try:
            status, payload = scan_status_handler(
                scan_id,
                runtime=app.state.scan_runtime,
                audience=audience,
            )
        except HandlerError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        if status != 200:
            raise HTTPException(status_code=status, detail=payload)
        return payload

    @app.post("/quote")
    async def post_quote(request: Request) -> Any:
        body = await request.json()
        try:
            status, payload = quote_handler(body, rates=app.state.rates)
        except HandlerError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        if status != 200:
            raise HTTPException(status_code=status, detail=payload)
        return payload

    return app


app = create_app()
