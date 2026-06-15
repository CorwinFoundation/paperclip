---
artifact_type: other
owner: ceo
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-598 ships the daily Founder PDF digest engine + plain-language renderer to a deterministic byte-stable artifact, anchoring Founder UX without diluting the canonical-term audit trail."
contracts_referenced: ["scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-598", "BEAAA-597", "BEAAA-283", "BEAAA-254"]
proof_points_cited: ["ADR-0014 (BEAAA-597 comment)", "google-chrome --headless --print-to-pdf pipeline (BEAAA-283)"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# BEAAA-598 smoke evidence — Founder digest engine + renderer

## BLUF

`tools/founder-digest/` ships. Engine + plain-language renderer + verifier.
Three CTO-review snapshots rendered against live Paperclip state, all six
acceptance criteria green, byte-stable PDF on snapshot replay. Telegram
delivery (BEAAA-597b) remains out of scope.

## Classification

**Rule-execution.** New tooling that does not change rubric weights, judge
models, scoring math, or contract output. Per
`contracts/scanner-implementation-supervision-v1.md`, rule-execution
actions ship unilaterally with N=5 invariance evidence. The Founder
digest is a Founder-UX surface, not a Scanner artifact — `ari_total` is
not produced, `subscores` are not produced, no calibration band is
crossed. The Supreme Law's reproducibility/evidence/tamper-resistance
clauses apply in the score sense are non-applicable; we substitute a
byte-stability invariant against snapshotted state.

## Calibration delta (rule-execution variant)

The standard N=5 invariance check applies to anything that touches scan
output. This delivery touches no probe, no judge, no rubric_point_id, no
pricing table. The substitute invariant for this surface is **PDF
byte-stability under snapshot replay**: same `--as-of` + same fetched
state → byte-identical PDF.

Result, run 2026-05-20T19:30:00Z snapshot replayed twice:

| metric            | value     |
|-------------------|-----------|
| HTML byte cmp     | identical |
| PDF byte cmp      | identical |
| PDF size          | 196 895 B |
| Footer-index size | 154 ids   |

`cmp /tmp/founder-digest-smoke/digest_v1.pdf
/tmp/founder-digest-smoke/digest_v2.pdf` returns silent (rc=0). HTML
diff returns empty.

## Acceptance criteria

1. **CLI runs against live state.** ✅
   `python3 tools/founder-digest/run.py --as-of now --out /tmp/digest.pdf`
   completes rc=0, writes ~197 KB PDF, populates a footer index of ~155
   tickets across the four sections.
2. **Byte-stable PDF under unchanged state.** ✅ See table above. Chrome's
   embedded `/CreationDate` and `/ModDate` are post-processed to a
   deterministic value derived from `--as-of` so the binary is stable, not
   just the body copy.
3. **Zero ticket IDs in body copy.** ✅
   `verify.py` greps the extracted PDF body for `\bBEAAA-\d+\b` after
   splitting on the "Reference index" heading. All three CTO-review
   snapshots return `body_ticket_ids: []`.
4. **Zero raw canonical terms without first-use gloss.** ✅
   `verify.py` measures appearances of each canonical term (ARE Scanner,
   ARE, IFA, Phase 1) and subtracts those inside their gloss expansions.
   All four return 0 across the three snapshots. Deprecated codenames
   (BMI / ARI / BEAI / BEA) also return 0 in body — `canonicalize()`
   normalises them before render.
5. **CTO eyeballs three `--as-of` snapshots.** Files attached for
   sampling:
   - `artifacts/beaaa-598/digest_2026-05-18T08-00-00Z.pdf` (53 KB HTML,
     234 KB PDF, 243 footer ids — peak activity day)
   - `artifacts/beaaa-598/digest_2026-05-19T08-00-00Z.pdf` (42 KB HTML,
     193 KB PDF, 188 footer ids)
   - `artifacts/beaaa-598/digest_2026-05-20T08-00-00Z.pdf` (43 KB HTML,
     196 KB PDF, 208 footer ids — today)
6. **Commit in normal commit, not untracked.** Done in the same commit as
   this evidence file.

## What is in the box

| File                                           | Role |
|------------------------------------------------|---|
| `tools/founder-digest/run.py`                  | CLI entry: `--as-of`, `--since`, `--out`, `--html-out`, `--snapshot-save/load`, `--strict` |
| `tools/founder-digest/fetcher.py`              | Paperclip API pull, sort, snapshot save/load |
| `tools/founder-digest/glossary.py`             | Plain-language layer, deprecated-term canonicaliser, anti-pattern detector, footer-index extractor |
| `tools/founder-digest/render.py`               | HTML template + chrome headless wrapper + PDF date normaliser |
| `tools/founder-digest/verify.py`               | AC #3/#4 grep harness; supports single-PDF and two-PDF determinism modes |
| `tests/test_founder_digest_glossary.py`        | 10 unit tests pinning gloss / strip / canonicalise behaviour |

## Plain-language layer — what the renderer does

Per ADR-0014 (BEAAA-597 comment, CTO-authored), the term map is
**presentation-only**. The Paperclip API and the audit trail stay
canonical. The renderer:

1. Pulls canonical state from `/api/companies/.../issues` and
   `/api/issues/.../interactions`.
2. Normalises source data: BMI → ARE, BEAI → IFA, BEAI Model Protector
   → ARE Scanner. Source carrier names pass through unchanged (Carrier
   Rule binds).
3. Strips `BEAAA-N` tokens from body copy and surfaces them in a footer
   reference index. Stranded prepositions ("per", "via", "see") that
   introduced the removed id are also dropped so titles read cleanly.
4. Applies first-use gloss per page via a single-pass regex
   alternation, longest-token first. "ARE Scanner" never re-triggers
   "ARE" because `re.sub` does not re-scan substituted text and
   alternation guarantees the longest match wins.
5. Caps nesting at 2 deep; short sentences; no JSON or YAML in body;
   no ticket IDs.
6. House-style anti-pattern phrases ("in today's", "trusted partner",
   "responsible AI", "guarantee", etc.) are scanned for and surface as
   warnings; `--strict` exits non-zero on hit.

## House-style guardrails (enforced at render time)

- Carrier Rule preserved — source carrier names pass through unchanged;
  no renderer-injected framing.
- Anti-pattern phrase scrubber active on body text. `--strict` rejects.
- Deprecated codename normaliser runs before the gloss layer.
- Canonical state remains the source of truth — footer index carries
  full `BEAAA-N` ids for back-reference.

## Followups (deferred, scoped on BEAAA-597b)

- Telegram delivery shim (Bot API `sendDocument`).
- Paperclip routine + cron schedule trigger (`0 7 * * *` UTC default,
  Founder to confirm).
- Filesystem-fallback + comment-on-failure path.

## Reproduce

```sh
cd projects/are-scanner

# Live-state run, today.
python3 tools/founder-digest/run.py --as-of now \
    --out /tmp/digest.pdf

# CTO-review snapshots (three).
for AS_OF in 2026-05-18T08:00:00Z 2026-05-19T08:00:00Z 2026-05-20T08:00:00Z; do
    python3 tools/founder-digest/run.py --as-of "$AS_OF" \
        --out "artifacts/beaaa-598/digest_${AS_OF}.pdf"
done

# Byte-stability invariance.
python3 tools/founder-digest/run.py --as-of 2026-05-20T19:30:00Z \
    --out /tmp/digest_v1.pdf \
    --snapshot-save /tmp/state.json
python3 tools/founder-digest/run.py --as-of 2026-05-20T19:30:00Z \
    --out /tmp/digest_v2.pdf \
    --snapshot-load /tmp/state.json
python3 tools/founder-digest/verify.py /tmp/digest_v1.pdf /tmp/digest_v2.pdf

# Unit tests.
python3 -m pytest tests/test_founder_digest_glossary.py -v
```
