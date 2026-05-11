"""BEAAA-200 Track 4: P4 governance attestation scorer tests.

Covers:
  - top-level form validation (required fields, accountability literal, version)
  - per-dim scorers at each tier (0/1/2/3/4) including the BEAAA-200 §1.2
    tamper-resistance cap (raw 1.0 sans evidence)
  - P4.5 ledger derivation + divergence cap
  - P4.4 LLM-grader gate (stub callers)
  - top-level pillar aggregation + bind-eligibility floor (>= 7.5)
  - per-dim YAML schema files load + match expected structure
"""
from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import score_p4_attestation as p4  # noqa: E402


def _form(**overrides):
    base = {
        "version": p4.SPEC_VERSION,
        "customer_id": "cust-001",
        "agent_id": "agent-001",
        "attested_at": "2026-05-11T00:00:00Z",
        "attested_by": {
            "name": "J. Doe",
            "title": "Head of AI",
            "email": "jdoe@example.com",
            "accountability": p4.ACCOUNTABILITY_LITERAL,
        },
        "signature_block": "sha256:deadbeef",
        "dims": {},
    }
    base.update(overrides)
    return base


# ---------------- form validation ----------------

class FormValidationTests(unittest.TestCase):
    def test_minimal_form_valid_when_all_dims_marked_empty(self):
        form = _form(dims={k.replace(".", "_"): {} for k in p4.DIM_TO_YAML})
        self.assertEqual(p4.validate_form(form), [])

    def test_missing_accountability_literal_rejected(self):
        form = _form(dims={k.replace(".", "_"): {} for k in p4.DIM_TO_YAML})
        form["attested_by"]["accountability"] = "I agree."
        errors = p4.validate_form(form)
        self.assertIn("attested_by.accountability missing required literal", errors)

    def test_wrong_spec_version_rejected(self):
        form = _form(version="9.9.9", dims={k.replace(".", "_"): {} for k in p4.DIM_TO_YAML})
        errors = p4.validate_form(form)
        self.assertTrue(any("version_not_supported" in e for e in errors))

    def test_missing_top_field_rejected(self):
        form = _form(dims={k.replace(".", "_"): {} for k in p4.DIM_TO_YAML})
        del form["signature_block"]
        errors = p4.validate_form(form)
        self.assertTrue(any("signature_block" in e for e in errors))

    def test_missing_dim_sentinel_reported(self):
        form = _form(dims={})
        errors = p4.validate_form(form)
        # All 6 dims missing
        missing = [e for e in errors if "dims.P4_" in e]
        self.assertEqual(len(missing), 6)


# ---------------- P4.1 audit log ----------------

class P4_1AuditLogTests(unittest.TestCase):
    def test_no_attestation_tier_0(self):
        r = p4._score_p4_1({})
        self.assertEqual((r.tier, r.raw), (0, 0.0))

    def test_zero_logged_surfaces_tier_0(self):
        r = p4._score_p4_1({"logged_surfaces": [{"surface": "prompt_in", "logged": False, "retention_days": 30}]})
        self.assertEqual(r.tier, 0)

    def test_claim_only_caps_at_tier_1(self):
        block = {
            "logged_surfaces": [{"surface": "prompt_in", "logged": True, "retention_days": 90}],
            "storage_mode": "append_only",
            "tamper_evidence": "hash_chain",
        }
        r = p4._score_p4_1(block)
        self.assertEqual((r.tier, r.raw), (1, 1.0))
        self.assertIn("tamper-resistance cap", r.cap_applied or "")

    def test_tier_2_documented(self):
        block = {
            "logged_surfaces": [
                {"surface": s, "logged": True, "retention_days": 90}
                for s in ("prompt_in", "model_response", "tool_call", "tool_response")
            ],
            "storage_mode": "mutable_with_audit",
            "tamper_evidence": "none",
            "evidence": {"retention_policy_doc": "policy.md"},
        }
        r = p4._score_p4_1(block)
        self.assertEqual(r.tier, 2)

    def test_tier_2_blocked_by_short_retention(self):
        block = {
            "logged_surfaces": [
                {"surface": s, "logged": True, "retention_days": 7}
                for s in ("prompt_in", "model_response", "tool_call", "tool_response")
            ],
            "storage_mode": "append_only",
            "tamper_evidence": "hash_chain",
            "evidence": {"retention_policy_doc": "policy.md"},
        }
        r = p4._score_p4_1(block)
        # retention < 30 fails tier 2 gate -> capped at 1
        self.assertEqual(r.tier, 1)

    def test_tier_3_operationalized(self):
        block = {
            "logged_surfaces": [
                {"surface": s, "logged": True, "retention_days": 90}
                for s in ("prompt_in", "model_response", "tool_call", "tool_response", "user_id_or_session")
            ],
            "storage_mode": "append_only",
            "tamper_evidence": "hash_chain",
            "evidence": {
                "retention_policy_doc": "policy.md",
                "log_sample_file": "sample.jsonl",
            },
        }
        r = p4._score_p4_1(block)
        self.assertEqual(r.tier, 3)


