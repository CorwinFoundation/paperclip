---
artifact_type: playbook
owner: cto
persona_audience: founder
phase: 1
canonical_terms_only: true
focus_claim: "This playbook makes the Sequence 20 BackBond Scanner production promotion reproducible, fail-closed, and immediately reversible on srv1749248."
contracts_referenced: ["are-scanner-api-v1@v1.1.0"]
ticket_refs: ["BEAAA-16254", "BEAAA-16253", "BEAAA-16232", "BEAAA-16230", "BEAAA-15400", "BEAAA-15373"]
proof_points_cited:
  - "Candidate commit 46eec2f1887800c2359a1cf75a06657e4eb4d844"
  - "Rollback image backbond-scanner-live@sha256:7c4ff251dd0cc4d41b88e9e148094355985dc4a39e7ac4e93e280171c4101121"
  - "srv1749248 is default-deny egress; images arrive by operator-mediated push"
version: 1.1
prior_version: "docs/deploy/sequence-20-stage-gate-runbook.md"
self_audit_passed: true
escalations_noted:
  - "Production promotion requires exact-build QA APPROVED on BEAAA-16253 and separate Founder sign-off under BEAAA-15373."
  - "The operator must stop if the signed release manifest lacks a repository-qualified candidate digest; this runbook does not invent production identity."
---

# Sequence 20 production deployment runbook

## Decision and authority

Promote only candidate commit `46eec2f1887800c2359a1cf75a06657e4eb4d844`, using its repository-qualified OCI digest from the signed release manifest. This document supersedes the deployment portion of `sequence-20-stage-gate-runbook.md`; it does not change the QA-approved nginx template, acceptance TOML, validator, scoring, rubric, corpus, or API contract.

This is an execution procedure, not promotion authority. Before step 1, the originating issue must contain:

1. QA Engineer comment with the exact word `APPROVED`, naming this runbook commit SHA;
2. Founder production sign-off under BEAAA-15373; and
3. a signed release manifest binding the candidate commit to a repository-qualified digest of the form `ghcr.io/corwinfoundation/are-scanner@sha256:<64 lowercase hex>`.

If any item is absent or names a different SHA, stop. Secrets stay in the root-only `srv1749248` channel. Do not paste secret values into shell history, this runbook, logs, or Paperclip.

## Topology

```text
Internet client
    |
    | HTTPS :443, external acceptance URL from env:BACKBOND_ACCEPTANCE_BASE_URL
    v
srv1749248 host nginx
    - TLS termination: existing production certificate/key configuration
    - live include: /etc/nginx/conf.d/backbond-scanner-edge.conf
    - access log: /var/log/nginx/backbond-scanner-edge.access.log
    - X-Request-ID generated, returned, and logged here
    |
    | HTTP loopback 127.0.0.1:8077
    v
systemd unit backbond-scanner-live.service
    -> container backbond-scanner-live-edge
    -> repository-qualified, digest-pinned BackBond Scanner 0.5.0 image
    -> state /var/lib/are-scanner (host mounted)
```

The production TLS listener and certificate directives already exist on the host and are not replaced by this change. The rendered candidate server block is installed into nginx's live `conf.d` set and listens on the existing TLS listener value supplied by the root-owned environment file. `EDGE_UPSTREAM` must remain `127.0.0.1:8077`; any non-loopback upstream is a stop condition.

## Fixed paths and operator inputs

Run on `srv1749248` as the authorized operator. The root-owned input file is `/etc/backbond/sequence-20-production.env`, mode `0600`, and contains only non-secret values or opaque secret references:

```sh
sudo install -d -o root -g root -m 0755 /etc/backbond /etc/nginx/backbond-backups
sudo test "$(sudo stat -c '%a:%U:%G' /etc/backbond/sequence-20-production.env)" = '600:root:root'
sudo sed -n -E '/^(CANDIDATE_COMMIT|CANDIDATE_IMAGE|CANDIDATE_TARBALL|EDGE_LISTEN|EDGE_UPSTREAM|BACKBOND_ACCEPTANCE_BASE_URL|TOKEN_A_REF)=/p' /etc/backbond/sequence-20-production.env
```

Required bindings:

