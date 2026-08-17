---
owner: CTO
artifact_type: decision
status: review
updated_at: 2026-08-17
source_refs:
  - BEAAA-24049 (this issue)
  - BEAAA-24046 (parent incident: control plane hung mid-boot, PM2 reported online)
  - paperclip server/src/services/heartbeat.ts:8366 (per-agent admission gate)
  - paperclip packages/shared/src/constants.ts:78 (AGENT_DEFAULT_MAX_CONCURRENT_RUNS = 20)
  - ops/host-admission/ (staged configuration, probe, warden, tests, ROLLBACK.md)
claim_or_decision: >
  The BEAAA-24046 stall was memory over-admission surfacing as an IO stall, not CPU
  contention; remediate with a memory-derived admission cap plus a cgroup split of the
  control plane from agent seats, and never restart on a DEGRADED health verdict.
verification: >
  Live host measurement 2026-08-17 (PSI, cgroup, systemctl show, /proc). Health probe
  sabotage suite 11/11 including the outage signature; warden safety suite 10/10
  including two sabotage self-tests. Root-required tier is unverified — this account
  has no sudo, so the drop-in is staged and untested.
approvals:
  - QA gate on the exact candidate SHA before any shared/live apply (pending)
  - Founder/host-admin authorization for the root-level drop-in (pending)
---

# ADR-0068 — Host admission control and control-plane isolation (BEAAA-24049)

## Decision

Bound agent concurrency by a **memory budget**, isolate the control plane into its
own cgroup subtree, and drive recovery from a **listener-aware** probe whose
DEGRADED verdict sheds load instead of restarting.

Three tiers, separated by the authority each requires:

| Tier | Change | Root? | Authority |
|---|---|---|---|
| A | Cap per-agent `maxConcurrentRuns` from a memory budget | no | QA gate + Founder (shared/live) |
| B1 | Priority warden: renice / oom_score_adj / ionice on seats | no | QA gate + Founder (shared/live) |
| B2 | systemd drop-in: `Delegate=yes`, memory caps, `OOMPolicy=continue` | **yes** | Founder / host admin |
| C | Listener-aware health probe + bounded restart policy | no | QA gate |

## Context — what the measurements actually say

The parent ticket framed this as CPU/load pressure. The host disagrees.

**The stall is IO, not CPU.** PSI at 2026-08-17T19:32Z, load 35.34 on 8 cores:

```
/proc/pressure/cpu     full avg10=0.00  avg60=0.00  avg300=0.00
/proc/pressure/io      full avg10=12.10 avg60=17.42 avg300=17.86
/proc/pressure/memory  full avg10=1.79  avg60=1.41  avg300=1.67
```

CPU is *never* fully stalled; IO stalls every task ~18% of the time. Load 35 is
D-state on swap, not runnable demand. Swap was 3.9/4.0 GiB used. So CPU weighting
is close to useless here, and the acceptance criterion's instinct that unit-level
weighting won't protect the listener is right — but for a stronger reason than
cgroup topology alone.

**Nothing bounds memory anywhere in the stack.** `MemoryHigh`, `MemoryMax` and
`MemorySwapMax` are all `infinity` on `pm2-beai-agent.service`; the host runs
`vm.overcommit_memory=1` (always overcommit) with `vm.swappiness=60`. There is no
point at which allocation produces back-pressure — it produces swap, and swap
produces the IO stall above.

**Admission is per-agent only, with no host-wide ceiling.** In
`startNextQueuedRunForAgent`:

```ts
const runningCount = await countRunningRunsForAgent(agentId);
const availableSlots = Math.max(0, policy.maxConcurrentRuns - runningCount);
```

That runs under `withAgentStartLock(agentId)` — a *per-agent* lock counting *that
agent's* runs. Default is 20 per agent, clamped 1..50. Nothing anywhere sums across
agents. With ~15 live seats the theoretical ceiling is 300 concurrent runs on a
15 GiB host; observed at the time was 11 `claude` + 4 `codex` seats, 443 tasks in
the cgroup, 10.9 GiB resident. Each `claude` seat measured 300–400 MiB RSS before
its subprocess tree (pytest, mempalace, curl).

