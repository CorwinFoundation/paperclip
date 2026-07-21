import { createHash, randomUUID } from "node:crypto";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import {
  assets,
  companies,
  createDb,
  issueAttachments,
  issues,
  releaseEvidenceAuditEvents,
  releaseEvidenceGrants,
  releaseEvidenceSessions,
} from "@paperclipai/db";
import {
  getEmbeddedPostgresTestSupport,
  startEmbeddedPostgresTestDatabase,
} from "../__tests__/helpers/embedded-postgres.js";
import { releaseEvidenceIngressService } from "./release-evidence-ingress.js";

const embeddedPostgresSupport = await getEmbeddedPostgresTestSupport();
const describeEmbeddedPostgres = embeddedPostgresSupport.supported ? describe : describe.skip;

if (!embeddedPostgresSupport.supported) {
  console.warn(
    `Skipping embedded Postgres release evidence ingress tests on this host: ${embeddedPostgresSupport.reason ?? "unsupported environment"}`,
  );
}

const now = new Date("2026-07-21T12:00:00.000Z");
const bundle = Buffer.from("release evidence bundle");
const bundleSha256 = createHash("sha256").update(bundle).digest("hex");

describeEmbeddedPostgres("releaseEvidenceIngressService", () => {
  let db!: ReturnType<typeof createDb>;
  let tempDb: Awaited<ReturnType<typeof startEmbeddedPostgresTestDatabase>> | null = null;

  beforeAll(async () => {
    tempDb = await startEmbeddedPostgresTestDatabase("paperclip-release-evidence-ingress-");
    db = createDb(tempDb.connectionString);
  }, 20_000);

  afterEach(async () => {
    await db.delete(releaseEvidenceAuditEvents);
    await db.delete(releaseEvidenceSessions);
    await db.delete(releaseEvidenceGrants);
    await db.delete(issueAttachments);
    await db.delete(assets);
    await db.delete(issues);
    await db.delete(companies);
  });

  afterAll(async () => {
    await tempDb?.cleanup();
  });

  async function seedSession() {
    const [company] = await db
      .insert(companies)
      .values({
        name: `Release Evidence ${randomUUID()}`,
        issuePrefix: `RE${randomUUID().slice(0, 6).toUpperCase()}`,
      })
      .returning();
    const [issue] = await db
      .insert(issues)
      .values({
        companyId: company!.id,
        title: "Release evidence target",
        status: "todo",
        priority: "high",
      })
      .returning();
    const [grant] = await db
      .insert(releaseEvidenceGrants)
      .values({
        companyId: company!.id,
        repository: "CorwinFoundation/are-scanner",
        repositoryId: "123456",
        workflowRef: "CorwinFoundation/are-scanner/.github/workflows/scanner-edge-image.yml@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        jobWorkflowRef: "CorwinFoundation/are-scanner/.github/workflows/scanner-edge-image.yml@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        allowedIssueIds: [issue!.id],
        sourceSha: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        sequence: 20,
        environment: "production",
        maxUploadBytes: 1024,
        expiresAt: new Date(now.getTime() + 60_000),
      })
      .returning();
    const [session] = await db
      .insert(releaseEvidenceSessions)
      .values({
        companyId: company!.id,
        grantId: grant!.id,
        grantRevision: grant!.revision,
        issueId: issue!.id,
        capabilityHash: createHash("sha256").update("pcrel_secret").digest("hex"),
        clientNonceHash: createHash("sha256").update("client-nonce").digest("hex"),
        repository: "CorwinFoundation/are-scanner",
        repositoryId: "123456",
        workflowRef: grant!.workflowRef,
        jobWorkflowRef: grant!.jobWorkflowRef!,
        sourceSha: grant!.sourceSha,
        runId: "10001",
        runAttempt: "1",
        eventName: "workflow_dispatch",
        ref: "refs/heads/master",
        actorId: "98765",
        sequence: 20,
        environment: "production",
        imageDigest: "ghcr.io/corwinfoundation/are-scanner@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        bundleSha256,
        bundleBytes: bundle.length,
        expiresAt: new Date(now.getTime() + 60_000),
      })
      .returning();

    return { company: company!, issue: issue!, session: session! };
  }

  it("creates exactly one attachment when two consumers race the same issued session", async () => {
    const { company, session } = await seedSession();
    const svc = releaseEvidenceIngressService(db, { now: () => now });
    const stored = {
      provider: "local_disk",
      objectKey: "release-evidence/session/bundle.tgz",
      contentType: "application/gzip",
      byteSize: bundle.length,
      sha256: bundleSha256,
      originalFilename: "bundle.tgz",
    };

    const results = await Promise.all([
      svc.consumeUpload(session.id, stored, { contentType: "application/gzip", originalFilename: "bundle.tgz" }),
      svc.consumeUpload(session.id, stored, { contentType: "application/gzip", originalFilename: "bundle.tgz" }),
    ]);

    expect(new Set(results.map((result) => result.attachmentId)).size).toBe(1);
    expect(results.filter((result) => result.existing)).toHaveLength(1);

    const attachmentRows = await db.select().from(issueAttachments);
    const assetRows = await db.select().from(assets);
    const sessionRows = await db.select().from(releaseEvidenceSessions);
    expect(attachmentRows).toHaveLength(1);
    expect(assetRows).toHaveLength(1);
    expect(sessionRows[0]).toMatchObject({
      status: "consumed",
      companyId: company.id,
      attachmentId: attachmentRows[0]!.id,
      assetId: assetRows[0]!.id,
    });
  });

  it("redacts client nonce values from denial audit request details", async () => {
    const { company, session } = await seedSession();
    const svc = releaseEvidenceIngressService(db, { now: () => now });

    await svc.auditDenied("release_evidence.exchange", "source_sha_claim_mismatch", {
      companyId: company.id,
      sessionId: session.id,
      request: {
        sourceSha: session.sourceSha,
        bundleSha256,
        bundleBytes: bundle.length,
        clientNonce: "raw-client-nonce",
      },
    });

    const [event] = await db.select().from(releaseEvidenceAuditEvents);
    expect(JSON.stringify(event!.details)).not.toContain("raw-client-nonce");
    expect(event!.details.request).toMatchObject({
      sourceSha: session.sourceSha,
      bundleSha256,
      bundleBytes: bundle.length,
      clientNonceHash: createHash("sha256").update("raw-client-nonce").digest("hex"),
    });
  });
});
