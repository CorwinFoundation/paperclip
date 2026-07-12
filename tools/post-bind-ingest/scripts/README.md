# Passive Log Reader end-to-end simulation

Run a separate synthetic AI-agent process through the complete local Monitor
path: production-shaped JSONL logs, collector-compatible source redaction,
Ed25519-signed receiver batches, receiver validation, passive detectors,
continuity analysis, and Log Tracker persistence.

```bash
python tools/post-bind-ingest/scripts/run_passive_log_reader_e2e.py
```

The simulation uses a synthetic non-VAPI tenant. A successful run:

- emits three sequential signed batches from a separate agent subprocess;
- detects synthetic P1-P6 peril signals;
- proves healthy heartbeat and sequence continuity;
- rejects replay and payload tampering;
- confirms synthetic email/secret values are absent after source redaction;
- persists each claim candidate into Log Tracker with bind/loss scan brackets.

The default evidence directory is a newly created temporary directory. To
retain evidence at a chosen location, pass an empty directory:

```bash
python tools/post-bind-ingest/scripts/run_passive_log_reader_e2e.py \
  --output-dir /tmp/backbond-passive-e2e
```

Evidence includes the synthetic raw JSONL, signed receiver batches, normalized
and detected JSONL, replay state, Log Tracker SQLite database, and
`analysis-report.json`.
