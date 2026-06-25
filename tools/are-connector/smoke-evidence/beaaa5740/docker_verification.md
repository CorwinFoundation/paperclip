---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-5740 hardens the BackBond connector build and response evidence path so BackBond can rate and insure AI agents without unpinned runtime dependencies or unbounded response materialization."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-5740", "BEAAA-5675", "BEAAA-5741"]
proof_points_cited: ["python -m pytest -q tools/are-connector/tests", "pip-compile --generate-hashes", "SOURCE_DATE_EPOCH=1735689600 wheel sha256 replay", "docker socket permission check"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Docker image rebuild and image manifest digest verification blocked by Docker socket permission in this heartbeat environment."]
---

# BEAAA-5740 Docker Verification Receipt

BLUF: source-side deterministic dependency and response-cap gates are implemented and verified; Docker image rebuild verification is blocked in this heartbeat by Docker socket permissions.

## Classification

Rule-execution under `scanner-implementation-supervision-v1@v1.1`: connector build determinism and HTTP boundary hardening under existing Scanner rules. No rubric point, scoring math, judge model, or `are-scanner-api-v1` output shape changed.

## Source Gates Completed

- `tools/are-connector/constraints.txt` generated with `pip-compile --generate-hashes`.
- Runtime direct dependencies are exact-pinned in `pyproject.toml`: `cryptography==45.0.7`, `pynacl==1.6.2`, `requests==2.34.2`.
- Dockerfile installs runtime dependencies with `pip install --require-hashes -r constraints.txt`.
- Dockerfile installs the local connector package with `--no-deps` after hashed dependency install.
- Dockerfile sets `SOURCE_DATE_EPOCH=1735689600` during local package install so wheel generation is byte-stable.
- Connector version bumped from `0.1.0` to `0.1.1`.

## Verification Commands

```
$ /tmp/beaaa5740-venv/bin/pip install --dry-run --require-hashes -r tools/are-connector/constraints.txt
Would install PyNaCl-1.6.2 certifi-2026.5.20 cffi-2.0.0 charset-normalizer-3.4.7 cryptography-45.0.7 idna-3.18 pycparser-3.0 requests-2.34.2 urllib3-2.7.0
```

```
$ . /tmp/beaaa5740-venv/bin/activate && python -m pytest -q tools/are-connector/tests
17 passed in 0.56s
```

```
$ SOURCE_DATE_EPOCH=1735689600 python -m pip wheel --no-deps ...
5bc066673530ea03e0d686c0764b551f7f87be4e7aa77e5e5c0b015ccc13e42b  are_connector-0.1.1-py3-none-any.whl

$ SOURCE_DATE_EPOCH=1735689600 python -m pip wheel --no-deps ...
5bc066673530ea03e0d686c0764b551f7f87be4e7aa77e5e5c0b015ccc13e42b  are_connector-0.1.1-py3-none-any.whl
```

```
$ python - <<'PY'
from harness.golden_set.n5_band import run_l1_n5
r = run_l1_n5()
print(r["summary"]["summary_line"])
print(r["current_scanner_version"], r["current_rubric_version"], r["current_contract_version"])
PY
N=5 | baselined=8/9 | pending=1 | max ARE drift=0 | breaches=0 | PASS
0.4.2 1.2.0 are-scanner-api-v1@v1.3.0
```

## Docker Blocker

Docker is installed, but this user cannot reach the daemon:

```
$ docker system df
permission denied while trying to connect to the docker API at unix:///var/run/docker.sock
```

Root filesystem pressure is also present:

```
$ df -h / /var/lib/docker /mnt/paperclipdata
/dev/vda1  24G  24G  51M  100% /
/dev/sda   100G 25G  70G   26% /mnt/paperclipdata
```

## AC10 Status

AC10 is not complete in this heartbeat because image rebuild and image manifest digest verification require Docker daemon access. The source gates required before the image rebuild are complete and ready for a Docker-capable runner.
