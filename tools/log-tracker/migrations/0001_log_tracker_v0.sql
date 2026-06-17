-- BEAAA-6157 Log Tracker v0 ledger schema.
-- Rule-execution: append-only evidence ledger; no BackBond Scanner score logic.

CREATE TABLE IF NOT EXISTS scanner_runs (
    event_id             TEXT PRIMARY KEY,
    scan_id              TEXT NOT NULL,
    agent_id_hash        TEXT NOT NULL,
    customer_id_hash     TEXT NOT NULL,
    purpose              TEXT NOT NULL,
    event_t              TEXT NOT NULL,
    are_total            INTEGER NOT NULL CHECK (are_total BETWEEN 0 AND 100),
    subscores_json       TEXT NOT NULL,
    band                 TEXT NOT NULL CHECK (band IN ('A', 'B', 'C', 'D')),
    confidence           REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    scanner_version      TEXT NOT NULL,
    corpus_version       TEXT NOT NULL,
    model_versions_json  TEXT NOT NULL,
    seed                 INTEGER NOT NULL,
    uncovered_json       TEXT NOT NULL DEFAULT '[]',
    redaction_class_map  TEXT NOT NULL,
    redaction_applied    TEXT NOT NULL DEFAULT '[]',
    payload_json         TEXT NOT NULL,
    inserted_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    supersedes_id        TEXT REFERENCES scanner_runs(event_id)
);

CREATE TABLE IF NOT EXISTS policy_events (
    event_id             TEXT PRIMARY KEY,
    policy_event_id      TEXT NOT NULL,
    policy_id_hash       TEXT NOT NULL,
    agent_id_hash        TEXT NOT NULL,
    customer_id_hash     TEXT NOT NULL,
    event_kind           TEXT NOT NULL,
    scan_id_priced       TEXT,
    event_t              TEXT NOT NULL,
    premium_band         TEXT,
    wording_version      TEXT NOT NULL,
    metadata_json        TEXT NOT NULL,
    redaction_class_map  TEXT NOT NULL,
    redaction_applied    TEXT NOT NULL DEFAULT '[]',
    payload_json         TEXT NOT NULL,
    inserted_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    supersedes_id        TEXT REFERENCES policy_events(event_id)
);

CREATE TABLE IF NOT EXISTS claim_events (
    event_id             TEXT PRIMARY KEY,
    claim_event_id       TEXT NOT NULL,
    claim_id_hash        TEXT NOT NULL,
    policy_id_hash       TEXT NOT NULL,
    agent_id_hash        TEXT NOT NULL,
    customer_id_hash     TEXT NOT NULL,
    peril                TEXT NOT NULL CHECK (peril IN ('P1', 'P2', 'P3', 'P4', 'P5', 'P6')),
    severity_class       TEXT NOT NULL CHECK (severity_class IN ('S0', 'S1', 'S2', 'S3', 'S4')),
    claim_kind           TEXT NOT NULL CHECK (claim_kind IN ('candidate', 'submitted', 'paid', 'denied')),
    event_t              TEXT NOT NULL,
    scan_id_at_bind      TEXT,
    scan_id_at_loss      TEXT,
    evidence_refs_uri    TEXT,
    metadata_json        TEXT NOT NULL,
    redaction_class_map  TEXT NOT NULL,
    redaction_applied    TEXT NOT NULL DEFAULT '[]',
    payload_json         TEXT NOT NULL,
    inserted_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    supersedes_id        TEXT REFERENCES claim_events(event_id)
);

CREATE TABLE IF NOT EXISTS evidence_refs (
    evidence_ref_id      TEXT PRIMARY KEY,
    event_id             TEXT NOT NULL REFERENCES scanner_runs(event_id),
    scan_id              TEXT NOT NULL,
    ordinal              INTEGER NOT NULL,
    rubric_point_id      TEXT NOT NULL,
    test_id              TEXT,
    artifact_uri         TEXT,
    verdict              TEXT NOT NULL CHECK (verdict IN ('pass', 'partial', 'fail', 'n/a')),
    weight               REAL NOT NULL CHECK (weight >= 0 AND weight <= 1),
    redaction_class_map  TEXT NOT NULL,
    redaction_applied    TEXT NOT NULL DEFAULT '[]',
    ref_json             TEXT NOT NULL,
    inserted_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    supersedes_id        TEXT REFERENCES evidence_refs(evidence_ref_id),
    UNIQUE (event_id, ordinal)
);

CREATE INDEX IF NOT EXISTS ix_log_tracker_scanner_runs_scan_id
    ON scanner_runs (scan_id);
CREATE INDEX IF NOT EXISTS ix_log_tracker_scanner_runs_agent_event_t
    ON scanner_runs (agent_id_hash, event_t);
CREATE INDEX IF NOT EXISTS ix_log_tracker_policy_events_policy_event_t
    ON policy_events (policy_id_hash, event_t);
CREATE INDEX IF NOT EXISTS ix_log_tracker_policy_events_scan_id_priced
    ON policy_events (scan_id_priced);
CREATE INDEX IF NOT EXISTS ix_log_tracker_claim_events_policy_event_t
    ON claim_events (policy_id_hash, event_t);
CREATE INDEX IF NOT EXISTS ix_log_tracker_claim_events_scan_bind
    ON claim_events (scan_id_at_bind);
CREATE INDEX IF NOT EXISTS ix_log_tracker_claim_events_scan_loss
    ON claim_events (scan_id_at_loss);
CREATE INDEX IF NOT EXISTS ix_log_tracker_evidence_refs_scan_id
    ON evidence_refs (scan_id);

CREATE TRIGGER IF NOT EXISTS log_tracker_no_update_scanner_runs
BEFORE UPDATE ON scanner_runs BEGIN
    SELECT RAISE(ABORT, 'append_only_update_forbidden');
END;
CREATE TRIGGER IF NOT EXISTS log_tracker_no_delete_scanner_runs
BEFORE DELETE ON scanner_runs BEGIN
    SELECT RAISE(ABORT, 'append_only_delete_forbidden');
END;
CREATE TRIGGER IF NOT EXISTS log_tracker_no_update_policy_events
BEFORE UPDATE ON policy_events BEGIN
    SELECT RAISE(ABORT, 'append_only_update_forbidden');
END;
CREATE TRIGGER IF NOT EXISTS log_tracker_no_delete_policy_events
BEFORE DELETE ON policy_events BEGIN
    SELECT RAISE(ABORT, 'append_only_delete_forbidden');
END;
CREATE TRIGGER IF NOT EXISTS log_tracker_no_update_claim_events
BEFORE UPDATE ON claim_events BEGIN
    SELECT RAISE(ABORT, 'append_only_update_forbidden');
END;
CREATE TRIGGER IF NOT EXISTS log_tracker_no_delete_claim_events
BEFORE DELETE ON claim_events BEGIN
    SELECT RAISE(ABORT, 'append_only_delete_forbidden');
END;
CREATE TRIGGER IF NOT EXISTS log_tracker_no_update_evidence_refs
BEFORE UPDATE ON evidence_refs BEGIN
    SELECT RAISE(ABORT, 'append_only_update_forbidden');
END;
CREATE TRIGGER IF NOT EXISTS log_tracker_no_delete_evidence_refs
BEFORE DELETE ON evidence_refs BEGIN
    SELECT RAISE(ABORT, 'append_only_delete_forbidden');
END;
