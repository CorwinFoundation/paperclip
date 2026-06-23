"""Production ``ScanRuntime`` factory — BEAAA-553 (T-CLOSE).

ADR-0009 §5 build closure: wires the API-edge ``ScanRuntime`` to the real
Prober subprocess + WorkerChannel + ModelRunner factory. The handler module
``api/scan_handlers.py`` is unchanged; this factory only constructs a
``ScanRuntime`` whose ``run_scan_fn`` performs the fork-spawn lifecycle.

Classification (per ``contracts/scanner-implementation-supervision-v1.md@v1.0``)
-----------------------------------------------------------------------------
**Rule-execution** — pure plumbing. The factory:

* changes no rubric point, no scoring math, no judge,
* introduces no new probe family,
* does not alter the ``contracts/are-scanner-api-v1.md`` output shape,
* leaves ``bin/are-scan`` and ``harness/cli_runner.py`` byte-identical
  (Hard Invariant #1).

No CTO co-sign required at merge; N=5 calibration delta on the offline
golden set is recorded as standard discipline (rule-execution row of the
classification table — "refactor that doesn't touch behavior").

Hard Invariants reinforced
--------------------------
* **#1 — Offline path untouched.** The factory is constructed by
  ``api/app.py::create_app`` only when ``ARE_PRODUCTION_SCAN_WIRING=1``;
  default behavior keeps ``scan_runtime=None`` and the 503 path on
  ``POST /scan``.
* **#3 — IPC validators are wrapped, not forked.** The Scorer subprocess
  reuses :func:`scanners.live_probe_workers.run_scorer`, which calls
  :func:`api.intake_guard.enforce_input_safety` and
  :func:`api.intake_guard.enforce_hosted_eval_only` verbatim.
* **#4 — Credential never crosses the IPC boundary.** The vault is
  registered on the API-edge side and inherited into the Prober
  subprocess via fork-only copy-on-write memory. The Scorer subprocess
  imports no credential code. ``forget_all`` runs on every exit path.
* **#5 — Tamper resistance posture unchanged.** The Prober opens
  ``ScopedEgressAllowlist(agent_endpoint)`` around the ``run_scan``
  call; the Scorer's ``ScopedEgressAllowlist([])`` (deny-all) lives
  inside :func:`run_scorer` for its entire lifetime.

Anti-pattern guards
-------------------
* The Prober target does NOT re-implement ``scanners.live_probe_workers``
  egress or IPC logic — it imports both modules and composes them.
* The factory does NOT wrap an upstream eval framework. ``probe_run_fn``
  is supplied by the caller; for v0 a synthetic-corpus walker
  (:func:`synthetic_v0_probe_run_fn`) is provided so ``POST /scan`` can
  return an ``incomplete: false`` result with non-empty ``dim_results``
  before any eval framework is wired in. The synthetic walker is
  deterministic per ``scan_request.seed`` so reproducibility holds.
* A single ``eval_run_id`` per scan is stamped by the orchestrator's
  ``run_scan`` itself; this module does not duplicate that logic.
"""
from __future__ import annotations

import hashlib
import io
import os
import tarfile
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

from api.scan_handlers import (
    ScanRuntime,
    _apply_prior_scan_cap,
    _chain_entry_from_row,
)
from api.scan_log import ScanLog
from harness.credentials import CredentialVault
from harness.egress import ScopedEgressAllowlist
from harness.runner import MockRunner, ModelRunner
from scanners.live_probe_orchestrator import (
    OrchestratorError,
    ProbeRunFn,
    ScanRequest,
    ScanResult,
    run_scan,
)
from scanners.live_probe_workers import (
    DEFAULT_SCORER_ROW_RECV_TIMEOUT_SECONDS,
    WorkerChannel,
    spawn_scorer,
    validate_inbound_envelope,
)
from scanners.snapshot_hash import compute_corpus_snapshot_hash


# ADR-0017 addendum-1 §2.4 — Phase 1 prod fleet scan_log path on EFS-mounted
# /var/lib/are-scanner. Overridable via ARE_SCAN_LOG_PATH so the cut-over
# pre-flight (and any future test rig) can point the runtime at a writable
# temp file without touching the prod constant.
ARE_SCAN_LOG_PATH_ENV: str = "ARE_SCAN_LOG_PATH"
ARE_SCAN_LOG_DEFAULT_PATH: str = "/var/lib/are-scanner/scan_log.sqlite3"


