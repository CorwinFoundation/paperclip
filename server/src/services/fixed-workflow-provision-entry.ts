import { createDb } from "@paperclipai/db";
import { agentService } from "./agents.js";
import { companyService } from "./companies.js";
import { issueService } from "./issues.js";
import { logActivity } from "./activity-log.js";
import { provisionFixedWorkflow } from "./fixed-workflow-provision.js";

export async function runFixedWorkflowProvisioning(databaseUrl: string) {
  const db = createDb(databaseUrl);
  try {
    await provisionFixedWorkflow({
      agents: agentService(db),
      companyExists: async id => Boolean(await companyService(db).getById(id)),
      parentCompany: async id => (await issueService(db).getById(id))?.companyId ?? null,
      record: async (companyId, agentId, keyId) => {
        await logActivity(db, { companyId, actorType: "system", actorId: "host-local-fixed-workflow-provision",
          action: "agent.api_key_created", entityType: "agent", entityId: agentId, details: { keyId } });
      },
    });
  } finally { await db.$client.end(); }
}
