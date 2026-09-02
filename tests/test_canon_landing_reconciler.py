"""Tests for the canon master-landing evidence reconciler (BEAAA-24456).

Everything here is built from fixtures. Nothing asserts a commit count, a
landing count, or a named failing SHA — the reconciler queries the range, and a
test that froze today's answer would go red the next time someone lands
correctly.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import subprocess
import sys

import pytest

_MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "tools" / "canon" / "landing_reconciler.py"
_spec = importlib.util.spec_from_file_location("landing_reconciler", _MODULE_PATH)
lr = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["landing_reconciler"] = lr  # dataclasses resolves annotations via sys.modules
_spec.loader.exec_module(lr)


CTO = "agent-cto"
QA = "agent-qa"
ENGINEER = "agent-se"

SHA_LANDED = "8b0b340bf05b10134783d7c569047c0057dacb98"
SHA_CANDIDATE = "63441702bd1b68a5b0aa7898ee449dfe582e234c"
SHA_PREDECESSOR = "7f8be7a113d2418d4370117a430802303e43567b"


def comment(cid, author, body, created="2026-08-17T00:00:00Z"):
    return {"id": cid, "authorAgentId": author, "body": body, "createdAt": created}


def landing(sha=SHA_LANDED, parents=("71c5124d", SHA_CANDIDATE), subject="BEAAA-23945 pin", tree="t1",
            parent_tree="t0", side=()):
    head = lr.Commit(sha=sha, committed_at="2026-08-19T06:44:15+00:00", tree=tree,
                     parents=tuple(parents), subject=subject, body="")
    return lr.Landing(head=head, side=list(side), parent_tree=parent_tree)


def merged_ok(sha=SHA_LANDED, comment_id="m1"):
    """A merged_sha_on_canon_master_at_close leg that passes."""
    return lr.MergedShaEvidence(comment_id=comment_id, token=sha[:12], resolved=sha,
                                reachable=True, names_landing=True, field_present=True)


# --------------------------------------------------------------------------
# window parsing
# --------------------------------------------------------------------------


class TestParseWindow:
    now = dt.datetime(2026, 8, 19, 12, 0, tzinfo=dt.timezone.utc)

    @pytest.mark.parametrize("spec,expected_days", [("30d", 30), ("2w", 14), ("1d", 1)])
    def test_relative_windows(self, spec, expected_days):
        assert lr.parse_window(spec, self.now) == self.now - dt.timedelta(days=expected_days)

    def test_hours(self):
        assert lr.parse_window("12h", self.now) == self.now - dt.timedelta(hours=12)

    def test_iso_date_is_treated_as_utc(self):
        assert lr.parse_window("2026-07-20", self.now) == dt.datetime(2026, 7, 20, tzinfo=dt.timezone.utc)

    def test_rejects_nonsense(self):
        with pytest.raises(ValueError):
            lr.parse_window("last tuesday", self.now)


# --------------------------------------------------------------------------
# SHA and ticket extraction
# --------------------------------------------------------------------------


class TestShaNaming:
    def test_full_sha_names_itself(self):
        assert lr.sha_named_in(f"merged at {SHA_LANDED}", SHA_LANDED) == SHA_LANDED

    def test_short_prefix_names_the_sha(self):
        assert lr.sha_named_in("merged at `8b0b340b`", SHA_LANDED) == "8b0b340b"

    def test_six_chars_is_too_weak_to_be_evidence(self):
        assert lr.sha_named_in("see 8b0b34 for detail", SHA_LANDED) is None

    def test_a_different_sha_does_not_name_it(self):
        assert lr.sha_named_in(f"approved {SHA_CANDIDATE}", SHA_LANDED) is None

    def test_prefix_of_a_different_sha_does_not_name_it(self):
        assert lr.sha_named_in("approved `6344170`", SHA_LANDED) is None


class TestIssueRefs:
    def test_extracts_in_order_without_duplicates(self):
        text = "BEAAA-23945 impl\n\nSupersedes BEAAA-23943, see BEAAA-23945 again"
        assert lr.issue_refs(text) == ["BEAAA-23945", "BEAAA-23943"]

    def test_ignores_prose(self):
        assert lr.issue_refs("Fix the thing, no ticket here") == []


# --------------------------------------------------------------------------
# verdict vocabulary
# --------------------------------------------------------------------------


class TestQaVerdict:
    def test_plain_approved(self):
        assert lr.is_qa_approval("**APPROVED**\n\nCandidate `63441702`") is True

    @pytest.mark.parametrize("body", [
        "NOT APPROVED — the guard is missing",
        "CHANGES REQUESTED: the receipt overstates the count",
        "REJECTED, see below",
    ])
    def test_negative_verdicts_do_not_approve(self, body):
        assert lr.is_qa_approval(body) is False

    def test_no_verdict_at_all(self):
        assert lr.is_qa_approval("Started testing, will report back") is False

    @pytest.mark.parametrize("body", [
        "**APPROVED**\n\nCandidate `bf21e9d9`. Nothing is blocked by this landing.",
        "**APPROVED**\n\nThe earlier CHANGES REQUESTED verdict does not apply.",
        "QA gate closed. **BEAAA-23766 is APPROVED** for candidate `d3fed4e8`.\n"
        "A predecessor was rejected.",
    ])
    def test_negative_vocabulary_off_the_verdict_line_does_not_veto(self, body):
        assert lr.is_qa_approval(body) is True

    def test_negative_vocabulary_on_the_verdict_line_still_vetoes(self):
        assert lr.is_qa_approval("**NOT APPROVED** for candidate `bf21e9d9`") is False

    def test_unemphasized_line_leading_negative_vetoes(self):
        body = "CHANGES REQUESTED - previously APPROVED at abc1234."
        assert lr.is_qa_approval(body) is False

    @pytest.mark.parametrize("body", [
        "WITHDRAWN. The earlier APPROVED no longer stands.",
        "DECLINED. Candidate was APPROVED in round 1.",
        "REVOKED - the APPROVED verdict at abc1234 is retracted.",
        "NOT AN APPROVAL. This restates the APPROVED row only.",
        "DO NOT PROMOTE. APPROVED at abc1234 was previously recorded.",
    ])
    def test_each_line_leading_negative_verdict_token_vetoes_approval(self, body):
        assert lr.is_qa_approval(body) is False

    @pytest.mark.parametrize("body", [
        ("Gate closed APPROVED. Capsule f01bc27c, candidate fba6bd9b. "
         "SUPERSEDED: d1daf0f7 -> fba6bd9b."),
        ("Gate closed APPROVED. Capsule f01bc27c, candidate fba6bd9b. "
         "SUPERSEDED: d1daf0f7 -> fba6bd9b. No blockers remain."),
        ("**APPROVED** at `1ecec12e`. "
         "SUPERSEDED: `649b362e` -> `1ecec12e`."),
    ])
    def test_same_line_candidate_supersession_note_preserves_approval(self, body):
        assert lr.is_qa_approval(body) is True

    @pytest.mark.parametrize("body", [
        ("APPROVED (candidate-only): SUPERSEDED 0d2556f -> d4e8fa9. "
         "All prior findings fixed."),
        ("**APPROVED** - `SUPERSEDED` re-test\n\n"
         "`bd583ae53f7a80b2c8549c1bb28362a905ef52a5` -> "
         "`08b517a88cd0016dadebc2982367e1a70486a8e5`."),
        ("**APPROVED**QA gate PASSED for PR #148. "
         "This SHA resolves the sole CHANGES-REQUESTED blocker from my prior review."),
    ])
    def test_real_board_supersession_approval_shapes(self, body):
        assert lr.is_qa_approval(body) is True

    def test_earlier_negative_is_not_overridden_by_later_code_citation(self):
        body = (
            "**CHANGES REQUESTED**\n\n"
            "Discussion: `is_qa_approval(\"**APPROVED** - not blocked by "
            "**anything**\")` returns false."
        )
        assert lr.is_qa_approval(body) is False

    def test_separate_line_supersession_authoring_workaround_preserves_approval(self):
        body = (
            "SUPERSEDED: d1daf0f7 -> fba6bd9b.\n"
            "Gate closed APPROVED for candidate fba6bd9b."
        )
        assert lr.is_qa_approval(body) is True

    def test_unrelated_bold_spans_do_not_form_an_emphasized_negative_window(self):
        assert lr.is_qa_approval("**APPROVED** — not blocked by **anything**") is True

    @pytest.mark.parametrize("body", [
        "**APPROVED** — not blocked by anything",
        "APPROVED — not blocked by anything",
    ])
    def test_affirmative_no_blocker_clause_is_independent_of_emphasis(self, body):
        assert lr.is_qa_approval(body) is True

    def test_unrelated_bold_spans_do_not_veto_valid_approval(self):
        body = "**APPROVED** for abc1234 - **nothing** here is blocked, **nothing** rejected"
        assert lr.is_qa_approval(body) is True

    def test_line_leading_negative_still_vetoes_separately_emphasized_approval(self):
        assert lr.is_qa_approval("NOT APPROVED - this is not **APPROVED**") is False

    @pytest.mark.parametrize("body", [
        ("**Landed:** child **BEAAA-24612** created. No APPROVED verdict exists "
         "and nothing is authorized to merge or promote."),
        ("Reviewed **BEAAA-28616** at **a6d0fd16**: the APPROVED above is "
         "WITHDRAWN."),
        "Per **CTO** ruling on **28614**, that APPROVED is REVOKED.",
        "Build **a6d0fd16** on **master**: APPROVED but DO NOT PROMOTE.",
    ])
    def test_mid_line_negative_token_vetoes_regardless_of_emphasis_count(self, body):
        assert lr.is_qa_approval(body) is False

    @pytest.mark.parametrize("body", [
        "QA gate closed. **BEAAA-23766 is APPROVED** for candidate d3fed4e8; "
        "the earlier CHANGES REQUESTED verdict does not apply.",
        "Promotion was BLOCKED on the prior candidate; this candidate `71c5124d` is APPROVED.",
        "APPROVED for `71c5124d`. No probe was rejected during the run.",
        "Blocked on BEAAA-26434: QA verdict APPROVED for candidate b6a76118 stands unchanged.",
    ])
    def test_adjudicated_false_negatives_prefer_safe_manual_review(self, body):
        assert lr.is_qa_approval(body) is False

    @pytest.mark.parametrize("body", [
        "**CHANGES REQUESTED** - previously APPROVED at abc1234.",
        "Candidate `e48711e`. Not approved.",
    ])
    def test_asserted_negative_verdict_vetoes_same_line_approval(self, body):
        assert lr.is_qa_approval(body) is False

    @pytest.mark.parametrize(("body", "expected"), [
        ("**APPROVED**\nNothing is blocked by this landing.", True),
        ("QA gate closed. **BEAAA-23766 is APPROVED** for candidate d3fed4e8; "
         "the earlier CHANGES REQUESTED verdict does not apply.", False),
        ("Promotion was BLOCKED on the prior candidate; this candidate is APPROVED.", False),
        ("APPROVED for 71c5124d. No probe was rejected during the run.", False),
        ("**CHANGES REQUESTED**\nCandidate e48711e. Not approved.", False),
        ("**NOT APPROVED** for candidate deadbee.", False),
        ("**CHANGES REQUESTED** - previously APPROVED at abc1234.", False),
        ("Verdict: CHANGES REQUESTED. Previously APPROVED at abc1234.", False),
        ("Status: NOT APPROVED. Prior candidate was APPROVED at abc1234.", False),
        ("QA: REJECTED. The earlier run was APPROVED.", False),
        ("CHANGES REQUESTED. Previously APPROVED at abc1234.", False),
        ("**APPROVED** - not blocked by **anything**", True),
        ("**APPROVED** for abc1234 - **nothing** here is blocked, **nothing** rejected", True),
        ("Verdict: **CHANGES REQUESTED**. An earlier candidate was APPROVED.", False),
        ("Note - blocked items were resolved; this is APPROVED.", False),
        ("Result: __NOT APPROVED__ though abc1234 was APPROVED before.", False),
        ("Result: __REJECTED__ though abc1234 was APPROVED before.", False),
        ("__SUPERSEDED__ - prior APPROVED retracted.", False),
        ("1. CHANGES REQUESTED - abc1234 was APPROVED earlier.", False),
        ("We have not blocked anything here; candidate abc1234 is APPROVED.", False),
        ("Round 2: CHANGES REQUESTED. Previously APPROVED at abc1234.", False),
        ("Verdict for BEAAA-28616: CHANGES REQUESTED. Previously APPROVED at abc1234.", False),
        ("Final QA verdict for this gate: CHANGES REQUESTED. Previously APPROVED at abc1234.", False),
        ("R2 status: NOT APPROVED. Earlier candidate APPROVED at abc1234.", False),
        ("QA (round 2): REJECTED. The earlier run was APPROVED.", False),
        ("**Verdict**: CHANGES REQUESTED. Previously APPROVED at abc1234.", False),
        ("2026-09-02: CHANGES REQUESTED. Previously APPROVED at abc1234.", False),
        ("Gate 6e4f303e: CHANGES REQUESTED. Previously APPROVED at abc1234.", False),
        ("**SUPERSEDED -> re-staged on a4f2f3c0. Still BLOCKED "
         "(do-not-test-yet). No approved SHA stands.**", False),
        ("**SUPERSEDED - prior APPROVED retracted. Gate re-BLOCKED "
         "pending BEAAA-18645.**", False),
        ("Summary: BLOCKED, then fixed; now APPROVED.", False),
        ("Update: rejected, then corrected; APPROVED at abc1234.", False),
        ("We have not approved this candidate, though an earlier one was APPROVED.", False),
        ("The earlier candidate was NOT APPROVED; this run is APPROVED at abc1234.", False),
    ])
    def test_fail_closed_verdict_matrix_prefers_safe_manual_review(self, body, expected):
        assert lr.is_qa_approval(body) is expected

    @pytest.mark.parametrize("body", [
        "If approved, target traffic remains held until the board grants authorization.",
        "Same posture as when I approved it - CI, not review, and not this gate's scope.",
        "The candidate was previously approved at abc1234.",
    ])
    def test_lowercase_approval_prose_is_not_a_verdict(self, body):
        assert lr.is_qa_approval(body) is False

    def test_uppercase_approval_remains_a_verdict(self):
        assert lr.is_qa_approval("**APPROVED** for candidate abc1234.") is True

    @pytest.mark.parametrize("body", [
        "Summary.\n\nThis candidate is **NOT APPROVED**.\n\n**APPROVED**",
        "**APPROVED**\n\nRETRACTED: this approval is withdrawn, see F-9.",
        "My earlier **APPROVED** is RETRACTED.",
        "**APPROVED** at abc1234.\n\nREVOKED.",
    ])
    def test_body_level_retraction_vetoes_approval(self, body):
        assert lr.is_qa_approval(body) is False

    @pytest.mark.parametrize("body", [
        "**APPROVED**\n\nOne finding was raised and withdrawn on execution.",
        "**APPROVED**\n\nThe implications are narrowed or retracted.",
        "**APPROVED**\n\nUnknown/revoked/invalid signatures fail closed.",
        "**APPROVED**\n\nThe issuer is unknown or revoked.",
        "**APPROVED**\n\nThe superseded candidate is not approved by this verdict.",
        "**APPROVED**\n\nPrior-head history: `7831c6b` approved-then-withdrawn after rejection.",
    ])
    def test_evidence_narration_does_not_withdraw_current_approval(self, body):
        assert lr.is_qa_approval(body) is True

    @pytest.mark.parametrize("body", [
        "SUPERSEDED: <old> -> <CHANGES REQUESTED>. Previously APPROVED.",
        "APPROVED. SUPERSEDED: <old> -> <NOT APPROVED>.",
    ])
    def test_placeholder_does_not_swallow_negative_verdict(self, body):
        assert lr.is_qa_approval(body) is False


@pytest.mark.parametrize(("body", "expected"), [
    (
        "Classification: rule-execution under `spec@v2.1`. "
        "No rubric, judge, scoring, or tamper-resistance rule changed.",
        lr.RULE_EXECUTION,
    ),
    (
        "Provisional classification: rule execution, subject to N=5. "
        "Any delta greater than 1 reclassifies it as rule change.",
        lr.RULE_EXECUTION,
    ),
    ("classification: rule execution -> **rule change**", lr.RULE_CHANGE),
])
def test_classification_ignores_negated_or_conditional_tail(body, expected):
    assert lr.ticket_classification([body]) == expected


class TestCosignStrength:
    def test_explicit_grant(self):
        assert lr.cosign_strength("## CTO merge co-sign: GRANTED — `63441702`") == 2

    def test_grant_survives_markdown_emphasis_and_a_dash(self):
        body = "CTO co-sign \u2014 **APPROVED** for exact candidate `6fbb569e36be`"
        assert lr.cosign_strength(body, "6fbb569e36be") == 2

    def test_a_conditional_cosign_is_not_a_grant(self):
        body = "## CTO review \u2014 co-signed on the diff, one change required before merge"
        assert lr.cosign_strength(body, "63441702") == 1

    def test_recalling_an_earlier_cosign_is_not_a_grant(self):
        body = "it keeps `63441702` in history instead of rewriting a SHA I had already co-signed"
        assert lr.cosign_strength(body, "63441702") == 1

    @pytest.mark.parametrize("body", [
        "Merge co-sign granted earlier is REVOKED.",
        "CTO merge co-sign: **DENIED**. Do not merge.",
        "I am withholding the merge co-sign until QA clears.",
    ])
    def test_negative_cosign_disposition_is_not_a_grant_or_reference(self, body):
        assert lr.cosign_strength(body) == 0

    def test_negative_evidence_narration_does_not_cancel_cosign_reference(self):
        body = "Co-sign recorded on BEAAA-123. Revoked signatures fail closed."
        assert lr.cosign_strength(body) == 1

    def test_bare_assertion_naming_the_sha_is_a_grant(self):
        assert lr.cosign_strength("BLUF: co-signed `63441702`.", "63441702") == 2

    def test_subordinate_clause_is_only_a_reference(self):
        body = "recording a residual found while co-signing `63441702` on BEAAA-23957"
        assert lr.cosign_strength(body, "63441702") == 1

    def test_closure_report_citing_a_cosign_is_only_a_reference(self):
        body = "Merged to canon master at 8b0b340b. Co-sign b6d0d61f, QA APPROVED on BEAAA-23946."
        assert lr.cosign_strength(body, "8b0b340b") == 1

    def test_unrelated_comment(self):
        assert lr.cosign_strength("Rebased onto current master.") == 0


# --------------------------------------------------------------------------
# landing topology
# --------------------------------------------------------------------------


class TestLanding:
    def test_merge_evidence_covers_the_non_master_parent(self):
        """The co-sign names the candidate tip; the merge that lands it cannot."""
        item = landing()
        assert item.is_merge is True
        assert item.evidence_shas == [SHA_LANDED, SHA_CANDIDATE]

    def test_direct_push_evidence_is_the_commit_itself(self):
        item = landing(parents=("71c5124d",))
        assert item.is_merge is False
        assert item.evidence_shas == [SHA_LANDED]

    def test_side_branch_commits_are_members_not_separate_landings(self):
        side = [lr.Commit("aaa1111", "2026-08-18T00:00:00+00:00", "t", (), "wip", "")]
        item = landing(side=side)
        assert item.member_shas == [SHA_LANDED, "aaa1111"]

    def test_tree_identical_to_first_parent_changes_nothing(self):
        assert landing(tree="same", parent_tree="same").introduces_no_tree_change is True
        assert landing(tree="t1", parent_tree="t0").introduces_no_tree_change is False


# --------------------------------------------------------------------------
# evidence resolution
# --------------------------------------------------------------------------


class TestFindEvidence:
    def _find(self, comments, shas=(SHA_LANDED, SHA_CANDIDATE)):
        return lr.find_evidence(list(comments), list(shas), {CTO}, {QA})

    def test_cosign_and_approval_on_different_issues_both_resolve(self):
        found = self._find([
            comment("cosign-1", CTO, f"## CTO merge co-sign: GRANTED — `{SHA_CANDIDATE}`"),
            comment("qa-1", QA, f"**APPROVED**\n\nCandidate `{SHA_CANDIDATE}`"),
        ])
        assert (found.cosign_comment_id, found.qa_comment_id) == ("cosign-1", "qa-1")
        assert found.matched_sha == SHA_CANDIDATE

    def test_a_reference_to_a_cosign_is_not_a_cosign(self):
        found = self._find([
            comment("report-1", CTO, f"Merged at {SHA_LANDED}. Co-sign b6d0d61f, QA APPROVED elsewhere."),
        ])
        assert found.cosign_comment_id is None
        assert found.cosign_mention_id == "report-1"

    def test_the_grant_wins_over_the_later_report_that_cites_it(self):
        found = self._find([
            comment("report-1", CTO, f"Merged at {SHA_LANDED}. Co-sign granted earlier.",
                    created="2026-08-19T06:45:00Z"),
            comment("grant-1", CTO, f"## CTO merge co-sign: GRANTED — `{SHA_CANDIDATE}`",
                    created="2026-08-17T08:58:00Z"),
        ])
        assert found.cosign_comment_id == "grant-1"

    def test_approval_of_a_predecessor_is_not_an_approval_of_this_landing(self):
        found = self._find([
            comment("qa-old", QA, f"**APPROVED** candidate `{SHA_PREDECESSOR}`"),
        ])
        assert found.qa_comment_id is None
        assert any(SHA_PREDECESSOR.startswith(t) for t in found.qa_other_shas)

    def test_approval_from_a_non_qa_seat_does_not_count(self):
        found = self._find([comment("se-1", ENGINEER, f"**APPROVED** `{SHA_CANDIDATE}`")])
        assert found.qa_comment_id is None

    def test_cosign_from_a_non_cto_seat_does_not_count(self):
        found = self._find([comment("se-1", ENGINEER, f"co-sign: GRANTED `{SHA_CANDIDATE}`")])
        assert found.cosign_comment_id is None

    def test_changes_requested_naming_the_sha_does_not_reconcile(self):
        found = self._find([comment("qa-1", QA, f"CHANGES REQUESTED for `{SHA_CANDIDATE}`")])
        assert found.qa_comment_id is None


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------


class TestClassify:
    def test_every_applicable_leg_present_reconciles(self):
        row = lr.classify(landing(), "BEAAA-23945", "c1", "q1", SHA_CANDIDATE, [], [], None,
                          ticket_class=lr.RULE_CHANGE, merged=merged_ok())
        assert row.classification == lr.RECONCILED
        assert row.matched_sha == SHA_CANDIDATE
        assert row.findings == []

    def test_rule_execution_reconciles_without_any_co_sign(self):
        """The re-calibration, stated as a test: v2.0 asks rule execution for no
        co-sign, so its absence is not a finding and must not be reported as one."""
        row = lr.classify(landing(), "BEAAA-23945", None, "q1", SHA_CANDIDATE, [], [], None,
                          ticket_class=lr.RULE_EXECUTION, merged=merged_ok())
        assert row.classification == lr.RECONCILED
        assert lr.FINDING_COSIGN not in row.findings
        assert "co-sign" not in row.reason

    def test_unclassified_ticket_is_not_charged_a_co_sign(self):
        row = lr.classify(landing(), "BEAAA-23945", None, "q1", SHA_CANDIDATE, [], [], None,
                          ticket_class=lr.UNCLASSIFIED, merged=merged_ok())
        assert row.findings == []

    def test_missing_cosign_on_a_rule_change_is_a_finding_and_says_so(self):
        row = lr.classify(landing(), "BEAAA-23945", None, "q1", SHA_CANDIDATE, [], [], None,
                          ticket_class=lr.RULE_CHANGE, merged=merged_ok())
        assert row.classification == lr.UNRECONCILED
        assert row.findings == [lr.FINDING_COSIGN]
        assert "co-sign" in row.reason and "rule change" in row.reason

    def test_missing_both_lists_both_and_counts_both_legs(self):
        row = lr.classify(landing(), "BEAAA-23945", None, None, None, [], [], None,
                          ticket_class=lr.RULE_CHANGE, merged=merged_ok())
        assert row.classification == lr.UNRECONCILED
        assert "co-sign" in row.reason and "QA APPROVED" in row.reason
        assert set(row.findings) == {lr.FINDING_COSIGN, lr.FINDING_QA}

    def test_predecessor_approval_is_named_in_the_reason(self):
        row = lr.classify(landing(), "BEAAA-23945", "c1", None, None, [], [SHA_PREDECESSOR], None,
                          ticket_class=lr.RULE_CHANGE, merged=merged_ok())
        assert row.classification == lr.UNRECONCILED
        assert "predecessor" in row.reason and SHA_PREDECESSOR[:12] in row.reason
        assert row.findings == [lr.FINDING_QA]

    def test_a_cosign_reference_is_reported_by_comment_id(self):
        row = lr.classify(landing(), "BEAAA-23945", None, "q1", None, [], [], None,
                          cosign_mention_id="b015ec60-1a2b-4c3d-8e9f-000000000000",
                          ticket_class=lr.RULE_CHANGE, merged=merged_ok())
        assert "b015ec60" in row.reason

    def test_unresolvable_issue_is_a_finding_not_a_crash(self):
        row = lr.classify(landing(), None, None, None, None, [], [], None)
        assert row.classification == lr.UNRECONCILED
        assert "no Paperclip issue" in row.reason
        assert lr.FINDING_UNRESOLVED in row.findings

    def test_exempt_always_states_a_reason(self):
        row = lr.classify(landing(), None, None, None, None, [], [], "landed before the control")
        assert row.classification == lr.EXEMPT
        assert row.reason == "landed before the control"
        assert row.findings == []


class TestMergedShaLeg:
    """The universal leg: required on rule-change and rule-execution alike,
    because the gate matches commit-shape, not classification-shape."""

    def test_absent_field_is_a_finding_even_for_rule_execution(self):
        row = lr.classify(landing(), "BEAAA-23945", None, "q1", SHA_CANDIDATE, [], [], None,
                          ticket_class=lr.RULE_EXECUTION, merged=None)
        assert row.classification == lr.UNRECONCILED
        assert row.findings == [lr.FINDING_MERGED_SHA]
        assert "quotes no merged_sha_on_canon_master_at_close" in row.reason

    def test_a_field_present_but_uncited_reads_as_that_not_as_absent(self):
        merged = lr.MergedShaEvidence(comment_id="m1", field_present=True)
        row = lr.classify(landing(), "BEAAA-23945", None, "q1", SHA_CANDIDATE, [], [], None,
                          ticket_class=lr.RULE_EXECUTION, merged=merged)
        assert row.findings == [lr.FINDING_MERGED_SHA]
        assert "cites no SHA" in row.reason

    def test_cited_sha_that_does_not_resolve_is_a_finding(self):
        merged = lr.MergedShaEvidence(comment_id="m1", token="deadbee", resolved=None,
                                      field_present=True)
        row = lr.classify(landing(), "BEAAA-23945", None, "q1", SHA_CANDIDATE, [], [], None,
                          ticket_class=lr.RULE_EXECUTION, merged=merged)
        assert row.findings == [lr.FINDING_MERGED_SHA]
        assert "does not resolve" in row.reason

    def test_cited_sha_not_reachable_from_canon_head_is_a_finding(self):
        merged = lr.MergedShaEvidence(comment_id="m1", token=SHA_PREDECESSOR[:12],
                                      resolved=SHA_PREDECESSOR, reachable=False,
                                      field_present=True)
        row = lr.classify(landing(), "BEAAA-23945", None, "q1", SHA_CANDIDATE, [], [], None,
                          ticket_class=lr.RULE_EXECUTION, merged=merged)
        assert row.findings == [lr.FINDING_MERGED_SHA]
        assert "not reachable" in row.reason and SHA_PREDECESSOR[:12] in row.reason

    def test_legs_are_counted_apart(self):
        rows = [
            lr.classify(landing(), "BEAAA-1", None, "q1", SHA_CANDIDATE, [], [], None,
                        ticket_class=lr.RULE_CHANGE, merged=merged_ok()),
            lr.classify(landing(), "BEAAA-2", "c1", None, None, [], [], None,
                        ticket_class=lr.RULE_CHANGE, merged=merged_ok()),
            lr.classify(landing(), "BEAAA-3", None, "q1", SHA_CANDIDATE, [], [], None,
                        ticket_class=lr.RULE_EXECUTION, merged=None),
        ]
        assert lr.summarise_findings(rows) == {
            lr.FINDING_COSIGN: 1, lr.FINDING_QA: 1,
            lr.FINDING_MERGED_SHA: 1, lr.FINDING_UNRESOLVED: 0,
        }

    def test_an_unresolved_landing_is_a_subset_not_a_fourth_leg(self):
        row = lr.classify(landing(), None, None, None, None, [], [], None)
        counts = lr.summarise_findings([row])
        assert counts[lr.FINDING_UNRESOLVED] == 1
        assert counts[lr.FINDING_QA] == 1, "it is also missing the QA leg, as the ruling counts it"
        assert counts[lr.FINDING_COSIGN] == 0, "no ticket, so no classification demanding one"


class TestMergedShaCitations:
    def test_reads_the_value_after_the_field_name(self):
        body = "Closure.\n\n- `merged_sha_on_canon_master_at_close`: `70452ee1234`\n"
        assert "70452ee1234" in lr.merged_sha_citations(body)

    def test_accepts_the_separators_a_closure_actually_writes(self):
        body = "- merged-sha-on-canon-master-at-close = 8b0b340bf05\n"
        assert "8b0b340bf05" in lr.merged_sha_citations(body)

    def test_value_on_the_following_line_still_counts(self):
        assert "5410126335a0" in lr.merged_sha_citations(
            "merged_sha_on_canon_master_at_close:\n`5410126335a0`\n")

    def test_does_not_read_a_sha_from_a_later_paragraph(self):
        body = "merged_sha_on_canon_master_at_close: pending\n\nSee also 8b0b340bf05.\n"
        assert lr.merged_sha_citations(body) == []

    def test_a_pending_field_followed_by_an_attachment_id_cites_nothing(self):
        """Observed on BEAAA-19021: the next bullet carried an attachment UUID
        whose first segment is valid hex. Reading past the value invents a
        citation the author never made."""
        body = ("- `merged_sha_on_canon_master_at_close`: pending; candidate is "
                "intentionally not merged.\n"
                "- Artifact: [evidence](/api/attachments/82c80547-3110-4828-87d5-cc5bd/content)\n")
        assert lr.merged_sha_citations(body) == []
        assert lr.merged_sha_field_present(body) is True

    def test_a_quoted_negative_check_on_the_same_line_cites_nothing(self):
        """Observed on BEAAA-16741: the author wrote "not available" and quoted
        the failing ancestor command. The sha in that command is evidence
        *against* the claim, not the field's value."""
        body = ("- merged_sha_on_canon_master_at_close: not available; this is a branch "
                "candidate. `git merge-base --is-ancestor 1794dda6c4ee35b1 origin/master` "
                "returned exit 1.\n")
        assert lr.merged_sha_citations(body) == []
        assert lr.merged_sha_field_present(body) is True

    def test_prose_about_a_merged_sha_is_not_the_field(self):
        assert lr.merged_sha_citations("merged sha 8b0b340bf05 looks right") == []
        assert lr.merged_sha_field_present("merged sha 8b0b340bf05 looks right") is False


