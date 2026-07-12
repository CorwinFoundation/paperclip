# BackBond scan-to-production live demo

This presentation environment executes one complete synthetic lifecycle:

1. The real scanner scores agent release `1.0.0` and produces a reproducible evidence hash.
2. The real underwriting admittance schedule records a constrained demonstration bind.
3. The scanner CI gate, collector, passive reader, and Log Tracker are installed in a synthetic production manifest.
4. Agent release `1.1.0` arrives, is rescanned, runs the focused pytest gate, and passes the real CI decision function.
5. An ephemeral Ed25519 key signs a Certificate of BackBond Scan.
6. The browser renders the complete signed certificate.
7. A separate synthetic agent process emits nine production-shaped JSONL logs; the real receiver, passive P1-P6 detectors, continuity gate, and Log Tracker process them.
8. The browser renders the real BackBond Report Card plus a combined lifecycle and monitoring report.

No production tenant, customer data, VAPI account, or real insurance system is contacted. The bind record is visibly marked `BOUND_DEMO`. The certificate is a certificate of scan only and is not proof of bound insurance coverage.

## Run locally

From the repository root on the configured Windows workstation:

```powershell
.\tools\post-bind-ingest\demo\start-showcase.ps1
```

Open `http://127.0.0.1:8765` and choose **Run full lifecycle**.

For a portable Python environment, install the focused runtime dependencies and packages:

```bash
python -m pip install pytest==9.0.3 pydantic==2.13.4 PyYAML==6.0.2
python -m pip install -e tools/post-bind-ingest -e tools/log-tracker
python tools/post-bind-ingest/demo/app.py --runs-dir .passive-demo-runs
```

Local development defaults to `DEMO_ENVIRONMENT=local`, so no access token is
required. Any public deployment must set `DEMO_ENVIRONMENT=public-demo` and a
`DEMO_ACCESS_TOKEN` of at least 32 characters. The browser then presents a
partner-token login and stores an HttpOnly, SameSite=Strict, Secure session
cookie. API clients use the same token as a Bearer credential.

## Verify the public contract

```bash
python tools/post-bind-ingest/demo/smoke_test.py \
  --url http://127.0.0.1:8765 \
  --timeout 180
```

For a public or otherwise authenticated deployment, pass the token explicitly
or through the `DEMO_ACCESS_TOKEN` environment variable:

```bash
python tools/post-bind-ingest/demo/smoke_test.py \
  --url https://demo.example.com \
  --token "$DEMO_ACCESS_TOKEN" \
  --timeout 180
```

The smoke test requires:

- initial score `80/B+` and updated score `81/B+`;
- a constrained demonstration bind and four installed components;
- a full CI gate pass at threshold 70;
- a valid signed certificate that can be viewed over HTTP;
- nine production-shaped agent logs;
- P1-P6 coverage, seven passive findings, and seven Log Tracker rows;
- a viewable self-contained BackBond Report Card;
- healthy continuity and zero production contact.

## CI/CD

`.github/workflows/passive-monitor-demo.yml` performs these gates:

- scanner lifecycle, passive monitor, and HTTP tests;
- presentation container build;
- full scan-to-production proof inside the container;
- immutable release bundle upload;
- candidate-first VM deployment followed by a public proof.

The deploy script keeps the previous image name and restores it if the final container fails health. The public endpoint is a Cloudflare quick tunnel to a loopback-only container port on the Paperclip VM.

Automated VM deployment is disabled unless `PASSIVE_DEMO_CD_ENABLED=true`. Enabling it requires a dedicated least-privilege deployment identity and these GitHub repository secrets:

- `PASSIVE_DEMO_HOST`
- `PASSIVE_DEMO_USER`
- `PASSIVE_DEMO_SSH_KEY`
- `PASSIVE_DEMO_ACCESS_TOKEN` (32 or more characters)

Do not upload the Paperclip VM root key. Optional repository variable `PASSIVE_DEMO_URL` enables a post-deploy proof through the public HTTPS endpoint. Until a dedicated identity exists, promote the immutable artifact manually with the locally held key.

## Operational boundaries

- Only one lifecycle job runs at a time.
- Browser requests cannot choose commands, fixtures, file paths, or test targets.
- Evidence is retained for at most 48 hours and capped to 20 in-memory jobs.
- The container is unprivileged, read-only, capability-free, and exposes only a loopback host port.
- All values resembling PII, secrets, vendors, policies, binds, and production activity are synthetic fixtures.
- The demo certificate uses an ephemeral per-run key and is never presented as a production trust anchor.