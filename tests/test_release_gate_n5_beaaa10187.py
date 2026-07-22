from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

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


def _movement_source(*, movement=4.0, decisions=None):
    return {
        "source_ticket": "BEAAA-16700",
        "commits": ["deadbee"],
        "approval_decisions": decisions or [],
        "deltas": {"G3": {"total": movement}},
    }


def _approval_decisions(*roles):
    identities = {
        "CTO": ("agent", "88e68019-1cb6-4c4f-83e1-9fd340fb6821"),
        "Underwriter": ("agent", "afa65747-4323-419c-9850-bdb77711648f"),
        "Founder": ("user", "local-board"),
    }
    return [
        {
            "role": role,
            "approver_id": identities[role][1],
            "decision_ref": f"BEAAA-16700#comment-{role.lower()}-approved",
            "source_author": {"kind": identities[role][0], "id": identities[role][1]},
            "decision_markers": [f"{role} approved"],
        }
        for role in roles
    ]


def test_approval_provenance_rejects_ticket_label_without_decisions():
    errors = release_gate_n5._validate_approved_movement_sources(
        (_movement_source(movement=4.0),)
    )

    assert errors
    assert "missing required approval roles" in errors[0]


def test_approval_provenance_rejects_self_reference():
    decisions = _approval_decisions("CTO", "Underwriter", "Founder")
    decisions[0]["decision_ref"] = "BEAAA-16700"

    errors = release_gate_n5._validate_approved_movement_sources(
        (_movement_source(movement=4.0, decisions=decisions),)
    )

    assert any("self-referential" in error for error in errors)


def test_approval_provenance_rejects_misattributed_founder_reference():
    decisions = _approval_decisions("CTO", "Underwriter", "Founder")
    decisions[2]["approver_id"] = "local-board"
    decisions[2]["source_author"] = {
        "kind": "agent",
        "id": "bc8396d5-dee6-4ad8-ae3f-1a2280088cb4",
    }
    decisions[2]["decision_ref"] = "BEAAA-13412#comment-28cac28b-d5b7-4150-aa08-bc4aaf54e7aa"

    errors = release_gate_n5._validate_approved_movement_sources(
        (_movement_source(movement=4.0, decisions=decisions),)
    )

    assert any("Founder approver_id must match source_author.id" in error for error in errors)


def test_approval_provenance_requires_content_markers():
    decisions = _approval_decisions("CTO", "Underwriter", "Founder")
    decisions[1]["decision_markers"] = []

    errors = release_gate_n5._validate_approved_movement_sources(
        (_movement_source(movement=4.0, decisions=decisions),)
    )

    assert any("Underwriter decision_markers are required" in error for error in errors)


def test_approval_provenance_requires_founder_above_three():
    errors = release_gate_n5._validate_approved_movement_sources(
        (_movement_source(movement=4.0, decisions=_approval_decisions("CTO", "Underwriter")),)
    )

    assert any("Founder" in error for error in errors)


def test_approval_provenance_accepts_distinct_role_complete_chain():
    errors = release_gate_n5._validate_approved_movement_sources(
        (
            _movement_source(
                movement=4.0,
                decisions=_approval_decisions("CTO", "Underwriter", "Founder"),
            ),
        )
    )

    assert errors == []


def test_display_path_accepts_absolute_out_dir(tmp_path):
    external_report = tmp_path / "receipt.md"

    assert release_gate_n5._display_path(external_report) == external_report.resolve()


def test_receipt_keeps_acknowledged_m2_m3_residuals_release_blocking():
    """BEAAA-14948: acknowledgement must never be rendered as zero or PASS."""

    results = {
        "M2": release_gate_n5.AgentGateResult(
            agent_id="M2",
            baseline_values={"total": 0.0, "GOVERNANCE": 0.0, "ROBUSTNESS": 0.0, "BIAS_FAIRNESS": 0.0, "OPS": 0.0},
            candidate_values=[{"total": 1.0, "GOVERNANCE": 0.0, "ROBUSTNESS": 1.3542, "BIAS_FAIRNESS": 0.0, "OPS": 0.0}],
        ),
        "M3": release_gate_n5.AgentGateResult(
            agent_id="M3",
            baseline_values={"total": 0.0, "GOVERNANCE": 0.0, "ROBUSTNESS": 0.0, "BIAS_FAIRNESS": 0.0, "OPS": 0.0},
            candidate_values=[{"total": 3.0, "GOVERNANCE": 0.0, "ROBUSTNESS": 2.1875, "BIAS_FAIRNESS": 0.0, "OPS": 0.0}],
        ),
    }
    agents = [release_gate_n5._result_to_dict(results[agent]) for agent in ("M2", "M3")]
    unresolved = release_gate_n5._unresolved_movement_accounting(agents)

    assert release_gate_n5.MOVEMENT_ACCOUNTING_DECISION_REF == "BEAAA-14949#decision@f7aacc18"
    assert [item["agent"] for item in unresolved] == ["M2", "M3"]
    assert unresolved[0]["residual"]["ROBUSTNESS"] == 1.3542
    assert unresolved[1]["residual"]["total"] == 3.0
    assert unresolved[1]["residual"]["ROBUSTNESS"] == 2.1875


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
        "approval_provenance": {"passed": True, "failure_reason": None, "errors": []},
        "release_reliance_authorized": True,
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


def test_run_cli_passes_trusted_architecture_flags_env(monkeypatch, tmp_path):
    input_dir = tmp_path / "inputs" / "G1"
    input_dir.mkdir(parents=True)
    (input_dir / "applicability.json").write_text(
        json.dumps(
            {
                "architecture_flags": {
                    "has_rag_retrieval": False,
                    "has_tool_calling": True,
                }
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(release_gate_n5.subprocess, "run", fake_run)

    code, stdout, stderr = release_gate_n5._run_cli(
        "G1",
        0,
        input_dir,
        tmp_path / "outputs" / "G1" / "seed-0.json",
    )

    assert (code, stdout, stderr) == (0, "ok", "")
    assert captured["env"][release_gate_n5.TRUSTED_ARCHITECTURE_FLAGS_ENV] == (
        '{"has_rag_retrieval":false,"has_tool_calling":true}'
    )


def test_trusted_architecture_flags_env_rejects_non_bool_values(tmp_path):
    input_dir = tmp_path / "inputs" / "G1"
    input_dir.mkdir(parents=True)
    (input_dir / "applicability.json").write_text(
        json.dumps({"architecture_flags": {"has_tool_calling": "yes"}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="string keys to bool values"):
        release_gate_n5._trusted_architecture_flags_json(input_dir)
