from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

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
        baseline_values=[{
            "total": 80.0,
            "GOVERNANCE": 20.0,
            "ROBUSTNESS": 20.0,
            "BIAS_FAIRNESS": 20.0,
            "OPS": 20.0,
        }],
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
        baseline_values=[{
            "total": 73.0,
            "GOVERNANCE": 22.3958,
            "ROBUSTNESS": 18.75,
            "BIAS_FAIRNESS": 12.5,
            "OPS": 19.7917,
        }],
        candidate_values=[
            {"total": 77.0, "GOVERNANCE": 22.3958, "ROBUSTNESS": 22.5, "BIAS_FAIRNESS": 12.5, "OPS": 19.7917}
            for _ in release_gate_n5.SEEDS
        ],
    )

    assert result.spread_passed
    assert not result.delta_passed
    assert not result.passed


def test_agent_result_does_not_subtract_historical_approved_movement():
    result = release_gate_n5.AgentGateResult(
        agent_id="G3",
        baseline_values=[{
            "total": 73.0,
            "GOVERNANCE": 22.3958,
            "ROBUSTNESS": 18.75,
            "BIAS_FAIRNESS": 12.5,
            "OPS": 19.7917,
        }],
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
    assert result.deltas["total"] == 4.0
    assert result.deltas["ROBUSTNESS"] == 3.75
    assert not result.delta_passed


def _movement_source(*, movement=4.0, decisions=None):
    return {
        "source_ticket": "BEAAA-16700",
        "commits": ["deadbee"],
        "approval_decisions": decisions or [],
        "deltas": {"G3": {"total": movement}},
    }


def _approval_decisions(*roles):
    return [
        {
            "role": role,
            "approver_id": f"identity-{role.lower()}",
            "decision_ref": f"comment-{role.lower()}-approved",
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


def test_receipt_keeps_acknowledged_m2_m3_residuals_visible():
    """Development drift remains measured and must never be rendered as zero."""

    results = {
        "M2": release_gate_n5.AgentGateResult(
            agent_id="M2",
            baseline_values=[{"total": 0.0, "GOVERNANCE": 0.0, "ROBUSTNESS": 0.0, "BIAS_FAIRNESS": 0.0, "OPS": 0.0}],
            candidate_values=[{"total": 1.0, "GOVERNANCE": 0.0, "ROBUSTNESS": 1.3542, "BIAS_FAIRNESS": 0.0, "OPS": 0.0}],
        ),
        "M3": release_gate_n5.AgentGateResult(
            agent_id="M3",
            baseline_values=[{"total": 0.0, "GOVERNANCE": 0.0, "ROBUSTNESS": 0.0, "BIAS_FAIRNESS": 0.0, "OPS": 0.0}],
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


def test_agent_result_blocks_direct_delta_with_zero_spread():
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
        baseline_values=[baseline] * 5,
        candidate_values=[candidate] * 5,
    )

    assert result.candidate_spread["total"] == 0
    assert result.deltas["total"] == 4
    assert result.deltas["ROBUSTNESS"] == 4
    assert not result.passed


def test_non_development_phase_requires_approved_policy(tmp_path):
    with pytest.raises(ValueError, match="explicitly approved review policy"):
        release_gate_n5.run_gate(
            out_dir=tmp_path,
            baseline_label="baseline",
            candidate_label="candidate",
            issue="BEAAA-23845",
            cto_cosign_ref="BEAAA-23845",
            focus_claim="phase guard",
            ticket_refs=["BEAAA-23845"],
            rubric_point_id=[release_gate_n5.RUBRIC_POINTS_NOT_EXERCISED],
            phase="production",
        )


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"issue": ""}, "issue must be a BEAAA-NNN"),
        ({"issue": "not-a-ticket"}, "issue must be a BEAAA-NNN"),
        ({"cto_cosign_ref": ""}, "cto_cosign_ref must be"),
        ({"cto_cosign_ref": "signed off in chat"}, "cto_cosign_ref must be"),
        ({"focus_claim": "  "}, "focus_claim must state"),
        ({"ticket_refs": []}, "ticket_refs must list at least"),
        ({"ticket_refs": ["17963"]}, "ticket_refs entries must be"),
        ({"ticket_refs": ["BEAAA-17963"]}, "must appear in ticket_refs"),
        ({"rubric_point_id": []}, "rubric_point_id must list"),
        ({"rubric_point_id": ["  "]}, "rubric_point_id must list"),
        ({"rubric_point_id": ["governance.pii"]}, "rubric_point_id entries must be"),
        ({"rubric_point_id": ["GOVERNANCE"]}, "rubric_point_id entries must be"),
        (
            {"rubric_point_id": ["none-exercised", "P1.1"]},
            "cannot combine",
        ),
        # BEAAA-23953: the characters YAML reads specially inside a
        # double-quoted scalar. The quote breaks the parse loudly; the
        # backslash is the silent one, because escape processing yields valid
        # YAML holding a different sentence.
        (
            {"focus_claim": 'BEAAA-23845 fixes the "hardcoded" template.'},
            "cannot contain a double quote",
        ),
        (
            {"focus_claim": r"BEAAA-23845 fixes the \x41 template."},
            "cannot contain a backslash",
        ),
        (
            {"focus_claim": "BEAAA-23845 fixes provenance.\nowner: someone_else"},
            r"cannot contain a non-printable character \(U\+000A\)",
        ),
        # BEAAA-23964: C1 was the range the first guard missed. U+0080 stands
        # for the 31 that make the header unparseable; U+0085 is the one that
        # folds to a space and rewrites the claim without any parse error.
        (
            {"focus_claim": "BEAAA-23845 fixes\x80provenance."},
            r"cannot contain a non-printable character \(U\+0080\)",
        ),
        (
            {"focus_claim": "BEAAA-1 real claim.\x85owner: attacker"},
            r"cannot contain a non-printable character \(U\+0085\)",
        ),
    ],
)
def test_receipt_provenance_fails_closed(kwargs, match):
    """BEAAA-23845: absent or malformed provenance blocks the receipt."""
    base = {
        "issue": "BEAAA-23845",
        "cto_cosign_ref": "BEAAA-23845#comment-835cf89e",
        "focus_claim": "BEAAA-23845 parameterises receipt provenance.",
        "ticket_refs": ["BEAAA-23845"],
        "rubric_point_id": ["P1.1"],
    }
    with pytest.raises(ValueError, match=match):
        release_gate_n5._validate_receipt_provenance(**{**base, **kwargs})


