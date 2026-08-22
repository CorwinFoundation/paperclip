# Push CD — one-click scanner deploy

Standing authority: Founder Directive 2026-07-21, "suspend the deploy ceremony
until first bind." This is the Development Mode deploy path. The full release
ceremony (cosign, SBOM, provenance, grant/lease/receipts, exact-SHA QA gates)
stays parked and intact in the tree, and the founder-approval checklist still
governs *that* path — not this one.

## Deploying

1. Merge to master. `scanner-edge-image.yml` builds on every master push, so a
   verified image artifact exists by the time you want it.
2. Actions -> **Deploy scanner (production)** -> **Run workflow**.

| Input | Meaning |
| --- | --- |
| `sha` | Blank ships current master HEAD. Or paste a full 40-char SHA. |
| `candidate_id` | Defaults to `BEAAA-22801`. The on-box helper requires `BEAAA-NNN`. |

The workflow refuses any commit that is not master or an ancestor of it, so a
side branch or an orphan cannot reach production.

**Rollback:** same workflow, previous good SHA in `sha`. Its image artifact is
retained 14 days; past that, `scanner-deploy` still holds the prior image and
env snapshot on the box (see `docs/deploy/scanner-deploy-runbook.md`).

## Why this runs on a self-hosted runner

**srv1749248 filters inbound SSH by source IP.** GitHub-hosted runners are not
on the allowlist. A deploy from `ubuntu-latest` hangs ~134 seconds on IPv4 and
then fails with:

```
ssh: connect to host srv1749248.hstgr.cloud port 22: Network is unreachable
```

The `Network is unreachable` is the IPv6 fallback, which GitHub-hosted runners
have no route for; the silent 134-second stall before it is IPv4 being dropped.

**This is not visible in `backbond-hostinger-hardening-firewall.sh`.** That
script governs the OS-level firewall and does keep port 22 open, dropping only
3000/8443 and 8000-8012. The source-IP restriction lives in the **Hostinger
cloud firewall**, outside the box. Reading the OS script alone will tell you,
incorrectly, that any host may connect. Measured:

| From | Result |
| --- | --- |
| GitHub-hosted runner | 134s hang, then `Network is unreachable` |
| AriClaw | same scp completes in ~5s |

So the deploy job runs on AriClaw via a self-hosted runner labelled `ariclaw`.
The box stays egress-sealed and never pulls its own release.

Only `deploy.yml` uses this runner. Every other workflow stays on GitHub-hosted
runners, so a stalled AriClaw blocks deploys but never blocks CI. Because a
self-hosted runner executes workflow code on that VM, **this repo must stay
private** — that is what prevents an outside PR from running code on AriClaw.

## The runner

Installed by `ariclaw-runner-setup.sh`. Summary of what exists on AriClaw:

- user `ghrunner` (the runner does not run as root)
- `/home/ghrunner/actions-runner`, registered as `ariclaw`
- systemd unit `actions.runner.CorwinFoundation-are-scanner.ariclaw.service`
- `gh` CLI installed — `deploy.yml` uses it to fetch the image artifact

Check status: `systemctl status actions.runner.CorwinFoundation-are-scanner.ariclaw.service`
Runner list: <https://github.com/CorwinFoundation/are-scanner/settings/actions/runners>

## Secrets

Repository secrets (Settings -> Secrets and variables -> Actions):

| Secret | Value |
| --- | --- |
| `DEPLOY_SSH_KEY` | private key authorized as `are-deploy` on srv1749248 |
| `DEPLOY_SSH_KNOWN_HOSTS` | `ssh-keyscan srv1749248.hstgr.cloud` output |

`SCANNER_EDGE_DEPLOY_SSH_PRIVATE_KEY` / `_KNOWN_HOSTS` (Jul 28, 2026) are an
earlier, unused attempt at the same thing. They are not read by this workflow.
Consolidate or delete them, but do not assume they are live.

The workflow declares no `environment:`, so there is no approval gate in front
of the button. Add `environment: production` to the `deploy` job to reinstate
reviewers.

## Local SSH gotcha

On Ari's laptop the box key is `~/.ssh/scanner_deploy`, and `~/.ssh/config.txt`
is **inert** — OpenSSH reads `~/.ssh/config` with no extension. Until that file
is renamed, every manual `ssh`/`scp` to the box needs an explicit
`-i ~/.ssh/scanner_deploy` or it silently tries the wrong key and fails with
`Permission denied (publickey,password)`.

## On-box helper contract

`/usr/local/sbin/scanner-deploy` (BEAAA-22831) takes **five** arguments:

```
sudo /usr/local/sbin/scanner-deploy <sha> <archive-path> <repo@digest> <archive-sha256> <BEAAA-candidate-id>
```

Two constraints that are easy to get wrong:

- The archive must be at `/var/tmp/backbond-scanner-<sha>.tar` exactly.
- The digest argument must be `<repo>@sha256:<64>` with **no tag**. The build
  artifact records `<repo>:sha-<sha>@sha256:<64>`, and the helper's regex
  rejects a `:` before the `@`. `deploy.yml` strips the tag; anything passing
  the artifact through verbatim will be refused on the box.

The helper verifies the archive SHA-256, the OCI revision label and the embedded
release manifest, activates atomically, probes readiness, and auto-rolls-back on
failure. Receipts: `/var/lib/backbond-deploy-agent/receipts/<sha>.json`.

## Verifying a deploy

```
curl -s https://scanner.backbond.ai/scanner/v1/sla                # expect 200
curl -s https://scanner.backbond.ai/scanner/v1/release-identity   # expect build_sha
ssh -i ~/.ssh/scanner_deploy are-deploy@srv1749248.hstgr.cloud \
  'ls -t /var/lib/backbond-deploy-agent/receipts/ | head -5'
```

## When it fails

| Symptom | Cause |
| --- | --- |
| `no successful scanner-edge-image.yml run found` | The build for that SHA has not finished or did not succeed. Check Actions, then re-dispatch. |
| Job queued forever | The `ariclaw` runner is offline. Check the systemd unit on AriClaw. |
| `Network is unreachable` after a long hang | The job ran on a GitHub-hosted runner. Check `runs-on`. |
| `Permission denied (publickey)` | `DEPLOY_SSH_KEY` is not authorized as `are-deploy`, or was pasted without its BEGIN/END lines. |
| `secret DEPLOY_SSH_KEY is not set` | Secret missing, misnamed, or created as an environment secret instead of a repository secret. |
| `usage: scanner-deploy ...` | The box has a helper older than BEAAA-22831. Reinstall from `scripts/deploy/scanner-deploy.sh`. |
| `archive hash drift` | Corrupted artifact round-trip. Re-run the build. |
| `embedded release identity mismatch` | Image `scanner_version`/`corpus_version` differ from what the helper pins. A version bump needs the helper updated too. |
| SLA never returns 200 | The helper already rolled back; production is on the previous image. Investigate on the box. |

## Local escape hatch

`backbond/operations/deploy/scanner-dev-mode-20260721/scanner-ship.sh` does the
same job from a laptop when Actions is unavailable. It was broken from
2026-07-28 until 2026-08-22 (it called the helper with 2 arguments after the
helper became 5-argument, and looked for a renamed artifact); it now matches.
