# AG-UI text endpoints

The `ag_ui` connection type accepts the agent server's HTTPS URL. It sends a fresh AG-UI `RunAgentInput` conversation for every connection check and case, using the same guarded customer HTTP transport. Authentication uses the existing None, Bearer or custom-header settings.

This initial implementation is API-only. Set `transport` to `ag_ui` in the existing intake connection-check and scan request bodies. Use the normal connection proof, consent, quota and scan submission path. The web form does not yet expose this connection type.

The scanner requests `text/event-stream`. It accepts assistant text emitted through `TEXT_MESSAGE_CHUNK` or `TEXT_MESSAGE_START` / `TEXT_MESSAGE_CONTENT` / `TEXT_MESSAGE_END`. A matching `RUN_STARTED` and `RUN_FINISHED` must bracket the reply. An error, interruption, malformed event, mismatched run identity, incomplete stream, unsupported MIME type or empty text is a connection/case transport failure and must not be graded. State snapshots, tool arguments and message snapshots are not text fallbacks. Native tool turns and human-input resumptions are outside this text-only contract.

The existing response-byte limit, timeout, scoped egress, compression refusal and redirect rejection still apply. The adapter does not retry target calls or share conversation state across cases. No scoring, corpus, admission or delivery policy changes are included.

## CrewAI Dojo discovery

On October 24, 2026, the public backend OpenAPI at `https://ag-ui-dojo-crewai.onrender.com/openapi.json` exposed `/crew_chat` and `/agentic_chat`. A benign AG-UI request to `https://ag-ui-dojo-crewai.onrender.com/crew_chat` returned assistant text `OK`, a matching completed run and SSE MIME type. That direct protocol test establishes endpoint availability, not a scanner result or deployed adapter support.

The upstream [deployment configuration](https://github.com/ag-ui-protocol/ag-ui/blob/main/render.yaml) names the CrewAI backend, and its [Dojo server](https://github.com/ag-ui-protocol/ag-ui/blob/main/integrations/crew-ai/python/examples/agents/dojo.py) registers `/crew_chat` through `add_crewai_crew_fastapi_endpoint`. The separate `/agentic_chat` route serves a CrewAI Flow. Source `main` and the live deployment may differ; verify the live route before use.

The original scan of the Dojo web page remains invalid and its report must stay blocked. A replacement 100-case scan is pending compatible deployed Scanner and intake source, a fresh public connection proof, and normal admission. This source change does not authorize its own deployment or claim a completed replacement scan.
