# tool_misuse_param_tamper offline fixtures

`offline_ext_v1.json` holds 20 Day One offline cases (BEAAA-30582, originally
scoped as BEAAA-30574) for the parameter-tampering family. They extend the
family's local coverage from 12 to 32 cases.

Scope and isolation:

- Synthetic inputs and mocked effects only. No real endpoints, no credentials,
  no external tool execution, no live-target probes.
- Executed by `harness.bench.tool_misuse_param_tamper.execute_offline_fixture`,
  which resolves every case in-process and returns a sanitized simulated
  receipt plus one mocked effect on `permit`, and neither on `deny`.
- Malformed or empty requests return `harness_blocked` rather than a decision.

Deliberately out of scope: these cases are not added to
`rubric/probe_mappings/tool_misuse_param_tamper.yaml`. That mapping is pinned
by the tranche-1 observational closed world in `tools/tranche1_observational.py`
(`ALLOWLIST` 12 cases per family, plus a `MAPPING_SHA256` content pin). Adding
probes there would change a scoring asset, which this offline test work does
not do. Same shape as the sibling family in commit d9fffd5f.

Each case carries a distinct `(control, approved, final)` state; 10 expect
`permit` and 10 expect `deny` across 10 binding controls.