class TestTicketClassification:
    def test_field_form(self):
        text = "- Contract: `v1.3.0`; classification: rule execution; Scanner `0.5.0`"
        assert lr.ticket_classification([text]) == lr.RULE_EXECUTION

    def test_a_corrected_field_reads_as_the_corrected_value(self):
        assert lr.ticket_classification(
            ["- classification: rule execution -> **rule change**"]) == lr.RULE_CHANGE

    def test_a_later_comment_reclassifies(self):
        assert lr.ticket_classification([
            "classification: rule execution",
            "The board reclassified BEAAA-23857 as a **rule change** at 12:02:36.",
        ], "BEAAA-23857") == lr.RULE_CHANGE

    def test_declaration_about_this_ticket(self):
        assert lr.ticket_classification(["This ticket is a rule change."]) == lr.RULE_CHANGE

    def test_quoted_contract_text_does_not_classify_the_quoting_ticket(self):
        text = ("The new row: adding, removing or re-flooring scored cases at an existing "
                "`rubric_point_id` is a **rule change**, CTO co-sign required.")
        assert lr.ticket_classification([text]) == lr.UNCLASSIFIED

    def test_a_negated_sentence_classifies_nothing(self):
        assert lr.ticket_classification(
            ["This ticket is not a rule change in my view."]) == lr.UNCLASSIFIED

    @pytest.mark.parametrize("text", [
        'The prior comment said "classification: rule change".',
        '> classification: rule change',
        'The prior comment said "This ticket is a rule change."',
    ])
    def test_quoted_classification_classifies_nothing(self, text):
        assert lr.ticket_classification([text]) == lr.UNCLASSIFIED

    def test_negated_field_classifies_nothing(self):
        assert lr.ticket_classification(
            ["classification: not a rule change"]) == lr.UNCLASSIFIED

    def test_negated_rule_execution_field_classifies_nothing(self):
        assert lr.ticket_classification(
            ["classification: not rule execution"]) == lr.UNCLASSIFIED

    @pytest.mark.parametrize(("text", "expected"), [
        ('classification: "rule change"', lr.RULE_CHANGE),
        ('classification: "rule execution"', lr.RULE_EXECUTION),
        ('classification: “rule change”', lr.RULE_CHANGE),
        ('classification: “rule execution”', lr.RULE_EXECUTION),
        ('Per "the v2.0 table", classification: rule change', lr.RULE_CHANGE),
    ])
    def test_quoted_field_values_are_preserved(self, text, expected):
        assert lr.ticket_classification([text]) == expected

    @pytest.mark.parametrize("text", [
        "Classification: rule execution - this is not a rule change under the v2.0 table.",
        "classification: rule execution (no rubric_point_id, no scoring math, so not a rule change)",
    ])
    def test_negated_later_value_does_not_replace_execution(self, text):
        assert lr.ticket_classification([text]) == lr.RULE_EXECUTION

    def test_sentence_initial_no_correction_reclassifies_bare_field(self):
        text = "classification: rule execution. No - the scoring table moved, so rule change."
        assert lr.ticket_classification([text]) == lr.RULE_CHANGE

    @pytest.mark.parametrize("tail", [
        "Any delta greater than 1 would reclassify it as a rule change.",
        "If the delta moves we will re-classify it as a rule change.",
        "Any delta greater than 1 reclassifies it as rule change.",
    ])
    def test_hypothetical_reclassification_does_not_replace_current_field(self, tail):
        assert lr.ticket_classification([
            f"classification: rule execution. {tail}"
        ]) == lr.RULE_EXECUTION

    def test_silence_is_unclassified_not_a_rule_change(self):
        assert lr.ticket_classification(["Land the probe wrapper."]) == lr.UNCLASSIFIED

    def test_an_identifier_subject_is_accepted_for_its_own_ticket(self):
        assert lr.ticket_classification(
            ["## Classification corrected: BEAAA-23857 is a rule change"],
            "BEAAA-23857") == lr.RULE_CHANGE


