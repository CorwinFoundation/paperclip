---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-5742 closes the BEAAA-5740 AC10 Docker smoke and master merge gate: connector 0.1.1 image built reproducibly, oversized_response cap verified in-container, source commit 38c472bd2f5c941c86f2dd3cc8d40b7a5727c575 fast-forward-merged to canonical master, and merge-base ancestry confirmed."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-5742", "BEAAA-5740", "BEAAA-5741", "BEAAA-5553", "BEAAA-5675", "BEAAA-5679"]
proof_points_cited: ["sg docker -c 'docker build ... ghcr.io/ifa/are-connector:0.1.1'", "are_connector-0.1.1-py3-none-any.whl sha256=5bc066673530ea03e0d686c0764b551f7f87be4e7aa77e5e5c0b015ccc13e42b (two passes)", "AC10 in-container smoke 5/5 PASS (1 MiB cap default; under, at, over, configurable, default-value)", "git push 1343c92..38c472b master", "git merge-base --is-ancestor 38c472bd2f5c941c86f2dd3cc8d40b7a5727c575 origin/master -> exit 0"]
version: 0.1
prior_version: "tools/are-connector/smoke-evidence/beaaa5740/docker_verification.md@0.1"
self_audit_passed: true
escalations_noted: []
scanner_version: "0.4.2"
rubric_version: "1.2.0"
contract_version: "are-scanner-api-v1@v1.3.0"
classification: "rule-execution under scanner-implementation-supervision-v1@v1.1"
supervision_contract: "scanner-implementation-supervision-v1@v1.1"
cto_cosign_ref: "self (CTO own-ticket discipline gate)"
underwriter_cosign_required: false
underwriter_cosign_rationale: "Δ=0 by-construction: no rubric, scoring math, judge model, judge prompt, or are-scanner-api-v1 output shape changed. Connector dep pins + HTTP response cap are runtime-determinism + boundary-hardening only."
founder_cosign_required: false
golden_set_run: "n/a — connector-only change, does not touch rubric/, harness/scoring.py, or judge fixtures. AC8 Δ=0 by-construction proof recorded under BEAAA-5740 closeout."
merged_sha_on_canon_master_at_close: "38c472bd2f5c941c86f2dd3cc8d40b7a5727c575"
---

# BEAAA-5742 Docker Smoke and Master Merge Gate — Receipt

BLUF: AC10 cleared on the 0.1.1 connector image, source commit `38c472bd2f5c941c86f2dd3cc8d40b7a5727c575` fast-forward-merged into canonical `origin/master`, and merge-base ancestry verified. Parent BEAAA-5740 unblocked on the Docker + canon-merge axis.

## Classification

Rule-execution under `scanner-implementation-supervision-v1@v1.1`. The change in scope is the existing BEAAA-5740 source remediation (connector deterministic deps + response-size cap); this ticket only carries out the AC10 Docker smoke and the canon-master merge gate that the BEAAA-5740 heartbeat environment could not perform. No rubric, scoring math, judge model, judge prompt, or `are-scanner-api-v1` output shape changed. Underwriter co-sign N/A. Founder co-sign N/A.

## Inputs

- Source commit: `38c472bd2f5c941c86f2dd3cc8d40b7a5727c575` on `origin/beaaa-5740-track-c-remediations` at this run start.
- Foundation ancestor: `1343c921694f966846f6b8f6ba97c05226bf525a` (BEAAA-5741 foundation import — already on canon master).
- Working tree: `/tmp/are-scanner-beaaa5741-foundation` (canon worktree, foundation branch + remediation tip).

## Environment unblock notes

The BEAAA-5740 heartbeat closeout (`tools/are-connector/smoke-evidence/beaaa5740/docker_verification.md@0.1`) records two blockers: (1) Docker socket permission denied, (2) root filesystem at 100%. This heartbeat resolved both:

1. **Docker reachability.** The agent user (`beai-agent`) is a member of supplementary group `docker` (gid 987). Direct `docker` invocations against `unix:///var/run/docker.sock` return permission denied because the login shell does not promote the supplementary group; the canonical workaround is `sg docker -c "<command>"`, which matches the BEAAA-5553 N=5 smoke pattern. `sg docker -c "docker info"` returns server info successfully under this run.
2. **Disk pressure.** Cleared obsolete `/tmp` artifacts (old worktrees, BEAAA-5439 / BEAAA-5553 / BEAAA-5440 / BEAAA-5271 / BEAAA-4721 / BEAAA-5678 caches; closed BEAAA-2942 / BEAAA-3291 / BEAAA-3296 run artifacts). Recovered from `47 MiB` free to `1.6 GiB` free on `/dev/vda1`. The current BEAAA-5740 venv (`/tmp/beaaa5740-venv`) and foundation worktree (`/tmp/are-scanner-beaaa5741-foundation`) were preserved.

Neither workaround changes the source contract; the unblocking is operational and applies only to this run.

## Build verification (AC3 + AC4 evidence on Docker image)

Two clean builds from the same source tree at commit `38c472b`, both producing the same connector wheel sha256:

```
$ sg docker -c "docker build -t ghcr.io/ifa/are-connector:0.1.1 ."
...
#11 24.90   Created wheel for are-connector: filename=are_connector-0.1.1-py3-none-any.whl size=15776 sha256=5bc066673530ea03e0d686c0764b551f7f87be4e7aa77e5e5c0b015ccc13e42b
#12 exporting manifest list sha256:1bd98cf3793ccdaa81e892f4e4a4854fd2686c0455b344237d28cb1756b6bc61
#12 exporting config sha256:1fdac4f9f67fd7aa2fe7073cc3a7d484dabeef3155596a4228680070f8525ac9

$ sg docker -c "docker build --no-cache -t ghcr.io/ifa/are-connector:0.1.1-pass2 ."
...
#11 31.44   Created wheel for are-connector: filename=are_connector-0.1.1-py3-none-any.whl size=15776 sha256=5bc066673530ea03e0d686c0764b551f7f87be4e7aa77e5e5c0b015ccc13e42b
```

Wheel sha256 byte-stable across both builds: `5bc066673530ea03e0d686c0764b551f7f87be4e7aa77e5e5c0b015ccc13e42b`. This matches the BEAAA-5740 source-side venv replay receipt, confirming the SOURCE_DATE_EPOCH=1735689600 lock holds end-to-end through the Docker layer.

Image identifiers (pass 1):

- Manifest list digest (multi-platform): `sha256:1bd98cf3793ccdaa81e892f4e4a4854fd2686c0455b344237d28cb1756b6bc61`
- Image config digest: `sha256:1fdac4f9f67fd7aa2fe7073cc3a7d484dabeef3155596a4228680070f8525ac9`
- Local image ID: `1fdac4f9f67f`

Note: the manifest list / config digests do not byte-replay between independent buildkit runs because the attestation layer (`exporting attestation manifest`) carries build-tooling metadata. The substantive determinism proof is the connector wheel sha (above) and the runtime dependency manifest (below).

In-container pip freeze (runtime image), confirming hashed constraint install landed:

```
$ sg docker -c "docker run --rm --entrypoint python3 ghcr.io/ifa/are-connector:0.1.1 -m pip freeze"
are-connector @ file:///opt/are-connector
certifi==2026.5.20
cffi==2.0.0
charset-normalizer==3.4.7
cryptography==45.0.7
idna==3.18
pycparser==3.0
PyNaCl==1.6.2
requests==2.34.2
urllib3==2.7.0
```

Connector package version reported by both `are_connector.__version__` and `importlib.metadata.version("are-connector")` = `0.1.1`.

## AC10 smoke — oversized_response on Docker image

Mock oversized agent (`/tmp/beaaa5742-smoke/mock_oversized_agent.py`) bound on `127.0.0.1:18090`; smoke driver (`/tmp/beaaa5742-smoke/in_container_smoke.py`) runs inside the 0.1.1 container with `--network host`, exercising `are_connector.http.call_agent` against the mock at varying response sizes.

Command:

```
$ sg docker -c "docker run --rm --network=host \
    -v /tmp/beaaa5742-smoke:/smoke:ro \
    -e AGENT_URL=http://127.0.0.1:18090/agent \
    --entrypoint python3 \
    ghcr.io/ifa/are-connector:0.1.1 \
    /smoke/in_container_smoke.py"
```

Result: `5/5 PASS`.

| # | Test | Status | Detail |
| - | ---- | ------ | ------ |
| 1 | `default_cap_is_1MiB` | PASS | `max_response_bytes()` returns `1048576` when `ARE_CONNECTOR_MAX_RESPONSE_BYTES` unset. |
| 2 | `under_cap_passes` | PASS | 64 KiB JSON response returned intact; `response` field length matches expected payload. |
| 3 | `at_cap_edge_passes` | PASS | Exactly-1-MiB response passed through `call_agent` without rejection. |
| 4 | `over_cap_rejected_no_body_leak` | PASS | 2 MiB response raised `OversizedResponseError observed=1114112 cap=1048576`; error message carries only metadata (`max_response_bytes=1048576`, `observed_byte_count=1114112`) — no response-body content leaked. |
| 5 | `configurable_cap_honored` | PASS | `ARE_CONNECTOR_MAX_RESPONSE_BYTES=32768` rejects a 64 KiB body with `e.max_response_bytes == 32768`; env reset cleanly afterwards. |

AC mapping:

- AC6 (boundary cap at HTTP layer before JSON parsing) — exercised by tests 2/3/4 (cap enforced via `_read_capped_response_body` streamed reader; raises before the JSON parse step).
- AC7 (signed failure-envelope class `oversized_response`, observed-byte-count, no body content) — `OversizedResponseError` carries `observed_byte_count` + `max_response_bytes` and zero body content; this is the in-process raise path that callers map to the failure envelope per `runtime.py` per-task contract. Verified by test 4 inspecting the error string for `xxxx` body content (none present).
- AC8 (defense-in-depth fail on `extract_response_text` / `redacted_excerpts`) — already covered by `tools/are-connector/tests/test_response_cap.py` unit tests (17 pass per BEAAA-5740 source receipt); the in-container smoke confirms the HTTP boundary check fires before those callers can run.
- AC9 (under-cap, at-cap, over-cap, configurable, multi-MiB body memory bound) — tests 2/3/4/5 plus the 2 MiB synthetic body driven on the mock confirm memory stays bounded (`iter_content(chunk_size=65536)` early-aborts).
- AC10 (BEAAA-5553-style smoke on Docker image) — this section.

## Canon master merge

Pre-push state:

```
$ git merge-base --is-ancestor 38c472bd2f5c941c86f2dd3cc8d40b7a5727c575 origin/master
$ echo $?
1
$ git log --oneline origin/master -3
1343c92 BEAAA-5741 foundation import: tools/are-connector v0.1.0 (+ BEAAA-5740 P1-C2)
3211c26 BEAAA-5552 commit N+2: N=5 calibration delta artifacts (Δ vs master = 0)
5b67768 BEAAA-5552 commit N+1: reject 3xx outright on customer_runner
```

Push (fast-forward):

```
$ git push "$PAT_URL" 38c472bd2f5c941c86f2dd3cc8d40b7a5727c575:refs/heads/master
To https://github.com/CorwinFoundation/are-scanner.git
   1343c92..38c472b  38c472bd2f5c941c86f2dd3cc8d40b7a5727c575 -> master
```

Post-push state:

```
$ git fetch "$PAT_URL" master:refs/remotes/origin/master
   1343c92..38c472b  master     -> origin/master

$ git merge-base --is-ancestor 38c472bd2f5c941c86f2dd3cc8d40b7a5727c575 origin/master
$ echo $?
0

$ git log --oneline origin/master -3
38c472b BEAAA-5740 connector deps and response cap gates
1343c92 BEAAA-5741 foundation import: tools/are-connector v0.1.0 (+ BEAAA-5740 P1-C2)
3211c26 BEAAA-5552 commit N+2: N=5 calibration delta artifacts (Δ vs master = 0)
```

Canonical `origin/master` tip is now `38c472b`. Supervision-v1.1 guarantee #5 (ancestry attestation) satisfied. Guarantee #7 (merged-sha quote) satisfied with `merged_sha_on_canon_master_at_close = 38c472bd2f5c941c86f2dd3cc8d40b7a5727c575`.

## Calibration (Δ=0 by-construction)

No rubric point, scoring math, judge model, judge prompt, judge fixture, or `are-scanner-api-v1` output shape changed by `1343c92..38c472b`. Modification set is confined to `tools/are-connector/{Dockerfile,pyproject.toml,constraints.txt,src/are_connector/__init__.py,src/are_connector/http.py,tests/test_response_cap.py,smoke-evidence/beaaa5740/}`. None of those paths feed the rubric or scoring pipeline. AC8 Δ=0 attestation already recorded under BEAAA-5740 source-side closeout.

## What I did not have to do

- I did not rebuild or rerun the rubric / scoring pipeline. No score-touching code path changed.
- I did not amend, rebase, or rewrite the BEAAA-5740 remediation commit. The push is strictly a fast-forward of the existing branch tip into master.
- I did not modify the failure-envelope schema or its caller; AC7 evidence is the existing `OversizedResponseError` raise path, which the runtime already maps to the signed envelope class `oversized_response` per BEAAA-5740 source receipt.
- I did not introduce new tooling or contract changes; the unblock is operational (`sg docker`, disk reclaim) and applies only to this run.

## Self-audit (CTO Supreme Law, scanner-implementation-supervision-v1@v1.1)

| Check | Outcome |
| ----- | ------- |
| Classification declared and accurate (rule-execution; no rubric/scoring/contract delta) | ✅ |
| Underwriter co-sign requirement evaluated and recorded (N/A: Δ=0 by-construction; modification set excludes rubric and scoring surfaces) | ✅ |
| Founder co-sign requirement evaluated (N/A: no BackBond Score wire-change; no canon term redefinition; no carrier-facing or contract change) | ✅ |
| Carrier Rule respected (no carrier-facing language in this receipt or related comments) | ✅ |
| Canonical terms only (Backbond → BackBond Score where score-facing; ARE Scanner; `are-scanner-api-v1`; supervision-v1.1) | ✅ |
| Reproducibility evidence attached (wheel sha replay across two clean builds) | ✅ |
| Ancestry attestation attached (`merge-base --is-ancestor … origin/master` exit 0; merged-sha recorded) | ✅ |
| Evidence trail one directory deep (`tools/are-connector/smoke-evidence/beaaa5742/docker_smoke_receipt.md`) | ✅ |
| Supervision contract version pinned (`scanner-implementation-supervision-v1@v1.1`) | ✅ |
| Prior-version self-cite present (`tools/are-connector/smoke-evidence/beaaa5740/docker_verification.md@0.1`) | ✅ |

Self-audit: PASS.
