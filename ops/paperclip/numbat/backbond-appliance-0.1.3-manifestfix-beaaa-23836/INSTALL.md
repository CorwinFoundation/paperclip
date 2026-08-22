# Install and operate the BackBond Passive Monitor appliance

## Compatibility

The release is tested with Docker 29 and Compose v2 on Linux `amd64`. The pinned
base is a multi-platform OCI index, but this release receipt approves only the
architecture named in `release/IMAGE_PLATFORM`. Podman is not approved by this
candidate. The appliance is asynchronous and never belongs in the request path.

## Inputs and permissions

Provide a substrate directory readable by UID 10001, a read-only config directory,
and the named `backbond-appliance-state` volume. The state volume contains signing,
chain, idempotency, and outbox state and must be backed up as sensitive operational
state. Supply `BACKBOND_SHIP_TOKEN` only through the environment or an approved
runtime secret source.

## Artifact verification

Run `./install.sh verify` before loading any image. It verifies every release file
listed in `release/SHA256SUMS`. `./install.sh install` then loads the compressed OCI
archive and refuses an image whose Docker content identity differs from
`release/IMAGE_ID`. Floating tags are not used for execution.

## Configuration and mounts

Use synthetic identifiers such as `vendor-agent-prod-01`. Configure a reader path
under `/data`; do not place host paths in the in-container config. The generic
`ndjson_log` and coding-agent readers are supported. `synthetic_engine` is a
test-only fixture reader and does not represent an external source contract.

```sh
mkdir -p config
cp config.example.yaml config/config.yaml
export BACKBOND_SUBSTRATE_PATH=/srv/agent/activity
export BACKBOND_CONFIG_PATH="$PWD/config"
./install.sh install
./install.sh start
```

The substrate and config mounts are read-only. The root filesystem is read-only;
only the named state volume and a 64 MiB `tmpfs` are writable.

## Resource starting limits

The Compose defaults are conservative starting values, not capacity claims: 1 CPU,
512 MiB memory, 128 PIDs, and 64 MiB temporary storage. Measure event volume,
processing latency, queue depth, and restarts before changing them. A limit breach
must remain visible; restart is `no` so a persistent error is not hidden.

## Selftest, readiness, and dry operation

```sh
docker compose exec appliance python -m appliance.main selftest -c /config/config.yaml
./install.sh status
docker compose logs --no-log-prefix appliance
```

The OCI healthcheck runs the same no-content selftest. A healthy result proves that
the local invariant test passed; it does not prove endpoint reachability or that a
protection structure is active. Use outbox mode for a non-networked first run.

## Upgrade and rollback

Keep the current bundle directory and image until the successor is verified.

1. Stop the service with `./install.sh stop`.
2. Back up the named state volume under your retention procedure.
3. Run `./install.sh upgrade` from the successor bundle.
4. Run the selftest, then `./install.sh start` and verify health.
5. If verification fails, run `./install.sh rollback` while the prior image remains
   local. The state volume is preserved.

This release makes no state-schema migration. A future release that changes state
compatibility must state that constraint in its release receipt before upgrade.

## Stop, uninstall, and retain

`./install.sh stop` stops collection and preserves containers and state.
`./install.sh uninstall` removes containers and preserves the named state volume.
Deleting the volume is a separate destructive operation and is intentionally not
implemented by the installer.

## Diagnostic evidence

Collect the appliance version, `release/IMAGE_ID`, stable `BB-APPL-*` error code,
`docker compose ps`, and redacted logs. Never include tokens, raw substrate records,
content, or vendor identifiers in an escalation bundle.