# --------------------------------------------------------------------------
# rendering and exit code
# --------------------------------------------------------------------------


class TestRendering:
    rows = [
        lr.Row(SHA_LANDED, "2026-08-19T06:44:15+00:00", "BEAAA-23945", "b6d0d61f", "539a1809",
               lr.RECONCILED, "co-sign and QA APPROVED both name " + SHA_CANDIDATE),
        lr.Row("71c5124d994a", "2026-08-18T18:37:42+00:00", "BEAAA-24310", None, None,
               lr.UNRECONCILED, "no CTO co-sign naming an exact SHA of this landing"),
    ]

    def test_table_has_the_acceptance_columns(self):
        out = lr.render_table(self.rows)
        header = out.splitlines()[0]
        for column in ["SHA", "DATE", "ISSUE", "TICKET", "CO-SIGN", "QA", "MERGED-SHA",
                       "CLASS", "REASON"]:
            assert column in header

    def test_a_rule_execution_co_sign_cell_reads_not_applicable(self):
        row = lr.Row("a" * 40, "2026-08-19", "BEAAA-1", None, "q", lr.RECONCILED, "ok",
                     ticket_class=lr.RULE_EXECUTION)
        assert "n/a" in lr.render_table([row]).splitlines()[-1]
        assert "n/a" in lr.render_markdown([row])

    def test_a_rule_change_missing_its_co_sign_reads_as_absent(self):
        row = lr.Row("a" * 40, "2026-08-19", "BEAAA-1", None, "q", lr.UNRECONCILED, "no co-sign",
                     ticket_class=lr.RULE_CHANGE, findings=[lr.FINDING_COSIGN])
        assert "n/a" not in lr.render_table([row]).splitlines()[-1]

    def test_table_has_one_row_per_landing(self):
        assert len(lr.render_table(self.rows).splitlines()) == len(self.rows) + 2

    def test_markdown_escapes_pipes_in_the_reason(self):
        row = lr.Row("a" * 40, "2026-08-19", "BEAAA-1", None, None, lr.UNRECONCILED, "a | b")
        assert "a \\| b" in lr.render_markdown([row])

    def test_summary_counts_each_class(self):
        assert lr.summarise(self.rows) == {lr.RECONCILED: 1, lr.UNRECONCILED: 1, lr.EXEMPT: 0}


