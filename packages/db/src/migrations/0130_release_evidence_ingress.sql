CREATE TABLE "release_evidence_grants" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "company_id" uuid NOT NULL REFERENCES "companies"("id") ON DELETE CASCADE,
  "revision" integer DEFAULT 1 NOT NULL,
  "repository" text NOT NULL,
  "repository_id" text,
  "workflow_ref" text NOT NULL,
  "job_workflow_ref" text,
  "allowed_issue_ids" jsonb NOT NULL,
  "source_sha" text NOT NULL,
  "sequence" integer NOT NULL,
  "environment" text NOT NULL,
  "max_upload_bytes" integer NOT NULL,
  "allowed_event_name" text DEFAULT 'workflow_dispatch' NOT NULL,
  "status" text DEFAULT 'active' NOT NULL,
  "expires_at" timestamp with time zone NOT NULL,
  "revoked_at" timestamp with time zone,
  "created_at" timestamp with time zone DEFAULT now() NOT NULL,
  "updated_at" timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE "release_evidence_sessions" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "company_id" uuid NOT NULL REFERENCES "companies"("id") ON DELETE CASCADE,
  "grant_id" uuid NOT NULL REFERENCES "release_evidence_grants"("id") ON DELETE CASCADE,
  "grant_revision" integer NOT NULL,
  "issue_id" uuid NOT NULL REFERENCES "issues"("id") ON DELETE RESTRICT,
  "capability_hash" text NOT NULL,
  "client_nonce_hash" text NOT NULL,
  "repository" text NOT NULL,
  "repository_id" text NOT NULL,
  "workflow_ref" text NOT NULL,
  "job_workflow_ref" text NOT NULL,
  "source_sha" text NOT NULL,
  "run_id" text NOT NULL,
  "run_attempt" text NOT NULL,
  "event_name" text NOT NULL,
  "ref" text NOT NULL,
  "actor_id" text NOT NULL,
  "sequence" integer NOT NULL,
  "environment" text NOT NULL,
  "image_digest" text NOT NULL,
  "bundle_sha256" text NOT NULL,
  "bundle_bytes" integer NOT NULL,
  "status" text DEFAULT 'issued' NOT NULL,
  "denial_reason" text,
  "attachment_id" uuid REFERENCES "issue_attachments"("id") ON DELETE RESTRICT,
  "asset_id" uuid REFERENCES "assets"("id") ON DELETE RESTRICT,
  "expires_at" timestamp with time zone NOT NULL,
  "consumed_at" timestamp with time zone,
  "revoked_at" timestamp with time zone,
  "created_at" timestamp with time zone DEFAULT now() NOT NULL,
  "updated_at" timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE "release_evidence_audit_events" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "company_id" uuid REFERENCES "companies"("id") ON DELETE CASCADE,
  "grant_id" uuid REFERENCES "release_evidence_grants"("id") ON DELETE SET NULL,
  "session_id" uuid REFERENCES "release_evidence_sessions"("id") ON DELETE SET NULL,
  "issue_id" uuid REFERENCES "issues"("id") ON DELETE SET NULL,
  "attachment_id" uuid REFERENCES "issue_attachments"("id") ON DELETE SET NULL,
  "event_type" text NOT NULL,
  "result" text NOT NULL,
  "denial_reason" text,
  "details" jsonb DEFAULT '{}'::jsonb NOT NULL,
  "redacted" text DEFAULT 'true' NOT NULL,
  "created_at" timestamp with time zone DEFAULT now() NOT NULL
);

CREATE INDEX "release_evidence_grants_company_status_idx"
  ON "release_evidence_grants" ("company_id", "status");
CREATE UNIQUE INDEX "release_evidence_grants_revision_uq"
  ON "release_evidence_grants" ("id", "revision");
CREATE UNIQUE INDEX "release_evidence_sessions_capability_hash_uq"
  ON "release_evidence_sessions" ("capability_hash");
CREATE UNIQUE INDEX "release_evidence_sessions_run_attempt_uq"
  ON "release_evidence_sessions" ("grant_id", "run_id", "run_attempt");
CREATE UNIQUE INDEX "release_evidence_sessions_evidence_tuple_uq"
  ON "release_evidence_sessions" ("issue_id", "source_sha", "sequence", "environment", "image_digest", "bundle_sha256");
CREATE INDEX "release_evidence_sessions_company_created_idx"
  ON "release_evidence_sessions" ("company_id", "created_at");
CREATE INDEX "release_evidence_sessions_grant_status_idx"
  ON "release_evidence_sessions" ("grant_id", "status");
CREATE INDEX "release_evidence_audit_company_created_idx"
  ON "release_evidence_audit_events" ("company_id", "created_at");
CREATE INDEX "release_evidence_audit_session_idx"
  ON "release_evidence_audit_events" ("session_id");
CREATE INDEX "release_evidence_audit_grant_idx"
  ON "release_evidence_audit_events" ("grant_id");
