# Unique test spec v1

Version: `unique-test-spec/1.1.0` (BEAAA-38921)
Linter: `python -m tools.unique_test_lint` (module `tools/unique_test_lint.py`)
Audit output: `artifacts/beaaa-38921/`

## Bottom line

A record in the live full-scan registry counts as one **eligible unique test**
only if all of these hold: the runner sends it to the target today, its prompt is
something a real user would send, it has an exact grading rule, it has complete
metadata (area, family, severity, source, licence, capability requirements), and
it is not a repeat of another eligible test. Anything else is `rewrite` or
`drop`. The eligible-unique count is the number of `keep` records.

This spec governs counting and auditing. It does not change what the scanner
executes, how a case is graded, or any scored or published count.

## What is audited

The population is the registry the full-scan worker executes:
`api.full_scan_worker.load_default_full_scan_registry_rows()`, which is
`tools.active_test_registry.build_registry()` filtered by
`_is_countable_active_executable`. Snapshots under `artifacts/`, `IFA/` and
`tools/probe-registry/*.json` are not sources.

A record is one registry row, identified as `<family>/<case_id>`.

## Eligibility rules

Each rule has a reason code. A record that breaks no rule is `keep`.

1. **Executable today.** The production dispatcher
   (`RegisteredCaseExecutor._execute_case`) must reach the target with the
   record. The linter checks this with a dry run: a stub target records every
   prompt it receives, and the capability manifest declares every surface so no
   gate short-circuits.
   - `not_executable`: the dispatcher raised before sending anything.
   - `target_not_exercised`: the dispatcher returned a verdict without calling
     the target (for example a trace-observation adapter that exercises the
     scanner's own dispatcher, or saved evidence replayed in place of a call).
2. **Concrete, user-realistic prompt.** The authored prompt is a message a user
   of the agent could plausibly send.
   - `prompt_missing`: no prompt text.
   - `prompt_placeholder`: unfilled template tokens (`{name}`, `[INSERT ...]`,
     `<placeholder>`, `TODO`, `TBD`, `lorem ipsum`, `@placeholder`). `${VAR}`
     shell syntax is content, not a placeholder.
   - `prompt_template_variation`: a "Variation NN" counter stands in for content.
   - `prompt_slug_only`: the scenario is named by a backticked slug
     (`` `expired-agent-certificate` ``) instead of being written out.
   - `prompt_third_person_scenario`: the prompt narrates a user ("A user asks
     for ...") instead of being the user's message. A task about a third party
     ("A customer whose account ... Write a reply") is not flagged.
   - `prompt_structured_spec`: the prompt is a serialized scenario object, not
     prose.
3. **No evaluation scaffolding.** Nothing in the prompt tells the target it is
   being tested, what the right answer is, or how it will be graded.
   - `scaffolding_eval_framing`: "this is a test", "as an evaluator",
     "evaluate the synthetic case", "synthetic input", "Case 3:", "control
     group", "target risk item".
   - `scaffolding_expected_answer`: expected-answer or rubric text in the
     prompt ("expected_label", "requires refusal", "decline and redirect",
     "pass if", "scoring rubric").
   - `scaffolding_internal_id`: ticket ids, `case_id`, `seed 0`.
   - `runner_render_scaffolding`: the scaffolding is added by the runner when it
     renders the record (role labels such as `USER:`, "Continue the same
     evaluation conversation", "Behavioral evaluation case ..."), not authored
     in the record. Fixed once in the runner, not per record.

   Words are not scaffolding. A realistic prompt that contains "password",
   "ignore", "secret", "system prompt", "rubric" or "test" is not rejected for
   the word. "I forgot my password, can you reset it?" is eligible. Only the
   phrase patterns above are rejected.
4. **Exact grading rule.** `grading_rule_missing` unless the record has a
   deterministic oracle with a body (field assertion, verifier, substring or
   canary oracle, database end state) or a pinned judge: a model id plus
   temperature, or a resolvable `path#key` reference to a judge block.
5. **Metadata.** `area_missing` (pillar), `family_missing`, `severity_missing`
   (one of critical, high, medium, low, info, sev1 to sev4), `source_missing`
   (who authored it or which upstream dataset; a file path alone does not
   count), `licence_missing` (an SPDX id or `LicenseRef-*`; the linter never
   infers one).
6. **Capability requirements declared.** N/A is never a pass, so the surfaces a
   test needs must be declared for N/A to be scoped.
   - `capability_undeclared`: no applicability declaration on the record, its
     source pack, or the family gate. A family that needs no special surface
     declares that explicitly.
   - `capability_declared_not_enforced`: the record names a required capability
     that the dispatcher's applicability gate does not check before the target
     call. A capability named after the family itself is treated as "no
     requirement".
7. **Unique.** Translations, paraphrase repeats and near-duplicates are variants
   of one test and count once.
   - `duplicate_exact`: same normalized prompt text (NFKC, casefolded,
     punctuation and whitespace collapsed, "Variation NN" removed) as another
     record. The same prompt under a different oracle in another family is still
     one test; its extra oracle should be merged into the kept record.
   - `near_duplicate`: word 3-shingle Jaccard similarity of at least 0.8.
     Slug-only stubs are excluded from near-duplicate matching because they
     differ only in the slug, and the slug is the scenario.
   - `generated_variant` (1.1.0, BEAAA-38985 fresh forms): a record carrying
     `parent_unique_test_id` was generated from that unique test by the form
     assembler. It is never a new unique test, whatever its wording, and counts
     as its parent even when the parent is not in the registry.
   - `translation_variant`: shares a base id with another record once a language
     marker (`.fr`, `_es`, `lang=de`) or an explicit `variant_base` is removed.

   In each duplicate group the canonical record is the one with the fewest other
   defects, then scored before unscored, then lowest id. The others get
   `dup_of` set to it.

## Dispositions

- `drop`: any of `not_executable`, `prompt_missing`, `duplicate_exact`,
  `near_duplicate`, `translation_variant`. Nothing to salvage, or it already
  counts through its canonical record.
- `rewrite`: any other reason. The content or binding is salvageable.
- `keep`: no reasons. Eligible.

A `rewrite` record is **metadata-only** when all of its reasons are among
`licence_missing`, `severity_missing`, `source_missing` and
`capability_undeclared`. Those can be fixed by recording a field, without
touching prompt, grading or runner. The summary reports
`eligible_unique_if_metadata_repaired` to rank repair work. It is not an
eligible count.

## Outputs

- `unique-test-audit.csv` has one row per record with the columns
  `id,area,family,source,licence,severity,capabilities,grading_rule,disposition,reasons,dup_of,unique_test_id,stratum_key`.
  `unique_test_id` is the canonical record a row counts as (itself when kept or
  unique). `stratum_key` is the record's `stratum` when declared, otherwise
  `<area>/<family>`; the form assembler budgets strata on it.
- `unique-test-audit-summary.json` holds overall, per-area and per-family
  counts (total, eligible, keep, rewrite, drop, metadata_only), the same counts
  per `stratum_key` under `by_stratum`, reason counts,
  the eligible-unique count, the spec version and the registry commit SHA.

## Changing this spec

Any change to a rule, a pattern list or the near-duplicate threshold is a
version bump: patch for a pattern fix that moves no disposition in the committed
audit, minor for anything that moves one, major for a change to the definition
of eligibility. Regenerate the audit at the new version.
