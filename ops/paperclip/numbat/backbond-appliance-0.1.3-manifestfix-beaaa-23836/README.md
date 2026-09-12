> **Status: not deployed. Do not install this tree.**
>
> The maintained appliance line is
> `ops/paperclip/numbat/backbond-appliance-0.1.0/backbond-appliance/`, which is what
> `ops/paperclip/numbat/install-listener-ariclaw.sh` ships. This 0.1.3 tree carries a
> higher version number but is a separate lineage and is not part of the live install
> path.
>
> The tree is kept, not abandoned — it holds the only copies of
> `appliance/rules/exposure_agent_pack.yaml`, `appliance/readers/synthetic_engine.py`,
> `tests/test_shared_exposure_pack.py` (BEAAA-29962, 2026-09-06) and the sealed 0.1.3
> `release/` bundle.

# BackBond Passive Monitor appliance

Run the sealed appliance in your environment to evaluate supported agent activity
locally and emit signed, content-reduced evidence. The appliance is
observability-plane only and does not sit in the agent's request path.

## Security boundary

- Reads: one vendor substrate mounted at `/data:ro` and configuration at `/config:ro`.
- Writes: one appliance-owned state volume and a bounded temporary filesystem.
- Emits: signed counts, action classes, determinations, and evidence hashes.
- Rejects: configured content fields at both appliance and receiver boundaries.
- Runs: non-root UID 10001, read-only root filesystem, no Linux capabilities,
  `no-new-privileges`, 1 CPU, 512 MiB memory, and 128 PIDs by default.

Installation starts only the observability component described here.

## Before you start

You need Docker 24+ with Compose v2, this release bundle, a readable substrate
path, and a configuration directory. Do not install Python or pip packages on the
host.

## Verify and install

```sh
./install.sh verify
./install.sh install
```

Expected terminal state:

```text
PASS  artifact checksums verified
PASS  sealed OCI image loaded
READY appliance can start
```

Copy `config.example.yaml` to `config/config.yaml`, set a vendor-neutral reader
path under `/data`, and export the host mounts:

```sh
mkdir -p config
cp config.example.yaml config/config.yaml
export BACKBOND_SUBSTRATE_PATH=/srv/agent/activity
export BACKBOND_CONFIG_PATH="$PWD/config"
```

## Start safely

`compose.yaml` makes the least-privilege controls visible and is the canonical
runtime path:

```sh
./install.sh start
./install.sh status
docker compose exec appliance python -m appliance.main selftest -c /config/config.yaml
```

The service does not auto-restart on persistent configuration errors. Inspect
`docker compose ps` and `docker compose logs --no-log-prefix appliance` before
retrying.

## Operate, upgrade, and roll back

```sh
./install.sh stop       # preserves state
./install.sh upgrade    # verifies and loads the new immutable image
./install.sh start
./install.sh rollback   # restores the previous locally retained image
./install.sh uninstall  # removes containers; preserves the named state volume
```

An interrupted or failed verification leaves the active release unchanged.
Rollback preserves the appliance state volume; do not delete that volume unless
your retention owner has approved permanent state removal.

## Failure states

| State | Safe result | Operator action |
| --- | --- | --- |
| Checksum or image mismatch | Execution stops | Re-obtain the exact release bundle |
| Config or substrate unreadable | No new evidence is sent | Correct the read-only mount or field |
| State volume unavailable | New processing stops | Restore volume ownership/capacity |
| Selftest unhealthy | Start/ship is blocked by operator gate | Keep stopped and collect digest plus redacted logs |
| Endpoint/auth failure | Local state is retained for bounded retry | Check endpoint and secret injection; never paste tokens into logs |
| Resource exhaustion | Container stops at configured limit | Inspect metrics, then adjust an approved measured limit |

See [INSTALL.md](INSTALL.md) for the complete runbook and
[`release/sbom.spdx.json`](release/sbom.spdx.json) for the release SBOM.