class TestShaSearchResolution:
    def test_sha_search_widens_to_owning_issue_neighbourhood(self):
        item = landing(subject="landing without a ticket reference")
        owner = {"id": "owner", "identifier": "BEAAA-9001", "description": "rule execution"}
        gate = {"id": "gate", "identifier": "BEAAA-9002"}
        comments = {
            "owner": [{
                "id": "merged", "authorAgentId": CTO, "createdAt": "2026-08-20T00:00:00Z",
                "body": f"mentions {SHA_CANDIDATE}\nmerged_sha_on_canon_master_at_close: {SHA_LANDED}",
            }],
            "gate": [{
                "id": "approved", "authorAgentId": QA, "createdAt": "2026-08-20T00:01:00Z",
                "body": f"**APPROVED** at {SHA_CANDIDATE}",
            }],
        }

        class Client:
            def search_issues(self, query):
                return [owner]

            def comments(self, issue_id):
                return comments[issue_id]

            def issue(self, issue_id):
                return owner if issue_id == "owner" else gate

            def neighbourhood(self, issue):
                return [owner, gate]

        class Repo:
            branch = "master"

            def resolve(self, rev):
                return SHA_LANDED if SHA_LANDED.startswith(rev) else None

            def is_ancestor(self, ancestor, descendant):
                return ancestor == SHA_LANDED

        row = lr.resolve_landing(item, Client(), Repo(), {CTO}, {QA}, None, SHA_LANDED)
        assert row.issue == "BEAAA-9001"
        assert row.qa_comment_id == "approved"
        assert lr.FINDING_QA not in row.findings


