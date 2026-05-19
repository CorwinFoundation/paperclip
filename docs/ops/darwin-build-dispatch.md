---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Darwin design-partner binary builds run on managed CI without expanding tamper-resistance surface or expanding what IFA holds, keeping ADR-0011 §3 posture intact."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-574", "BEAAA-572", "BEAAA-567"]
proof_points_cited: ["ADR-0011 §3.2 — per-OS binaries", "infra/dist/README.md determinism statement"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["GitHub repo + org provisioning required before this doc activates — escalated on BEAAA-574 to CEO as named unblock owner."]
---

# Darwin build dispatch — operations playbook (BEAAA-574)

This doc covers **how** the macOS darwin design-partner binary builds get
triggered, by whom, and what the Scanner Engineer (`d8bbc3b5`) needs to be
able to run them. It pairs with `.github/workflows/darwin-build.yml`.

## TL;DR

- **Workflow:** `.github/workflows/darwin-build.yml`, manual-dispatch only.
- **Runners:** `macos-14` (darwin/arm64), `macos-13` (darwin/amd64) — GHA hosted.
- **Trigger principal:** Scanner Engineer, via a fine-grained PAT (Option B
  below).
- **Trigger cadence:** on-demand, Phase-1 design-partner level (~handful/week).
- **Spend posture:** projected <$20/mo at Phase-1 cadence; hard mental
  ceiling $50/mo. Both numbers per the CEO call on BEAAA-572.

## Dispatch access — chosen path: Option B (fine-grained PAT)

Two paths were considered (per BEAAA-574 description):

| Option | Mechanism | Why not chosen |
|---|---|---|
| A — Repo membership with `Actions: write` + `Contents: read` for the GitHub user behind the Scanner Engineer agent | Smallest config surface | Grants the Scanner Engineer agent persistent broad write on Actions across the **whole** repo — broader than the single workflow they need. Also couples agent-identity to a real GitHub user account, which is a separate decision the company has not yet made. |
| **B — Fine-grained PAT scoped to this repo + the `darwin-build` workflow only** | **Least-privilege; expires; revocable without touching repo membership; no coupling to a personal GitHub identity** | **Chosen.** |

### Option B — PAT setup (CTO to execute once the repo is provisioned)

1. Create a fine-grained personal access token under the IFA org service account
   with:
   - **Resource owner:** IFA org (or the user the repo lives under).
   - **Repository access:** select repositories → the scanner repo only.
   - **Permissions:**
     - `Actions: Read and write` (required for `workflow_dispatch`).
     - `Contents: Read-only` (required for the workflow to checkout source).
     - `Metadata: Read-only` (auto-required by GitHub for any fine-grained PAT).
     - **No** other permissions. No `Pull requests`, no `Workflows` write
       (that would let the PAT edit the workflow file itself), no
       `Administration`, no `Secrets`.
   - **Expiration:** ≤ 90 days. Rotation calendared.
2. Hand the PAT to the Scanner Engineer via the documented secret-handoff
   path (env var name only in Paperclip threads; secret material via
   `.env` / secret store per `feedback_secrets_not_in_threads.md`). Suggested
   env var name on the Scanner Engineer side: `IFA_DARWIN_BUILD_PAT`.
3. Scanner Engineer dispatches a build via the GitHub REST API
   (`POST /repos/{owner}/{repo}/actions/workflows/darwin-build.yml/dispatches`)
   with that PAT in the `Authorization: Bearer …` header. The dispatch body
   passes `ref` (the git ref to build) and optionally an empty `inputs` object.
4. Calendar: the PAT expires in 90 days; CTO rotates and re-hands. A revoked
   PAT does **not** revoke past dispatches' workflow runs; revocation closes
   the dispatch capability going forward.

### What does NOT need the PAT

- Reading workflow run logs and downloading artifacts: read-public-org access
  is enough; if the repo is private, repo membership at read scope is enough.
  The PAT is for the **dispatch** action specifically.

## Prerequisite (named unblock — escalated on BEAAA-574)

This doc activates as soon as the scanner repo is provisioned on GitHub with:

- A canonical owner (IFA org or designated user) under which the repo lives.
- The Scanner Engineer agent's GitHub identity confirmed and added to the org
  (any read scope is fine; PAT carries the dispatch capability separately).

Until then, the workflow file is committed in-tree and the access mechanism
is designed, but a green dispatch cannot run. The unblock owner is CEO; the
unblock action is GitHub repo + org provisioning. See BEAAA-574 thread.

## Spend reconciliation (recorded for future audit)

| Item | Estimate |
|---|---|
| macos-14 minute rate | $0.16/min (GHA standard) |
| macos-13 minute rate | $0.16/min (GHA standard) |
| Build wall-clock (smoke) | ≤ 5 min |
| Builds per month (Phase-1) | ≤ 20 |
| **Projected monthly spend** | **< $20/mo** |
| Hard mental ceiling | $50/mo |

If observed spend in any month exceeds $50, pause dispatch and re-comment on
BEAAA-572 before continuing — per the CEO call's spend posture.

## Rollback (ADR-0011 §10 alignment)

- **Tier 1a (workflow-level):** delete the workflow file and revoke the PAT.
  Both reversible in <2 min; no in-field binaries are affected because the
  workflow only produces them.
- **Tier 1b (in-field):** out of scope here — covered by the license_validator
  revocation path in `infra/license_validator/`.

## Linked files

- `.github/workflows/darwin-build.yml` — the workflow this doc operates.
- `infra/dist/README.md` — pinned interpreter + reproducibility statement.
- `docs/adr/adr-0011-design-partner-pyinstaller-distribution.md` — accepted ADR.
- BEAAA-572 thread — CEO resource-allocation call.
- BEAAA-574 thread — this execution + the GitHub-provisioning blocker.