```text
CANDIDATE_COMMIT=46eec2f1887800c2359a1cf75a06657e4eb4d844
CANDIDATE_IMAGE=ghcr.io/corwinfoundation/are-scanner@sha256:<signed-manifest digest>
CANDIDATE_TARBALL=/var/tmp/backbond-scanner-46eec2f.tar
EDGE_LISTEN=<existing production TLS listen value>
EDGE_UPSTREAM=127.0.0.1:8077
BACKBOND_ACCEPTANCE_BASE_URL=https://<external acceptance hostname>
TOKEN_A_REF=file:/etc/credstore.encrypted#<existing-non-customer-token-a>
```

`CANDIDATE_IMAGE` is not a secret, but it must come verbatim from the signed release manifest. The tarball must have been pulled and signature-verified on an egress-capable operator machine, then pushed inbound to the box. The box must not pull from GHCR.

Load and validate the inputs without printing the token:

```sh
sudo sh -eu <<'SH'
. /etc/backbond/sequence-20-production.env
[ "$CANDIDATE_COMMIT" = '46eec2f1887800c2359a1cf75a06657e4eb4d844' ]
printf '%s\n' "$CANDIDATE_IMAGE" | grep -Eq '^ghcr\.io/corwinfoundation/are-scanner@sha256:[0-9a-f]{64}$'
[ "$EDGE_UPSTREAM" = '127.0.0.1:8077' ]
printf '%s\n' "$BACKBOND_ACCEPTANCE_BASE_URL" | grep -Eq '^https://[^/]+/?$'
printf '%s\n' "$TOKEN_A_REF" | grep -Eq '^(credstore|file|env|vault):'
test -f "$CANDIDATE_TARBALL"
SH
```

## 1. Retain the rollback state

```sh
sudo sh -eu <<'SH'
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="/etc/nginx/backbond-backups/sequence-20-${STAMP}"
install -d -o root -g root -m 0700 "$BACKUP"
cp -a /etc/nginx/conf.d/backbond-scanner-edge.conf "$BACKUP/backbond-scanner-edge.conf"
readlink -f /etc/nginx/conf.d/backbond-scanner-edge.conf > "$BACKUP/live-config-path.txt"
docker inspect --format '{{index .Config.Image}}' backbond-scanner-live-edge > "$BACKUP/prior-image.txt"
printf '%s\n' 'backbond-scanner-live@sha256:7c4ff251dd0cc4d41b88e9e148094355985dc4a39e7ac4e93e280171c4101121' > "$BACKUP/required-rollback-image.txt"
docker image inspect 'backbond-scanner-live@sha256:7c4ff251dd0cc4d41b88e9e148094355985dc4a39e7ac4e93e280171c4101121' >/dev/null
printf '%s\n' "$BACKUP" > /etc/backbond/sequence-20-active-backup
SH
```

Pass: the backup directory exists, contains the prior config, and the exact rollback image is locally inspectable. Otherwise stop before mutation.

## 2. Render and install nginx configuration

The candidate checkout must be the exact remote commit. Rendering substitutes only `EDGE_*`; nginx runtime variables such as `$request_id` remain literal.

```sh
git -C /opt/backbond/are-scanner fetch origin beaaa-16230-seq20-request-id-provisioning
test "$(git -C /opt/backbond/are-scanner rev-parse FETCH_HEAD)" = '46eec2f1887800c2359a1cf75a06657e4eb4d844'
git -C /opt/backbond/are-scanner show 46eec2f1887800c2359a1cf75a06657e4eb4d844:infra/edge/nginx/backbond-scanner-edge.conf.template \
  | sudo tee /etc/nginx/backbond-scanner-edge.conf.template >/dev/null

sudo sh -eu <<'SH'
. /etc/backbond/sequence-20-production.env
export EDGE_LISTEN EDGE_UPSTREAM
envsubst '${EDGE_LISTEN} ${EDGE_UPSTREAM}' \
  < /etc/nginx/backbond-scanner-edge.conf.template \
  > /etc/nginx/backbond-scanner-edge.conf.candidate
grep -F '$request_id' /etc/nginx/backbond-scanner-edge.conf.candidate >/dev/null
grep -F 'add_header X-Request-ID $request_id always;' /etc/nginx/backbond-scanner-edge.conf.candidate >/dev/null
sed -i 's#access_log /dev/stdout edge_json;#access_log /var/log/nginx/backbond-scanner-edge.access.log edge_json;#' \
  /etc/nginx/backbond-scanner-edge.conf.candidate
chown root:root /etc/nginx/backbond-scanner-edge.conf.candidate
chmod 0644 /etc/nginx/backbond-scanner-edge.conf.candidate
ln -sfn /etc/nginx/backbond-scanner-edge.conf.candidate /etc/nginx/conf.d/backbond-scanner-edge.conf
SH
```

