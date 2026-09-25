# BEAAA-35663 - disposition of the production-scored rows that stayed unreadable

Production-scored rows audited: 479. Verdicts: {'clear': 437, 'indeterminate': 42}.

BEAAA-35660 opened with 96 indeterminate rows. 54 became readable once the extractor's key matcher and the descriptor-pack runner route were fixed. 42 remain, each in one of the classes below. No row is flagged.

| class | rows | packs | what it would take |
| --- | --- | --- | --- |
| aggregate_category_row_over_upstream_dataset | 17 | `rubric/probe_mappings/advbench.yaml` (1)<br>`rubric/probe_mappings/factscore.yaml` (2)<br>`rubric/probe_mappings/halueval.yaml` (5)<br>`rubric/probe_mappings/jailbreakbench.yaml` (7)<br>`rubric/probe_mappings/promptinject.yaml` (2) | Define an aggregate row's construction as the union of its member cases and require the wrapper to emit a per-case manifest at execution, then run the predicate over the manifest instead of over the category row. That is a change to what the wrapper writes, not a change to the predicate. |
| no_model_facing_prompt_by_construction | 2 | `rubric/probe_mappings/eval_representation.yaml` (1)<br>`rubric/probe_mappings/pii_attestation.yaml` (1) | A not_applicable verdict class for non-prompt rows, so they stop being counted as unread. That widens the predicate's vocabulary and is a proposal on BEAAA-35661, not a silent change here: until it exists these two rows stay indeterminate and the gate keeps refusing them, which is the conservative direction. |
| upstream_library_generates_prompts_at_dispatch | 23 | `rubric/probe_mappings/agentdojo.yaml` (4)<br>`rubric/probe_mappings/garak.yaml` (8)<br>`rubric/probe_mappings/garak_beaaa152.yaml` (11) | Pin the upstream version, snapshot each named probe class's emitted prompt list into a vendored corpus under tests/probe_sets/, and have the wrapper record a per-case text hash at execution. Size and licence of the vendored snapshot is a decision above this issue, so it is filed as its own row rather than done here. |

## aggregate_category_row_over_upstream_dataset (17 rows)

The row is one dataset category, not one case: probe_id jbb.<category> / advbench.<slice> / halueval.<split> with a category label and a weight and no case list. Its members are pulled from the upstream dataset by the wrapper at run time, so no single construction text exists to read at the row's granularity.

Construction lives at: the upstream dataset the wrapper fetches, plus the in-tree per-case corpora that cover part of the same ground (tests/probe_sets/p5_direct_advbench_v1.jsonl, p5_direct_jailbreakbench_v1.jsonl, p4_grounded_factuality_halueval_qa_v1.jsonl, p4_citation_grounded_halueval_summ_v1.jsonl - whose per-case registry rows read clear)

Rows: `advbench.b2b_harmful`, `factscore.bio.citation_grounding`, `factscore.bio.factuality`, `halueval.dialogue`, `halueval.general`, `halueval.qa`, `halueval.summarization.citation_integrity`, `halueval.summarization.factuality`, `jbb.disinformation`, `jbb.economic_harm`, `jbb.expert_advice`, `jbb.fraud_deception`, `jbb.harassment_discrimination`, `jbb.malware_hacking`, `jbb.privacy.exfil`, `promptinject.direct`, `promptinject.indirect_goodside`

## no_model_facing_prompt_by_construction (2 rows)

The row has no target channel at all. eval_representation.er.derived_coverage is derived from other rows' coverage and pii_attestation.evidence_pack is a governance attestation over an evidence pack. Neither sends text to a model, so the question the predicate asks does not apply to them.

Construction lives at: not applicable - no prompt is ever constructed

Rows: `er.derived_coverage`, `pii_attestation.evidence_pack`

## upstream_library_generates_prompts_at_dispatch (23 rows)

The row names an upstream probe class (garak probe_id, AgentDojo task suite), not a prompt. The text is constructed inside the third-party package when the wrapper dispatches it; the pack carries probe_id, pillar, dim and weight and no prompt text. Neither package is vendored in this tree, so there is nothing in-repo to read.

Construction lives at: the installed third-party package (garak probe modules / agentdojo task suites) at the version the wrapper resolves at run time

Rows: `dan.AntiDAN`, `docs.attachment.rendered_html.command_hijack`, `encoding.InjectBase64`, `encoding.InjectROT13`, `garak.atkgen.Tox`, `garak.continuation.ContinueSlursReclaimedSlurs`, `garak.divergence.Repeat`, `garak.encoding.InjectBase64`, `garak.encoding.InjectROT13`, `garak.exploitation.SQLInjectionEcho`, `garak.goodside.WhoIsRiley`, `garak.leakreplay.LiteratureCloze`, `garak.packagehallucination.Python`, `garak.realtoxicityprompts.RTPBlank`, `garak.topic.WordnetControversial`, `leakreplay.LiteratureCloze`, `misleading.FalseAssertion`, `promptinject.HijackHateHumans`, `retrieval.citation_card.canary_exfil`, `snowball.GraphConnectivity`, `tools.email_thread.injected_followup`, `workspace.write_then_summarize.indirect_system_override`, `xss.MarkdownImageExfil`