# --------------------------------------------------------------------------
# git layer, against a real throwaway repository
# --------------------------------------------------------------------------


@pytest.fixture
def canon(tmp_path):
    """A tiny repo with one direct push and one merge, mirrored the way the tool does."""
    origin = tmp_path / "origin"
    origin.mkdir()

    def git(*args, cwd=origin):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                              text=True).stdout.strip()

    git("init", "-q", "-b", "master")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "T")
    (origin / "a.txt").write_text("one\n")
    git("add", "a.txt")
    git("commit", "-q", "-m", "BEAAA-1 first")
    base = git("rev-parse", "HEAD")

    (origin / "a.txt").write_text("two\n")
    git("commit", "-qam", "BEAAA-2 direct push")
    direct = git("rev-parse", "HEAD")

    git("checkout", "-q", "-b", "feature", base)
    (origin / "b.txt").write_text("feature\n")
    git("add", "b.txt")
    git("commit", "-q", "-m", "BEAAA-3 candidate")
    candidate = git("rev-parse", "HEAD")
    git("checkout", "-q", "master")
    git("merge", "-q", "--no-ff", "-m", "Merge BEAAA-3 candidate", "feature")
    merge = git("rev-parse", "HEAD")

    repo = lr.CanonRepo(str(tmp_path / "mirror.git"), "local/canon", "master")
    repo.url = str(origin)
    repo.sync()
    return repo, {"base": base, "direct": direct, "candidate": candidate, "merge": merge}


