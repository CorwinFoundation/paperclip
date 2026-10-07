# Dedicated test runner rollout

Internal setup note, 2026-10-07. Selected host: srv1799443.hstgr.cloud (82.29.173.38).
Runner activation is pending validation.

Observed on the selected host, 2026-10-07: rootless container started as UID 1001;
CPU quota 150000/100000; memory limit 5368709120 bytes; swap limit 0; PIDs limit
512; NoNewPrivs=1. PostgreSQL port 5432 and Ollama port 11434 were unreachable
from the container at 127.0.0.1, 10.0.2.2 and 82.29.173.38. GitHub HTTPS returned
200. Both host services remained active. Runner registration and full suite
execution remain pending; these checks do not establish workload compatibility.

September billing recorded 43,725.81 Linux runner minutes at $0.006/minute.
The tests.yml workflow accounted for 27,969 hosted minutes in the repository's
usage metrics, about 64% of that Linux usage. Moving this workflow should address
the largest source of execution charges. This is an estimate from historical
usage, not a guarantee of future savings. A small hosted Docker/nginx integration
job retains edge coverage, so actual offloaded minutes will be lower. Storage,
other workflows and the Pro
subscription remain separately billable.

## First stage

1. Select an existing Ubuntu 24.04 x64 host with systemd, at least 8 GB RAM and
   adequate free disk (allow 40 GB initially for the large dependency caches).
   Prefer a dedicated VM. Do not reuse the AriClaw deployment runner account:
   it holds a deployment SSH identity and shares an already busy host.
2. Inspect the host and its other services. Review scripts/ci/install-dedicated-runner.sh.
   The selected host runs PostgreSQL and Ollama. The installer creates a separate
   account and a rootless Podman container, verifies the official runner download
   checksum, and caps execution at 1.5 CPUs and 5 GB RAM with no swap allocation.
   Only a task-owned named volume is mounted; no host files, credentials, container
   sockets or devices are exposed. No ports are published. slirp4netns disables
   host loopback access while preserving the container's own loopback for tests.
   Verify that host PostgreSQL and Ollama are unreachable before registration.
3. Obtain a fresh short-lived registration token from GitHub repository Settings
   > Actions > Runners > New self-hosted runner, or the GitHub REST API. Run the
   installer as root first. Register through podman exec inside the prepared
   container, using its non-root backbond-ci user. Transfer the short-lived token
   only to the selected host, with explicit authorization, and never commit it.
   Run scripts/ci/start-dedicated-runner.sh as root after registration to install
   and start its user service for reboot recovery. Do not put a long-lived GitHub
   account token on the server.
4. Verify the container first, including its filesystem boundaries, cgroup limits,
   external connectivity and blocked host service access.
5. Set repository Actions variable BACKBOND_LOCAL_CI to the exact string true.
   Manually dispatch the existing tests workflow on the migration branch with
   full=true before merging, then verify every selected job, including the
   required python pytest aggregate. The current master workflow ignores this
   variable until the change is merged. After a successful branch run, merge and
   manually run Dedicated CI runner smoke to check setup-python for both Python
   3.11 and 3.12 and the selector tests. Validate a same-repository PR before
   relying on this runner for normal merges.
6. Compare hosted minutes after activation, then consider migrating PR Unit Tests
   and version-control-preflight in a separate change.

## Routing and operating behavior

The six substantive jobs in tests.yml use the dedicated backbond-ci label only
when BACKBOND_LOCAL_CI=true, the actor is CorwinFoundation, and any PR branch belongs
to the same repository. Fork and bot runs stay on hosted runners. The selector and
required python pytest aggregate stay hosted. Test commands, shard selection,
nightly full coverage and required check names remain unchanged.
Docker/nginx edge coverage stays hosted and its result gates the same required
aggregate. The dedicated runner prepares cached Python with a relative library
lookup path so subprocess tests also work without LD_LIBRARY_PATH.
Local dependency caches live in the container's named volume, outside checkout
cleanup, with keys tied to Python and lock hashes. Hosted jobs retain their
GitHub cache behavior. Pip dependency checks gate the local test cache marker;
failed installs do not become reusable environments. Eval jobs retain their
existing clean-build verification and use the local wheel cache.
Two pytest workers
and one matrix job at a time limit resource use; pytest's timeout becomes 30 minutes.

Keep this dedicated account free of deploy credentials and personal files. The
rootless container reduces exposure but does not make untrusted code safe; routing
is deliberately restricted to owner-triggered work. Changes to those restrictions
require a separate review.

When enabled, an offline dedicated runner causes its jobs to queue. GitHub does
not automatically switch them to hosted runners. The machine must stay awake and
connected, including for the nightly 03:23 UTC full suite. Disable the variable
(delete it or set false) to return future runs to hosted runners. Cancel queued
local runs and start new runs after rollback; existing jobs retain their routing.

## Evidence and validation boundaries

This migration has not yet demonstrated a full suite pass on the target machine.
Activation is gated on isolation checks and an observed full branch run. GitHub
usage metrics and billing may update at different times, so compare daily usage
after the rollout rather than assuming immediate invoice changes.

References: [GitHub self-hosted runners](https://docs.github.com/en/actions/concepts/runners/self-hosted-runners),
[Python on self-hosted runners](https://github.com/actions/setup-python/blob/main/docs/advanced-usage.md),
[runner services](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application?platform=linux).