## 3. Validate and zero-downtime reload nginx

```sh
sudo nginx -t
sudo systemctl reload nginx
sudo systemctl is-active --quiet nginx
sudo journalctl -u nginx -n 20 --no-pager
```

Pass: `nginx -t` prints both `syntax is ok` and `test is successful`; reload exits `0`; `is-active` exits `0`; the journal contains no reload error. Any failure triggers immediate rollback below.

## 4. Load, verify, and activate the candidate image

The image preserves Scanner `0.5.0`, corpus `0.1.0`, corpus snapshot `sha256:112f3410a75d5f73d840c097054d510a7fcf962950a898452c927ac4ac0d3829`, and seed `42` through the existing root-owned service environment.

```sh
sudo sh -eu <<'SH'
. /etc/backbond/sequence-20-production.env
EXPECTED_DIGEST="${CANDIDATE_IMAGE##*@}"
LOADED_REF="$(/usr/local/sbin/box_deploy load "$CANDIDATE_TARBALL" "$EXPECTED_DIGEST")"
docker image inspect "$CANDIDATE_IMAGE" >/dev/null
sed -i "s#^SCANNER_EDGE_IMAGE=.*#SCANNER_EDGE_IMAGE=$CANDIDATE_IMAGE#" /etc/backbond/scanner-edge.env
systemctl restart backbond-scanner-live.service
systemctl is-active --quiet backbond-scanner-live.service
test "$(docker inspect --format '{{index .Config.Image}}' backbond-scanner-live-edge)" = "$CANDIDATE_IMAGE"
SH

curl --silent --show-error --max-time 30 \
  --output /tmp/sequence-20-health.body \
  --write-out '%{http_code}\n' \
  http://127.0.0.1:8077/scanner/v1/sla \
  | grep -Fx '200'
```

Pass: the loaded digest matches the signed-manifest digest; the service is active on that exact repository-qualified image; the app-tier health endpoint on loopback returns `200` with an opaque JSON body. The health probe intentionally bypasses nginx because the approved edge allowlist default-denies `/scanner/v1/sla`. Delete `/tmp/sequence-20-health.body` after recording only its redacted shape.

## 5. Read-only v0.2 preflight: ordinary 404 and exact log correlation

Resolve `TOKEN_A_REF` inside the approved root-only channel into the environment variable `TOKEN_A` without echoing it. Then:

```sh
sudo sh -eu <<'SH'
. /etc/backbond/sequence-20-production.env
: "${TOKEN_A:?resolve TOKEN_A_REF in the root-only channel before running}"
umask 077
WORK="$(mktemp -d)"
trap 'rm -f "$WORK/headers" "$WORK/body"; rmdir "$WORK"' EXIT
CODE="$(curl --silent --show-error --max-time 30 \
  --header "Authorization: Bearer $TOKEN_A" \
  --dump-header "$WORK/headers" \
  --output "$WORK/body" \
  --write-out '%{http_code}' \
  "$BACKBOND_ACCEPTANCE_BASE_URL/__sequence20_read_only_preflight__")"
[ "$CODE" = 404 ]
REQUEST_ID="$(sed -n 's/^[Xx]-[Rr]equest-[Ii][Dd]:[[:space:]]*\([^[:space:]\r]*\).*/\1/p' "$WORK/headers" | head -1)"
printf '%s\n' "$REQUEST_ID" | grep -Eq '^[0-9A-Za-z._-]{8,128}$'
grep -Eq '^\{"error":"not_found","request_id":"[0-9A-Za-z._-]+"\}$' "$WORK/body"
SERVER_HEADER="$(sed -n 's/^[Ss]erver:[[:space:]]*\([^[:space:]\r]*\).*/\1/p' "$WORK/headers" | head -1)"
[ -z "$SERVER_HEADER" ] || [ "$SERVER_HEADER" = nginx ]
! grep -Eiq '^(Via|X-Powered-By|X-Runtime|X-AspNet-Version|Location):' "$WORK/headers"
MATCHES="$(grep -Fc -- "$REQUEST_ID" /var/log/nginx/backbond-scanner-edge.access.log || true)"
[ "$MATCHES" -eq 1 ]
printf 'PASS status=404 request_id=%s edge_log_matches=1\n' "$REQUEST_ID"
SH
```

