import { createHash, createPublicKey, createVerify, type JsonWebKey as NodeJsonWebKey } from "node:crypto";
import { Router, type Request, type Response } from "express";
import multer from "multer";
import { z } from "zod";
import type { Db } from "@paperclipai/db";
import type { StorageService } from "../storage/types.js";
import { normalizeContentType } from "../attachment-types.js";
import { badRequest, HttpError, unauthorized, unprocessable } from "../errors.js";
import { validate } from "../middleware/validate.js";
import {
  releaseEvidenceIngressService,
  type ReleaseEvidenceOidcClaims,
} from "../services/release-evidence-ingress.js";

const GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com";
const GITHUB_OIDC_JWKS_URL = "https://token.actions.githubusercontent.com/.well-known/jwks";
const RELEASE_EVIDENCE_AUDIENCE = "paperclip-release-evidence";
const MAX_UPLOAD_BYTES_FALLBACK = 256 * 1024 * 1024;
const ALLOWED_UPLOAD_CONTENT_TYPES = new Set([
  "application/gzip",
  "application/tar+gzip",
  "application/x-gzip",
  "application/zip",
  "application/octet-stream",
]);

type Jwk = NodeJsonWebKey & { kid?: string; alg?: string; kty?: string };
type Jwks = { keys?: Jwk[] };
let jwksCache: { fetchedAt: number; keys: Jwk[] } | null = null;

const exchangeSchema = z.object({
  grantId: z.string().uuid(),
  issueId: z.string().uuid(),
  sourceSha: z.string().trim().regex(/^[a-f0-9]{40,64}$/i),
  workflowSha: z.string().trim().regex(/^[a-f0-9]{40,64}$/i),
  sequence: z.number().int().positive(),
  environment: z.string().trim().min(1).max(120),
  imageDigest: z.string().trim().min(12).max(300),
  bundleSha256: z.string().trim().regex(/^(sha256:)?[a-f0-9]{64}$/i),
  bundleBytes: z.number().int().positive().max(MAX_UPLOAD_BYTES_FALLBACK),
  clientNonce: z.string().trim().min(12).max(200),
}).strict();

const provisionGrantSchema = z.object({
  companyId: z.string().uuid(),
  repository: z.string().trim().min(1).max(240),
  repositoryId: z.string().trim().min(1).max(80).optional(),
  workflowRef: z.string().trim().min(1).max(500),
  workflowSha: z.string().trim().regex(/^[a-f0-9]{40,64}$/i),
  jobWorkflowRef: z.string().trim().min(1).max(500).optional(),
  jobWorkflowSha: z.string().trim().regex(/^[a-f0-9]{40,64}$/i).optional(),
  triggerRef: z.string().trim().min(1).max(300),
  issueId: z.string().uuid(),
  sourceSha: z.string().trim().regex(/^[a-f0-9]{40,64}$/i),
  sequence: z.number().int().positive(),
  environment: z.string().trim().min(1).max(120),
  maxUploadBytes: z.number().int().positive().max(MAX_UPLOAD_BYTES_FALLBACK),
  allowedEventName: z.string().trim().min(1).max(80).optional(),
  expiresAt: z.string().datetime(),
  dryRun: z.boolean().optional(),
}).strict();

export type ReleaseEvidenceOidcVerifier = (token: string, now: Date) => Promise<ReleaseEvidenceOidcClaims>;

function base64urlJson(value: string) {
  try {
    return JSON.parse(Buffer.from(value, "base64url").toString("utf8")) as Record<string, unknown>;
  } catch {
    throw unauthorized("OIDC token is malformed");
  }
}

function asRequiredString(payload: Record<string, unknown>, key: string) {
  const value = payload[key];
  if (typeof value !== "string" || !value.trim()) throw unauthorized(`OIDC token is missing ${key}`);
  return value;
}

function asAudience(payload: Record<string, unknown>) {
  const value = payload.aud;
  if (typeof value === "string") return value;
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) return value;
  throw unauthorized("OIDC token is missing aud");
}

async function fetchGithubJwks(): Promise<Jwk[]> {
  const cached = jwksCache;
  if (cached && Date.now() - cached.fetchedAt < 10 * 60 * 1000) return cached.keys;
  const response = await fetch(GITHUB_OIDC_JWKS_URL);
  if (!response.ok) throw unauthorized("Unable to fetch GitHub OIDC JWKS");
  const jwks = await response.json() as Jwks;
  const keys = Array.isArray(jwks.keys) ? jwks.keys : [];
  jwksCache = { fetchedAt: Date.now(), keys };
  return keys;
}

