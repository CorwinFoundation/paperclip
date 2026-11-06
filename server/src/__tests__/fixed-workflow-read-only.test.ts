import express from "express";
import request from "supertest";
import { describe, expect, it, vi } from "vitest";
import type { Db } from "@paperclipai/db";
import { agentApiKeyScopeSchema } from "@paperclipai/shared";
import { fixedWorkflowReadOnly } from "../middleware/fixed-workflow-read-only.js";

const companyId = "11111111-1111-4111-8111-111111111111";
const parentIssueId = "22222222-2222-4222-8222-222222222222";
const childId = "33333333-3333-4333-8333-333333333333";
const scope = { kind: "fixed_workflow_read_only", companyId, parentIssueId,
  workflowId: "0b845d09083c12076c3877efbf7a50ba" };
const list = `/api/companies/${companyId}/issues?parentId=${parentIssueId}`;
function setup(rows: unknown[][] = [], actorCompany = companyId) {
  const select = vi.fn(() => {
    const result = Promise.resolve(rows.shift() ?? []);
    return { from: () => ({ where: () => Object.assign(result, { orderBy: () => result }) }) };
  });
  const app = express();
  app.use((req, _res, next) => {
    req.actor = { type: "agent", source: "agent_key", agentId: childId,
      keyId: childId, companyId: actorCompany, keyScope: scope as any };
    next();
  });
  app.use(fixedWorkflowReadOnly({ select } as unknown as Db));
  app.use((_req, res) => res.status(418).end());
  return { app, select };
}

describe("fixed workflow read-only credential", () => {
  it("requires all bindings and rejects unknown scope fields", () => {
    expect(agentApiKeyScopeSchema.safeParse(scope).success).toBe(true);
    expect(agentApiKeyScopeSchema.safeParse({ ...scope, parentIssueId: undefined }).success).toBe(false);
    expect(agentApiKeyScopeSchema.safeParse({ ...scope, projectId: childId }).success).toBe(false);
  });
  it("returns the bound child list and child comments", async () => {
    const { app } = setup([[{ id: parentIssueId }], [{ id: childId }],
      [{ id: parentIssueId }], [{ id: childId }], [{ body: "review" }]]);
    expect((await request(app).get(list)).body).toEqual([{ id: childId }]);
    expect((await request(app).get(`/api/issues/${childId}/comments`)).body).toEqual([{ body: "review" }]);
  });
  it.each(["post", "patch", "put", "delete", "head", "options"] as const)("denies %s", async method => {
    const { app, select } = setup();
    expect((await request(app)[method](list)).status).toBe(403);
    expect(select).not.toHaveBeenCalled();
  });
  it.each([
    "/api/health", "/api/agents/me", "/api/auth/session", "/api/attachments/file/content",
    `/api/issues/${childId}`, `/api/issues/${childId}/comments?limit=10`,
    `/api/companies/${companyId}/issues`, list + "&q=anything",
    list + `&parentId=${parentIssueId}`, list.replace(parentIssueId, childId),
    list.replace(companyId, childId), list.replace("/issues?", "/issues/?"),
  ])("denies unrelated or expanded route %s", async route => {
    const { app, select } = setup();
    expect((await request(app).get(route)).status).toBe(403);
    expect(select).not.toHaveBeenCalled();
  });
  it("denies cross-company credentials before querying", async () => {
    const { app, select } = setup([], childId);
    expect((await request(app).get(list)).status).toBe(403);
    expect(select).not.toHaveBeenCalled();
  });
  it("denies absent parent and unrelated child", async () => {
    expect((await request(setup([[]]).app).get(list)).status).toBe(403);
    expect((await request(setup([[{ id: parentIssueId }], []]).app)
      .get(`/api/issues/${childId}/comments`)).status).toBe(403);
  });
  it("denies GET bodies", async () => {
    const { app, select } = setup();
    expect((await request(app).get(list).send({ action: "write" })).status).toBe(403);
    expect(select).not.toHaveBeenCalled();
  });
});