Pass: HTTP `404`; exactly one non-empty opaque `X-Request-ID`; opaque `not_found` body; `Server` is absent or exactly the unversioned value `nginx`; no upstream topology header (`Via`, `X-Powered-By`, `X-Runtime`, `X-AspNet-Version`, or `Location`) is present; and exactly one edge access-log line contains that same ID verbatim. The command performs no scan.

## 6. Promotion gates

| Gate | Pass criterion | Failure action |
|---|---|---|
| Identity | commit is `46eec2f…d844`; image is signed-manifest repository digest | rollback |
| Pins | Scanner `0.5.0`; corpus `0.1.0`; snapshot `112f3410…0d3829`; seed `42` read back from root-owned config | rollback |
| Health | app-tier loopback `GET http://127.0.0.1:8077/scanner/v1/sla` returns `200` and opaque JSON; it does not traverse the default-deny edge | rollback |
| Auth posture | preflight sends existing non-customer Tenant A bearer token; no token appears in output/log | rollback and incident review if leaked |
| Redaction | ordinary `404` is opaque; `Server` is absent or bare `nginx`; no version or upstream topology header/body detail | rollback |
| Request ID | header is present and exactly one access-log line carries the identical ID | rollback |
| nginx | config test succeeds; reload succeeds; unit remains active | rollback |

No scan, customer mutation, credential rotation, or tenant creation is authorized by this preflight.

## 7. Immediate rollback on any failed check

Run all commands; do not attempt an in-place repair during the production window.

```sh
sudo sh -eu <<'SH'
BACKUP="$(cat /etc/backbond/sequence-20-active-backup)"
test -f "$BACKUP/backbond-scanner-edge.conf"
cp -a "$BACKUP/backbond-scanner-edge.conf" /etc/nginx/backbond-scanner-edge.conf.rollback
ln -sfn /etc/nginx/backbond-scanner-edge.conf.rollback /etc/nginx/conf.d/backbond-scanner-edge.conf
nginx -t
systemctl reload nginx
systemctl is-active --quiet nginx

ROLLBACK_IMAGE='backbond-scanner-live@sha256:7c4ff251dd0cc4d41b88e9e148094355985dc4a39e7ac4e93e280171c4101121'
docker image inspect "$ROLLBACK_IMAGE" >/dev/null
sed -i "s#^SCANNER_EDGE_IMAGE=.*#SCANNER_EDGE_IMAGE=$ROLLBACK_IMAGE#" /etc/backbond/scanner-edge.env
systemctl restart backbond-scanner-live.service
systemctl is-active --quiet backbond-scanner-live.service
test "$(docker inspect --format '{{index .Config.Image}}' backbond-scanner-live-edge)" = "$ROLLBACK_IMAGE"
SH

curl --silent --show-error --max-time 30 \
  --output /tmp/sequence-20-rollback-health.body \
  --write-out '%{http_code}\n' \
  http://127.0.0.1:8077/scanner/v1/sla \
  | grep -Fx '200'
sudo nginx -t
sudo systemctl is-active --quiet nginx backbond-scanner-live.service
```

Rollback passes only when the prior nginx config is live, nginx validates and reloads, the exact retained rollback image is running, and its app-tier loopback health endpoint returns `200`. If rollback itself fails, page the Founder/operator immediately and keep design-partner access closed.

## Redacted completion receipt

Attach only: operator role, UTC window, candidate commit, repository-qualified image digest, nginx backup path, `nginx -t` outcome, service/image identity, health status, preflight status, opaque request ID, exact log-match count, and final `PASS` or `ROLLED BACK`. Never attach bearer tokens, secret values, environment-file contents, proprietary prompts, or request bodies.

## CTO pre-submission self-audit

1. One thing: executable, reversible production promotion of the QA-reviewed Sequence 20 candidate.
2. Audience/action: Founder-designated operator copies the commands on `srv1749248` only after both gates exist.
3. Supreme Law: evidence completeness, pinned identity, and fail-closed behavior are preserved.
4. No score is reported; score-output version fields are not applicable.
5. No scoring/corpus/rubric change; N=5 calibration is not applicable.
6. Rollback restores both prior nginx configuration and the exact retained image, then rechecks health.
7. No LLM judge is introduced.
8. Canonical terminology check passed.
9. Carrier Rule check passed; carriers are not discussed.
10. Likely critique addressed: no digest is guessed; the signed manifest is mandatory and the run aborts without it.
