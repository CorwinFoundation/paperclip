"""Scan orchestrator — BEAAA-543 / ADR-0009 §5 #2 (+ T5 BEAAA-544, T9 BEAAA-545).

Prober-side driver for the live-agent hosted probe service. Implements the
canonical §5 #2 build line from ``docs/adr/adr-0009-live-agent-hosted-probe-
v0.md``:

    Scan orchestrator — new ``scanners/live_probe_orchestrator.py``: drives
    ModelRunner through pinned corpus, emits HOSTED_RUNNER rows.

This module is the **Prober-side** orchestrator. It is invoked inside the
Prober subprocess that T4 (``scanners/live_probe_workers.py``) spawns. The
API edge (T1, not yet landed) is responsible for: validating the inbound
``POST /scan`` request, calling ``vault.register(credential_ref, secret)``,
spawning the Prober subprocess with this orchestrator as its body, and
translating the orchestrator's typed errors to HTTP responses (notably
:class:`CorpusSnapshotMismatchError` → HTTP 422 with
``error: "corpus_snapshot_mismatch"`` per AC#2).

Scope:

* Receives a validated scan request.
* Verifies the corpus tarball hash matches ``corpus_snapshot_hash`` (T6).
* Resolves the credential from the T3 vault inside the Prober.
* Drives a :class:`harness.runner.ModelRunner` through the pinned corpus to
  produce HOSTED_RUNNER measurement rows.
* Streams each row to the Scorer via the T4 IPC channel.
* Calls ``vault.forget(credential_ref)`` on every code path (success OR
  failure).
* Enforces three operational ceilings per scan (T9 / BEAAA-545): probe
  count, total tokens (input + output across all probes), and wall-clock.
  On breach the loop halts cleanly, the partial-result row stream is
  closed with END_OF_SCAN, and the returned :class:`ScanResult` carries
  ``incomplete=True`` with ``incomplete_reason`` of ``"probe_cap" |
  "token_cap" | "wallclock_cap"``. The ``incomplete: true`` /
  ``incomplete_reason`` JSON field shape is the v1.1.0 contract surface
  co-signed under BEAAA-531; the cap thresholds themselves are
  implementation defaults, not contract surface.
* Returns scan id + status; rows themselves go to the Scorer over IPC.

Out of scope — DO NOT add here. Covered by sibling tickets with their own
classifications:

* T1 / BEAAA-547 — ``POST /scan`` route, handler, request validation.
* Per-scan cap overrides via request fields; per-tenant cap policies. Not
  in v0 per BEAAA-545 "Out of scope".
* Pricing, bundle emission, scoring math — separate modules.

Carrier-rule / glossary posture
--------------------------------
The orchestrator MUST NOT log the credential, the customer's endpoint host,
or the customer's organization name. The ``target_endpoint_host`` lands in
the forensic bundle (T10) only, gated by DPA. This module emits no log
lines that carry any of those values.

Anti-pattern guards
-------------------
* HOSTED_RUNNER is imported from :mod:`api.intake_guard`, not literal-
  stringed (AC#4). Drift between this constant and the API-edge enforced
  value is prevented by the import.
* Probe execution is delegated to a ``probe_run_fn`` callback (same shape
  as T4's ``run_prober(probe_fn=...)``). This module does NOT re-implement
  any upstream eval-framework wrapper (``scanners/promptfoo_runner.py``,
  ``scanners/inspect_runner.py``); T1's wiring supplies a ``probe_run_fn``
  that adapts whichever wrapper applies for the requested pillars.
* Credential lifecycle is enforced by ``try / finally`` around
  ``vault.forget(credential_ref)`` — every exit path (corpus mismatch,
  credential miss, runner crash, success) hits the cleanup line.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

# AC#4 — import, do not literal-string. Drift between this constant and the
# intake-guard's enforced value is prevented by the import.
from api.intake_guard import HOSTED_RUNNER
from harness.credentials import CredentialVault
from harness.runner import ModelRunner
from scanners.canary_anti_gaming import (
    AntiGamingCanaryResult,
    CanaryProbeObservation,
    FiredProbeLedger,
    detect_goodhart,
    normalize_phrasing_hash,
    observation_from_probe_row,
    records_from_probe_metadata,
    select_fresh_canary_hashes,
)
from scanners.snapshot_hash import compute_corpus_snapshot_hash


# ---------------------------------------------------------------------------
# Typed errors. The API edge (T1) maps these to HTTP responses.
# ---------------------------------------------------------------------------


class CorpusSnapshotMismatchError(ValueError):
    """Pinned corpus tarball hash does not match the scan request.

    AC#2 — T1 translates this to HTTP 422 with body
    ``{"error": "corpus_snapshot_mismatch"}``. The message names both the
    expected and computed hash so the customer can diagnose pin drift.
    """


class CorpusProvenanceMismatchError(ValueError):
    """Caller-asserted provenance disagrees with server-actual corpus state."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class CredentialResolveError(RuntimeError):
    """Vault has no secret registered for the request's credential_ref.

    Indicates an API-edge wiring bug — either ``vault.register`` was never
    called, or ``vault.forget`` ran out of order. Surfaced as a typed error
    so T1 can map it to a redacted HTTP 500.
    """