class TestCanonRepo:
    def test_landings_follow_first_parent_only(self, canon):
        repo, shas = canon
        since = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)
        found = [item.sha for item in repo.landings(since, None)]
        assert shas["merge"] in found
        assert shas["direct"] in found
        assert shas["candidate"] not in found, "a merged branch commit is not its own landing"

    def test_merge_landing_absorbs_the_side_branch(self, canon):
        repo, shas = canon
        since = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)
        merge = next(i for i in repo.landings(since, None) if i.sha == shas["merge"])
        assert merge.is_merge is True
        assert shas["candidate"] in merge.evidence_shas
        assert shas["candidate"] in merge.member_shas

    def test_window_bounds_the_result(self, canon):
        repo, _ = canon
        future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
        assert repo.landings(future, None) == []

    def test_ancestry_backs_the_predecessor_finding(self, canon):
        repo, shas = canon
        assert repo.is_ancestor(shas["base"], shas["merge"]) is True
        assert repo.is_ancestor(shas["merge"], shas["base"]) is False

    def test_unknown_revision_resolves_to_none(self, canon):
        repo, _ = canon
        assert repo.resolve("deadbeefdeadbeef") is None

    def test_sync_is_idempotent(self, canon):
        repo, shas = canon
        repo.sync()
        assert repo.resolve(shas["merge"]) == shas["merge"]


