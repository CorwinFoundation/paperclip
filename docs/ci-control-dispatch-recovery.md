# Idle control dispatch recovery

Ari requested investigation and prevention of the final aggregate's 40-minute
dispatch wait on 27 October 2026. This change uses existing VM capacity and App
permissions, with $0 additional instance or hosted-runner spending.

## Incident evidence and uncertainty

For run 40462039011, head `5df25445e7d087eb8a249e50de022ce4d093974a`,
aggregate job 122052321651 became eligible at 08:47:21 Israel time and started
on control runner 98 at 09:27:28. It passed seven seconds later.

API snapshots showed both matching control runners online and idle during the
wait. Historical guest logs show no connection/authentication exception or
service restart in the incident window. Both hosts resumed receiving broker
acknowledgements at the same second, 09:26:34 Israel time. Resource snapshots
show unchanged 1 GiB / 0.25 CPU control bounds and no OOM kills.

This points to a shared job-delivery pause rather than exhausted VM capacity.
The exact upstream trigger is unverified; an open HTTPS socket or API `online`
status alone does not prove that GitHub will deliver a job. No public incident
was reported for that interval. The change is a bounded mitigation and an audit
trail, not a guarantee about GitHub's scheduler.

## Recovery boundary

The existing root build broker can opt in with the boolean configuration field
`control_dispatch_guard_enabled: true`. The guard remains off unless enabled.
The App key stays root-only on the original VM. No key is copied to VM2, no App
permissions are added, and no new service, user, runner or listener is created.

Once per minute, the guard checks for a trusted same-repository final `python
pytest` aggregate queued for at least five minutes on the exact existing
control route. It scans at most eight tests runs per pass and bounds additional
observation time. An active build reservation, withdrawn route, unexpected
identity/labels, API busy state, local Worker process or unhealthy service
prevents an automatic attempt.

Under the broker's existing exclusive lock it journals intent, withdraws only
the original control runner's custom label, then observes 65 seconds of stable
API/local idleness. Any activity or outside route restoration aborts without a
restart. If the target job has resolved, it restores the role without a restart.
Otherwise it refreshes only `backbond-ci-control-runner.service` under UID1006.
Registration, RSA identity, home/volumes, CPU/RAM/PID limits and network rules are
preserved. A newly created broker session and API/local health are required
before restoring the exact original control label.

There is one automatic attempt per job and at least 30 minutes between attempts
on different jobs. A job still waiting after the refresh produces an explicit
`control_dispatch_stall_unresolved` journal event; it is not repeatedly reset.
No job is cancelled, rerun, replayed, marked successful, or moved to another
route. All required checks and Founder cost-policy tests remain enforced.

## Deployment and recovery

Install the canonical `control_dispatch_guard.py` alongside the existing broker
and policy modules in `/opt/backbond-build-pool`. Enable the new field without
altering the local-CI opt-ins or existing configuration values. Update/restart
the root broker only while its admission journal is absent and no disposable
build/publish work exists; its ordinary heavy/control services keep running.
Verify the installed source against the reviewed commit and observe live
control-only smoke execution within the existing caps.

State is root-private under `/var/lib/backbond-build-pool`:
`control-dispatch-state.json` records cooldown/attempts;
`control-dispatch-recovery.json` durably records an owned maintenance action.
An interrupted pre-restart action restores the old route without stopping work.
An interrupted restart resumes at most once, and an unverified recovery leaves
the role withheld for inspection. The broker must recover this journal before
admitting another build. Never manually remove it or restore its label over
unverified service state. Disabling the new opt-in does not bypass recovery of
an existing maintenance journal.

Sources: [GitHub runner routing and pickup timeout](https://docs.github.com/en/actions/reference/runners/self-hosted-runners),
[official broker listener implementation](https://github.com/actions/runner/blob/v2.338.0/src/Runner.Listener/BrokerMessageListener.cs),
and [Founder spending policy](founder-ci-cost-policy.md).
