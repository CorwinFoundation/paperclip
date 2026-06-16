---
artifact_type: implementation_closeout
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-5553 re-confirms the ghcr.io/ifa/are-connector:0.1.0 image: fresh build digest captured, static-attestation pass clean, 10/10 envelopes accepted by Portal v1 on the canonical run, and N=5 spread=0 on are_total/band/confidence/envelopes across five independent Docker-image runs."
contracts_referenced: ["are-scanner-api-v1@v1.0", "connector-portal-api-v0.1", "scanner-implementation-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-5553", "BEAAA-5551", "BEAAA-4721", "BEAAA-4748"]
proof_points_cited:
  - "manifest list sha256:109ede9ac225ff619392028147fde6a593580e4ccb349cd0233182bf5f41ee77"
  - "image manifest sha256:cf587145181abd169ac90f159ef92b318ff610da37e634cd87f63309fcf91617"
  - "image config sha256:ac70a1e2c3a899788842083f89231921650e1a9dd2f44150ccefd284785d877e"
  - "canonical scan_id da077202-fe34-4bdb-ad2f-8d80be646384, connector_id a6661615-c55a-4824-82cd-3bb5b44b4175"
  - "N=5 are_total={64}, band={C}, confidence={0.72}, envelopes_stored={10} — spread=0 on every dimension"
  - "static-attestation: 0 yaml/yml files, 0 occurrences of HaluEval/BBQ/TruthfulQA/DoNotAnswer/AdvBench/JailbreakBench/rubric/scoring/pillar_score across installed connector code"
  - "audit_log 30 rows: invite_created=1, consent_accepted=1, endpoint_configured=1, connector_enrolled=1, scan_triggered=1, task_issued=10, envelope_ingested=10, score_produced=1, scan_diff_skipped=1, report_rendered=3"
  - "sample envelope: redacted_excerpts hash-only, signature_chain populated (portal counter-signature), endpoint URL+auth method hashed"
version: 0.1
prior_version: BEAAA-4748 (2026-06-12)
self_audit_passed: true
escalations_noted:
  - "Docker daemon access required `sg docker -c ...` because the agent uid is not in the docker group GID by default. Documented for the operator runbook; not a connector defect."
  - "Portal v1 admin endpoints require PORTAL_ADMIN_TOKEN env var before any /admin/* or /scans POST will accept the request. README does not currently call this out for the smoke recipe; suggest a follow-on doc-only PR on portal_v1 (not blocking)."
  - "Carries forward BEAAA-4748's `--network=none` README/Dockerfile mismatch as still-unresolved hardening item; not a v0.1.0 image acceptance blocker."
---

# BEAAA-5553 — Docker image verification: ghcr.io/ifa/are-connector:0.1.0

## BLUF
Re-verification of the connector image for BEAAA-5551 first-DP deployment readiness is complete. Fresh build digest `sha256:109ede9ac225ff619392028147fde6a593580e4ccb349cd0233182bf5f41ee77` captured; static-attestation pass clean (no probe text, no rubric YAML, no scoring code embedded); canonical Docker enroll + 10-task scan ingested 10/10 signed envelopes on Portal v1 with valid signature chains; N=5 spread on the Docker run path is 0 across `are_total`, `band`, `confidence`, and `envelopes_stored`. Image is acceptable for first-DP invite per the contract pins recorded below.

## Acceptance criteria — disposition table

