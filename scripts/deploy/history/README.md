# scripts/deploy/history/

Pre-promotion legacy deploy scripts preserved here for evidence.

ADR-0057 (BEAAA-13362) defined a four-part box-side pipeline. Three of the
four entrypoints are retired here (BEAAA-16598); `acceptance_checks.sh` is
retained at `scripts/deploy/acceptance_checks.sh` because it is still a live
shared utility invoked by `scripts/deploy/deploy_agent_acceptance.py`.

| Script | ADR-0057 role | Superseded by |
|---|---|---|
| `operator_push.sh` | Phase 3 orchestrator — SCP tar + invoke box_deploy | `dev_deploy.sh` SCP-helper emission |
| `box_deploy.sh` | Phase 3 on-box deploy verb — load + retag + restart | `scanner-deploy.sh` |
| `post_deploy_record.py` | Phase 3 deploy record writer, invoked by operator_push | commit-level evidence in runbook |

These three scripts are retired entrypoints; they are preserved here as
evidence and are not invoked by the new systemd-owned deploy path
(`scanner-deploy.sh` / `dev_deploy.sh`, BEAAA-16598).
