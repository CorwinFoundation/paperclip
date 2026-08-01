-- BEAAA-20234 evidence-tier-assignment/1.0 append-only audit ledger.
CREATE TABLE IF NOT EXISTS evidence_tier_events (
    event_id                   TEXT PRIMARY KEY,
    tenant_id_hash             TEXT NOT NULL,
    agent_id_hash              TEXT NOT NULL,
    term_id_hash               TEXT NOT NULL,
    policy_version             TEXT NOT NULL,
    assigned_at                TEXT NOT NULL,
    agent_tier                 TEXT NOT NULL CHECK (agent_tier IN ('T0','T1','T2','T3','T4')),
    status                     TEXT NOT NULL CHECK (status IN ('assigned','downgraded','exception_active','pending_verification')),
    verification_snapshot_json TEXT NOT NULL,
    assignment_json            TEXT NOT NULL,
    inserted_at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS ix_evidence_tier_tenant_agent_time
    ON evidence_tier_events (tenant_id_hash, agent_id_hash, assigned_at);
CREATE TRIGGER IF NOT EXISTS log_tracker_no_update_evidence_tier_events
BEFORE UPDATE ON evidence_tier_events BEGIN
    SELECT RAISE(ABORT, 'append_only_update_forbidden');
END;
CREATE TRIGGER IF NOT EXISTS log_tracker_no_delete_evidence_tier_events
BEFORE DELETE ON evidence_tier_events BEGIN
    SELECT RAISE(ABORT, 'append_only_delete_forbidden');
END;
