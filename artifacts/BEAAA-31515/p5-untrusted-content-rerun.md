---
artifact_title: "P5 rerun with untrusted command-output events"
artifact_type: "engineering evidence"
artifact_status: "complete"
owner: "CTO"
persona_audience: "internal"
issue: "BEAAA-31515"
created_at: "2026-09-10"
---

# P5 untrusted-content rerun

The read-only Codex history corpus was rerun after the adapter began emitting
command output as `tool_source_reference` events linked to their originating
`tool_call`. Source events carry `trust: untrusted-content` and retain the
output as content for the existing P5 detector.

| Measure | Count |
|---|---:|
| Records inventoried | 76,846 |
| Runtime events normalized | 67,839 |
| Tool-call candidates | 25,538 |
| Untrusted-content source events | 25,028 |
| Untrusted-content marker matches | 323 |
| P5 findings | 324 |

Command used:

```sh
PYTHONPATH=tools/post-bind-ingest/src python3 -m post_bind_ingest.codex_history_adapter \
  /mnt/paperclipdata/dot-paperclip/instances/default/companies/59f8876e-e729-4dda-98f9-1317c2b50492/codex-home/thread_history_1.sqlite \
  --output p5-untrusted-output.json
```

The JSON output is generated read-only from the source database and is retained
outside the repository because it is a 55 MB fleet-history artifact.
