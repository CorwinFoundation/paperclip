import { randomUUID } from "node:crypto";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { agents, companies, createDb, issues } from "@paperclipai/db";
import {
  getEmbeddedPostgresTestSupport,
  startEmbeddedPostgresTestDatabase,
} from "../__tests__/helpers/embedded-postgres.js";
import { accessService } from "./access.js";
import type { AuthorizationActor, AuthorizationResource } from "./authorization.js";

const embeddedPostgresSupport = await getEmbeddedPostgresTestSupport();
const describeEmbeddedPostgres = embeddedPostgresSupport.supported ? describe : describe.skip;

if (!embeddedPostgresSupport.supported) {
  console.warn(
    `Skipping issue-comment lineage authz tests on this host: ${embeddedPostgresSupport.reason ?? "unsupported environment"}`,
  );
}

describeEmbeddedPostgres("issue:comment manager-chain + child-to-ancestor authorization", () => {
  let db!: ReturnType<typeof createDb>;
  let tempDb: Awaited<ReturnType<typeof startEmbeddedPostgresTestDatabase>> | null = null;
  let access!: ReturnType<typeof accessService>;

  beforeAll(async () => {
    tempDb = await startEmbeddedPostgresTestDatabase("paperclip-issue-comment-authz-");
    db = createDb(tempDb.connectionString);
    access = accessService(db);
  }, 20_000);

  afterEach(async () => {
    await db.delete(issues);
    await db.delete(agents);
    await db.delete(companies);
  });

  afterAll(async () => {
    await tempDb?.cleanup();
  });

  async function makeCompany() {
    const [company] = await db
      .insert(companies)
      .values({
        name: `Authz ${randomUUID()}`,
        issuePrefix: `AZ${randomUUID().slice(0, 6).toUpperCase()}`,
      })
      .returning();
    return company!;
  }

  async function makeAgent(companyId: string, name: string, reportsTo: string | null = null) {
    const [agent] = await db
      .insert(agents)
      .values({ companyId, name, ...(reportsTo ? { reportsTo } : {}) })
      .returning();
    return agent!;
  }

  async function makeIssue(
    companyId: string,
    opts: { parentId?: string | null; assigneeAgentId?: string | null; status?: string },
  ) {
    const [issue] = await db
      .insert(issues)
      .values({
        companyId,
        title: `Issue ${randomUUID()}`,
        status: opts.status ?? "todo",
        priority: "medium",
        parentId: opts.parentId ?? null,
        assigneeAgentId: opts.assigneeAgentId ?? null,
      })
      .returning();
    return issue!;
  }

  function actor(companyId: string, agentId: string): AuthorizationActor {
    return { type: "agent", agentId, companyId } as AuthorizationActor;
  }

  function resourceOf(issue: {
    id: string;
    companyId: string;
    parentId: string | null;
    assigneeAgentId: string | null;
    status: string;
  }): AuthorizationResource {
    return {
      type: "issue",
      companyId: issue.companyId,
      issueId: issue.id,
      projectId: null,
      parentIssueId: issue.parentId,
      assigneeAgentId: issue.assigneeAgentId,
      assigneeUserId: null,
      status: issue.status,
    } as AuthorizationResource;
  }

  function decide(companyId: string, actorAgentId: string, issue: any, action: "issue:comment" | "issue:mutate") {
    return access.decide({
      actor: actor(companyId, actorAgentId),
      action,
      resource: resourceOf(issue),
      scope: {
        issueId: issue.id,
        parentIssueId: issue.parentId,
        assigneeAgentId: issue.assigneeAgentId,
      },
    });
  }

  it("allows a completed child's assignee to comment on the direct parent (BEAAA-21122 -> BEAAA-21121 shape)", async () => {
    const co = await makeCompany();
    const editor = await makeAgent(co.id, "Editor-Agent"); // parent owner, reports to nobody
    const cto = await makeAgent(co.id, "CTO"); // child owner, NOT managed by editor
    const parent = await makeIssue(co.id, { assigneeAgentId: editor.id, status: "done" });
    await makeIssue(co.id, { parentId: parent.id, assigneeAgentId: cto.id, status: "done" }); // completed child
    const d = await decide(co.id, cto.id, parent, "issue:comment");
    expect(d.allowed, JSON.stringify(d)).toBe(true);
    expect(d.reason).toBe("allow_issue_lineage_grant");
  });

  it("allows a multi-level descendant's assignee to comment on a grandparent", async () => {
    const co = await makeCompany();
    const owner = await makeAgent(co.id, "GrandparentOwner");
    const worker = await makeAgent(co.id, "GrandchildWorker");
    const gp = await makeIssue(co.id, { assigneeAgentId: owner.id, status: "in_progress" });
    const mid = await makeIssue(co.id, { parentId: gp.id, assigneeAgentId: owner.id, status: "in_progress" });
    await makeIssue(co.id, { parentId: mid.id, assigneeAgentId: worker.id, status: "todo" });
    const d = await decide(co.id, worker.id, gp, "issue:comment");
    expect(d.allowed, JSON.stringify(d)).toBe(true);
    expect(d.reason).toBe("allow_issue_lineage_grant");
  });

  it("allows a manager to comment on a subordinate's issue (manager chain)", async () => {
    const co = await makeCompany();
    const manager = await makeAgent(co.id, "Manager");
    const report = await makeAgent(co.id, "Report", manager.id);
    const issue = await makeIssue(co.id, { assigneeAgentId: report.id, status: "in_progress" });
    const d = await decide(co.id, manager.id, issue, "issue:comment");
    expect(d.allowed, JSON.stringify(d)).toBe(true);
    expect(d.reason).toBe("allow_manager_chain");
  });

  it("allows the assignee to comment on their own issue", async () => {
    const co = await makeCompany();
    const a = await makeAgent(co.id, "Owner");
    const issue = await makeIssue(co.id, { assigneeAgentId: a.id, status: "todo" });
    const d = await decide(co.id, a.id, issue, "issue:comment");
    expect(d.allowed, JSON.stringify(d)).toBe(true);
    expect(d.reason).toBe("allow_self");
  });

  it("denies an unrelated lateral agent from commenting", async () => {
    const co = await makeCompany();
    const owner = await makeAgent(co.id, "Owner");
    const lateral = await makeAgent(co.id, "Lateral");
    const issue = await makeIssue(co.id, { assigneeAgentId: owner.id, status: "in_progress" });
    const d = await decide(co.id, lateral.id, issue, "issue:comment");
    expect(d.allowed, JSON.stringify(d)).toBe(false);
    expect(d.reason).toBe("deny_missing_grant");
  });

  it("denies a sibling-issue assignee (sibling is not an ancestor)", async () => {
    const co = await makeCompany();
    const owner = await makeAgent(co.id, "ParentOwner");
    const siblingA = await makeAgent(co.id, "SiblingA");
    const siblingB = await makeAgent(co.id, "SiblingB");
    const parent = await makeIssue(co.id, { assigneeAgentId: owner.id, status: "in_progress" });
    const childA = await makeIssue(co.id, { parentId: parent.id, assigneeAgentId: siblingA.id, status: "todo" });
    await makeIssue(co.id, { parentId: parent.id, assigneeAgentId: siblingB.id, status: "todo" });
    const d = await decide(co.id, siblingB.id, childA, "issue:comment");
    expect(d.allowed, JSON.stringify(d)).toBe(false);
    expect(d.reason).toBe("deny_missing_grant");
  });

  it("does not let a parent-issue assignee comment down onto an unrelated child they do not own or manage", async () => {
    const co = await makeCompany();
    const parentOwner = await makeAgent(co.id, "ParentOwner");
    const childOwner = await makeAgent(co.id, "ChildOwner"); // does NOT report to parentOwner
    const parent = await makeIssue(co.id, { assigneeAgentId: parentOwner.id, status: "in_progress" });
    const child = await makeIssue(co.id, { parentId: parent.id, assigneeAgentId: childOwner.id, status: "todo" });
    // parentOwner is not the assignee of a descendant of `child`, nor a manager of childOwner
    const d = await decide(co.id, parentOwner.id, child, "issue:comment");
    expect(d.allowed, JSON.stringify(d)).toBe(false);
    expect(d.reason).toBe("deny_missing_grant");
  });

  it("denies cross-company: a descendant in another company does not grant access", async () => {
    const coA = await makeCompany();
    const coB = await makeCompany();
    const ownerA = await makeAgent(coA.id, "OwnerA");
    const childOwnerA = await makeAgent(coA.id, "ChildOwnerA");
    const outsiderB = await makeAgent(coB.id, "OutsiderB");
    const parentA = await makeIssue(coA.id, { assigneeAgentId: ownerA.id, status: "in_progress" });
    await makeIssue(coA.id, { parentId: parentA.id, assigneeAgentId: childOwnerA.id, status: "todo" });
    // outsiderB (company B) tries to comment on company A's parent issue
    const d = await access.decide({
      actor: actor(coB.id, outsiderB.id),
      action: "issue:comment",
      resource: resourceOf(parentA),
      scope: { issueId: parentA.id },
    });
    expect(d.allowed, JSON.stringify(d)).toBe(false);
  });

  it("keeps issue:mutate denied for manager-chain (comment-only grant)", async () => {
    const co = await makeCompany();
    const manager = await makeAgent(co.id, "Manager");
    const report = await makeAgent(co.id, "Report", manager.id);
    const issue = await makeIssue(co.id, { assigneeAgentId: report.id, status: "in_progress" });
    const d = await decide(co.id, manager.id, issue, "issue:mutate");
    expect(d.allowed, JSON.stringify(d)).toBe(false);
  });

  it("keeps issue:mutate denied for a child-issue assignee on the parent", async () => {
    const co = await makeCompany();
    const parentOwner = await makeAgent(co.id, "ParentOwner");
    const childOwner = await makeAgent(co.id, "ChildOwner");
    const parent = await makeIssue(co.id, { assigneeAgentId: parentOwner.id, status: "in_progress" });
    await makeIssue(co.id, { parentId: parent.id, assigneeAgentId: childOwner.id, status: "todo" });
    const d = await decide(co.id, childOwner.id, parent, "issue:mutate");
    expect(d.allowed, JSON.stringify(d)).toBe(false);
  });
});