# Wall-clock guard on the parent-side join. The orchestrator enforces its
# own MAX_WALL_CLOCK_SECONDS cap on the Prober; we add a small slack so the
# parent unblocks even if the cap-breach path is slow to flush.
_PARENT_JOIN_SLACK_SECONDS: int = 30

# Scorer-side envelope projection should complete immediately after the
# Prober flushes END_OF_SCAN. Keep this independent from the scan wall-clock
# cap so a crashed Scorer cannot pin a single-worker API process for the
# full live-probe ceiling.
SCORER_ENVELOPE_DRAIN_TIMEOUT_SECONDS: float = float(
    os.environ.get("ARE_SCORER_ENVELOPE_DRAIN_TIMEOUT_SECONDS", "10")
)

SCORER_ROW_RECV_TIMEOUT_SECONDS: float = float(
    os.environ.get(
        "ARE_SCORER_ROW_RECV_TIMEOUT_SECONDS",
        str(DEFAULT_SCORER_ROW_RECV_TIMEOUT_SECONDS),
    )
)


# BEAAA-2182 Track A — kill-switch for the Scorer-side scoring path.
# Default `on` (real scoring inside Scorer subprocess, projected envelope
# shipped back over the WorkerChannel return channel). Tier-1 rollback
# flips `off` and the factory falls back to the master 52c003e
# `_phase1_pending` placeholder envelope.
ARE_SCORER_REAL_ENV: str = "ARE_SCORER_REAL"


def _scorer_real_enabled() -> bool:
    """True iff the Scorer-side scoring path is enabled.

    Default `on`. Set ``ARE_SCORER_REAL=off`` for Tier-1 rollback to the
    pre-Track-A `_phase1_pending` envelope behavior.
    """
    value = os.environ.get(ARE_SCORER_REAL_ENV, "on").strip().lower()
    return value in ("on", "1", "true", "yes")


# BEAAA-5414 v0.3 — customer-true Track B walker selector. Default off.
# Set ``ARE_WALKER_CUSTOMER_AGENT=1`` to swap
# :func:`synthetic_v0_probe_run_fn` + :func:`v0_mock_runner_factory` for
# :func:`scanners.customer_agent_walker.customer_agent_probe_run_fn` +
# :func:`harness.customer_runner.customer_agent_http_runner_factory` on
# :func:`make_default_production_scan_runtime`. Tier-1 rollback flips
# back to synthetic_v0 in ≤10 min by setting the env var to 0 / unset.
# CTO co-sign: BEAAA-5418. UW op-seat co-sign: BEAAA-5420. v1.5.0 patch
# envelope shape unchanged; only the ``applicability_decisions``
# provenance dial (CASE 1 → CASE 2) and ``partial_completion`` semantics
# move under the customer-true path.
ARE_WALKER_CUSTOMER_AGENT_ENV: str = "ARE_WALKER_CUSTOMER_AGENT"


def _customer_agent_walker_enabled() -> bool:
    """True iff the BEAAA-5414 customer-true Track B walker is selected.

    Default off. The synthetic_v0 path is byte-identical to the canon
    master ``bec8f80`` baseline when this flag is unset.
    """
    value = os.environ.get(ARE_WALKER_CUSTOMER_AGENT_ENV, "0").strip().lower()
    return value in ("on", "1", "true", "yes")


# ---------------------------------------------------------------------------
# v0 helpers — synthetic probe stream + MockRunner factory.
# ---------------------------------------------------------------------------


def _noop_validated_row(dim_id: str, entry: dict[str, Any]) -> None:  # noqa: ARG001
    """v0 Scorer sink — accepts validated rows and drops them.

    The scoring engine wiring is out of scope for T-CLOSE per the issue's
    "Out of scope" clause. The Scorer subprocess is still spawned (deny-all
    egress posture, IPC validators active) so the v0 wiring exercises the
    full process-model topology before any scoring math reaches it.
    """
    return None


