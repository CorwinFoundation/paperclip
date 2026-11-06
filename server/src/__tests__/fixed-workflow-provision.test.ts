import { chmodSync, mkdtempSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { homedir } from "node:os";
import { bindingSchema, KEY_NAME, provisionFixedWorkflow, WORKFLOW_ID } from "../services/fixed-workflow-provision.js";
const binding = { version: 1, companyId: "11111111-1111-4111-8111-111111111111", agentId: "22222222-2222-4222-8222-222222222222",
  parentIssueId: "33333333-3333-4333-8333-333333333333", workflowId: WORKFLOW_ID };
const keyId = "44444444-4444-4444-8444-444444444444";
const dirs: string[] = [];
afterEach(() => { for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true }); });
function setup() {
  // /root is protected; /tmp intentionally fails ancestor validation.
  const dir = mkdtempSync(homedir() + "/paperclip-synthetic-"); dirs.push(dir);
  const paths = { binding: dir + "/binding.json", output: dir + "/credential.json" };
  writeFileSync(paths.binding, JSON.stringify(binding), { mode: 0o600 });
  let native: any = null;
  const services = {
    agents: {
      getById: vi.fn(async () => ({ companyId: binding.companyId, status: "idle" })),
      createApiKey: vi.fn(async (_id: string, name: string, scope: any) => {
        native = { id: keyId, name, scope, agentId: binding.agentId, companyId: binding.companyId, revokedAt: null };
        return { ...native, token: "synthetic-test-token" };
      }),
      listKeys: vi.fn(async () => native ? [native] : []), getKeyById: vi.fn(async () => native),
      verifyApiKeyToken: vi.fn(async (_id: string, token: string) => token === "synthetic-test-token"),
      revokeKey: vi.fn(async () => null),
    },
    companyExists: vi.fn(async () => true), parentCompany: vi.fn(async () => binding.companyId), record: vi.fn(async () => {}),
  };
  return { paths, services: services as any, native: () => native };
}
describe("protected diagnostic provisioning", () => {
  it("creates only the pinned scope and reuses a verified receipt", async () => {
    const { paths, services } = setup();
    expect(await provisionFixedWorkflow(services, paths, process.getuid!())).toBe("created");
    expect(services.agents.createApiKey).toHaveBeenCalledWith(binding.agentId, KEY_NAME, {
      kind: "fixed_workflow_read_only", companyId: binding.companyId, parentIssueId: binding.parentIssueId, workflowId: WORKFLOW_ID,
    });
    expect(statSync(paths.output).mode & 0o777).toBe(0o600);
    expect(await provisionFixedWorkflow(services, paths, process.getuid!())).toBe("existing");
    expect(services.agents.createApiKey).toHaveBeenCalledTimes(1);
  });
  it.each([{ workflowId: "f".repeat(32) }, { scope: { kind: "standard" } }, { keyType: "task_bridge" }, { companyId: "invalid" }])("rejects expanded bindings %j", async patch => {
    const { paths, services } = setup(); writeFileSync(paths.binding, JSON.stringify({ ...binding, ...patch }));
    await expect(provisionFixedWorkflow(services, paths, process.getuid!())).rejects.toThrow(); expect(services.agents.createApiKey).not.toHaveBeenCalled();
  });
  it.each(["company", "agent", "parent", "missing", "mode", "symlink", "destination"])("rejects unsafe %s before issuance", async kind => {
    const { paths, services } = setup();
    if (kind === "company") services.companyExists.mockResolvedValue(false);
    if (kind === "agent") services.agents.getById.mockResolvedValue({ companyId: keyId });
    if (kind === "parent") services.parentCompany.mockResolvedValue(keyId);
    if (kind === "missing") rmSync(paths.binding);
    if (kind === "mode") chmodSync(paths.binding, 0o644);
    if (kind === "symlink") { rmSync(paths.binding); symlinkSync("/dev/null", paths.binding); }
    if (kind === "destination") writeFileSync(paths.output, "", { mode: 0o600 });
    await expect(provisionFixedWorkflow(services, paths, process.getuid!())).rejects.toThrow(); expect(services.agents.createApiKey).not.toHaveBeenCalled();
  });
  it("rejects a forged receipt token without issuing another key", async () => {
    const { paths, services } = setup(); await provisionFixedWorkflow(services, paths, process.getuid!());
    const receipt = JSON.parse(readFileSync(paths.output, "utf8")); receipt.token = "forged"; writeFileSync(paths.output, JSON.stringify(receipt));
    await expect(provisionFixedWorkflow(services, paths, process.getuid!())).rejects.toThrow(); expect(services.agents.createApiKey).toHaveBeenCalledTimes(1);
  });
  it("revokes a newly created key when recording fails", async () => {
    const { paths, services } = setup(); services.record.mockRejectedValue(Error("test"));
    await expect(provisionFixedWorkflow(services, paths, process.getuid!())).rejects.toThrow(); expect(services.agents.revokeKey).toHaveBeenCalledWith(binding.agentId, keyId);
  });
});
it("requires an explicit binding", () => { expect(bindingSchema.safeParse({}).success).toBe(false); });
