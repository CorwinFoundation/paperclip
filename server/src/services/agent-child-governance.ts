import { z } from "zod";

export const AGENT_CHILD_GOVERNANCE_ENV = "PAPERCLIP_AGENT_CHILD_GOVERNANCE";

const issuePrioritySchema = z.enum(["critical", "high", "medium", "low"]);

const companyPolicySchema = z.object({
  maxChildren: z.number().int().min(1).max(25),
  singletonAssigneeAgentIds: z.array(z.string().uuid()).default([]),
  overrideAgentIds: z.array(z.string().uuid()).default([]),
  enforcePriorityCeiling: z.boolean().default(true),
}).strict();

const governanceConfigSchema = z.record(z.string().uuid(), companyPolicySchema);

export type AgentChildGovernancePolicy = z.infer<typeof companyPolicySchema>;

export type GovernedExistingChild = {
  id: string;
  identifier: string | null;
  status: string;
  assigneeAgentId: string | null;
};

export type AgentChildGovernanceViolation = {
  code:
    | "agent_child_singleton_exists"
    | "agent_child_limit_reached"
    | "agent_child_priority_exceeds_parent";
  message: string;
  details: Record<string, unknown>;
};

export function readAgentChildGovernancePolicy(
  companyId: string,
  rawConfig = process.env[AGENT_CHILD_GOVERNANCE_ENV],
): AgentChildGovernancePolicy | null {
  if (!rawConfig?.trim()) return null;

  let parsedJson: unknown;
  try {
    parsedJson = JSON.parse(rawConfig);
  } catch {
    throw new Error(`${AGENT_CHILD_GOVERNANCE_ENV} must contain valid JSON`);
  }

  const parsed = governanceConfigSchema.safeParse(parsedJson);
  if (!parsed.success) {
    throw new Error(`${AGENT_CHILD_GOVERNANCE_ENV} is invalid: ${parsed.error.issues[0]?.message ?? "unknown error"}`);
  }
  return parsed.data[companyId] ?? null;
}

const PRIORITY_RANK: Record<z.infer<typeof issuePrioritySchema>, number> = {
  low: 0,
  medium: 1,
  high: 2,
  critical: 3,
};

export function evaluateAgentChildGovernance(input: {
  policy: AgentChildGovernancePolicy;
  actorAgentId: string;
  parentPriority: string;
  childPriority: string | null | undefined;
  assigneeAgentId: string | null | undefined;
  existingChildren: GovernedExistingChild[];
}): AgentChildGovernanceViolation | null {
  const activeChildren = input.existingChildren.filter((child) => child.status !== "cancelled");
  const singletonAssignee = input.assigneeAgentId &&
    input.policy.singletonAssigneeAgentIds.includes(input.assigneeAgentId)
    ? input.assigneeAgentId
    : null;

  if (singletonAssignee) {
    const existing = activeChildren.find((child) => child.assigneeAgentId === singletonAssignee);
    if (existing) {
      return {
        code: "agent_child_singleton_exists",
        message: `Reuse existing child ${existing.identifier ?? existing.id}; this assignee may have only one non-cancelled direct child per parent`,
        details: {
          existingIssueId: existing.id,
          existingIdentifier: existing.identifier,
          assigneeAgentId: singletonAssignee,
        },
      };
    }
  }

  const hasOverride = input.policy.overrideAgentIds.includes(input.actorAgentId);
  if (!hasOverride && activeChildren.length >= input.policy.maxChildren) {
    return {
      code: "agent_child_limit_reached",
      message: `Parent already has the maximum ${input.policy.maxChildren} non-cancelled direct child issues; reuse an existing child or ask an override agent to create the exception`,
      details: {
        activeChildCount: activeChildren.length,
        maxChildren: input.policy.maxChildren,
      },
    };
  }

  if (input.policy.enforcePriorityCeiling && !hasOverride) {
    const parentPriority = issuePrioritySchema.safeParse(input.parentPriority);
    const childPriority = issuePrioritySchema.safeParse(input.childPriority ?? "medium");
    if (
      parentPriority.success &&
      childPriority.success &&
      PRIORITY_RANK[childPriority.data] > PRIORITY_RANK[parentPriority.data]
    ) {
      return {
        code: "agent_child_priority_exceeds_parent",
        message: `Child priority ${childPriority.data} cannot exceed parent priority ${parentPriority.data} without an override agent`,
        details: {
          childPriority: childPriority.data,
          parentPriority: parentPriority.data,
        },
      };
    }
  }

  return null;
}
