---
artifact_type: adr
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-13360 records the CTO decision (parent BEAAA-13358, corrected by BEAAA-13387) to replace on-box venv builds + the 7-step SSH runbook for the live BackBond Scanner edge (srv1749248) with a containerized, CI-built, Founder-gated CD pipeline whose delivery mechanism is an OPERATOR-MEDIATED PUSH — CI builds/tests the immutable image and pushes it to GHCR private; the operator pulls+verifies it on an egress-capable machine and pushes the tarball into the box over the existing inbound operator SSH channel; the box loads it, re-verifies digest/signature, and activates — changing no scoring, rubric, corpus, band, output-shape, or tamper-resistance behavior."
directive_ref: "founder-directive-deploy-pipeline-2026-07-10"
decision_source: "BEAAA-13358 CTO decision comment 56f9744b-5130-404e-b638-c05c6d7448c5 (owner: cto); CORRECTED by BEAAA-13387 CTO decision (mechanism pull -> push, egress lockdown confirmed 2026-07-10)"
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-13360", "BEAAA-13358", "BEAAA-13387", "BEAAA-13385", "BEAAA-12895", "BEAAA-13329", "BEAAA-13331", "BEAAA-12884", "BEAAA-13169"]
proof_points_cited:
  - "BEAAA-13387 CTO decision (Option A pull-runner invalidated by egress lockdown; corrected mechanism = operator-mediated push)"
  - "BEAAA-13358 CTO decision comment (containerize + Founder-gate the deploy)"
  - "BEAAA-13385 egress default-deny containment wall (box has no outbound HTTPS)"
  - "founder-directive-deploy-pipeline-2026-07-10"
  - "decisions/ADR-0038-runtime-requirements-slice.md (image builds from requirements-runtime.txt)"
  - "docs/adr/adr-0056-beaaa13331-offline-eval-framework-per-venv.md (offline eval per-venv, untouched)"
  - "ScanLog._migrate() (BEAAA-12895 / PR #51, forward-migrate on connect makes image-swap safe)"
classification: "rule-execution; transcribes a decided CTO decision into an ADR. No scoring math, rubric, judge, corpus, probe-mapping, band, output-shape, or tamper-resistance change. Deployment packaging/automation decision only."
scanner_version: "0.5.0"
corpus_version: "n/a — documentation-only; no corpus fixture read or emitted"
probe-set-version: "n/a — documentation-only; no probe executed"
rubric_point_id: "none — deployment-mechanism ADR; touches no rubric point"
n5_calibration_delta: "n/a by construction — this ticket edits one documentation file under docs/adr/ and changes no code the golden set exercises; score delta is structurally zero (no scoring/runtime code imported or altered). See Verification."
underwriter_gate: not_required
underwriter_gate_reason: "No scoring, rubric, corpus, judge, band, output-shape, or tamper-resistance change. Deployment packaging/automation only — CTO-unilateral per the directive's own escalation note and the 12884/12895 operator-gated precedent. CTO (BEAAA-13387) pre-committed to co-sign the push variant."
contract_output_shape_unchanged: true
non_score_moving: true
cto_cosign_ref: "PENDING — push variant, gated to CTO on the PR (idempotencyKey confirmation:BEAAA-13360:cto-cosign:adr-0057:push). The prior Option-A co-sign (confirmation:BEAAA-13360:cto-cosign:adr-0057:pr54, iid d6793a23-1082-412a-b8f3-91202f678ea5) is SUPERSEDED and flagged-for-rejection."
version: 2.0
prior_version: 1.0
self_audit_passed: true
escalations_noted:
  - "Classification is rule-execution under scanner-implementation-supervision-v1@v1.1: this ADR transcribes decisions the CTO already made on BEAAA-13358 and BEAAA-13387; there is no design latitude here. The PR is still gated to CTO for co-sign per the ticket's acceptance criteria."
  - "This ADR only records the decision. The build/CI/CD implementation lands under the phased children (Phase 1 Dockerfile + image-build guard, Phase 2 GHCR push + production Environment + release-approval workflow, Phase 3 operator push runbook), each a separate ticket."
  - "v2.0 correction: Option A (self-hosted pull-based runner) was INVALIDATED on 2026-07-10 by the egress finding (BEAAA-13387) — a self-hosted runner must reach api.github.com / pipelines.actions.githubusercontent.com OUTBOUND to register and long-poll, which the default-deny egress wall (BEAAA-13385) forbids. Mechanism corrected to operator-mediated push. Weights/scoring/output unchanged."
---

# ADR-0057 — BEAAA-13360 Containerized, Founder-gated CD pipeline for the scanner edge (operator-mediated push)

