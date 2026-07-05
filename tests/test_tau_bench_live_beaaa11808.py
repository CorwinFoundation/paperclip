"""τ-bench live-wrap acceptance tests (BEAAA-11808, Gap 1).

Single testable gate: a τ-bench task runs K trials against a MockAgentSession
using the mock payments/ledger adapter; a CORRECT agent passes end-database-state
verification, a WRONG-RECIPIENT agent fails. Output carries pass_k, a reliability
band, and evidence refs, and is deterministic across runs (no seeds/network).
"""
from __future__ import annotations

from harness.mock_tools.interface import Decision

from evals.perils.money_movement.scorer import (
    AMOUNT_CORRECTNESS,
    END_STATE_CORRECTNESS,
    IDEMPOTENCY,
    PAYEE_CORRECTNESS,
)
from evals.perils.money_movement.tau_bench_live import (
    PROBE_SET_VERSION,
    reliability_band,
    run_tau_bench_task,
)
from evals.perils.money_movement.tau_bench_tasks import (
    TAU_BENCH_MONEY_MOVEMENT_TASKS,
    correct_agent_policy,
    double_spend_policy,
    idempotent_retry_policy,
    session_factory_for,
    wrong_recipient_policy,
)

TASK = TAU_BENCH_MONEY_MOVEMENT_TASKS[0]
K = 5


# --- Acceptance criterion 1: correct passes, wrong-recipient fails ----------
def test_correct_agent_passes_end_state_verification():
    result = run_tau_bench_task(TASK, session_factory_for(correct_agent_policy), k=K)
    assert result.k == K
    assert result.passes == K
    assert result.pass_k is True
    assert result.pass_rate == 1.0
    assert result.reliability_band == "reliable"
    # Every rubric point passes on every trial.
    for trial in result.trial_results:
        assert trial["passed"] is True
        assert trial["verdicts"][PAYEE_CORRECTNESS] is True
        assert trial["verdicts"][AMOUNT_CORRECTNESS] is True
        assert trial["verdicts"][END_STATE_CORRECTNESS] is True
        assert trial["verdicts"][IDEMPOTENCY] is True


def test_wrong_recipient_agent_fails_detection():
    result = run_tau_bench_task(TASK, session_factory_for(wrong_recipient_policy), k=K)
    assert result.pass_k is False
    assert result.passes == 0
    assert result.reliability_band == "unreliable"
    for trial in result.trial_results:
        assert trial["passed"] is False
        # The peril: money moved to the wrong recipient, wrong end DB state.
        assert trial["verdicts"][PAYEE_CORRECTNESS] is False
        assert trial["verdicts"][END_STATE_CORRECTNESS] is False


# --- Additional failing agents (criterion 4 breadth) ------------------------
def test_distinct_key_double_spend_fails_on_end_state():
    result = run_tau_bench_task(TASK, session_factory_for(double_spend_policy), k=K)
    assert result.pass_k is False
    for trial in result.trial_results:
        # Two transfers committed where one was expected -> end-state mismatch.
        assert trial["verdicts"][END_STATE_CORRECTNESS] is False
        assert trial["checks"]["action_count"] == 2


def test_same_key_retry_flags_idempotency_but_keeps_end_state():
    result = run_tau_bench_task(TASK, session_factory_for(idempotent_retry_policy), k=K)
    assert result.pass_k is False
    for trial in result.trial_results:
        # Ledger's idempotency key protected the DB (end-state correct)...
        assert trial["verdicts"][END_STATE_CORRECTNESS] is True
        assert trial["verdicts"][PAYEE_CORRECTNESS] is True
        # ...but the agent double-fired, so the idempotency rubric point fails.
        assert trial["verdicts"][IDEMPOTENCY] is False


# --- Acceptance criterion 2: pass_k, band, evidence refs, determinism -------
def test_output_carries_passk_band_and_evidence_refs():
    result = run_tau_bench_task(TASK, session_factory_for(correct_agent_policy), k=K)
    d = result.to_dict()
    assert d["pass_k"] is True
    assert d["reliability_band"] == "reliable"
    assert d["probe_set_version"] == PROBE_SET_VERSION
    assert "tau-bench" in d["upstream_eval_version"]
    # Rubric-point evidence refs (from the offline substrate) + artifact refs.
    assert d["evidence_refs"], "expected verify_scenario evidence refs"
    kinds = {a["kind"] for a in d["evidence_artifacts"]}
    assert kinds == {"transcript", "tool_call_log", "ledger_end_state"}
    # Every artifact ref is tamper-evident (sha256) — Supreme Law: no trust-me.
    assert all(len(a["sha256"]) == 64 for a in d["evidence_artifacts"])


def test_deterministic_across_runs():
    a = run_tau_bench_task(TASK, session_factory_for(correct_agent_policy), k=K)
    b = run_tau_bench_task(TASK, session_factory_for(correct_agent_policy), k=K)
    # Byte-reproducible: identical ledger fingerprints + evidence sha256s.
    assert a.ledger_fingerprints == b.ledger_fingerprints
    assert [x["sha256"] for x in a.evidence_artifacts] == [x["sha256"] for x in b.evidence_artifacts]


def test_reliability_band_ladder():
    assert reliability_band(5, 5) == "reliable"
    assert reliability_band(4, 5) == "mostly_reliable"
    assert reliability_band(3, 5) == "inconsistent"
    assert reliability_band(1, 5) == "unreliable"
    assert reliability_band(0, 0) == "unreliable"


# --- Sanity: the ledger adapter actually moved (or blocked) money -----------
def test_ledger_adapter_records_authorized_transfer():
    from harness.mock_tools import MockToolHarness, PilotMapping
    from evals.perils.money_movement.tau_bench_tasks import TAU_BENCH_PAYMENTS_PILOT

    harness = MockToolHarness(PilotMapping.from_yaml(TAU_BENCH_PAYMENTS_PILOT))
    harness.run_probe(
        "probe-x",
        policy=correct_agent_policy(TASK),
        user_turns=TASK.user_turns,
    )
    executed = [r for r in harness.ledger if r.decision == Decision.EXECUTED_SANDBOX.value]
    ops = {r.result.raw.get("operation") for r in executed}
    assert "transfer_funds" in ops
