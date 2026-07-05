-- BEAAA-11872 fired-probe canary ledger — append-only audit-spine book of record.
--
-- The durable substrate for the anti-gaming canary fired-probe ledger
-- (Founder rule 3d). Mirrors the append-only semantics of the determination
-- audit spine (determination/migrations/0001_determination_records_v1.sql) and
-- the evidence store (BEAAA-11366 / BEAAA-11369): rows are only ever inserted,
-- never updated or deleted in place. The runtime writes the same canonical-JSON
-- book-of-record entries produced by
-- scanners.canary_anti_gaming.AuditSpineFiredProbeLedger; a loader syncs those
-- lines into this table.
--
-- This is additive anti-gaming sidecar infrastructure under
-- are-scanner-api-v1@v1.9.0. It changes no BackBond Score math, rubric weight,
-- judge model, or /scanner/v1/scans response shape — the canary ledger is an
-- Underwriting-review signal only and never feeds scoring
-- (scanners/canary_anti_gaming.py module docstring).

CREATE SCHEMA IF NOT EXISTS canary;

-- Shared append-only guard for the canary book-of-record table.
CREATE OR REPLACE FUNCTION canary.reject_mutation()
RETURNS trigger AS $$
BEGIN
    IF (TG_OP = 'UPDATE') THEN
        RAISE EXCEPTION 'canary_append_only_update_forbidden'
            USING ERRCODE = 'integrity_constraint_violation';
    ELSE
        RAISE EXCEPTION 'canary_append_only_delete_forbidden'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS canary.fired_probe_records (
    fired_probe_row_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    schema_version         text NOT NULL,
    -- Tamper-evident content hash over the semantic fields only; the envelope
    -- fields (recorded_at / preimage_sha256 / record_kind) are excluded from
    -- the preimage by construction so it is stable across replays.
    preimage_sha256        text NOT NULL
        CHECK (preimage_sha256 ~ '^(sha256:)?[0-9a-f]{64}$'),
    -- Tenant/vendor lineage: fired-probe reads are scoped by both, exactly like
    -- the in-memory and JSONL ledger implementations.
    tenant_id              text NOT NULL,
    vendor_id              text NOT NULL,
    phrasing_hash          text NOT NULL
        CHECK (phrasing_hash ~ '^sha256:[0-9a-f]{64}$'),
    scan_id                text NOT NULL,
    fired_at               timestamptz NOT NULL,
    rubric_point_id        text,
    test_id                text,
    recorded_at            timestamptz NOT NULL DEFAULT now()
);

-- The fresh-canary selector and Goodhart detector both key on (tenant, vendor).
CREATE INDEX IF NOT EXISTS ix_fired_probe_records_tenant_vendor
    ON canary.fired_probe_records (tenant_id, vendor_id);
CREATE INDEX IF NOT EXISTS ix_fired_probe_records_scan
    ON canary.fired_probe_records (scan_id);
CREATE INDEX IF NOT EXISTS ix_fired_probe_records_phrasing
    ON canary.fired_probe_records (tenant_id, vendor_id, phrasing_hash);

CREATE TRIGGER trg_fired_probe_records_append_only
    BEFORE UPDATE OR DELETE ON canary.fired_probe_records
    FOR EACH ROW EXECUTE FUNCTION canary.reject_mutation();