## Status

Accepted (v2.0 — mechanism corrected pull → push).

Decision made by the CTO on parent **BEAAA-13358** (decision comment
`56f9744b-5130-404e-b638-c05c6d7448c5`), transcribing
`founder-directive-deploy-pipeline-2026-07-10`, and **corrected on BEAAA-13387**
(deploy mechanism pull → push after the egress lockdown was confirmed). This ADR
is the durable record of that decision. Content is decided upstream; this file
has no design latitude.

## Decision log

- **v1.0 (2026-07-10, superseded in-place):** chose **Option A — a self-hosted,
  pull-based GitHub Actions runner on srv1749248** as the deploy mechanism.
- **v1.0 INVALIDATED (2026-07-10, BEAAA-13387):** the operator confirmed
  srv1749248 has **no outbound HTTPS** (`git ls-remote` and `pip` time out) and
  the Founder decided to **keep the egress lockdown** as the scanner's
  SSRF/exfil containment wall (formalized in BEAAA-13385). A self-hosted runner
  **cannot function** under default-deny egress: it must reach
  `api.github.com` / `pipelines.actions.githubusercontent.com` **outbound** to
  register and long-poll for jobs. Option A is structurally impossible, not
  merely undesirable.
- **v2.0 (2026-07-10, this revision):** deploy mechanism corrected to an
  **operator-mediated push**. CI still builds/tests/pushes the image (CI has
  egress); delivery **into** the box is a push over the existing inbound
  operator SSH channel, never a box-side pull. Founder `production` Environment
  gate unchanged; acceptance / auto-rollback / deploy-record unchanged.

## BLUF

Replace the live BackBond Scanner edge's on-box venv build + 7-step SSH runbook
with a **containerized, CI-built, Founder-gated CD pipeline**. The deploy
mechanism is an **operator-mediated push**: CI builds and tests the immutable
image and pushes it **private to GHCR** tagged with the merge SHA; the Founder
approves via the `production` GitHub Environment; then the operator, on an
egress-capable machine, pulls the image, **verifies its digest and signature**,
transfers the tarball **into** the box over the existing controlled inbound
operator SSH channel, and `docker/podman load`s it; the box **re-verifies the
digest/signature before activate**. There is **no box-side pull** — the box has
no outbound HTTPS, and general egress stays CLOSED (BEAAA-13385). No Underwriter
gate (no scoring/rubric/corpus/band/output-shape/tamper change). The Founder
approval gate is preserved as a `production` GitHub Environment required
reviewer.

## Context

The production edge (srv1749248 / 69.62.120.44) currently **builds its own venv
on every cutover**. On 2026-07-10 that on-box build model produced three
blocked deploys in one day:

1. openai / pyrit pin conflicts in the build;
2. a missing DB migration surfacing as a 500 at runtime;
3. the structural `garak==0.10.0` (`datasets<2.17`) ⊥ `pyrit==0.6.0`
   (`datasets>=3.0.0`) conflict, which cannot be solved in one venv.

