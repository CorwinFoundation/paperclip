# BEAAA-24049 — rollback

Nothing in this directory has been applied. The host is unchanged; every
command below is written for the operator who applies a tier and then needs to
back it out.

## Recorded baseline (restore target)

Captured 2026-08-17 on this host, 8 cores / 15 GiB RAM / 4 GiB swap:

```
systemctl show pm2-beai-agent.service:
  CPUWeight=[not set]   IOWeight=[not set]
  MemoryHigh=infinity   MemoryMax=infinity   MemorySwapMax=infinity
  OOMScoreAdjust=0      OOMPolicy=stop       Restart=on-failure
  WatchdogUSec=0        Delegate=no          TasksMax=19144
  Slice=system.slice    ControlGroup=/system.slice/pm2-beai-agent.service
sysctl: vm.swappiness=60  vm.overcommit_memory=1  vm.overcommit_ratio=50
runtime: memory.current=10.9 GiB  memory.swap.current=1.19 GiB  pids.current=443
agent seats: 11 claude + 4 codex   listener PID 3056834 on 127.0.0.1:3100
```

Re-capture before applying anything:

```
systemctl show pm2-beai-agent.service > /tmp/24049-baseline-unit.txt
./agent-priority-warden.sh --report  > /tmp/24049-baseline-procs.txt
```

## Tier A — admission cap (`maxConcurrentRuns`), no root

Applied through the Paperclip agents API, per agent. Reversible by writing the
prior value back; capture it first.

- Forward: `PATCH /api/agents/{id}` with `heartbeat.maxConcurrentRuns = <n>`.
- Rollback: same call with the captured prior value (default is 20,
  `packages/shared/src/constants.ts:78`; server clamps to 1..50,
  `server/src/services/heartbeat.ts:229-230`).
- Blast radius: queued runs wait longer. No run is cancelled — the cap is read
  at dispatch time in `startNextQueuedRunForAgent`, so in-flight runs finish.
- Verify: `./control-plane-health.sh` should stay exit 0 with `ready_s` falling.

## Tier B1 — priority warden, no root

- Forward: `./agent-priority-warden.sh --apply`
- Rollback: `./agent-priority-warden.sh --revert`

`--apply` captures each process's start time, nice value, `oom_score_adj`, and
I/O class/priority in a uid-private runtime directory before changing it.
`--revert` rejects reused PIDs, then restores the captured `oom_score_adj` and
I/O priority. On the observed uid 1001 host, `oom_score_adj 500 -> 0` is
reversible by the same uid without effective capabilities.

Nice restoration is different: after the same uid raises niceness from 0 to
10, Linux requires `CAP_SYS_NICE` to lower it to 0. `--revert` therefore leaves
the running process at nice 10 and prints an explicit residual. Complete
operational rollback is: stop scheduling `--apply`; run `--revert` to restore
OOM and I/O state; then allow affected short-lived seats to exit normally. New
seats inherit nice 0. An immediate nice restore requires a privileged operator
to run `renice -n <captured-prior-value> -p <pid>` or restart the affected seat,
not the control plane.

The warden never targets the listener, its ancestors, or postgres; that
property is asserted by `test_warden_safety.sh` (10/10 on 2026-08-17,
including two sabotage self-tests).

## Tier B2 — systemd drop-in, root required

- Forward:
  ```
  install -D -m0644 systemd/10-control-plane-protection.conf \
    /etc/systemd/system/pm2-beai-agent.service.d/10-control-plane-protection.conf
  systemctl daemon-reload
  systemctl restart pm2-beai-agent.service
  ```
- Rollback:
  ```
  rm -f /etc/systemd/system/pm2-beai-agent.service.d/10-control-plane-protection.conf
  rmdir --ignore-fail-on-non-empty /etc/systemd/system/pm2-beai-agent.service.d
  systemctl daemon-reload
  systemctl restart pm2-beai-agent.service
  ```

Every directive in the drop-in is additive over a value that was unset, so
deleting the file restores the recorded baseline exactly — there is no prior
value to lose.

Two cautions:

1. `systemctl daemon-reload` alone applies the cgroup properties, but
   `Delegate=yes` and `OOMPolicy` only take effect on restart. A restart of
   this unit kills every in-flight agent run, because they are all children in
   its cgroup. Schedule it, and expect run-recovery to be exercised.
2. `MemorySwapMax=2G` is the highest-risk value. If the working set genuinely
   exceeds `MemoryMax=11G`, the kernel starts OOM-killing inside the cgroup
   instead of swapping. That is the intended trade — a bounded local kill
   beats a global IO stall — but it is only safe once `OOMPolicy=continue` is
   in the same file, which it is. Do not apply `MemorySwapMax` without it.

## Tier C — health monitor

`control-plane-health.sh` is read-only and needs no rollback. Any supervisor
wired to it must honour the exit-code contract: **exit 1 (DEGRADED) must never
trigger a restart.** Restarting a starved-but-serving control plane discards
in-flight runs and deepens the stall — that is the failure this whole ticket
exists to prevent.
