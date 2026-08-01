# INSTALL — BackBond appliance (environment requirements & security posture)

## Requirements

- Linux (or container runtime); macOS/Windows supported for dev.
- Python 3.9+ (3.11 tested) OR Docker.
- Dependencies: `cryptography`, `PyYAML` (installed by `install.sh` or the image).
- **Read** access to the substrate where agent activity is written. Nothing else.

## Privileges (least-privilege by design)

- The appliance runs as a **non-root** user (`backbond`, uid 10001 in the image).
- It needs **read-only** access to the substrate (mount `:ro`).
- It needs **write** access only to its own state dir (`~/.backbond`): the Ed25519
  key, chain/idempotency state, and the outbox. Nothing else on the host is touched.
- The systemd unit ships with `ProtectSystem=strict`, `NoNewPrivileges=true`,
  `PrivateTmp=true`, and a single `ReadWritePaths` for the state dir.

## Substrate options (readers)

- `enso_engine` — reads the Enso engine's activity output (dir of JSON / `events`
  arrays). First partner substrate.
- `ndjson_log` — tails any newline-delimited JSON activity log. Generic fallback.
- New environments = new reader in `appliance/readers/` (BackBond builds these; the
  partner does not).

Auto-discovery is supported, but a missing substrate **fails loudly** at run time
(non-zero exit + explicit message) — the appliance never silently scans nothing.
If auto-discovery can't find it, set `reader.path` explicitly.

## Network

- `ship.mode: outbox` (default) writes signed batches to a local directory — zero
  network. Good for air-gapped validation.
- `ship.mode: http` POSTs signed batches to BackBond; the bearer token comes from the
  `BACKBOND_SHIP_TOKEN` environment variable, never from config or flags.
- The appliance is **never in the agent's request/serving path** — it is
  observability-plane only. It cannot add latency or downtime to the agents.

## Data handling

- Content (post text, recipients, CRM records, PII, transcripts) is read only in
  memory on the partner host for local rule evaluation, then dropped.
- Emitted batches contain only counts, action classes, determinations, and
  evidence-commitment hashes — enforced fail-closed (`selftest`).
- Treat the state dir and signing key as sensitive; they authenticate this
  appliance to BackBond.

## Verify before trusting

```bash
python3 -m appliance.main selftest -c config.yaml    # no-content invariant must PASS
python3 tests/e2e.py                                  # full gate on synthetic data
```

Both must be green before shipping to a live endpoint.

## Uninstall / kill switch

- Stop: `systemctl disable --now backbond-appliance.timer` (or stop the container).
- The appliance holds no agent state; removing it stops all collection immediately.
