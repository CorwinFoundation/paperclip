# First production container cutover

This procedure migrates `scanner.backbond.ai` from the legacy
`are-scanner-api.service` process on `127.0.0.1:8443` to the container on
`127.0.0.1:8080`. The legacy service remains running throughout the cutover
and initial soak. Rollback changes nginx back to `:8443`; it does not rebuild
or restart the legacy application.

## Safety model

- The candidate container is reachable only on loopback.
- The Dockerfile listens on container port `8077`; `box_deploy` publishes it
  as host `127.0.0.1:8080`.
- The signed release manifest binds the registry digest, exact image archive
  SHA-256, git revision, approver, and Actions run.
- Candidate acceptance runs directly against `:8080` before nginx changes.
- nginx is validated with `nginx -t` before reload.
- The old nginx config is retained at
  `/etc/are-scanner/nginx-legacy-pre-container.conf`.
- The legacy service is not stopped by the deploy scripts.
- The acceptance tenant must never resolve to `vapi`.

## One-time production preparation

Install the reviewed scripts:

```sh
install -o root -g root -m 0755 scripts/deploy/box_deploy.sh /usr/local/sbin/box_deploy
install -d -o root -g root -m 0755 /root/deploy
install -o root -g root -m 0755 scripts/deploy/operator_push.sh /root/deploy/operator_push.sh
install -o root -g root -m 0755 scripts/deploy/acceptance_checks.sh /root/deploy/acceptance_checks.sh
install -o root -g root -m 0755 scripts/deploy/post_deploy_record.py /root/deploy/post_deploy_record.py
```

Create `/etc/are-scanner/deploy.env` as `root:root`, mode `0600`:

```sh
ARE_CONTAINER_NAME=are-scanner-edge
ARE_CONTAINER_ENGINE=docker
ARE_BIND=127.0.0.1:8080
ARE_APP_PORT=8077
ARE_CONTAINER_USER=0:0
ARE_STATE_DIR=/var/lib/are-scanner
ARE_SECRETS_ENV=/etc/are-scanner/secrets.env
ARE_MANIFEST_PUBKEY=/etc/are-scanner/cd-manifest-signing-pubkey.pem
ARE_LEGACY_SERVICE=are-scanner-api.service
ARE_NGINX_SITE=/etc/nginx/sites-available/scanner.backbond.ai
ARE_NGINX_BACKUP=/etc/are-scanner/nginx-legacy-pre-container.conf
CANDIDATE_BASE_URL=http://127.0.0.1:8080
PUBLIC_BASE_URL=https://127.0.0.1
SCAN_LOG_DB=/var/lib/are-scanner/scan_log.sqlite3
STAGING_TENANT_ID=<non-vapi-staging-tenant>
STAGING_AGENT_ID=<staging-agent>
ARE_STAGING_ADMIN_TOKEN=<admin-token>
```

`ARE_CONTAINER_USER=0:0` preserves the current root-owned state-directory
contract for the first migration. A later hardening change can migrate state
ownership and remove this override after it is tested independently.

Create `/etc/are-scanner/secrets.env` as `root:root`, mode `0600`. Rotate
the existing provider and signing credentials before placing their replacements
there. Never paste secret values into a ticket, terminal transcript, commit, or
Actions log.

Install the public half of the deployment-manifest signing key at
`/etc/are-scanner/cd-manifest-signing-pubkey.pem`, mode `0644`. The private
half exists only as the GitHub Actions secret
`CD_MANIFEST_SIGNING_KEY_PEM`.

Before running the deploy, require all of these checks:

```sh
systemctl is-active are-scanner-api.service
curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1/scanner/v1/sla
nginx -t
test ! -e /etc/are-scanner/nginx-legacy-pre-container.conf
test -s /etc/are-scanner/secrets.env
test -s /etc/are-scanner/cd-manifest-signing-pubkey.pem
```

## Release artifacts

After the approval issue is approved, download both artifacts from the same
Actions run:

- `image-payload-<sha>`: `are-scanner-<sha>.tar` and its SHA-256 file.
- `release-manifest-<sha>`: the manifest, signature, and an informational
  public-key copy. The copy never replaces the box's preinstalled trust anchor.

Read `sha`, `ref`, `digest`, `tar_sha256`, `approver`, and `run_url`
from the signed manifest. Push the tar, manifest, and signature onto the box
through the controlled inbound SSH channel.

## Reversible cutover

Run the operator orchestrator with the explicit first-cutover flag:

```sh
/root/deploy/operator_push.sh \
  --first-cutover \
  --tarball /tmp/are-scanner-<sha>.tar \
  --tar-sha256 <tar_sha256> \
  --digest <digest> \
  --ref <ref> \
  --sha <sha> \
  --approver <approver> \
  --run-url <run_url> \
  --manifest /tmp/release-manifest.json \
  --manifest-sig /tmp/release-manifest.json.sig
```

The script performs this sequence:

1. Verify the signed manifest and all operator-supplied release fields.
2. Verify the archive SHA-256, loaded image tag, and git revision label.
3. Start the candidate on `127.0.0.1:8080`.
4. Run health, auth, non-`vapi` smoke-scan, persistence-pin, and customer-envelope checks.
5. Save the current nginx config.
6. Switch nginx to `:8080`, validate, and reload.
7. Verify the public loopback route.
8. Leave `are-scanner-api.service` active on `:8443`.

If any step fails after candidate activation, the script restores legacy nginx
when necessary, removes the failed candidate, and verifies legacy health.

## Immediate manual rollback

During the soak window, restore legacy ingress with:

```sh
/usr/local/sbin/box_deploy rollback-legacy-ingress
curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1/scanner/v1/sla
```

Do not stop or disable `are-scanner-api.service` until the agreed soak period
has completed and scan persistence, customer envelopes, provider calls, and
host egress policy have been rechecked.
