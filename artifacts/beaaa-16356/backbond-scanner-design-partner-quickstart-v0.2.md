---
artifact_type: one_pager
owner: cmo
persona_audience: prospect_ai_vendor
phase: 1
canonical_terms_only: true
brand_backbone_version: "v1.6"
brand_backbone_sha256: "a2ffc4f919b0f71a17b9f4506ac4edf11cd37956ff494f00d7a45f0b166ea090"
brand_backbone_verified: true
focus_claim: "This quickstart helps an AI vendor run the live BackBond Scanner today so BackBond becomes the third-party trust layer they can embed in enterprise deal reviews."
contracts_referenced: ["are-scanner-api-v1@v1.12.0", "standard-agent-interface-v1"]
ticket_refs: ["BEAAA-16789", "BEAAA-16356", "BEAAA-16355", "BEAAA-15593"]
proof_points_cited: ["BackBond Scanner route contract: POST /scanner/v1/scans and GET /scan/{scan_id}", "Scanner release pins: scanners.SCANNER_VERSION 0.5.0 and scanners.CORPUS_VERSION 0.1.0", "BackBond Scanner live edge: https://scanner.backbond.ai returned 401 authorization_bearer_required on unauthenticated POST /scanner/v1/scans on 2026-07-22", "BackBond Scanner bands: Preferred A 85+, Standard B 70-84, Constrained C 55-69, Decline D <55", "Brand Backbone v1.6 SHA verifier exit 0 on 2026-07-22"]
version: "v0.3"
prior_version: "backbond-scanner-design-partner-quickstart-v0.2"
self_audit_passed: true
escalations_noted: ["No carrier names; no competitor names; no new public proof point beyond local live-edge reachability check; no insurance-policy implication; scanner bearer token must be delivered out-of-band by BackBond, never pasted into Paperclip."]
---

# BackBond Scanner Design-Partner Quickstart

**Status:** partner-ready source content. Live scanner edge confirmed 2026-07-22.

**Use this when:** a design partner wants to point their AI agent at the live BackBond Scanner today and receive a BackBond Score.

**Scanner access fields:**

| Field | Value |
|---|---|
| Scanner base URL | `https://scanner.backbond.ai` |
| Scanner API key | BackBond-issued tenant-scoped bearer token, delivered out-of-band |
| Agent endpoint | Partner-owned HTTPS staging endpoint |
| Agent auth | Partner-issued bearer token if the agent endpoint requires auth |

**Release pins:** `scanner_version: 0.5.0`; `corpus_version: 0.2.0`.

## 1. Expose a staging agent endpoint

Give BackBond a reachable HTTPS endpoint for the agent you want scanned. Use staging unless BackBond has separately approved a production scan.

The endpoint must accept a scanner request and return the agent's answer as JSON:

```http
POST https://your-agent.example.com/backbond/scan
Authorization: Bearer <partner-agent-token>
Content-Type: application/json
```

```json
{
  "input": "BackBond Scanner probe text appears here",
  "metadata": {
    "probe_id": "provided-by-backbond"
  }
}
```

Expected response:

```json
{
  "output": "Your agent response text"
}
```

Also expose the self-identification endpoint:

```http
GET https://your-agent.example.com/.well-known/agent
```

```json
{
  "agent_id": "claims-assistant-v3",
  "agent_version": "2026-07-21",
  "environment": "staging",
  "owner": "partner-name"
}
```

## 2. Run the scan

Set the scanner credentials:

```bash
export BACKBOND_BASE_URL="https://scanner.backbond.ai"
export BACKBOND_API_KEY="<backbond-issued-api-key>"
export AGENT_ENDPOINT="https://your-agent.example.com/backbond/scan"
export AGENT_AUTH_TOKEN="<partner-agent-token-if-required>"
```

BackBond gives you the scanner API key through your agreed secure channel. Do not paste the key into email, Slack, Paperclip, CI logs, or source control.

Submit the agent:

```bash
SCAN_ID="$(
  curl -sS -X POST "$BACKBOND_BASE_URL/scanner/v1/scans" \
    -H "Authorization: Bearer $BACKBOND_API_KEY" \
    -H "Content-Type: application/json" \
    --data-binary @- <<JSON | python -c 'import json,sys; print(json.load(sys.stdin)["scan_id"])'
{
  "agent_id": "claims-assistant-v3",
  "agent_endpoint": "$AGENT_ENDPOINT",
  "scan_tier": "full",
  "scanner_invocation_context": {
    "purpose": "pre_bind"
  },
  "agent_auth_token_ref": "partner-secret-manager:claims-assistant-v3/backbond-token"
}
JSON
)"

echo "$SCAN_ID"
```

Do not add `X-Tenant-Id` unless BackBond gives you one. The live edge resolves your tenant from the scanner bearer token.

Poll for the result:

```bash
curl -sS "$BACKBOND_BASE_URL/scan/$SCAN_ID" \
  -H "Authorization: Bearer $BACKBOND_API_KEY"
```

The response includes the BackBond Score (`are_total`) and band:

```json
{
  "scan_id": "scan_abc123",
  "are_total": 86,
  "band": "A",
  "run_timestamp": "2026-07-21T00:00:00Z"
}
```

Band map: Preferred A 85+, Standard B 70-84, Constrained C 55-69, Decline D below 55.

## 3. Minimal LangChain adapter

Use this only as the scanner-facing adapter. Keep your production serving path unchanged.

```python
from fastapi import Depends, FastAPI, Header, HTTPException
from langchain_core.messages import HumanMessage

app = FastAPI()

BACKBOND_AGENT_TOKEN = "replace-with-secret-manager-value"


def require_backbond_token(authorization: str = Header(default="")) -> None:
    if authorization != f"Bearer {BACKBOND_AGENT_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/.well-known/agent")
def agent_metadata():
    return {
        "agent_id": "claims-assistant-v3",
        "agent_version": "2026-07-21",
        "environment": "staging",
        "owner": "partner-name",
    }


@app.post("/backbond/scan", dependencies=[Depends(require_backbond_token)])
def run_backbond_probe(payload: dict):
    prompt = payload["input"]
    result = langchain_agent.invoke([HumanMessage(content=prompt)])
    return {"output": result.content if hasattr(result, "content") else str(result)}
```

## 4. What BackBond needs back

Send these four fields to your BackBond contact:

```text
Agent name:
Agent version:
Agent endpoint:
Auth handoff method:
```

BackBond returns the BackBond Score and the scan timestamp. Qualifying agents can then move to the design-partner trust page and warranty workflow. The warranty is not an insurance policy; BackBond pays the covered loss up to the limit under the BackBond warranty.

## Live Edge Check

BackBond verified the public edge before sending this quickstart:

```text
POST https://scanner.backbond.ai/scanner/v1/scans without a bearer token -> 401 authorization_bearer_required
GET  https://scanner.backbond.ai/scan/nonexistent with an invalid bearer token -> 401 authorization_invalid
GET  https://scanner.backbond.ai/openapi.json -> 404 by design; the edge allowlist exposes scan submit and scan read only
```

## Pre-Submission Self-Audit

1. One thing this asset does: gives an AI vendor the exact minimum path to run the BackBond Scanner today.
2. Persona: Partner audience, specifically an agent builder or agent-platform operator; next action is exposing a staging endpoint and submitting the scan.
3. Commercial insight: enterprise buyers are starting to ask vendors for a third-party agent risk score. A design partner that can produce a current BackBond Score has a procurement answer before the objection hardens.
4. Narrative beat: Bridge plus Proof.
5. Strongest sentence: "BackBond returns the BackBond Score and the scan timestamp."
6. Weakest sentence retained: "Use staging unless BackBond has separately approved a production scan." It is operationally necessary because current CI/plugin canon refuses production by default.
7. Competitor test: the copy rests on BackBond Scanner routes, BackBond Score bands, and the warranty workflow, so a generic eval vendor could not publish it unchanged.
8. FAL audit: pass.
9. Locked Focus visible: yes, the asset shows BackBond as the trust layer a vendor embeds to close enterprise deals.
10. CEO/Founder critique pre-empted: live URL is confirmed, secret handoff is described without exposing token material, warranty is separated from insurance, and no carrier or competitor is named.

## Designer Routing Note

Per external-asset routing, this is CMO source content, not a finished visual. If this must leave as a branded PDF, Designer should render it as a one-page quickstart using the locked BackBond design system, with no new copy beyond spacing or table-label edits.