class OrchestratorError(RuntimeError):
    """Generic orchestrator failure during probe-execution setup.

    Reserved for failures that are neither a corpus pin mismatch nor a
    vault-resolve miss — for example, missing runner-construction inputs.
    """


class ProbeRunIncompleteError(RuntimeError):
    """Probe walker stopped after emitting a bounded partial result.

    ``reason`` is carried onto :class:`ScanResult.incomplete_reason` when at
    least one row reached the Scorer. If no rows reached the Scorer, the
    orchestrator raises instead of accepting an empty scan.
    """

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(message or reason)
        self.reason = reason


class InvalidKReplicatesError(ValueError):
    """Raised when ``k_replicates`` is outside the v1.1.0 contract allow-list.

    BEAAA-544 / Δ2 binding via BEAAA-531 + BEAAA-535: only ``5`` and ``10``
    are permitted. T1 maps to HTTP 422 with ``error: "invalid_k_replicates"``.
    """


# ---------------------------------------------------------------------------
# T5 (BEAAA-544) — variance band protocol constants.
# ---------------------------------------------------------------------------

#: Allowed values of ``k_replicates`` on the v1.1.0 contract (Δ2 binding).
ALLOWED_K_REPLICATES: frozenset[int] = frozenset({5, 10})

#: Default K when the request omits ``k_replicates``.
DEFAULT_K_REPLICATES: int = 5

#: Per-dim + per-pillar spread allowance for live-probe scans. Spreads
#: above this band trigger ``incomplete: true`` (refuse-to-quote).
#: v1.1.0 contract field name ``live_probe_variance_band``.
LIVE_PROBE_VARIANCE_BAND: int = 2

#: Canonical pillar prefixes (P1=GOV, P2=ROB, P3=BIAS_FAIRNESS, P4=OPS).
_PILLAR_PREFIXES: tuple[str, ...] = ("P1", "P2", "P3", "P4")


# ---------------------------------------------------------------------------
# T9 (BEAAA-545) — operational cap constants.
# ---------------------------------------------------------------------------

#: Smoke/dev ceiling on measurement rows emitted by a single scan, summed
#: across all K replicates. Full underwriting MUST NOT use this as a base-suite
#: terminator; it is retained for smoke/dev boundedness and backwards imports.
MAX_SMOKE_DEV_PROBES_PER_SCAN: int = 1000
MAX_PROBES_PER_SCAN: int = MAX_SMOKE_DEV_PROBES_PER_SCAN

#: Full-underwriting runaway guard. This is intentionally not a coverage cap:
#: a full scan that reaches it returns ``incomplete_reason="probe_cap"`` and
#: refuses completion instead of silently sampling the base suite.
MAX_FULL_UNDERWRITING_PROBES_PER_SCAN: int = 100_000

#: Hard ceiling on tokens (input + output) reported by ``probe_run_fn``
#: across the entire scan. Tokens are surfaced by the probe wrapper via
#: the 3-tuple yield form ``(dim_id, partial_row, probe_tokens)``; the
#: 2-tuple form contributes zero to the running sum (probe wrappers that
#: do not report tokens are not rate-limited by this cap). Phase-1
#: design-partner sizing.
MAX_TOKENS_PER_SCAN: int = 1_000_000

