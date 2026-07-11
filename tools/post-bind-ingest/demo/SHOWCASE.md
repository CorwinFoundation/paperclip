# BackBond live AI risk demo

This presentation environment shows one complete proof run:

1. A separate synthetic AI-agent process emits production-shaped JSONL.
2. The collector-compatible boundary redacts fake PII and a fake token, batches
   events, and signs them with Ed25519.
3. The real receiver verifies signatures and replay state, normalizes events,
   and runs the passive P1-P6 detectors.
4. The continuity gate checks heartbeats and batch sequence.
5. Log Tracker persists bracketed claim candidates in SQLite.
6. A focused pytest gate exercises the same receiver and E2E paths.
7. The browser displays the GitHub Actions run and deployed Git revision.

No production tenant, customer data, scanner scoring path, or VAPI account is
contacted by the demo.

## Run locally

From the repository root on the configured Windows workstation:

```powershell
.\tools\post-bind-ingest\demo\start-showcase.ps1
```

Open `http://127.0.0.1:8765` and choose **Run the live proof**.

For a portable Python environment:

```bash
python -m pip install -r requirements-dev.txt
python -m pip install -e tools/post-bind-ingest -e tools/log-tracker
python tools/post-bind-ingest/demo/app.py --runs-dir .passive-demo-runs
```

## Verify the HTTP contract

```bash
python tools/post-bind-ingest/demo/smoke_test.py \
  --url http://127.0.0.1:8765 \
  --timeout 180
```

The smoke test starts a proof run and requires P1-P6 coverage, a passing pytest
gate, seven Log Tracker rows, healthy service state, and zero production contact.

## CI/CD

`.github/workflows/passive-monitor-demo.yml` performs four gates:

- focused Python tests;
- container build;
- full proof run inside the container;
- candidate-first VM deployment followed by a final health check.

The deploy script keeps the previous image name and restores it if the final
container fails health. The public endpoint is a Cloudflare quick tunnel to a
loopback-only container port on the Paperclip VM.

Automated VM deployment is disabled unless `PASSIVE_DEMO_CD_ENABLED=true`.
Enabling it requires a dedicated least-privilege deployment identity and these
GitHub repository secrets:

- `PASSIVE_DEMO_HOST`
- `PASSIVE_DEMO_USER`
- `PASSIVE_DEMO_SSH_KEY`

Do not upload the Paperclip VM root key. Optional repository variable
`PASSIVE_DEMO_URL` enables a post-deploy proof run through the public HTTPS
endpoint. Until a dedicated identity exists, use the candidate-first deploy
script manually with the locally held key.

## Operational boundaries

- Only one proof job runs at a time.
- Browser requests cannot choose commands, file paths, or test targets.
- Evidence is retained for at most 48 hours and capped to 20 in-memory jobs.
- The container is unprivileged, read-only, capability-free, and exposes only a
  loopback host port.
- All values resembling PII or secrets are synthetic fixtures.
