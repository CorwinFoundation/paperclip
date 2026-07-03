-- BEAAA-11372 Determination Engine v1 — append-only determination audit spine.
--
-- The book of record for covered-peril determinations. Mirrors the append-only
-- semantics of the evidence store (BEAAA-11366 / BEAAA-11369): rows are only ever
-- inserted, never updated or deleted in place. Corrections are new inserts that
-- supersede a prior determination within the same tenant/agent lineage. Reviewer
-- rulings are appended to determination_review_rulings, never edited into the
-- determination row.
--
-- This is additive runtime-adjudication infrastructure under
-- are-scanner-api-v1@v1.3.0. It changes no BackBond Score math, rubric weight,
-- judge model, or /scanner/v1/scans response shape.

CREATE SCHEMA IF NOT EXISTS determination;

-- Shared append-only guard reused by every book-of-record table below.
CREATE OR REPLACE FUNCTION determination.reject_mutation()
RETURNS trigger AS $$
BEGIN
    IF (TG_OP = 'UPDATE') THEN
        RAISE EXCEPTION 'determination_append_only_update_forbidden'
            USING ERRCODE = 'integrity_constraint_violation';
    ELSE
        RAISE EXCEPTION 'determination_append_only_delete_forbidden'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS determination.determination_records (
    determination_row_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    determination_id          text NOT NULL,
    determination_preimage_sha256 text NOT NULL
        CHECK (determination_preimage_sha256 ~ '^[0-9a-f]{64}$'),
    rule_set_version          text NOT NULL,
    engine_version            text NOT NULL,
    peril                     text,
    triggered_rule_id         text,
    final_decision            text NOT NULL,
    review_required           boolean NOT NULL,
    review_owner              text NOT NULL,
    -- Tenant/agent lineage: determinations are tenant-isolated exactly like the
    -- evidence packets they read.
    tenant_id_hash            text NOT NULL,
    agent_id                  text NOT NULL,
    agent_version             text NOT NULL,
    -- Reference back to the immutable evidence packet + its content hashes.
    evidence_batch_id         text NOT NULL,
    manifest_content_sha256   text NOT NULL,
    evidence_packet_ref       jsonb NOT NULL,
    trigger                   jsonb NOT NULL,
    source_spans              jsonb NOT NULL,
    content_hashes            jsonb NOT NULL,
    policy_engine_check       jsonb NOT NULL,
    audit                     jsonb NOT NULL,
    -- Advisory LLM triage output is stored for reviewer context only; it is
    -- excluded from determination_preimage_sha256 by construction.
    advisory_triage           jsonb,
    -- Corrections: a new row may supersede a prior determination row.
    supersedes_determination_row_id uuid
        REFERENCES determination.determination_records (determination_row_id),
    correction_reason         text,
    created_at                timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT determination_correction_requires_reason
        CHECK (supersedes_determination_row_id IS NULL OR correction_reason IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_determination_records_determination_id
    ON determination.determination_records (determination_id);
CREATE INDEX IF NOT EXISTS ix_determination_records_tenant_agent
    ON determination.determination_records (tenant_id_hash, agent_id, agent_version);
CREATE INDEX IF NOT EXISTS ix_determination_records_batch
    ON determination.determination_records (evidence_batch_id);

-- At most one direct correcting child per determination row (single-head chain),
-- matching the evidence-store one-direct-child rule.
CREATE UNIQUE INDEX IF NOT EXISTS ux_determination_records_one_direct_correction
    ON determination.determination_records (supersedes_determination_row_id)
    WHERE supersedes_determination_row_id IS NOT NULL;

-- Current-head view: hides superseded determinations from current-state reads.
CREATE OR REPLACE VIEW determination.latest_determination_records AS
    SELECT record.*
    FROM determination.determination_records AS record
    WHERE NOT EXISTS (
        SELECT 1
        FROM determination.determination_records AS correction
        WHERE correction.supersedes_determination_row_id = record.determination_row_id
    );

-- Reviewer rulings are appended here; the determination row is never mutated.
CREATE TABLE IF NOT EXISTS determination.determination_review_rulings (
    ruling_row_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    determination_id     text NOT NULL,
    determination_row_id uuid NOT NULL
        REFERENCES determination.determination_records (determination_row_id),
    reviewer             text NOT NULL,
    ruling               text NOT NULL,
    ruling_detail        jsonb,
    ruled_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_determination_review_rulings_determination_id
    ON determination.determination_review_rulings (determination_id);

CREATE TRIGGER trg_determination_records_append_only
    BEFORE UPDATE OR DELETE ON determination.determination_records
    FOR EACH ROW EXECUTE FUNCTION determination.reject_mutation();

CREATE TRIGGER trg_determination_review_rulings_append_only
    BEFORE UPDATE OR DELETE ON determination.determination_review_rulings
    FOR EACH ROW EXECUTE FUNCTION determination.reject_mutation();