This is the root cause. The per-agent default is not "a bit high" — it is the wrong
denominator.

**The listener was never dead.** During the incident PM2 reported `online`, and
that was true but uninformative. Measured while degraded:

```
/                        200 in 0.006s
/api/health              200 in 1.510s
/api/issues/BEAAA-24049  200 in 30.242s   (000 at --max-time 45 earlier)
```

The socket was bound and the process alive throughout; the DB-backed path was
starved. Any probe of `/` or of PM2 state is false-quiet here.

**A latent total-outage bug, found while reading the unit.** The unit carries
`OOMPolicy=stop` with `Restart=on-failure` and `Delegate=no`, so the control plane,
the embedded postgres, and every agent seat share one cgroup. If the kernel OOM-kills
*any* process in that cgroup — including a disposable agent seat — systemd stops the
**entire unit**, and because it treats that as a clean stop, `Restart=on-failure`
does not restart it. One OOM-killed agent can permanently down the control plane.
This has not fired yet (no OOM kills in the kernel log, `systemd-oomd` inactive), but
it is one memory spike away and it is why `OOMPolicy=continue` is the highest-priority
line in the staged drop-in.

## Options considered

**Raise CPUWeight on the unit.** Rejected. `cpu full` pressure is 0.00% — there is no
CPU starvation to fix. It also cannot separate the listener from the seats, because
they are in the same cgroup.

**Lower the per-agent default from 20.** Necessary but not sufficient, and it is the
wrong shape: it still admits `N_agents × cap` with no host-wide term. Adding a seat
silently raises the ceiling again. Taken as the Tier A stopgap while the real fix is
specified, not as the fix.

**A global admission semaphore in the Paperclip scheduler.** The correct fix. It is an
upstream change to `heartbeat.ts` (a host-wide counter consulted alongside the
per-agent one) in a vendored dependency outside this repo, so it is filed as
follow-up rather than done here.

**Cap memory via cgroups without splitting the tree.** Bounds total footprint but
still lets the seats evict the listener inside the shared cgroup. Needed, insufficient
alone. Hence `Delegate=yes` plus the subtree split.

## Consequences

Tier A trades latency for stability: queued runs wait longer. The cap is read at
dispatch, so in-flight runs are never cancelled.

Tier B1's rollback is asymmetric and this is a real limitation, not a footnote.
Raising `nice`/`oom_score_adj` is unprivileged; lowering them is not. `--revert`
cannot fully restore already-running processes. It is acceptable only because agent
seats are short-lived — stopping `--apply` returns new seats to defaults.

Tier B2 requires a unit restart, which kills every in-flight agent run because they
are all children in its cgroup. That is precisely the run-recovery invariant the
acceptance criteria ask to verify, so it should be applied as a scheduled exercise
rather than opportunistically.

`MemorySwapMax=2G` is the highest-risk value: past `MemoryMax`, the kernel OOM-kills
inside the cgroup rather than swapping. That is the intended trade — a bounded local
kill beats a global IO stall — but it is safe *only* alongside `OOMPolicy=continue`.
The two must ship together.

## Status of verification

Verified: the diagnosis, and Tiers C and B1 in read-only modes. `control-plane-health.sh`
passes 11/11 sabotage cases, including reproducing the outage signature and proving a
naive `/` probe calls it healthy while the shipped probe returns DEGRADED.
`agent-priority-warden.sh` passes 10/10 safety assertions — it never targets the
listener, its ancestors, or postgres — with two sabotage self-tests confirming those
assertions can actually fail.

Not verified: everything requiring root, and the controlled-load run. `sudo -n true`
returns "a password is required" for uid 1001. The drop-in is staged and has never been
loaded by systemd. No claim is made that its values are correct under load; they are
derived from a single measurement window and should be treated as a starting point to
be tuned against the controlled-load exercise, not as settled configuration.
