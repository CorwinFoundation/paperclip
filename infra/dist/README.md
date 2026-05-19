# `infra/dist/` — design-partner distribution build (BEAAA-567 / ADR-0011)

This directory is the build pipeline for **Path 3** — the single-file
PyInstaller binary shipped to Phase-1 design partners under a per-partner
license-key gate. The pipeline is run by `infra/dist/build.py`; the
PyInstaller spec is `infra/dist/are_scan.spec`; the runtime gate is
`infra/dist/license_gate.py`; the entry script is
`infra/dist/are_scan_dist.py`.

Read this alongside:

* `docs/adr/adr-0011-design-partner-pyinstaller-distribution.md` —
  Founder-signed ADR. §3 (decision), §4 (tamper-resistance posture),
  §10 (rollback).
* `infra/license_validator/README.md` — BEAAA-569 endpoint stub the
  binary talks to at runtime.
* Plan document on BEAAA-567 — sub-stream decomposition, child issues.

## Quick start

```sh
# 1. (Operator) Issue a license against the BEAAA-569 stub.
python -m infra.license_validator.cli init-keys \
  --private-key /tmp/lv/private.pem \
  --public-key  /tmp/lv/public.pem
python -m infra.license_validator.cli issue \
  --registry /tmp/lv/registry.json \
  --partner-id partner-acme \
  --binary-build-id build-20260520-x86_64-linux-gnu-<sha8>-partner-acme  # match infra/dist/build.py
# -> emits an lk_... key on stdout

# 2. (Operator) Build the binary for one partner.
pip install pyinstaller==6.11.1
python -m infra.dist.build \
  --partner-id partner-acme \
  --license-endpoint-url http://127.0.0.1:8731 \
  --license-key "lk_..." \
  --public-key-pem /tmp/lv/public.pem \
  --master-salt "$(openssl rand -hex 32)" \
  --target-os x86_64-linux-gnu \
  --allow-insecure-endpoint   # stub-loop only; partner builds must use https://

# 3. (Operator) Start the stub server in another terminal, then run the binary.
python -m infra.license_validator.cli serve \
  --registry    /tmp/lv/registry.json \
  --audit       /tmp/lv/audit.log \
  --private-key /tmp/lv/private.pem \
  --admin-token "$(openssl rand -hex 32)" \
  --host 127.0.0.1 --port 8731 &

./build/build-*/dist/are-scan-design-partner --agent G1 --seed 42 --corpus 0.1.0 --out /tmp/G1.json
```

## Determinism statement

**Claim.** Given the same:

* git sha (source tree),
* `SOURCE_DATE_EPOCH` (defaults to the git commit timestamp),
* `pyinstaller==6.11.1`,
* `python==3.12.3` (linux/amd64) or matching pinned interpreter on darwin,
* `--master-salt` value (operator-held),
* `--partner-id` value,
* build flags as set by `infra/dist/build.py` (`--noupx --clean --strip`,
  `PYTHONHASHSEED=0`, `TZ=UTC`),

the binary's sha256 MUST reproduce byte-for-byte on any builder running
the same OS/arch. The watermark salt is deterministic from
`sha256(partner_id || master_salt)[:16]` so per-partner builds reproduce
across builders, and the canonical `corpus_snapshot_hash` is computed
without the watermark sidecar — keeping it equal across Path 1 / Path 2 /
Path 3 (ADR-0011 §3.5 / Supreme Law).

**Verification (S1 close-out gate).** A second independent build on a
fresh ubuntu-22.04 container with the same inputs must reproduce the
binary's sha256. The verification harness lands in the S1 implementation
work and is not yet wired here.

## Rollback statement (ADR-0011 §10)

| Tier | Action | Wall-clock | Owner |
|---|---|---|---|
| 1a | ADR rollback pre-build (`status: proposed → superseded`) | ≤ 2 min | CTO |
| 1b | In-field per-partner revocation (server flip to `revoked`; binary fails closed on next score call) | ≤ 10 min decision-to-effect; per-binary effect bounded by attestation TTL ≤ 24h | CTO or Founder |
| 1c | Phase-wide rollback (stop building new binaries; outstanding keys auto-expire) | bounded by longest unexpired key | CTO / CBDO |

The runtime contract enforced by `license_gate.py` is what makes 1b
work: no offline grace, no retry, no cached attestation extending past
TTL.

## Exit codes

The binary inherits `bin/are-scan`'s 0/1/2/3 contract for the scoring
layer, and adds **4** for license-gate refusals. Operators can branch
on:

| Exit | Meaning |
|---|---|
| 0 | Scan clean. |
| 1 | `evidence_refs` incomplete (refuse-to-quote per the Scanner contract). |
| 2 | Invalid inputs. |
| 3 | Scanner internal error. |
| 4 | License gate refused this invocation (revoked / unreachable / expired / partner mismatch / binary blacklisted / EULA not accepted / dist config missing). |

## What this directory does NOT do

* It does not build for darwin. PyInstaller does not cross-compile; the
  darwin/arm64 + darwin/amd64 binaries require a macOS build host. See
  the S6 child of BEAAA-567.
* It does not run the calibration smoke (BEAAA-567 §5). That lives in
  the S4 child — it consumes the linux binary built here plus the
  local-try-it baseline.
* It does not run the license-gate negative smokes (BEAAA-567 §6).
  That lives in the S5 child.
* It does not verify pinning survival under PyInstaller (BEAAA-567 §7).
  That lives in the S3 child.

## File map

| File | Role |
|---|---|
| `__init__.py` | package marker |
| `license_gate.py` | runtime gate; only place that imports the BEAAA-569 client |
| `are_scan_dist.py` | PyInstaller entry point; orchestrates EULA, gate, delegate |
| `_dist_config.py.template` | template rendered per build with partner-specific values |
| `build.py` | deterministic build orchestrator |
| `are_scan.spec` | PyInstaller spec (datas, hidden imports, excludes, EXE flags) |
| `manifest_schema.json` | JSON schema for per-build manifest |
| `eula.txt` | short-form EULA shown on first invocation; full text in `contracts/design-partner-eula-v0.md` |
| `README.md` | this file |