function assertJwtTime(payload: Record<string, unknown>, now: Date) {
  const nowSeconds = Math.floor(now.getTime() / 1000);
  const skew = 60;
  const exp = typeof payload.exp === "number" ? payload.exp : null;
  const nbf = typeof payload.nbf === "number" ? payload.nbf : null;
  const iat = typeof payload.iat === "number" ? payload.iat : null;
  if (!exp || exp + skew < nowSeconds) throw unauthorized("OIDC token expired");
  if (nbf && nbf - skew > nowSeconds) throw unauthorized("OIDC token not yet valid");
  if (iat && iat - skew > nowSeconds) throw unauthorized("OIDC token issued in the future");
}

export async function verifyGithubOidcToken(token: string, now: Date): Promise<ReleaseEvidenceOidcClaims> {
  const parts = token.split(".");
  if (parts.length !== 3) throw unauthorized("OIDC token is malformed");
  const header = base64urlJson(parts[0] ?? "");
  const payload = base64urlJson(parts[1] ?? "");
  if (header.alg !== "RS256" || typeof header.kid !== "string") throw unauthorized("OIDC token uses an unsupported signature");
  const key = (await fetchGithubJwks()).find((candidate) => candidate.kid === header.kid);
  if (!key) throw unauthorized("OIDC token signing key is unknown");
  const verifier = createVerify("RSA-SHA256");
  verifier.update(`${parts[0]}.${parts[1]}`);
  verifier.end();
  const valid = verifier.verify(
    createPublicKey({ key: key as NodeJsonWebKey, format: "jwk" } as Parameters<typeof createPublicKey>[0]),
    Buffer.from(parts[2] ?? "", "base64url"),
  );
  if (!valid) throw unauthorized("OIDC token signature is invalid");
  assertJwtTime(payload, now);
  const aud = asAudience(payload);
  const claims: ReleaseEvidenceOidcClaims = {
    iss: asRequiredString(payload, "iss"),
    aud,
    repository: asRequiredString(payload, "repository"),
    repository_id: asRequiredString(payload, "repository_id"),
    workflow_ref: asRequiredString(payload, "workflow_ref"),
    workflow_sha: asRequiredString(payload, "workflow_sha"),
    job_workflow_ref: typeof payload.job_workflow_ref === "string" && payload.job_workflow_ref.trim()
      ? payload.job_workflow_ref
      : undefined,
    job_workflow_sha: typeof payload.job_workflow_sha === "string" && payload.job_workflow_sha.trim()
      ? payload.job_workflow_sha
      : undefined,
    sha: asRequiredString(payload, "sha"),
    run_id: asRequiredString(payload, "run_id"),
    run_attempt: asRequiredString(payload, "run_attempt"),
    event_name: asRequiredString(payload, "event_name"),
    ref: asRequiredString(payload, "ref"),
    actor_id: asRequiredString(payload, "actor_id"),
  };
  if (claims.iss !== GITHUB_OIDC_ISSUER) throw unauthorized("OIDC token issuer is not trusted");
  if (!(Array.isArray(aud) ? aud.includes(RELEASE_EVIDENCE_AUDIENCE) : aud === RELEASE_EVIDENCE_AUDIENCE)) {
    throw new HttpError(403, "Release evidence exchange denied", { code: "audience_mismatch" });
  }
  return claims;
}

function sha256(value: Buffer) {
  return createHash("sha256").update(value).digest("hex");
}

function readBearerToken(req: Request) {
  const auth = req.header("authorization");
  if (!auth?.toLowerCase().startsWith("bearer ")) return null;
  const token = auth.slice("bearer ".length).trim();
  return token || null;
}

function runSingleEvidenceUpload(req: Request, res: Response, maxBytes: number) {
  const upload = multer({ storage: multer.memoryStorage(), limits: { files: 1, fileSize: maxBytes } });
  return new Promise<void>((resolve, reject) => {
    upload.single("file")(req, res, (err: unknown) => {
      if (err) reject(err);
      else resolve();
    });
  });
}

function errorCode(err: unknown, fallback: string) {
  if (err instanceof HttpError) {
    const details = err.details as { code?: unknown } | undefined;
    if (typeof details?.code === "string") return details.code;
    return err.message.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || fallback;
  }
  return fallback;
}

