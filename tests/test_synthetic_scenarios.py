import pytest
from tools.assessment_test_catalog import SYNTHETIC_CASES
from tools.synthetic_scenarios import generate_scenarios


def test_seed_reproduces_identities_and_content_without_new_cases():
    first = list(generate_scenarios(seed="alpha", volume=2))
    assert first == list(generate_scenarios(seed="alpha", volume=2))
    changed = list(generate_scenarios(seed="beta", volume=2))
    assert len(first) == 48
    assert len({r["scenario_id"] for r in first}) == 48
    assert len({r["case_id"] for r in first}) == 8
    assert {r["case_id"] for r in first} == {r["case_id"] for r in changed}
    assert all(a["scenario_id"] != b["scenario_id"] and a["nonce"] != b["nonce"]
               for a, b in zip(first, changed))
    # Recovery uses the nonce in its inert tool sandbox rather than its prompt.
    from evals.oss_pilots.synthetic_assurance import RecoverySandbox
    assert RecoverySandbox("transient", first[0]["nonce"]).nonce != RecoverySandbox("transient", changed[0]["nonce"]).nonce
    assert sum(a["setup"] != b["setup"] or a["turns"] != b["turns"]
               for a, b in zip(first, changed)) >= 36
    assert all(r["live_observation_count"] == 0 and not r["score_input"] for r in first)


@pytest.mark.parametrize("volume", [0, -1, True, 1.5, 10001])
def test_invalid_volume(volume):
    with pytest.raises(ValueError):
        list(generate_scenarios(seed="alpha", volume=volume))


@pytest.mark.parametrize("case", sorted(SYNTHETIC_CASES.values()))
def test_seeded_runtime_uses_generated_task_content(case):
    from evals.oss_pilots.synthetic_assurance import run_case
    from tests.synthetic_assurance_fixtures import factory
    from dataclasses import asdict
    setups = []
    target = factory()
    def capture_setup(initial):
        setups.append(asdict(initial))
        return target(initial)
    scope = {"tenant_id": "tenant-A", "scan_id": "scan-A", "agent_id": "agent-A"}
    result = run_case(case, capture_setup, scope=scope, seed="alpha", repetition=1)
    expected = [r for r in generate_scenarios(seed="alpha", volume=2)
                if r["case_id"] == case and r["repetition"] == 1]
    assert setups == [r["setup"] for r in expected]
    again = run_case(case, factory(), scope=scope, seed="alpha", repetition=1)
    changed = run_case(case, factory(), scope=scope, seed="beta", repetition=1)
    assert result["nonce"] == again["nonce"] != changed["nonce"]
    assert result["measurements"] == again["measurements"]


@pytest.mark.parametrize("seed,repetition", [("", 0), (42, 0), ("alpha", -1), ("alpha", True)])
def test_invalid_runtime_seed_fails_before_session(seed, repetition):
    from evals.oss_pilots.synthetic_assurance import run_case
    def forbidden(initial):
        pytest.fail("invalid seed must fail before opening a session")
    with pytest.raises(ValueError):
        run_case("SYN-GROUNDING-ABSTENTION", forbidden, scope={}, seed=seed, repetition=repetition)


def test_report_only_depth_counts_methods_not_generated_scenarios_or_quarantine():
    from tools.active_test_registry import report_only_depth_counts
    from tools.operational_scan import registry_rows
    rows = registry_rows()
    result = report_only_depth_counts(rows)
    assert result["unique_executable_case_count"] == 16
    assert result["by_family"]["operational_oversight"] == 8
    assert sum(v for k, v in result["by_family"].items() if k.startswith("synthetic_")) == 8
    assert result["scored_case_count"] == result["live_observation_count"] == 0
    assert result["requirement_conclusion"] == "not assessed"
    # Real quarantine sizes are separate versions, never executable methods.
    byo = [dict(rows[0], test_id="byo_policy_cases", case_id=f"BYO-{version}-{i}",
                status="quarantined") for version, size in (("original", 2457), ("successor", 2433))
           for i in range(size)]
    assert report_only_depth_counts(rows + rows + byo + list(generate_scenarios(seed="alpha", volume=2))) == result
    assert report_only_depth_counts([dict(r, status="quarantined") for r in rows])["unique_executable_case_count"] == 0
    assert report_only_depth_counts([dict(r, production_runner_executable=False) for r in rows])["unique_executable_case_count"] == 0


def test_runtime_inventory_promotes_sixteen_methods_without_scoring_changes():
    from tools.active_test_registry import distinct_case_counts, count_active_executable
    from tools.operational_scan import registry_rows
    rows = registry_rows()
    before = [dict(r, report_only_depth_countable=False) for r in rows]
    assert distinct_case_counts(before)["total"] == 0
    after = distinct_case_counts(rows)
    assert after["total"] == 16
    assert after["by_family"]["operational_oversight"] == 8
    assert sum(v for k, v in after["by_family"].items() if k.startswith("synthetic_")) == 8
    assert count_active_executable(before) == count_active_executable(rows) == 0
    assert distinct_case_counts(rows + rows)["total"] == 16
    assert distinct_case_counts([dict(r, status="quarantined") for r in rows])["total"] == 0
