import type { RequestHandler } from "express";
import { and, asc, eq, isNull } from "drizzle-orm";
import { issues, issueComments, type Db } from "@paperclipai/db";

// Terminal handler: restricted credentials never reach general routes, including
// routes that do not use the central authorization service.
export function fixedWorkflowReadOnly(db: Db): RequestHandler {
  return async (req, res, next) => {
    const scope = req.actor.keyScope;
    if (req.actor.type !== "agent" || scope?.kind !== "fixed_workflow_read_only") return next();
    const deny = () => { res.status(403).json({ error: "Fixed workflow read-only scope denied" }); };
    if (req.actor.source !== "agent_key" || !req.actor.keyId ||
        req.actor.companyId !== scope.companyId || req.method !== "GET" ||
        (req.headers["content-length"] && req.headers["content-length"] !== "0") ||
        req.headers["transfer-encoding"]) return deny();
    const url = new URL(req.originalUrl, "http://localhost");
    const listPath = `/api/companies/${scope.companyId}/issues`;
    const commentMatch = /^\/api\/issues\/([a-f0-9-]{36})\/comments$/.exec(url.pathname);
    const list = url.pathname === listPath &&
      [...url.searchParams.keys()].length === 1 &&
      url.searchParams.get("parentId") === scope.parentIssueId;
    if (!list && !(commentMatch && !url.search)) return deny();
    const parent = await db.select({ id: issues.id }).from(issues)
      .where(and(eq(issues.id, scope.parentIssueId), eq(issues.companyId, scope.companyId)));
    if (!parent.length) return deny();
    if (list) {
      res.json(await db.select().from(issues).where(and(
        eq(issues.companyId, scope.companyId), eq(issues.parentId, scope.parentIssueId),
      )));
      return;
    }
    const childId = commentMatch![1];
    const child = await db.select({ id: issues.id }).from(issues).where(and(
      eq(issues.id, childId), eq(issues.companyId, scope.companyId),
      eq(issues.parentId, scope.parentIssueId),
    ));
    if (!child.length) return deny();
    res.json(await db.select().from(issueComments).where(and(
      eq(issueComments.companyId, scope.companyId), eq(issueComments.issueId, childId),
      isNull(issueComments.deletedAt),
    )).orderBy(asc(issueComments.createdAt), asc(issueComments.id)));
  };
}
