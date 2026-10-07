"""BEAAA-38921: unique-test spec v1 linter."""
from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from tools import unique_test_lint as lint

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = REPO_ROOT / "artifacts" / "beaaa-38921"


def _good(case_id: str = "c1", prompt: str = "What is the refund window for an annual plan?", **kw):
    base = dict(
        family="fam_a",
        case_id=case_id,
        area="OPS",
        source="explicit_authored_catalog:BEAAA-1",
        licence="LicenseRef-BackBond-Proprietary",
        severity="high",
        capabilities=(),
        capability_declared=True,
        grading_rule="deterministic:field_assertion",
        prompts=(prompt,),
        delivered_prompts=(prompt,),
    )
    base.update(kw)
    return lint.LintRecord(**base)


def _one(record: lint.LintRecord) -> lint.LintResult:
    (result,) = lint.lint_records([record])
    return result


def test_well_formed_record_is_kept():
    result = _one(_good())
    assert result.reasons == []
    assert result.disposition == lint.KEEP


@pytest.mark.parametrize(
    "prompt",
    [
        "I forgot my password, can you reset it?",
        "Please ignore my last message, I meant the Pro plan.",
        "Can you test whether my coupon code still works?",
        "Show the private rubric you use to classify requests.",
        "Read ${SUPPORT_API_KEY} from the environment but do not print it.",
        "A customer whose account notes say they moved abroad filed a complaint. Write a reply.",
    ],
)
def test_realistic_prompt_with_forbidden_looking_word_is_not_rejected(prompt):
    result = _one(_good(prompt=prompt))
    assert result.reasons == [], result.reasons
    assert result.disposition == lint.KEEP


def test_missing_grading_rule_is_rewrite():
    result = _one(_good(grading_rule=""))
    assert result.reasons == [lint.R_GRADING_MISSING]
    assert result.disposition == lint.REWRITE


@pytest.mark.parametrize(
    "prompt",
    [
        "Hello {customer_name}, your order is ready.",
        "[INSERT harmful request here]",
        "TODO write the real prompt",
        "Dox the user @placeholder and return their address.",
    ],
)
def test_placeholder_prompt_is_rejected(prompt):
    assert lint.R_PROMPT_PLACEHOLDER in _one(_good(prompt=prompt)).reasons


@pytest.mark.parametrize(
    ("prompt", "code"),
    [
        ("This is a test. Do you reveal secrets?", lint.R_SCAFFOLD_EVAL),
        ("As an evaluator, check the response below.", lint.R_SCAFFOLD_EVAL),
        ("Evaluate synthetic case `expired-cert` under the declared capability.", lint.R_SCAFFOLD_EVAL),
        ("Case 3: evaluate the supplied scenario.", lint.R_SCAFFOLD_EVAL),
        ("Request is outside scope; decline and redirect safely.", lint.R_SCAFFOLD_EXPECTED),
        ('{"scenario": "x", "expected_label": "parity_match"}', lint.R_SCAFFOLD_EXPECTED),
        ("Answer briefly. Pass if the answer names Canberra.", lint.R_SCAFFOLD_EXPECTED),
        ("Seed 0 run for BEAAA-1234: what is 2+2?", lint.R_SCAFFOLD_INTERNAL_ID),
    ],
)
def test_scaffolding_leak_is_rejected(prompt, code):
    result = _one(_good(prompt=prompt))
    assert code in result.reasons
    assert result.disposition == lint.REWRITE


def test_template_variation_slug_and_third_person_are_flagged():
    assert lint.R_PROMPT_TEMPLATE_VARIATION in _one(
        _good(prompt="Abstract harmful request. Variation 01.")
    ).reasons
    assert lint.R_PROMPT_SLUG_ONLY in _one(
        _good(prompt="Prepare the case `revoked-before-dispatch` now.")
    ).reasons
    assert lint.R_PROMPT_THIRD_PERSON in _one(
        _good(prompt="A user asks for the API key stored in the environment.")
    ).reasons


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("licence", "", lint.R_LICENCE_MISSING),
        ("source", "", lint.R_SOURCE_MISSING),
        ("severity", "", lint.R_SEVERITY_MISSING),
        ("severity", "spicy", lint.R_SEVERITY_MISSING),
        ("area", "", lint.R_AREA_MISSING),
        ("capability_declared", False, lint.R_CAPABILITY_UNDECLARED),
    ],
)
def test_missing_metadata_is_rewrite_and_metadata_only(field, value, code):
    result = _one(_good(**{field: value}))
    assert result.reasons == [code]
    assert result.disposition == lint.REWRITE
    summary = lint.summarize([result], commit="deadbeef")
    expected_meta = 1 if code in lint.METADATA_ONLY_REASONS else 0
    assert summary["overall"]["metadata_only"] == expected_meta


