"""BEAAA-12030 multi-turn behavioral golden fixtures — release-gate coverage.

These fixtures drive the SAI multi-turn runner, the mock-tool harness, the
end-state verifiers, and the behavioral emitters (tau-bench + STOP) — paths the
single-turn golden N=5 gate never touches. They assert evidence-row / end-state
stability and the ADR-0045 `behavioral_integrity.scored: false` invariant, NOT
`are_total`.
"""
from __future__ import annotations

from scripts import behavioral_golden_fixtures as bgf


def test_behavioral_gate_passes_and_covers_all_runner_paths():
    report = bgf.run_behavioral_gate()
    assert report["passed"] is True
    ids = {fx["fixture_id"] for fx in report["fixtures"]}
    # Coverage: tau-bench correct / wrong-recipient / idempotency + STOP compliant / non-compliant.
    assert ids == {
        "BG-TAU-CORRECT",
        "BG-TAU-WRONG-RECIPIENT",
        "BG-TAU-IDEMPOTENCY",
        "BG-STOP-COMPLIANT",
        "BG-STOP-NONCOMPLIANT",
    }
    for fx in report["fixtures"]:
        assert fx["passed"] is True, fx
        assert fx["reproducible"] is True
        assert fx["matches_golden"] is True
        assert fx["scored_invariant_ok"] is True
        assert not fx["errors"]


def test_every_fixture_is_byte_reproducible_across_outer_n():
    # The whole roster must be exactly reproducible run-to-run (deterministic,
    # no seeds/network) — this is the behavioral analogue of the N=5 spread<=1 band.
    for fx in bgf.BEHAVIORAL_ROSTER:
        first = fx.observe()
        assert fx.observe() == first, f"{fx.fixture_id} not byte-reproducible"


def test_behavioral_integrity_scored_stays_false():
    # ADR-0045 versioned invariant: STOP fixtures emit a behavioral_integrity block
    # that is reported-not-scored; tau fixtures emit no block (scored is None).
    stop_obs = bgf._observe_stop(bgf._stop_compliant_policy)
    assert stop_obs["scored"] is False
    tau_obs = bgf._observe_tau(
        __import__(
            "evals.perils.money_movement.tau_bench_tasks", fromlist=["correct_agent_policy"]
        ).correct_agent_policy
    )
    assert tau_obs["scored"] is None


def test_gate_blocks_when_a_fixture_diverges_from_golden():
    # A regression that changes observed behavior must flip the fixture to BLOCK.
    tampered = bgf.BehavioralFixture(
        fixture_id="BG-TAU-CORRECT-TAMPERED",
        runner_path="test",
        description="frozen golden deliberately wrong to prove the gate bites",
        observe=lambda: bgf._observe_tau(
            __import__(
                "evals.perils.money_movement.tau_bench_tasks",
                fromlist=["correct_agent_policy"],
            ).correct_agent_policy
        ),
        expected={"pass_k": False},  # real observation is pass_k True -> must BLOCK
    )
    result = bgf.run_behavioral_fixture(tampered)
    assert result.passed is False
    assert result.matches_golden is False
    assert result.errors


def test_scored_true_would_block_the_gate():
    # Guard the invariant directly: if behavioral_integrity.scored ever came back
    # true, the fixture must fail (it would mean the block entered the score path).
    fixture = bgf.BehavioralFixture(
        fixture_id="BG-SCORED-TRUE-GUARD",
        runner_path="test",
        description="synthetic scored:true observation must block",
        observe=lambda: {"kind": "stop", "signal": "x", "scored": True},
        expected={"kind": "stop"},
    )
    result = bgf.run_behavioral_fixture(fixture)
    assert result.scored_invariant_ok is False
    assert result.passed is False


def test_markdown_section_renders_table_and_result():
    report = bgf.run_behavioral_gate()
    md = "\n".join(bgf.render_markdown_section(report))
    assert "Multi-Turn Behavioral Golden Fixtures" in md
    assert "behavioral_integrity.scored: false" in md
    assert "BG-STOP-NONCOMPLIANT" in md
    assert "PASS" in md