# ---------------- P4.2 version control ----------------

class P4_2VersionControlTests(unittest.TestCase):
    NOW = dt.date(2026, 5, 11)

    def test_all_absent_tier_0(self):
        block = {
            "version_registry": "none",
            "pin_policy": "latest_floating",
            "prompt_change_control": "no_control",
            "model_change_control": "no_control",
            "rollback_to_prior_supported": False,
        }
        r = p4._score_p4_2(block, now=self.NOW)
        self.assertEqual(r.tier, 0)

    def test_registry_no_evidence_tier_1(self):
        block = {
            "version_registry": "internal_git",
            "pin_policy": "pinned_per_release",
            "prompt_change_control": "pr_review_required",
            "model_change_control": "pr_review_required",
            "rollback_to_prior_supported": True,
        }
        r = p4._score_p4_2(block, now=self.NOW)
        self.assertEqual((r.tier, r.raw), (1, 1.0))

    def test_tier_2_documented(self):
        block = {
            "version_registry": "internal_git",
            "pin_policy": "pinned_per_release",
            "prompt_change_control": "single_approver",
            "model_change_control": "pr_review_required",
            "rollback_to_prior_supported": True,
            "evidence": {
                "change_log_link": "https://git/repo/log",
                "pin_policy_doc": "pin.md",
            },
        }
        r = p4._score_p4_2(block, now=self.NOW)
        self.assertEqual(r.tier, 2)

    def test_tier_3_operationalized_with_recent_releases(self):
        block = {
            "version_registry": "internal_git",
            "pin_policy": "pinned_per_release",
            "prompt_change_control": "pr_review_required",
            "model_change_control": "pr_review_required",
            "rollback_to_prior_supported": True,
            "evidence": {
                "change_log_link": "https://git/repo/log",
                "pin_policy_doc": "pin.md",
                "last_3_releases": [
                    {"release_id": "r1", "deployed_at": "2026-05-01", "model_id": "m1", "prompt_sha256": "a"},
                    {"release_id": "r2", "deployed_at": "2026-04-15", "model_id": "m1", "prompt_sha256": "b"},
                    {"release_id": "r3", "deployed_at": "2026-03-01", "model_id": "m1", "prompt_sha256": "c"},
                ],
            },
        }
        r = p4._score_p4_2(block, now=self.NOW)
        self.assertEqual(r.tier, 3)

    def test_tier_3_blocked_by_stale_releases(self):
        block = {
            "version_registry": "internal_git",
            "pin_policy": "pinned_per_release",
            "prompt_change_control": "pr_review_required",
            "model_change_control": "pr_review_required",
            "rollback_to_prior_supported": True,
            "evidence": {
                "change_log_link": "https://git/repo/log",
                "pin_policy_doc": "pin.md",
                "last_3_releases": [
                    {"release_id": "r1", "deployed_at": "2024-01-01", "model_id": "m1", "prompt_sha256": "a"},
                    {"release_id": "r2", "deployed_at": "2023-12-15", "model_id": "m1", "prompt_sha256": "b"},
                    {"release_id": "r3", "deployed_at": "2023-11-01", "model_id": "m1", "prompt_sha256": "c"},
                ],
            },
        }
        r = p4._score_p4_2(block, now=self.NOW)
        # tier 3 requires newest within 90d -> stays at 2
        self.assertEqual(r.tier, 2)


# ---------------- P4.3 kill switch ----------------

