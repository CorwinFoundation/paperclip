import express from "express";
import request from "supertest";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { errorHandler } from "../middleware/index.js";
import { instanceDispatchRoutes } from "../routes/instance-dispatch.js";
import { dispatchAdmission } from "../services/dispatch-admission.js";

const log = vi.hoisted(() => vi.fn());
vi.mock("../services/activity-log.js", () => ({ logActivity: log }));
let running: unknown = 0;
let queryError = false;
const where = vi.fn(async () => {
  if (queryError) throw Error("database offline");
  return [{ count: running }];
});
const db = { select: vi.fn(() => ({ from: vi.fn(() => {
  const rows = Promise.resolve([{ id: "company-a" }, { id: "company-b" }]);
  return Object.assign(rows, { where });
}) })) };
const admin = { type: "board", source: "session", userId: "admin", isInstanceAdmin: true };
function app(actor = admin) {
  const server = express();
  server.use(express.json());
  server.use((req, _res, next) => { req.actor = actor as typeof req.actor; next(); });
  server.use("/api", instanceDispatchRoutes(db as never));
  server.use(errorHandler);
  return server;
}
beforeEach(() => {
  dispatchAdmission.resume(); running = 0; queryError = false;
  vi.clearAllMocks(); log.mockResolvedValue(undefined);
});
describe("instance dispatch controls", () => {
  it.each([
    { type: "none" },
    { type: "agent", source: "agent_key", companyId: "company-a" },
    { type: "board", source: "session", isInstanceAdmin: false },
  ])("denies all controls to $type without instance authority", async actor => {
    const server = app(actor as typeof admin);
    await request(server).get("/api/instance/dispatch").expect(403);
    for (const action of ["drain", "resume"]) {
      await request(server).post(`/api/instance/dispatch/${action}`).send({}).expect(403);
    }
    expect(db.select).not.toHaveBeenCalled(); expect(log).not.toHaveBeenCalled();
  });
  it("drains without cancelling a running run, reports completion, and resumes", async () => {
    const server = app(); running = 1;
    const drain = await request(server).post("/api/instance/dispatch/drain").send({}).expect(200);
    expect(drain.body).toEqual({ closed: true, claims: 0, running: 1, restartReady: false });
    expect(log).toHaveBeenCalledTimes(2);
    expect(log.mock.calls.map(c => c[1].companyId)).toEqual(["company-a", "company-b"]);
    expect(dispatchAdmission.acquire()).toBeNull();
    running = 0;
    const status = await request(server).get("/api/instance/dispatch").expect(200);
    expect(status.body.restartReady).toBe(true);
    await request(server).post("/api/instance/dispatch/resume").send({}).expect(200);
    const release = dispatchAdmission.acquire(); expect(release).not.toBeNull(); release?.();
    expect(Object.keys(db)).toEqual(["select"]); // No run/session/queue mutations available.
  });
  it("allows local implicit board and rejects invalid bounded timeouts", async () => {
    const server = app({ ...admin, source: "local_implicit", isInstanceAdmin: false });
    await request(server).get("/api/instance/dispatch").expect(200);
    for (const timeoutMs of [-1, 30001, 0.5, "0"]) {
      await request(server).post("/api/instance/dispatch/drain").send({ timeoutMs }).expect(400);
    }
    expect(dispatchAdmission.closed).toBe(false); expect(log).not.toHaveBeenCalled();
  });
  it("fails closed when the authoritative count is unavailable and permits resume", async () => {
    const server = app(); queryError = true;
    await request(server).post("/api/instance/dispatch/drain").send({}).expect(500);
    expect(dispatchAdmission.closed).toBe(true);
    await request(server).post("/api/instance/dispatch/resume").send({}).expect(200);
    expect(dispatchAdmission.closed).toBe(false);
  });
  it("does not change admission if activity logging fails", async () => {
    log.mockRejectedValueOnce(Error("log offline"));
    await request(app()).post("/api/instance/dispatch/drain").send({}).expect(500);
    expect(dispatchAdmission.closed).toBe(false);
  });
});
