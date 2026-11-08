import express from "express";
import { createHash } from "node:crypto";
import { agentApiKeys, agents } from "@paperclipai/db";
import { agentApiKeyScopeSchema } from "../../../packages/shared/src/validators/agent.js";
import { actorMiddleware } from "../middleware/auth.js";
import request from "supertest";
import { describe, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ getById: vi.fn(), listComments: vi.fn(), addComment: vi.fn(), log: vi.fn() }));
// The retained worktree currently borrows server/node_modules. Resolve the
// credential contract from this candidate, rather than the donor checkout.
vi.mock("@paperclipai/shared", async () => import("../../../packages/shared/src/index.js"));
vi.mock("../services/board-auth.js", () => ({ boardAuthService: () => ({ findBoardApiKeyByToken: vi.fn().mockResolvedValue(null) }) }));
vi.mock("../services/issues.js", () => ({ issueService: () => mocks }));
vi.mock("../services/activity-log.js", () => ({ logActivity: mocks.log }));
import { exactIssueRelay } from "../middleware/exact-issue-relay.js";
const companyId = "11111111-1111-4111-8111-111111111111";
const issueId = "22222222-2222-4222-8222-222222222222";
const commentId = "33333333-3333-4333-8333-333333333333";
const path = `/api/issues/${issueId}`;
function setup() {
  vi.clearAllMocks();
  mocks.getById.mockResolvedValue({ id: issueId, companyId, status: "done", executionRunId: null });
  mocks.listComments.mockResolvedValue([{ id: commentId, body: "existing" }]);
  mocks.addComment.mockResolvedValue({ id: commentId, body: "relay" });
  const app = express(); app.use(express.json());
  app.use((req, _res, next) => {
    req.actor = { type: "agent", source: "agent_key", agentId: commentId, keyId: commentId,
      companyId, keyScope: { kind: "exact_issue_relay", companyId, issueIds: [issueId] } }; next();
  });
  app.use(exactIssueRelay({} as any));
  app.use((_req, res) => res.status(418).end());
  return app;
}
describe("exact issue relay terminal routes", () => {
  it("reads bound issue and existing comments and posts attributed comment", async () => {
    const app = setup();
    expect((await request(app).get(path)).status).toBe(200);
    expect((await request(app).get(path + "/comments")).body[0].id).toBe(commentId);
    expect((await request(app).get(path + "/comments/" + commentId)).status).toBe(200);
    expect((await request(app).post(path + "/comments").send({ body: "relay" })).status).toBe(201);
    expect(mocks.addComment).toHaveBeenCalledWith(issueId, "relay", { agentId: commentId });
    expect(mocks.log).toHaveBeenCalledWith({}, expect.objectContaining({ action: "issue.comment_added" }));
  });
  it.each(["/api/agents/me", "/api/companies/" + companyId, "/api/agents/" + commentId,
    path + "/checkout", path + "/comments?limit=10", path + "/comments/", "/api/issues/" + commentId,
    "/api/companies/" + companyId + "/issues", "/api/companies/" + companyId + "/secrets",
    "/api/companies/" + companyId + "/skills", "/api/auth/session", "/api/health",
    "/api/agents/" + commentId + "/runtime-state", path + "/attachments"])("denies route %s", async route => {
    const app = setup(); expect((await request(app).get(route)).status).toBe(403);
    expect(mocks.getById).not.toHaveBeenCalled();
  });
  it("excludes deleted comments from list and single reads", async () => {
    const app = setup();
    mocks.listComments.mockResolvedValue([{ id: commentId, body: "deleted", deletedAt: new Date() }]);
    expect((await request(app).get(path + "/comments")).body).toEqual([]);
    expect((await request(app).get(path + "/comments/" + commentId)).status).toBe(404);
  });
  it.each(["patch", "delete", "put", "head", "options"] as const)("denies %s", async method => {
    expect((await request(setup())[method](path)).status).toBe(403);
  });
  it.each([{ body: "relay", resume: true }, { body: "relay", reopen: true },
    { body: "relay", authorType: "user" }, { body: "relay", metadata: {} }, { body: "" }])("denies expanded body %j", async body => {
    expect((await request(setup()).post(path + "/comments").send(body)).status).toBe(403);
    expect(mocks.addComment).not.toHaveBeenCalled();
  });
  it("denies company mismatch and POST issue mutations", async () => {
    const app = setup(); mocks.getById.mockResolvedValue({ id: issueId, companyId: commentId });
    expect((await request(app).get(path)).status).toBe(403);
    expect((await request(app).post(path).send({ status: "todo" })).status).toBe(403);
  });
});


describe("exact issue relay credential authentication", () => {
  const scope = { kind: "exact_issue_relay", companyId, issueIds: [issueId] };

  it.each([
    { ...scope, issueIds: [] },
    { ...scope, issueIds: ["BAC-250"] },
    { ...scope, issueIds: Array(51).fill(issueId) },
    { ...scope, companyId: undefined },
    { ...scope, allowMutations: true },
  ])("rejects malformed scope %j", malformed => {
    expect(agentApiKeyScopeSchema.safeParse(malformed).success).toBe(false);
  });

  function authenticatedApp(scopeConfig: unknown = scope, revoked = false) {
    const token = "test-only-relay-token";
    const key = { id: commentId, agentId: commentId, companyId,
      scopeConfig, keyHash: createHash("sha256").update(token).digest("hex") };
    const db = {
      select: vi.fn(() => ({ from: (table: unknown) => ({ where: () => Promise.resolve(
        table === agentApiKeys ? (revoked ? [] : [key]) :
        table === agents ? [{ id: commentId, companyId, status: "paused" }] : [],
      ) }) })),
      update: vi.fn(() => ({ set: () => ({ where: () => Promise.resolve() }) })),
    };
    const app = express();
    app.use(express.json());
    app.use(actorMiddleware(db as any, { deploymentMode: "authenticated" }));
    app.use(exactIssueRelay(db as any));
    app.use((req, res) => res.status(req.actor.type === "none" ? 401 : 418).end());
    return { app, token };
  }

  it("authenticates a stored scoped key before applying terminal routes", async () => {
    setup();
    const { app, token } = authenticatedApp();
    expect((await request(app).get(path).auth(token, { type: "bearer" })).status).toBe(200);
    expect((await request(app).get("/api/agents/me").auth(token, { type: "bearer" })).status).toBe(403);
    expect((await request(app).get("/api/tasks/" + issueId).auth(token, { type: "bearer" })).status).toBe(403);
    expect((await request(app).post(path + "/comments").auth(token, { type: "bearer" })
      .send({ body: "relay", resume: true })).status).toBe(403);
    expect(mocks.addComment).not.toHaveBeenCalled();
  });

  it("rejects revoked credentials before issue access", async () => {
    setup();
    const { app, token } = authenticatedApp(scope, true);
    expect((await request(app).get(path).auth(token, { type: "bearer" })).status).toBe(401);
    expect(mocks.getById).not.toHaveBeenCalled();
  });

  it("does not downgrade malformed stored scopes to standard keys", async () => {
    setup();
    const { app, token } = authenticatedApp({ ...scope, issueIds: [] });
    expect((await request(app).get(path).auth(token, { type: "bearer" })).status).toBe(403);
    expect(mocks.getById).not.toHaveBeenCalled();
  });
});