def synthetic_v0_probe_run_fn(runner: ModelRunner) -> Iterable[tuple[str, dict[str, Any]]]:
    """Deterministic synthetic walker for v0 — yields enough rows to satisfy
    ``incomplete: false`` on the v1.1.0 contract.

    Yields one row per pillar (P1.1, P2.1, P3.1, P4.1) with a stable raw
    value and synthetic evidence URI/SHA. The runner argument is required
    by the ``ProbeRunFn`` shape but not invoked (no network), matching the
    issue's allowance: "the ``probe_run_fn`` may be a small synthetic-
    corpus walker that yields a handful of ``(dim_id, partial_row)``
    tuples sufficient to produce ``incomplete: false``".
    """
    # ``runner`` retained for shape compatibility with the ProbeRunFn
    # protocol. A future wrap-ticket may swap this for an
    # eval-framework-driven generator.
    _ = runner.model_id()
    for dim_id, raw in (
        ("P1.1", 3.0),
        ("P2.1", 2.5),
        ("P3.1", 2.0),
        ("P4.1", 3.5),
    ):
        # Deterministic sha256 derived from the dim_id so the synthetic
        # evidence bundle byte-pattern is stable across replicates.
        sha_hex = hashlib.sha256(dim_id.encode("utf-8")).hexdigest()
        yield (
            dim_id,
            {
                "raw": raw,
                "evidence_uri": f"https://evidence.local/{dim_id.lower()}",
                "evidence_sha256": f"sha256:{sha_hex}",
            },
        )


def v0_mock_runner_factory(agent_endpoint: str, secret: str) -> ModelRunner:  # noqa: ARG001
    """v0 runner factory — returns a MockRunner that echoes its prompt.

    Production wiring will replace this with a real adapter
    (AnthropicRunner / OpenAIRunner / HTTP shim against the customer
    endpoint). For T-CLOSE the synthetic probe walker never invokes
    ``runner.complete``, so the mock satisfies the ProbeRunFn shape
    without any network traffic. The ``secret`` is dropped on return —
    a real factory would bind it into the runner's auth header.
    """
    del secret
    name = "v0-{}".format(agent_endpoint.replace("https://", "").replace("/", "_"))
    return MockRunner(name, lambda prompt: prompt)


def build_v0_corpus_tarball(dest_dir: Path | None = None) -> Path:
    """Build a single-file corpus tarball for v0 production-wiring smokes.

    The tarball carries one ``manifest.json`` member naming the corpus
    version. ``compute_corpus_snapshot_hash`` is content-addressed (mtime
    /uid/gid/mode/ordering excluded by construction), so repeated builds
    of the same logical corpus collapse to the same ``corpus_snapshot_hash``.
    The hash is computed and returned via :func:`v0_corpus_snapshot_hash`.

    The destination defaults to ``$ARE_V0_CORPUS_DIR`` (or a temp dir).
    """
    base = dest_dir
    if base is None:
        base_env = os.environ.get("ARE_V0_CORPUS_DIR")
        base = Path(base_env) if base_env else Path(tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)
    path = base / "are_v0_corpus.tar.gz"
    payload = b'{"corpus_version":"0.1.0"}\n'
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return path


def v0_corpus_snapshot_hash(corpus_tarball_path: Path | str) -> str:
    """Return the canonical ``sha256:...`` digest of the v0 corpus tarball."""
    return compute_corpus_snapshot_hash(corpus_tarball_path)


# ---------------------------------------------------------------------------
# Prober subprocess target. Module-level so the fork child has a clean
# function reference (no captured lambdas needed; fork inherits the rest).
# ---------------------------------------------------------------------------


