"""BEAAA-14423 [F10] Distinct-pair accounting for materialized text cases.

Defect (CTO reproduction on origin/master@7f2dc61, BEAAA-14406 comment
a00ce54c): ``_stable_select`` maps N distinct ``case_id`` values onto a smaller
fixture pool by independent hashing, so several text-compatible target-bound
registry rows materialize to *byte-identical* ``(prompts, expected)`` payloads.
Counting those as distinct evidence over-weights single probes and overstates
distinct coverage — concentrated in ROBUSTNESS.

Bug-fix discipline (issue AC #4): this test first reproduces the collapse that
exists today — ``distinct_case_count`` (unique normalized ``(prompts, expected)``
pairs) is strictly less than ``runnable_case_count`` (203 raw rows) — and then
pins the post-fix behavior:

* distinct accounting keys off unique pairs, never raw row count;
* a collision does not increment distinct coverage;
* non-strict validation WARNs (the scan still runs all 203 rows) and exposes a
  structured collapse table as evidence, but does not hard-fail;
* strict / CI mode fails-closed so the registry cannot silently over-declare
  distinct coverage.
"""
from __future__ import annotations

import unittest

from tools.active_test_registry import (
    build_registry,
    count_target_bound_runtime,
    distinct_case_collapses,
    distinct_case_counts,
    load_published_count,
    runtime_case_identity,
    validate_registry,
)
from tools.text_case_materializer import TextCaseSpec, text_case_identity


# The five collapsing families from the CTO reproduction (declared -> distinct).
# Every one is a ROBUSTNESS family; the double-count concentrates there.
EXPECTED_COLLAPSES = {
    "garak": (9, 8),
    "garak_beaaa152": (18, 15),
    "jailbreakbench": (8, 7),
    "p3_2_indirect_injection_rob": (12, 8),
    "p3_3_secret_exfiltration_rob": (12, 7),
}


class DistinctCaseCountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = build_registry()

    # ---- reproduction: the collapse exists today ---------------------------
    def test_distinct_case_count_is_below_runnable_case_count(self) -> None:
        counts = distinct_case_counts(self.rows)
        runnable = count_target_bound_runtime(self.rows)
        self.assertEqual(runnable, 327)
        # 327 rows still execute — they are simply not all distinct.
        self.assertEqual(counts["declared_total"], 327)
        self.assertLess(counts["total"], runnable)  # <-- reproduces the collapse
        # Global unique (prompts, expected) pairs — matches the CTO reproduction
        # Lane A adds 124 distinct reported-not-scored cases to the prior 180.
        self.assertEqual(counts["total"], 304)
        self.assertEqual(counts["declared_total"] - counts["total"], 23)

    def test_total_reconciles_intra_and_cross_family_collapse(self) -> None:
        # 23 total dupes = 14 within-family + 9 shared across families.
        counts = distinct_case_counts(self.rows)
        intra = sum(c["collapsed"] for c in distinct_case_collapses(self.rows))
        self.assertEqual(intra, 14)
        self.assertEqual(counts["cross_family_collapsed"], 9)
        self.assertEqual(
            sum(counts["by_family"].values()) - counts["total"],
            counts["cross_family_collapsed"],
        )
        self.assertEqual(
            counts["declared_total"] - counts["total"],
            intra + counts["cross_family_collapsed"],
        )

    def test_identity_collapses_byte_identical_pairs(self) -> None:
        # Same normalized (prompts, expected) -> same identity even when the
        # judge criteria / provenance labels differ; a different oracle splits it.
        base = dict(
            source_mode="native_local_fixture",
            source_ref="ref",
        )
        a = TextCaseSpec(prompts=("hi",), expected={"x": 1}, judge_criteria="c1", **base)
        b = TextCaseSpec(
            prompts=("hi",),
            expected={"x": 1},
            judge_criteria="c2-different",
            source_mode="native_local_fixture",
            source_ref="ref-different",
        )
        c = TextCaseSpec(prompts=("hi",), expected={"x": 2}, judge_criteria="c1", **base)
        self.assertEqual(text_case_identity(a), text_case_identity(b))
        self.assertNotEqual(text_case_identity(a), text_case_identity(c))

    def test_per_family_collapse_matches_cto_reproduction(self) -> None:
        collapses = {
            c["family"]: (c["declared"], c["distinct"])
            for c in distinct_case_collapses(self.rows)
        }
        for family, (declared, distinct) in EXPECTED_COLLAPSES.items():
            self.assertIn(family, collapses, msg=f"{family} should collapse")
            self.assertEqual(collapses[family], (declared, distinct), msg=family)
            self.assertEqual(
                collapses[family][0] - collapses[family][1],
                declared - distinct,
            )
        # Exactly these families collapse — no more, no fewer.
        self.assertEqual(set(collapses), set(EXPECTED_COLLAPSES))

    # ---- post-fix behavior --------------------------------------------------
    def test_collision_does_not_increment_distinct_coverage(self) -> None:
        counts = distinct_case_counts(self.rows)
        for family, (declared, distinct) in EXPECTED_COLLAPSES.items():
            self.assertEqual(counts["declared_by_family"][family], declared, msg=family)
            self.assertEqual(counts["by_family"][family], distinct, msg=family)
            self.assertLess(counts["by_family"][family], counts["declared_by_family"][family])

    def test_runtime_identity_is_content_only_and_label_independent(self) -> None:
        # A text-compatible row's identity is the pure content hash of its
        # materialized (prompts, expected) — no family/label prefix — so a
        # byte-identical pair collapses across families, matching the criterion's
        # "unique normalized (prompts, expected) pairs".
        from tools.text_case_materializer import (
            is_text_compatible_test,
            materialize_text_case,
        )

        text_row = next(
            r
            for r in self.rows
            if r["target_bound"] is True
            and r["case_class"] != "attestation"
            and is_text_compatible_test(str(r["test_id"]))
        )
        spec = materialize_text_case(
            str(text_row["test_id"]), str(text_row["case_id"]), text_row
        )
        self.assertEqual(runtime_case_identity(text_row), text_case_identity(spec))
        # No family label leaks into the identity.
        self.assertNotIn(str(text_row["test_id"]), runtime_case_identity(text_row))

    def test_non_strict_validation_warns_but_does_not_fail(self) -> None:
        # Scan path: collapse is surfaced as evidence, not a hard error — the
        # 287 target-bound rows still execute.
        errors = validate_registry(self.rows, published_count=load_published_count())
        self.assertFalse([e for e in errors if "distinct coverage" in e], errors)

    def test_strict_mode_fails_closed_on_over_declared_distinct_coverage(self) -> None:
        errors = validate_registry(
            self.rows, published_count=load_published_count(), strict=True
        )
        collapse_errors = [e for e in errors if "distinct coverage" in e]
        self.assertTrue(collapse_errors, msg=errors)
        for family in EXPECTED_COLLAPSES:
            self.assertTrue(
                any(family in error for error in collapse_errors), msg=family
            )


if __name__ == "__main__":
    unittest.main()
