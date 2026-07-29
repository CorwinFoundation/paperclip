-- BEAAA-19052. Provision once on the durable shared PostgreSQL primary.
-- Replace both bootstrap placeholders in the same controlled operation.
CREATE TABLE backbond_promotion_lease_authority_identity (
    singleton boolean PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    storage_identity text NOT NULL UNIQUE,
    continuity_epoch text NOT NULL,
    schema_version integer NOT NULL CHECK (schema_version = 1)
);

CREATE TABLE backbond_promotion_lease_consumptions (
    scope_sha256 text PRIMARY KEY,
    lease_id text NOT NULL UNIQUE,
    signature text NOT NULL,
    commit_sha text NOT NULL,
    image_ref text NOT NULL,
    tenant_id text NOT NULL,
    environment text NOT NULL CHECK (environment = 'production'),
    consumed_at timestamptz NOT NULL,
    continuity_epoch text NOT NULL
);

INSERT INTO backbond_promotion_lease_authority_identity
    (singleton, storage_identity, continuity_epoch, schema_version)
VALUES
    (TRUE, 'REPLACE_WITH_RANDOM_PRODUCTION_STORAGE_IDENTITY',
     'REPLACE_ON_EVERY_RECOVERY_OR_RESTORE', 1);
