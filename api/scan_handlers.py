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
from typing import Any, Callable, Mapping

# Verbatim T7 / BEAAA-546 alias — no fork, no copy.
from api.handlers import HandlerError, verify_tenant_isolation
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

# Fields the orchestrator's ScanResult carries that are UW-only (Δ4).
_UW_ONLY_FIELDS: frozenset[str] = frozenset({"live_probe_variance_band"})


# ---------------------------------------------------------------------------
# Runtime — the API edge's dependency on the orchestrator + result store.
# ---------------------------------------------------------------------------


class ScanRuntime:
    """Pluggable orchestrator wiring used by the handler.

    Test setups instantiate this with a synchronous ``run_scan_fn`` so
    ``POST /scan`` returns 202 with an immediately-resolvable ``scan_id``.
    Production wiring (deferred to T-CLOSE) will spawn the Prober subprocess
    and the runtime will surface the result asynchronously.
    """

    def __init__(
        self,
        *,
        run_scan_fn: Callable[[ScanRequest, str], ScanResult],
    ) -> None:
        self._run_scan_fn = run_scan_fn
        self._results: dict[str, ScanResult] = {}

    def kickoff(self, scan_request: ScanRequest, secret: str) -> ScanResult:
        """Run the scan now; record the result by scan_id."""
        result = self._run_scan_fn(scan_request, secret)
        self._results[scan_request.scan_id] = result
        return result

    def fetch(self, scan_id: str) -> ScanResult | None:
        return self._results.get(scan_id)


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
# Public handlers.
# ---------------------------------------------------------------------------


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
        runtime.kickoff(scan_request, credential_secret)
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

    return 202, {"scan_id": scan_id, "status": "accepted"}


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
    "ScanRuntime",
    "scan_handler",
    "scan_status_handler",
]
