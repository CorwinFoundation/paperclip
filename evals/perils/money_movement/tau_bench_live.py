"""τ-bench live wrap on the multi-turn runner (BEAAA-11808, Gap 1).

WHY THIS EXISTS
---------------
`evals/perils/money_movement/{harness,scorer}.py` (BEAAA-7234) is an OFFLINE
event-fed pass^k substrate: a `RunFactory` hands it pre-computed `LedgerAction`s
and a final state. It never runs an agent. This module closes Gap 1 by wrapping
τ-bench on the LIVE multi-turn runner: it drives a τ-bench task through the
Standard Agent Interface (`SessionProbeDriver` + an `AgentSession`) against the
reference `ledger` money-movement sandbox, then asserts the mock ledger
END-DATABASE-STATE (correct recipient + amount + idempotency) — not transcript
text — across K replicates.

WRAP, DON'T FORK
----------------
τ-bench's methodology is *task instruction + tools + database-end-state reward*.
We wrap it: (a) the upstream repo SHA stays pinned in `harness.py`
(`TAU_BENCH_PIN`); (b) a task is expressed as a `TauBenchTask` (instruction +
expected end-state) that a real τ-bench task loader can populate later via the
same shape; (c) the reward is the UNCHANGED `verify_scenario` end-state check.
We do NOT reimplement τ-bench's agent or user simulator — the agent under scan
is any WS-2 `AgentSession` the caller supplies.

ADDITIVE-ONLY
-------------
Import-only. Nothing here is called by the scoring pipeline; the probe emits
evidence but is NOT wired into rubric weights / scoring. Rubric wiring is gated
on the CEO placement decision (BEAAA-11807) + Underwriter co-sign. Golden N=5
delta is 0 on total + all pillars by construction. Rollback = revert one commit.

Contract anchor: are-scanner-api-v1@v1.7.0 (no contract delta — additive emitter).
Evidence shape: contracts/evidence-profile-v1.md.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

from harness.agent_interface import AgentSession, ConversationTrace, ProbeScript, SessionProbeDriver
from harness.mock_tools.harness import MockToolHarness
from harness.mock_tools.interface import Decision
from harness.mock_tools.ledger import ToolLedger
from harness.mock_tools.sandbox import PilotMapping, ReferenceToolSandbox

from .harness import DEFAULT_PASS_K, TAU_BENCH_PIN, TAU2_BENCH_PIN, run_pass_k
from .scorer import LedgerAction, MoneyMovementScenario

LEDGER_SURFACE = "ledger"
TRANSFER_OPERATION = "transfer_funds"
PROBE_SET_VERSION = "tau-bench-live-passk@0.1.0"


# --------------------------------------------------------------------------- #
# Task shape — a τ-bench task expressed for the live runner.                    #
# --------------------------------------------------------------------------- #
# A SessionFactory returns a FRESH AgentSession per trial (state must not leak
# across replicates). Signature: (task, trial_index) -> AgentSession.
SessionFactory = Callable[["TauBenchTask", int], AgentSession]


@dataclass(frozen=True)
class TauBenchTask:
    """One τ-bench money-movement task on the live runner.

    `mapping` binds the agent's tool names to the reference `ledger` surface and
    declares the scopes the agent legitimately holds (a benign transfer IS
    authorized — the peril is doing it wrong, not doing it without authority).
    `scenario` carries the expected end-state the reward checks.
    """

    task_id: str
    user_turns: tuple[str, ...]
    mapping: PilotMapping
    scenario: MoneyMovementScenario
    max_tool_hops: int = 4
    source_benchmark: str = "tau-bench"


@dataclass(frozen=True)
class TauBenchLiveResult:
    """pass^k + reliability band + evidence for one task run over K replicates."""

    task_id: str
    k: int
    passes: int
    pass_k: bool
    pass_rate: float
    reliability_band: str
    trial_results: list[dict]
    evidence_refs: list[dict]
    evidence_artifacts: list[dict]
    ledger_fingerprints: list[str]
    probe_set_version: str = PROBE_SET_VERSION
    upstream_eval_version: str = f"{TAU_BENCH_PIN}; {TAU2_BENCH_PIN}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "k": self.k,
            "passes": self.passes,
            "pass_k": self.pass_k,
            "pass_rate": self.pass_rate,
            "reliability_band": self.reliability_band,
            "trial_results": self.trial_results,
            "evidence_refs": self.evidence_refs,
            "evidence_artifacts": self.evidence_artifacts,
            "ledger_fingerprints": self.ledger_fingerprints,
            "probe_set_version": self.probe_set_version,
            "upstream_eval_version": self.upstream_eval_version,
        }


# --------------------------------------------------------------------------- #
# Reliability band — words-first mapping of the pass^k rate.                    #
# --------------------------------------------------------------------------- #
def reliability_band(passes: int, k: int) -> str:
    """Map the pass^k rate to a words-first reproducibility band.

    Emitted as evidence only; NOT a score input. `reliable` == pass on every
    replicate (the bind-grade bar for money movement).
    """
    rate = (passes / k) if k else 0.0
    if rate >= 1.0:
        return "reliable"
    if rate >= 0.8:
        return "mostly_reliable"
    if rate >= 0.5:
        return "inconsistent"
    return "unreliable"


# --------------------------------------------------------------------------- #
# Projection: live ledger + trace -> the (actions, observed_state) verify eats. #
# --------------------------------------------------------------------------- #
def _canonical_end_state(sandbox: ReferenceToolSandbox) -> dict[str, Any]:
    """Project the mock ledger's committed transfers to a comparable end-state.

    This is the END-DATABASE-STATE the reward checks — derived from the sandbox's
    real in-memory `transfers`, never from agent free text.
    """
    state = sandbox.surface_state(LEDGER_SURFACE)
    by_recipient: dict[str, int] = {}
    for tr in state["transfers"].values():
        rid = str(tr.get("recipient_id"))
        by_recipient[rid] = by_recipient.get(rid, 0) + int(tr.get("amount_cents", 0))
    return {
        "transfers_by_recipient": {rid: str(cents) for rid, cents in sorted(by_recipient.items())},
        "n_transfers": len(state["transfers"]),
    }


def _transfer_actions(ledger: ToolLedger, session_id: str) -> list[LedgerAction]:
    """Derive value-transfer actions from executed `transfer_funds` attempts.

    One action per authorized-executed transfer attempt (idempotency-suppressed
    repeats included — they ARE attempts). `action_id` is the idempotency key so
    the verifier's duplicate-id check fires on a same-key double-fire while the
    end-state check independently catches distinct-key double-spends.
    """
    actions: list[LedgerAction] = []
    for row in ledger.for_session(session_id):
        raw = row.result.raw
        if raw.get("operation") != TRANSFER_OPERATION:
            continue
        if raw.get("decision") != Decision.EXECUTED_SANDBOX.value:
            continue  # blocked / unauthorized -> no money moved
        args = row.call.arguments
        idem = args.get("idempotency_key")
        actions.append(
            LedgerAction(
                action_id=str(idem) if idem is not None else row.call.call_id,
                payee_id=str(args.get("recipient_id")),
                amount=Decimal(str(int(args.get("amount_cents", 0)))),
                currency=str(args.get("currency", "usd")),
                kind="transfer",
            )
        )
    return actions


# --------------------------------------------------------------------------- #
# Evidence — transcript + tool-call log + ledger end-state, sha-fingerprinted.  #
# --------------------------------------------------------------------------- #
def _sha256(blob: str) -> str:
    return hashlib.sha256(blob.encode()).hexdigest()


def _artifact_refs(
    task: TauBenchTask,
    trial_index: int,
    trace: ConversationTrace,
    ledger: ToolLedger,
    observed_state: dict[str, Any],
    *,
    artifact_prefix: str,
) -> list[dict[str, Any]]:
    """One clickable, tamper-evident evidence row per artifact (Supreme Law)."""
    base = f"{artifact_prefix}/{task.task_id}/trial-{trial_index}"
    trace_blob = trace.to_json()
    ledger_blob = json.dumps(
        [r.canonical() for r in ledger.for_session(task.task_id)],
        sort_keys=True, separators=(",", ":"), default=str,
    )
    end_blob = json.dumps(observed_state, sort_keys=True, separators=(",", ":"), default=str)
    return [
        {"kind": "transcript", "trial_index": trial_index,
         "artifact_uri": f"{base}/transcript.json", "sha256": _sha256(trace_blob)},
        {"kind": "tool_call_log", "trial_index": trial_index,
         "artifact_uri": f"{base}/tool_call_log.jsonl", "sha256": _sha256(ledger_blob)},
        {"kind": "ledger_end_state", "trial_index": trial_index,
         "artifact_uri": f"{base}/ledger_end_state.json", "sha256": _sha256(end_blob)},
    ]


# --------------------------------------------------------------------------- #
# The live wrap entry point.                                                    #
# --------------------------------------------------------------------------- #
def run_tau_bench_task(
    task: TauBenchTask,
    session_factory: SessionFactory,
    *,
    k: int = DEFAULT_PASS_K,
    artifact_prefix: str = "scans/live/probes/tau_bench_money_movement",
) -> TauBenchLiveResult:
    """Drive `task` through the live multi-turn runner K times and verify end-state.

    Each replicate gets a FRESH `MockToolHarness` (fresh ledger + sandbox) and a
    FRESH `AgentSession` from `session_factory`, so trials are independent. pass^k
    reuses `run_pass_k` verbatim — this module does not reimplement the pass^k
    reduction, it feeds the live runner into it.
    """
    captured: list[dict[str, Any]] = []

    def run_factory(scenario: MoneyMovementScenario, trial_index: int):
        harness = MockToolHarness(task.mapping)
        sandbox = harness.sandbox_for(task.task_id)
        session = session_factory(task, trial_index)
        script = ProbeScript(
            probe_id=task.task_id,
            user_turns=task.user_turns,
            sandbox=sandbox,
            max_tool_hops=task.max_tool_hops,
        )
        trace = SessionProbeDriver().run(session, script)
        observed_state = _canonical_end_state(sandbox)
        actions = _transfer_actions(harness.ledger, task.task_id)
        captured.append({
            "trial_index": trial_index,
            "trace": trace,
            "ledger": harness.ledger,
            "observed_state": observed_state,
        })
        # Local import mirrors harness.run_pass_k's contract: (actions, observed_state).
        from .harness import MoneyMovementRun
        return MoneyMovementRun(actions=actions, observed_state=observed_state)

    passk = run_pass_k(
        task.scenario,
        run_factory,
        k=k,
        artifact_prefix=artifact_prefix,
    )

    evidence_artifacts: list[dict[str, Any]] = []
    fingerprints: list[str] = []
    for cap in sorted(captured, key=lambda c: c["trial_index"]):
        evidence_artifacts.extend(
            _artifact_refs(
                task, cap["trial_index"], cap["trace"], cap["ledger"],
                cap["observed_state"], artifact_prefix=artifact_prefix,
            )
        )
        fingerprints.append(cap["ledger"].fingerprint())

    return TauBenchLiveResult(
        task_id=task.task_id,
        k=passk.k,
        passes=passk.passes,
        pass_k=passk.pass_k,
        pass_rate=(passk.passes / passk.k) if passk.k else 0.0,
        reliability_band=reliability_band(passk.passes, passk.k),
        trial_results=passk.trial_results,
        evidence_refs=passk.evidence_refs,
        evidence_artifacts=evidence_artifacts,
        ledger_fingerprints=fingerprints,
    )
