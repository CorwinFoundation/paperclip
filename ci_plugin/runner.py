"""Orchestrates the CI/CD gate: preflight → submit → poll → verify → decide.

The runner holds the retry/backoff loop (infra failures are retried, never
red the build on exhaustion) and the secret-redaction discipline (the two
CI secrets — ``auth_token`` and ``backbond_api_key`` — never reach a log
line). All decision math lives in :mod:`ci_plugin.gate`; all I/O lives in
:mod:`ci_plugin.client`. This module wires them and reads inputs from the
environment for both the GitHub Action and the GitLab template.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ci_plugin.client import (
    BAND_FLOOR,
    ScanClient,
    ScanTransportError,
    Transport,
    UrllibTransport,
)
from ci_plugin.gate import GateDecision, decide_exit, resolve_threshold

Logger = Callable[[str], None]


@dataclass(frozen=True)
class Inputs:
    agent_endpoint: str
    backbond_api_key: str
    backbond_base_url: str
    scan_tier: str = "full"
    auth_token: str | None = None
    fail_below_score: float | None = None
    webhook_secret: str | None = None
    #: A completion webhook already delivered to this job (body + signature
    #: header). When present, its HMAC is verified before the score is
    #: trusted. When absent, polling is the source of truth.
    webhook_body: Mapping[str, Any] | None = None
    webhook_signature: str | None = None
    max_infra_retries: int = 3
    poll_max_wait_s: float = 120.0


def _redactor(secrets: list[str]) -> Callable[[str], str]:
    real = [s for s in secrets if s]

    def redact(text: str) -> str:
        for s in real:
            if s:
                text = text.replace(s, "***")
        return text

    return redact


def run(
    inputs: Inputs,
    *,
    transport: Transport,
    sleep: Callable[[float], None] = time.sleep,
    log: Logger = lambda m: print(m, file=sys.stderr),
) -> GateDecision:
    """Execute the gate and return the :class:`GateDecision`.

    Never raises for expected failure modes — every path resolves to a
    decision with an exit code. Secrets are redacted from every log line.
    """
    redact = _redactor([inputs.backbond_api_key, inputs.auth_token or "",
                        inputs.webhook_secret or ""])

    def emit(msg: str) -> None:
        log(redact(msg))

    client = ScanClient(
        base_url=inputs.backbond_base_url,
        api_key=inputs.backbond_api_key,
        transport=transport,
        webhook_secret=inputs.webhook_secret,
    )

    # 1. §6 preflight — refuse production before we submit anything.
    try:
        if _with_infra_retry(
            lambda: client.endpoint_self_identifies_production(
                inputs.agent_endpoint, inputs.auth_token
            ),
            retries=inputs.max_infra_retries, sleep=sleep, emit=emit,
            what="preflight",
        ):
            emit("§6 preflight: endpoint self-identifies as production")
            return decide_exit(
                production_self_identified=True, infra_exhausted=False,
                webhook_verified=None, score=None, threshold=None,
                scan_tier=inputs.scan_tier,
            )
    except _InfraExhausted:
        emit("preflight infra-exhausted → advisory")
        return _infra_advisory(inputs.scan_tier)

    # 2. Submit + 3. poll, retrying infra failures with backoff.
    try:
        submit = _with_infra_retry(
            lambda: client.submit_scan(
                agent_endpoint=inputs.agent_endpoint,
                auth_token=inputs.auth_token, scan_tier=inputs.scan_tier,
            ),
            retries=inputs.max_infra_retries, sleep=sleep, emit=emit,
            what="submit",
        )
        scan_id = submit["scan_id"]
        emit(f"scan submitted: {scan_id} (tier={inputs.scan_tier})")
        result = _with_infra_retry(
            lambda: client.poll_scan(
                scan_id, max_wait_s=inputs.poll_max_wait_s, sleep=sleep
            ),
            retries=inputs.max_infra_retries, sleep=sleep, emit=emit,
            what="poll",
        )
    except _InfraExhausted:
        emit("scan infra-exhausted after retries → advisory")
        return _infra_advisory(inputs.scan_tier)

    # 4. If a webhook was delivered, verify HMAC before trusting the score.
    webhook_verified: bool | None = None
    if inputs.webhook_body is not None and inputs.webhook_signature is not None:
        webhook_verified = client.verify_webhook(
            inputs.webhook_body, inputs.webhook_signature
        )
        if not webhook_verified:
            emit("webhook HMAC mismatch → fail-closed (exit 4)")
            return decide_exit(
                production_self_identified=False, infra_exhausted=False,
                webhook_verified=False, score=None, threshold=None,
                scan_tier=inputs.scan_tier,
            )

    # 5. Resolve threshold (vendor policy floor from API lookup; override is
    #    stricter-only) and read the measured score + effective tier.
    policy_floor = _policy_floor(result)
    threshold = resolve_threshold(inputs.fail_below_score, policy_floor)
    score = result.get("are_total")
    score = None if score is None else float(score)
    effective_tier = str(result.get("scan_tier") or inputs.scan_tier)

    decision = decide_exit(
        production_self_identified=False, infra_exhausted=False,
        webhook_verified=webhook_verified, score=score, threshold=threshold,
        scan_tier=effective_tier,
    )
    emit(decision.reason)
    if not decision.bindable and decision.score is not None:
        emit("result is NON-bindable (smoke tier) — MUST NOT feed Underwriter "
             "pricing")
    return decision


def _policy_floor(result: Mapping[str, Any]) -> float | None:
    """Vendor contracted policy-band floor for the default gate.

    Prefers the scanner's explicit numeric ``policy_band_floor`` (the
    BEAAA-11725 API-lookup source of truth); falls back to the contract band
    cut for a returned ``policy_band`` letter. Never uses the *measured*
    band — that would let a bad agent set its own bar.
    """
    explicit = result.get("policy_band_floor")
    if explicit is not None:
        return float(explicit)
    policy_band = result.get("policy_band")
    if isinstance(policy_band, str) and policy_band.upper() in BAND_FLOOR:
        return BAND_FLOOR[policy_band.upper()]
    return None


def _infra_advisory(scan_tier: str) -> GateDecision:
    return decide_exit(
        production_self_identified=False, infra_exhausted=True,
        webhook_verified=None, score=None, threshold=None, scan_tier=scan_tier,
    )


class _InfraExhausted(Exception):
    pass


def _with_infra_retry(
    fn: Callable[[], Any], *, retries: int, sleep: Callable[[float], None],
    emit: Logger, what: str,
) -> Any:
    """Call ``fn``; retry with backoff on :class:`ScanTransportError`.

    All :class:`ScanTransportError` kinds are infra by construction (the
    client only raises them for transport failures). On exhaustion raises
    :class:`_InfraExhausted` so the runner returns the advisory exit.
    """
    delay = 1.0
    last_kind = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except ScanTransportError as exc:
            last_kind = exc.kind
            if attempt >= retries:
                emit(f"{what}: infra error '{exc.kind}' — retries exhausted")
                raise _InfraExhausted(exc.kind) from exc
            emit(f"{what}: infra error '{exc.kind}', retry "
                 f"{attempt + 1}/{retries} in {delay:.0f}s")
            sleep(delay)
            delay = min(delay * 2, 30.0)
    raise _InfraExhausted(last_kind or "unknown")


# ---------------------------------------------------------------------------
# Environment adapter — one entrypoint for both surfaces.
# ---------------------------------------------------------------------------
def inputs_from_env(env: Mapping[str, str]) -> Inputs:
    """Build :class:`Inputs` from CI env vars.

    GitHub Actions exposes ``with:`` inputs as ``INPUT_<NAME>`` (uppercased);
    the GitLab template maps the same values to ``BACKBOND_<NAME>``. We
    accept either spelling so a single entrypoint serves both surfaces.
    """
    def get(name: str, default: str | None = None) -> str | None:
        return (
            env.get(f"INPUT_{name.upper()}")
            or env.get(f"BACKBOND_{name.upper()}")
            or default
        )

    agent_endpoint = get("AGENT_ENDPOINT")
    api_key = get("BACKBOND_API_KEY") or env.get("BACKBOND_API_KEY")
    base_url = get("BACKBOND_BASE_URL") or "https://api.backbond.ai"
    if not agent_endpoint or not api_key:
        raise SystemExit(
            "ci_plugin: agent_endpoint and backbond_api_key are required"
        )
    fbs = get("FAIL_BELOW_SCORE")
    return Inputs(
        agent_endpoint=agent_endpoint,
        backbond_api_key=api_key,
        backbond_base_url=base_url,
        scan_tier=(get("SCAN_TIER") or "full"),
        auth_token=get("AUTH_TOKEN"),
        fail_below_score=None if fbs in (None, "") else float(fbs),
        webhook_secret=get("WEBHOOK_SECRET"),
    )


def main(argv: list[str] | None = None) -> int:
    """Container entrypoint. Returns the process exit code."""
    inputs = inputs_from_env(os.environ)
    decision = run(inputs, transport=UrllibTransport())
    # Emit machine-readable outputs for the GitHub Action (never secrets).
    out = {
        "exit_code": decision.exit_code,
        "reason": decision.reason,
        "bindable": decision.bindable,
        "advisory": decision.advisory,
        "score": decision.score,
        "threshold": decision.threshold,
    }
    gh_out = os.environ.get("GITHUB_OUTPUT")
    line = "result=" + json.dumps(out)
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    print(line)
    return decision.exit_code


__all__ = ["Inputs", "run", "inputs_from_env", "main"]


if __name__ == "__main__":  # pragma: no cover - container entry
    sys.exit(main())
