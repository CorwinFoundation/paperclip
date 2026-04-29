"""FastAPI wiring for /score and /forensic_bundle/{bundleId} (Task A).

Thin adapter over api.handlers — keeps red-line semantics framework-agnostic
and lets unittest target handlers directly without booting an ASGI app.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

from pricing.quote import Rates
from rubric.scoring import Rubric

from .bundle import BundleStore
from .handlers import HandlerError, forensic_bundle_handler, quote_handler, score_handler

REPO_ROOT = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO_ROOT / "rubric" / "rubric.yaml"
RATES_PATH = REPO_ROOT / "pricing" / "rates.yaml"


def create_app(
    *,
    rubric: Rubric | None = None,
    bundles: BundleStore | None = None,
    rates: Rates | None = None,
) -> FastAPI:
    app = FastAPI(title="BMI Scanner ⇄ Underwriter API", version="0.2.0")
    app.state.rubric = rubric or Rubric.load(RUBRIC_PATH)
    app.state.bundles = bundles or BundleStore()
    app.state.rates = rates or Rates.load(RATES_PATH)

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
