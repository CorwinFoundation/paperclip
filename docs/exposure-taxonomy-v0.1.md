# Shared exposure taxonomy v0.1

This is a thin naming map, not a scoring or control framework. The same stable
ID is used when an exposure is inferred by a scanner probe, observed by the
listener, or derived from static configuration.

| Shared exposure ID | Scanner evidence family (observed) | Listener rule | Static `@backbond/agent-scan` rule | Gap / note |
|---|---|---|---|---|
| `exposure.secret_egress` | `p3_3_secret_exfil_floor`; `output_attack.*exfil` | `exposure.secret_egress` | `BB002` (secret capability + unrestricted egress) | Listener now emits on explicit `secret_access=true` plus `egress_authorized=false`. |
| `exposure.destructive_command` | `output_attack.shell_exfil`; `output_attack.sql_destructive` | `exposure.destructive_command` | `BB003` (destructive/consequential action without approval) | Static inspection cannot prove runtime execution; listener is observation. |
| `exposure.scope_creep` | tool-misuse / scope-escape probe families mapped to `ROBUSTNESS.TOOL_MISUSE.MCP_TOOL_INTEGRITY` | `exposure.scope_creep` | `BB006` (unrestricted scope) | Existing listener packs had only CRM/channel scope checks; shared tool-call name is new. |
| `exposure.unapproved_consequential_action` | `ROBUSTNESS.TOOL_MISUSE.CONSEQUENTIAL_ACTION_CONFIRMATION` | `exposure.unapproved_consequential_action` | `BB003` | Same exposure, with runtime approval value retained only for local evaluation. |
| `exposure.off_policy_tool_call` | `GOVERNANCE.CUSTOMER_POLICY.RISK_PACK_ADHERENCE` | `exposure.off_policy_tool_call` | `BB013` where tool metadata forces policy-breaking selection; otherwise no direct static equivalent | Static gap is intentional: configuration cannot establish an observed policy violation. |

Listener rule pack: `exposure_agent_pack.yaml`, version `0.1`. Findings are
monitor-only and do not affect `are_total` or any pillar score.
