# CD pipeline — containerized, Founder-gated deploy (Option A)

**BEAAA-13362 (Phase 2)** · rule-execution under BEAAA-13358 /
`founder-directive-deploy-pipeline-2026-07-10` · mechanism per ADR-0057
(BEAAA-13360). No scoring / rubric / corpus / band / output-shape / tamper
change — deploy plumbing only, **no Underwriter gate**.

## What ships every merge

```
push to master
  └─ build-push  (GitHub-hosted CI)   build image from Phase-1 Dockerfile,
     │                                 import-closure guard runs at build,
     │                                 push PRIVATE → ghcr.io/corwinfoundation/are-scanner:<sha>
     └─ deploy    (self-hosted runner, environment: production)
        ├─ ⛔ FOUNDER GATE: the `production` Environment required-reviewer
        │      Approve click. Nothing reaches the box without it.
        ├─ box_deploy pull <ref>       docker pull (no swap)
        ├─ box_deploy activate <ref>   swap running container, keep prev image
        ├─ acceptance_checks.sh        health / auth×2 / staging self-scan / envelope
        ├─ on failure → box_deploy rollback <prev>  (retag+restart, seconds)
        └─ post_deploy_record.py       SHA·digest·checks·approver·ts → tracker
```

The deploy job only ever references the **digest** `build-push` emitted, so
*what the Founder approved is exactly what runs*.

## One-time provisioning (Founder / operator — cannot be done from an agent seat)

These are GitHub-org and on-box settings; no agent has repo-admin or box SSH.

1. **`production` GitHub Environment + Founder gate.**
   Repo → Settings → Environments → **New** `production` →
   **Required reviewers** = the Founder. This single setting *is* the gate the
   `deploy` job blocks on.

2. **Self-hosted runner on srv1749248** (ADR-0057, pull-based, zero inbound SSH).
   - Register with an **ephemeral** token; labels `self-hosted, production`.
   - Run as unprivileged user **`are-deploy`**.
   - Scope the runner to this repo only (private repo → no fork-PR runner risk).

3. **Box deploy verb + sudoers whitelist.**
   Install `scripts/deploy/box_deploy.sh` → `/usr/local/sbin/box_deploy`
   (`root:root`, `0755`) and whitelist exactly it:
   ```
   are-deploy ALL=(root) NOPASSWD: /usr/local/sbin/box_deploy
   ```
   `are-deploy` gets **no other** root capability. Optionally drop box-local
   overrides in `/etc/are-scanner/deploy.env` (`ARE_CONTAINER_NAME`,
   `ARE_STATE_DIR`, `ARE_SECRETS_ENV`, `ARE_BIND`).

4. **GHCR access.** `GITHUB_TOKEN` with `packages: write` (already granted in
   `cd.yml`) pushes to `ghcr.io/corwinfoundation/*`. Ensure the package is
   **private** and the repo can write it (org → Packages → visibility).

5. **Secrets / vars** (repo or `production` environment scope):
   - secret `ARE_STAGING_ADMIN_TOKEN` — admin key to trigger the staging self-scan.
   - secret `PAPERCLIP_API_URL`, `PAPERCLIP_DEPLOY_BOT_KEY` — deploy-record poster.
   - var `PROD_BASE_URL` (e.g. `https://127.0.0.1:8443` loopback on box),
     `STAGING_TENANT_ID` (**never `vapi`**), `STAGING_AGENT_ID`,
     `SCAN_LOG_DB` (`/var/lib/are-scanner/scan_log.sqlite3`).

## Auto-rollback

Any failed acceptance check exits `acceptance_checks.sh` non-zero → the
`AUTO-ROLLBACK` step restarts the container on the previously-active image
(retained on the box, so it is a local retag+restart, seconds) and re-runs the
health leg. The deploy record is posted either way (`if: always()`), flagged
`AUTO-ROLLED BACK`.

## Safety invariants

- **Staging self-scan never targets `vapi`** — `acceptance_checks.sh` fails
  closed if the resolved tenant is `vapi` (standing blackout, BEAAA-13170).
- State + secrets are **host-mounted**, never baked into the image
  (`/var/lib/are-scanner/`, `secrets.env`). `ScanLog._migrate()` (PR #51)
  forward-migrates additive columns on connect, so image swap is safe.
- Image builds from `requirements-runtime.txt` (ADR-0038 slice); a failing
  import-closure guard **fails the build**, so a broken slice can't be pushed.

## Dependency / status

- Consumes the **container contract** from Phase 1 (BEAAA-13361): the
  `Dockerfile`, `ARE_CONTAINER_NAME`, `ARE_BIND`, host-mount layout.
- Phase 1 is blocked by BEAAA-12895 (Phase-0 clean-slice cutover).
- Therefore the **end-to-end dry-run acceptance + CTO co-sign** (this ticket's
  acceptance) runs once Phase 1 lands the container and the box is provisioned.
  See `docs/deploy/dry-run-acceptance.md`.
