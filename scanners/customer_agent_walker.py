"""BEAAA-5414 — Customer-true Track B walker (``ARE_WALKER_CUSTOMER_AGENT``).

Replaces :func:`api.scan_runtime_factory.synthetic_v0_probe_run_fn` on
the ``ARE_PRODUCTION_SCAN_WIRING=1`` path when ``ARE_WALKER_CUSTOMER_AGENT=1``.

Per ADR-0030 (BEAAA-5414):

* The walker is invoked INSIDE the Prober subprocess's
  ``ScopedEgressAllowlist(agent_endpoint_host)`` scope — every
  ``runner.complete()`` call is bound to the customer endpoint host
  at the transport layer.
* The vault is fork-inherited; no credential crosses the IPC. The
  ``runner`` argument has already had the credential bound into its
  auth header by the orchestrator's ``runner_factory(agent_endpoint,
  secret)`` call.
* Yielded rows pass through the existing
  :func:`scanners.live_probe_workers` IPC validators + closed
  ALLOWED_ROW_FIELDS allow-list.
* ``evidence_uri`` points at an internal HTTPS wrap artifact reference
  (``https://evidence.are-scanner.local/wrap/<family>/<version>/<dim_id>``);
  ``evidence_sha256`` is the
  canonical SHA-256 hash over the RAW response payload bytes — NOT
  over the dim_id (distinguishes from the synthetic_v0 walker's
  ``sha256(dim_id)`` rule and gives Auditor a verifiable evidence
  fingerprint per real probe).

v0.3 scope (per ADR-0030 Q-B2 = ACCEPT 1 family): one wrapped probe
family, :mod:`scanners.wraps.inspect_section1_v0`. Adding wrap families
is a separate rule-execution wrap-ticket per family; the walker reads
the list from :func:`_resolve_wrap_modules`.

Tier-1 rollback: ``ARE_WALKER_CUSTOMER_AGENT=0`` (default) keeps the
synthetic_v0 walker byte-for-byte. No code deletion of
``synthetic_v0_probe_run_fn`` per Q-B6.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Iterable, Sequence, Tuple

from harness.runner import ModelRunner, RunnerError
from scanners.live_probe_orchestrator import ProbeRunIncompleteError
from scanners.wraps import inspect_section1_v0


GLOBAL_SCAN_DEADLINE_SECONDS: float = 45.0
"""BEAAA-7887 production budget for the customer-true walker.

The synchronous ``/scan`` path must return inside the external 60s probe
budget even when proxy egress is flaky. The runner may still retry transient
per-call failures, but all retries across wrap families share this deadline.
"""


WRAP_MODULES = (inspect_section1_v0,)
"""Tuple of wrap modules the walker iterates in v0.3. Single-entry
list keeps the iteration shape stable for future wrap-tickets to
extend by appending a single module reference; per Q-B2 carry-along,
the close packet must state pillar coverage is held off pending
follow-on wrap-tickets."""


def _resolve_wrap_modules() -> Sequence[Any]:
    """Return the tuple of wrap modules iterated by the walker.

    Indirection point so test fixtures can swap wrap modules per
    AG-B1/AG-B5 without monkey-patching the module-level constant; the
    smoke test under ``tests/integration/test_walker_customer_agent_v0_3.py``
    uses a fixture wrap module that yields deterministic responses
    against the local HTTP echo server.
    """
    return WRAP_MODULES


def _evidence_uri(wrap_family_id: str, wrap_version: str, dim_id: str) -> str:
    """Build the evidence URI emitted for a wrap'd probe row.

    Shape: ``https://evidence.are-scanner.local/wrap/<family>/<version>/<dim_id>``.
    The Auditor dereferences this URI against the wrap module registry at
    audit time; the follow-on wrap-ticket integrates the real upstream
    framework's log URI scheme on a per-family basis (e.g., Inspect's session
    log URI).
    """
    return (
        "https://evidence.are-scanner.local/wrap/"
        f"{wrap_family_id}/{wrap_version}/{dim_id}"
    )


def _evidence_sha256_bytes(response: str) -> str:
    """Canonical SHA-256 over the raw response payload bytes.

    Per ADR-0030 §"Walker contract": evidence_sha256 is computed over
    the RAW response payload (UTF-8 bytes), not over the dim_id. This
    distinguishes the customer-true walker's evidence from the
    synthetic_v0 walker's ``sha256(dim_id)`` rule and gives the
    Auditor a verifiable per-probe fingerprint.
    """
    digest = hashlib.sha256(response.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _set_runner_scan_deadline(runner: ModelRunner, deadline: float) -> None:
    setter = getattr(runner, "set_scan_deadline", None)
    if callable(setter):
        setter(deadline)


def customer_agent_probe_run_fn(
    runner: ModelRunner,
) -> Iterable[Tuple[str, dict[str, Any]]]:
    """Customer-true ProbeRunFn — drives wrap'd probe families against
    the customer endpoint via ``runner`` and yields one ``(dim_id,
    partial_row)`` tuple per real probe response.

    See module docstring for tamper-resistance posture, evidence URI
    shape, and v0.3 scope rationale.

    Hard Invariants preserved:

    * **#1** — offline path untouched (this module is only imported when
      ``ARE_WALKER_CUSTOMER_AGENT=1`` on the production scan-runtime
      factory path; the CLI/offline goldens never touch it).
    * **#3** — IPC validators are wrapped, not forked (the Scorer-side
      :func:`scanners.live_probe_workers.run_scorer` runs verbatim over
      the yielded rows; the closed ALLOWED_ROW_FIELDS allow-list rejects
      any extra field).
    * **#4** — credential never crosses the IPC (``runner`` has the
      credential bound into the auth header BEFORE the walker is
      invoked; the walker never reads it back).
    * **#5** — tamper resistance posture unchanged (the Prober
      subprocess's ``ScopedEgressAllowlist(agent_endpoint_host)``
      scope holds for the entire walker iteration; the Scorer's
      deny-all scope is untouched).
    """
    deadline = time.monotonic() + GLOBAL_SCAN_DEADLINE_SECONDS
    rows_emitted = 0
    _set_runner_scan_deadline(runner, deadline)
    for wrap_module in _resolve_wrap_modules():
        wrap_family_id = wrap_module.WRAP_FAMILY_ID
        wrap_version = wrap_module.WRAP_VERSION
        for spec in wrap_module.probe_specs():
            if time.monotonic() >= deadline:
                raise ProbeRunIncompleteError(
                    "customer_endpoint_unreachable_transport",
                    "customer endpoint scan deadline exceeded",
                )
            # The runner.complete call exits the walker into the
            # ScopedEgressAllowlist scope; the customer endpoint host
            # is the only host any transport layer below can reach.
            try:
                response = runner.complete(spec.prompt)
            except RunnerError as exc:
                if (
                    time.monotonic() >= deadline
                    or str(exc).lower() == "syllable scan deadline exceeded"
                ):
                    raise ProbeRunIncompleteError(
                        "customer_endpoint_unreachable_transport",
                        "customer endpoint scan deadline exceeded",
                    ) from exc
                raise
            raw = spec.scoring_rule(response)
            rows_emitted += 1
            yield (
                spec.dim_id,
                {
                    "raw": float(raw),
                    "evidence_uri": _evidence_uri(
                        wrap_family_id, wrap_version, spec.dim_id
                    ),
                    "evidence_sha256": _evidence_sha256_bytes(response),
                },
            )


__all__ = [
    "customer_agent_probe_run_fn",
    "GLOBAL_SCAN_DEADLINE_SECONDS",
    "WRAP_MODULES",
]
