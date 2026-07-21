"""AIUC-1 Path B lane C behavioral & MCP family packs (BEAAA-16272).

Parent build order BEAAA-16264 (15-family Path B expansion). This package holds
the Tier-3 agent-behavior packs and the Tier-4 conditional MCP pack:

  * human_escalation_flagging        (20 cases)
  * consequential_action_confirmation (15 cases)
  * customer_defined_risk_pack        (12 cases / reference tenant)
  * mcp_tool_integrity                (behind an applicability gate)

Every family is **reported-not-scored** (ADR-0045 seam): the runners are
evidence-only emitters. They do NOT import or mutate `rubric.scoring`, so the
golden-roster N=5 total/pillar deltas are 0 by construction.
"""
