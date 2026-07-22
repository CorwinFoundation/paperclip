# Retired deploy documentation (historical evidence)

These documents describe deploy machinery that is **no longer the active path**.
They are retained as historical evidence and MUST NOT be used to deploy. The
current, single active deploy runbook is
[`../scanner-deploy-runbook.md`](../scanner-deploy-runbook.md) (BEAAA-16598).

| file | describes | superseded by |
|---|---|---|
| `cd-pipeline.md` | signed-candidate + deploy-lease CD pipeline / GHCR flow | systemd-owned `scanner-deploy.sh` |
| `first-container-cutover.md` | `box_deploy` / `operator_push` first container cutover | systemd-owned `scanner-deploy.sh` |
| `dry-run-acceptance.md` | staging dry-run acceptance for the old cutover | systemd-owned `scanner-deploy.sh` |
| `first-two-production-deploy-observation.md` | observation notes from the first two old-path deploys | — (evidence only) |

The old on-box verbs (`box_deploy`), the operator-push orchestrator
(`operator_push.sh`), GHCR-ref container swaps, `/version` deploy proofs, and the
`/etc/are-scanner` config tree are not part of the active deploy path.