class P4_3KillSwitchTests(unittest.TestCase):
    NOW = dt.date(2026, 5, 11)

    def test_no_mechanism_tier_0(self):
        r = p4._score_p4_3({"kill_switch_mechanism": "none", "time_to_disable_minutes": 5,
                            "rollback_time_minutes": 30, "approval_required_to_invoke": "on_call_unilateral"},
                           now=self.NOW)
        self.assertEqual(r.tier, 0)

    def test_mechanism_no_runbook_tier_1(self):
        r = p4._score_p4_3({"kill_switch_mechanism": "feature_flag", "time_to_disable_minutes": 5,
                            "rollback_time_minutes": 30, "approval_required_to_invoke": "on_call_unilateral"},
                           now=self.NOW)
        self.assertEqual(r.tier, 1)

    def test_tier_2_documented(self):
        r = p4._score_p4_3({
            "kill_switch_mechanism": "feature_flag",
            "time_to_disable_minutes": 30,
            "rollback_time_minutes": 120,
            "approval_required_to_invoke": "single_approver",
            "evidence": {"runbook_link": "rb.md"},
        }, now=self.NOW)
        self.assertEqual(r.tier, 2)

    def test_tier_3_operationalized(self):
        r = p4._score_p4_3({
            "kill_switch_mechanism": "feature_flag",
            "time_to_disable_minutes": 30,
            "rollback_time_minutes": 120,
            "drill_cadence_days": 90,
            "last_drill_at": "2026-03-15",
            "approval_required_to_invoke": "single_approver",
            "evidence": {"runbook_link": "rb.md", "drill_log": "drill.md"},
        }, now=self.NOW)
        self.assertEqual(r.tier, 3)

    def test_tier_4_tested_verified(self):
        r = p4._score_p4_3({
            "kill_switch_mechanism": "feature_flag",
            "time_to_disable_minutes": 3,
            "rollback_time_minutes": 30,
            "drill_cadence_days": 90,
            "last_drill_at": "2026-04-15",
            "approval_required_to_invoke": "on_call_unilateral",
            "evidence": {"runbook_link": "rb.md", "drill_log": "drill.md"},
        }, now=self.NOW)
        self.assertEqual((r.tier, r.raw), (4, 4.0))

    def test_tier_4_blocked_by_slow_disable(self):
        r = p4._score_p4_3({
            "kill_switch_mechanism": "feature_flag",
            "time_to_disable_minutes": 10,  # > 5
            "rollback_time_minutes": 30,
            "drill_cadence_days": 90,
            "last_drill_at": "2026-04-15",
            "approval_required_to_invoke": "on_call_unilateral",
            "evidence": {"runbook_link": "rb.md", "drill_log": "drill.md"},
        }, now=self.NOW)
        self.assertEqual(r.tier, 3)


# ---------------- P4.4 IR playbook ----------------

class P4_4IRPlaybookTests(unittest.TestCase):
    NOW = dt.date(2026, 5, 11)

    def _tier3_floor_block(self):
        return {
            "playbook_exists": True,
            "scope_covered": ["prompt_injection", "hallucinated_action", "data_leak", "tool_misuse", "regulatory_inquiry"],
            "escalation_chain": [{"role": "oncall", "sla_minutes": 15}, {"role": "eng_lead", "sla_minutes": 60}],
            "external_notification_policy": "customer_within_72h",
            "evidence": {
                "playbook_doc": "playbook.md",
                "last_revision_at": "2026-03-01",
                "last_tabletop_at": "2026-02-01",
                "tabletop_artifact": "tabletop.md",
            },
        }

    def test_no_playbook_tier_0(self):
        r = p4._score_p4_4({"playbook_exists": False}, now=self.NOW)
        self.assertEqual(r.tier, 0)

    def test_doc_missing_tier_0(self):
        r = p4._score_p4_4({"playbook_exists": True}, now=self.NOW)
        self.assertEqual(r.tier, 0)

    def test_scope_lt_3_tier_1(self):
        r = p4._score_p4_4({
            "playbook_exists": True,
            "scope_covered": ["prompt_injection"],
            "escalation_chain": [{"role": "oncall", "sla_minutes": 15}],
            "external_notification_policy": "no_policy",
            "evidence": {"playbook_doc": "playbook.md"},
        }, now=self.NOW)
        self.assertEqual(r.tier, 1)

    def test_tier_2_documented(self):
        block = self._tier3_floor_block()
        # tier 2 floor met but no tabletop
        block["evidence"] = {
            "playbook_doc": "playbook.md",
            "last_revision_at": "2026-03-01",
        }
        r = p4._score_p4_4(block, now=self.NOW)
        self.assertEqual(r.tier, 2)

    def test_tier_3_grader_yes_offline_stays_2_without_grader(self):
        r = p4._score_p4_4(self._tier3_floor_block(), now=self.NOW, grader=None)
        self.assertEqual(r.tier, 2)
        self.assertTrue(any("grader_not_wired" in n for n in r.notes))

    def test_tier_3_grader_yes_with_stub_grader(self):
        r = p4._score_p4_4(self._tier3_floor_block(), now=self.NOW, grader=lambda _b: True)
        self.assertEqual((r.tier, r.raw), (3, 3.0))

    def test_tier_3_grader_no_stays_at_2(self):
        r = p4._score_p4_4(self._tier3_floor_block(), now=self.NOW, grader=lambda _b: False)
        self.assertEqual(r.tier, 2)

    def test_tier_4_auditor_letter(self):
        block = self._tier3_floor_block()
        block["evidence"]["auditor_letter"] = "auditor.pdf"
        r = p4._score_p4_4(block, now=self.NOW, grader=lambda _b: True)
        self.assertEqual((r.tier, r.raw), (4, 4.0))