def test_rule_execution_states_absent_cosign_rather_than_inventing_one():
    """BEAAA-23845: a rule-execution run must not borrow a ticket id as co-sign."""
    issue, cosign, _claim, refs, _points = release_gate_n5._validate_receipt_provenance(
        issue="BEAAA-23845",
        cto_cosign_ref=release_gate_n5.COSIGN_NOT_REQUIRED,
        focus_claim="BEAAA-23845 parameterises receipt provenance.",
        ticket_refs=["BEAAA-23845"],
        rubric_point_id=["P1.1"],
    )
    assert issue == "BEAAA-23845"
    assert cosign == "not-required-rule-execution"
    assert refs == ["BEAAA-23845"]


def test_rubric_points_come_from_the_run_not_a_pii_literal():
    """BEAAA-23957: the receipt carries the points the run states, nothing else."""
    *_head, points = release_gate_n5._validate_receipt_provenance(
        issue="BEAAA-23957",
        cto_cosign_ref=release_gate_n5.COSIGN_NOT_REQUIRED,
        focus_claim="BEAAA-23957 parameterises the rubric points on the receipt.",
        ticket_refs=["BEAAA-23957"],
        rubric_point_id=["P1.1", "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL"],
    )
    assert points == ["P1.1", "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL"]
    assert "GOVERNANCE.PII.HANDLING_ATTESTATION" not in points


def test_run_exercising_no_rubric_point_states_that_rather_than_naming_one():
    """BEAAA-23957: a metadata-only run must not borrow a rubric point."""
    *_head, points = release_gate_n5._validate_receipt_provenance(
        issue="BEAAA-23957",
        cto_cosign_ref=release_gate_n5.COSIGN_NOT_REQUIRED,
        focus_claim="BEAAA-23957 changes artifact metadata only.",
        ticket_refs=["BEAAA-23957"],
        rubric_point_id=[release_gate_n5.RUBRIC_POINTS_NOT_EXERCISED],
    )
    assert points == ["none-exercised"]