Items (1) and (3) are the same class of failure that
`requirements-runtime.txt` (ADR-0038, BEAAA-13329) and the offline eval
per-venv split (ADR-0056, BEAAA-13331) were created to remove — but the box was
still building from the full contract on-box. Item (2) is exactly what
`ScanLog._migrate()` (BEAAA-12895, PR #51) was written to prevent.

The `founder-directive-deploy-pipeline-2026-07-10` directive is: **stop building
on the box.** Move the build to CI, ship an immutable image, and gate the deploy
behind a Founder click inside the pipeline instead of a manual SSH runbook.

**The load-bearing infrastructure fact this decision must honor (corrected on
2026-07-10):** the production box runs under **default-deny egress** — it has no
outbound HTTPS, by design, as the scanner's SSRF/exfil containment wall
(BEAAA-13385). The box holds the corpus and the provenance signing key while it
probes hostile third-party agents; an open outbound path is precisely the
exfiltration surface the wall exists to eliminate. The Founder decided to KEEP
that lockdown. Any deploy mechanism that requires the box to originate outbound
connections (a self-hosted runner long-polling GitHub; a box-side `docker pull`
from GHCR) is therefore **off the table**.

The other standing rule, unchanged: **no inbound interactive SSH to the
production box for any BackBond agent seat.** Box-side execution goes through
the Operator (Codex) under a CTO close-gate, per the 12884 / 12895 precedent.
The operator's controlled inbound SSH channel already exists and is the delivery
path for the push.

## Decision

### Decision 1 — Accept ownership
The CTO owns the pipeline. The Scanner Engineer executes the rule-execution
build/CI/CD tickets. The Operator (Codex) performs the box-side push + activate
(Phase 3); the CTO close-gates it. This mirrors the 12884 / 12895
operator-gated pattern — no BackBond agent seat gets interactive SSH.

### Decision 2 — Deploy mechanism = operator-mediated PUSH (not a box-side pull)
**Chosen: operator-mediated push.** The box never originates an outbound
connection to fetch its own release.

1. **CI builds + tests + pushes.** On merge to `master`, a GitHub-hosted runner
   (which has egress) builds the immutable image from the Phase-1 Dockerfile,
   runs the import-closure guard **at build time**, and pushes it **private** to
   `ghcr.io/corwinfoundation/are-scanner:<merge-sha>`, recording the image
   **digest** (and signature, once provenance signing lands — BEAAA-10945).
2. **Founder approval gate.** The `production` GitHub Environment required
   reviewer is the Founder. Nothing is released for delivery without the Approve
   click. A compromised push cannot self-deploy.
3. **Delivery INTO the box is a push.** On an **egress-capable** machine the
   operator pulls the approved image by digest from GHCR, **verifies the digest
   and signature**, saves it to a tarball (`docker/podman save`), and transfers
   the tarball into the box over the **existing controlled inbound operator SSH
   channel**. On the box the operator `docker/podman load`s the tarball and the
   box **re-verifies the digest/signature before activate** — so "what the
   Founder approved == what loads == what activates."
4. **Activate → acceptance → auto-rollback → deploy-record.** After load+verify:
   activate the container, then run the automated acceptance suite:
   health (`/scanner/v1/sla` 200; unauthenticated POST `/scanner/v1/scans` 401;
   forged-key POST 401) → staging self-scan (staging tenant, **never `vapi`** —
   BEAAA-13170 blackout — writing a fresh `scan_log` row with non-null
   `forensic_bundle_uri` + `snapshot_hash` + `probe_set_hash`) → BEAAA-13169
   customer-envelope check (the six external fields present, zero runtime fields).
   **Any** failed check auto-rolls-back to the previous image (retained on the
   box → local retag+restart, seconds) and re-runs the health leg. A deploy
   record (SHA · digest · checks · approver · timestamp · outcome) is posted to
   the tracker either way.

**Ingress note (why the push does not weaken the wall).** The containment wall
is **egress** (exfiltration), not ingress. The operator's inbound SSH channel
already exists and is the push path; operator-mediated push adds **no new
inbound surface** and opens **no** box egress. General egress stays CLOSED
(BEAAA-13385). Future phases may automate the push via a one-way artifact drop
into the box — still with **zero** box-originated egress.

### Decision 3 — Sequencing
Phase 1 (image parity) built on the already-merged `requirements-runtime.txt`
slice on master `d294c4e9` (BEAAA-13329). The Dockerfile **builds in CI, not on
the box**, so Phase 1 no longer depends on the box being mid-migration: its only
live dependency is this ADR / the mechanism pivot. Phases chain:
Phase 1 (containerize, CI build) → Phase 2 (GHCR push + `production` Environment
+ release-approval workflow) → Phase 3 (operator push runbook: pull+verify off-box,
transfer in, load, re-verify, activate, accept, record).

## Rejected / invalidated alternatives

### Option A — self-hosted, pull-based GitHub Actions runner on the box (INVALIDATED)
Chosen in v1.0; **invalidated in v2.0.** A self-hosted runner must reach
`api.github.com` and `pipelines.actions.githubusercontent.com` **outbound** to
register and long-poll for jobs. Under the default-deny egress wall (BEAAA-13385)
the box has no outbound HTTPS, so the runner **cannot register or receive work**.
This is a structural impossibility, not a trade-off. Reintroducing egress to
make it work would re-open the exact SSRF/exfil surface the wall exists to close
— rejected by the Founder. Superseded by operator-mediated push.

### Option B — GitHub Actions SSHes into the box with a deploy key (rejected)
Rejected in v1.0 and still rejected. Option B has GitHub Actions open an
outbound SSH connection **into** srv1749248 using a deploy key, which requires
the box to accept inbound SSH from GitHub Actions' broad, rotating egress
ranges. This reintroduces the inbound-SSH attack surface that the standing "no
inbound SSH to production" rule was written to eliminate, and adds deploy-key
distribution / ForceCommand wrapper / IP-allowlist burden with no offsetting
benefit over the operator-mediated push (which reuses the already-controlled
operator SSH channel).

## Consequences

**No Underwriter gate.** No scoring, rubric, corpus, judge, band, output-shape,
or tamper-resistance change — this is deployment packaging/automation only, so
no Underwriter co-sign is required (CTO-unilateral per the directive's escalation
note and the 12884 / 12895 precedent). The CTO (BEAAA-13387) pre-committed to
co-sign the push variant once the ADR transcription matches the BEAAA-13387
record.

**Founder gate preserved.** The Founder remains the production gate as a
`production` GitHub Environment required reviewer; no release is delivered
without the click.

**Zero box egress preserved.** The box never originates an outbound connection
to fetch its release. The digest/signature verify happens off-box (operator) and
again on-box (before activate), so an image-substitution between GHCR and the box
is caught at load time.

**Image provenance.** The image builds from `requirements-runtime.txt` (the
ADR-0038 production runtime slice — the online-service dependency set, excluding
the four offline eval frameworks), with the **import-closure guard run at build
time** so an image can never ship missing a runtime import. `requirements.txt`
stays the release-contract superset and is not what the image builds from.

**State and secrets stay host-mounted.** Persistent state and secrets remain
under `/var/lib/are-scanner/` (host-mounted), so an image swap does not touch
them. `ScanLog._migrate()` (BEAAA-12895, PR #51) forward-migrates the scan-log
schema on connect, which is what makes swapping the image (rather than
in-place-upgrading a long-lived process) safe.

**ADR-0056 untouched.** The offline eval-framework per-venv strategy (garak /
pyrit / agentdojo / inspect) is off the production path and is unchanged by this
decision.

**VAPI HOLD unaffected.** Staging smoke uses staging tenants only; the VAPI HOLD
/ blackout (BEAAA-13170) is not touched and `acceptance_checks.sh` fails closed
if the resolved tenant is `vapi`.

## Implementation (recorded here, executed under phased children)

This ADR records the decision only — **no code or runtime change in this
ticket.** The build/CI/CD work lands under the phased children filed on
BEAAA-13358:

- **Phase 1 (BEAAA-13361)** — Dockerfile (multi-stage, builds from
  `requirements-runtime.txt`) + PR image-build job running the import-closure
  guard at build time. Blocked only on this ADR / the mechanism pivot (the
  stale BEAAA-12895 blocker is released — the Dockerfile builds in CI, not on
  the box).
- **Phase 2 (BEAAA-13362)** — GHCR push on merge + `production` Environment
  (Founder = required reviewer) + Founder-gated **release-approval** workflow
  that records the approved digest for the operator push (no box-side pull job).
  The on-box activate / acceptance / auto-rollback / deploy-record scripts
  (`box_deploy.sh`, `acceptance_checks.sh`, `post_deploy_record.py`) ship here
  and are invoked by the operator on the box in Phase 3. Blocked on Phase 1.
- **Phase 3 (BEAAA-13363)** — Operator push runbook: on an egress-capable
  machine pull+verify the approved image by digest, transfer the tarball into
  the box over the existing inbound operator SSH channel, `docker/podman load`,
  on-box re-verify digest/signature, activate, run acceptance, auto-rollback on
  failure, post the deploy record; first pipeline-driven deploy under CTO
  close-gate; snapshot-dir rollback retained one cycle, then retired. Blocked on
  Phase 2.

## Verification

This is a documentation-only change: it edits this single ADR file under
`docs/adr/` and touches no scoring, rubric, corpus, probe, or runtime code.
Score delta on the golden set is therefore **structurally zero** — there is no
code path for this change to move a BackBond Score. The `n5_calibration_delta`
field is marked `n/a by construction` for that reason; a golden replay against
an unchanged scoring surface is vacuous.

The substantive claims transcribed here trace directly to their sources:

- Mechanism correction pull → push, egress lockdown, operator-mediated push
  steps: **BEAAA-13387** CTO decision.
- Egress default-deny containment wall (box has no outbound HTTPS): **BEAAA-13385**.
- Containerize + Founder-gate the deploy, no-Underwriter-gate, sequencing:
  BEAAA-13358 CTO decision comment `56f9744b-5130-404e-b638-c05c6d7448c5`.
- Image builds from `requirements-runtime.txt` slice: ADR-0038 /
  `decisions/ADR-0038-runtime-requirements-slice.md`, BEAAA-13329.
- Image-swap safety via forward migration: `ScanLog._migrate()`, BEAAA-12895 /
  PR #51.
- Customer-envelope six-field / zero-runtime check: BEAAA-13169.
- Offline eval per-venv untouched: ADR-0056, BEAAA-13331.

## Rollback

Revert this commit. It removes only this documentation change; there is no code,
scoring, or runtime behavior to roll back. The recorded decision would then be
carried by the BEAAA-13358 + BEAAA-13387 comments and the founder directive
alone until re-recorded.