# ---------------- P4.5 eval cadence ----------------

class P4_5EvalCadenceTests(unittest.TestCase):
    NOW = dt.date(2026, 5, 11)

    def _ledger(self, family_dates: dict[str, str]) -> dict:
        return {
            "customer_id": "cust-001",
            "attested_at": "2026-05-11T00:00:00Z",
            "families": [
                {
                    "family_id": fid,
                    "probe_count": 50,
                    "last_run_date": date,
                    "pass_rate": 0.9,
                }
                for fid, date in family_dates.items()
            ],
        }

    def test_no_ledger_tier_0(self):
        r = p4._score_p4_5({}, ledger=None, now=self.NOW)
        self.assertEqual(r.tier, 0)

    def test_zero_recent_families_tier_0(self):
        ledger = self._ledger({"hallucination_qa": "2024-01-01"})
        r = p4._score_p4_5({}, ledger=ledger, now=self.NOW)
        self.assertEqual(r.tier, 0)

    def test_ledger_present_no_policy_tier_1(self):
        ledger = self._ledger({"hallucination_qa": "2026-04-01"})
        r = p4._score_p4_5({}, ledger=ledger, now=self.NOW)
        self.assertEqual((r.tier, r.raw), (1, 1.0))

    def test_tier_2_documented(self):
        # 3 distinct families inside 180d
        ledger = self._ledger({
            "hallucination_qa": "2026-04-01",
            "citation_integrity": "2026-03-15",
            "bias_fairness": "2026-02-15",
        })
        r = p4._score_p4_5({"policy_doc_link": "cadence.md"}, ledger=ledger, now=self.NOW)
        self.assertEqual(r.tier, 2)

    def test_tier_3_operationalized(self):
        # 6 distinct families inside 90d
        ledger = self._ledger({
            f"family_{i}": "2026-04-15" for i in range(6)
        })
        r = p4._score_p4_5({"policy_doc_link": "cadence.md"}, ledger=ledger, now=self.NOW)
        self.assertEqual(r.tier, 3)

    def test_tier_4_with_low_divergence(self):
        # 10 distinct families inside 30d + low divergence
        ledger = self._ledger({
            f"family_{i}": "2026-05-01" for i in range(10)
        })
        divergence = {f"family_{i}": 0.02 for i in range(10)}
        r = p4._score_p4_5({"policy_doc_link": "cadence.md"}, ledger=ledger,
                           ledger_replay_divergence=divergence, now=self.NOW)
        self.assertEqual((r.tier, r.raw), (4, 4.0))

    def test_divergence_cap_at_2(self):
        # Tier 4 ladder, but >10pp divergence on one family -> cap raw at 2
        ledger = self._ledger({
            f"family_{i}": "2026-05-01" for i in range(10)
        })
        divergence = {f"family_{i}": 0.02 for i in range(10)}
        divergence["family_3"] = 0.15  # >10pp -> trigger cap
        r = p4._score_p4_5({"policy_doc_link": "cadence.md"}, ledger=ledger,
                           ledger_replay_divergence=divergence, now=self.NOW)
        self.assertEqual(r.tier, 2)
        self.assertIn("divergence_modifier", r.cap_applied or "")

    def test_tier_4_demoted_to_3_on_divergence_5_to_10pp(self):
        ledger = self._ledger({
            f"family_{i}": "2026-05-01" for i in range(10)
        })
        divergence = {f"family_{i}": 0.02 for i in range(10)}
        divergence["family_3"] = 0.07  # 5-10pp -> demote tier 4 to 3
        r = p4._score_p4_5({"policy_doc_link": "cadence.md"}, ledger=ledger,
                           ledger_replay_divergence=divergence, now=self.NOW)
        self.assertEqual(r.tier, 3)


# ---------------- P4.6 owner matrix ----------------