#: Hard ceiling on scan wall-clock seconds. Measured from the first call
#: to :func:`_now` inside ``run_scan`` to the elapsed-time check after each
#: row emit. Phase-1 sizing — 30 min.
MAX_WALL_CLOCK_SECONDS: int = 1800


def _now() -> float:
    """Monotonic clock indirection — module-level so tests can monkeypatch.

    Production callers never override; the wall-clock cap test
    monkeypatches this symbol to advance time deterministically without
    pausing the test runner.
    """
    return time.monotonic()


# ---------------------------------------------------------------------------
# Public data shapes.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScanRequest:
    """Prober-side view of the ``POST /scan`` request payload.

    The T1 API-edge handler parses the inbound JSON, coerces to this shape,
    and hands it off to the orchestrator. The full URL of the customer's
    agent endpoint is carried so the Prober can build a runner against it;
    only the *host* component of that URL ever lands in forensic evidence
    (T10), per ADR-0009 §6b.
    """

    scan_id: str
    tenant_id: str
    agent_endpoint: str
    credential_ref: str
    corpus_snapshot_hash: str
    scanner_version: str
    corpus_version: str
    seed: int
    requested_pillars: tuple[str, ...] = ()
    scan_mode: str = "live_probe"
    scan_tier: str = "full"
    # T5 (BEAAA-544 / Δ2) — K-replicate per v1.1.0 contract.
    k_replicates: int = DEFAULT_K_REPLICATES
    # Agent identifier carried through to score_inputs for envelope assembly.
    # Optional for backwards compat; scan_runtime_factory uses it when building
    # score_inputs for the Scorer subprocess (BEAAA-3337 production cut-over).
    agent_id: str | None = None
    # BEAAA-5486 (P1/P2): carry scanner_invocation_context/purpose
    # from /scanner/v1/rescans through the orchestrator boundary so
    # production score_inputs can preserve customer attestation intent.
    invocation_context: dict[str, Any] | None = None
    # BEAAA-11721 / ADR-0041 section 3: optional anti-gaming sidecar inputs.
    # These are review signals only and do not affect scoring math.
    vendor_id: str | None = None
    canary_phrasing_hashes: tuple[str, ...] = ()
    # BEAAA-11864 / ADR-0046 (probe surface rotation, CTO ruling A on
    # BEAAA-11863): per-run probe-surface rotation seed. STRICTLY SEPARATE
    # from the canonical scoring ``seed`` above — ``surface_seed`` drives
    # paraphrase-template selection in the Prober (customer_agent_walker)
    # ONLY and NEVER touches the scoring RNG. When ``None`` (or when the
    # ARE_PROBE_PARAPHRASE_RANDOMIZATION toggle is off) the walker emits the
    # canonical probe surface byte-for-byte, so the default path is
    # unchanged and rollback is toggle-off.
    surface_seed: int | None = None

    def __post_init__(self) -> None:
        if self.k_replicates not in ALLOWED_K_REPLICATES:
            raise InvalidKReplicatesError(
                f"k_replicates={self.k_replicates} not in "
                f"{sorted(ALLOWED_K_REPLICATES)} (v1.1.0 contract — Δ2 "
                "binding via BEAAA-531 + BEAAA-535)"
            )


@dataclass(frozen=True)
class DimResult:
    """T5 — per-dim aggregated result. ``raw`` is the median of K replicates;
    ``spread`` = max − min across the K runs.
    """

    dim_id: str
    raw: float
    spread: float
    replicates: tuple[float, ...]


