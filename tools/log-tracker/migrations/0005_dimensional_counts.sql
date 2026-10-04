-- BEAAA-36242: count-only dimensional projection. Raw event records are absent.
CREATE TABLE IF NOT EXISTS appliance_ingest_receipt_windows (
    batch_hash TEXT PRIMARY KEY REFERENCES appliance_ingest_receipts(batch_hash),
    occurrence_start TEXT NOT NULL,
    occurrence_end TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS appliance_ingest_receipt_dimensions (
    batch_hash TEXT NOT NULL REFERENCES appliance_ingest_receipts(batch_hash),
    agent_id_hash TEXT NOT NULL,
    event_type TEXT NOT NULL,
    outcome TEXT NOT NULL,
    events_count INTEGER NOT NULL CHECK (events_count > 0),
    PRIMARY KEY (batch_hash, agent_id_hash, event_type, outcome)
);

CREATE INDEX IF NOT EXISTS ix_appliance_dimensions_lookup
    ON appliance_ingest_receipt_dimensions (event_type, outcome, agent_id_hash);

CREATE TRIGGER IF NOT EXISTS appliance_windows_no_update
BEFORE UPDATE ON appliance_ingest_receipt_windows BEGIN
    SELECT RAISE(ABORT, 'append_only_update_forbidden');
END;
CREATE TRIGGER IF NOT EXISTS appliance_windows_no_delete
BEFORE DELETE ON appliance_ingest_receipt_windows BEGIN
    SELECT RAISE(ABORT, 'append_only_delete_forbidden');
END;
CREATE TRIGGER IF NOT EXISTS appliance_dimensions_no_update
BEFORE UPDATE ON appliance_ingest_receipt_dimensions BEGIN
    SELECT RAISE(ABORT, 'append_only_update_forbidden');
END;
CREATE TRIGGER IF NOT EXISTS appliance_dimensions_no_delete
BEFORE DELETE ON appliance_ingest_receipt_dimensions BEGIN
    SELECT RAISE(ABORT, 'append_only_delete_forbidden');
END;
