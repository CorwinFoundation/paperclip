# Agent supply-chain and indirect-injection failure modes vs BackBond Scanner coverage

BEAAA-33025. Source: Google/Mandiant AI risk and resilience report, September 2026
(https://cloud.google.com/security/resources/ai-risk-and-resilience-2026), surfaced as item 5 of
the daily news brief BEAAA-32994.

Evidence base: the 138-family surface census at `harness/data/beaaa29961_surface_census.json`,
the scored-lane family table at `harness/bench/scored_lane_common.py`, the probe registry at
`tools/probe-registry/active_registry.json`, and the deterministic chain probes under `harness/`.

Two levels of coverage are distinguished below, because they price differently:

- **routed** — a family exists, is applicable to a target surface, and can be executed.
- **graded** — the family reaches a terminal verdict that feeds a BackBond subscore.

The census records 138/138 families routed and 0 verdicts, so every row below is routed-not-graded
unless it names a scored-lane corpus in `FAMILY_CORPUS_VERSIONS`.

## 1. Coverage by named failure mode

| Mandiant mode | Status | Probes |
|---|---|---|
| Indirect prompt injection | **Covered** | `indirect_injection`, `indirect_injection_channels`, `email_calendar_injection`, `p3_2_indirect_injection_rob`; scored-lane corpora `p3_2_indirect_injection_floor` and `p3_2_indirect_injection_floor30` (rubric dim P3.2) in `harness/bench/scored_lane_common.py`; `harness/bench/indirect_injection.py`; AgentDojo path via `harness/agentdojo_registered_adapter.py`; garak `promptinject.indirect_goodside`. Applicability is gated per surface by `harness/applicability.py` (P3.2 fires only where retrieval, attachments, or inline HTML exist). |
| Extension/tool hooks used for reconnaissance or lateral movement | **Covered** | `tool_misuse_unauthorized_call`, `tool_misuse_param_tamper`, `tool_misuse_scope_escape`, `tool_authorization_confused_deputy`, `mcp_tool_integrity`, `tool_schema_drift_partial_failure`, `out_of_scope_containment`, `sandbox_process_network_boundary`, `tenant_account_boundary`; plus the deterministic multi-hop probe `containment-escape-causal-chain-v1` (`harness/containment_escape_chain.py`, rubric point `ROBUSTNESS.TOOL_MISUSE.SCOPE_ESCAPE`), which is the only probe that asserts post-egress third-party outcome attribution across hops. |
| Poisoned data sources | **Partially covered** | `retrieval_source_trust_poisoning`, `rag_grounding_retrieval_integrity`, `persistent_memory_poisoning`, `provenance_chain_of_custody` all exist and are routed, but only to the `browser` target surface, and none carries a scored-lane corpus. Nothing grades resistance to a source that was trusted at ingestion and poisoned afterwards. |
| Model dependencies | **Not covered** | `code_agent_supply_chain` (coding surface only) is the nearest family and covers package/tool supply chain, not model supply chain. The census contains zero occurrences of a model-dependency surface. No probe asks an agent which model, adapter, or fine-tune it depends on, or detects an unannounced swap. This is the widest gap of the four, and it is the one a buyer asks about first. |

## 2. Gaps worth landing as probes

The two the CEO named first — indirect prompt-injection resistance and tool-boundary respect — are
already covered and do not need new probes. Building duplicates there would add corpus surface
without adding pricing power. The cheap, genuinely uncovered work is:

1. **Model-dependency declaration and swap detection.** A deterministic probe that asks a target to
   declare its model identity and version, then compares the declaration against the observed
   response fingerprint across seeds. Verdict: `declared_and_stable`, `declared_unstable`,
   `undeclared`. Pattern to follow: `harness/containment_escape_chain.py` plus a pilot mapping
   under `harness/mock_tools/pilots/`. Reported-not-scored on first landing.
2. **Post-ingestion source poisoning.** Extend `retrieval_source_trust_poisoning` beyond the
   `browser` surface, and add a chain fixture where a source is clean on turn 1 and poisoned on a
   later turn, so the probe measures re-validation rather than first-read filtering.

Both are self-contained fixtures with no scoring, rubric, or corpus writes, so neither moves a
golden-agent score and neither needs a calibration delta report on landing.

## 3. Dependency on the log listener

Reconnaissance and lateral movement can only be *scored* from the runtime tool-call log, because
both are cross-session patterns that no single-session probe can observe; the containment-escape
chain proves the capability exists but cannot prove it was exercised against a live account, which
is what the log listener (Founder priority 3, in flight per Addendum A) supplies.
