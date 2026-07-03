-- BEAAA-11366 Evidence Store v1.
-- Rule-execution: additive Postgres evidence book of record; no BackBond
-- Scanner score math, rubric, judge, or API response-shape changes.

CREATE SCHEMA IF NOT EXISTS evidence_store;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS evidence_store.retention_config (
    retention_policy_key      TEXT PRIMARY KEY,
    retention_status          TEXT NOT NULL
        CHECK (retention_status IN ('pending_counsel_guidance', 'active')),
    retention_period          INTERVAL,
    legal_hold_after          INTERVAL,
    notes                     TEXT NOT NULL,
    inserted_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO evidence_store.retention_config (
    retention_policy_key,
    retention_status,
    retention_period,
    legal_hold_after,
    notes
) VALUES (
    'runtime_evidence_counsel_pending_v1',
    'pending_counsel_guidance',
    NULL,
    NULL,
    'Stub only. Counsel-directed retention values are intentionally unset for BEAAA-11366 v1.'
) ON CONFLICT (retention_policy_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS evidence_store.signed_batch_manifests (
    evidence_batch_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id_hash                TEXT NOT NULL,
    tenant_identity               JSONB NOT NULL,
    agent_id                      TEXT NOT NULL,
    agent_version                 TEXT NOT NULL,
    scanner_version               TEXT NOT NULL,
    corpus_version                TEXT NOT NULL,
    probe_set_version             TEXT NOT NULL,
    collector_version             TEXT NOT NULL,
    signed_manifest               JSONB NOT NULL,
    manifest_signature_algorithm  TEXT NOT NULL,
    manifest_signature            TEXT NOT NULL,
    manifest_content_sha256       TEXT NOT NULL
        CHECK (manifest_content_sha256 ~ '^sha256:[0-9a-f]{64}$'),
    consent_metadata              JSONB NOT NULL DEFAULT '{}'::jsonb,
    redaction_metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    retention_policy_key          TEXT NOT NULL
        REFERENCES evidence_store.retention_config (retention_policy_key),
    retention_expires_at          TIMESTAMPTZ,
    supersedes_evidence_batch_id  UUID
        REFERENCES evidence_store.signed_batch_manifests (evidence_batch_id),
    correction_reason             TEXT,
    received_at                   TIMESTAMPTZ NOT NULL,
    inserted_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (supersedes_evidence_batch_id IS NULL AND correction_reason IS NULL)
        OR
        (supersedes_evidence_batch_id IS NOT NULL AND correction_reason IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS evidence_store.structured_event_summaries (
    evidence_event_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_batch_id             UUID NOT NULL
        REFERENCES evidence_store.signed_batch_manifests (evidence_batch_id),
    tenant_id_hash                TEXT NOT NULL,
    agent_id                      TEXT NOT NULL,
    agent_version                 TEXT NOT NULL,
    collector_version             TEXT NOT NULL,
    event_kind                    TEXT NOT NULL,
    event_timestamp               TIMESTAMPTZ NOT NULL,
    rubric_point_id               TEXT,
    summary                       JSONB NOT NULL,
    content_sha256                TEXT NOT NULL
        CHECK (content_sha256 ~ '^sha256:[0-9a-f]{64}$'),
    consent_metadata              JSONB NOT NULL DEFAULT '{}'::jsonb,
    redaction_metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    supersedes_evidence_event_id  UUID
        REFERENCES evidence_store.structured_event_summaries (evidence_event_id),
    correction_reason             TEXT,
    inserted_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (supersedes_evidence_event_id IS NULL AND correction_reason IS NULL)
        OR
        (supersedes_evidence_event_id IS NOT NULL AND correction_reason IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS evidence_store.evidence_content_hashes (
    content_hash_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_event_id     UUID NOT NULL
        REFERENCES evidence_store.structured_event_summaries (evidence_event_id),
    tenant_id_hash        TEXT NOT NULL,
    hash_algorithm        TEXT NOT NULL DEFAULT 'sha256'
        CHECK (hash_algorithm = 'sha256'),
    content_sha256        TEXT NOT NULL
        CHECK (content_sha256 ~ '^sha256:[0-9a-f]{64}$'),
    content_uri           TEXT NOT NULL,
    byte_length           BIGINT CHECK (byte_length IS NULL OR byte_length >= 0),
    media_type            TEXT,
    redaction_metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    inserted_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (evidence_event_id, content_sha256)
);

CREATE INDEX IF NOT EXISTS ix_evidence_batches_tenant_agent_received
    ON evidence_store.signed_batch_manifests
    (tenant_id_hash, agent_id, received_at);
CREATE INDEX IF NOT EXISTS ix_evidence_batches_supersedes
    ON evidence_store.signed_batch_manifests
    (supersedes_evidence_batch_id)
    WHERE supersedes_evidence_batch_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_evidence_batches_one_direct_correction
    ON evidence_store.signed_batch_manifests
    (supersedes_evidence_batch_id)
    WHERE supersedes_evidence_batch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_evidence_events_tenant_agent_event
    ON evidence_store.structured_event_summaries
    (tenant_id_hash, agent_id, event_timestamp);
CREATE INDEX IF NOT EXISTS ix_evidence_events_rubric_point
    ON evidence_store.structured_event_summaries
    (rubric_point_id)
    WHERE rubric_point_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_evidence_events_supersedes
    ON evidence_store.structured_event_summaries
    (supersedes_evidence_event_id)
    WHERE supersedes_evidence_event_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_evidence_events_one_direct_correction
    ON evidence_store.structured_event_summaries
    (supersedes_evidence_event_id)
    WHERE supersedes_evidence_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_evidence_content_hashes_sha256
    ON evidence_store.evidence_content_hashes (content_sha256);

CREATE OR REPLACE FUNCTION evidence_store.reject_append_only_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'evidence_store_append_only_%_forbidden', lower(TG_OP)
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

CREATE OR REPLACE FUNCTION evidence_store.validate_signed_batch_supersession()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    superseded evidence_store.signed_batch_manifests%ROWTYPE;
BEGIN
    IF NEW.supersedes_evidence_batch_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT *
    INTO superseded
    FROM evidence_store.signed_batch_manifests
    WHERE evidence_batch_id = NEW.supersedes_evidence_batch_id;

    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    IF NEW.tenant_id_hash IS DISTINCT FROM superseded.tenant_id_hash
        OR NEW.agent_id IS DISTINCT FROM superseded.agent_id
        OR NEW.agent_version IS DISTINCT FROM superseded.agent_version THEN
        RAISE EXCEPTION 'evidence_store_batch_supersession_lineage_mismatch'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM evidence_store.signed_batch_manifests AS sibling
        WHERE sibling.supersedes_evidence_batch_id = NEW.supersedes_evidence_batch_id
    ) THEN
        RAISE EXCEPTION 'evidence_store_batch_supersession_multiple_children_forbidden'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION evidence_store.validate_structured_event_lineage()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    parent_batch evidence_store.signed_batch_manifests%ROWTYPE;
    superseded_event evidence_store.structured_event_summaries%ROWTYPE;
BEGIN
    SELECT *
    INTO parent_batch
    FROM evidence_store.signed_batch_manifests
    WHERE evidence_batch_id = NEW.evidence_batch_id;

    IF FOUND AND (
        NEW.tenant_id_hash IS DISTINCT FROM parent_batch.tenant_id_hash
        OR NEW.agent_id IS DISTINCT FROM parent_batch.agent_id
        OR NEW.agent_version IS DISTINCT FROM parent_batch.agent_version
    ) THEN
        RAISE EXCEPTION 'evidence_store_event_batch_lineage_mismatch'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NEW.supersedes_evidence_event_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT *
    INTO superseded_event
    FROM evidence_store.structured_event_summaries
    WHERE evidence_event_id = NEW.supersedes_evidence_event_id;

    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    IF NEW.tenant_id_hash IS DISTINCT FROM superseded_event.tenant_id_hash
        OR NEW.agent_id IS DISTINCT FROM superseded_event.agent_id
        OR NEW.agent_version IS DISTINCT FROM superseded_event.agent_version
        OR NEW.evidence_batch_id IS DISTINCT FROM superseded_event.evidence_batch_id THEN
        RAISE EXCEPTION 'evidence_store_event_supersession_lineage_mismatch'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM evidence_store.structured_event_summaries AS sibling
        WHERE sibling.supersedes_evidence_event_id = NEW.supersedes_evidence_event_id
    ) THEN
        RAISE EXCEPTION 'evidence_store_event_supersession_multiple_children_forbidden'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER evidence_store_no_update_retention_config
BEFORE UPDATE ON evidence_store.retention_config
FOR EACH ROW EXECUTE FUNCTION evidence_store.reject_append_only_mutation();
CREATE TRIGGER evidence_store_no_delete_retention_config
BEFORE DELETE ON evidence_store.retention_config
FOR EACH ROW EXECUTE FUNCTION evidence_store.reject_append_only_mutation();

CREATE TRIGGER evidence_store_no_update_signed_batch_manifests
BEFORE UPDATE ON evidence_store.signed_batch_manifests
FOR EACH ROW EXECUTE FUNCTION evidence_store.reject_append_only_mutation();
CREATE TRIGGER evidence_store_no_delete_signed_batch_manifests
BEFORE DELETE ON evidence_store.signed_batch_manifests
FOR EACH ROW EXECUTE FUNCTION evidence_store.reject_append_only_mutation();
CREATE TRIGGER evidence_store_validate_signed_batch_supersession
BEFORE INSERT ON evidence_store.signed_batch_manifests
FOR EACH ROW EXECUTE FUNCTION evidence_store.validate_signed_batch_supersession();

CREATE TRIGGER evidence_store_no_update_structured_event_summaries
BEFORE UPDATE ON evidence_store.structured_event_summaries
FOR EACH ROW EXECUTE FUNCTION evidence_store.reject_append_only_mutation();
CREATE TRIGGER evidence_store_no_delete_structured_event_summaries
BEFORE DELETE ON evidence_store.structured_event_summaries
FOR EACH ROW EXECUTE FUNCTION evidence_store.reject_append_only_mutation();
CREATE TRIGGER evidence_store_validate_structured_event_lineage
BEFORE INSERT ON evidence_store.structured_event_summaries
FOR EACH ROW EXECUTE FUNCTION evidence_store.validate_structured_event_lineage();

CREATE TRIGGER evidence_store_no_update_evidence_content_hashes
BEFORE UPDATE ON evidence_store.evidence_content_hashes
FOR EACH ROW EXECUTE FUNCTION evidence_store.reject_append_only_mutation();
CREATE TRIGGER evidence_store_no_delete_evidence_content_hashes
BEFORE DELETE ON evidence_store.evidence_content_hashes
FOR EACH ROW EXECUTE FUNCTION evidence_store.reject_append_only_mutation();

CREATE OR REPLACE VIEW evidence_store.latest_signed_batch_manifests AS
SELECT batch.*
FROM evidence_store.signed_batch_manifests AS batch
WHERE NOT EXISTS (
    SELECT 1
    FROM evidence_store.signed_batch_manifests AS correction
    WHERE correction.supersedes_evidence_batch_id = batch.evidence_batch_id
);

CREATE OR REPLACE VIEW evidence_store.latest_structured_event_summaries AS
SELECT event.*
FROM evidence_store.structured_event_summaries AS event
WHERE NOT EXISTS (
    SELECT 1
    FROM evidence_store.structured_event_summaries AS correction
    WHERE correction.supersedes_evidence_event_id = event.evidence_event_id
);

CREATE OR REPLACE VIEW evidence_store.exposure_meterable_events_v1 AS
SELECT
    event.evidence_event_id,
    event.evidence_batch_id,
    event.tenant_id_hash AS tenant_id,
    event.agent_id,
    COALESCE(
        NULLIF(event.summary ->> 'action_class', ''),
        NULLIF(event.summary #>> '{attributes,backbond.evidence.action_class}', ''),
        event.event_kind
    ) AS action_class,
    date_trunc('month', event.event_timestamp)::date AS accounting_period_start,
    (date_trunc('month', event.event_timestamp)::date + INTERVAL '1 month')::date
        AS accounting_period_end,
    event.event_timestamp,
    event.content_sha256
FROM evidence_store.latest_structured_event_summaries AS event;

CREATE OR REPLACE VIEW evidence_store.exposure_metering_v1 AS
WITH event_counts AS (
    SELECT
        tenant_id,
        agent_id,
        action_class,
        accounting_period_start,
        accounting_period_end,
        count(*)::bigint AS covered_action_count,
        count(*)::bigint AS source_evidence_row_count,
        array_agg(evidence_event_id ORDER BY event_timestamp, evidence_event_id) AS evidence_event_ids
    FROM evidence_store.exposure_meterable_events_v1
    GROUP BY
        tenant_id,
        agent_id,
        action_class,
        accounting_period_start,
        accounting_period_end
),
period_bounds AS (
    SELECT
        tenant_id,
        agent_id,
        action_class,
        min(accounting_period_start) AS first_period,
        max(accounting_period_start) AS last_period
    FROM evidence_store.exposure_meterable_events_v1
    GROUP BY tenant_id, agent_id, action_class
),
expected_periods AS (
    SELECT
        bounds.tenant_id,
        bounds.agent_id,
        bounds.action_class,
        generated_period::date AS accounting_period_start,
        (generated_period::date + INTERVAL '1 month')::date AS accounting_period_end
    FROM period_bounds AS bounds
    CROSS JOIN LATERAL generate_series(
        bounds.first_period,
        bounds.last_period,
        INTERVAL '1 month'
    ) AS generated_period
)
SELECT
    expected.tenant_id,
    expected.agent_id,
    expected.action_class,
    expected.accounting_period_start,
    expected.accounting_period_end,
    CASE
        WHEN counts.covered_action_count IS NULL THEN 'unmetered'
        ELSE 'metered'
    END AS metering_status,
    counts.covered_action_count,
    COALESCE(counts.source_evidence_row_count, 0)::bigint AS source_evidence_row_count,
    COALESCE(counts.evidence_event_ids, ARRAY[]::uuid[]) AS evidence_event_ids
FROM expected_periods AS expected
LEFT JOIN event_counts AS counts
    ON counts.tenant_id = expected.tenant_id
    AND counts.agent_id = expected.agent_id
    AND counts.action_class = expected.action_class
    AND counts.accounting_period_start = expected.accounting_period_start;
