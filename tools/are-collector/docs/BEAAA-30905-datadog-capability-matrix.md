# Datadog capability matrix

Original read 2026-09-09 (BEAAA-30905) against the Datadog US1 API reference and unauthenticated live US1 responses. Reconciled 2026-09-16 (BEAAA-32176/BEAAA-32178) after credentialed live acceptance of logs, Agent Observability spans and attached evaluations.

Each surface is recorded at one of three distinct levels. They are not interchangeable.

- **Code**: a read path exists in this repository and is covered by tests.
- **Controlled live acceptance**: the code was run against a real Datadog organization on non-customer telemetry and delivered a signed batch to the internal receiver, with a recorded receipt.
- **External deployment**: the path runs against a customer or third-party organization. No surface below has reached this level.

| Surface | Read endpoint / API | Required access | Level reached | Live evidence | Collection coverage |
|---|---|---|---|---|---|
| Logs | `POST https://api.datadoghq.com/api/v2/logs/events/search` (Logs API v2) | API key plus Application key with `logs_read_data` | Controlled live acceptance (BEAAA-30905, BEAAA-31835) | Credentialed end-to-end read to the internal receiver; remains the collector default. | Generic logs only. Tool misuse, scope escape, runaway loop and hallucinated action are `N/A - collection coverage incomplete` on this surface. |
| Agent Observability spans | `POST https://api.datadoghq.com/api/v2/llm-obs/v1/spans/events/search` (preview export API) | API key plus Application key; the organization must have Agent Observability enabled | Controlled live acceptance (BEAAA-32178) | Receipt/batch `beaaa-32178-live-1`, hash `sha256:99996b9e669c458558818e63967e5fe5bfd6f50291a7a9254e86192baadb4dab`, six signed events including three linked spans. Implementation `73cd45acc949e1777fec1e9a8a1d7299b85995ea`. | Run/trace/span/parent links, agent and deployment identity, action class, timestamps, outcome and source provenance. Opt-in `agent_spans` mode; logs stay the default. Absent fields are labelled, never inferred. |
| Evaluations attached to spans | Returned on Agent Observability span resources with `include_attachments=true`. The intake endpoint `POST /api/intake/llm-obs/v2/eval-metric` is write-only and is not a read API. | Same read credentials as the span export | Controlled live acceptance (BEAAA-32178) | Two evaluations in the batch above. | Evaluator identity/version, scope and evaluation timestamp. Automated judgments carry `automated_unconfirmed` and are distinguished from confirmed outcomes. Evaluations can lag their spans; early absence is reported as incomplete, not as a pass. |

## Evidence contract shared with other sources

Any source driver that claims trajectory coverage emits the same normalized shape, so Langfuse and OTEL can be aligned against one contract rather than each inventing its own:

1. Run correlation: run/trace id, span id, parent span id, agent identity, deployment identity.
2. Action record: action class, start and end timestamps, outcome, source provenance reference.
3. Model and authority evidence where the source supports it; an explicit absent-field label where it does not.
4. Evaluation record: evaluator identity and version, scope, evaluation timestamp, and the automated-versus-confirmed distinction.
5. Completeness reported separately from connectivity: freshness, empty versus stalled source, sampling or unknown sampling, missing run segments, dropped records.
6. Minimization: no prompts, outputs, transcripts, tool arguments, secrets, PII or customer identifiers.

Remaining scope, explicitly not done: Langfuse has a source driver but has not been read against this contract; OTEL has adapter code and no live source-driver acquisition wiring in this checkout. Neither is equivalent to the Datadog path today.

## Boundaries

Production configuration contains only the exact HTTPS site origin and query. `DD_API_KEY` and `DD_APP_KEY` are read from the existing environment-backed secret mechanism. Redirects and any origin change fail closed. Normalization uses an allowlist.

Listener ingestion and third-party evaluations do not change the BackBond Score. An observed failure is evidence for a regression case or rescan request, not a covered loss. This is internal controlled activation only, not customer or production ingestion.
