# Small-check worker on the existing CI VM

Ari authorized this addition in Codex on 2026-10-25: add a memory-limited second
worker on the existing VM, retain heavy tests on the current worker, add no paid
fallback, and continue merging Quick PR #1269 before #1272. Additional VM or
hosted-runner spending authorized by this change: **$0**. This is an ongoing
local profile, with activation gated on the checks below.

The control worker uses `backbond-ci-control-srv1799443`, with the sole custom
label `backbond-ci-control`, on srv1799443.hstgr.cloud. Its host account is
`backbond-ci-control`, UID 1006, with a distinct home, subordinate-ID range,
rootless container, named volume and checkout. Nothing copies a live runner's
home, registration, dependency cache or credentials. No host directory or
container socket is mounted, and no port is published.

| Boundary | Limit |
| --- | --- |
| Aggregate UID 1006 slice, including rootless helpers | 1 GiB RAM, 0.25 CPU, zero swap, 256 tasks |
| Control container | 1 GiB RAM, 0.25 CPU, zero swap, 128 PIDs |
| Memory pressure priority | OOM score adjustment 1000 for manager, helpers and workload |
| Container privileges | Non-root, all capabilities dropped, no new privileges |
| Network | Host loopback disabled; separate UID-specific firewall denies host/public-host, private, link-local and IPv6 destinations |
| Allowed outbound traffic | Host stub DNS on port 53; public HTTPS for GitHub, action downloads and small Python test dependencies |

The current heavy worker retains its existing label, resources and two pytest
processes. Its configured 5 GiB/1.5 CPU ceiling plus the control profile totals
6 GiB/1.75 CPU on the observed 7.75 GiB/2 CPU host. These are ceilings, not a
claim of measured per-job demand. Current and peak memory, anonymous memory,
file cache and OOM events must be observed during qualification.

## Jobs

Only these five jobs move: `tests.yml` selector and pytest aggregate,
`pr-unit-tests.yml` selector, and `version-control-preflight.yml` release-identity
and policy checks. The selector's **output** still routes heavy work to
`backbond-ci`. Scanner regression, pytest shards, evaluator environments, CLI
rendering and other substantive tests retain their existing routes.

Existing actor, same-repository and local opt-in restrictions remain. The new
control smoke has no hosted branch: it runs only with the current local opt-in
and trusted actor/repository checks. Busy, offline or OOM-killed control capacity
queues or fails; it never purchases hosted execution or increases its limits.
Cost and isolation regressions remain failures, including routing heavy work to
the small worker. See [the Founder policy](founder-ci-cost-policy.md).

## Provision and qualify

1. Review the exact commit, `scripts/ci/install-control-runner.sh`,
   `start-control-runner.sh`, `control_worker_probe.py`, broker changes and tests.
   The publishing pool must remain inactive until its custom-label integration
   is merged. Do not interrupt active heavy or Operations jobs.
2. Export the clean `localhost/backbond-ci:2.337.0` image from the protected
   image-builder UID 1005 store, using the pinned `scripts/ci/Containerfile`.
   Supply `/opt/backbond-build-pool/control-base.tar` or another explicit
   root-owned mode-0600 archive and its independently retained SHA-256.
   Run `bash scripts/ci/install-control-runner.sh ABSOLUTE_ARCHIVE SHA256` as
   root. The script refuses occupied accounts/paths and existing firewall
   state. A partial install requires inspection, not blind rerunning.
3. Inspect the UID slice and all persistent rootless-helper cgroups, OOM score
   adjustments, container limits, volume mounts and network rules. Confirm
   the container and pause/conmon helpers survive the transient CLI service's
   exit, and a subsequent independent `podman exec` succeeds. The CLI helper
   uses `KillMode=process` so service exit does not kill detached descendants;
   those descendants must remain under the same bounded UID slice and retain
   OOM adjustment 1000. This setting applies only to transient CLI services,
   not to the persistent runner listener service. Confirm
   private/link-local/host addresses and host PostgreSQL/Ollama are unreachable,
   while DNS and public HTTPS work. The new firewall table must leave the
   existing publishing-pool fingerprint and all other UIDs unchanged.
4. Obtain a fresh repository-scoped registration token through the existing
   approved root-only App. Transfer it only to this container, never source,
   logs or persistent host configuration. Register the exact name with only
   `backbond-ci-control` as custom label and `_work` as work folder. Keep the
   listener stopped until label/isolation review completes.
5. Set the broker's root-owned `control_runner_id` to the returned positive
   registration ID. The broker refuses missing, invalid or changed identities.
   It journals each runner's exact custom label before deleting it, waits for
   stable idle API and local Worker observations, and cleans the heavy job
   before restoring recorded labels. Legacy string-only journals still restore
   `backbond-ci`; control entries restore `backbond-ci-control`. Verify restart
   recovery and API-restore-failure retention before publishing activation.
6. With publishing still inactive, start the reviewed control candidate using
   `bash scripts/ci/start-control-runner.sh` for its PR acceptance run. Run the
   `CI control worker smoke` workflow and observe these actual five checks
   while a heavy job runs. Retain exact source, runner/job IDs,
   before/after resource output and OOM counters. An offline worker is a queue,
   not permission to enable hosted routing. Qualify the publishing pool's
   drain/restore cycle before enabling its separate opt-in. Both profiles may
   be active together only after the control/broker integration is merged.

## Recovery

An OOM or control-job failure requires inspection of its cgroup evidence. Do not
raise caps automatically. Recover only this worker; preserve the active heavy
worker and outstanding broker journal. The control UID's egress service is a
dependency of its user manager at boot. Do not flush the firewall while the
worker is active. Keep local-CI opt-ins enabled; the Founder policy supersedes
historical hosted-rollback instructions in older setup notes.

A partially completed installer must not be rerun wholesale. After inspecting
the exact container, image, volume and account, use the reviewed transient helper
to resume only that stopped, unregistered container with `podman start`. Recheck
survival across helper exits, inherited cgroups/OOM priority and isolation before
continuing the remaining probe installation and registration steps. Preserve the
original failure log and the source hashes used for both attempts.

Implementation, local tests, VM provisioning, live acceptance and merged routing
are separate states. This document defines the intended acceptance procedure;
it does not itself establish that the worker is active or qualified.
