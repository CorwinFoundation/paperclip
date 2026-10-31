# Scan delivery monitor

The intake worker can run the delivery monitor as an opt-in, read-only observer
of completed Findings scans. It never advances a review, sends report mail, or
changes a scan row. The existing Findings worker remains the owner of delivery.

## Configuration

Leave `QA_DELIVERY_MONITOR` unset unless the separate alert adapters are ready.
When enabled, all three values below are required:

```sh
QA_DELIVERY_MONITOR=1
QA_DELIVERY_MONITOR_SINCE=2026-10-31T00:00:00Z
QA_DELIVERY_ALERT_DB_PATH=/var/lib/backbond-scan-intake/delivery-alerts.sqlite
```

`QA_DELIVERY_MONITOR_SINCE` is an explicit UTC cutoff. It prevents deployment
from backfilling alerts for older scans. `QA_DELIVERY_ALERT_DB_PATH` must be a
separate service-owned SQLite path, not the intake or Findings database.

The Paperclip incident adapter requires `PAPERCLIP_API_URL`,
`PAPERCLIP_COMPANY_ID`, and `PAPERCLIP_API_KEY`. The Founder push adapter
requires `QA_FOUNDER_PUSH_URL` (or `FOUNDER_PUSH_URL`) and
`QA_FOUNDER_PUSH_TOKEN` (or `FOUNDER_PUSH_TOKEN`). Use HTTPS for remote
origins; HTTP is accepted only for loopback fixtures. Optional
`QA_ALERT_ASSIGNEE_AGENT_ID` assigns newly created incidents to the requesting
agent.

## Alert behavior

The monitor ages a completed scan from `scans.finished_at`. At four hours, a
critical delivery incident is created with the held stage (`review`,
`workflow_write_back`, `credential`, or `provider`) and the same sanitized
record is pushed to the Founder. Review holds produce an earlier high-priority
decision incident with explicit options. Each alert has independent durable
ticket and push receipts, stable retry keys, and SQLite serialization, so a
failed push does not create another ticket.

Missing provider acceptance is not treated as delivery. A successful HTTP
response alone is not a delivery receipt; the Findings outbox must contain a
validated provider message ID and `sent` state.

## Runtime and verification

The monitor runs once per existing intake worker tick, before the Findings
delivery tick. Adapter failures are retained in the separate alert outbox and
retried on a later tick. Inspect only receipt metadata when validating a
deployment; do not print tokens, raw provider errors, or report content.

The focused offline check is:

```sh
python3 -m pytest -q backbond/scan-intake/tests/test_alert_adapters.py \
  backbond/scan-intake/tests/test_delivery_monitor.py
```

No paid hosted CI fallback is part of this configuration. Heavy checks remain
on the dedicated `backbond-ci` runner under BEAAA-40719.

## Protected configuration diagnosis

Run this read-only, repeatable command on AriClaw from the implementation
workspace (absolute path shown for the BEAAA-45026 checkout):

```sh
sudo python3 /mnt/paperclipdata/dot-paperclip/instances/default/workspaces/88e68019-1cb6-4c4f-83e1-9fd340fb6821/beaaa-45026-scanner/scripts/deploy/diagnose-delivery-monitor-config.py
```

The diagnostic parses the deployed code without importing it, reads the active
service process environment, and inspects unit assignments and referenced
EnvironmentFile sources. Output contains names, presence/nonempty flags, an
explicit monitor-enabled flag and exception classes only. It never prints
configuration values or subprocess output. Unit source flags are diagnostic;
the running process environment is authoritative for the active process.

A missing match in an earlier regex inspection does not establish missing
configuration. If the process environment is inaccessible, the diagnosis
remains unresolved. Both empty and absent required variables block alert
acceptance; optional assignee and alternate push variable names may be absent.
Actual ticket IDs, UTC timestamps and push receipts must be demonstrated
separately after configuration is established.
