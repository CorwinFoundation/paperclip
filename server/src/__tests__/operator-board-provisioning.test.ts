import { describe, expect, it, vi } from "vitest";
import { authUsers, boardApiKeys, companyMemberships, instanceUserRoles } from "@paperclipai/db";
import type { Db } from "@paperclipai/db";
import { boardAuthService } from "../services/board-auth.js";

const { logActivity } = vi.hoisted(() => ({ logActivity: vi.fn() }));
vi.mock("../services/activity-log.js", () => ({ logActivity }));

function database(companyExists = true) {
  const writes: Array<{ table: unknown; value: any }> = [];
  const tx = {
    select: () => ({ from: () => ({ where: async () => companyExists ? [{ id: "company" }] : [] }) }),
    insert: (table: unknown) => ({ values: (value: any) => {
      writes.push({ table, value });
      return { returning: async () => [{ ...value, id: "key", createdAt: new Date() }] };
    } }),
  };
  let committed = false;
  const db = { transaction: async (fn: (arg: typeof tx) => Promise<unknown>) => {
    const result = await fn(tx);
    committed = true;
    return result;
  } } as unknown as Db;
  return { db, writes, committed: () => committed };
}

describe("local operator provisioning", () => {
  it("creates a distinct machine identity and stores only a hash", async () => {
    const d = database();
    const persist = vi.fn(async (_credential: { keyId: string; token: string; userId: string }) => { expect(d.committed()).toBe(false); });
    const result = await boardAuthService(d.db).provisionOperatorBoardApiKey({
      name: "Operations operator", companyId: "company", persistCredential: persist,
    });
    const user = d.writes.find(w => w.table === authUsers)!.value;
    expect(user.id).toMatch(/^operator_/);
    expect(user.email).toBe(`${user.id}@operators.invalid`);
    expect(d.writes.find(w => w.table === instanceUserRoles)!.value.userId).toBe(user.id);
    expect(d.writes.find(w => w.table === companyMemberships)!.value.companyId).toBe("company");
    const credential = persist.mock.calls[0][0];
    expect(d.writes.find(w => w.table === boardApiKeys)!.value.keyHash).not.toBe(credential.token);
    expect(JSON.stringify(result)).not.toContain(credential.token);
    expect(d.committed()).toBe(true);
  });

  it("does not commit when credential persistence fails", async () => {
    const d = database();
    await expect(boardAuthService(d.db).provisionOperatorBoardApiKey({
      name: "operator", companyId: "company",
      persistCredential: async () => { throw new Error("disk full"); },
    })).rejects.toThrow("disk full");
    expect(d.committed()).toBe(false);
  });

  it("rejects an unknown company before creating an identity", async () => {
    const d = database(false);
    await expect(boardAuthService(d.db).provisionOperatorBoardApiKey({
      name: "operator", companyId: "missing", persistCredential: vi.fn(),
    })).rejects.toThrow("Company not found");
    expect(d.writes).toHaveLength(0);
  });
});
