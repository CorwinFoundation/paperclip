import { createHash } from "node:crypto";
import express from "express";
import request from "supertest";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { conflict, HttpError, unauthorized } from "../errors.js";
import { errorHandler } from "../middleware/index.js";
import { releaseEvidenceRoutes, type ReleaseEvidenceOidcVerifier } from "../routes/release-evidence.js";
import type { StorageService } from "../storage/types.js";

const mockReleaseEvidenceIngressService = vi.hoisted(() => ({
  auditDenied: vi.fn(async () => undefined),
  exchange: vi.fn(),
  prepareUpload: vi.fn(),
  consumeUpload: vi.fn(),
  provisionGrant: vi.fn(),
}));

vi.mock("../services/release-evidence-ingress.js", async () => {
  const actual = await vi.importActual<typeof import("../services/release-evidence-ingress.js")>(
    "../services/release-evidence-ingress.js",
  );
  return {
    ...actual,
    releaseEvidenceIngressService: () => mockReleaseEvidenceIngressService,
  };
});

const now = new Date("2026-07-21T12:00:00.000Z");
const companyId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const issueId = "11111111-1111-4111-8111-111111111111";
const grantId = "22222222-2222-4222-8222-222222222222";
const sessionId = "33333333-3333-4333-8333-333333333333";
const attachmentId = "44444444-4444-4444-8444-444444444444";
const sourceSha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const workflowSha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const bundle = Buffer.from("release evidence bundle");
const bundleSha256 = createHash("sha256").update(bundle).digest("hex");

const validClaims = {
  iss: "https://token.actions.githubusercontent.com",
  aud: "paperclip-release-evidence",
  repository: "CorwinFoundation/are-scanner",
  repository_id: "123456",
  workflow_ref: "CorwinFoundation/are-scanner/.github/workflows/scanner-edge-qa-evidence.yml@refs/heads/beaaa-16889-qa-evidence",
  workflow_sha: workflowSha,
  sha: "dddddddddddddddddddddddddddddddddddddddd",
  run_id: "10001",
  run_attempt: "1",
  event_name: "workflow_dispatch",
  ref: "refs/heads/beaaa-16889-qa-evidence",
  actor_id: "98765",
};

function exchangeBody(overrides: Record<string, unknown> = {}) {
  return {
    grantId,
    issueId,
    sourceSha,
    workflowSha,
    sequence: 20,
    environment: "production",
    imageDigest: "ghcr.io/corwinfoundation/are-scanner@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
    bundleSha256,
    bundleBytes: bundle.length,
    clientNonce: "nonce-123456789",
    ...overrides,
  };
}

function createStorageService(): StorageService & { putFile: ReturnType<typeof vi.fn> } {
  return {
    provider: "local_disk",
    putFile: vi.fn(async (input) => ({
      provider: "local_disk",
      objectKey: `${input.namespace}/${input.originalFilename ?? "upload"}`,
      contentType: input.contentType,
      byteSize: input.body.length,
      sha256: createHash("sha256").update(input.body).digest("hex"),
      originalFilename: input.originalFilename,
    })),
    getObject: vi.fn(),
    headObject: vi.fn(),
    deleteObject: vi.fn(),
  };
}

function createApp(options: {
  actor?: Express.Request["actor"];
  verifyOidcToken?: ReleaseEvidenceOidcVerifier;
  storage?: StorageService;
} = {}) {
  const app = express();
  app.use(express.json());
  app.use((req, _res, next) => {
    req.actor = options.actor ?? { type: "none", source: "none" };
    next();
  });
  app.use("/api", releaseEvidenceRoutes({} as never, options.storage ?? createStorageService(), {
    verifyOidcToken: options.verifyOidcToken ?? vi.fn(async () => validClaims),
    now: () => now,
    maxUploadBytes: 1024,
  }));
  app.get("/api/issues/:id", (req, res) => {
    if (req.actor.type === "none") {
      res.status(401).json({ error: "Unauthorized" });
      return;
    }
    res.json({ id: req.params.id });
  });
  app.use(errorHandler);
  return app;
}