@dataclass(frozen=True)
class ScanResult:
    """Orchestrator return handed back to the API edge.

    The measurement rows themselves do NOT live in this result; they flow
    to the Scorer over the IPC channel. The Scorer is responsible for the
    final scoring output. This struct is the orchestrator's lifecycle-
    summary handle: scan id, status, version pins, rows streamed, plus
    the T5 variance-band aggregation surface (UW-facing fields).

    T1's customer-adapter strips ``live_probe_variance_band`` from the
    customer payload (Δ4 — BEAAA-547); UW pricing keeps it.
    """

    scan_id: str
    status: str
    scanner_version: str
    corpus_version: str
    rows_emitted: int
    # T5 — variance band protocol surface (UW-facing).
    k_replicates: int = DEFAULT_K_REPLICATES
    live_probe_variance_band: int = LIVE_PROBE_VARIANCE_BAND
    dim_results: tuple[DimResult, ...] = ()
    spread_per_dim: Mapping[str, float] = field(default_factory=dict)
    pillar_spread: Mapping[str, float] = field(default_factory=dict)
    total_spread: float = 0.0
    incomplete: bool = False
    incomplete_reason: str | None = None
    # BEAAA-11721 / ADR-0041 section 6: Underwriting-review signal only.
    anti_gaming: AntiGamingCanaryResult = field(
        default_factory=AntiGamingCanaryResult
    )


class IPCOutbound(Protocol):
    """Structural type for the Prober → Scorer IPC channel.

    T4's :class:`scanners.live_probe_workers.WorkerChannel` satisfies this
    Protocol. Tests can supply a fake with the same surface — the
    orchestrator never touches the underlying ``multiprocessing.Queue``
    directly.
    """

    def send_row(self, dim_id: str, row: dict[str, Any]) -> None: ...

    def send_end_of_scan(self) -> None: ...


# Probe-execution callback. Yields ``(dim_id, partial_row)`` per probe; the
# orchestrator overlays HOSTED_RUNNER provenance + ``eval_run_id`` before
# the row crosses the IPC boundary. The callback receives the constructed
# :class:`ModelRunner` so it can call ``runner.complete(prompt)`` against
# the customer endpoint. Production callers wire this to
# ``scanners/promptfoo_runner.py`` or ``scanners/inspect_runner.py``; T2
# does NOT bind to either framework (that is rule-execution work behind
# separate tickets, classified as upstream-eval wraps).
#
# T9 (BEAAA-545) — probe wrappers that know their per-probe token counts
# (input + output) MAY yield the 3-tuple form ``(dim_id, partial_row,
# probe_tokens)`` where ``probe_tokens`` is a non-negative int. The
# orchestrator sums these across all probes and enforces
# :data:`MAX_TOKENS_PER_SCAN`. Wrappers that yield the 2-tuple form
# contribute zero to the running sum and are not rate-limited by the
# token cap. Token counts NEVER cross the IPC boundary — the closed
# allow-list in ``scanners.live_probe_workers.ALLOWED_ROW_FIELDS`` is
# unchanged.
ProbeRunFn = Callable[
    [ModelRunner],
    Iterable[
        tuple[str, dict[str, Any]] | tuple[str, dict[str, Any], int]
    ],
]


def _unpack_probe_yield(item: Any) -> tuple[str, dict[str, Any], int]:
    """Normalize ``probe_run_fn`` yields to ``(dim_id, partial_row, tokens)``.

    Accepts both the historical 2-tuple ``(dim_id, partial_row)`` and the
    T9 3-tuple ``(dim_id, partial_row, probe_tokens)``. Anything else
    raises :class:`OrchestratorError` — a misbehaving wrapper that yields
    the wrong shape is a contract failure, not silent zero-fill.
    """
    if not isinstance(item, tuple):
        raise OrchestratorError(
            f"probe_run_fn must yield tuples; got {type(item).__name__}"
        )
    if len(item) == 2:
        dim_id, partial_row = item
        tokens = 0
    elif len(item) == 3:
        dim_id, partial_row, tokens = item
        if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
            raise OrchestratorError(
                f"probe_run_fn yielded probe_tokens={tokens!r}; expected "
                "a non-negative int (input + output token sum for this probe)"
            )
    else:
        raise OrchestratorError(
            f"probe_run_fn yielded a {len(item)}-tuple; expected (dim_id, "
            "partial_row) or (dim_id, partial_row, probe_tokens)"
        )
    return dim_id, partial_row, tokens