def _prober_target(
    *,
    scan_request: ScanRequest,
    vault: CredentialVault,
    channel: WorkerChannel,
    corpus_tarball_path: Any,
    probe_run_fn: ProbeRunFn,
    runner_factory: Callable[[str, str], ModelRunner],
    agent_endpoint_host: str,
    result_queue: Any,
) -> None:
    """Prober subprocess entrypoint.

    Opens :class:`ScopedEgressAllowlist` scoped to the customer's
    ``agent_endpoint`` for the lifetime of the ``run_scan`` call —
    customer-endpoint traffic from ``runner_factory``'s adapter is allowed
    only on the declared scheme/port, and everything else is denied at
    the transport layer. After ``run_scan``
    returns (or raises), the result tuple is enqueued on ``result_queue``
    and the channel's END_OF_SCAN sentinel is best-effort flushed so the
    Scorer's drain loop terminates cleanly on error paths too.

    Error paths push ``("err", type_name, message)`` so the parent can
    raise a typed error rather than time out waiting for a result.
    """
    try:
        with ScopedEgressAllowlist(agent_endpoint_host):
            result = run_scan(
                scan_request,
                vault,
                channel,
                corpus_tarball_path=corpus_tarball_path,
                probe_run_fn=probe_run_fn,
                runner_factory=runner_factory,
            )
        result_queue.put(("ok", result))
    except BaseException as exc:  # noqa: BLE001 — surface every failure
        # ``run_scan`` only flushes END_OF_SCAN on its success path; on
        # any raise the Scorer would otherwise block on recv_row. Flush
        # here so the Scorer drains and the parent's join returns.
        try:
            channel.send_end_of_scan()
        except Exception:  # noqa: BLE001
            pass
        result_queue.put(("err", type(exc).__name__, str(exc)))


# ---------------------------------------------------------------------------
# Factory entry points.
# ---------------------------------------------------------------------------


