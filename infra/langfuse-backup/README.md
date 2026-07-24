# BEAAA-17701 bounded synthetic Langfuse backup

BLUF: this is an isolated, loopback-only Langfuse candidate for synthetic BEAAA-17690 journey evidence. It is a non-authoritative observability sidecar and cannot affect the BackBond Scanner score or evidence store.

## Controls

- All six images are immutable digest references.
- Only the web service binds the host, on `127.0.0.1:3300` by default.
- Dependencies use an internal Docker network and publish no host ports.
- Upstream telemetry is disabled.
- The acceptance client rejects production/customer/PII and insurance-decision vocabulary before ingest.
- Secrets are supplied through `/etc/backbond/langfuse-synthetic.env`, populated from Paperclip company-secret references; values never belong in git or issue comments.
- The run window ends `2026-07-29T23:59:59Z`. Teardown removes only this Compose project's containers, network, and volumes; secret versions must then be revoked in Paperclip.
- This sidecar is not the authoritative BackBond Scanner evidence store and provides no score, warranty, quote, bind, price, limit, decline, or insurance decision.

## Candidate workflow

Render without starting:

```sh
docker compose --env-file infra/langfuse-backup/env.example \
  -f infra/langfuse-backup/docker-compose.yml config --quiet
```

After the canonical QA child approves the exact commit and digest set, the operator may provision the secret-backed env file and start the isolated service:

```sh
docker compose --env-file /etc/backbond/langfuse-synthetic.env \
  -f infra/langfuse-backup/docker-compose.yml up -d
```

Run the clean-client acceptance using scoped project keys:

```sh
LANGFUSE_ENDPOINT=http://127.0.0.1:3300 \
LANGFUSE_PUBLIC_KEY='<resolved scoped public-key reference>' \
LANGFUSE_SECRET_KEY='<resolved scoped secret-key reference>' \
python3 infra/langfuse-backup/synthetic_acceptance.py
```

Teardown:

```sh
sh infra/langfuse-backup/teardown.sh
```

Contract anchor: `are-scanner-api-v1@v1.0`. Classification: rule-execution, supervision table row “CLI plumbing / customer onboarding harness / smoke tests that do not change rubric, judge, scoring math, tamper resistance, or output shape.”
