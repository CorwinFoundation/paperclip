---
artifact_type: test_report
owner: scanner_engineer
persona_audience: internal
canonical_terms_only: true
focus_claim: "BEAAA-23761 R2 pins the passive-demo judge-socket bind source to the same fixed directory the root preflight helper evaluates, and rejects any caller override before the root boundary."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-23761", "BEAAA-23765", "BEAAA-23766", "BEAAA-23759", "BEAAA-23760", "BEAAA-23703", "BEAAA-23667"]
version: 0.2
prior_version: 0.1
self_audit_passed: true
prior_self_audit_passed: false
escalations_noted:
  - "CTO co-sign required before merge because the tamper-resistance boundary is touched"
  - "exact-candidate QA APPROVED required before any shared or production promotion"
  - "duplicate-lane question for the CTO: BEAAA-23760 covers the same defect and its approved candidate f85ec18d never landed on master"
scanner_version: "0.5.0"
corpus_version: "not_read_by_candidate"
probe-set-version: "not_read_by_candidate"
rubric_point_id: "N/A_INFRA_TAMPER_BOUNDARY"
n5_calibration_delta: 0
cto_cosign_ref: "pending"
---

# BEAAA-23761 R2 — pin the judge-socket bind source to the evaluated path

BLUF: on canonical master `8b0b340bf05b10134783d7c569047c0057dacb98`, `demo-real-agent/deploy.sh` still derives its Docker bind source from the caller-controlled `CLAUDE_JUDGE_SOCKET_DIR`, while the root preflight helper evaluates the fixed `/run/backbond-claude-judge/judge.sock`. A `JUDGE_SOCKET: ready` verdict therefore did not prove which directory would be mounted. R2 pins the bind source to the fixed directory and rejects a divergent override before the helper is called, so an override attempt reaches neither root nor Docker.

## What changed since v0.1

v0.1 was withheld by the CTO on BEAAA-23765 and gated CHANGES REQUESTED by QA on BEAAA-23766, for the same boundary mismatch. Its bytes were never committed, so canonical-master ancestry was unprovable (QA finding D1). R2 discards those bytes entirely and rebuilds on current canonical master. `self_audit_passed` applies to this revision only; `prior_self_audit_passed: false` records that v0.1 did not hold up.

The root-evaluated names-only readiness half of this ticket is already on master: BEAAA-23759 landed it in `c06e7ef2`, and the installed helper is `9ecc60538c9904a2e9c1583c7af2ffed9d37f9775fa3888b02313237a53874e2` (tracked at `artifacts/BEAAA-23759/v2/backbond-real-agent-demo-secret-preflight`). R2 therefore changes no helper byte. The bind-source pin is the only part of this ticket's scope still missing from master.

## Classification and security rationale

Rule change under `scanner-implementation-supervision-v1@v1.1`, because it moves the boundary between a caller-supplied value and a root-attested one.

The change removes an input, it does not add access. No group membership, socket owner, socket mode, ACL, sudoers rule, SSH permission, systemd unit, container user, or secret read changes. `git diff` contains no `chown`, `chmod`, `setfacl`, `groupadd`, `usermod`, or `systemctl`. The container still runs `10001:10001` with a read-only bind and the same in-container socket path. Nothing is installed, armed, dispatched, deployed, or restarted by this candidate.

The residual master defect this closes was pre-existing and was not introduced here.

## Candidate bytes

Base: canonical master `8b0b340bf05b10134783d7c569047c0057dacb98`. Branch: `agent/beaaa-23761-bind-pin`.

| File | SHA-256 |
|---|---|
| `demo-real-agent/deploy.sh` | `0816c18f7980dfb933512a328ad5b4888eff9b74ee6ec6f03c0f4bf010505ce0` |
| `demo-real-agent/tests/test_deploy_bind_source_beaaa23761.py` | `06f7e6427e0f88db5d86a20b81edc54c21d6d2646b75f7f8fe0ff37abe0a35fb` |

Diff against master is two files: `deploy.sh` +4/−1, and one added test file. No other path is touched.

## Verification

Every test executes the shipped `deploy.sh` under a PATH-isolated harness with recording `sudo` and `docker` stubs, and asserts on the recorded invocations. Nothing asserts on source text. The real root helper is never executed, because executing it would consume the live one-shot deployment grant.