def make_production_scan_runtime(
    *,
    corpus_tarball_path: Path | str,
    runner_factory: Callable[[str, str], ModelRunner],
    probe_run_fn: ProbeRunFn,
    parent_join_timeout_seconds: int | None = None,
    score_runtime: Any = None,
    score_envelope_registry: dict[str, dict[str, Any]] | None = None,
) -> ScanRuntime:
    """Build a production ``ScanRuntime`` wired to the fork-spawned Prober/Scorer.

    Per-scan lifecycle (executed inside the synchronous ``run_scan_fn``):

    1. Create a fresh :class:`CredentialVault`; register the secret on the
       API-edge side via ``vault.register(credential_ref, secret)``.
    2. Create a :class:`WorkerChannel`.
    3. Spawn the Scorer subprocess via
       :func:`scanners.live_probe_workers.spawn_scorer` with a no-op
       ``on_validated_row`` (v0 scoring sink — Hard Invariant #1 keeps
       the offline path unchanged).
    4. Spawn the Prober subprocess via the channel's fork context. The
       Prober target invokes :func:`scanners.live_probe_orchestrator.run_scan`
       inside a ``ScopedEgressAllowlist(agent_endpoint_host)`` scope and
       pushes the resulting :class:`ScanResult` to a result Queue.
    5. Parent drains the result Queue, joins both subprocesses, calls
       ``vault.forget_all()``, and returns the ``ScanResult``.

    All three callable parameters (``corpus_tarball_path``,
    ``runner_factory``, ``probe_run_fn``) are required and have no
    defaults — the factory is adapter-agnostic by design. Callers that
    want the v0 defaults wire them via :func:`make_default_production_scan_runtime`.
    """
    join_slack = (
        parent_join_timeout_seconds
        if parent_join_timeout_seconds is not None
        else _PARENT_JOIN_SLACK_SECONDS
    )

    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        vault = CredentialVault()
        try:
            # AC#1 step (a) — API-edge registers the secret before the
            # Prober subprocess is spawned. Fork-inherited memory carries
            # the vault into the child via copy-on-write; the secret is
            # never re-serialized across the boundary.
            vault.register(scan_request.credential_ref, secret)

            # AC#1 step (b) — WorkerChannel + fork context.
            channel = WorkerChannel(max_queue=256)
            ctx = channel.context

            # AC#1 step (c) — Scorer subprocess. Deny-all egress posture
            # lives inside ``run_scorer`` for its entire lifetime
            # (Hard Invariant #5).
            #
            # BEAAA-2182 Track A — when ``score_runtime`` was passed in
            # (default flag-on at make_default_production_scan_runtime),
            # the Scorer accumulates validated rows, projects the §2.2
            # envelope on END_OF_SCAN via the fork-inherited
            # ``scanners.score_runtime.ScoreRuntime``, and ships the
            # result back over the WorkerChannel return queue.
            # ``score_inputs`` carries the per-scan inputs the
            # projection needs (everything except accumulated_rows).
            # BEAAA-2182 Track A — when ``score_runtime`` is provided,
            # the Scorer accumulates rows and projects the §2.2 envelope
            # on END_OF_SCAN. Forensic stamps live on ``scan_request``
            # (the orchestrator's ``ScanResult`` reuses them verbatim);
            # ``prior_scan_refs`` is left empty here and the API-edge
            # factory closure injects the real chain at envelope-build
            # time (scan_log query is API-edge state).
            # BEAAA-2182 Track A — Q-A4 confirmed CASE 1 (CTO comment
            # 2c673aa1): production score_inputs MUST NOT carry
            # ``applicability_decisions``. The synthetic-walker
            # production path relies on
            # ``ScoreRuntime.compute_envelope``'s auto-synthesized N/A
            # behavior (kwarg default = None). The kwarg only widens
            # the callee for test/golden fixtures + the CLI offline
            # path (where the CLI passes the golden's explicit
            # applicability.json). If Track B (or any future production
            # work) needs to flip this to case 2, that change requires
            # CTO co-sign + UW op-seat re-review on the v1.5.0 patch
            # (applicability decision provenance becomes a production
            # behavior dial — synthesized vs. customer-supplied).
            # ``tests/test_beaaa2182_a_scorer_wiring.py::TestProductionScoreInputsAreCase1``
            # codifies this contract as a regression test.
            score_inputs: dict[str, Any] | None = None
            if score_runtime is not None:
                score_inputs = {
                    "scan_id": scan_request.scan_id,
                    "agent_id": scan_request.agent_id,
                    "seed": scan_request.seed,
                    "scanner_version": scan_request.scanner_version,
                    "corpus_version": scan_request.corpus_version,
                    "run_timestamp": _utc_now_iso(),
                    "invocation_context": scan_request.invocation_context,
                    "prior_scan_refs": [],
                }
            scorer_proc = spawn_scorer(
                channel=channel,
                on_validated_row=_noop_validated_row,
                score_runtime=score_runtime,
                score_inputs=score_inputs,
                row_recv_timeout_seconds=_scorer_row_recv_timeout(),
            )

            # AC#1 step (d) — Prober subprocess. Fork-multiprocessing so
            # the vault is inherited via copy-on-write rather than
            # pickled (vault.__reduce__ refuses to carry secrets across
            # pickle, so spawn-mode would silently detach the vault).
            result_queue: Any = ctx.Queue(maxsize=1)
            # The orchestrator carries the full URL on ``scan_request
            # .agent_endpoint``; pass that through to the egress scope so
            # scheme/port and DNS-rebind guards bind to the declared endpoint.
            agent_endpoint_host = scan_request.agent_endpoint
            prober_proc = ctx.Process(
                target=_prober_target,
                kwargs={
                    "scan_request": scan_request,
                    "vault": vault,
                    "channel": channel,
                    "corpus_tarball_path": str(corpus_tarball_path),
                    "probe_run_fn": probe_run_fn,
                    "runner_factory": runner_factory,
                    "agent_endpoint_host": agent_endpoint_host,
                    "result_queue": result_queue,
                },
                name="are-prober",
            )
            prober_proc.start()

            # AC#1 step (e) — drain result Queue + join both subprocesses.
            try:
                outcome = result_queue.get(timeout=_orchestrator_join_timeout(join_slack))
            except Exception as exc:  # noqa: BLE001 — Queue.Empty / timeout
                _terminate(prober_proc, scorer_proc)
                raise OrchestratorError(
                    f"prober result queue drain failed: {type(exc).__name__}: {exc}"
                ) from exc

            # BEAAA-2182 Track A — when the Scorer ran with a
            # score_runtime, drain the envelope return channel BEFORE
            # joining so the Scorer's send completes. The envelope is
            # registered in ``score_envelope_registry`` keyed by
            # scan_id so the API-edge score_envelope_factory closure
            # picks it up at kickoff-finalize time. The drain is
            # timeout-bounded separately from the Prober wall-clock cap:
            # envelope projection runs in the Scorer immediately after
            # END_OF_SCAN, so a missing envelope means a broken Scorer,
            # not a still-running probe.
            if score_runtime is not None and score_envelope_registry is not None:
                try:
                    env_outcome = channel.recv_score_envelope(
                        timeout=_scorer_envelope_drain_timeout(scorer_proc)
                    )
                except Exception as exc:  # noqa: BLE001
                    _terminate(prober_proc, scorer_proc)
                    raise OrchestratorError(
                        f"scorer envelope queue drain failed: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
                if env_outcome[0] == "ok":
                    # Defense-in-depth re-validation on the receiver side
                    # — Hard Invariant #4 is not contingent on Scorer
                    # being well-behaved.
                    score_envelope_registry[scan_request.scan_id] = (
                        validate_inbound_envelope(env_outcome[1])
                    )
                else:
                    # ("err", type_name, message) — surface as
                    # OrchestratorError on the kickoff path.
                    _terminate(prober_proc, scorer_proc)
                    raise OrchestratorError(
                        f"scorer subprocess raised {env_outcome[1]}: "
                        f"{env_outcome[2]}"
                    )

            prober_proc.join(timeout=join_slack)
            scorer_proc.join(timeout=join_slack)
            _terminate(prober_proc, scorer_proc)

            if outcome[0] == "ok":
                return outcome[1]
            # outcome == ("err", type_name, message)
            raise OrchestratorError(
                f"prober subprocess raised {outcome[1]}: {outcome[2]}"
            )
        finally:
            # Hard Invariant #4 — vault is dropped on every exit path,
            # success or failure. ``run_scan`` also calls
            # ``vault.forget(cred_ref)`` per credential; ``forget_all``
            # here is the scan-finalize hook.
            vault.forget_all()

    return ScanRuntime(run_scan_fn=run_scan_fn)


def _production_score_envelope_factory(
    runtime_ref: list[ScanRuntime],
    score_envelope_registry: dict[str, dict[str, Any]] | None = None,
) -> Callable[[ScanResult, str, str], dict[str, Any]]:
    """Build the score_envelope_factory per ADR-0017 addendum-1 §2.2.

    Two operating modes per ``ARE_SCORER_REAL`` (Track A flag):

    * **Flag-on (default)** — ``score_envelope_registry`` carries the
      Scorer-projected envelope dict keyed by ``scan_id``, populated by
      ``run_scan_fn`` after draining the WorkerChannel return queue.
      The closure looks up ``registry[result.scan_id]``, injects the
      API-edge ``prior_scan_refs`` (scan_log query is API-edge state),
      and returns the patched envelope. The Scorer's compute_envelope
      already filled every other §2.2 field via the canonical
      ``rubric.scoring.score`` + ``harness.contract_envelope.to_contract_output``
      pipeline, so the dict ships with real scoring values, not
      placeholders.
    * **Flag-off (Tier-1 rollback)** — ``score_envelope_registry`` is
      ``None``. The closure emits the master 52c003e
      ``_phase1_pending`` placeholder envelope so the JCS-canonicalized
      key set remains byte-stable for replay and the snapshot_hash
      seal continues to sign a deterministic domain.

    ``ScoreEnvelopeFactory`` signature is unchanged
    (``(ScanResult, agent_id, run_timestamp) → dict``).
    """

    def factory(
        result: ScanResult, agent_id: str, run_timestamp: str
    ) -> dict[str, Any]:
        runtime = runtime_ref[0] if runtime_ref else None
        prior_rows = (
            runtime.prior_scan_rows(agent_id, exclude_scan_id=result.scan_id)
            if runtime is not None
            else []
        )
        prior_capped, truncated = _apply_prior_scan_cap(prior_rows)
        prior_refs = [_chain_entry_from_row(r) for r in prior_capped]

        # BEAAA-2182 Track A — flag-on path. If the Scorer projected an
        # envelope into the registry, inject the API-edge prior_scan_refs
        # + run_timestamp and return that.
        if score_envelope_registry is not None:
            scorer_envelope = score_envelope_registry.get(result.scan_id)
            if scorer_envelope is not None:
                # Defensive shallow copy so the registry entry is not
                # mutated for later observers (e.g., audit re-reads).
                envelope = dict(scorer_envelope)
                envelope["prior_scan_refs"] = prior_refs
                # The kickoff-finalize timestamp wins; the Scorer-side
                # _utc_now_iso() stamp at spawn is overwritten so the
                # envelope timestamp matches the scan_log row exactly.
                envelope["run_timestamp"] = run_timestamp
                if truncated:
                    envelope["prior_scan_refs_truncated"] = True
                return envelope
            # Registry MISS under flag-on is a programmer error — fall
            # through to the placeholder envelope so persistence doesn't
            # block, but surface the miss to stderr so the next replay
            # check catches it.
            import sys as _sys
            print(
                f"[score_envelope_factory] WARN: registry miss for "
                f"scan_id={result.scan_id} under ARE_SCORER_REAL=on; "
                f"falling back to _phase1_pending envelope.",
                file=_sys.stderr,
            )

        # Flag-off path (or registry miss) — master 52c003e placeholder
        # envelope. Tier-1 rollback target.
        envelope: dict[str, Any] = {
            "agent_id": agent_id,
            "are_total": "_phase1_pending",
            "band": "_phase1_pending",
            "confidence_below_adjudication_threshold": "_phase1_pending",
            "corpus_version": result.corpus_version,
            "evidence_refs": "_phase1_pending",
            "model_versions": "_phase1_pending",
            "partial_completion": "_phase1_pending",
            "prior_scan_refs": prior_refs,
            "purpose": "_phase1_pending",
            "rubric_version": "_phase1_pending",
            "run_timestamp": run_timestamp,
            "scanner_version": result.scanner_version,
            "seed": "_phase1_pending",
            "subscores": "_phase1_pending",
        }
        if truncated:
            envelope["prior_scan_refs_truncated"] = True
        return envelope

    return factory


def make_default_production_scan_runtime() -> ScanRuntime:
    """v0 convenience wrapper — wires the synthetic probe walker, the
    MockRunner factory, and a transient v0 corpus tarball.

    Used by ``api/app.py::create_app`` when ``ARE_PRODUCTION_SCAN_WIRING=1``.

    ADR-0017 addendum-1 wiring (BEAAA-2139 cut-over):

    * ``scan_log`` bound to the Phase 1 EFS-mounted SQLite at
      ``/var/lib/are-scanner/scan_log.sqlite3`` per addendum §2.4 (env
      override ``ARE_SCAN_LOG_PATH`` for pre-flight + future test rigs).
    * ``score_envelope_factory`` produces the addendum §2.2 envelope; see
      :func:`_production_score_envelope_factory`.
    * ``forensic_bundle_uri_factory`` left default per addendum §2.5 —
      ``_default_forensic_bundle_uri`` at ``api/scan_handlers.py``
      already emits the spec URI ``s3://ifa-forensic-bundles/agent-<id>/
      scan-<scan_id>.json``.

    Replace either dependency at call time once an upstream eval framework
    is wrapped under its own rule-execution ticket.
    """
    corpus_path = build_v0_corpus_tarball()

    # BEAAA-2182 Track A — when ARE_SCORER_REAL=on (default), load the
    # Rubric on the parent side once (fork-inherited into the Scorer
    # subprocess; Q-A1 ACCEPT — no per-scan YAML parse) and construct
    # the per-runtime score_envelope_registry. The import is local to
    # this function so the AG-A5 surface check on api/**/*.py does not
    # need to allow ``scanners.score_runtime`` at module-import time on
    # every API-edge load — only when the production wiring activates.
    score_runtime: Any = None
    score_envelope_registry: dict[str, dict[str, Any]] | None = None
    if _scorer_real_enabled():
        from scanners.score_runtime import ScoreRuntime
        rubric_path = Path(__file__).resolve().parents[1] / "rubric" / "rubric.yaml"
        score_runtime = ScoreRuntime.from_rubric_path(rubric_path)
        score_envelope_registry = {}

    # BEAAA-5414 v0.3 — customer-true Track B walker selector. Default
    # off → synthetic_v0 path byte-identical to canon master ``bec8f80``.
    # Flag-on → customer-true walker + HTTP runner factory. The lazy
    # imports keep the AG-A5 ``api/**/*.py`` surface check tight: the
    # customer-true modules are only imported when the flag selects
    # them at production wiring time. CTO co-sign BEAAA-5418 + UW
    # op-seat co-sign BEAAA-5420.
    if _customer_agent_walker_enabled():
        from harness.customer_runner import customer_agent_http_runner_factory
        from scanners.customer_agent_walker import customer_agent_probe_run_fn
        _selected_runner_factory: Callable[[str, str], ModelRunner] = (
            customer_agent_http_runner_factory
        )
        _selected_probe_run_fn: ProbeRunFn = customer_agent_probe_run_fn
    else:
        _selected_runner_factory = v0_mock_runner_factory
        _selected_probe_run_fn = synthetic_v0_probe_run_fn

    runtime = make_production_scan_runtime(
        corpus_tarball_path=corpus_path,
        runner_factory=_selected_runner_factory,
        probe_run_fn=_selected_probe_run_fn,
        score_runtime=score_runtime,
        score_envelope_registry=score_envelope_registry,
    )
    scan_log_path = os.environ.get(
        ARE_SCAN_LOG_PATH_ENV, ARE_SCAN_LOG_DEFAULT_PATH
    )
    # Ensure parent dir exists when caller pointed at a non-prod path; the
    # EFS-mount path is created at infra-provision time and exists in prod.
    parent_dir = Path(scan_log_path).parent
    if str(parent_dir) and str(parent_dir) != ".":
        parent_dir.mkdir(parents=True, exist_ok=True)
    scan_log = ScanLog(scan_log_path)
    # Post-set the persistence bindings on the constructed ScanRuntime so
    # the score_envelope_factory can close over the runtime and project
    # prior_scan_refs at envelope build time per addendum §2.2.
    runtime._scan_log = scan_log
    runtime_ref: list[ScanRuntime] = [runtime]
    runtime._score_envelope_factory = _production_score_envelope_factory(
        runtime_ref,
        score_envelope_registry,
    )
    return runtime


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _extract_host(agent_endpoint: str) -> str:
    """Return the bare host from an ``https://host[/path]`` endpoint.

    The orchestrator's ``ScanRequest.agent_endpoint`` is the full URL
    composed in ``scan_handlers._validate_agent_endpoint``; the egress
    scope's allow-list shape (per T4) is the bare host string.
    """
    url = agent_endpoint
    for prefix in ("https://", "http://"):
        if url.startswith(prefix):
            url = url[len(prefix):]
            break
    # Strip any path component.
    host = url.split("/", 1)[0]
    # Strip any port suffix — egress scope keys on hostname only.
    host = host.split(":", 1)[0]
    return host


def _orchestrator_join_timeout(slack: int) -> int:
    """Total parent-side timeout: orchestrator's wall-clock cap + slack."""
    from scanners.live_probe_orchestrator import MAX_WALL_CLOCK_SECONDS
    return MAX_WALL_CLOCK_SECONDS + max(0, slack)


def _scorer_row_recv_timeout() -> float:
    """Bound scorer-side wait for a row or END_OF_SCAN sentinel."""
    return max(0.1, SCORER_ROW_RECV_TIMEOUT_SECONDS)


def _scorer_envelope_drain_timeout(scorer_proc: Any) -> float:
    """Bound parent-side wait for the Scorer return-channel envelope.

    Dead scorers use the short envelope-drain bound from BEAAA-7840. A live
    scorer may still be inside its own row-receive timeout, so the parent waits
    long enough to receive the scorer's typed error tuple instead of masking it
    with an earlier parent-side Queue.Empty.
    """
    envelope_timeout = max(0.1, SCORER_ENVELOPE_DRAIN_TIMEOUT_SECONDS)
    try:
        if scorer_proc.is_alive():
            return max(envelope_timeout, _scorer_row_recv_timeout() + 1.0)
    except Exception:  # noqa: BLE001
        pass
    return envelope_timeout


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with ``Z`` suffix.

    Used to stamp ``run_timestamp`` on the score_inputs the Scorer
    receives at spawn time. The API-edge factory closure may overwrite
    this when finalizing the envelope at kickoff-finalize time so the
    timestamp matches the scan_log row exactly.
    """
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _terminate(*procs: Any) -> None:
    """Best-effort terminate / close on subprocess handles.

    Idempotent: ``terminate`` on an already-exited process is a no-op;
    ``close`` after terminate releases the OS handle. Wrapped in
    try/except because the multiprocessing surface raises in edge cases
    (e.g. closing a process that never started).
    """
    for proc in procs:
        try:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.close()
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "ARE_WALKER_CUSTOMER_AGENT_ENV",
    "build_v0_corpus_tarball",
    "make_default_production_scan_runtime",
    "make_production_scan_runtime",
    "synthetic_v0_probe_run_fn",
    "v0_corpus_snapshot_hash",
    "v0_mock_runner_factory",
]
