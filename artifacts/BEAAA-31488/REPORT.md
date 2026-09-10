---
artifact_title: "P5 detector run over real non-probe Codex agent traffic"
artifact_type: "engineering finding"
artifact_status: "complete"
owner: "CTO"
persona_audience: "internal"
issue: "BEAAA-31488"
created_at: "2026-09-10"
source_artifact: "artifacts/BEAAA-31488/codex-history-p5-run.json"
---

# P5 detector run over real agent traffic

## Verdict

Content-based P5 detection **does not produce an operator-useful finding** on the sampled real, non-probe agent traffic.

The adapter inventoried 76,634 Codex history records, normalized 42,690 semantically equivalent runtime events, and presented 25,465 genuine tool-call candidates to the P5 detector. One event matched an injection marker. Manual review classified it as a false positive: a legitimate internal risk-analysis command contained the word `jailbreak`. Operator-useful P5 findings: **0**.

## Corpus and method

- Source: the Paperclip fleet's local Codex thread-history SQLite database.
- Traffic class: real model completions and agent-issued shell tool calls from ordinary company work; not authored as an adversarial probe corpus.
- Mapping boundary: `commandExecution` becomes `tool_call`; `agentMessage` becomes `llm_completion`.
- Scope limit: the adapter emits no `tool_source_reference` events and excludes command output, so the run sampled only agent-authored text and did not sample the untrusted-content channel P5 is intended to detect.
- Exclusions: user messages, probe prompts, request bodies, and command output are not promoted to tool calls or source-content events.
- Read mode: SQLite URI `mode=ro&immutable=1`; the adapter does not mutate the source corpus.
- Detector: existing `detect_p5_events` path and marker logic, unchanged.

## Counts

| Stage | Count |
|---|---:|
| Records inventoried | 76,634 |
| Runtime events normalized | 42,690 |
| Genuine P5 tool-call candidates | 25,465 |
| Injection-pattern matches | 1 |
| True-positive / operator-useful findings | 0 |

## Match classification

The sole match was event `codex-exec-b5651011-84b2-42bb-9b17-bd27cfc8b46e`. Its shell command generated an internal spreadsheet describing Scanner evidence for insurance-trigger scenarios. The command contained `jailbreak` as a risk-taxonomy term. It was neither untrusted content instructing an agent nor evidence that injected instructions affected a downstream action.

Classification: **false positive caused by legitimate security-analysis vocabulary in an agent-authored tool call**.

## Product implication

This run does not support a claim that the current content-based P5 detector identifies prompt injection in ordinary agent traffic. It shows the opposite practical constraint: scanning whole tool-call payloads for attack vocabulary can surface legitimate engineering and risk-analysis text. A partner-usable detector needs source-aware provenance and an effect or boundary-crossing signal, rather than marker presence alone.

## Reproduction

```sh
PYTHONPATH=tools/post-bind-ingest/src \
  python -m post_bind_ingest.codex_history_adapter \
  /path/to/thread_history.sqlite \
  --output artifacts/BEAAA-31488/codex-history-p5-run.json

PYTHONPATH=tools/post-bind-ingest/src \
  pytest -q tools/post-bind-ingest/tests/test_codex_history_adapter.py
```

The JSON artifact preserves aggregate counts, the matched normalized event, and the deterministic P5 finding emitted by the existing detector.
