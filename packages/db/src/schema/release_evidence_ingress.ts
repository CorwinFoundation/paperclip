import { index, integer, jsonb, pgTable, text, timestamp, uniqueIndex, uuid } from "drizzle-orm/pg-core";
import { assets } from "./assets.js";
import { companies } from "./companies.js";
import { issueAttachments } from "./issue_attachments.js";
import { issues } from "./issues.js";

export const releaseEvidenceGrants = pgTable(
  "release_evidence_grants",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    companyId: uuid("company_id").notNull().references(() => companies.id, { onDelete: "cascade" }),
    revision: integer("revision").notNull().default(1),
    repository: text("repository").notNull(),
    repositoryId: text("repository_id"),
    workflowRef: text("workflow_ref").notNull(),
    jobWorkflowRef: text("job_workflow_ref"),
    allowedIssueIds: jsonb("allowed_issue_ids").$type<string[]>().notNull(),
    sourceSha: text("source_sha").notNull(),
    sequence: integer("sequence").notNull(),
    environment: text("environment").notNull(),
    maxUploadBytes: integer("max_upload_bytes").notNull(),
    allowedEventName: text("allowed_event_name").notNull().default("workflow_dispatch"),
    status: text("status").notNull().default("active"),
    expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
    revokedAt: timestamp("revoked_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => ({
    companyStatusIdx: index("release_evidence_grants_company_status_idx").on(table.companyId, table.status),
    revisionUq: uniqueIndex("release_evidence_grants_revision_uq").on(table.id, table.revision),
  }),
);

export const releaseEvidenceSessions = pgTable(
  "release_evidence_sessions",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    companyId: uuid("company_id").notNull().references(() => companies.id, { onDelete: "cascade" }),
    grantId: uuid("grant_id").notNull().references(() => releaseEvidenceGrants.id, { onDelete: "cascade" }),
    grantRevision: integer("grant_revision").notNull(),
    issueId: uuid("issue_id").notNull().references(() => issues.id, { onDelete: "restrict" }),
    capabilityHash: text("capability_hash").notNull(),
    clientNonceHash: text("client_nonce_hash").notNull(),
    repository: text("repository").notNull(),
    repositoryId: text("repository_id").notNull(),
    workflowRef: text("workflow_ref").notNull(),
    jobWorkflowRef: text("job_workflow_ref").notNull(),
    sourceSha: text("source_sha").notNull(),
    runId: text("run_id").notNull(),
    runAttempt: text("run_attempt").notNull(),
    eventName: text("event_name").notNull(),
    ref: text("ref").notNull(),
    actorId: text("actor_id").notNull(),
    sequence: integer("sequence").notNull(),
    environment: text("environment").notNull(),
    imageDigest: text("image_digest").notNull(),
    bundleSha256: text("bundle_sha256").notNull(),
    bundleBytes: integer("bundle_bytes").notNull(),
    status: text("status").notNull().default("issued"),
    denialReason: text("denial_reason"),
    attachmentId: uuid("attachment_id").references(() => issueAttachments.id, { onDelete: "restrict" }),
    assetId: uuid("asset_id").references(() => assets.id, { onDelete: "restrict" }),
    expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
    consumedAt: timestamp("consumed_at", { withTimezone: true }),
    revokedAt: timestamp("revoked_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => ({
    capabilityHashUq: uniqueIndex("release_evidence_sessions_capability_hash_uq").on(table.capabilityHash),
    runAttemptUq: uniqueIndex("release_evidence_sessions_run_attempt_uq").on(
      table.grantId,
      table.runId,
      table.runAttempt,
    ),
    evidenceTupleUq: uniqueIndex("release_evidence_sessions_evidence_tuple_uq").on(
      table.issueId,
      table.sourceSha,
      table.sequence,
      table.environment,
      table.imageDigest,
      table.bundleSha256,
    ),
    companyCreatedIdx: index("release_evidence_sessions_company_created_idx").on(table.companyId, table.createdAt),
    grantStatusIdx: index("release_evidence_sessions_grant_status_idx").on(table.grantId, table.status),
  }),
);

export const releaseEvidenceAuditEvents = pgTable(
  "release_evidence_audit_events",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    companyId: uuid("company_id").references(() => companies.id, { onDelete: "cascade" }),
    grantId: uuid("grant_id").references(() => releaseEvidenceGrants.id, { onDelete: "set null" }),
    sessionId: uuid("session_id").references(() => releaseEvidenceSessions.id, { onDelete: "set null" }),
    issueId: uuid("issue_id").references(() => issues.id, { onDelete: "set null" }),
    attachmentId: uuid("attachment_id").references(() => issueAttachments.id, { onDelete: "set null" }),
    eventType: text("event_type").notNull(),
    result: text("result").notNull(),
    denialReason: text("denial_reason"),
    details: jsonb("details").$type<Record<string, unknown>>().notNull().default({}),
    redacted: text("redacted").notNull().default("true"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => ({
    companyCreatedIdx: index("release_evidence_audit_company_created_idx").on(table.companyId, table.createdAt),
    sessionIdx: index("release_evidence_audit_session_idx").on(table.sessionId),
    grantIdx: index("release_evidence_audit_grant_idx").on(table.grantId),
  }),
);
