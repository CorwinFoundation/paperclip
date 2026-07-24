from __future__ import annotations

from scripts import release_gate_n5


def _payload(seed: int, total: float = 80.0, gov: float = 20.0) -> dict:
    return {
        "scanner_version": "0.4.1",
        "corpus_version": "path_a_v2_1@2026-05-15",
        "model_versions": {"primary": "fixture"},
        "seed": seed,
        "are_total": total,
        "subscores": {
            "GOVERNANCE": gov,
            "ROBUSTNESS": 20.0,
            "BIAS_FAIRNESS": 20.0,
            "OPS": 20.0,
        },
        "evidence_refs": [{"rubric_point_id": "P1.1", "artifact_uri": "file://fixture"}],
    }


def test_contract_payload_requires_all_release_gate_fields():
    errors = release_gate_n5._validate_contract_payload(
        {"seed": 0, "subscores": {}, "evidence_refs": []},
        agent_id="G1",
        seed=0,
    )

    assert "G1/seed-0: missing scanner_version" in errors
    assert "G1/seed-0: missing are_total" in errors
    assert "G1/seed-0: missing subscore GOVERNANCE" in errors
    assert "G1/seed-0: evidence_refs missing or empty" in errors


def test_contract_payload_rejects_blank_corpus_version():
    payload = _payload(seed=0)
    payload["corpus_version"] = ""

    errors = release_gate_n5._validate_contract_payload(payload, agent_id="B1", seed=0)

    assert "B1/seed-0: corpus_version required (Supreme Law pin)" in errors


def test_agent_result_blocks_candidate_spread_over_one():
    result = release_gate_n5.AgentGateResult(
        agent_id="G1",
        baseline_values={
            "total": 80.0,
            "GOVERNANCE": 20.0,
            "ROBUSTNESS": 20.0,
            "BIAS_FAIRNESS": 20.0,
            "OPS": 20.0,
        },
        candidate_values=[
            {"total": 80.0, "GOVERNANCE": 20.0, "ROBUSTNESS": 20.0, "BIAS_FAIRNESS": 20.0, "OPS": 20.0},
            {"total": 81.2, "GOVERNANCE": 20.0, "ROBUSTNESS": 20.0, "BIAS_FAIRNESS": 20.0, "OPS": 20.0},
        ],
    )

    assert result.candidate_spread["total"] == 1.2000000000000028
    assert not result.passed


def test_agent_result_treats_development_delta_as_informational_with_zero_spread():
    baseline = {
        "total": 80.0,
        "GOVERNANCE": 20.0,
        "ROBUSTNESS": 20.0,
        "BIAS_FAIRNESS": 20.0,
        "OPS": 20.0,
    }
    candidate = dict(baseline, total=84.0, ROBUSTNESS=24.0)
    result = release_gate_n5.AgentGateResult(
        agent_id="G3",
        baseline_values=baseline,
        candidate_values=[candidate] * 5,
    )

    assert result.candidate_spread["total"] == 0
    assert result.deltas["total"] == 4
    assert result.deltas["ROBUSTNESS"] == 4
    assert result.passed


def test_non_development_phase_requires_approved_policy(tmp_path):
    try:
        release_gate_n5.run_gate(
            out_dir=tmp_path,
            baseline_label="baseline",
            candidate_label="candidate",
            phase="production",
        )
    except ValueError as exc:
        assert "explicitly approved review policy" in str(exc)
    else:
        raise AssertionError("production phase must fail closed without approved policy")


def test_render_markdown_uses_required_delta_table_columns():
    report = {
        "contract": "are-scanner-api-v1@v1.0",
        "phase": "development",
        "development_drift_disposition": "informational_non_blocking",
        "baseline": "0.4.0",
        "candidate": "0.4.1",
        "claim": "8/9 baselined",
        "full_roster": ["G1", "B2"],
        "roster": ["G1"],
        "excluded": {"B2": "approved v1 exclusion"},
        "seeds": [0, 1, 2, 3, 4],
        "spread_tolerance": 1.0,
        "passed": True,
        "agents": [
            {
                "agent": "G1",
                "passed": True,
                "errors": [],
                "delta": {
                    "total": 0.0,
                    "GOVERNANCE": 0.0,
                    "ROBUSTNESS": 0.0,
                    "BIAS_FAIRNESS": 0.0,
                    "OPS": 0.0,
                },
                "spread": {
                    "baseline": {
                        "total": 0.0,
                        "GOVERNANCE": 0.0,
                        "ROBUSTNESS": 0.0,
                        "BIAS_FAIRNESS": 0.0,
                        "OPS": 0.0,
                    },
                    "candidate": {
                        "total": 0.0,
                        "GOVERNANCE": 0.0,
                        "ROBUSTNESS": 0.0,
                        "BIAS_FAIRNESS": 0.0,
                        "OPS": 0.0,
                    },
                },
            }
        ],
    }

    markdown = release_gate_n5._render_markdown(report)

    assert "| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | spread (baseline) | spread (candidate) |" in markdown
    assert "| G1 | 0 | 0 | 0 | 0 | 0 | total=0" in markdown
    assert "Claim: `8/9 baselined`" in markdown
    assert "B2 is explicitly excluded from the v1 gate" in markdown


def test_v1_gate_excludes_b2_but_keeps_full_roster_reference():
    assert "B2" in release_gate_n5.FULL_ROSTER
    assert "B2" not in release_gate_n5.ROSTER
    assert set(release_gate_n5.ROSTER) == {"G1", "G2", "G3", "M1", "M2", "M3", "B1", "B3"}
    assert release_gate_n5.EXCLUDED_FIXTURES["B2"].startswith("BEAAA-10199")
