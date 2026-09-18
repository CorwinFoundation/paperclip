import { describe, expect, it } from "vitest";
import {
  evaluateAgentChildGovernance,
  readAgentChildGovernancePolicy,
  type AgentChildGovernancePolicy,
} from "../services/agent-child-governance.js";

const companyId = "11111111-1111-4111-8111-111111111111";
const ownerAgentId = "22222222-2222-4222-8222-222222222222";
const overrideAgentId = "33333333-3333-4333-8333-333333333333";
const qaAgentId = "44444444-4444-4444-8444-444444444444";

const policy: AgentChildGovernancePolicy = {
  maxChildren: 3,
  singletonAssigneeAgentIds: [qaAgentId],
  overrideAgentIds: [overrideAgentId],
  enforcePriorityCeiling: true,
};

function child(overrides: Partial<{
  id: string;
  identifier: string | null;
  status: string;
  assigneeAgentId: string | null;
}> = {}) {
  return {
    id: "55555555-5555-4555-8555-555555555555",
    identifier: "PAP-10",
    status: "done",
    assigneeAgentId: null,
    ...overrides,
  };
}

describe("agent child governance", () => {
  it("reads a company-scoped policy and supplies optional defaults", () => {
    const parsed = readAgentChildGovernancePolicy(companyId, JSON.stringify({
      [companyId]: {
        maxChildren: 3,
      },
    }));

    expect(parsed).toEqual({
      maxChildren: 3,
      singletonAssigneeAgentIds: [],
      overrideAgentIds: [],
      enforcePriorityCeiling: true,
    });
  });

  it("rejects a second non-cancelled child for a singleton assignee", () => {
    const violation = evaluateAgentChildGovernance({
      policy,
      actorAgentId: ownerAgentId,
      parentPriority: "high",
      childPriority: "medium",
      assigneeAgentId: qaAgentId,
      existingChildren: [child({ assigneeAgentId: qaAgentId })],
    });

    expect(violation).toEqual(expect.objectContaining({
      code: "agent_child_singleton_exists",
      details: expect.objectContaining({
        existingIssueId: "55555555-5555-4555-8555-555555555555",
        existingIdentifier: "PAP-10",
      }),
    }));
  });

  it("allows reuse of a singleton slot after the prior child is cancelled", () => {
    const violation = evaluateAgentChildGovernance({
      policy,
      actorAgentId: ownerAgentId,
      parentPriority: "medium",
      childPriority: "medium",
      assigneeAgentId: qaAgentId,
      existingChildren: [child({ status: "cancelled", assigneeAgentId: qaAgentId })],
    });

    expect(violation).toBeNull();
  });

  it("caps non-cancelled direct children for ordinary agents", () => {
    const violation = evaluateAgentChildGovernance({
      policy,
      actorAgentId: ownerAgentId,
      parentPriority: "high",
      childPriority: "medium",
      assigneeAgentId: null,
      existingChildren: [
        child({ id: "55555555-5555-4555-8555-555555555551" }),
        child({ id: "55555555-5555-4555-8555-555555555552" }),
        child({ id: "55555555-5555-4555-8555-555555555553" }),
      ],
    });

    expect(violation?.code).toBe("agent_child_limit_reached");
  });

  it("rejects child priority above the parent for ordinary agents", () => {
    const violation = evaluateAgentChildGovernance({
      policy,
      actorAgentId: ownerAgentId,
      parentPriority: "medium",
      childPriority: "critical",
      assigneeAgentId: null,
      existingChildren: [],
    });

    expect(violation?.code).toBe("agent_child_priority_exceeds_parent");
  });

  it("lets an override agent exceed the count and priority ceilings", () => {
    const violation = evaluateAgentChildGovernance({
      policy,
      actorAgentId: overrideAgentId,
      parentPriority: "low",
      childPriority: "critical",
      assigneeAgentId: null,
      existingChildren: [
        child({ id: "55555555-5555-4555-8555-555555555551" }),
        child({ id: "55555555-5555-4555-8555-555555555552" }),
        child({ id: "55555555-5555-4555-8555-555555555553" }),
      ],
    });

    expect(violation).toBeNull();
  });

  it("does not let an override agent create a duplicate singleton child", () => {
    const violation = evaluateAgentChildGovernance({
      policy,
      actorAgentId: overrideAgentId,
      parentPriority: "low",
      childPriority: "critical",
      assigneeAgentId: qaAgentId,
      existingChildren: [child({ assigneeAgentId: qaAgentId })],
    });

    expect(violation?.code).toBe("agent_child_singleton_exists");
  });
});
