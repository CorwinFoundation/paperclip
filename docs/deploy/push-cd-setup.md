# Push CD setup — one-click scanner deploy

Standing authority: Founder Directive 2026-07-21, "suspend the deploy ceremony
until first bind." This is the Development Mode deploy path. The full release
ceremony (cosign, SBOM, provenance, grant/lease/receipts, exact-SHA QA gates)
stays parked and intact in the tree.

## What this replaces

The old path was: build locally with `scripts/deploy/dev_deploy.sh` (clean
master, docker, cosign, a minted `BACKBOND_CANDIDATE_ID`), let it *print* an scp
+ ssh command, then run that command yourself from an egress-capable machine.
Every deploy needed your laptop awake and a human in the loop.

The new path is: merge to master, then click **Run workflow** on
**Deploy scanner (production)**. That's it.

## Why CI is allowed to SSH in now

The parked `docs/deploy/parked/cd.yml.parked` argues for operator-mediated push
because srv1749248 has no egress and therefore cannot pull its own release.
That premise is correct and unchanged. It just doesn't constrain a *push*:
`backbond/operations/deploy/backbond-hostinger-hardening-firewall.sh` keeps port
22 public and drops only 3000/8443 and the Docker-published 8000–8012 range.
Inbound SSH has always been open, so CI reaching in needs no firewall change and
no self-hosted runner.

Nothing about the box's trust model changes:

- `are-deploy` still has NOPASSWD sudo for exactly `/usr/local/sbin/scanner-deploy`.
- That helper still verifies the archive SHA-256, the OCI revision label, and the
  embedded release manifest (`scanner_version`, `corpus_version`, `build_sha`,
  `release_tag`) before touching anything.
- Activation is still atomic, health-checked on `127.0.0.1:8077/openapi.json`,
  and **auto-rolls-back** on failure.

The delta is who can *trigger* a deploy. In Development Mode that's an accepted
trade; revisit it at first bind.

## Setup (three steps, once)

### 1. Generate a dedicated key

Do this on your machine, not the box:

```bash
ssh-keygen -t ed25519 -N '' -C 'github-actions-deploy' -f ~/.ssh/are_deploy_ci
```

### 2. Authorize it on the box

```bash
ssh are-deploy@srv1749248.hstgr.cloud \
  "install -d -m 700 ~/.ssh && cat >> ~/.ssh/authorized_keys" < ~/.ssh/are_deploy_ci.pub
```

**Check for an IP pin first.** If the existing `are-deploy` entries carry a
`from="..."` clause, GitHub's runner IPs will never match it and the deploy will
fail at the SSH step. Confirm what's there:

```bash
ssh are-deploy@srv1749248.hstgr.cloud 'cat ~/.ssh/authorized_keys'
```

The new line must have no `from=` restriction. GitHub's runner ranges are large
and rotate, so pinning them is not practical; the sudo scoping above is what
bounds the key.

### 3. Add the GitHub Secrets

Repository → Settings → Secrets and variables → Actions → New repository secret:

| Secret | Value |
| --- | --- |
| `DEPLOY_SSH_KEY` | contents of `~/.ssh/are_deploy_ci` (the **private** key, including the BEGIN/END lines) |
| `DEPLOY_SSH_KNOWN_HOSTS` | output of `ssh-keyscan srv1749248.hstgr.cloud` (optional but recommended) |

Without `DEPLOY_SSH_KNOWN_HOSTS` the workflow keyscans at run time and logs a
warning — it works, but it trusts the host key on first sight every run.

These are repository-level secrets. The workflow deliberately declares no
`environment:`, so no approval prompt sits in front of the click. Add
`environment: production` to the `deploy` job if you later want reviewers back.

## Deploying

**Merge to master.** `scanner-edge-image.yml` now runs on every master push, so
a verified, deployable image exists by the time you want it.

**Then:** Actions → *Deploy scanner (production)* → Run workflow.

| Input | Meaning |
| --- | --- |
| `sha` | Blank ships current master HEAD. Or paste a full 40-char SHA. |
| `candidate_id` | Defaults to `BEAAA-22801`. The on-box helper requires a `BEAAA-NNN`; in Development Mode a fixed constant is fine. |

The workflow refuses any commit that isn't master or an ancestor of it, so a
side branch or an orphan can't reach production.

## Rolling back

Dispatch the same workflow with the previous good SHA in `sha`. Its image
artifact is retained for 14 days. Past that, `scanner-deploy` has already kept
the prior image and env snapshot on the box — see
`docs/deploy/scanner-deploy-runbook.md`.

## When it fails

| Symptom | Cause |
| --- | --- |
| `no successful scanner-edge-image.yml run found` | The commit predates the push trigger. Dispatch that workflow against the SHA, then retry. |
| SSH step hangs or `Permission denied` | The key isn't in `authorized_keys`, or an existing `from=` clause is pinning source IPs. |
| `usage: scanner-deploy ...` | The box is running a helper older than BEAAA-22831. Reinstall from `scripts/deploy/scanner-deploy.sh`. |
| `archive hash drift` | Corrupted artifact round-trip. Re-run the build. |
| `embedded release identity mismatch` | The image's `scanner_version`/`corpus_version` don't match what the helper pins. A version bump needs the helper updated too. |
| SLA never returns 200 | The helper already rolled back; production is on the previous image. Check the box. |

## Local escape hatch

`backbond/operations/deploy/scanner-dev-mode-20260721/scanner-ship.sh` does the
same thing from a laptop for when Actions is unavailable. It was broken from
2026-07-28 until 2026-08-22 (it called the helper with 2 arguments after the
helper became 5-argument, and looked for a renamed artifact); it now matches.
