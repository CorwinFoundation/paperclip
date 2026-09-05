-- BEAAA-24613: per-agent receipt projection without storing customer agent ids.

CREATE TABLE IF NOT EXISTS appliance_agent_salts (
    appliance_id TEXT PRIMARY KEY REFERENCES appliance_enrollments(appliance_id),
    salt         BLOB NOT NULL CHECK (length(salt) = 32)
);

CREATE TRIGGER IF NOT EXISTS appliance_agent_salts_no_update
BEFORE UPDATE ON appliance_agent_salts BEGIN
    SELECT RAISE(ABORT, 'append_only_update_forbidden');
END;
CREATE TRIGGER IF NOT EXISTS appliance_agent_salts_no_delete
BEFORE DELETE ON appliance_agent_salts BEGIN
    SELECT RAISE(ABORT, 'append_only_delete_forbidden');
END;

CREATE TABLE IF NOT EXISTS appliance_ingest_receipt_agents (
    batch_hash           TEXT NOT NULL REFERENCES appliance_ingest_receipts(batch_hash),
    agent_id_hash        TEXT NOT NULL,
    events_count         INTEGER NOT NULL CHECK (events_count >= 0),
    determinations_count INTEGER NOT NULL CHECK (determinations_count >= 0),
    billable_actions     INTEGER NOT NULL CHECK (billable_actions >= 0),
    PRIMARY KEY (batch_hash, agent_id_hash)
);

CREATE TRIGGER IF NOT EXISTS appliance_receipt_agents_no_update
BEFORE UPDATE ON appliance_ingest_receipt_agents BEGIN
    SELECT RAISE(ABORT, 'append_only_update_forbidden');
END;
CREATE TRIGGER IF NOT EXISTS appliance_receipt_agents_no_delete
BEFORE DELETE ON appliance_ingest_receipt_agents BEGIN
    SELECT RAISE(ABORT, 'append_only_delete_forbidden');
END;
