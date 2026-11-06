import { constants, lstatSync, openSync, closeSync, fstatSync, readFileSync, writeFileSync, fsyncSync } from "node:fs";
import { dirname, parse } from "node:path";
import { isDeepStrictEqual } from "node:util";
import { z } from "zod";
import { agentApiKeyScopeSchema } from "@paperclipai/shared";
import type { agentService } from "./agents.js";

export const WORKFLOW_ID = "0b845d09083c12076c3877efbf7a50ba";
export const BINDING_PATH = "/etc/paperclip/fixed-workflow-diagnostic/binding.json";
export const OUTPUT_PATH = "/etc/paperclip/fixed-workflow-diagnostic/credential.json";
export const KEY_NAME = "host-local-fixed-workflow-diagnostic-v1";
export const bindingSchema = z.object({
  version: z.literal(1), companyId: z.string().uuid(), agentId: z.string().uuid(),
  parentIssueId: z.string().uuid(), workflowId: z.literal(WORKFLOW_ID),
}).strict();
const receiptSchema = z.object({ binding: bindingSchema, keyId: z.string().uuid(), token: z.string().min(1) }).strict();
type Agents = Pick<ReturnType<typeof agentService>, "getById" | "createApiKey" | "listKeys" | "getKeyById" | "verifyApiKeyToken" | "revokeKey">;
export type ProvisionServices = {
  agents: Agents;
  companyExists(id: string): Promise<boolean>;
  parentCompany(id: string): Promise<string | null>;
  record(companyId: string, agentId: string, keyId: string): Promise<void>;
};

function protectedParents(path: string, owner: number) {
  let parent = dirname(path);
  for (;;) {
    const st = lstatSync(parent);
    if (!st.isDirectory() || st.isSymbolicLink() || (st.uid !== 0 && st.uid !== owner) || (st.mode & 0o022)) throw Error("Unsafe protected directory");
    if (parent === parse(parent).root) break;
    parent = dirname(parent);
  }
}
function readProtected(path: string, owner: number) {
  protectedParents(path, owner);
  const fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const st = fstatSync(fd);
    if (!st.isFile() || st.uid !== owner || (st.mode & 0o777) !== 0o600 || st.nlink !== 1 || st.size > 16384) throw Error("Unsafe protected file");
    return JSON.parse(readFileSync(fd, "utf8"));
  } finally { closeSync(fd); }
}

// Paths are injectable only for synthetic tests. The operator entry point takes no options.
export async function provisionFixedWorkflow(services: ProvisionServices,
  paths = { binding: BINDING_PATH, output: OUTPUT_PATH }, fileOwner = 0) {
  const binding = bindingSchema.parse(readProtected(paths.binding, fileOwner));
  protectedParents(paths.output, fileOwner);
  const scope = agentApiKeyScopeSchema.parse({ kind: "fixed_workflow_read_only",
    companyId: binding.companyId, parentIssueId: binding.parentIssueId, workflowId: WORKFLOW_ID });
  const agent = await services.agents.getById(binding.agentId);
  if (!await services.companyExists(binding.companyId) || !agent || agent.companyId !== binding.companyId ||
    agent.status === "pending_approval" || agent.status === "terminated" ||
    await services.parentCompany(binding.parentIssueId) !== binding.companyId) throw Error("Invalid identity binding");
  let receipt: z.infer<typeof receiptSchema> | undefined;
  try { receipt = receiptSchema.parse(readProtected(paths.output, fileOwner)); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
  const keys = (await services.agents.listKeys(binding.agentId)).filter(k => k.name === KEY_NAME && !k.revokedAt);
  if (receipt) {
    const key = await services.agents.getKeyById(receipt.keyId);
    if (JSON.stringify(receipt.binding) !== JSON.stringify(binding) || keys.length !== 1 || keys[0].id !== receipt.keyId ||
      !key || key.agentId !== binding.agentId || key.companyId !== binding.companyId || key.revokedAt ||
      !isDeepStrictEqual(key.scope, scope) || !await services.agents.verifyApiKeyToken(key.id, receipt.token)) {
      throw Error("Existing credential does not match native key");
    }
    return "existing";
  }
  if (keys.length) throw Error("Existing native key has no protected receipt; refusing duplicate issuance");
  // Exclusive destination doubles as a lock. Interrupted/empty receipts fail closed.
  const fd = openSync(paths.output, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
  let keyId: string | undefined;
  try {
    const key = await services.agents.createApiKey(binding.agentId, KEY_NAME, scope);
    keyId = key.id;
    await services.record(binding.companyId, binding.agentId, key.id);
    writeFileSync(fd, JSON.stringify({ binding, keyId: key.id, token: key.token }) + "\n");
    fsyncSync(fd);
    return "created";
  } catch (error) {
    if (keyId) await services.agents.revokeKey(binding.agentId, keyId);
    throw error;
  } finally { closeSync(fd); }
}
