# Pytest capacity across two existing CI VMs

On 26 October 2026 Ari requested: “Can we safely let two pytest shards run in
parallel now that we have two VMs, so the remaining shards don't hold up the
queue?” This is an ongoing concurrency change on existing prepaid capacity.
Additional instance, plan or GitHub-hosted execution spending: $0.

The local `tests.yml` pytest matrix admits at most two jobs concurrently instead
of one. The observed heavy-runner pool contains one `backbond-ci` runner per VM:

| Host | Heavy runner | Existing job limits |
| --- | --- | --- |
| srv1799443 | 22, backbond-ci-srv1799443 | 5 GiB RAM, 1.5 CPU, two pytest processes |
| srv1701968 | 83, backbond-ci-srv1701968 | 5 GiB RAM, 1 CPU, two pytest processes |

Each runner accepts one job at a time. GitHub assigns a job only to an available
matching runner, so two eligible shards can occupy the two different hosts. If
one slot is unavailable or reserved, the extra shard queues. This does not create
another process or another heavy runner on either host.

The original host's broker still withdraws its ordinary test/control routes and
waits for active work to finish before admitting a Docker or publishing job. The
second host's test and control workers remain independent of that reservation.
The small control workers carry only `backbond-ci-control` and cannot consume
pytest shards; disposable build/publish and deployment workers have other labels.

Safety depends on preserving one heavy `backbond-ci` runner per physical host
and its enforced cgroup/storage/network boundaries. Registering additional heavy
workers on the same VM requires a separate resource/admission review; the matrix
limit is not a substitute for host limits. No runner registrations, labels,
permissions, host limits or broker behavior change in this rollout.

Hosted pytest concurrency remains six, local pytest processes remain two per job,
and evaluator concurrency remains one local/four hosted. Test selection, shard
coverage, failure reporting, timeouts, aggregate gates, trusted actor/fork
restrictions and local opt-ins remain unchanged. Busy/offline capacity never
authorizes paid fallback; the Founder CI spending policy remains binding.

Validate with the capacity boundary/mutation tests, existing Founder cost and
control-route guards, selector/sharding tests and actionlint. Before relying on
the change, retain a live run showing two pytest jobs with overlapping execution
on runners 22 and 83, and require all applicable exact-head gates to pass.
Existing runs retain their already-evaluated matrix settings; do not cancel
active work to adopt the new setting.

References: [GitHub matrix concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations),
[self-hosted runner assignment](https://docs.github.com/en/actions/reference/runners/self-hosted-runners),
and [Founder policy](founder-ci-cost-policy.md).
