# Variable tests for Starter and Full Scan

Implementation candidate for BEAAA-38985, 7 October 2026. This module supplies
durable form selection; product activation and statistical comparability remain
the Starter/Full Scan integration and calibration work in BEAAA-38986/38987/38988.

Every ordinary run must assemble fresh actual scenarios with no fixed core.
The stable blueprint is the coverage budget: family, rubric ID/version,
difficulty, severity and count. Changing a rubric or a scenario bank requires a
version change. An equivalent-form claim requires reference-agent measurements;
identical metadata alone does not establish equivalence.

`scanners.variable_test_forms` provides `Stratum`, `Scenario` and `FormStore`.
Starter callers supply ten strata with ten slots each (or more detailed strata
whose quotas still total 100). Full Scan callers supply their independently
versioned coverage budget. Never use a versioned pack name as `product`; the
stable identities `starter` and `full` retain history across bank revisions.
Every `Scenario` must carry a nonempty `test_spec_id` from the existing registry.
Multiple authored scenario forms may map to the same specification; the assembler
preserves that linkage but does not certify registry eligibility itself.

```python
store = FormStore(restricted_evidence_directory / "variable-forms.sqlite")
form = store.create(
    scope=(trusted_tenant_id, trusted_agent_id),
    product="starter",  # "full" for the larger scan
    bank_version="starter-scenarios/v1",
    strata=strata,
    scenarios=scenarios,
    history_window=1,
    form_id=server_job_id,
)
# Dispatch only after this durable reservation succeeds.
for case in form["cases"]:
    run_and_grade_exact_payload(case["payload"])
```

`payload` contains the target inputs and their bound grading data together:
`prompt` or `system`/`turns`, plus canary, reference answer, paired turns,
setup/tools and any grading specification needed by that runner. IDs, expected
answers, selection metadata and grading rubrics must not be appended to target
messages. A fairness pair remains one selected scenario.

Each create uses server-generated 256-bit selection entropy. There is no
customer seed argument. Use a trusted server job ID to make retries idempotent;
`store.get(form_id, scope=..., product=...)` replays exact saved payloads after a
restart or a bank revision. Keep the stored form for resume instead of selecting
again. Each replicate of the same run uses that same form. Public reports use
only `FormStore.report_summary(form)` plus existing aggregate result fields.

The SQLite file contains private prompts, answers and seed. Store it under the
existing restricted evidence path and access controls; this module does not
configure operating-system ACLs, encrypt evidence, or sign manifests. Content
hashes detect accidental corruption; existing evidence signing remains the
runner's responsibility. Keep one shared durable store for all processes;
separate replicas require shared reservation infrastructure, not independent
copies of this database.

Reservations are atomic. Recent scenarios are excluded by both stable scenario
ID and target-input hash, including across a bank version change. The default
window excludes the immediately previous run for that tenant/agent/product.
Choose a larger window with enough supply: a stratum with `k` slots needs at
least `(history_window + 1) * k` scenarios to sustain fresh forms. Exhaustion
raises `InsufficientScenarios` before any dispatch. Report incomplete/unavailable
coverage; do not silently revert to the old fixed list or reduce quotas.
The minimum supply is checked even for the first run, so a thin bank cannot
dispatch a fixed core once and defer the failure until the next run.

The bank must contain substantively different setups or attack mechanisms;
changing a nonce, translating, rewording or relabeling a row does not establish a
new unique test. The exact-input dedupe here supplements the existing semantic
content linter; it does not replace it. Each candidate must be reviewed against
its declared rubric/difficulty/severity and preserve coherent canaries and
expected answers. `require_calibrated=True` excludes uncalibrated candidates;
the module preserves the status when calibration is not required. It does not
promote rows into scoring or underwriting eligibility.

Keep Starter and Full Scan score semantics separate. No score weights, bands,
insurance terms or policy criteria are changed by this module. Generated forms
contribute zero additional unique tests to coverage claims. The test suite's
100-slot and 2,000-slot fixtures verify mechanics, not shipped test coverage or
live score comparability.
