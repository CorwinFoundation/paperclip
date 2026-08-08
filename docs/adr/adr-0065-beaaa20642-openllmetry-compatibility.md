# ADR-0065: OpenLLMetry compatibility pilot

BLUF: BEAAA-20642 adds a default-off, vendor-local compatibility seam for CrewAI and LangChain OpenLLMetry instrumentors. It exports only to a loopback OTLP IP literal, disables content and upstream telemetry, maps through the existing direct-driver allowlist, and removes framework monkey patches symmetrically when disabled.

- Status: immutable candidate for CTO handoff; do not merge or promote
- Classification: rule-execution, existing runtime-evidence driver implementation
- Contract: `contracts/are-scanner-api-v1.md@v1.3.0`, unchanged
- Scanner: `0.5.0`, unchanged
- Corpus: `frozen-0.5.0`, unchanged
- Evidence tier: `vendor-local-redacted`

## Decision

`BACKBOND_OPENLLMETRY_COMPAT` defaults off. Enabling the pilot loads only `CrewAIInstrumentor` or `LangchainInstrumentor`, configures OTLP/HTTP to a validated loopback IP literal, disables Traceloop telemetry and content capture, and disables CrewAI upstream telemetry. Before changing process environment, the context snapshots every key it owns. Context exit calls `uninstrument()`, restores every pre-existing value exactly, and removes every key that the pilot added, so rollback retains no monkey patches, environment mutation, or migration state.

The receiver normalizer now preserves the direct driver's signed source attribution under `provenance.source`. It accepts only the exact `vendor_otel` attribution emitted by the existing driver; a different or expanded attribution fails closed.

## Pinned upstream proof

OpenLLMetry commit `93429cfb9767e6b51db6cb35f6b70f758117669a` contains:

- `packages/opentelemetry-instrumentation-crewai`, entry point `CrewAIInstrumentor`;
- `packages/opentelemetry-instrumentation-langchain`, entry point `LangchainInstrumentor`;
- `traceloop-sdk` initialization paths `init_crewai_instrumentor` and `init_langchain_instrumentor`.

The selected instrumentor packages are both pinned to `0.62.1`. CrewAI `1.15.10` requires the OpenTelemetry exporter `1.42` line, so the isolated lock pins `opentelemetry-exporter-otlp-proto-http==1.42.0`. This does not change the Scanner or direct-driver dependency set.

## Security and rollback

- Hosted Traceloop URL is empty; upstream telemetry and content capture are false.
- Export endpoint accepts only loopback IP literals. DNS names, private non-loopback addresses, and public endpoints reject.
- The direct-driver allowlist remains unchanged.
- Unknown attributes, content, credentials, malformed batches, replay, reorder, attribution tampering, and signed-event tampering reject before accepted state advances.
- Feature flag off selects `OTEL_TRACES_EXPORTER=none` and imports no instrumentor.
- Rollback: context exit or explicit `disable()` uninstalls framework patches and restores the exact pre-enable process environment; flag off and uninstall the isolated packages. No data migration, retained patch, or pilot-added environment key exists after exit.

## Reproducibility and evidence

- Lock SHA-256: `2e2d037941da09d091b4bd5ac02c502ebb837982dd78139246d0e4c60ef96ec1`
- CycloneDX SBOM SHA-256: `49c0986cab6840152e683a94bfc124a802242eb09a29a591996305b29c71c387`
- Effective enabled-config SHA-256: `d977e41d8e80e06babb59f751a7d5f3ba791c2b31653038c761f2547eabacb02`
- SBOM components: `155`
- Hash-locked clean-venv install: passed
- Exact imports: `CrewAIInstrumentor LangchainInstrumentor`
- Exact targeted verification: `PYTHONPATH=tools/post-bind-ingest/src pytest -q tools/post-bind-ingest/tests/test_openllmetry_compat_beaaa20642.py tools/post-bind-ingest/tests/test_otel_driver_beaaa20418.py tools/post-bind-ingest/tests/test_otel_log_tracker_integration_beaaa20420.py tools/post-bind-ingest/tests/test_receiver.py tools/post-bind-ingest/tests/test_normalizer.py` -> `54 passed`

N=5 canonical bytes and digest were identical for each fixture. CrewAI digest was `31b69e39ac18660454ef57c6fa1080d06da5909e9ca1ee3308342ea01063d1d4`; LangChain digest was `90cb84051a25dd26db467540427d8fe370597c7dc342303feebd8337f35f4712`.

The 1,000-sample local compatibility-boundary benchmark recorded CrewAI p50/p95 added latency `0.025543/0.050408 ms` and `534` emitted bytes/event. LangChain recorded `0.023335/0.054790 ms` and `540` emitted bytes/event. These report-only measurements do not enter scoring.

## Operational metadata

- scanner_version: `0.5.0`
- corpus_version: `frozen-0.5.0`
- probe-set-version: `not-applicable-runtime-evidence-compatibility`
- rubric_point_id: `OPS.DATA_HANDLING.LOCAL_INGEST`
- N=5 calibration delta: `0`; no scoring or judge path imported, canonical evidence identical across five runs per fixture
- cto_cosign_ref: not required; CTO scope decision [BEAAA-20643](/BEAAA/issues/BEAAA-20643)
- FAL: internal engineering decision linked to the existing Passive Monitor and direct-driver lane; no external claim introduced
