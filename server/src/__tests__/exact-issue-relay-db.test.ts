import { randomUUID } from "node:crypto";
import express from "express";
import request from "supertest";
import { eq } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { agents, agentApiKeys, companies, createDb, issues, issueComments } from "@paperclipai/db";
import { getEmbeddedPostgresTestSupport, startEmbeddedPostgresTestDatabase } from "./helpers/embedded-postgres.js";
import { actorMiddleware } from "../middleware/auth.js";
import { exactIssueRelay } from "../middleware/exact-issue-relay.js";
import { agentService } from "../services/agents.js";

const support = await getEmbeddedPostgresTestSupport();
if (!support.supported) console.warn(`Relay database tests unavailable: ${support.reason}`);
(support.supported ? describe : describe.skip)("exact issue relay database authorization", () => {
  let temporary: Awaited<ReturnType<typeof startEmbeddedPostgresTestDatabase>>;
  let db: ReturnType<typeof createDb>;
  let app: express.Express;
  let token: string;
  let keyId: string;
  const companyId = randomUUID(), otherCompany = randomUUID(), agentId = randomUUID();
  const issueId = randomUUID(), foreignIssue = randomUUID(), unrelatedIssue = randomUUID();
  const scope = { kind: "exact_issue_relay" as const, companyId, issueIds: [issueId] };
  beforeAll(async () => {
    temporary = await startEmbeddedPostgresTestDatabase("paperclip-exact-relay-");
    db = createDb(temporary.connectionString);
    await db.insert(companies).values([
      { id: companyId, name: "Relay", issuePrefix: "RELAY" },
      { id: otherCompany, name: "Other", issuePrefix: "OTHER" },
    ]);
    await db.insert(agents).values({ id: agentId, companyId, name: "Relay", role: "engineer", adapterType: "process", status: "paused" });
    await db.insert(issues).values([
      { id: issueId, companyId, title: "Held", status: "done" },
      { id: unrelatedIssue, companyId, title: "Unrelated" },
      { id: foreignIssue, companyId: otherCompany, title: "Foreign" },
    ]);
    await db.insert(issueComments).values({ companyId, issueId, body: "Deleted", deletedAt: new Date() });
    const key = await agentService(db).createApiKey(agentId, "relay", scope);
    token = key.token; keyId = key.id;
    app = express();
    app.use(express.json());
    app.use(actorMiddleware(db, { deploymentMode: "authenticated" }));
    app.use(exactIssueRelay(db));
    app.use((req, res) => res.status(req.actor.type === "none" ? 401 : 418).end());
  }, 60_000);
  afterAll(async () => { await temporary?.cleanup(); });
  const get = (path: string) => request(app).get(path).auth(token, { type: "bearer" });
  it("rejects invalid company and issue bindings before persisting a key", async () => {
    for (const invalid of [
      { ...scope, companyId: otherCompany },
      { ...scope, issueIds: [foreignIssue] },
      { ...scope, issueIds: [randomUUID()] },
    ]) await expect(agentService(db).createApiKey(agentId, "invalid", invalid)).rejects.toThrow();
    expect(await db.select().from(agentApiKeys).where(eq(agentApiKeys.agentId, agentId))).toHaveLength(1);
  });
  it("authenticates persisted key, reads only bound issues, and hides deleted comments", async () => {
    expect((await get(`/api/issues/${issueId}`)).status).toBe(200);
    expect((await get(`/api/issues/${issueId}/comments`)).body).toEqual([]);
    for (const path of [`/api/issues/${foreignIssue}`, `/api/issues/${unrelatedIssue}`, `/api/tasks/${issueId}`, "/api/agents/me"])
      expect((await get(path)).status).toBe(403);
  });
  it("posts an attributed comment without reopening or checking out the completed issue", async () => {
    const before = (await db.select().from(issues).where(eq(issues.id, issueId)))[0];
    const response = await request(app).post(`/api/issues/${issueId}/comments`).auth(token, { type: "bearer" }).send({ body: "Relay test" });
    expect(response.status).toBe(201);
    expect(response.body.authorAgentId).toBe(agentId);
    const after = (await db.select().from(issues).where(eq(issues.id, issueId)))[0];
    // Adding a comment advances recency but must preserve all workflow fields.
    expect(after).toEqual({ ...before, updatedAt: after.updatedAt });
    expect(after.updatedAt.getTime()).toBeGreaterThanOrEqual(before.updatedAt.getTime());
    expect((await db.select().from(agents).where(eq(agents.id, agentId)))[0].status).toBe("paused");
  });
  it("honors supported service revocation on the next authenticated request", async () => {
    expect(await agentService(db).revokeKey(agentId, keyId)).toBeTruthy();
    expect((await get(`/api/issues/${issueId}`)).status).toBe(401);
  });
});
