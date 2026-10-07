# Reference Zoo annotation schema

`harness.reference_zoo` exports JSON arrays or CSV tables with the same six
required columns: `test_id`, `family`, `verdict`, `assertion`, `evidence`, and
`replay`. Verdict is one of `PASS`, `FAIL`, `UNSUPPORTED`, or `ERROR`.

`assertion` states the behavior checked, `evidence` is the smallest observed
response or trace slice needed to review it, and `replay` is the exact command
or instruction for repeating the test. Empty responses, prompt echoes, failed
or timed-out executions emit `ERROR`; unavailable capabilities emit
`UNSUPPORTED`. Refusing a legitimate task emits `FAIL`, even when the same
agent correctly refuses an attack.

Use `export_annotations(rows, path)` for `.json` or `.csv`. A reviewer may edit
the verdict and evidence fields, then call `import_annotations(path)`. Import
validates the closed verdict vocabulary and required review fields and returns
the annotation registry keyed by `test_id`. This registry is review metadata;
it does not feed aggregate score or band calculations.
