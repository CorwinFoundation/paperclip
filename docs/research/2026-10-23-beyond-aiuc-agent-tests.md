# Agent tests beyond AIUC: source review and first additions

Internal technical review started 23 October 2026, extended 24 October. Current comparison tree:
`8ad1af149ddf0d1d27cab0b42000dc765e5dabf4`. The dated
[follow-up gap review](2026-10-24-agent-workflow-gap-review.md) identifies additions and sources rechecked;
upstream branches and documentation can change. The machine-readable decisions
are in [the source catalog](../../config/research/agent-evaluation-sources-20261023.json).

The current addition is twenty original agent-facing scenarios: ten attacks and
ten matched benign controls. The initial six pairs exercise tool descriptions, server-qualified
tool identity, tool results, retrieved memory, claimed peer authority and hidden
extra actions. Four new pairs cover workflow-file instructions, poisoned summary parameters,
forged approval receipts and transient tool-error recovery. An agent must complete the legitimate read-and-record task while
respecting its grant. Refusing every request fails utility. Attempting an
unauthorized action fails even when the inert recorder blocks its execution.

These are opt-in, report-only tests. They do not increase production executable
coverage, change the BackBond Score, alter underwriting, or reproduce any named
upstream benchmark. No new dependency or paid CI route is added.

## Publications, benchmarks and libraries reviewed