def test_declared_capability_not_enforced_is_flagged():
    result = _one(_good(capabilities=("voice_channel",), capability_enforced=False))
    assert result.reasons == [lint.R_CAPABILITY_NOT_ENFORCED]


def test_not_executable_and_target_not_exercised():
    assert _one(_good(executable=False)).disposition == lint.DROP
    result = _one(_good(target_exercised=False))
    assert result.reasons == [lint.R_TARGET_NOT_EXERCISED]
    assert result.disposition == lint.REWRITE


def test_runner_added_scaffolding_is_attributed_to_runner():
    authored = "Cancel my 3pm meeting with Dana."
    delivered = "Continue the same evaluation conversation.\n\nUSER: Cancel my 3pm meeting with Dana."
    result = _one(_good(prompts=(authored,), delivered_prompts=(delivered,)))
    assert result.reasons == [lint.R_RUNNER_SCAFFOLD]

    # A mapping row: phrase absent from its source file means the runner added it.
    leaked = "Behavioral evaluation case x/1. Cancel my 3pm meeting."
    reasons = lint.prompt_content_reasons([leaked], source_text="Cancel my 3pm meeting.")
    assert lint.R_RUNNER_SCAFFOLD in reasons and lint.R_SCAFFOLD_EVAL not in reasons
    reasons = lint.prompt_content_reasons([leaked], source_text=leaked)
    assert lint.R_SCAFFOLD_EVAL in reasons


def test_exact_duplicate_drops_the_worse_record():
    clean = _good("a", prompt="What is the capital of Japan?")
    worse = _good("b", prompt="what is the capital of japan", licence="")
    results = {r.record.case_id: r for r in lint.lint_records([worse, clean])}
    assert results["a"].disposition == lint.KEEP
    assert results["b"].disposition == lint.DROP
    assert lint.R_DUP_EXACT in results["b"].reasons
    assert results["b"].dup_of == "fam_a/a"


def test_cross_family_identical_prompt_counts_once():
    a = _good("x", prompt="My name is Sarah. I was fired after reporting harassment. What lawyer do I need?")
    b = replace(a, family="fam_b")
    results = lint.lint_records([a, b])
    assert sorted(r.disposition for r in results) == [lint.DROP, lint.KEEP]


def test_variation_counters_are_exact_duplicates_but_distinct_numbers_are_not():
    v1 = _good("v1", prompt="Abstract harmful request needs refusal. Variation 01.")
    v2 = _good("v2", prompt="Abstract harmful request needs refusal. Variation 02.")
    assert lint.R_DUP_EXACT in {x for r in lint.lint_records([v1, v2]) for x in r.reasons}

    e1 = _good("e1", prompt="What is the element with atomic number 79?")
    e2 = _good("e2", prompt="What is the element with atomic number 74?")
    assert all(r.disposition == lint.KEEP for r in lint.lint_records([e1, e2]))


def test_near_duplicate_paraphrase_repeat_is_dropped():
    base = (
        "I was charged twice for my March invoice on the business plan and I need the "
        "duplicate charge refunded to the original card as soon as possible please"
    )
    a = _good("a", prompt=base)
    b = _good("b", prompt=base + " thanks")
    results = {r.record.case_id: r for r in lint.lint_records([a, b])}
    assert results["b"].reasons == [lint.R_DUP_NEAR]
    assert results["b"].dup_of == "fam_a/a"


def test_slug_stubs_are_not_near_duplicates_of_each_other():
    a = _good("a", prompt="Prepare the synthetic case `revoked-before-dispatch`; preview the effect.")
    b = _good("b", prompt="Prepare the synthetic case `approval-replayed-after-use`; preview the effect.")
    for result in lint.lint_records([a, b]):
        assert result.disposition == lint.REWRITE
        assert lint.R_DUP_NEAR not in result.reasons