export function releaseEvidenceRoutes(
  db: Db,
  storage: StorageService,
  opts: { verifyOidcToken?: ReleaseEvidenceOidcVerifier; now?: () => Date; maxUploadBytes?: number } = {},
) {
  const router = Router();
  const svc = releaseEvidenceIngressService(db, { now: opts.now });
  const verifyOidc = opts.verifyOidcToken ?? verifyGithubOidcToken;
  const maxUploadBytes = opts.maxUploadBytes ?? MAX_UPLOAD_BYTES_FALLBACK;

  router.post("/release-evidence/v1/grants", validate(provisionGrantSchema), async (req, res) => {
    if (req.actor.type !== "board") throw unauthorized("release_evidence_grant_admin_required");
    const body = req.body as z.infer<typeof provisionGrantSchema>;
    const result = await svc.provisionGrant({
      ...body,
      expiresAt: new Date(body.expiresAt),
      actor: { type: "board", id: req.actor.userId ?? null },
    });
    res.status(body.dryRun ? 200 : 201).json(result);
  });

  router.post("/release-evidence/v1/exchange", validate(exchangeSchema), async (req, res) => {
    if (req.actor.type === "agent" || req.actor.type === "board") {
      await svc.auditDenied("release_evidence.exchange", "oidc_required", { request: req.body });
      throw unauthorized("oidc_required");
    }
    const token = readBearerToken(req);
    if (!token) {
      await svc.auditDenied("release_evidence.exchange", "oidc_required", { request: req.body });
      throw unauthorized("oidc_required");
    }
    let claims: ReleaseEvidenceOidcClaims | null = null;
    try {
      claims = await verifyOidc(token, opts.now?.() ?? new Date());
      const result = await svc.exchange(claims, req.body);
      res.status(201).json(result);
    } catch (err) {
      await svc.auditDenied("release_evidence.exchange", errorCode(err, "exchange_denied"), {
        grantId: req.body.grantId,
        issueId: req.body.issueId,
        claims: claims ?? undefined,
        request: req.body,
      });
      throw err;
    }
  });

  router.post("/release-evidence/v1/sessions/:sessionId/attachment", async (req, res) => {
    const capability = req.header("x-paperclip-release-evidence-capability")?.trim();
    if (!capability) throw unauthorized("Missing release evidence upload capability");
    try {
      await runSingleEvidenceUpload(req, res, maxUploadBytes);
    } catch (err) {
      if (err instanceof multer.MulterError) {
        if (err.code === "LIMIT_FILE_SIZE") throw unprocessable(`Attachment exceeds ${maxUploadBytes} bytes`);
        throw badRequest(err.message);
      }
      throw err;
    }
    const file = (req as Request & { file?: { mimetype: string; buffer: Buffer; originalname: string } }).file;
    if (!file) throw badRequest("Missing file field 'file'");
    const contentType = normalizeContentType(file.mimetype);
    if (!ALLOWED_UPLOAD_CONTENT_TYPES.has(contentType)) {
      throw new HttpError(422, "Release evidence media type is not allowed", { code: "media_type_denied" });
    }
    if (file.buffer.length <= 0) throw unprocessable("Release evidence attachment is empty");
    const bodySha256 = sha256(file.buffer);
    try {
      const prepared = await svc.prepareUpload(req.params.sessionId as string, capability, bodySha256, file.buffer.length);
      if (prepared.kind === "existing") {
        res.status(200).json({
          attachmentId: prepared.attachmentId,
          contentPath: `/api/attachments/${prepared.attachmentId}/content`,
          replay: true,
        });
        return;
      }
      const stored = await storage.putFile({
        companyId: prepared.session.companyId,
        namespace: `release-evidence/${prepared.session.issueId}/${prepared.session.id}`,
        originalFilename: file.originalname || "release-evidence.tar.gz",
        contentType,
        body: file.buffer,
      });
      const consumed = await svc.consumeUpload(prepared.session.id, stored, {
        contentType,
        originalFilename: file.originalname || null,
      });
      res.status(consumed.existing ? 200 : 201).json({
        attachmentId: consumed.attachmentId,
        contentPath: `/api/attachments/${consumed.attachmentId}/content`,
        replay: consumed.existing,
      });
    } catch (err) {
      await svc.auditDenied("release_evidence.upload", errorCode(err, "upload_denied"), { sessionId: req.params.sessionId as string });
      throw err;
    }
  });

  return router;
}
