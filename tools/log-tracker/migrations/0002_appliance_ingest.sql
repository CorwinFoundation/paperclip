-- BEAAA-20231: counts/hashes-only appliance ingest receipts.
-- Raw events, determinations, commands, and content are intentionally absent.

CREATE TABLE IF NOT EXISTS appliance_enrollments (
    appliance_id        TEXT PRIMARY KEY,
    tenant_id_hash      TEXT NOT NULL,
    source_id_hash      TEXT NOT NULL,
    token_hash          TEXT NOT NULL,
    public_key_pem      TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS appliance_ingest_receipts (
    batch_hash          TEXT PRIMARY KEY,
    appliance_id        TEXT NOT NULL REFERENCES appliance_enrollments(appliance_id),
    batch_seq           INTEGER NOT NULL,
    prev_batch_hash     TEXT,
    tenant_id_hash      TEXT NOT NULL,
    source_id_hash      TEXT NOT NULL,
    events_count        INTEGER NOT NULL CHECK (events_count >= 0),
    determinations_count INTEGER NOT NULL CHECK (determinations_count >= 0),
    billable_actions    INTEGER NOT NULL CHECK (billable_actions >= 0),
    tokens_count        INTEGER NOT NULL CHECK (tokens_count >= 0),
    accepted_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (appliance_id, batch_seq)
);

CREATE INDEX IF NOT EXISTS ix_appliance_ingest_receipts_tip
    ON appliance_ingest_receipts (appliance_id, batch_seq DESC);

CREATE TRIGGER IF NOT EXISTS appliance_receipts_no_update
BEFORE UPDATE ON appliance_ingest_receipts BEGIN
    SELECT RAISE(ABORT, 'append_only_update_forbidden');
END;
CREATE TRIGGER IF NOT EXISTS appliance_receipts_no_delete
BEFORE DELETE ON appliance_ingest_receipts BEGIN
    SELECT RAISE(ABORT, 'append_only_delete_forbidden');
END;