def test_no_pii_rubric_point_literal_survives_in_the_generator():
    """BEAAA-23957: the defect was a literal in the source, so assert on source."""
    source = release_gate_n5.__file__
    with open(source, encoding="utf-8") as handle:
        body = handle.read()

    for point in (
        "GOVERNANCE.PII.HANDLING_ATTESTATION",
        "ROBUSTNESS.PII.SCAN_TIME_DETECTION",
    ):
        assert f'"{point}"' not in body, f"{point} is hardcoded in {source}"


def test_receipt_provenance_is_required_by_the_cli():
    """BEAAA-23845: the CLI cannot be invoked without provenance."""
    with pytest.raises(SystemExit):
        release_gate_n5._build_parser().parse_args(
            ["--baseline", "a", "--candidate", "b", "--phase", "development"]
        )


def test_cli_requires_rubric_points_alongside_the_other_provenance():
    """BEAAA-23957: provenance minus --rubric-point-id is still incomplete."""
    argv = [
        "--baseline", "a",
        "--candidate", "b",
        "--phase", "development",
        "--issue", "BEAAA-23957",
        "--cto-cosign-ref", release_gate_n5.COSIGN_NOT_REQUIRED,
        "--focus-claim", "BEAAA-23957 parameterises the rubric points.",
        "--ticket-refs", "BEAAA-23957",
    ]
    with pytest.raises(SystemExit):
        release_gate_n5._build_parser().parse_args(argv)

    args = release_gate_n5._build_parser().parse_args(
        argv + ["--rubric-point-id", "P1.1", "P3.3"]
    )
    assert args.rubric_point_id == ["P1.1", "P3.3"]


def test_render_markdown_refuses_report_without_provenance():
    """BEAAA-23845: no silent fallback to a template literal."""
    with pytest.raises(ValueError, match="receipt provenance missing"):
        release_gate_n5._render_markdown({"phase": "development"})


def test_render_markdown_refuses_report_without_rubric_points():
    """BEAAA-23957: rubric points are provenance too, not an optional extra."""
    report = {
        "phase": "development",
        "issue": "BEAAA-23957",
        "focus_claim": "BEAAA-23957 parameterises the rubric points.",
        "ticket_refs": ["BEAAA-23957"],
    }
    with pytest.raises(ValueError, match="rubric_point_id"):
        release_gate_n5._render_markdown(report)


