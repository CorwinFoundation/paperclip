# BEAAA-23759 V2: zero-new-privilege judge readiness repair

BLUF: `demo-real-agent/deploy.sh` no longer stats the protected host socket. It accepts exactly one `JUDGE_SOCKET: ready` or `JUDGE_SOCKET: missing` line from the existing fixed, empty-argument root preflight helper. Missing, unknown, duplicate, contradictory, or helper-failure output stops before Docker.

## Immutable base and compatibility

- Review branch: `beaaa-23759-zero-privilege-v2`
- Local `origin/master` base: `d9d680f53db7de263f026be5284c97229126872c`
- Refresh caveat: `git fetch origin master --prune` failed authentication on 2026-08-16. The candidate is bound to the locally recorded remote-tracking commit and must be ancestry-revalidated by QA in an authenticated checkout.
- Actual installed helper baseline recorded by BEAAA-23705 and recovered from the staged BEAAA-23703 index: `927fca8b796e7e868a319ab2fffb4372e34bb29228723090f31f6b994a46f6f8`.
- V2 helper is that exact baseline plus a fixed `/run/backbond-claude-judge/judge.sock` `lstat`, a names-only result, and invocation only when `DEMO_JUDGE_BACKEND` is configured. The empty argument contract, sudo command path, secret validation, one-shot consume behavior, and existing names-only outputs remain intact.
- The earlier referenced candidate `3dfc576598c74c54155cf211b46927ea490b5c8f0e67560af01d4cb4f01b49cf` was independently recomputed from separate workspace bytes but is not treated as installed or reused. V2 helper SHA is `9ecc60538c9904a2e9c1583c7af2ffed9d37f9775fa3888b02313237a53874e2`.

## Security boundary

V2 grants `passive-demo-deploy` zero new host access. There is no new group, ACL, socket permission, sudo entry, SSH permission, secret read, service permission, container mount, container user, or restart. The deploy principal continues to call the one existing fixed-path, empty-argument sudo helper. Only that root helper evaluates the fixed socket pathname; it emits no path metadata, owner, mode, token, environment value, or socket content.

The container remains uid/gid `10001:10001`, with the existing read-only directory bind and in-container socket path. V2 changes readiness observation only.

## Exact installation

Do not install from this issue. After fresh QA approval of the exact candidate commit and archive, the authorized host installer runs:

```sh
sudo artifacts/BEAAA-23759/v2/install-helper.sh artifacts/BEAAA-23759/v2
```

The installer fails unless the live helper is exactly the recorded `927fca…` baseline with `root:root 0755`. It verifies V2 bytes, stages in the target directory, then atomically renames. It does not touch any other host state and does not restart the service.

## Exact rollback

```sh
sudo artifacts/BEAAA-23759/v2/rollback-helper.sh artifacts/BEAAA-23759/v2
```

Rollback fails unless the live helper is exactly V2 and the packaged baseline is exactly `927fca…`; it restores the baseline atomically as `root:root 0755`. Repository rollback is `git revert` of the V2 commit. Leave all one-shot authorization absent.

## Verification

`demo-real-agent/tests/test_deploy_judge_socket_beaaa23759_v2.py` executes ready, missing, non-socket, OS-denial, exact-ready, exact-missing, absent, unknown, duplicate, contradictory, prefixed-output, syntax, and forbidden-privilege cases. Five seeded runs pass. Host verification remains the canonical QA child's responsibility; this issue performed no host mutation.

## Operational metadata

- Contract: `contracts/are-scanner-api-v1.md@v1.3.0`
- Classification: rule-change because the tamper-resistance boundary changes; CTO co-sign required before merge
- scanner_version: `0.5.0` unchanged
- corpus_version: `0.1.0` unchanged
- probe-set-version: `passive-demo-host-gate-d9` unchanged
- rubric_point_id: `N/A_INFRA_TAMPER_BOUNDARY`
- N=5 calibration delta: `0` across all four pillars; no scoring import or mutation path
- cto_cosign_ref: pending
- FAL audit: internal engineering candidate; authority and evidence are BEAAA-23759 and canonical QA STOP BEAAA-23763