| AC | Outcome |
|---|---|
| Image digest captured + posted | **Pass.** manifest list `sha256:109ede9ac225ff619392028147fde6a593580e4ccb349cd0233182bf5f41ee77`; image manifest `sha256:cf587145181abd169ac90f159ef92b318ff610da37e634cd87f63309fcf91617`; image config `sha256:ac70a1e2c3a899788842083f89231921650e1a9dd2f44150ccefd284785d877e`. |
| Connector enrolls against Portal v1 staging | **Pass.** `enrolled connector_id=a6661615-c55a-4824-82cd-3bb5b44b4175 portal_version=0.4.0`. |
| 10/10 tasks complete, 10 signed envelopes accepted, scan complete | **Pass.** `envelopes_posted=10`; sqlite `envelopes.COUNT=10`; `scans.status=complete`; 30-row audit log with hash-chained entries (`task_issued=10`, `envelope_ingested=10`, `score_produced=1`). |
| Static-attestation: no probe text, no rubric YAML, no scoring code embedded | **Pass.** Image filesystem inspected (extracted from `docker save`). Zero `*.yaml`/`*.yml` files. Zero occurrences of any probe-corpus fingerprint (HaluEval, BBQ, TruthfulQA, DoNotAnswer, AdvBench, JailbreakBench/JBB) and zero occurrences of scoring identifiers (`rubric`, `pillar_score`, `aggregate_score`, `are_total`/`ari_total`, `scoring`, `judge`, `corpus`, `probe`, `score`) in the installed connector package at `usr/local/lib/python3.11/site-packages/are_connector/` or `opt/are-connector/src/`. |
| N=5 spread = 0 on the focused smoke path on the Docker run | **Pass.** Five independent runs (fresh portal sqlite, fresh docker named volume, fresh DP per seed) all returned `are_total=64`, `band=C`, `confidence=0.72`, `envelopes_stored=10`. Set cardinality = 1 per dimension; spread = 0. |

## Environment

- Host: Docker Engine 29.5.3 reached via `sg docker -c …` (`beai-agent` uid is in the `docker` group GID 987).
- Portal: `tools/portal_v1/portal.py serve --host 127.0.0.1 --port 18088 --data-dir /tmp/beaaa5553-smoke/portal-data` with `PORTAL_ADMIN_TOKEN=beaaa5553-admin`. Launched via a small wrapper (`/tmp/beaaa5553-smoke/portal_launch.py`) that imports `portal` under its canonical module name to avoid a `__main__`-vs-module circular import.
- Mock DP agent: stdlib HTTP on `127.0.0.1:18089/agent` (`/tmp/beaaa5553-smoke/mock_agent.py`).
- Container network: `--network=host` so the connector reaches `127.0.0.1` portal and agent.
- Connector state: docker named volumes (`beaaa5553-state` canonical, `beaaa5553-n5-seed-{0..4}` for N=5).

## Step 1 — `docker build`

```
docker build -t ghcr.io/ifa/are-connector:0.1.0 tools/are-connector
```

Result: build succeeded under buildx with attestation. `docker save` + `tar`-extract of the 9 image layers yields a 161 MB rootfs; the connector package is the `are-connector-0.1.0-py3-none-any.whl` installed under `usr/local/lib/python3.11/site-packages/are_connector/` and the source tree under `opt/are-connector/src/are_connector/`. Manifest list digest: `sha256:109ede9ac225ff619392028147fde6a593580e4ccb349cd0233182bf5f41ee77`. Image manifest: `sha256:cf587145181abd169ac90f159ef92b318ff610da37e634cd87f63309fcf91617`. Image config: `sha256:ac70a1e2c3a899788842083f89231921650e1a9dd2f44150ccefd284785d877e`.

Note: this digest differs from the BEAAA-4748 manifest list `sha256:3bab0a2c603…` because buildx attestation manifest hashes vary across builds even when content is identical, and the base-image pin `python:3.11-slim@sha256:6ed5bff4d…` resolves through Debian apt mirrors that may have advanced. The connector wheel digest (`98332a924958c68c2855a4f92837c9703f358f021cad54435e3333504dae96f5`) is reproducible from the source tree.

## Step 2 — Static-attestation pass

Procedure: `docker save` → extract all 9 layer tarballs into `/tmp/beaaa5553-smoke/rootfs/` → grep.

Checks executed:

