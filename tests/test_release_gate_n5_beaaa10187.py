from __future__ import annotations

import json

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


def test_agent_result_blocks_stable_candidate_delta_over_one():
    result = release_gate_n5.AgentGateResult(
        agent_id="G3",
        baseline_values={
            "total": 73.0,
            "GOVERNANCE": 22.3958,
            "ROBUSTNESS": 18.75,
            "BIAS_FAIRNESS": 12.5,
            "OPS": 19.7917,
        },
        candidate_values=[
            {"total": 77.0, "GOVERNANCE": 22.3958, "ROBUSTNESS": 22.5, "BIAS_FAIRNESS": 12.5, "OPS": 19.7917}
            for _ in release_gate_n5.SEEDS
        ],
    )

    assert result.spread_passed
    assert not result.delta_passed
    assert not result.passed


def test_agent_result_isolates_approved_rule_change_delta():
    result = release_gate_n5.AgentGateResult(
        agent_id="G3",
        baseline_values={
            "total": 73.0,
            "GOVERNANCE": 22.3958,
            "ROBUSTNESS": 18.75,
            "BIAS_FAIRNESS": 12.5,
            "OPS": 19.7917,
        },
        approved_rule_change_deltas={
            "total": 4.0,
            "GOVERNANCE": 0.0,
            "ROBUSTNESS": 3.125,
            "BIAS_FAIRNESS": 0.0,
            "OPS": 0.0,
        },
        candidate_values=[
            {"total": 77.0, "GOVERNANCE": 22.3958, "ROBUSTNESS": 22.5, "BIAS_FAIRNESS": 12.5, "OPS": 19.7917}
            for _ in release_gate_n5.SEEDS
        ],
    )

    assert result.raw_deltas["total"] == 4.0
    assert result.raw_deltas["ROBUSTNESS"] == 3.75
    assert result.deltas["total"] == 0.0
    assert result.deltas["ROBUSTNESS"] == 0.625
    assert result.delta_passed


def test_render_markdown_uses_required_delta_table_columns():
    report = {
        "contract": "are-scanner-api-v1@v1.0",
        "scanner_version": "0.6.0",
        "probe-set-version": "artifacts/beaaa-387/manifest.json",
        "rubric_point_id": [
            "GOVERNANCE.PII.HANDLING_ATTESTATION",
            "ROBUSTNESS.PII.SCAN_TIME_DETECTION",
        ],
        "cto_cosign_ref": "BEAAA-14867",
        "baseline": "0.4.0",
        "candidate": "0.4.1",
        "claim": "8/9 baselined",
        "full_roster": ["G1", "B2"],
        "roster": ["G1"],
        "excluded": {"B2": "approved v1 exclusion"},
        "seeds": [0, 1, 2, 3, 4],
        "spread_tolerance": 1.0,
        "delta_tolerance": 1.0,
        "single_turn_spread_passed": True,
        "single_turn_delta_passed": True,
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
                "raw_delta": {
                    "total": 0.0,
                    "GOVERNANCE": 0.0,
                    "ROBUSTNESS": 0.0,
                    "BIAS_FAIRNESS": 0.0,
                    "OPS": 0.0,
                },
                "approved_rule_change_delta": {
                    "total": 0.0,
                    "GOVERNANCE": 0.0,
                    "ROBUSTNESS": 0.0,
                    "BIAS_FAIRNESS": 0.0,
                    "OPS": 0.0,
                },
            }
        ],
    }

    markdown = release_gate_n5._render_markdown(report)

    assert "| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | spread (baseline) | spread (candidate) |" in markdown
    assert "| G1 | 0 | 0 | 0 | 0 | 0 | total=0" in markdown
    assert "Claim: `8/9 baselined`" in markdown
    assert "B2 is explicitly excluded from the v1 gate" in markdown
    assert "Approved Rule-Change Isolation" in markdown


def test_run_gate_writes_sibling_json_by_default(monkeypatch, tmp_path):
    baseline_root = tmp_path / "baselines"
    agent_root = baseline_root / "G1"
    agent_root.mkdir(parents=True)
    (baseline_root / "manifest.json").write_text(
        json.dumps({"fixtures": {"G1": {"baseline_persisted": True}}}),
        encoding="utf-8",
    )
    (agent_root / "baseline.json").write_text(
        json.dumps(
            {
                "bmi": 80.0,
                "pillars": {
                    "GOVERNANCE": {"score": 20.0},
                    "ROBUSTNESS": {"score": 20.0},
                    "BIAS_FAIRNESS": {"score": 20.0},
                    "OPS": {"score": 20.0},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(release_gate_n5, "BASELINES", baseline_root)
    monkeypatch.setattr(release_gate_n5, "ROSTER", ("G1",))
    monkeypatch.setattr(release_gate_n5, "SEEDS", (0,))
    monkeypatch.setattr(release_gate_n5, "APPROVED_MOVEMENT_SOURCES", ())
    monkeypatch.setattr(release_gate_n5.behavioral_golden_fixtures, "run_behavioral_gate", lambda: {"passed": True})
    monkeypatch.setattr(release_gate_n5.behavioral_golden_fixtures, "render_markdown_section", lambda _report: ["behavioral"])

    def fake_prepare_input_dir(agent_id, tmp_root):
        path = tmp_root / "inputs" / agent_id
        path.mkdir(parents=True)
        return path

    def fake_run_cli(agent_id, seed, input_dir, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = _payload(seed=seed, total=80.0)
        payload["corpus_version"] = release_gate_n5.RELEASE_GATE_CORPUS_VERSION
        output_path.write_text(json.dumps(payload), encoding="utf-8")
        return 0, "", ""

    monkeypatch.setattr(release_gate_n5, "_prepare_input_dir", fake_prepare_input_dir)
    monkeypatch.setattr(release_gate_n5, "_run_cli", fake_run_cli)

    report, md_path = release_gate_n5.run_gate(
        manifest_path=baseline_root / "manifest.json",
        out_dir=tmp_path / "out",
        baseline_label="baseline",
        candidate_label="candidate",
        today="2026-07-12",
    )

    json_path = md_path.with_suffix(".json")
    assert report["passed"] is True
    assert md_path.exists()
    assert json_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["passed"] is True


def test_v1_gate_excludes_b2_but_keeps_full_roster_reference():
    assert "B2" in release_gate_n5.FULL_ROSTER
    assert "B2" not in release_gate_n5.ROSTER
    assert set(release_gate_n5.ROSTER) == {"G1", "G2", "G3", "M1", "M2", "M3", "B1", "B3"}
    assert release_gate_n5.EXCLUDED_FIXTURES["B2"].startswith("BEAAA-10199")