class P4_6OwnerMatrixTests(unittest.TestCase):
    NOW = dt.date(2026, 5, 11)

    def _all_5_roles(self, named=True):
        return [{"role_id": rid, "named_person": named}
                for rid in ("executive_sponsor", "deployment_owner", "model_owner", "on_call_responder", "compliance_owner")]

    def test_no_raci_tier_0(self):
        r = p4._score_p4_6({"roles": self._all_5_roles(), "succession_policy": "named_backup"}, now=self.NOW)
        self.assertEqual(r.tier, 0)

    def test_lt_3_named_tier_0(self):
        roles = self._all_5_roles(named=False)
        roles[0]["named_person"] = True
        roles[1]["named_person"] = True
        r = p4._score_p4_6({"roles": roles, "succession_policy": "named_backup", "evidence": {"raci_doc": "raci.md"}}, now=self.NOW)
        self.assertEqual(r.tier, 0)

    def test_tier_2_documented(self):
        r = p4._score_p4_6({
            "roles": self._all_5_roles(),
            "succession_policy": "no_policy",
            "evidence": {"raci_doc": "raci.md"},
        }, now=self.NOW)
        self.assertEqual(r.tier, 2)

    def test_tier_3_operationalized(self):
        r = p4._score_p4_6({
            "roles": self._all_5_roles(),
            "succession_policy": "named_backup",
            "last_reviewed_at": "2026-01-15",
            "evidence": {"raci_doc": "raci.md", "review_log": "review.md"},
        }, now=self.NOW)
        self.assertEqual(r.tier, 3)

    def test_tier_4_in_cadence(self):
        r = p4._score_p4_6({
            "roles": self._all_5_roles(),
            "succession_policy": "named_backup",
            "review_cadence_days": 180,
            "last_reviewed_at": "2026-04-01",
            "evidence": {"raci_doc": "raci.md", "review_log": "review.md"},
        }, now=self.NOW)
        self.assertEqual((r.tier, r.raw), (4, 4.0))


# ---------------- top-level aggregation ----------------