1. **No rubric / probe-mapping YAML**: `find rootfs -name '*.yaml' -o -name '*.yml'` returns the empty set. (The only YAML-adjacent hit anywhere on rootfs is `usr/share/doc/libzstd1/copyright`, which is a Debian package copyright doc, not a rubric or probe artifact.)
2. **No probe-corpus fingerprints in connector code**: case-insensitive grep for `HaluEval|BBQ|TruthfulQA|DoNotAnswer|AdvBench|JailbreakBench|JBB|hallucination_corpus|probe_text|probe_corpus` across `opt/are-connector/src/` and `usr/local/lib/python3.11/site-packages/are_connector/` returns 0 matches.
3. **No scoring code in connector code**: grep for `pillar_score|aggregate_score|are_total|ari_total|rubric\.scoring|governance_score|bias_score|robustness_score|ops_score|score\(\)|from rubric|import rubric|from scoring|import scoring` across both code locations returns 0 matches.
4. **No broad scoring vocabulary at all**: grep for `rubric|judge|corpus|probe|score` across both code locations returns 0 matches — the connector code does not mention any of these words.

Shipped sources (per `egg-info/SOURCES.txt`): `pyproject.toml`, `src/are_connector/{__init__,canonical,cli,crypto,envelope,http,observability,redaction,runtime,telemetry}.py` and the egg-info metadata. No corpus files, no rubric YAML, no scoring modules.

**Verdict: static-attestation pass clean.**

## Step 3 — `docker run … --enroll` (canonical)

Invite issued via `POST /admin/invites` with admin bearer (`dp_name=BEAAA-5553-DP`). Invite token: `JCeMtQi24jpvhsBykvSsntFd0UGPUy9Q`.

```
docker run --rm --network=host \
  -e PORTAL_BASE_URL=http://127.0.0.1:18088 \
  -e DP_AGENT_ENDPOINT=http://127.0.0.1:18089/agent \
  -e DP_ORG_NAME=BEAAA-5553-DP \
  -v beaaa5553-state:/var/lib/are-connector \
  ghcr.io/ifa/are-connector:0.1.0 \
  --enroll JCeMtQi24jpvhsBykvSsntFd0UGPUy9Q --accept-invite --cert-ref beaaa5553-test-cert
```

Result (stdout):
```
enrolled connector_id=a6661615-c55a-4824-82cd-3bb5b44b4175 portal_version=0.4.0
```

Portal-side: `audit_log` records `invite_created` → `consent_accepted` → `endpoint_configured` → `connector_enrolled` for this DP.

## Step 4 — `docker run … --scan` (canonical, 10 tasks)

Scan triggered via `POST /scans` with admin bearer + `{"dp_id":"7c67d150-5459-4007-88c0-f6c6039c1150"}`:
```
{"scan_id":"da077202-fe34-4bdb-ad2f-8d80be646384","status":"running","task_count":10}
```

```
docker run --rm --network=host \
  -e PORTAL_BASE_URL=http://127.0.0.1:18088 \
  -e DP_AGENT_ENDPOINT=http://127.0.0.1:18089/agent \
  -e DP_ORG_NAME=BEAAA-5553-DP \
  -v beaaa5553-state:/var/lib/are-connector \
  ghcr.io/ifa/are-connector:0.1.0 \
  --scan da077202-fe34-4bdb-ad2f-8d80be646384 --max-tasks 10
```

Result (stdout):
```
scan_id=da077202-fe34-4bdb-ad2f-8d80be646384 envelopes_posted=10
```

Portal-side persistence (sqlite at `/tmp/beaaa5553-smoke/portal-data/portal.sqlite3`):
- `scans.status` = `complete`, `score_json.are_total` = 64, `band` = C, `confidence` = 0.72.
- `envelopes` rows for this scan = 10.
- `audit_log` rows total = 30, including `task_issued=10`, `envelope_ingested=10`, `score_produced=1`, `scan_diff_skipped=1`, `report_rendered=3`.

Sample envelope captured at `tools/are-connector/smoke-evidence/beaaa5553/sample_envelope.json`:
- `envelope_version=0.1`, `connector_version=0.1.0`, `redaction_policy_version=0.1`.
- `redacted_excerpts` carries only `excerpt_hash + length + pii_class=none + category` — no raw text.
- `endpoint_called.url_hash` and `auth_method_hash` only — no plaintext URL or auth header.
- `signature_chain[0].actor=portal`, `alg=ed25519` — portal counter-signature applied.
- `signature` over RFC 8785-style canonical JSON.

## Step 5 — N=5 spread on the Docker run path