describe("release evidence routes", () => {
  beforeEach(() => {
    mockReleaseEvidenceIngressService.auditDenied.mockClear();
    mockReleaseEvidenceIngressService.exchange.mockReset();
    mockReleaseEvidenceIngressService.prepareUpload.mockReset();
    mockReleaseEvidenceIngressService.consumeUpload.mockReset();
    mockReleaseEvidenceIngressService.provisionGrant.mockReset();
  });

  it("requires GitHub OIDC at exchange and rejects agent keys", async () => {
    const res = await request(createApp({
      actor: {
        type: "agent",
        source: "agent_key",
        agentId: "55555555-5555-4555-8555-555555555555",
        companyId,
        keyId: "66666666-6666-4666-8666-666666666666",
      },
    }))
      .post("/api/release-evidence/v1/exchange")
      .set("Authorization", "Bearer pc_agent_key")
      .send(exchangeBody());

    expect(res.status).toBe(401);
    expect(res.body.error).toBe("oidc_required");
    expect(mockReleaseEvidenceIngressService.exchange).not.toHaveBeenCalled();
    expect(mockReleaseEvidenceIngressService.auditDenied).toHaveBeenCalledWith(
      "release_evidence.exchange",
      "oidc_required",
      expect.any(Object),
    );
  });

  it("exchanges a valid OIDC token for a five-minute upload capability", async () => {
    const verifyOidcToken = vi.fn(async () => validClaims);
    mockReleaseEvidenceIngressService.exchange.mockResolvedValue({
      sessionId,
      capability: "pcrel_secret",
      uploadUrl: `/api/release-evidence/v1/sessions/${sessionId}/attachment`,
      expiresAt: new Date(now.getTime() + 5 * 60 * 1000),
    });

    const res = await request(createApp({ verifyOidcToken }))
      .post("/api/release-evidence/v1/exchange")
      .set("Authorization", "Bearer github.oidc.jwt")
      .send(exchangeBody());

    expect(res.status).toBe(201);
    expect(res.body).toMatchObject({
      sessionId,
      capability: "pcrel_secret",
      uploadUrl: `/api/release-evidence/v1/sessions/${sessionId}/attachment`,
    });
    expect(verifyOidcToken).toHaveBeenCalledWith("github.oidc.jwt", now);
    expect(mockReleaseEvidenceIngressService.exchange).toHaveBeenCalledWith(validClaims, exchangeBody());
  });

  it("audits each bound exchange denial without storing an attachment", async () => {
    const table = [
      ["repository", { ...validClaims, repository: "Other/repo" }, new HttpError(403, "Release evidence exchange denied", { code: "repository_mismatch" })],
      ["audience", { ...validClaims, aud: "api" }, new HttpError(403, "Release evidence exchange denied", { code: "audience_mismatch" })],
      ["workflow sha", { ...validClaims, workflow_sha: "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee" }, new HttpError(403, "Release evidence exchange denied", { code: "workflow_sha_mismatch" })],
      ["workflow", { ...validClaims, workflow_ref: "CorwinFoundation/are-scanner/.github/workflows/other.yml@refs/heads/beaaa-16889-qa-evidence" }, new HttpError(403, "Release evidence exchange denied", { code: "workflow_ref_mismatch" })],
    ] as const;

    for (const [, claims, err] of table) {
      mockReleaseEvidenceIngressService.exchange.mockRejectedValueOnce(err);
      const res = await request(createApp({ verifyOidcToken: vi.fn(async () => claims) }))
        .post("/api/release-evidence/v1/exchange")
        .set("Authorization", "Bearer github.oidc.jwt")
        .send(exchangeBody());
      expect(res.status).toBe(403);
    }

    expect(mockReleaseEvidenceIngressService.auditDenied).toHaveBeenCalledTimes(table.length);
    expect(mockReleaseEvidenceIngressService.prepareUpload).not.toHaveBeenCalled();
    expect(mockReleaseEvidenceIngressService.consumeUpload).not.toHaveBeenCalled();
  });

  it("provisions one active release evidence grant for board admins and returns only its opaque id", async () => {
    mockReleaseEvidenceIngressService.provisionGrant.mockResolvedValue({
      grantId,
      preflight: false,
    });

    const body = {
      companyId,
      repository: "CorwinFoundation/are-scanner",
      repositoryId: "123456",
      workflowRef: validClaims.workflow_ref,
      workflowSha,
      triggerRef: validClaims.ref,
      issueId,
      sourceSha,
      sequence: 20,
      environment: "qa-evidence",
      maxUploadBytes: 268435456,
      allowedEventName: "workflow_dispatch",
      expiresAt: new Date(now.getTime() + 60 * 60 * 1000).toISOString(),
    };

    const res = await request(createApp({
      actor: { type: "board", source: "session", userId: "local-board" },
    }))
      .post("/api/release-evidence/v1/grants")
      .send(body);

    expect(res.status).toBe(201);
    expect(res.body).toEqual({ grantId, preflight: false });
    expect(mockReleaseEvidenceIngressService.provisionGrant).toHaveBeenCalledWith(expect.objectContaining({
      ...body,
      expiresAt: new Date(body.expiresAt),
      actor: { type: "board", id: "local-board" },
    }));
  });

  it("stores exactly one attachment for valid upload and returns existing attachment on replay", async () => {
    const storage = createStorageService();
    mockReleaseEvidenceIngressService.prepareUpload
      .mockResolvedValueOnce({
        kind: "new",
        session: { id: sessionId, companyId, issueId },
      })
      .mockResolvedValueOnce({
        kind: "existing",
        attachmentId,
      });
    mockReleaseEvidenceIngressService.consumeUpload.mockResolvedValueOnce({ attachmentId, existing: false });

    const app = createApp({ storage });
    const first = await request(app)
      .post(`/api/release-evidence/v1/sessions/${sessionId}/attachment`)
      .set("X-Paperclip-Release-Evidence-Capability", "pcrel_secret")
      .attach("file", bundle, { filename: "bundle.tgz", contentType: "application/gzip" });
    const second = await request(app)
      .post(`/api/release-evidence/v1/sessions/${sessionId}/attachment`)
      .set("X-Paperclip-Release-Evidence-Capability", "pcrel_secret")
      .attach("file", bundle, { filename: "bundle.tgz", contentType: "application/gzip" });

    expect(first.status).toBe(201);
    expect(second.status).toBe(200);
    expect(second.body).toMatchObject({ attachmentId, replay: true });
    expect(storage.putFile).toHaveBeenCalledTimes(1);
    expect(mockReleaseEvidenceIngressService.consumeUpload).toHaveBeenCalledTimes(1);
  });

  it("rejects byte conflicts, expiry, and revocation before attachment storage", async () => {
    const storage = createStorageService();
    mockReleaseEvidenceIngressService.prepareUpload
      .mockRejectedValueOnce(conflict("Release evidence conflict", { code: "release_evidence_conflict" }))
      .mockRejectedValueOnce(unauthorized("Release evidence upload capability expired"))
      .mockRejectedValueOnce(new HttpError(403, "Release evidence upload denied", { code: "release_evidence_revoked" }));

    for (const expectedStatus of [409, 401, 403]) {
      const res = await request(createApp({ storage }))
        .post(`/api/release-evidence/v1/sessions/${sessionId}/attachment`)
        .set("X-Paperclip-Release-Evidence-Capability", "pcrel_secret")
        .attach("file", bundle, { filename: "bundle.tgz", contentType: "application/gzip" });
      expect(res.status).toBe(expectedStatus);
    }

    expect(storage.putFile).not.toHaveBeenCalled();
    expect(mockReleaseEvidenceIngressService.consumeUpload).not.toHaveBeenCalled();
  });

  it("does not treat upload capabilities as valid auth for representative non-release APIs", async () => {
    const res = await request(createApp())
      .get(`/api/issues/${issueId}`)
      .set("Authorization", "Bearer pcrel_secret");

    expect(res.status).toBe(401);
  });
});
