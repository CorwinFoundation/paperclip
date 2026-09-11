# BEAAA-30905 Datadog capability matrix

Read 2026-09-09 against the Datadog US1 API reference and unauthenticated live US1 responses. Authentication-denied responses prove routing and fail-closed behavior only; data-shape and coverage claims remain pending the authorized Datadog organization live run.

| Surface | Read endpoint / API | Required access | Documented response | Live result | Collection coverage |
|---|---|---|---|---|---|
| Logs | `POST https://api.datadoghq.com/api/v2/logs/events/search` (Logs API v2) | API key plus Application key with `logs_read_data` | JSON:API `data[]` log resources with `id`, `type`, `attributes`; `meta.page.after` cursor | US1 live response: HTTP `401`, `{"errors":["Unauthorized"]}` without credentials on 2026-09-09. Authorized data response pending. | Generic logs only. Tool misuse, scope escape, runaway loop, and hallucinated action remain `N/A - collection coverage incomplete`. |
| Agent Observability spans | `POST https://api.datadoghq.com/api/v2/llm-obs/v1/spans/events/search` (preview export API) | API key plus Application key; organization must have Agent Observability access | JSON:API span resources; cursor pagination; span/evaluation fields | US1 live response: HTTP `401`, `{"errors":["Unauthorized"]}` without credentials on 2026-09-09. Authorized data response pending. | Potential trajectory surface; no coverage claim until authorized live response proves configured fields. |
| Evaluations attached to spans | Returned on Agent Observability span resources and experiment event exports; intake-only endpoint `POST /api/intake/llm-obs/v2/eval-metric` is not a read API | Same read credentials for export; API key for intake | Evaluation metrics associated with exported spans/experiment events | No authorized read response yet. | Not collected by the Logs v2 driver. |

Production configuration contains only the exact HTTPS site origin and query. `DD_API_KEY` and `DD_APP_KEY` are read from the existing environment-backed secret mechanism. Redirects and any origin change fail closed. Normalization uses an allowlist and does not emit prompts, outputs, transcripts, tool arguments, secrets, PII, or customer identifiers.

This is internal controlled activation only, not customer or production ingestion.
