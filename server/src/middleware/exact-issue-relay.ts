import type { RequestHandler } from "express";
import { z } from "zod";
import type { Db } from "@paperclipai/db";
import { issueService } from "../services/issues.js";
import { logActivity } from "../services/activity-log.js";

const commentBody = z.object({ body: z.string().trim().min(1).max(50000) }).strict();

// Terminal handler: no legacy route, low-trust preset, membership, or permission
// default can expand this credential. Comments never resume or reopen work.
export function exactIssueRelay(db: Db): RequestHandler {
  const svc = issueService(db);
  return async (req, res, next) => {
    const scope = req.actor.keyScope;
    if (scope?.kind !== "exact_issue_relay") return next();
    const deny = () => { res.status(403).json({ error: "Exact issue relay scope denied" }); };
    if (req.actor.type !== "agent" || req.actor.source !== "agent_key" ||
        !req.actor.keyId || !req.actor.agentId || req.actor.companyId !== scope.companyId) return deny();
    const match = /^\/api\/issues\/([a-f0-9-]{36})(\/comments(?:\/([a-f0-9-]{36}))?)?$/.exec(req.originalUrl);
    if (!match || !scope.issueIds.includes(match[1])) return deny();
    const read = req.method === "GET";
    const write = req.method === "POST" && match[2] === "/comments";
    if (!read && !write) return deny();
    if (read && (req.headers["transfer-encoding"] ||
        (req.headers["content-length"] && req.headers["content-length"] !== "0"))) return deny();
    const parsed = write ? commentBody.safeParse(req.body) : null;
    if (write && !parsed?.success) return deny();
    const issue = await svc.getById(match[1]);
    if (!issue || issue.companyId !== scope.companyId) return deny();
    if (read) {
      if (!match[2]) { res.json(issue); return; }
      const comments = (await svc.listComments(issue.id)).filter(row => !row.deletedAt);
      if (match[3]) {
        const comment = comments.find(row => row.id === match[3]);
        if (!comment) { res.status(404).json({ error: "Comment not found" }); return; }
        res.json(comment);
      } else res.json(comments);
      return;
    }
    if (!parsed?.success) return deny();
    const comment = await svc.addComment(issue.id, parsed.data.body, { agentId: req.actor.agentId });
    await logActivity(db, {
      companyId: issue.companyId, actorType: "agent", actorId: req.actor.agentId,
      agentId: req.actor.agentId, action: "issue.comment_added", entityType: "issue",
      entityId: issue.id, details: { commentId: comment.id, credentialScope: scope.kind },
    });
    res.status(201).json(comment);
  };
}
