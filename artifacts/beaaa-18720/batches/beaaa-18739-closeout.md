# BEAAA-18739 explicit catalog closeout

**BLUF:** One validated 125-row catalog supplies exactly 25 explicit scenarios for each requested voice, multilingual, and accessibility family without changing runtime, scoring, integration, or published counts.

- Contract: `are-scanner-api-v1.md@v1.0`
- Classification: rule-execution, content-only
- Reporting status: `reported_not_scored`
- Accepted root: `5d07b6247b22d21c082de1f195f65f7e5d1b4891`
- Catalog SHA-256: `c8d7b56cf922e467381f81f8cb5908a9c631bfbf960731128ef0d1befabe3c8f`
- Rows: 125; five families x 25
- Substantive clusters: 24, 24, 24, 25, 25 by family
- Positive controls: 7, 9, 6, 7, 6 by family
- Identity-insensitive normalized skeletons: 125 unique
- Behavior-specific transitions: at least 3 per case
- Wrapper-stripped expected-observation skeletons: 125 unique
- Wrapper-stripped failure-condition skeletons: 125 unique
- Voice profile coverage: all 75 voice-family rows include `voice_realtime_agent`
- Token near-overlap threshold: `0.50`; shared schema and wrapper boilerplate removed before comparison; all 29 remaining qualifying pairs carry their concrete distinguishing transitions, oracle fields, and trust boundaries
- `scanner_version`: unchanged (`0.5.0`)
- `corpus_version`: unchanged
- `probe-set-version`: BEAAA-18739 catalog candidate `1.0.0`, unregistered
- `rubric_point_id`: unchanged / not mapped
- `N=5 calibration delta`: five repetitions at `0.0000`
- `cto_cosign_ref`: not required

Validation: `python3 tools/validate_beaaa18739_catalogs.py`

FAL: internal technical evidence; Focus remains recoverable through the BackBond Scanner contract and no external product or coverage claim is introduced.

No QA promotion gate is invoked because this change does not promote code, configuration, a build, or content into any shared or production environment.