def _probe_cap_for_request(scan_request: ScanRequest) -> int:
    """Return the tier-aware probe-row runaway guard for this scan."""
    if scan_request.scan_tier in {"smoke", "dev"}:
        return MAX_SMOKE_DEV_PROBES_PER_SCAN
    return MAX_FULL_UNDERWRITING_PROBES_PER_SCAN


# ---------------------------------------------------------------------------
# Public surface — AC#1.
# ---------------------------------------------------------------------------


def run_scan(
    scan_request: ScanRequest,
    vault: CredentialVault,
    ipc_outbound: IPCOutbound,
    *,
    corpus_tarball_path: Path | str,
    probe_run_fn: ProbeRunFn,
    runner: ModelRunner | None = None,
    runner_factory: Callable[[str, str], ModelRunner] | None = None,
    fired_probe_ledger: FiredProbeLedger | None = None,
) -> ScanResult:
    """Prober-side driver — see module docstring.

    Lifecycle (in order):

    1. Verify the pinned corpus tarball hash matches
       ``scan_request.corpus_snapshot_hash`` — mismatch raises
       :class:`CorpusSnapshotMismatchError` (AC#2).
    2. Resolve the credential from ``vault`` — Prober side only (AC#3).
    3. Construct a :class:`ModelRunner` against the customer endpoint, OR
       accept one supplied by the caller (test injection).
    4. Drive ``probe_run_fn(runner)`` across K replicates — iterate
       probes, build rows. Each row emit is followed by the T9 cap
       checks (probe count, total tokens, wall-clock); a breach halts
       further probes and short-circuits to an ``incomplete=True``
       :class:`ScanResult` carrying the cap as ``incomplete_reason``.
    5. Stamp each row with HOSTED_RUNNER provenance + an ``eval_run_id``
       (AC#4), then stream via ``ipc_outbound.send_row`` (AC#5).
    6. Send END_OF_SCAN so the Scorer drains cleanly (always, including
       the cap-breach path — the partial-result rows accumulated so far
       reach the Scorer).
    7. ``vault.forget(credential_ref)`` on every exit path including
       exceptions (AC#3, enforced by ``try / finally``).
    """
    # NB: pass the raw string ref to the vault rather than constructing a
    # CredentialRef here. The vault's ``_coerce_ref`` validates + wraps,
    # and confining ref-instance creation to the vault module avoids the
    # cross-class identity hazard that surfaces under
    # ``importlib.reload(harness.credentials)`` in the test infrastructure.
    cred_ref = scan_request.credential_ref
    rows_emitted = 0
    try:
        # --- 1. AC#2 — corpus pin enforcement. ---
        # Mismatch is a typed error so T1's API-edge handler can translate
        # to HTTP 422 with the contract's error key.
        actual_hash = compute_corpus_snapshot_hash(corpus_tarball_path)
        if actual_hash != scan_request.corpus_snapshot_hash:
            raise CorpusSnapshotMismatchError(
                "corpus_snapshot_mismatch: expected "
                f"{scan_request.corpus_snapshot_hash!r}, computed "
                f"{actual_hash!r}"
            )

        # --- 2. AC#3 (resolve) — credential read path is Prober-only. ---
        # The vault was registered by the API edge BEFORE Prober subprocess
        # spawn; ``resolve`` reads from the per-process bucket. ``None``
        # means a wiring bug (forget ran before resolve, or register never
        # ran) — surfaced as a typed error.
        secret = vault.resolve(cred_ref)
        if secret is None:
            raise CredentialResolveError(
                "credential_ref not registered in vault; the API edge must "
                "call vault.register before invoking the orchestrator"
            )

        # --- 3. Runner construction. ---
        # The orchestrator does NOT pick a default ModelRunner adapter
        # (that is upstream-eval-wrap work behind separate tickets).
        # Callers supply either a constructed runner (test injection) or
        # a runner_factory ``(agent_endpoint, secret) -> ModelRunner`` —
        # production wiring in T1 supplies the factory.
        active_runner = runner
        if active_runner is None:
            if runner_factory is None:
                raise OrchestratorError(
                    "run_scan requires either `runner` or `runner_factory`; "
                    "T2 orchestrator is adapter-agnostic by design"
                )
            active_runner = runner_factory(
                scan_request.agent_endpoint, secret,
            )

        # BEAAA-11864 / ADR-0046 — carry the per-run probe-surface rotation
        # seed to the Prober-side walker via the runner, WITHOUT widening the
        # fixed ``ProbeRunFn(runner)`` signature. This is surface-only: the
        # walker consults it to select a paraphrase template; the scoring RNG
        # (``scan_request.seed``) is never derived from it. Best-effort setattr
        # mirrors the optional ``set_scan_deadline`` stamp idiom — a runner
        # that rejects the attribute simply falls back to the canonical
        # (un-rotated) surface.
        try:
            active_runner.surface_seed = scan_request.surface_seed
        except AttributeError:
            pass
        try:
            active_runner.scan_tier = scan_request.scan_tier
        except AttributeError:
            pass
        try:
            active_runner.operational_scan_deadline_seconds = (
                45.0 if scan_request.scan_tier in {"smoke", "dev"} else None
            )
        except AttributeError:
            pass

        # --- 4 & 5. Drive K replicates, stamp HOSTED_RUNNER, stream over IPC. ---
        # T5 (BEAAA-544 / Δ2) — K-replicate the corpus run for live-probe
        # variance band measurement. Each replicate calls probe_run_fn(runner)
        # to produce one full pass over the pinned corpus. Per-dim raws are
        # accumulated; median + spread are computed after the loop.
        #
        # T9 (BEAAA-545) — three operational ceilings are enforced inside
        # this loop. On breach we stop issuing further probes, flush the
        # partial result to the Scorer cleanly (END_OF_SCAN below still
        # fires from the `finally` branch of the surrounding lifecycle),
        # and short-circuit the variance-band check (cap breach is more
        # authoritative than partial-replicate spread).
        #
        # One ``eval_run_id`` per scan call; every row in a given scan
        # shares it so the forensic bundle (T10) can group on retrieval.
        eval_run_id = str(uuid.uuid4())
        raws_by_dim: dict[str, list[float]] = {}
        canary_observations: list[CanaryProbeObservation] = []
        fired_probe_metadata_rows: list[dict[str, Any]] = []
        vendor_id = scan_request.vendor_id
        previously_fired_hashes = frozenset()
        fresh_canary_hashes = frozenset()
        if fired_probe_ledger is not None and vendor_id is not None:
            previously_fired_hashes = fired_probe_ledger.seen_hashes(
                tenant_id=scan_request.tenant_id,
                vendor_id=vendor_id,
            )
            fresh_canary_hashes = frozenset(
                select_fresh_canary_hashes(
                    scan_request.canary_phrasing_hashes,
                    ledger=fired_probe_ledger,
                    tenant_id=scan_request.tenant_id,
                    vendor_id=vendor_id,
                )
            )
        previously_fired_hash_set = frozenset(
            h.lower() for h in previously_fired_hashes
        )
        fresh_canary_hash_set = frozenset(h.lower() for h in fresh_canary_hashes)
        cap_reason: str | None = None
        tokens_used = 0
        scan_started_at = _now()
        probe_cap = _probe_cap_for_request(scan_request)
        for _replicate_idx in range(scan_request.k_replicates):
            if cap_reason is not None:
                break
            try:
                for item in probe_run_fn(active_runner):
                    dim_id, partial_row, probe_tokens = _unpack_probe_yield(item)
                    metadata_row = dict(partial_row)
                    raw_hash = metadata_row.get("phrasing_hash") or metadata_row.get(
                        "paraphrase_hash"
                    )
                    if isinstance(raw_hash, str):
                        try:
                            normalized_hash = normalize_phrasing_hash(raw_hash)
                        except ValueError:
                            normalized_hash = ""
                        previously_fired = (
                            normalized_hash.lower() in previously_fired_hash_set
                        )
                        is_fresh_canary = (
                            normalized_hash.lower() in fresh_canary_hash_set
                        )
                        if previously_fired or is_fresh_canary:
                            obs = observation_from_probe_row(
                                metadata_row,
                                previously_fired=previously_fired,
                            )
                            if obs is not None:
                                canary_observations.append(obs)
                        fired_probe_metadata_rows.append(metadata_row)
                    row = dict(partial_row)
                    for anti_gaming_field in (
                        "phrasing_hash",
                        "paraphrase_hash",
                        "probe_passed",
                        "passed",
                        "rubric_point_id",
                        "test_id",
                    ):
                        row.pop(anti_gaming_field, None)
                    # AC#4 — provenance stamped unconditionally from the imported
                    # constant. Any value the probe_run_fn might have placed here
                    # is overwritten so a misbehaving wrapper cannot stamp a row
                    # as anything other than HOSTED_RUNNER.
                    row["runner"] = HOSTED_RUNNER
                    row.setdefault("eval_run_id", eval_run_id)
                    # AC#5 — ``ipc_outbound.send_row`` runs T4's
                    # ``serialize_measurement_row`` which enforces the closed
                    # allow-list. Any field outside the allow-list fails here
                    # before transit, so contract drift surfaces immediately
                    # rather than at the Scorer.
                    ipc_outbound.send_row(dim_id, row)
                    rows_emitted += 1
                    tokens_used += probe_tokens

                    # T5 — accumulate per-dim raws for median + spread.
                    raw_value = row.get("raw")
                    if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
                        raws_by_dim.setdefault(dim_id, []).append(float(raw_value))

                    # T9 — cap checks after the row lands so the row that
                    # tripped the cap still reaches the Scorer as evidence
                    # (partial-result emission, AC#5 on BEAAA-545).
                    if rows_emitted >= probe_cap:
                        cap_reason = "probe_cap"
                        break
                    if tokens_used > MAX_TOKENS_PER_SCAN:
                        cap_reason = "token_cap"
                        break
                    if _now() - scan_started_at > MAX_WALL_CLOCK_SECONDS:
                        cap_reason = "wallclock_cap"
                        break
            except ProbeRunIncompleteError as exc:
                cap_reason = exc.reason
                break

        # --- 6. END_OF_SCAN sentinel so the Scorer's drain loop returns. ---
        ipc_outbound.send_end_of_scan()

        # T9 — cap breach short-circuits the variance-band aggregation;
        # an incomplete K-replicate set has no meaningful spread, and the
        # cap is the authoritative reason on the response.
        if cap_reason is not None:
            anti_gaming = detect_goodhart(
                canary_observations,
                evidence_ref=f"are-probe://anti-gaming/{scan_request.scan_id}/canaries",
            )
            if fired_probe_ledger is not None and vendor_id is not None:
                for record in records_from_probe_metadata(
                    tenant_id=scan_request.tenant_id,
                    vendor_id=vendor_id,
                    scan_id=scan_request.scan_id,
                    rows=fired_probe_metadata_rows,
                ):
                    fired_probe_ledger.append(record)
            return ScanResult(
                scan_id=scan_request.scan_id,
                status="incomplete",
                scanner_version=scan_request.scanner_version,
                corpus_version=scan_request.corpus_version,
                rows_emitted=rows_emitted,
                k_replicates=scan_request.k_replicates,
                live_probe_variance_band=LIVE_PROBE_VARIANCE_BAND,
                dim_results=(),
                spread_per_dim={},
                pillar_spread={},
                total_spread=0.0,
                incomplete=True,
                incomplete_reason=cap_reason,
                anti_gaming=anti_gaming,
            )

        # T5 — aggregate per-dim median + spread + band check.
        dim_results, spread_per_dim, pillar_spread, total_spread = _aggregate_band(
            raws_by_dim
        )
        incomplete = False
        incomplete_reason: str | None = None
        band = float(LIVE_PROBE_VARIANCE_BAND)
        if total_spread > band:
            incomplete = True
            incomplete_reason = "variance_band_exceeded_total"
        else:
            for pillar, sp in pillar_spread.items():
                if sp > band:
                    incomplete = True
                    incomplete_reason = "variance_band_exceeded_pillar"
                    break
        anti_gaming = detect_goodhart(
            canary_observations,
            evidence_ref=f"are-probe://anti-gaming/{scan_request.scan_id}/canaries",
        )
        if fired_probe_ledger is not None and vendor_id is not None:
            for record in records_from_probe_metadata(
                tenant_id=scan_request.tenant_id,
                vendor_id=vendor_id,
                scan_id=scan_request.scan_id,
                rows=fired_probe_metadata_rows,
            ):
                fired_probe_ledger.append(record)

        return ScanResult(
            scan_id=scan_request.scan_id,
            status="complete" if not incomplete else "incomplete",
            scanner_version=scan_request.scanner_version,
            corpus_version=scan_request.corpus_version,
            rows_emitted=rows_emitted,
            k_replicates=scan_request.k_replicates,
            live_probe_variance_band=LIVE_PROBE_VARIANCE_BAND,
            dim_results=tuple(dim_results),
            spread_per_dim=dict(spread_per_dim),
            pillar_spread=dict(pillar_spread),
            total_spread=total_spread,
            incomplete=incomplete,
            incomplete_reason=incomplete_reason,
            anti_gaming=anti_gaming,
        )
    finally:
        # --- 7. AC#3 (forget) — runs on EVERY exit path. ---
        # Idempotent: ``vault.forget`` is a no-op if the ref was never
        # registered, so the cleanup is safe to run unconditionally even
        # when control fell through the corpus-mismatch branch (which
        # never reached ``vault.resolve``).
        vault.forget(cred_ref)


