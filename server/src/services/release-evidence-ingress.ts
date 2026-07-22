import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { and, eq, inArray } from "drizzle-orm";
import type { Db } from "@paperclipai/db";
import {
  assets,
  issueAttachments,
  issues,
  releaseEvidenceAuditEvents,
  releaseEvidenceGrants,
  releaseEvidenceSessions,
} from "@paperclipai/db";
import { conflict, HttpError, notFound, unauthorized } from "../errors.js";
import type { PutFileResult } from "../storage/types.js";

const CAPABILITY_BYTES = 32;
const CAPABILITY_TTL_MS = 5 * 60 * 1000;

export type ReleaseEvidenceOidcClaims = {
  iss: string;
  aud: string | string[];
  repository: string;
  repository_id: string;
  workflow_ref: string;
  workflow_sha: string;
  job_workflow_ref?: string;
  job_workflow_sha?: string;
  sha: string;
  run_id: string;
  run_attempt: string;
  event_name: string;
  ref: string;
  actor_id: string;
};

export type ProvisionReleaseEvidenceGrantInput = {
  companyId: string;
  repository: string;
  repositoryId?: string | null;
  workflowRef: string;
  workflowSha: string;
  jobWorkflowRef?: string | null;
  jobWorkflowSha?: string | null;
  triggerRef: string;
  issueId: string;
  sourceSha: string;
  sequence: number;
  environment: string;
  maxUploadBytes: number;
  allowedEventName?: string;
  expiresAt: Date;
  dryRun?: boolean;
  actor?: { type: string; id?: string | null };
};

export type ExchangeReleaseEvidenceInput = {
  grantId: string;
  issueId: string;
  sourceSha: string;
  workflowSha: string;
  sequence: number;
  environment: string;
  imageDigest: string;
  bundleSha256: string;
  bundleBytes: number;
  clientNonce: string;
};

export type PreparedReleaseEvidenceUpload =
  | { kind: "new"; session: typeof releaseEvidenceSessions.$inferSelect }
  | { kind: "existing"; attachmentId: string };

function sha256(value: string | Buffer) {
  return createHash("sha256").update(value).digest("hex");
}

function normalizeSha256(value: string) {
  return value.replace(/^sha256:/i, "").toLowerCase();
}

function generateCapability() {
  const secret = `pcrel_${randomBytes(CAPABILITY_BYTES).toString("base64url")}`;
  return { secret, hash: sha256(secret) };
}

function constantTimeHashEquals(left: string, right: string) {
  const leftBuffer = Buffer.from(left, "hex");
  const rightBuffer = Buffer.from(right, "hex");
  return leftBuffer.length === rightBuffer.length && timingSafeEqual(leftBuffer, rightBuffer);
}

function hasAudience(claim: string | string[], expected: string) {
  return Array.isArray(claim) ? claim.includes(expected) : claim === expected;
}

function assertEqual(actual: unknown, expected: unknown, code: string) {
  if (actual !== expected) throw forbiddenWithCode("Release evidence exchange denied", code);
}

function forbiddenWithCode(message: string, code: string) {
  return new HttpError(403, message, { code });
}

function sanitizedClaims(claims?: Partial<ReleaseEvidenceOidcClaims>) {
  if (!claims) return {};
  return {
    iss: claims.iss,
    aud: claims.aud,
    repository: claims.repository,
    repository_id: claims.repository_id,
    workflow_ref: claims.workflow_ref,
    workflow_sha: claims.workflow_sha,
    job_workflow_ref: claims.job_workflow_ref,
    job_workflow_sha: claims.job_workflow_sha,
    sha: claims.sha,
    run_id: claims.run_id,
    run_attempt: claims.run_attempt,
    event_name: claims.event_name,
    ref: claims.ref,
    actor_id: claims.actor_id,
  };
}

function sanitizedRequest(request?: Record<string, unknown>) {
  if (!request) return {};
  return {
    grantId: request.grantId,
    issueId: request.issueId,
    sourceSha: request.sourceSha,
    workflowSha: request.workflowSha,
    sequence: request.sequence,
    environment: request.environment,
    imageDigest: request.imageDigest,
    bundleSha256: request.bundleSha256,
    bundleBytes: request.bundleBytes,
    clientNonceHash: typeof request.clientNonce === "string" ? sha256(request.clientNonce) : undefined,
  };
}

