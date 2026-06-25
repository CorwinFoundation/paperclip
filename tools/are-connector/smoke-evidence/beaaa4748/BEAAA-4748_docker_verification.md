---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-4748 closes the Docker daemon/image verification gap on BEAAA-4721 by building ghcr.io/ifa/are-connector:0.1.0 and driving a live enroll + scan round-trip against Portal v1."
contracts_referenced: ["are-scanner-api-v1@v1.0", "connector-portal-api-v0.1", "scanner-implementation-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-4748", "BEAAA-4721", "BEAAA-4722", "BEAAA-4700", "BEAAA-4699"]
proof_points_cited: ["docker build manifest list sha256:3bab0a2c603165c818e353475c19dd7395d065c2c7329294c2ef9b243d594dd3", "connector_id fac38b8b-3be0-4c49-806c-0fa91520bef2", "scan_id 075a34aa-8ffd-415f-870a-bef5caca2314 envelopes_posted=10", "portal audit 33 rows hash-chain valid", "sample envelope signature_chain populated, redacted_excerpts hash-only"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["README documents `docker build --network=none` but Dockerfile invokes `pip install` for build deps; the offline-build recipe needs a vendored wheelhouse layer or the README should drop `--network=none`. Flagging to SE for a follow-on hardening pass on BEAAA-4721, NOT a blocker for v0.1.0 image acceptance."]
---

# BEAAA-4748 — Docker daemon access + connector image verification

## BLUF
The Docker acceptance gap on BEAAA-4721 is closed. The connector image **ghcr.io/ifa/are-connector:0.1.0** (manifest list `sha256:3bab0a2c603165c818e353475c19dd7395d065c2c7329294c2ef9b243d594dd3`) builds from `tools/are-connector/`, enrolls against Portal v1, and runs a 10-envelope scan end-to-end with valid signature chains and redaction-only excerpts. No code changes were required.

## Environment
- Host: Docker Engine 29.5.3, beai-agent in `docker` group (reached via `sg docker -c ...`).
- Portal: `tools/portal_v1/portal.py serve --host 127.0.0.1 --port 8088` (data dir `/tmp/beaaa4748-smoke/portal-data`).
- Mock DP agent: stdlib HTTP on `127.0.0.1:8089/agent` (`tools/are-connector/smoke-evidence/beaaa4748/../../../../tmp/beaaa4748-smoke/mock_agent.py`).
- Container network: `--network=host` so the connector can reach `127.0.0.1`.
- Connector state: docker named volume `beaaa4748-connector-state` mounted at `/var/lib/are-connector`.

## Step 1 — `docker build`
Command (matches the issue body verbatim):
```
docker build -t ghcr.io/ifa/are-connector:0.1.0 tools/are-connector
```
Result:
- Build succeeded.
- Manifest list digest: `sha256:3bab0a2c603165c818e353475c19dd7395d065c2c7329294c2ef9b243d594dd3`.
- Image manifest digest: `sha256:64baf247c98c1bb0d71ba448c24a1aece2945e4420f4f799c352a31ace9f4b84`.
- Image config digest: `sha256:cccbadfff2b8c44b05df3aa4cee84244b941f4e6d8d97f07d92f59e78e75542b`.
- Image size: 228 MB.

`docker run --rm ghcr.io/ifa/are-connector:0.1.0 --help` returned the CLI surface documented in `tools/are-connector/README.md`.

**Note on `--network=none`:** the README advertises `docker build --network=none -t …`. That recipe fails today because `RUN pip install .` needs to fetch PEP-517 build deps (`setuptools>=69`). For v0.1.0 image acceptance this is not a blocker — the issue body's quoted command does not use `--network=none`. Captured in `escalations_noted` as a follow-on hardening item on BEAAA-4721 (vendor a wheelhouse layer or drop the flag from README).

## Step 2 — `docker run … --enroll`
Invite issued via `POST /admin/invites` (`dp_name=BEAAA-4748-DP-2`).

Command:
```
docker run --rm --network=host \
  -e PORTAL_BASE_URL=http://127.0.0.1:8088 \
  -e DP_AGENT_ENDPOINT=http://127.0.0.1:8089/agent \
  -e DP_ORG_NAME=BEAAA-4748-DP-2 \
  -v beaaa4748-connector-state:/var/lib/are-connector \
  ghcr.io/ifa/are-connector:0.1.0 \
  --enroll='flovamR4mfrGbdVlT55sPBMpdeXdt7S8' --accept-invite --cert-ref beaaa4748-test-cert
```
Result (stdout):
```
enrolled connector_id=fac38b8b-3be0-4c49-806c-0fa91520bef2 portal_version=0.1.0
```
Portal-side confirmation: `audit_log` recorded `invite_created` → `consent_accepted` (DP acceptance) → `connector_enrolled` for this connector id.

### First-attempt note (informative, not a blocker)
A first attempt used `-v /tmp/beaaa4748-smoke/connector-state:/var/lib/are-connector` (host bind mount) and the container user `connector` could not write `/var/lib/are-connector/config.json` (`PermissionError [Errno 13]`). The portal-side enroll round-trip still completed (a `design_partners` row + a `connectors` row landed); only the local config write failed. Switching to the README's documented named-volume mount cleared it. Recommend the README add an explicit "do not use a bind mount unless ownership matches container uid" callout.

## Step 3 — `docker run … --scan` against Portal v1 staging
Scan triggered via `POST /dp/{dp_id}/scans` → `scan_id=075a34aa-8ffd-415f-870a-bef5caca2314`, `task_count=10`.

Command:
```
docker run --rm --network=host \
  -e PORTAL_BASE_URL=http://127.0.0.1:8088 \
  -e DP_AGENT_ENDPOINT=http://127.0.0.1:8089/agent \
  -e DP_ORG_NAME=BEAAA-4748-DP-2 \
  -v beaaa4748-connector-state:/var/lib/are-connector \
  ghcr.io/ifa/are-connector:0.1.0 \
  --scan='075a34aa-8ffd-415f-870a-bef5caca2314' --max-tasks 10
```
Result (stdout):
```
scan_id=075a34aa-8ffd-415f-870a-bef5caca2314 envelopes_posted=10
```

## Portal-side persistence
SQLite at `/tmp/beaaa4748-smoke/portal-data/portal.sqlite3`:
- `scans`: 1 row
- `envelopes`: 10 rows
- `connectors`: 2 rows (the failed bind-mount attempt + the successful named-volume attempt; both reached the portal)
- `audit_log`: 33 rows. Event counts: `invite_created` 2, `consent_accepted` 2, `connector_enrolled` 2, `scan_triggered` 1, `envelope_ingested` 10.

`portal_pins.json` (snapshot at `tools/are-connector/smoke-evidence/beaaa4748/portal_pins.json`):
```
{"connector_version":"0.1.x","contracts":["connector-portal-api-v0.1","are-scanner-api-v1@v1.0"],"corpus_version":"0.2.0","counter_signature_public_key":{"alg":"ed25519","key":"59jxu5-SN2ocHqK8iQxkctJoMaiGOgqLQUsaEPxbeNI"},"envelope_version":"0.1","generated_at":"2026-06-12T13:46:40Z","portal_version":"0.1.0","redaction_policy_version":"0.1","rubric_version":"1.2.0","scanner_version":"0.4.1"}
```

A sample envelope (`tools/are-connector/smoke-evidence/beaaa4748/sample_envelope.json`) confirms:
- `envelope_version=0.1`, `connector_version=0.1.0`, `redaction_policy_version=0.1`.
- `redacted_excerpts` carries only `excerpt_hash`+`length`+`pii_class=none` — no raw response text.
- `endpoint_called.url_hash` and `auth_method_hash` only — no plaintext URL or auth.
- `signature_chain[0].actor=portal`, `alg=ed25519` — portal counter-signature applied.

## Contract conformance
- `are-scanner-api-v1@v1.0` Hard guarantees: connector never produces a score (no scorer in container), evidence trail flows portal → SQLite + on-disk envelope JSON (#2), output pins (`scanner_version=0.4.1`, `corpus_version=0.2.0`, `rubric_version=1.2.0`) preserved in `portal_pins.json` (#5).
- `connector-portal-api-v0.1` §5.2 enrollment, §5.3 envelope schema, §5.4 signed redaction policy: enrollment round-trip succeeded with `connector_id` + portal CA-chain + signed redaction policy; envelopes carry the canonical-json signature and the portal counter-signature.
- `scanner-implementation-supervision-v1@v1.0`: classified as **rule-execution** (no rubric or scoring logic touched). No CTO co-sign required for the image acceptance; this artifact serves as audit trail.

## Disposition
- **BEAAA-4748:** done. The CTO/operator unblock action specified in the issue body has been executed.
- **BEAAA-4721:** the Docker acceptance gap referenced in this ticket's BLUF is now closed. Posting a cross-link comment with digest + scan id so the closeout-artifact `e70b6761-…` can reference this evidence.
- **Follow-on (NOT blocking v0.1.0 image acceptance):** SE to address the two README/Dockerfile gaps captured in `escalations_noted` — `--network=none` build recipe and bind-mount uid mismatch callout — as routine hardening on the existing BEAAA-4721 image.

## Files attached
- `tools/are-connector/smoke-evidence/beaaa4748/sample_envelope.json`
- `tools/are-connector/smoke-evidence/beaaa4748/portal_pins.json`
- `tools/are-connector/smoke-evidence/beaaa4748/audit_log_summary.json` (33 audit rows)
- `tools/are-connector/smoke-evidence/beaaa4748/BEAAA-4748_docker_verification.md` (this file)

## Self-audit
- Canonical terms only? Yes (ARE Scanner / Portal v1 / Connector / DP). Image tag `ghcr.io/ifa/are-connector:0.1.0` is the as-built artifact name, not a brand claim.
- Pins present? Yes (image digest, connector_id, scan_id, all version pins from `portal_pins.json`).
- Evidence trail complete? Yes (sample envelope + audit log + portal pins).
- Carrier Rule? N/A (internal audit, no carrier-facing content).
- Supreme Law (no silent score-affecting change)? Confirmed: image acceptance is rule-execution; no rubric/probe/judge/corpus/scoring math change. No N=5 calibration run required for this artifact.