Procedure (`/tmp/beaaa5553-smoke/n5_docker_smoke.sh`): for seed ∈ {0..4}, tear down portal, create fresh `/tmp/beaaa5553-smoke/n5/seed-$seed/` portal data directory, restart `portal serve` against it, issue a fresh admin-authed invite for `BEAAA-5553-DP-$seed`, create a fresh docker named volume `beaaa5553-n5-seed-$seed`, run `--enroll`, `POST /scans`, run `--scan --max-tasks 10`, and capture the resulting `scores` row + envelope count from sqlite.

Results (one line per seed, `/tmp/beaaa5553-smoke/n5_results.jsonl`):

| seed | scan_status | envelopes_stored | are_total | band | confidence |
|---|---|---|---|---|---|
| 0 | complete | 10 | 64 | C | 0.72 |
| 1 | complete | 10 | 64 | C | 0.72 |
| 2 | complete | 10 | 64 | C | 0.72 |
| 3 | complete | 10 | 64 | C | 0.72 |
| 4 | complete | 10 | 64 | C | 0.72 |

Set cardinality: `are_total`={64}, `band`={C}, `confidence`={0.72}, `envelopes_stored`={10}. Spread on every dimension = 0. Image satisfies the reproducibility band claim referenced in BEAAA-4721 v0.2 closeout.

## Contract conformance

- **are-scanner-api-v1@v1.0** Hard guarantees: connector never produces a score (no scorer in container per static-attestation pass), evidence trail flows portal → sqlite + on-disk envelope JSON (#2), output pins (`scanner_version=0.4.1`, `corpus_version=0.2.0`, `rubric_version=1.2.0`, `redaction_policy_version=0.1`, `envelope_version=0.1`) preserved in `portal_pins.json` (#5). The Underwriter's existing pricing rule that reads these pins is unaffected.
- **connector-portal-api-v0.1** §5.2 enrollment, §5.3 envelope schema, §5.4 signed redaction policy: enrollment round-trip succeeded with `connector_id` + portal CA-chain + signed redaction policy; envelopes carry the canonical-json signature and portal counter-signature on `signature_chain`.
- **scanner-implementation-supervision-v1@v1.0**: classified as **rule-execution** (no rubric, scoring math, probe corpus, or judge prompts touched). No CTO co-sign required; this artifact is the audit trail. No UW co-sign required (no rubric/scoring delta; calibration N/A by construction).

## Supreme Law self-audit

- No silent score-affecting change? **Confirmed.** This ticket builds and verifies an image whose source tree is already on canon; no rubric/probe/judge/corpus/scoring-math change is made. Calibration N=5 was run anyway as evidence and produced spread=0 across all dimensions, well inside the ±1 band.
- Canonical terms only? **Confirmed.** Backbond / ARE Scanner / Portal v1 / Connector / DP used throughout. The image tag `ghcr.io/ifa/are-connector:0.1.0` is the as-built artifact name and reflects the legacy IFA namespace per `glossary.md`.
- Pins present? **Confirmed.** Image manifest list + manifest + config digests, connector_id, scan_id, all version pins from `portal_pins.json`.
- Evidence trail complete? **Confirmed.** Sample envelope, portal pins, audit log summary, N=5 results JSONL all written under `tools/are-connector/smoke-evidence/beaaa5553/`.
- Carrier Rule? **N/A** (internal audit; no carrier-facing content).

## Disposition

- **BEAAA-5553:** done. Image acceptable for the BEAAA-5551 first-DP invite.
- **BEAAA-4721 §Remaining Blocker:** closed by this artifact (carries forward BEAAA-4748's prior closure with a re-verified digest on today's build).

## Files attached

- `tools/are-connector/smoke-evidence/beaaa5553/BEAAA-5553_docker_verification.md` (this file)
- `tools/are-connector/smoke-evidence/beaaa5553/sample_envelope.json`
- `tools/are-connector/smoke-evidence/beaaa5553/portal_pins.json`
- `tools/are-connector/smoke-evidence/beaaa5553/audit_log_summary.json`
- `tools/are-connector/smoke-evidence/beaaa5553/n5_results.jsonl`