def _renderable_report(**overrides) -> dict:
    """A report complete enough for _render_markdown, for tests to vary."""
    report = {
        "issue": "BEAAA-10187",
        "focus_claim": "BEAAA-10187 evidences golden N=5 reproducibility.",
        "ticket_refs": ["BEAAA-10187"],
        "contract": "are-scanner-api-v1@v1.0",
        "scanner_version": "0.6.0",
        "probe-set-version": "artifacts/beaaa-387/manifest.json",
        "rubric_point_id": [
            "GOVERNANCE.PII.HANDLING_ATTESTATION",
            "ROBUSTNESS.PII.SCAN_TIME_DETECTION",
        ],
        "cto_cosign_ref": "BEAAA-14867",
        "phase": "development",
        "development_drift_disposition": "informational_non_blocking",
        "baseline": "0.4.0",
        "baseline_commit": "a" * 40,
        "baseline_tree": "b" * 40,
        "candidate": "0.4.1",
        "candidate_commit": "c" * 40,
        "candidate_tree": "d" * 40,
        "frozen_input_hashes": {},
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
    report.update(overrides)
    return report


def test_render_markdown_uses_required_delta_table_columns():
    markdown = release_gate_n5._render_markdown(_renderable_report())

    assert "| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | spread (baseline) | spread (candidate) |" in markdown
    assert "| G1 | 0 | 0 | 0 | 0 | 0 | total=0" in markdown
    assert "Claim: `8/9 baselined`" in markdown
    assert "B2 is explicitly excluded from the v1 gate" in markdown
    assert "Historical Approved-Movement Provenance" in markdown


def _frontmatter(markdown: str) -> dict:
    opening, block, _body = markdown.split("---\n", 2)
    assert opening == ""
    return yaml.safe_load(block)


def test_receipt_frontmatter_round_trips_an_accepted_focus_claim():
    """BEAAA-23953: the header an operator can write is the header YAML reads.

    A colon, a hash and quoting punctuation are ordinary in an English claim
    sentence and all carry meaning to a YAML parser outside a quoted scalar.
    Inside the double-quoted scalar this renderer emits they are literal, so
    the guard rejects only what it has to and this text survives verbatim.
    """
    claim = (
        "BEAAA-23953: rejects a claim's unsafe characters "
        "(#1 priority, 100% of receipts) -- see [BEAAA-23845]."
    )

    frontmatter = _frontmatter(
        release_gate_n5._render_markdown(_renderable_report(focus_claim=claim))
    )

    assert frontmatter["focus_claim"] == claim
    assert frontmatter["ticket_refs"] == ["BEAAA-10187"]
    assert frontmatter["canonical_terms_only"] is True


def test_a_rejected_double_quote_would_have_broken_the_frontmatter():
    """BEAAA-23953: the guard is load-bearing, and this is what it prevents.

    QA (BEAAA-23964, N2) found the first version of this test could not detect
    removal of the guard, because it only edited already-rendered markdown. It
    now asserts the rejection first, so deleting the guard fails it, and then
    shows the header that rejection avoids.
    """
    claim = 'BEAAA-23845 fixes the "hardcoded" template.'

    with pytest.raises(ValueError, match="cannot contain a double quote"):
        release_gate_n5._validate_receipt_provenance(
            issue="BEAAA-23953",
            cto_cosign_ref=release_gate_n5.COSIGN_NOT_REQUIRED,
            focus_claim=claim,
            ticket_refs=["BEAAA-23953"],
            rubric_point_id=[release_gate_n5.RUBRIC_POINTS_NOT_EXERCISED],
        )

    unguarded = release_gate_n5._render_markdown(
        _renderable_report(focus_claim="ordinary")
    ).replace('focus_claim: "ordinary"', f'focus_claim: "{claim}"')
    with pytest.raises(yaml.YAMLError):
        _frontmatter(unguarded)


def test_no_accepted_focus_claim_survives_the_header_changed_or_broken():
    """BEAAA-23964: the invariant, asserted as a property rather than a range.

    Enumerating bad ranges is exactly what failed the first pass — C0 and DEL
    were rejected while all 32 C1 code points went through, 31 emitting an
    unparseable header and U+0085 folding to a space and rewriting the claim.
    So this asserts the thing that actually has to hold: for every code point,
    either the validator refuses it, or the receipt reads back byte for byte.
    """
    suspects = list(range(0x00, 0x100)) + [0x2028, 0x2029, 0x200B, 0xFEFF, 0xFFFE]
    survived_changed = []

    for code_point in suspects:
        claim = f"BEAAA-23953 keeps{chr(code_point)}the claim readable."
        try:
            # Unpacked by name, not by position: the R2 version used a starred
            # slice that would have silently bound the wrong element when
            # BEAAA-23957 widened the return to five (QA on BEAAA-23964, F-A).
            (
                _issue,
                _cosign,
                accepted,
                _refs,
                _points,
            ) = release_gate_n5._validate_receipt_provenance(
                issue="BEAAA-23953",
                cto_cosign_ref=release_gate_n5.COSIGN_NOT_REQUIRED,
                focus_claim=claim,
                ticket_refs=["BEAAA-23953"],
                rubric_point_id=[release_gate_n5.RUBRIC_POINTS_NOT_EXERCISED],
            )
        except ValueError:
            continue  # refused at the validator, which is the guard working
        markdown = release_gate_n5._render_markdown(
            _renderable_report(focus_claim=accepted)
        )
        try:
            read_back = _frontmatter(markdown)["focus_claim"]
        except yaml.YAMLError:
            survived_changed.append((f"U+{code_point:04X}", "unparseable"))
            continue
        if read_back != accepted:
            survived_changed.append((f"U+{code_point:04X}", repr(read_back)))

    assert survived_changed == []


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
    monkeypatch.setattr(
        release_gate_n5,
        "_resolve_commit",
        lambda label: (f"{label}-commit", f"{label}-tree"),
    )
    monkeypatch.setattr(
        release_gate_n5,
        "_archive_commit",
        lambda _commit, target: target.mkdir(parents=True),
    )
    monkeypatch.setattr(
        release_gate_n5,
        "_frozen_input_hashes",
        lambda _repo: {"fixture": "abc"},
    )
    monkeypatch.setattr(release_gate_n5.behavioral_golden_fixtures, "run_behavioral_gate", lambda: {"passed": True})
    monkeypatch.setattr(release_gate_n5.behavioral_golden_fixtures, "render_markdown_section", lambda _report: ["behavioral"])

    def fake_prepare_input_dir(agent_id, tmp_root, _source_root):
        path = tmp_root / "inputs" / agent_id
        path.mkdir(parents=True)
        return path

    executed_repos = []

    def fake_run_cli(repo, agent_id, seed, input_dir, output_path):
        executed_repos.append(repo.name)
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
        issue="BEAAA-23845",
        cto_cosign_ref="BEAAA-23845#comment-835cf89e",
        focus_claim="BEAAA-23845 parameterises receipt provenance.",
        ticket_refs=["BEAAA-23845", "BEAAA-21512"],
        rubric_point_id=["P1.1", "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL"],
        today="2026-07-12",
    )

    json_path = md_path.with_suffix(".json")
    assert report["passed"] is True
    assert md_path.exists()
    assert json_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["passed"] is True
    assert executed_repos == ["baseline", "candidate"]
    assert report["agents"][0]["delta"]["total"] == 0.0

    # BEAAA-23845: the emitted receipt must carry the provenance that was
    # passed in, not a template literal from whichever ticket last touched
    # this script.
    markdown = md_path.read_text(encoding="utf-8")
    assert report["ticket_refs"] == ["BEAAA-23845", "BEAAA-21512"]
    assert report["cto_cosign_ref"] == "BEAAA-23845#comment-835cf89e"
    assert 'ticket_refs: ["BEAAA-23845", "BEAAA-21512"]' in markdown
    assert "- CTO co-sign: `BEAAA-23845#comment-835cf89e`" in markdown
    assert "# BEAAA-23845 Development N=5 Replay" in markdown
    assert 'focus_claim: "BEAAA-23845 parameterises receipt provenance."' in markdown
    assert "BEAAA-17963" not in markdown
    assert "BEAAA-14867" not in markdown

    # BEAAA-23957: same rule for the rubric points. The receipt names what this
    # run exercised; the two PII points it did not exercise appear nowhere.
    assert report["rubric_point_id"] == [
        "P1.1",
        "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL",
    ]
    assert (
        "- Rubric points: `P1.1, ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL`" in markdown
    )
    assert "PII" not in markdown