# ---------------------------------------------------------------------------
# T5 — aggregation helpers (median + spread + pillar rollup).
# ---------------------------------------------------------------------------


def _median_of(values: Sequence[float]) -> float:
    """Median per T5 spec.

    * Odd K (default K=5): middle of the sorted list.
    * Even K (K=10): lower-of-two-middles convention — keeps the aggregated
      raw inside the observed range rather than an interpolated value that
      no replicate actually produced (deterministic, audit-friendly).
    """
    if not values:
        raise ValueError("_median_of: empty sequence")
    sorted_values = sorted(values)
    n = len(sorted_values)
    if n % 2 == 1:
        return float(sorted_values[n // 2])
    return float(sorted_values[n // 2 - 1])


def _spread_of(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return float(max(values) - min(values))


def _pillar_of(dim_id: str) -> str:
    if "." in dim_id:
        head = dim_id.split(".", 1)[0]
        if head in _PILLAR_PREFIXES:
            return head
    return ""


def _aggregate_band(
    raws_by_dim: Mapping[str, list[float]],
) -> tuple[list[DimResult], dict[str, float], dict[str, float], float]:
    """Compute per-dim DimResult, per-dim spread, per-pillar spread, and
    total spread (max across dims) from raw values.
    """
    dim_results: list[DimResult] = []
    spread_per_dim: dict[str, float] = {}
    pillar_spread: dict[str, float] = {p: 0.0 for p in _PILLAR_PREFIXES}
    for dim_id, raws in sorted(raws_by_dim.items()):
        median = _median_of(raws)
        spread = _spread_of(raws)
        spread_per_dim[dim_id] = spread
        dim_results.append(
            DimResult(
                dim_id=dim_id,
                raw=median,
                spread=spread,
                replicates=tuple(raws),
            )
        )
        pillar = _pillar_of(dim_id)
        if pillar in pillar_spread and spread > pillar_spread[pillar]:
            pillar_spread[pillar] = spread
    total_spread = max(spread_per_dim.values(), default=0.0)
    return dim_results, spread_per_dim, pillar_spread, total_spread


__all__ = [
    "ALLOWED_K_REPLICATES",
    "CorpusSnapshotMismatchError",
    "CredentialResolveError",
    "DEFAULT_K_REPLICATES",
    "DimResult",
    "IPCOutbound",
    "InvalidKReplicatesError",
    "LIVE_PROBE_VARIANCE_BAND",
    "MAX_PROBES_PER_SCAN",
    "MAX_FULL_UNDERWRITING_PROBES_PER_SCAN",
    "MAX_SMOKE_DEV_PROBES_PER_SCAN",
    "MAX_TOKENS_PER_SCAN",
    "MAX_WALL_CLOCK_SECONDS",
    "OrchestratorError",
    "ProbeRunIncompleteError",
    "ProbeRunFn",
    "ScanRequest",
    "ScanResult",
    "run_scan",
]