class TestCli:
    def test_exits_non_zero_when_a_landing_is_unreconciled(self, monkeypatch, capsys):
        rows = [lr.Row("a" * 40, "2026-08-19", "BEAAA-1", None, None, lr.UNRECONCILED, "no co-sign")]
        self._run(monkeypatch, rows)
        assert lr.main(["--no-fetch"]) == 1

    def test_exits_zero_when_every_landing_reconciles(self, monkeypatch):
        rows = [lr.Row("a" * 40, "2026-08-19", "BEAAA-1", "c", "q", lr.RECONCILED, "ok")]
        self._run(monkeypatch, rows)
        assert lr.main(["--no-fetch"]) == 0

    def test_exempt_alone_does_not_fail_the_run(self, monkeypatch):
        rows = [lr.Row("a" * 40, "2026-08-19", None, None, None, lr.EXEMPT, "pre-control")]
        self._run(monkeypatch, rows)
        assert lr.main(["--no-fetch"]) == 0

    def test_missing_credentials_exit_two_without_calling_anything(self, monkeypatch):
        for var in ["PAPERCLIP_API_URL", "PAPERCLIP_API_KEY", "PAPERCLIP_COMPANY_ID"]:
            monkeypatch.delenv(var, raising=False)
        assert lr.main(["--no-fetch"]) == 2

    def test_unknown_gate_roles_exit_two_rather_than_fail_every_landing(self, monkeypatch):
        self._run(monkeypatch, [], roles={"engineer": {"x"}})
        assert lr.main(["--no-fetch"]) == 2

    def test_missing_canon_ref_exits_two_before_reading_landings(
            self, monkeypatch, canon, capsys):
        repo, _ = canon
        for var, value in {
            "PAPERCLIP_API_URL": "http://api.invalid",
            "PAPERCLIP_API_KEY": "k",
            "PAPERCLIP_COMPANY_ID": "c",
        }.items():
            monkeypatch.setenv(var, value)

        assert lr.main([
            "--no-fetch", "--cache-dir", repo.dir, "--branch", "missing-ref",
        ]) == 2
        assert "cannot resolve canon missing-ref HEAD" in capsys.readouterr().err

    @pytest.mark.parametrize("failure", [
        subprocess.CalledProcessError(128, ["git", "clone"], stderr="repository not found"),
        subprocess.CalledProcessError(128, ["git", "fetch"], stderr="remote branch missing"),
    ])
    def test_default_fetch_sync_failure_exits_two_without_traceback(
            self, monkeypatch, capsys, failure):
        for var, value in {
            "PAPERCLIP_API_URL": "http://api.invalid",
            "PAPERCLIP_API_KEY": "k",
            "PAPERCLIP_COMPANY_ID": "c",
        }.items():
            monkeypatch.setenv(var, value)
        monkeypatch.setattr(lr.CanonRepo, "sync", lambda self: (_ for _ in ()).throw(failure))

        assert lr.main([]) == 2
        err = capsys.readouterr().err
        assert "cannot sync canon" in err
        assert failure.stderr in err
        assert "Traceback" not in err

    def _run(self, monkeypatch, rows, roles=None):
        monkeypatch.setenv("PAPERCLIP_API_URL", "http://api.invalid")
        monkeypatch.setenv("PAPERCLIP_API_KEY", "k")
        monkeypatch.setenv("PAPERCLIP_COMPANY_ID", "c")
        monkeypatch.setattr(lr.CanonRepo, "landings", lambda self, *a: [landing()] * len(rows))
        monkeypatch.setattr(lr.CanonRepo, "head", lambda self: SHA_LANDED)
        monkeypatch.setattr(lr.PaperclipClient, "agents_by_role",
                            lambda self: roles if roles is not None else {"cto": {CTO}, "qa": {QA}})
        it = iter(rows)
        monkeypatch.setattr(lr, "resolve_landing", lambda *a, **k: next(it))
