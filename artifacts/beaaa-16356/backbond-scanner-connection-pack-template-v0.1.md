---
artifact_type: other
owner: scanner_engineer_2
persona_audience: internal
canonical_terms_only: true
contracts_referenced: ["are-scanner-api-v1@v1.12.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-16789", "BEAAA-16356"]
version: "v0.1"
self_audit_passed: true
---

# BackBond Scanner Design-Partner Connection Pack Template

BLUF: copy this block into a design-partner connection pack so every consumer names the release pins emitted by the BackBond Scanner.

```yaml
scanner_base_url: https://scanner.backbond.ai
scanner_version: 0.5.0
corpus_version: 0.1.0
agent_endpoint: https://your-agent.example.com/backbond/scan
agent_auth_token_ref: partner-secret-manager:agent/backbond-token
```

The Scanner constants are authoritative. Do not independently advance either version in a connection pack.
