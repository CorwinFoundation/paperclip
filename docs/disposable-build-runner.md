# Disposable Docker and publishing runners

Provisioned on October 25, 2026. Routing is controlled by
`BACKBOND_LOCAL_DOCKER`; enable it only after the acceptance checks below pass
on srv1799443. Live rollout evidence is recorded in
[PR 1270](https://github.com/CorwinFoundation/are-scanner/pull/1270).
The inactive change may merge first so its protected manual acceptance workflow
is available on master. Existing `BACKBOND_LOCAL_CI` and
`BACKBOND_LOCAL_PREFLIGHT` stay enabled throughout.

Six current jobs can use the pool: Docker/nginx integration, Kaltura image
readiness, ci-plugin acceptance, read-only image verification, the master
Scanner image publisher, and the Collector release publisher. Historical
evidence/signing branches have pinned workflow identities and remain outside
this migration. macOS work requires macOS capacity. The deployment runner and
its SSH authority are outside this pool.

## Boundaries

`backbond-build` and `backbond-publish` are separate nonlogin Linux accounts,
with separate subordinate UID ranges. Each admitted job receives a new JIT
runner identity, fresh home/tool cache, private Docker daemon, private Unix
socket and a fixed 14 GiB ext4 filesystem. There is no cross-job build cache.
Cleanup removes the entire pod and filesystem before ordinary CI resumes.
Cleanup failure closes admission and retains the recovery journal.
The host-side Podman home, configuration, temporary files and runtime namespace
also belong to that job. Cleanup ends its rootless pause namespace and user
session before unmounting the filesystem. Execution-account homes are root-owned
and do not retain writable user-service configuration.

`backbond-image-build` is a third nonlogin account used only to build the runner
image from approved source. It never receives GitHub jobs. Keep this account's
image cache separate from both execution profiles when updating runner images.

The runner root filesystem is read-only, drops all capabilities and sets
NoNewPrivs. Docker runs in a rootless Podman sidecar with namespaced privilege.
This is container isolation on a shared kernel, not a nested VM. No `/dev/kvm`
is available on this host. The sidecar's root maps to an unprivileged host UID;
it has no VM Docker socket, production directory or administration credential.
Jobs still require the existing trusted actor and same-repository checks.
The immutable job-start hook also checks repository, workflow, run, attempt,
job, event and actor; publishers require master or the admitted Collector tag.
The broker checks that a publishing commit is an ancestor of master.

Only the three new CI UIDs receive egress restrictions: deny host/public-host addresses,
private/link-local networks and IPv6; permit the local DNS resolver on port 53.
Public registry, GitHub and keyless signing services remain reachable. No host
ports are published. DNS and GitHub/GHCR access must be tested after installation.

The host has 2 CPUs, 7,940 MiB RAM, no swap and 28 GiB free disk at inspection.
Both existing CI runners have independent 5 GiB caps. The broker removes only
their `backbond-ci` routing label, waits for stable idle API observations and
absence of Runner.Worker processes, then admits one worker capped at 5 GiB and
1.5 CPUs. Existing jobs finish normally; subsequent jobs queue. It restores
the labels after cleanup and gives ordinary CI a two-minute scheduling window.
It never changes local opt-ins or buys hosted overflow.
Admission also requires at least 6 GiB of currently available memory. New build
containers receive OOM score 1000 so memory pressure preferentially kills them.
These controls leave production headroom; they do not reserve capacity for a
future Ollama model load. Treat production memory pressure as a failed/queued
build, never as authority for hosted overflow. An existing label reservation by
another administrator closes admission rather than being taken over.

The other drained runner belongs to
`CorwinFoundation/backbond-customer-operations`. Its current integration workflow
uses static queue-only labels. Preserve that property when changing admission.
Runner identities are checked against both repository and name; numeric IDs
alone are insufficient because IDs overlap between repositories.

## Restricted GitHub App

The manifest is `scripts/ci/build-pool-app-manifest.json`. Install a private App
only on `are-scanner` and `backbond-customer-operations`, with:

- Administration write: required by GitHub's repository JIT-runner and runner
  label APIs. This is a broad repository permission and must be approved as such.
- Actions read: inspect queued jobs, actor/event identity and busy status.
- Contents read: verify publishing commits are on master history.
- Metadata read: GitHub's implicit installation permission.

The approved installation is App `5465492`, installation `175081250`, named
BackBond isolated CI admission. It is restricted to those two repositories.

No webhook, contents write, package write or secret-management permission is
requested. Only the root broker holds the App private key. It requests short-lived
installation tokens restricted to these repositories and permissions. Do not use
a long-lived personal account token. Job-scoped GHCR/OIDC credentials remain in
the existing publishing workflow; they never enter the PR build profile.

Keep `/etc/backbond-build-pool/config.json` and `app.pem` root-owned mode 0600.
App ID and installation ID use the example generated by the preparation script.
The App key is never copied into an image, job volume, Git repository or log.

## Installation and acceptance

1. Review the source and App permission scope. Approve App creation separately
   before granting new repository administration access.
2. Run `scripts/ci/prepare-build-pool.sh` as root on the inspected Ubuntu 24.04
   host. It refuses to replace an existing installation. It prepares inactive
   services and does not touch existing routing. Review generated egress rules.
3. Reserve capacity by draining the two existing runners and recording a
   `bootstrap: true` admission journal. Build under `backbond-image-build`, which
   has never executed a job. Podman 4.9 image builds use `--cpu-period 100000
   --cpu-quota 100000 --memory 1g`; `podman build --cpus` is unsupported here.
   Build the existing Containerfile as `localhost/backbond-ci:2.337.0`, then
   build `BuildRunner.Containerfile` from the scripts/ci directory as
   `localhost/backbond-build-runner:2.337.0`. Pull the digest-pinned Docker image
   in build_pool_worker.py. Save both final images into the root-owned archive
   `/opt/backbond-build-pool/images.tar`; confirm both image contents load into
   an empty Podman store. Docker archives retain the runner tag but omit the
   Docker image's repository digest, so Podman can resolve that pinned digest
   again from Docker Hub before starting it. After successful preparation and inspection of
   the stopped builder, clear only the bootstrap flag and let broker recovery
   restore the labels. An interrupted bootstrap deliberately blocks automatic
   recovery until the builder has been inspected.
4. Install the approved App key and configuration. Enable the egress service.
   After reviewing the active rules, record the SHA-256 of the exact bytes from
   `nft --stateless list table inet backbond_build_pool` as `egress_sha256` in
   the root configuration. The broker checks this fingerprint before and after
   draining; missing or changed rules close admission.
   Check denial of VM public/loopback/private addresses from both profiles, DNS
   resolution, public HTTPS, UID mappings, mount inventory and absence of host
   Docker/SSH/database credentials. Confirm that publisher files are unreadable
   by the build UID. Exercise a real Docker build, bind-mounted container action,
   published port and Docker buildx operation with the nonroot runner client.
5. Exercise broker drain/recovery without product publishing: queued tests stay
   queued, running tests finish, memory never overlaps, and forced worker failure
   removes its pod before labels are restored. Restart the controller during an
   active job and verify journal recovery. Confirm the immutable start hook sees
   every required GitHub field and rejects a mismatched run/workflow/ref.
6. Merge the opt-in change while `BACKBOND_LOCAL_DOCKER` remains unset. The
   `build-pool-smoke.yml` workflow is manual, master/actor gated and entirely
   self-hosted; it never has hosted overflow or package-write permission.
   Start the broker with the Docker opt-in still unset.
   Dispatch it twice to prove Docker, GHCR pull, OIDC and fresh per-job homes.
   Run the migrated Docker coverage and a nonpublishing GHCR pull/OIDC smoke on
   the intended profiles. Confirm artifact uploads and empty cache/home on the
   next job. Do not publish a release solely as a migration smoke test.
7. Only after these checks, set
   `BACKBOND_LOCAL_DOCKER=true` for the reviewed workflow change. Eligible jobs
   then queue on role/run/attempt/job labels. Busy/offline capacity has no paid
   fallback. Verify the first normal publishing run on the isolated profile.

The small unregistered Docker probe on October 25 successfully built and ran
an Alpine image as host UID 1003, with NoNewPrivs=1 and no host volume/socket.
The separate read-only, capability-free UID-1001 client also passed buildx,
a bind-mounted container execution and an nginx port-forwarding smoke inside
the pod network. The probe pod was removed afterward; PostgreSQL and Ollama
remained active.
This proves nested Docker feasibility only. It does not establish the full
acceptance list or authorize activation.

## Recovery

If the broker stops, leave the local opt-ins enabled. Restart it to clean the
worker and restore the journaled labels. If cleanup or API recovery fails,
inspect `/var/lib/backbond-build-pool/admission.json` and the exact pod before
restoring labels. Never delete another runner's container, volume or daemon.
Never restore labels while a build worker is still consuming its reservation.
No global container/image prune belongs in recovery.

Stopping the pool after migration causes eligible jobs to queue. Returning
them to hosted runners or disabling the new opt-in would expand paid routing
and requires Ari's explicit approval under docs/founder-ci-cost-policy.md.

GitHub's [runner API](https://docs.github.com/en/rest/actions/self-hosted-runners)
documents JIT registration permissions. Its [secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use)
requires clean environments when reusing hardware. Docker's
[rootless guidance](https://docs.docker.com/engine/security/rootless/)
and [Podman's run reference](https://docs.podman.io/en/v4.9.3/markdown/podman-run.1.html)
describe the limits of namespaced privilege.