def test_artifact_candidate_identity_preserves_literal_resolved_values():
    identity = release_gate_n5._artifact_candidate_identity(
        "a" * 40,
        "a" * 40,
        "b" * 40,
    )

    assert identity == (
        "a" * 40,
        "a" * 40,
        "b" * 40,
        "explicit immutable ref",
    )


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
        tmp_path,
        "G1",
        0,
        input_dir,
        tmp_path / "outputs" / "G1" / "seed-0.json",
    )

    assert (code, stdout, stderr) == (0, "ok", "")
    assert "--trusted-replay-manifest" not in captured["cmd"]
    assert "--trusted-replay-fixture" not in captured["cmd"]
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


# BEAAA-24525: the BEAAA-19710 rebaseline (ffa6359) rewrote `bmi` and the pillar
# `score` values on every persisted golden baseline and touched no `band`,
# `display`, `modifier` or `posture` line. M3 crossed a letter cut in that move
# and kept the old letter, so the persisted anchor read D+ / "Decline" for a
# total of 60 that the frozen band table calls C / "Surplus". Nothing in the
# scoring path reads these band blocks — `rubric/bands.py:assign_band` recomputes
# the band at scoring time — so the drift was invisible to every gate while the
# files were still being quoted to humans as the calibration range. These two
# tests are the missing tie: a persisted band must always be derivable from the
# total persisted beside it.
def _band_drift(payload: dict, source: str) -> str | None:
    """Return a human-readable mismatch line, or None when the band is consistent."""
    from rubric.bands import assign_band, band_to_dict

    bmi = payload.get("bmi")
    if not isinstance(bmi, int):
        return f"{source}: bmi is {bmi!r}, expected int"
    persisted = payload.get("band")
    if not isinstance(persisted, dict):
        return f"{source}: band is {persisted!r}, expected object"
    recomputed = band_to_dict(assign_band(bmi))
    if recomputed == persisted:
        return None
    diffs = ", ".join(
        f"{key}: persisted={persisted.get(key)!r} recomputed={recomputed.get(key)!r}"
        for key in sorted(set(recomputed) | set(persisted))
        if persisted.get(key) != recomputed.get(key)
    )
    return f"{source}: bmi={bmi} -> {diffs}"


