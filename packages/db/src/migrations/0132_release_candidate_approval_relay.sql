-- Release-candidate approval relay tables for scanner-ship deployment handoff.
CREATE TABLE IF NOT EXISTS "release_candidates" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "company_id" uuid NOT NULL REFERENCES "companies"("id"),
  "source_issue_id" uuid NOT NULL REFERENCES "issues"("id") ON DELETE RESTRICT,
  "created_by_agent_id" uuid REFERENCES "agents"("id") ON DELETE SET NULL,
  "created_by_run_id" uuid REFERENCES "heartbeat_runs"("id") ON DELETE SET NULL,
  "commit_sha" text NOT NULL,
  "image_digest" text NOT NULL,
  "signature_bundle_ref" text NOT NULL,
  "signature_bundle_sha256" text NOT NULL,
  "provenance_ref" text NOT NULL,
  "sbom_hash" text NOT NULL,
  "workflow_run_url" text NOT NULL,
  "environment" text NOT NULL,
  "target_host" text NOT NULL,
  "sequence" integer NOT NULL,
  "document_revision_id" text,
  "status" text DEFAULT 'candidate_created' NOT NULL,
  "approval_interaction_id" uuid REFERENCES "issue_thread_interactions"("id") ON DELETE RESTRICT,
  "approved_by_user_id" text,
  "approved_at" timestamp with time zone,
  "staged_artifact_asset_id" uuid REFERENCES "assets"("id") ON DELETE SET NULL,
  "staged_artifact_sha256" text,
  "staged_signature_bundle_asset_id" uuid REFERENCES "assets"("id") ON DELETE SET NULL,
  "staged_signature_bundle_sha256" text,
  "staged_at" timestamp with time zone,
  "metadata" jsonb DEFAULT '{}'::jsonb NOT NULL,
  "created_at" timestamp with time zone DEFAULT now() NOT NULL,
  "updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "release_deploy_authorizations" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "company_id" uuid NOT NULL REFERENCES "companies"("id"),
  "candidate_id" uuid NOT NULL REFERENCES "release_candidates"("id") ON DELETE RESTRICT,
  "approval_interaction_id" uuid NOT NULL REFERENCES "issue_thread_interactions"("id") ON DELETE RESTRICT,
  "token_hash" text NOT NULL,
  "token_prefix" text NOT NULL,
  "target_host" text NOT NULL,
  "image_digest" text NOT NULL,
  "environment" text NOT NULL,
  "sequence" integer NOT NULL,
  "expires_at" timestamp with time zone NOT NULL,
  "used_at" timestamp with time zone,
  "lease_artifact_asset_id" uuid REFERENCES "assets"("id") ON DELETE SET NULL,
  "lease_signature_bundle_asset_id" uuid REFERENCES "assets"("id") ON DELETE SET NULL,
  "lease_issued_at" timestamp with time zone,
  "created_by_user_id" text,
  "created_at" timestamp with time zone DEFAULT now() NOT NULL,
  "updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "release_candidate_audit_events" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "company_id" uuid NOT NULL REFERENCES "companies"("id"),
  "candidate_id" uuid NOT NULL REFERENCES "release_candidates"("id") ON DELETE RESTRICT,
  "authorization_id" uuid REFERENCES "release_deploy_authorizations"("id") ON DELETE SET NULL,
  "issue_id" uuid REFERENCES "issues"("id") ON DELETE SET NULL,
  "actor_agent_id" uuid REFERENCES "agents"("id") ON DELETE SET NULL,
  "actor_user_id" text,
  "event_type" text NOT NULL,
  "payload" jsonb DEFAULT '{}'::jsonb NOT NULL,
  "redacted" boolean DEFAULT true NOT NULL,
  "created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "release_candidates_company_created_idx"
  ON "release_candidates" ("company_id", "created_at");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "release_candidates_source_issue_idx"
  ON "release_candidates" ("source_issue_id");
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "release_candidates_company_target_sequence_uq"
  ON "release_candidates" ("company_id", "environment", "target_host", "sequence");
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "release_candidates_company_digest_uq"
  ON "release_candidates" ("company_id", "image_digest");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "release_candidates_approval_interaction_idx"
  ON "release_candidates" ("approval_interaction_id")
  WHERE "approval_interaction_id" IS NOT NULL;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "release_deploy_authorizations_company_candidate_idx"
  ON "release_deploy_authorizations" ("company_id", "candidate_id");
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "release_deploy_authorizations_candidate_approval_interaction_uq"
  ON "release_deploy_authorizations" ("candidate_id", "approval_interaction_id");
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "release_deploy_authorizations_token_hash_uq"
  ON "release_deploy_authorizations" ("token_hash");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "release_candidate_audit_events_candidate_created_idx"
  ON "release_candidate_audit_events" ("candidate_id", "created_at");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "release_candidate_audit_events_company_created_idx"
  ON "release_candidate_audit_events" ("company_id", "created_at");