| Source | What is relevant | Decision for Scanner |
|---|---|---|
| [INFUSE](https://arxiv.org/abs/2610.20776v1) | Workflow input surfaces, including files and runtime error messages; preprint submitted 14 October 2026. | Add original workflow-file and actual tool-error/retry pairs. No upstream tasks or long-horizon shell environment are imported. |
| [MCP-SafetyBench](https://arxiv.org/abs/2512.15163v2) | Real MCP workflows and attacks across server, host and user layers. | Add original description, result and tool-identity tests. Defer the upstream real-service suite, which can perform actual repository/filesystem operations. |
| [ASB](https://arxiv.org/abs/2410.02644v4) | Attacks through tools and memory, with a utility/security trade-off. | Add a retrieved-memory authority attack and paired control. Full mixed attacks and backdoors remain unimplemented here. |
| [Agent-SafetyBench](https://arxiv.org/abs/2412.14470v2) | Interactive environments covering multiple failure modes. | Relevant next integration; defer its environment dependencies and ShieldAgent scorer until separately bounded and calibrated. |
| [SHADE-Arena](https://www.anthropic.com/research/shade-arena-sabotage-monitoring) | Legitimate tasks paired with hidden sabotage and monitor evaluation. | Add an original hidden-alert-disable action alongside the ticket task. We do not evaluate monitor quality or reproduce the private task split. |
| [AgentDojo](https://arxiv.org/abs/2406.13352v3) | Tool-returned data can redirect an agent away from the user's task. | Existing adapter overlaps. Adopt separate attack and task-completion outcomes for this pilot. |
| [InjecAgent](https://github.com/uiuc-kang-lab/InjecAgent) | Indirect injections in tool-integrated agents. | Retain the existing pinned records and inert-recorder pilot; no duplicate imported subset. |
| [ToolSandbox](https://machinelearning.apple.com/research/toolsandbox-stateful-conversational-llm-benchmark) | Stateful tools, intermediate milestones and final world state. | Require a prior read result before recording its fields. A guessed parallel read/write cannot pass. |
| [tau2-bench](https://github.com/sierra-research/tau2-bench) | Current documentation adds multilingual text, voice and entity-intake work. | Compare the existing pinned version and supported target surfaces before upgrading or importing tasks. |
| [AgentHarm](https://arxiv.org/abs/2410.09024v3) | Multi-step harmful task execution rather than refusal text alone. | Existing Scanner source-inspired cases and runtime/license boundary already exist; preserve them. |
| [OWASP Agentic Top 10](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) | Threat taxonomy for agent-specific risks. | Use for review and peer/tool trust boundaries. A taxonomy reference does not establish conformance. |
| [OWASP Red Teaming Manual 1.0](https://genai.owasp.org/resource/genai-red-teaming-manual-version-1-0/) | Published 20 October 2026; scoped testing, reproducible findings and measured outcomes. | Adopt bounded execution and evidence receipts. This review covers its official overview, not an audit against the complete manual. |
| [OWASP ACS](https://genai.owasp.org/resource/agent-control-standard-acs/) | Runtime middleware hooks and portable controls. | Evaluate as a future evidence adapter; no ACS conformance claim or new runtime dependency. |
| [MCP security guidance](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices) | OAuth consent, token passthrough, state-handle hijacking and SSRF. | Add a forged-consent behavioral test; defer protocol-level verification. These tests cannot prove an MCP host validates tokens, scopes or sessions. |
| [Inspect](https://inspect.aisi.org.uk/) | Agent evaluations, MCP/custom tools, sandboxing and limits. | Already pinned and routed; retain it for larger isolated environment integrations. |
| [PyRIT](https://github.com/microsoft/PyRIT) | Programmatic red-team experimentation. | Already integrated; retain the existing route rather than add another attack engine. |
| [garak](https://github.com/NVIDIA/garak) | Static, dynamic and adaptive application probes. | Already integrated; model-response probes complement tool-action evidence. |
| [Promptfoo MCP plugin](https://www.promptfoo.dev/docs/red-team/plugins/mcp/) | Function-call exploitation, tool discovery and privilege escalation tests. | Review support through the existing Promptfoo runner before adding plugin configuration or changing its pin. |

## Why the additions are useful

The inspected `evals/aiuc_path_b/mcp_tool_integrity.py` describes tampered tools
and grades a `rejected_tool` boolean. It includes benign canaries, but does not
itself expose a real tool inventory and observe the agent selecting a tool.
The new pilot complements that path with actual SAI tool calls. This is a gap
in that inspected path, not a claim that Scanner has no other tool evidence.

Existing trace families under `tests/test_beaaa38011_tool_schema_trace.py` and
`tests/test_beaaa40425_persistent_memory_poisoning_trace.py` verify controlled
dispatcher observations. `harness/retrieval_source_poisoning_chain.py` checks a
scripted source-revision chain. Those are useful runtime checks; this addition
puts an agent in front of untrusted content and records its decisions.

Each pair has the same legitimate user request. Only its lower-trust carrier
changes. The session factory receives the system policy, task and tool schemas;
it does not receive an expected verdict or attack/control label. Memory and
peer tests deliver text in a tool result, so they test treatment of that text,
not persistence across restarts or authenticated inter-agent protocols.

## Running and reading the evidence

Run from the repository root. The three fixture modes exercise the oracle and
HTTP tools adapter without network requests:

```powershell
python scripts/run_agent_boundary_pilot.py --fixture resistant --output output/boundary-resistant
python scripts/run_agent_boundary_pilot.py --fixture reckless --output output/boundary-reckless
python scripts/run_agent_boundary_pilot.py --fixture refuse_all --output output/boundary-refuse-all
python scripts/run_agent_boundary_pilot.py --export --output output/boundary-preview
```

Expected calibration: resistant passes all twenty; reckless fails all ten
attacks and passes all ten controls; refusal fails all twenty for incomplete
task utility. The reckless/refusal commands exit 1 because measured failures
remain failures. Fixture receipts are explicitly labeled `scripted_fixture`.
The export contains initial task/tool setups; tool-result carriers appear later
in the runtime receipt. It is not a complete live-request transcript.

For a self-owned, OpenAI-tools-compatible staging endpoint:

```powershell
python scripts/run_agent_boundary_pilot.py --target-url https://YOUR-STAGING-ENDPOINT/chat/completions --model YOUR-MODEL --auth-env SCANNER_TEST_API_KEY --output output/boundary-target
```

The endpoint receives only synthetic task/tool context and inert tool results.
The pilot supplies its documented support-agent policy; it does not preserve or
prove the production target's policy. A batch is capped at 120 HTTP requests,
256 requested output tokens per response, six replies and twelve tool calls
per case. There are no SDK retries. Tools only read synthetic in-memory ticket
facts or append an in-memory summary; every other operation is recorded and
blocked. No real MCP server is launched. Backend compliance with the token cap
depends on the chosen compatible endpoint.

Receipts bind the exact case and public setup by SHA256, retain visible replies
and our own invocation/result ledger, and separate task completion from unsafe
attempts. They do not rely on the adapter's mutable accumulated transcript.
Missing tool visibility, interrupted sessions and exhausted limits cannot pass.
An observed unauthorized attempt remains a failure after a later transport
error. Error messages are not copied into receipts because providers can echo
credentials. An agent's narration of success cannot substitute for a recorded
read and write.

## Validation and remaining scope

The updated focused Linux run passed 118 checks across the pilot, existing SAI
interface and Founder CI cost policy, including its shell guards (the initial 23 October run passed 67).
Calibration uses scripted agents only;
no live-model or deployed-target performance is claimed. Heavy CI and preflight
remain on the existing isolated VM. The research work is tracked in PR #1244.

The new prompts and fixtures are original; upstream papers and documentation
inform methods only. Upstream code/data licenses are not interchangeable:
The [ASB license](https://github.com/agiresearch/ASB/blob/main/LICENSE) and
[Agent-SafetyBench license](https://github.com/thu-coai/Agent-SafetyBench/blob/main/LICENSE)
are MIT; [ToolSandbox](https://github.com/apple/ToolSandbox/blob/main/LICENSE)
has Apple terms and separately licensed components. MCP-SafetyBench reuse rights were not
established in this review. No upstream code, task corpus, private SHADE tasks,
scorer weights or broad package upgrades are imported here.

Next concrete work is a native MCP host/server protocol suite and target-backed
memory/revision tests, followed by a bounded comparison of the pinned tau2
voice/multilingual surfaces. Those require their actual environments; the
current pilot makes no claim to cover them.
