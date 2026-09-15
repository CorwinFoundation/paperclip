# backbond-count 0.1

**What to export:** generic OpenAI messages JSONL, OTEL/OpenLLMetry JSONL, or the VAPI-shape stub. Real VAPI support follows when Ari supplies the dump.

**Command:** `backbond-count export.jsonl --judge none --from 2026-09-01 --to 2026-09-14 --agent-version "prompt v14" --show-what-leaves`. Run `backbond-count --self-check` first.

**What leaves:** one `vendor-count-v0.1` aggregate JSON document. No transcript, identifier, excerpt, per-conversation row, or content hash. The pack has no upload or network path.

**How to verify:** `--self-check` validates 100 synthetic conversations and the published A1=1, A2=1, A3=not measurable, A4=1, A5=1 counts. `--show-what-leaves` prints exact bytes and writes nothing.
