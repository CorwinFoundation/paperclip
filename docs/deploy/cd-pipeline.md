# CD pipeline — containerized, Founder-gated deploy (operator-mediated push)

**BEAAA-13362 (Phase 2)** · rule-execution under BEAAA-13358 /
`founder-directive-deploy-pipeline-2026-07-10` · mechanism per ADR-0057 v2.0
(BEAAA-13360), corrected by CTO decision **BEAAA-13387**. No scoring / rubric /
corpus / band / output-shape / tamper change — deploy plumbing only, **no
Underwriter gate**.

## Why push, not pull

srv1749248 runs under **default-deny egress** (BEAAA-13385): no outbound HTTPS,
by design, as the scanner's SSRF/exfil containment wall. A self-hosted GitHub
Actions runner on the box (the original Option A) would have to reach
`api.github.com` / `pipelines.actions.githubusercontent.com` **outbound** to
register and long-poll for jobs — impossible under the wall. So **the box never
pulls its own release.** GitHub Actions only builds+pushes the image and holds
the Founder gate; delivery **into** the box is an operator-mediated push over
the existing controlled inbound operator SSH channel. The wall is EGRESS, not
ingress — the operator SSH-in already exists, so the push adds no new inbound
surface and opens no box egress.

## What ships every merge

```
push to master
  └─ build-push  (GitHub-hosted CI — CI has egress)
     │   build image from Phase-1 Dockerfile, import-closure guard at build,
     │   push PRIVATE → ghcr.io/corwinfoundation/are-scanner:<sha>, record DIGEST
     └─ release-approval  (GitHub-hosted, environment: production)
        ├─ ⛔ FOUNDER GATE: the `production` Environment required-reviewer
        │      Approve click. No box contact from CI.
        ├─ write release-manifest.json  (sha · ref · DIGEST · approver · run-url)
        └─ post "approved — awaiting operator push" record → tracker

        ── then, OFF this workflow, the operator performs the push ──

operator, on an EGRESS-CAPABLE machine (not the box):
  ├─ docker pull <ref>@<DIGEST>        pull the Founder-approved digest from GHCR
  ├─ verify digest + signature         (fail closed on mismatch)
  ├─ docker/podman save → tarball
  └─ scp tarball INTO the box          over the existing inbound operator SSH

operator, ON THE BOX  (scripts/deploy/operator_push.sh):
  ├─ box_deploy load <tarball> <DIGEST>   docker load + ON-BOX digest RE-VERIFY
  │                                       (fail closed if digest ≠ approved)
  ├─ box_deploy current                   record previous image (rollback target)
  ├─ box_deploy activate <ref>            swap running container, keep prev image
  ├─ acceptance_checks.sh                 health / auth×2 / staging self-scan / envelope
  ├─ on failure → box_deploy rollback <prev>  (local retag+restart, seconds; NO egress)
  └─ post_deploy_record.py                SHA·digest·checks·approver·ts → tracker
```

"What the Founder approved == what loads == what activates" is enforced by the
**on-box digest re-verify** in `box_deploy load`, not by trusting the transport.
The box never originates an outbound connection at any step.

## One-time provisioning (Founder / operator — cannot be done from an agent seat)

These are GitHub-org and on-box settings; no agent has repo-admin or box SSH.

1. **`production` GitHub Environment + Founder gate.**
   Repo → Settings → Environments → **New** `production` →
   **Required reviewers** = the Founder. This single setting *is* the gate the
   `release-approval` job blocks on.

2. **GHCR access.** `GITHUB_TOKEN` with `packages: write` (already granted in
   `cd.yml`) pushes to `ghcr.io/corwinfoundation/*`. Ensure the package is
   **private** and the repo can write it (org → Packages → visibility). The
   operator's egress-capable machine needs a read token to `docker pull` the
   approved digest.

3. **Box deploy verb + sudoers whitelist.**
   Install `scripts/deploy/box_deploy.sh` → `/usr/local/sbin/box_deploy`
   (`root:root`, `0755`) and whitelist exactly it:
   ```
   are-deploy ALL=(root) NOPASSWD: /usr/local/sbin/box_deploy
   ```
   `are-deploy` gets **no other** root capability, and — unlike Option A —
   there is **no self-hosted runner process** on the box at all. Optionally drop
   box-local overrides in `/etc/are-scanner/deploy.env` (`ARE_CONTAINER_NAME`,
   `ARE_STATE_DIR`, `ARE_SECRETS_ENV`, `ARE_BIND`, `ARE_CONTAINER_ENGINE`).

4. **Box-local push env** (for `operator_push.sh`, in `/etc/are-scanner/deploy.env`
   or the operator's shell — never in CI):
   - `PROD_BASE_URL` (e.g. `https://127.0.0.1:8443` loopback on box),
     `STAGING_TENANT_ID` (**never `vapi`**), `STAGING_AGENT_ID`,
     `SCAN_LOG_DB` (`/var/lib/are-scanner/scan_log.sqlite3`).
   - `ARE_STAGING_ADMIN_TOKEN` — admin key to trigger the staging self-scan.
   - `PAPERCLIP_API_URL`, `PAPERCLIP_DEPLOY_BOT_KEY` — deploy-record poster.

## Operator push runbook (per release)

After the Founder approves the `release-approval` job, download the
`release-manifest-<sha>` artifact (sha · ref · **digest** · approver · run-url),
then on an egress-capable machine:

```
docker pull "<ref>@<digest>"            # pull exactly the approved digest
# verify signature (cosign, once BEAAA-10945 provenance signing lands)
docker save "<ref>" -o are-scanner-<sha>.tar
scp are-scanner-<sha>.tar operator@<box>:/tmp/     # inbound operator SSH only
```

then on the box:

```
scripts/deploy/operator_push.sh \
  --tarball  /tmp/are-scanner-<sha>.tar \
  --digest   <digest> \
  --ref      <ref> \
  --sha      <sha> \
  --approver <approver-from-manifest> \
  --run-url  <run-url-from-manifest>
```

`operator_push.sh` re-verifies the digest on the box, activates, runs
acceptance, auto-rolls-back on any failure, and posts the final deploy record.
The first pipeline-driven deploy runs under CTO close-gate (Phase 3,
BEAAA-13363).

## Auto-rollback

Any failed acceptance check exits `acceptance_checks.sh` non-zero → the
`operator_push.sh` auto-rollback restarts the container on the previously-active
image (retained on the box, so it is a local retag+restart, seconds — **no
egress, no pull**) and re-runs the health leg. The deploy record is posted
either way, flagged `AUTO-ROLLED BACK`. If even the previous image is absent
locally (should never happen — it is never pruned) the box cannot self-heal and
the operator is paged.

## Safety invariants

- **Zero box egress.** The box never originates an outbound connection —
  `box_deploy` has no `docker pull`; delivery is a tarball pushed in, rollback
  uses the locally-retained image. General egress stays CLOSED (BEAAA-13385).
- **On-box digest re-verify.** `box_deploy load` fails closed unless the loaded
  image carries the Founder-approved digest, so a tampered/wrong tarball never
  activates.
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
- Phase 1's Dockerfile builds in **CI, not on the box** (BEAAA-13387), so its
  stale BEAAA-12895 blocker is released; it stays blocked only on the ADR pivot.
- The **end-to-end dry-run acceptance + CTO co-sign** (this ticket's acceptance)
  runs once Phase 1 lands the container and the box is provisioned.
  See `docs/deploy/dry-run-acceptance.md`.