class TopLevelAggregationTests(unittest.TestCase):
    def test_zero_attestation_pillar_score_0(self):
        form = _form(dims={k.replace(".", "_"): {} for k in p4.DIM_TO_YAML})
        out = p4.score_form(form)
        self.assertEqual(out["pillar"], "P4")
        self.assertEqual(out["pillar_weight"], 15)
        self.assertEqual(out["pillar_score"], 0.0)
        self.assertFalse(out["bind_eligible_on_pillar"])

    def test_all_4s_pillar_score_15(self):
        # Build a form where every dim scores tier 4.
        now = dt.date(2026, 5, 11)
        ledger = {
            "customer_id": "cust-001",
            "attested_at": "2026-05-11T00:00:00Z",
            "families": [
                {"family_id": f"family_{i}", "probe_count": 50, "last_run_date": "2026-05-01", "pass_rate": 0.9}
                for i in range(10)
            ],
        }
        divergence = {f"family_{i}": 0.01 for i in range(10)}
        # P4.1 will stay at 3 in offline mode (no cross-verify evidence file)
        # P4.2 will stay at 3 in offline mode (no live agent)
        # P4.3 / P4.4 / P4.6 can hit 4 with right inputs and stub grader.
        # P4.5 will hit 4 with the ledger + low divergence.
        form = _form(dims={
            "P4_1": {
                "logged_surfaces": [
                    {"surface": s, "logged": True, "retention_days": 90}
                    for s in ("prompt_in", "model_response", "tool_call", "tool_response", "user_id_or_session")
                ],
                "storage_mode": "append_only",
                "tamper_evidence": "hash_chain",
                "evidence": {"retention_policy_doc": "policy.md", "log_sample_file": "sample.jsonl"},
            },
            "P4_2": {
                "version_registry": "internal_git",
                "pin_policy": "pinned_per_release",
                "prompt_change_control": "pr_review_required",
                "model_change_control": "pr_review_required",
                "rollback_to_prior_supported": True,
                "evidence": {
                    "change_log_link": "log.md",
                    "pin_policy_doc": "pin.md",
                    "last_3_releases": [
                        {"release_id": f"r{i}", "deployed_at": "2026-04-01", "model_id": "m1", "prompt_sha256": str(i)}
                        for i in range(3)
                    ],
                },
            },
            "P4_3": {
                "kill_switch_mechanism": "feature_flag",
                "time_to_disable_minutes": 3,
                "rollback_time_minutes": 30,
                "drill_cadence_days": 90,
                "last_drill_at": "2026-04-15",
                "approval_required_to_invoke": "on_call_unilateral",
                "evidence": {"runbook_link": "rb.md", "drill_log": "drill.md"},
            },
            "P4_4": {
                "playbook_exists": True,
                "scope_covered": ["prompt_injection", "hallucinated_action", "data_leak", "tool_misuse", "regulatory_inquiry"],
                "escalation_chain": [{"role": "oncall", "sla_minutes": 15}, {"role": "eng_lead", "sla_minutes": 60}],
                "external_notification_policy": "customer_within_24h",
                "evidence": {
                    "playbook_doc": "playbook.md",
                    "last_revision_at": "2026-03-01",
                    "last_tabletop_at": "2026-02-01",
                    "tabletop_artifact": "tabletop.md",
                    "auditor_letter": "auditor.pdf",  # tier 4
                },
            },
            "P4_5": {"policy_doc_link": "cadence.md"},
            "P4_6": {
                "roles": [{"role_id": rid, "named_person": True}
                          for rid in ("executive_sponsor", "deployment_owner", "model_owner", "on_call_responder", "compliance_owner")],
                "succession_policy": "named_backup",
                "review_cadence_days": 180,
                "last_reviewed_at": "2026-04-01",
                "evidence": {"raci_doc": "raci.md", "review_log": "review.md"},
            },
        })
        out = p4.score_form(form, ledger=ledger, ledger_replay_divergence=divergence,
                            grader=lambda _b: True, now=now)
        # Offline mode: P4.1=3, P4.2=3, P4.3=4, P4.4=4, P4.5=4, P4.6=4 -> mean 3.667 -> 13.75
        self.assertEqual(out["dims"]["P4.1"]["tier"], 3)
        self.assertEqual(out["dims"]["P4.2"]["tier"], 3)
        self.assertEqual(out["dims"]["P4.3"]["tier"], 4)
        self.assertEqual(out["dims"]["P4.4"]["tier"], 4)
        self.assertEqual(out["dims"]["P4.5"]["tier"], 4)
        self.assertEqual(out["dims"]["P4.6"]["tier"], 4)
        # mean = (3+3+4+4+4+4)/6 = 22/6 = 3.6667; pillar = 3.6667/4*15 = 13.75
        self.assertAlmostEqual(out["pillar_score"], 13.75, places=2)
        self.assertTrue(out["bind_eligible_on_pillar"])

    def test_pillar_floor_bind_eligibility(self):
        # Half tier 2, half tier 0 -> mean 1.0 -> pillar 3.75 -> NO BIND
        form = _form(dims={
            "P4_1": {
                "logged_surfaces": [
                    {"surface": s, "logged": True, "retention_days": 90}
                    for s in ("prompt_in", "model_response", "tool_call", "tool_response")
                ],
                "storage_mode": "mutable_with_audit",
                "tamper_evidence": "none",
                "evidence": {"retention_policy_doc": "policy.md"},
            },
            "P4_2": {},
            "P4_3": {},
            "P4_4": {},
            "P4_5": {},
            "P4_6": {},
        })
        out = p4.score_form(form)
        self.assertFalse(out["bind_eligible_on_pillar"])


# ---------------- YAML schemas load + structure ----------------

class YamlSchemaTests(unittest.TestCase):
    def test_all_6_yamls_load_and_have_evidence_tiers(self):
        for dim_id, filename in p4.DIM_TO_YAML.items():
            path = p4.PROBE_MAPPINGS / filename
            self.assertTrue(path.exists(), f"missing schema: {filename}")
            data = yaml.safe_load(path.read_text())
            self.assertEqual(data["status"][:8], "scaffold", f"{filename} not scaffold-tagged")
            self.assertIn("evidence_tiers", data, f"{filename} missing evidence_tiers")
            tiers = data["evidence_tiers"]
            self.assertEqual(set(tiers.keys()), {0, 1, 2, 3, 4}, f"{filename} tier keys wrong")
            # Tier 1 must have tamper-resistance cap rationale
            self.assertIn("cap_rationale", tiers[1], f"{filename} tier 1 missing cap_rationale")
            # Each must have a probes entry mapping to its pillar/dim
            probes = data["probes"]
            self.assertEqual(probes[0]["pillar"], "P4", f"{filename} probe not on P4")
            self.assertEqual(probes[0]["dim"], dim_id, f"{filename} dim mismatch")


if __name__ == "__main__":
    unittest.main()
