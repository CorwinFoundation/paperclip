CREATE UNIQUE INDEX IF NOT EXISTS "release_evidence_grants_active_issue_scope_uq"
  ON "release_evidence_grants" ("company_id", ((allowed_issue_ids->>0)), "sequence", "environment")
  WHERE "status" = 'active';