| Test | Proves |
|---|---|
| `test_default_run_binds_the_fixed_judge_socket_directory` | recorded `docker run` argv carries `source=/run/backbond-claude-judge,target=/run/claude-judge,readonly` |
| `test_override_to_another_directory_is_rejected_before_root_and_docker` | with a real listening socket in the attacker directory: exit 1, 0 sudo calls, 0 docker calls |
| `test_override_set_but_empty_is_rejected` | the guard uses `${VAR+x}`, so set-but-empty is caught |
| `test_override_equal_to_the_fixed_directory_is_accepted` | the guard rejects divergence, not presence |
| `test_missing_socket_fails_closed_naming_the_inspected_path` | fail-closed message names `/run/backbond-claude-judge/judge.sock`, the path actually inspected (QA finding D3) |
| `test_malformed_socket_status_fails_closed` | two status lines stop the run before Docker |
| `test_helper_failure_fails_closed` | helper non-zero stops the run before Docker |

Mutation results — five mutations, five killed:

| Mutation | Killed by |
|---|---|
| M1 revert to the master shape | both override tests |
| M2 pin kept, guard deleted | both override tests |
| M3 guard uses `${VAR:-}` | set-but-empty test |
| M4 guard moved after the sudo call | override tests, on the `0 sudo calls` assertion |
| M5 pin repointed to another directory | three bind-source tests |

After each mutation `deploy.sh` was restored and reconfirmed byte-identical to `0816c18f7980dfb933512a328ad5b4888eff9b74ee6ec6f03c0f4bf010505ce0`.

Other checks:

- `bash -n demo-real-agent/deploy.sh` passes.
- N=5: `PYTHONHASHSEED=0..4 python3 -m pytest -q demo-real-agent/tests/test_deploy_bind_source_beaaa23761.py`, each seed `7 passed`.
- Whole-directory regression: base `8b0b340bf05b10134783d7c569047c0057dacb98` gives `6 failed, 56 passed`; the candidate gives `6 failed, 63 passed`. The two failed sets are identical line for line, so the six reds are pre-existing on master and the candidate adds seven passes and no red.
- No container was created: `docker ps -a` shows no `backbond-passive-monitor-demo-candidate` or `-rollback`. The harness also sets `DOCKER_HOST` to a nonexistent socket, so a bypassed stub could not have reached a daemon.

Portability note, stated plainly rather than implied: `deploy.sh` checks the helper at an absolute path. On this host the helper is installed, so all seven tests run against the unmodified script. Where the helper is absent, the fixture runs a copy with only the `PREFLIGHT_HELPER=` constant repointed, and asserts exactly one line differs. The two override tests are host-independent either way, because the guard precedes the helper check.

## Calibration delta

Total 0; GOVERNANCE 0; ROBUSTNESS 0; BIAS_FAIRNESS 0; OPS 0.

Proved by construction rather than by assertion: the candidate diff touches only `demo-real-agent/`. Every scoring surface tree object is identical between `8b0b340bf05b10134783d7c569047c0057dacb98` and the candidate commit, so no scoring input can have moved. `demo-real-agent/deploy.sh` is a host deployment script with no import or execution path into rubric, corpus, judge selection, or contract output.

## Open question for the CTO

BEAAA-23760 carries the same title and BLUF as this ticket and is `done`. Its candidate `f85ec18d4455059789d96640009690c8da49e549` was QA APPROVED on BEAAA-23768 and CTO co-signed on BEAAA-23767, and it fixes this same defect — but it is not an ancestor of master, so the fix is not in canonical master. That candidate also fuses in the BEAAA-23703 one-shot d9 grant semantics, which is a larger decision than a bind-source pin and is not mine to merge.

R2 is deliberately the narrow slice: the bind-source pin alone, on current master, with no helper change. The CTO should name which lane is canonical. If `f85ec18d` is to be landed instead, this candidate should be cancelled rather than merged alongside it.

## Gates and rollback

This report authorizes no merge and no promotion. CTO co-sign is pending. QA must test these exact bytes and confirm canonical-master ancestry before any shared or production promotion.

Rollback is byte reversion of the two files; the deployment stays unarmed and fails closed. No live-state rollback is needed, because no install, arm, dispatch, deploy, restart, or host mutation was performed.

FAL audit: one host-boundary defect on canonical master; authority is BEAAA-23761 under BEAAA-23703; linkage is BEAAA-23761 to BEAAA-23667. No external claim or protection statement is introduced.