export function releaseEvidenceIngressService(db: Db, opts: { now?: () => Date } = {}) {
  const now = opts.now ?? (() => new Date());

  async function appendAudit(args: {
    companyId?: string | null;
    grantId?: string | null;
    sessionId?: string | null;
    issueId?: string | null;
    attachmentId?: string | null;
    eventType: string;
    result: "accepted" | "denied";
    denialReason?: string | null;
    details?: Record<string, unknown>;
  }) {
    await db.insert(releaseEvidenceAuditEvents).values({
      companyId: args.companyId ?? null,
      grantId: args.grantId ?? null,
      sessionId: args.sessionId ?? null,
      issueId: args.issueId ?? null,
      attachmentId: args.attachmentId ?? null,
      eventType: args.eventType,
      result: args.result,
      denialReason: args.denialReason ?? null,
      details: args.details ?? {},
      redacted: "true",
    });
  }

  async function auditDenied(eventType: string, denialReason: string, context: {
    companyId?: string | null;
    grantId?: string | null;
    sessionId?: string | null;
    issueId?: string | null;
    claims?: Partial<ReleaseEvidenceOidcClaims>;
    request?: Record<string, unknown>;
  } = {}) {
    await appendAudit({
      companyId: context.companyId ?? null,
      grantId: context.grantId ?? null,
      sessionId: context.sessionId ?? null,
      issueId: context.issueId ?? null,
      eventType,
      result: "denied",
      denialReason,
      details: { claims: sanitizedClaims(context.claims), request: sanitizedRequest(context.request) },
    });
  }

  async function exchange(claims: ReleaseEvidenceOidcClaims, input: ExchangeReleaseEvidenceInput) {
    if (claims.iss !== "https://token.actions.githubusercontent.com") throw unauthorized("OIDC token issuer is not trusted");
    if (!hasAudience(claims.aud, "paperclip-release-evidence")) {
      throw forbiddenWithCode("Release evidence exchange denied", "audience_mismatch");
    }
    const grant = await db
      .select()
      .from(releaseEvidenceGrants)
      .where(eq(releaseEvidenceGrants.id, input.grantId))
      .then((rows) => rows[0] ?? null);
    if (!grant) throw notFound("Release evidence grant not found");
    const issue = await db.select().from(issues).where(eq(issues.id, input.issueId)).then((rows) => rows[0] ?? null);
    if (!issue || issue.companyId !== grant.companyId) {
      throw forbiddenWithCode("Release evidence exchange denied", "issue_company_mismatch");
    }
    const current = now();
    if (grant.status !== "active" || grant.revokedAt || grant.expiresAt <= current) {
      throw forbiddenWithCode("Release evidence exchange denied", "grant_inactive");
    }
    if (!Array.isArray(grant.allowedIssueIds) || !grant.allowedIssueIds.includes(input.issueId)) {
      throw forbiddenWithCode("Release evidence exchange denied", "issue_not_granted");
    }
    assertEqual(claims.repository, grant.repository, "repository_mismatch");
    if (grant.repositoryId) assertEqual(claims.repository_id, grant.repositoryId, "repository_id_mismatch");
    assertEqual(claims.workflow_ref, grant.workflowRef, "workflow_ref_mismatch");
    assertEqual(claims.workflow_sha, grant.workflowSha, "workflow_sha_mismatch");
    if (grant.jobWorkflowRef) assertEqual(claims.job_workflow_ref, grant.jobWorkflowRef, "job_workflow_ref_mismatch");
    if (grant.jobWorkflowSha) assertEqual(claims.job_workflow_sha, grant.jobWorkflowSha, "job_workflow_sha_mismatch");
    assertEqual(claims.ref, grant.triggerRef, "trigger_ref_mismatch");
    assertEqual(claims.event_name, grant.allowedEventName, "event_mismatch");
    if (claims.event_name === "pull_request" || claims.event_name === "pull_request_target") {
      throw forbiddenWithCode("Release evidence exchange denied", "pull_request_event_denied");
    }
    assertEqual(input.sourceSha, grant.sourceSha, "source_sha_request_mismatch");
    assertEqual(input.sequence, grant.sequence, "sequence_mismatch");
    assertEqual(input.environment, grant.environment, "environment_mismatch");
    assertEqual(input.workflowSha, grant.workflowSha, "workflow_sha_request_mismatch");
    if (input.bundleBytes <= 0 || input.bundleBytes > grant.maxUploadBytes) {
      throw new HttpError(422, "Release evidence bundle size is outside the grant limit", { code: "bundle_size_mismatch" });
    }
    const bundleSha256 = normalizeSha256(input.bundleSha256);
    if (!/^[a-f0-9]{64}$/.test(bundleSha256)) {
      throw new HttpError(422, "Release evidence bundle SHA-256 is invalid", { code: "bundle_sha256_invalid" });
    }
    const existingRun = await db
      .select()
      .from(releaseEvidenceSessions)
      .where(and(
        eq(releaseEvidenceSessions.grantId, grant.id),
        eq(releaseEvidenceSessions.runId, claims.run_id),
        eq(releaseEvidenceSessions.runAttempt, claims.run_attempt),
      ))
      .then((rows) => rows[0] ?? null);
    if (existingRun) {
      if (
        existingRun.issueId !== input.issueId ||
        existingRun.sourceSha !== input.sourceSha ||
        existingRun.sequence !== input.sequence ||
        existingRun.environment !== input.environment ||
        existingRun.imageDigest !== input.imageDigest ||
        existingRun.bundleSha256 !== bundleSha256 ||
        existingRun.bundleBytes !== input.bundleBytes
      ) {
        throw conflict("Release evidence conflict", { code: "release_evidence_conflict" });
      }
      return {
        sessionId: existingRun.id,
        uploadUrl: `/api/release-evidence/v1/sessions/${existingRun.id}/attachment`,
        expiresAt: existingRun.expiresAt,
      };
    }
    const capability = generateCapability();
    const expiresAt = new Date(current.getTime() + CAPABILITY_TTL_MS);
    const [session] = await db.insert(releaseEvidenceSessions).values({
      companyId: grant.companyId,
      grantId: grant.id,
      grantRevision: grant.revision,
      issueId: input.issueId,
      capabilityHash: capability.hash,
      clientNonceHash: sha256(input.clientNonce),
      repository: claims.repository,
      repositoryId: claims.repository_id,
      workflowRef: claims.workflow_ref,
      workflowSha: claims.workflow_sha,
      jobWorkflowRef: claims.job_workflow_ref ?? null,
      jobWorkflowSha: claims.job_workflow_sha ?? null,
      triggerSha: claims.sha,
      sourceSha: input.sourceSha,
      runId: claims.run_id,
      runAttempt: claims.run_attempt,
      eventName: claims.event_name,
      ref: claims.ref,
      actorId: claims.actor_id,
      sequence: input.sequence,
      environment: input.environment,
      imageDigest: input.imageDigest,
      bundleSha256,
      bundleBytes: input.bundleBytes,
      expiresAt,
    }).returning();
    await appendAudit({
      companyId: grant.companyId,
      grantId: grant.id,
      sessionId: session.id,
      issueId: input.issueId,
      eventType: "release_evidence.exchange",
      result: "accepted",
      details: {
        grantRevision: grant.revision,
        claims: sanitizedClaims(claims),
        release: {
          sourceSha: input.sourceSha,
          sequence: input.sequence,
          environment: input.environment,
          imageDigest: input.imageDigest,
          bundleSha256,
          bundleBytes: input.bundleBytes,
        },
      },
    });
    return {
      sessionId: session.id,
      capability: capability.secret,
      uploadUrl: `/api/release-evidence/v1/sessions/${session.id}/attachment`,
      expiresAt,
    };
  }

  async function prepareUpload(sessionId: string, capability: string, bodySha256: string, byteSize: number): Promise<PreparedReleaseEvidenceUpload> {
    const capabilityHash = sha256(capability);
    const session = await db
      .select()
      .from(releaseEvidenceSessions)
      .where(eq(releaseEvidenceSessions.id, sessionId))
      .then((rows) => rows[0] ?? null);
    if (!session || !constantTimeHashEquals(session.capabilityHash, capabilityHash)) {
      throw unauthorized("Release evidence upload capability is invalid");
    }
    const grant = await db
      .select()
      .from(releaseEvidenceGrants)
      .where(eq(releaseEvidenceGrants.id, session.grantId))
      .then((rows) => rows[0] ?? null);
    if (!grant || grant.status !== "active" || grant.revokedAt || session.revokedAt) {
      throw forbiddenWithCode("Release evidence upload denied", "release_evidence_revoked");
    }
    if (session.expiresAt <= now()) throw unauthorized("Release evidence upload capability expired");
    if (normalizeSha256(bodySha256) !== session.bundleSha256 || byteSize !== session.bundleBytes) {
      throw conflict("Release evidence conflict", { code: "release_evidence_conflict" });
    }
    if (session.status === "consumed") {
      if (!session.attachmentId) throw conflict("Release evidence session is consumed without an attachment");
      return { kind: "existing", attachmentId: session.attachmentId };
    }
    if (session.status !== "issued") throw forbiddenWithCode("Release evidence upload denied", "session_inactive");
    return { kind: "new", session };
  }

  async function consumeUpload(sessionId: string, stored: PutFileResult, input: {
    contentType: string;
    originalFilename: string | null;
  }) {
    return db.transaction(async (tx) => {
      const current = now();
      const [session] = await tx
        .update(releaseEvidenceSessions)
        .set({ status: "consuming", updatedAt: current })
        .where(and(eq(releaseEvidenceSessions.id, sessionId), eq(releaseEvidenceSessions.status, "issued")))
        .returning();
      if (!session) {
        const existing = await tx
          .select()
          .from(releaseEvidenceSessions)
          .where(eq(releaseEvidenceSessions.id, sessionId))
          .then((rows) => rows[0] ?? null);
        if (!existing) throw notFound("Release evidence session not found");
        if (existing.status === "consumed" && existing.attachmentId) {
          return { attachmentId: existing.attachmentId, existing: true };
        }
        throw forbiddenWithCode("Release evidence upload denied", "session_inactive");
      }
      if (stored.sha256 !== session.bundleSha256 || stored.byteSize !== session.bundleBytes) {
        throw conflict("Release evidence conflict", { code: "release_evidence_conflict" });
      }
      const [asset] = await tx.insert(assets).values({
        companyId: session.companyId,
        provider: stored.provider,
        objectKey: stored.objectKey,
        contentType: stored.contentType,
        byteSize: stored.byteSize,
        sha256: stored.sha256,
        originalFilename: stored.originalFilename ?? input.originalFilename,
        createdByAgentId: null,
        createdByUserId: null,
      }).returning();
      const [attachment] = await tx.insert(issueAttachments).values({
        companyId: session.companyId,
        issueId: session.issueId,
        assetId: asset.id,
      }).returning();
      await tx.update(releaseEvidenceSessions).set({
        status: "consumed",
        attachmentId: attachment.id,
        assetId: asset.id,
        consumedAt: current,
        updatedAt: current,
      }).where(eq(releaseEvidenceSessions.id, session.id));
      await tx.insert(releaseEvidenceAuditEvents).values({
        companyId: session.companyId,
        grantId: session.grantId,
        sessionId: session.id,
        issueId: session.issueId,
        attachmentId: attachment.id,
        eventType: "release_evidence.upload",
        result: "accepted",
        details: {
          grantRevision: session.grantRevision,
          repository: session.repository,
          workflowRef: session.workflowRef,
          workflowSha: session.workflowSha,
          jobWorkflowRef: session.jobWorkflowRef,
          jobWorkflowSha: session.jobWorkflowSha,
          triggerSha: session.triggerSha,
          runId: session.runId,
          runAttempt: session.runAttempt,
          sourceSha: session.sourceSha,
          eventName: session.eventName,
          ref: session.ref,
          actorId: session.actorId,
          release: {
            sequence: session.sequence,
            environment: session.environment,
            imageDigest: session.imageDigest,
            bundleSha256: session.bundleSha256,
            bundleBytes: session.bundleBytes,
            contentType: input.contentType,
          },
        },
        redacted: "true",
      });
      return { attachmentId: attachment.id, existing: false };
    });
  }

  async function provisionGrant(input: ProvisionReleaseEvidenceGrantInput) {
    const current = now();
    if (input.expiresAt <= current) throw new HttpError(422, "Release evidence grant expiry must be in the future", { code: "grant_expiry_invalid" });
    if (input.expiresAt.getTime() - current.getTime() > 24 * 60 * 60 * 1000) {
      throw new HttpError(422, "Release evidence grant expiry exceeds 24 hours", { code: "grant_expiry_too_long" });
    }
    const issue = await db.select().from(issues).where(eq(issues.id, input.issueId)).then((rows) => rows[0] ?? null);
    if (!issue || issue.companyId !== input.companyId) {
      throw forbiddenWithCode("Release evidence grant denied", "issue_company_mismatch");
    }

    return db.transaction(async (tx) => {
      const activeSameIssue = await tx
        .select()
        .from(releaseEvidenceGrants)
        .where(and(eq(releaseEvidenceGrants.companyId, input.companyId), eq(releaseEvidenceGrants.status, "active")))
        .then((rows) => rows.filter((row) => Array.isArray(row.allowedIssueIds) && row.allowedIssueIds.includes(input.issueId)));
      const grantsToRevoke = activeSameIssue.filter((row) => row.expiresAt <= current || row.sourceSha !== input.sourceSha || row.workflowSha !== input.workflowSha);
      if (grantsToRevoke.length) {
        await tx.update(releaseEvidenceGrants).set({
          status: "revoked",
          revokedAt: current,
          updatedAt: current,
        }).where(inArray(releaseEvidenceGrants.id, grantsToRevoke.map((row) => row.id)));
      }

      const existing = await tx.select().from(releaseEvidenceGrants).where(and(
        eq(releaseEvidenceGrants.companyId, input.companyId),
        eq(releaseEvidenceGrants.repository, input.repository),
        eq(releaseEvidenceGrants.workflowRef, input.workflowRef),
        eq(releaseEvidenceGrants.workflowSha, input.workflowSha),
        eq(releaseEvidenceGrants.sourceSha, input.sourceSha),
        eq(releaseEvidenceGrants.sequence, input.sequence),
        eq(releaseEvidenceGrants.environment, input.environment),
      )).then((rows) => rows[0] ?? null);

      if (input.dryRun) {
        await tx.insert(releaseEvidenceAuditEvents).values({
          companyId: input.companyId,
          issueId: input.issueId,
          eventType: "release_evidence.grant_preflight",
          result: "accepted",
          details: {
            existingGrant: Boolean(existing),
            actor: input.actor,
            repository: input.repository,
            workflowRef: input.workflowRef,
            workflowSha: input.workflowSha,
            triggerRef: input.triggerRef,
            sourceSha: input.sourceSha,
            sequence: input.sequence,
            environment: input.environment,
            maxUploadBytes: input.maxUploadBytes,
            expiresAt: input.expiresAt.toISOString(),
          },
          redacted: "true",
        });
        return { grantId: existing?.id ?? null, preflight: true };
      }

      const [grant] = await tx.insert(releaseEvidenceGrants).values({
        companyId: input.companyId,
        repository: input.repository,
        repositoryId: input.repositoryId ?? null,
        workflowRef: input.workflowRef,
        workflowSha: input.workflowSha,
        jobWorkflowRef: input.jobWorkflowRef ?? null,
        jobWorkflowSha: input.jobWorkflowSha ?? null,
        triggerRef: input.triggerRef,
        allowedIssueIds: [input.issueId],
        sourceSha: input.sourceSha,
        sequence: input.sequence,
        environment: input.environment,
        maxUploadBytes: input.maxUploadBytes,
        allowedEventName: input.allowedEventName ?? "workflow_dispatch",
        status: "active",
        expiresAt: input.expiresAt,
        updatedAt: current,
      }).returning();
      await tx.insert(releaseEvidenceAuditEvents).values({
        companyId: input.companyId,
        grantId: grant.id,
        issueId: input.issueId,
        eventType: "release_evidence.grant_provisioned",
        result: "accepted",
        details: {
          actor: input.actor,
          repository: input.repository,
          workflowRef: input.workflowRef,
          workflowSha: input.workflowSha,
          jobWorkflowRef: input.jobWorkflowRef ?? null,
          jobWorkflowSha: input.jobWorkflowSha ?? null,
          triggerRef: input.triggerRef,
          sourceSha: input.sourceSha,
          sequence: input.sequence,
          environment: input.environment,
          maxUploadBytes: input.maxUploadBytes,
          expiresAt: input.expiresAt.toISOString(),
        },
        redacted: "true",
      });
      return { grantId: grant.id, preflight: false };
    });
  }

  return { auditDenied, exchange, prepareUpload, consumeUpload, provisionGrant };
}