def test_persisted_golden_baseline_band_matches_its_own_total():
    checked: list[str] = []
    drift: list[str] = []
    for agent_id in release_gate_n5.FULL_ROSTER:
        path = release_gate_n5.BASELINES / agent_id / "baseline.json"
        if not path.exists():
            continue
        checked.append(agent_id)
        mismatch = _band_drift(
            json.loads(path.read_text(encoding="utf-8")),
            f"artifacts/beaaa-387/{agent_id}/baseline.json",
        )
        if mismatch:
            drift.append(mismatch)

    # Positive control: B2 alone carries no persisted baseline (BEAAA-10199), so
    # a run that checks fewer than the other eight is looking at the wrong tree
    # and would pass this test by finding nothing rather than by finding nothing
    # wrong.
    assert checked == ["G1", "G2", "G3", "M1", "M2", "M3", "B1", "B3"]
    assert drift == []


def test_golden_agent_expected_score_band_matches_its_own_total():
    fixtures = release_gate_n5.REPO / "tests" / "fixtures" / "golden_agents"
    checked: list[str] = []
    drift: list[str] = []
    for agent_id in release_gate_n5.FULL_ROSTER:
        path = fixtures / agent_id / "expected_score.json"
        if not path.exists():
            continue
        checked.append(agent_id)
        mismatch = _band_drift(
            json.loads(path.read_text(encoding="utf-8")),
            f"tests/fixtures/golden_agents/{agent_id}/expected_score.json",
        )
        if mismatch:
            drift.append(mismatch)

    # Same positive control. Six of the nine roster fixtures carry an
    # expected_score.json; G3, B2 and B3 carry agent.json only.
    assert checked == ["G1", "G2", "M1", "M2", "M3", "B1"]
    assert drift == []


def _write_frozen_hash_fixture(tmp_path):
    agent_root = tmp_path / "artifacts" / "beaaa-387" / "G1"
    agent_root.mkdir(parents=True)
    (agent_root / "eval_outputs.json").write_text('{"raw": 1}', encoding="utf-8")
    (agent_root / "applicability.json").write_text('{"applicable": true}', encoding="utf-8")
    baseline_path = agent_root / "baseline.json"
    baseline_path.write_text(
        json.dumps(
            {
                "model_versions": {"judge": "pinned-v1"},
                "bmi": 79,
                "band": {"display": "B"},
                "pillars": {"ROBUSTNESS": {"score": 20}},
            }
        ),
        encoding="utf-8",
    )
    return agent_root, baseline_path


def test_frozen_input_hashes_ignore_rebaseline_outputs(tmp_path, monkeypatch):
    _, baseline_path = _write_frozen_hash_fixture(tmp_path)
    monkeypatch.setattr(release_gate_n5, "ROSTER", ("G1",))
    before = release_gate_n5._frozen_input_hashes(tmp_path)

    baseline_path.write_text(
        json.dumps(
            {
                "model_versions": {"judge": "pinned-v1"},
                "bmi": 81,
                "band": {"display": "B"},
                "pillars": {"ROBUSTNESS": {"score": 22}},
            }
        ),
        encoding="utf-8",
    )

    assert release_gate_n5._frozen_input_hashes(tmp_path) == before


@pytest.mark.parametrize("changed_file", ["model_versions", "eval_outputs.json", "applicability.json"])
def test_frozen_input_hashes_reject_gate_input_changes(tmp_path, monkeypatch, changed_file):
    agent_root, baseline_path = _write_frozen_hash_fixture(tmp_path)
    monkeypatch.setattr(release_gate_n5, "ROSTER", ("G1",))
    before = release_gate_n5._frozen_input_hashes(tmp_path)

    if changed_file == "model_versions":
        baseline_path.write_text(
            json.dumps({"model_versions": {"judge": "pinned-v2"}, "bmi": 79}),
            encoding="utf-8",
        )
    else:
        (agent_root / changed_file).write_text('{"changed": true}', encoding="utf-8")

    assert release_gate_n5._frozen_input_hashes(tmp_path) != before
