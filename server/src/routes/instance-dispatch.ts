import { Router } from "express";
import type { Db } from "@paperclipai/db";
import { companies, heartbeatRuns } from "@paperclipai/db";
import { count, eq } from "drizzle-orm";
import { z } from "zod";
import { validate } from "../middleware/validate.js";
import { logActivity } from "../services/activity-log.js";
import { createDispatchControl, dispatchAdmission } from "../services/dispatch-admission.js";
import { assertInstanceAdmin, getActorInfo } from "./authz.js";

const drainSchema = z.object({ timeoutMs: z.number().int().min(0).max(30_000).default(0) }).strict();

// One control per process: concurrent router instances must share resume revisions.
let control: ReturnType<typeof createDispatchControl> | undefined;
export function instanceDispatchRoutes(db: Db) {
  const router = Router();
  const dispatch = control ??= createDispatchControl(dispatchAdmission, async () => {
    const rows = await db.select({ count: count() }).from(heartbeatRuns)
      .where(eq(heartbeatRuns.status, "running"));
    // Missing results must fail closed, rather than treating unavailable data as idle.
    return rows[0]?.count as number;
  });

  router.use("/instance/dispatch", (req, _res, next) => {
    assertInstanceAdmin(req);
    next();
  });
  router.get("/instance/dispatch", async (_req, res) => {
    res.json(await dispatch.status());
  });
  for (const action of ["drain", "resume"] as const) {
    router.post(`/instance/dispatch/${action}`, validate(action === "drain" ? drainSchema : z.object({}).strict()), async (req, res) => {
      const actor = getActorInfo(req);
      // Record intent before changing process admission; a log failure leaves it untouched.
      const companyRows = await db.select({ id: companies.id }).from(companies);
      await Promise.all(companyRows.map(({ id: companyId }) => logActivity(db, {
        companyId, ...actor,
        action: `instance.dispatch.${action}_requested`,
        entityType: "instance", entityId: "default",
        details: action === "drain" ? { timeoutMs: req.body.timeoutMs } : {},
      })));
      const result = action === "drain"
        ? await dispatch.drain(req.body.timeoutMs)
        : dispatch.resume();
      res.json(result);
    });
  }
  return router;
}
