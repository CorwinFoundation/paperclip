"""Targeted CI for AIUC Path B lane C — BEAAA-16272 (parent BEAAA-16264).

Proves, per family, that all SIX BEAAA-16264 validation points hold, that each
family is live in the full-scan manifest as reported-not-scored, and that the
golden-roster N=5 total/pillar deltas are exactly 0 (by construction — no
scoring-path dependency). Also proves the reference tenant config is included and
the MCP applicability gate works.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from evals.aiuc_path_b import base, manifest
from evals.aiuc_path_b import consequential_action_confirmation as cac
from evals.aiuc_path_b import cross_session_data_leak as csdl
from evals.aiuc_path_b import customer_defined_risk_pack as cdr
from evals.aiuc_path_b import human_escalation_flagging as hef
from evals.aiuc_path_b import mcp_tool_integrity as mcp
from evals.aiuc_path_b import system_prompt_extraction as spe

REPO = Path(__file__).resolve().parents[1]
MAPPING_DIR = REPO / "rubric" / "probe_mappings"

# External eval frameworks that MUST NOT be on the critical path (validation #1).
FORBIDDEN_FRAMEWORKS = ("garak", "pyrit", "inspect_ai", "promptfoo", "tau_bench", "tau2_bench", "agentdojo", "advbench")

# name -> (module, expected_case_count, expected_applicability_flag)
FAMILIES = {
    "cross_session_data_leak": (csdl, 20, None),
    "human_escalation_flagging": (hef, 20, None),
    "consequential_action_confirmation": (cac, 15, None),
    "customer_defined_risk_pack": (cdr, 12, None),
    "mcp_tool_integrity": (mcp, 12, "has_mcp_tool_integration"),
    "system_prompt_extraction": (spe, 20, "has_hidden_control_material"),
}

PILLARS = ("GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS")
_PNM = re.compile(r"^P\d+\.\d+$")  # P<n>.<m> namespace


def _mods_on_critical_path() -> list[Path]:
    return sorted((REPO / "evals" / "aiuc_path_b").glob("*.py"))


# ---- Validation point 1: in-repo runner, embedded cases, no external framework ----
@pytest.mark.parametrize("name", list(FAMILIES))
def test_vp1_in_repo_embedded_cases_no_external_framework(name):
    module, count, _flag = FAMILIES[name]
    spec = module.SPEC
    assert len(spec.cases) == count, f"{name}: embedded case count"
    assert all(c.scenario_variants for c in spec.cases)


def test_vp1_no_forbidden_framework_import_anywhere_on_lane():
    for path in _mods_on_critical_path():
        src = path.read_text(encoding="utf-8")
        for fw in FORBIDDEN_FRAMEWORKS:
            assert not re.search(rf"\bimport\s+{fw}\b|\bfrom\s+{fw}\b", src), f"{path.name} imports {fw}"


# ---- Validation point 2: probe-mapping YAML in BOTH namespaces ----
@pytest.mark.parametrize("name", list(FAMILIES))
def test_vp2_mapping_both_namespaces(name):
    data = yaml.safe_load((MAPPING_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
    ids = data["rubric_point_ids"]
    assert any(_PNM.match(i) for i in ids), f"{name}: missing P<n>.<m> namespace: {ids}"
    assert any(i.split(".")[0] in PILLARS for i in ids), f"{name}: missing PILLAR.FAMILY.SUB namespace: {ids}"
    assert data["scoring"] == "reported_not_scored"
    # Mapping <-> SPEC agreement.
    assert list(ids) == list(FAMILIES[name][0].SPEC.rubric_point_ids)


def test_vp2_new_dim_ids_do_not_collide_with_scored_rubric_dims():
    # New-behavior reported-not-scored families must use FREE P<n>.<m> ids, not
    # reuse an existing scored dim (which would corrupt the AIUC-1 crosswalk).
    scored_ids = set(re.findall(r"\bid:\s*(P\d+\.\d+)", (REPO / "rubric" / "rubric.yaml").read_text()))
    rows = {r["family"]: r for r in manifest.load_registry()["families"]}
    for name, (module, _c, _f) in FAMILIES.items():
        pnm = next(i for i in module.SPEC.rubric_point_ids if _PNM.match(i))
        mapping = yaml.safe_load((MAPPING_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
        reuse = bool(rows[name].get("existing_rubric_point_reuse")) and bool(
            mapping.get("existing_rubric_point_reuse")
        )
        assert pnm not in scored_ids or reuse, f"{name}: {pnm} collides without explicit reuse flag"


# ---- Validation point 3: registry entry with owner/version pin/contract/K=5 ----
@pytest.mark.parametrize("name", list(FAMILIES))
def test_vp3_registry_entry_complete(name):
    reg = manifest.load_registry()
    row = next(r for r in reg["families"] if r["family"] == name)
    assert row["owner"] == "scanner_engineer"
    assert row["version_pin"] == f"{name}@0.1.0"
    assert row["expected_result_contract"].strip()
    assert int(row["replication_required"]) == 5 == base.REPLICATION_K


def test_vp3_registry_validates_against_code():
    # Raises on any registry<->SPEC drift (version pin, K, case count, namespaces, gate).
    manifest.validate_registry()


# ---- Validation point 4: does not feed admittance / scored surface (reported-not-scored) ----
@pytest.mark.parametrize("name", list(FAMILIES))
def test_vp4_not_wired_to_admittance_or_scoring(name):
    module, _count, _flag = FAMILIES[name]
    src = Path(module.__file__).read_text(encoding="utf-8")
    assert "rubric.scoring" not in src, f"{name}: must not import the scoring path"
    assert "underwriting.admittance" not in src, f"{name}: must not wire into admittance"
    assert module.SPEC.expected_result_contract  # co-sign tracked in disposition, not code


# ---- Validation point 5: golden N=5 total/pillar delta == 0 (by construction) ----
def test_vp5_lane_has_no_scoring_dependency():
    # Construction proof: neither the scoring engine nor the band math references
    # this lane, and this lane never imports them -> golden delta is 0.
    scoring_src = (REPO / "rubric" / "scoring.py").read_text(encoding="utf-8")
    bands_src = (REPO / "rubric" / "bands.py").read_text(encoding="utf-8")
    assert "aiuc_path_b" not in scoring_src
    assert "aiuc_path_b" not in bands_src
    for path in _mods_on_critical_path():
        src = path.read_text(encoding="utf-8")
        assert "import rubric.scoring" not in src
        assert "from rubric.scoring" not in src


@pytest.mark.parametrize("name", list(FAMILIES))
def test_vp5_manifest_entry_is_reported_not_scored(name):
    m = manifest.full_scan_manifest()
    entry = next(e for e in m["families"] if e["family"] == name)
    assert entry["scored"] is False
    assert entry["scoring"] == "reported_not_scored"
    assert m["reported_not_scored"] is True


@pytest.mark.parametrize("name", list(FAMILIES))
def test_vp5_byte_deterministic_per_seed(name):
    module, _count, flag = FAMILIES[name]
    flags = {flag: True} if flag else None
    for seed in range(5):
        a = base.run_family(module.SPEC, module.compliant_policy, seed=seed, architecture_flags=flags)
        b = base.run_family(module.SPEC, module.compliant_policy, seed=seed, architecture_flags=flags)
        assert a == b and a["sha256"] == b["sha256"], f"{name}: seed {seed} not byte-stable"


# ---- Validation point 6: fired-probe ledger + canary + surface_seed rotation ----
@pytest.mark.parametrize("name", list(FAMILIES))
def test_vp6_fired_ledger_and_canary_registration(name):
    module, _count, flag = FAMILIES[name]
    flags = {flag: True} if flag else None
    ev = base.run_family(module.SPEC, module.compliant_policy, seed=0, architecture_flags=flags)
    assert ev["fired_probe_ledger"] == [c.case_id for c in module.SPEC.cases]
    canaries = [c.case_id for c in module.SPEC.cases if c.is_canary]
    assert canaries and ev["canary_registration"] == canaries


@pytest.mark.parametrize("name", list(FAMILIES))
def test_vp6_canary_defeats_metric_gaming(name):
    module, _count, flag = FAMILIES[name]
    flags = {flag: True} if flag else None
    ev = base.run_family(module.SPEC, module.gaming_policy, seed=0, architecture_flags=flags)
    canary_rows = [r for r in ev["case_results"] if r["is_canary"]]
    assert canary_rows and all(r["verdict"] == base.FAIL for r in canary_rows), f"{name}: canary must trip gaming"


@pytest.mark.parametrize("name", list(FAMILIES))
def test_vp6_surface_seed_rotation_enabled(name):
    module, _count, flag = FAMILIES[name]
    flags = {flag: True} if flag else None
    prompts_by_case: dict[str, set[str]] = {}
    for seed in range(5):
        ev = base.run_family(module.SPEC, module.compliant_policy, seed=seed, architecture_flags=flags)
        for row in ev["case_results"]:
            prompts_by_case.setdefault(row["case_id"], set()).add(row["rendered_prompt"])
    # At least one multi-variant case must actually surface >1 phrasing across seeds.
    assert any(len(v) > 1 for v in prompts_by_case.values()), f"{name}: phrasing rotation not observed"


# ---- Discrimination: grader separates compliant / reckless / gaming ----
@pytest.mark.parametrize("name", list(FAMILIES))
def test_grader_discriminates(name):
    module, _count, flag = FAMILIES[name]
    flags = {flag: True} if flag else None
    good = base.verdict_tally(base.run_family(module.SPEC, module.compliant_policy, seed=0, architecture_flags=flags))
    reck = base.verdict_tally(base.run_family(module.SPEC, module.reckless_policy, seed=0, architecture_flags=flags))
    assert good[base.FAIL] == 0 and good[base.PASS] > 0
    assert reck[base.FAIL] > 0  # over-autonomous agent fails the require-action cases


# ---- MCP applicability gate ----
def test_mcp_gate_na_when_flag_absent():
    off = base.run_family(mcp.SPEC, mcp.compliant_policy, seed=0, architecture_flags={})
    assert off["applicable"] is False
    assert all(r["verdict"] == base.NA for r in off["case_results"])
    on = base.run_family(mcp.SPEC, mcp.compliant_policy, seed=0, architecture_flags={"has_mcp_tool_integration": True})
    assert on["applicable"] is True
    assert all(r["verdict"] != base.NA for r in on["case_results"])


# ---- Reference tenant config included ----
def test_reference_tenant_config_present_and_yields_12_cases():
    cfg_path = REPO / "rubric" / "aiuc_path_b" / "tenants" / "reference_tenant.yaml"
    assert cfg_path.exists()
    cfg = cdr.load_tenant(cfg_path)
    assert cfg["family"] == "customer_defined_risk_pack"
    assert len(cdr.cases_for_tenant(cfg)) == 12


# ---- BEAAA-16297: strict decision-contract grading, no fail-open coercion ----
# Declared expected-result field per family; must match grader, reference
# policies, and the emitted evidence decision blob (acceptance criterion 2).
DECISION_FIELDS = {
    "cross_session_data_leak": csdl.DISCLOSE_FIELD,
    "human_escalation_flagging": hef.ESCALATE_FIELD,
    "consequential_action_confirmation": cac.CONFIRM_FIELD,
    "customer_defined_risk_pack": cdr.FLAG_FIELD,
    "mcp_tool_integrity": mcp.REJECT_FIELD,
    "system_prompt_extraction": spe.DISCLOSE_FIELD,
}
# Oracle key that carries the ground-truth boolean per family.
ORACLE_KEYS = {
    "cross_session_data_leak": "must_disclose_cross_session_data",
    "human_escalation_flagging": "requires_escalation",
    "consequential_action_confirmation": "requires_confirmation",
    "customer_defined_risk_pack": "tenant_prohibits",
    "mcp_tool_integrity": "tool_is_tampered",
    "system_prompt_extraction": "should_disclose_hidden_instruction",
}


@pytest.mark.parametrize("name", list(FAMILIES))
def test_bug16297_declared_field_matches_contract_grader_and_policies(name):
    module, _count, _flag = FAMILIES[name]
    field = DECISION_FIELDS[name]
    # Contract-of-record declares exactly this field name (fixes the
    # escalate/escalated drift in human_escalation_flagging).
    assert f"{field}:bool" in module.SPEC.expected_result_contract, (
        f"{name}: contract must declare {field}:bool"
    )
    # Every reference policy emits exactly this field into the evidence decision.
    for policy in (module.compliant_policy, module.reckless_policy, module.gaming_policy):
        decision = policy(module.SPEC.cases[0], "prompt")
        assert field in decision, f"{name}: policy must emit declared field {field}"
    # And it surfaces under that key in a real evidence blob.
    flags = {_flag: True} if _flag else None
    ev = base.run_family(module.SPEC, module.compliant_policy, seed=0, architecture_flags=flags)
    assert field in ev["case_results"][0]["decision"]


@pytest.mark.parametrize("name", list(FAMILIES))
def test_bug16297_missing_field_is_invalid_never_fail_open_pass(name):
    module, _count, _flag = FAMILIES[name]
    # An omitted decision field must resolve to INVALID for EVERY case — including
    # the false-oracle control cases that previously fail-open PASSed via
    # bool(decision.get(field, False)).
    for case in module.SPEC.cases:
        verdict = module.grade(case, {"rationale": "field-omitted"})
        assert verdict == base.INVALID, (
            f"{name}/{case.case_id}: missing field -> {verdict}, expected INVALID"
        )
        assert verdict != base.PASS


@pytest.mark.parametrize("name", list(FAMILIES))
def test_bug16297_non_boolean_value_is_invalid(name):
    module, _count, _flag = FAMILIES[name]
    field = DECISION_FIELDS[name]
    # ints (incl. 1/0), strings, None, containers are NOT genuine bools.
    for bad in (1, 0, "true", "false", None, [True], {}):
        verdict = module.grade(module.SPEC.cases[0], {field: bad})
        assert verdict == base.INVALID, (
            f"{name}: non-bool {bad!r} -> {verdict}, expected INVALID"
        )


@pytest.mark.parametrize("name", list(FAMILIES))
def test_bug16297_false_oracle_control_case_not_fail_open(name):
    module, _count, _flag = FAMILIES[name]
    okey = ORACLE_KEYS[name]
    false_cases = [c for c in module.SPEC.cases if c.oracle[okey] is False]
    assert false_cases, f"{name}: expected at least one false-oracle control case"
    for case in false_cases:
        # Empty decision on a False-oracle case is exactly the regression the CTO
        # flagged: must be INVALID, never a coerced-False PASS.
        assert module.grade(case, {}) == base.INVALID, f"{name}/{case.case_id}"


@pytest.mark.parametrize("name", list(FAMILIES))
def test_bug16297_valid_bool_still_grades_pass_or_fail(name):
    module, _count, _flag = FAMILIES[name]
    field = DECISION_FIELDS[name]
    case = module.SPEC.cases[0]
    expected = bool(case.oracle[ORACLE_KEYS[name]])
    assert module.grade(case, {field: expected}) == base.PASS
    assert module.grade(case, {field: not expected}) == base.FAIL


def test_bug16297_require_bool_helper_semantics():
    ok, val = base.require_bool({"f": True}, "f")
    assert ok and val is True
    ok, val = base.require_bool({"f": False}, "f")
    assert ok and val is False
    assert base.require_bool({}, "f") == (False, None)
    assert base.require_bool({"f": 1}, "f") == (False, None)  # bool is subclass of int
    assert base.require_bool({"f": "true"}, "f") == (False, None)


# ---- Rollback lever: disabling activation removes a family from the manifest ----
def test_rollback_flag_removes_family_from_manifest():
    reg = manifest.load_registry()
    for r in reg["families"]:
        if r["family"] == "mcp_tool_integrity":
            r["activation_enabled"] = False
    m = manifest.full_scan_manifest(reg)
    assert "mcp_tool_integrity" not in {e["family"] for e in m["families"]}
    assert len(m["families"]) == len(FAMILIES) - 1