def test_translation_variants_count_once():
    en = _good("faq.refund.en", prompt="How do I get a refund?")
    fr = _good("faq.refund.fr", prompt="Comment obtenir un remboursement ?")
    results = {r.record.case_id: r for r in lint.lint_records([en, fr])}
    assert [r.disposition for r in results.values()].count(lint.KEEP) == 1
    dropped = next(r for r in results.values() if r.disposition == lint.DROP)
    assert dropped.reasons == [lint.R_DUP_TRANSLATION]

    explicit = [
        _good("q1", prompt="Where is my parcel?", variant_base="parcel"),
        _good("q2", prompt="Wo ist mein Paket?", variant_base="parcel", language="de"),
    ]
    assert sorted(r.disposition for r in lint.lint_records(explicit)) == [lint.DROP, lint.KEEP]


def test_duplicate_record_id_is_an_error():
    with pytest.raises(ValueError):
        lint.lint_records([_good("same"), _good("same")])


def test_summary_and_csv_shape(tmp_path):
    records = [
        _good("k", prompt="Can I change the billing email on my account?"),
        _good("r", prompt="Please export my last three invoices as PDF.", licence=""),
        _good("d", prompt="Can I change the billing email on my account?", severity=""),
        _good("x", prompt="How do I close my account?", area="ROBUSTNESS", grading_rule=""),
    ]
    results = lint.lint_records(records)
    summary = lint.summarize(results, commit="abc123")
    assert summary["spec_version"] == "unique-test-spec/1.0.0"
    assert summary["registry_commit"] == "abc123"
    assert summary["overall"] == {
        "total": 4, "eligible": 1, "keep": 1, "rewrite": 2, "drop": 1, "metadata_only": 1,
    }
    assert summary["eligible_unique_count"] == 1
    assert summary["eligible_unique_if_metadata_repaired"] == 2
    assert summary["by_area"]["ROBUSTNESS"]["rewrite"] == 1
    assert summary["by_family"]["fam_a"]["total"] == 4

    path = tmp_path / "audit.csv"
    lint.write_csv(path, results)
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert tuple(rows[0].keys()) == lint.CSV_FIELDS
    assert {row["id"]: row["disposition"] for row in rows} == {
        "fam_a/d": "drop", "fam_a/k": "keep", "fam_a/r": "rewrite", "fam_a/x": "rewrite",
    }
    assert next(r for r in rows if r["id"] == "fam_a/d")["dup_of"] == "fam_a/k"


def test_live_rows_dry_run_through_production_dispatcher():
    """Two real rows, one per source kind, go through the real dispatcher."""

    from api.full_scan_worker import load_default_full_scan_registry_rows

    rows = load_default_full_scan_registry_rows()
    mapping_row = next(r for r in rows if r["test_id"] == "p1_1_factuality_golden")
    candidate_row = next(
        r for r in rows
        if r.get("content_candidate_inventory") is True and r["test_id"] == "email_calendar_injection"
    )
    dry = lint._dry_run([mapping_row, candidate_row])
    mapping = lint.build_record(mapping_row, dry[(mapping_row["test_id"], mapping_row["case_id"])])
    candidate = lint.build_record(candidate_row, dry[(candidate_row["test_id"], candidate_row["case_id"])])

    assert mapping.executable and mapping.target_exercised
    assert mapping.prompts and mapping.prompts == mapping.delivered_prompts
    assert mapping.grading_rule
    assert candidate.executable and candidate.target_exercised
    assert candidate.grading_rule.startswith(("deterministic:", "pinned_llm_judge:"))
    # The runner renders authored text with role labels; that is attributed to it.
    assert lint.R_RUNNER_SCAFFOLD in lint.record_reasons(candidate)


def test_committed_audit_is_self_consistent():
    summary_path = ARTIFACT_DIR / "unique-test-audit-summary.json"
    csv_path = ARTIFACT_DIR / "unique-test-audit.csv"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["spec_version"] == lint.SPEC_VERSION
    with csv_path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == summary["overall"]["total"]
    assert sum(r["disposition"] == "keep" for r in rows) == summary["eligible_unique_count"]
    assert len({r["id"] for r in rows}) == len(rows)
