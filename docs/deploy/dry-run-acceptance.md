# CD dry-run acceptance procedure (BEAAA-13362 acceptance-of-record)

The ticket acceptance is **one end-to-end dry run against a staging container**
plus a **forced-failure run proving auto-rollback**, then **CTO co-sign**. This
is the exact runbook the operator/CTO executes once Phase 1 (BEAAA-13361) lands
the container and the box is provisioned (`docs/deploy/cd-pipeline.md`).

## Preconditions
- Phase 1 image builds and `box_deploy activate` runs a healthy container
  against a **copy** of the prod state dir (never the live prod tenant).
- `production` Environment + Founder required-reviewer configured.
- Self-hosted `production` runner online; `box_deploy` sudoers-whitelisted.
- Secrets/vars set; `STAGING_TENANT_ID` is a staging tenant, **not `vapi`**.

## A. Happy-path dry run
1. Merge a no-op commit to master (or `workflow_dispatch` with a known SHA) to
   **simulate the merge** → `build-push` builds + pushes `:<sha>` to GHCR.
2. Confirm the `deploy` job is **queued and blocked** on the Founder review gate
   (nothing has touched the box yet). ← proves requirement #2.
3. Founder clicks **Approve**.
4. Observe: `box_deploy pull` → `activate` (swap) → `acceptance_checks.sh`:
   - `health` = 200 on `GET /scanner/v1/sla`
   - `auth_unauthenticated` / `auth_forged_key` = 401 on `POST /scanner/v1/scans`
   - `smoke_scan` writes a fresh `scan_log` row; `scan_log_pins` = `1|1|1`
     (forensic_bundle_uri + snapshot_hash + probe_set_hash all non-null)
   - `envelope` = six external fields present, zero runtime fields (BEAAA-13169)
5. Confirm the **deploy record** comment lands on BEAAA-13362 with SHA, digest,
   checks table, approver, timestamp. ← proves requirement #5.

## B. Forced-failure run (auto-rollback proof)
1. Break one check deterministically on the *staging* container only — e.g.
   point `PROD_BASE_URL` health at a port serving 503, **or** inject an image
   whose envelope leaks a runtime field.
2. Re-run the pipeline; approve the gate.
3. Observe: acceptance fails → `AUTO-ROLLBACK` restarts the container on the
   **previous** image → rollback health leg passes → deploy record posts
   `outcome=failure … AUTO-ROLLED BACK`. ← proves requirement #4.
4. Confirm the staging container is back on the prior image and healthy.

## C. Sign-off
- Attach both run URLs + both deploy-record comments.
- **CTO co-sign** on the Phase-2 PR (`cto_cosign_ref`).
- Then Phase 3 (BEAAA-13363) cuts the live edge over to this pipeline.

> This is a **staging** dry run. The first *production* deploy through the
> pipeline is Phase 3, operator-run, against the live edge.
