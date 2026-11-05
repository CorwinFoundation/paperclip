import { randomUUID } from "node:crypto";
import express from "express";
import request from "supertest";
import { eq } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { agents, agentApiKeys, companies, createDb, issues, issueComments } from "@paperclipai/db";
import { getEmbeddedPostgresTestSupport, startEmbeddedPostgresTestDatabase } from "./helpers/embedded-postgres.js";
import { actorMiddleware } from "../middleware/auth.js";
import { fixedWorkflowReadOnly } from "../middleware/fixed-workflow-read-only.js";
import { agentService } from "../services/agents.js";

const support = await getEmbeddedPostgresTestSupport();
if (!support.supported) console.warn(`Fixed workflow database tests unavailable: ${support.reason}`);
(support.supported ? describe : describe.skip)("fixed workflow database authorization", () => {
  let temporary: Awaited<ReturnType<typeof startEmbeddedPostgresTestDatabase>>;
  let db: ReturnType<typeof createDb>;
  const companyId = randomUUID();
  const otherCompany = randomUUID();
  const agentId = randomUUID();
  const parentIssueId = randomUUID();
  const childId = randomUUID();
  const siblingId = randomUUID();
  const foreignChild = randomUUID();
  const grandchild = randomUUID();
  const foreignParent = randomUUID();
  const scope = { kind: "fixed_workflow_read_only" as const, companyId, parentIssueId,
    workflowId: "0b845d09083c12076c3877efbf7a50ba" };
  let app: express.Express;
  let token: string;
  let keyId: string;
  beforeAll(async () => {
    temporary = await startEmbeddedPostgresTestDatabase("paperclip-fixed-workflow-");
    db = createDb(temporary.connectionString);
    await db.insert(companies).values([
      { id: companyId, name: "Diagnostic", issuePrefix: "DIAG" },
      { id: otherCompany, name: "Other", issuePrefix: "OTHER" },
    ]);
    await db.insert(agents).values({ id: agentId, companyId, name: "Diagnostic", role: "engineer", adapterType: "process" });
    await db.insert(issues).values([
      { id: parentIssueId, companyId, title: "Parent" },
      { id: siblingId, companyId, title: "Unrelated" },
      { id: foreignParent, companyId: otherCompany, title: "Foreign parent" },
    ]);
    await db.insert(issues).values([
      { id: childId, companyId, parentId: parentIssueId, title: "Child" },
      { id: foreignChild, companyId: otherCompany, parentId: parentIssueId, title: "Foreign child" },
    ]);
    await db.insert(issues).values({ id: grandchild, companyId, parentId: childId, title: "Grandchild" });
    await db.insert(issueComments).values([
      { companyId, issueId: childId, body: "Visible" },
      { companyId, issueId: childId, body: "Deleted", deletedAt: new Date() },
      { companyId: otherCompany, issueId: childId, body: "Foreign" },
    ]);
    const key = await agentService(db).createApiKey(agentId, "fixed", scope);
    token = key.token;
    keyId = key.id;
    app = express();
    app.use(actorMiddleware(db, { deploymentMode: "authenticated" }));
    app.use(fixedWorkflowReadOnly(db));
    app.use((req, res) => res.status(req.actor.type === "none" ? 401 : 418).end());
  }, 60_000);
  afterAll(async () => { await temporary?.cleanup(); });
  const get = (path: string) => request(app).get(path).set("Authorization", `Bearer ${token}`);
  it("uses SQL company and parent predicates and excludes deleted or foreign comments", async () => {
    const listed = await get(`/api/companies/${companyId}/issues?parentId=${parentIssueId}`);
    expect(listed.status).toBe(200);
    expect(listed.body.map((row: { id: string }) => row.id)).toEqual([childId]);
    const comments = await get(`/api/issues/${childId}/comments`);
    expect(comments.status).toBe(200);
    expect(comments.body.map((row: { body: string }) => row.body)).toEqual(["Visible"]);
    for (const id of [parentIssueId, siblingId, foreignChild, grandchild, randomUUID()]) {
      expect((await get(`/api/issues/${id}/comments`)).status).toBe(403);
    }
  });
  it("rejects invalid issuer bindings before creating a credential", async () => {
    const svc = agentService(db);
    for (const invalid of [
      { ...scope, companyId: otherCompany },
      { ...scope, parentIssueId: foreignParent },
      { ...scope, parentIssueId: randomUUID() },
    ]) {
      await expect(svc.createApiKey(agentId, "invalid", invalid)).rejects.toThrow();
    }
    const keys = await db.select().from(agentApiKeys).where(eq(agentApiKeys.agentId, agentId));
    expect(keys).toHaveLength(1);
  });
  it("preserves legacy unscoped keys and rejects stored key company mismatches", async () => {
    const legacy = await agentService(db).createApiKey(agentId, "legacy");
    await db.update(agentApiKeys).set({ scopeConfig: null }).where(eq(agentApiKeys.id, legacy.id));
    const legacyGet = () => request(app).get("/api/agents/me")
      .set("Authorization", `Bearer ${legacy.token}`);
    // The terminal fallback proves that ordinary credentials still pass through.
    expect((await legacyGet()).status).toBe(418);
    await db.update(agentApiKeys).set({ companyId: otherCompany })
      .where(eq(agentApiKeys.id, legacy.id));
    expect((await legacyGet()).status).toBe(403);
    await db.delete(agentApiKeys).where(eq(agentApiKeys.id, legacy.id));
  });
  it("rejects malformed stored scopes and honors revocation", async () => {
    await db.update(agentApiKeys).set({ scopeConfig: { ...scope, workflowId: "invalid" } }).where(eq(agentApiKeys.id, keyId));
    expect((await get(`/api/companies/${companyId}/issues?parentId=${parentIssueId}`)).status).toBe(403);
    await db.update(agentApiKeys).set({ scopeConfig: scope, revokedAt: new Date() }).where(eq(agentApiKeys.id, keyId));
    expect((await get(`/api/companies/${companyId}/issues?parentId=${parentIssueId}`)).status).toBe(401);
  });
});
