# BEAAA-30574 offline fixture evidence

Candidate form: committed branch `agent/beaaa-30582-param-tamper-offline` (no deployment). Filed under BEAAA-30582.

- Family: `tool_misuse_param_tamper`
- Rubric point: `ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER`
- Scanner contract: `contracts/are-scanner-api-v1.md` v1.0
- Scored probe set: `tool_misuse_param_tamper@0.1.0`, unchanged. The offline cases are not added to `rubric/probe_mappings/tool_misuse_param_tamper.yaml`, which is content-pinned by the tranche-1 closed world in `tools/tranche1_observational.py`.
- Corpus: `r2-rob-2026.06.14`
- Local catalogue: 12 preserved scored probe IDs + 20 distinct offline fixture IDs = 32; the two id sets are disjoint
- Execution: 10 permit fixtures and 10 deny fixtures; 0 incomplete; 0 not executable
- Sanitized permit return: `{case_id: <fixture id>, status: simulated}` plus one local mocked effect
- Sanitized deny return: no receipt and no mocked effect
- Harness validation: malformed request is `harness_blocked`; empty, malformed, mismatched, or extra-field receipts fail validation
- Limitations: synthetic illustrative values only; no network, credentials, live targets, production dispatch, scoring activation, underwriting, pricing, or protection behavior

Verification command:

`python3 -m pytest -q tests/test_beaaa30574_param_tamper_offline.py tests/test_probe_registry.py`

The deliberately incorrect behavior control permits `PT-EXT-004`; the independent expected-state oracle rejects it, while the actual adapter denies it.
