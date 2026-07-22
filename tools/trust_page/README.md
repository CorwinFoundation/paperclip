# Trust Page v1 — dynamic wiring (BEAAA-11926 / WI-3)

CTO-owned follow-on to the Trust Page v1 build lane ([BEAAA-11917](/BEAAA/issues/BEAAA-11917)).
Replaces the illustrative static prototype with a **data-driven renderer** wired to the
three real Phase-1 data sources named in the Trust Page spec §1.

## What this wires

| Page field | Real source | Module |
|---|---|---|
| BackBond Score **band** + verified-as-of + reproducibility envelope | `rubric.scoring.score()` on the repository's current **locked rubric** (WI-1 / [BEAAA-11915](/BEAAA/issues/BEAAA-11915)) → `are_total` → band via frozen bands v1.0 | `verification_record.py` |
| Monitoring continuity (`healthy`/`grace`/`gap`/`stale_ineligible`) | `post_bind_ingest.continuity_gate` (WI-2 / [BEAAA-11916](/BEAAA/issues/BEAAA-11916)) | `verification_record.py` |
| Warranty remedy eligibility (`pending` in v1) | WI-6 §2 Eligibility Rule ([BEAAA-11920](/BEAAA/issues/BEAAA-11920)) evaluated against current inputs | `warranty_eligibility.py` |
| Visible stale state (`current`/`aging`/`due_soon`/`stale`/`expired`) | derived: `now − verified_as_of` vs next-required-verification (spec §2) | `verification_record.py` |

## Supreme Law (reproducibility)

`build_record` **refuses to emit** a record unless the reproducibility envelope is complete
(`scanner_version`, `corpus_version`, `rubric_version`, `rubric_locked_through`, `seed`,
`bands_version`) → raises `MissingEnvelopeError`. Every rendered page stamps the full envelope
in an HTML comment **and** a visible provenance line. No band is rendered against an unlocked
rubric.

## Warranty posture is honest, not a placeholder

WI-6 (BEAAA-11920) is a Legal drafting artifact — there is no ratified minimum band, no
allocated caps/reserve, and no counsel clearance in Phase 1. `evaluate_warranty_eligibility`
encodes the real §2 conjunction; because the program-level conditions are unratified brackets,
it deterministically returns **`pending`**. There is intentionally no code path that returns
`eligible` in v1 (that requires a per-agent Head-of-Underwriting determination). Confirmed as
the correct posture by spec §1 and the Legal wording review (BEAAA-11927).

## Warranty-safe copy (Legal gate)

All rendered copy uses the Legal-Coordinator-approved wording from BEAAA-11927. `render.py`
**fail-closes**: it raises if any §3 red-line term (`insurance`, `insured`, `policy`,
`coverage`, `covered`, `premium`, `bind`/`bound`, `binder`, …) appears in the output.

## Run

The committed examples are frozen historical records. Regenerate their HTML without
rescoring against the current rubric:

```bash
PYTHONPATH=.:tools/post-bind-ingest/src python3 -m tools.trust_page.build_sample \
    --frozen-record tools/trust_page/samples/sample_record-current.json \
    --out-html tools/trust_page/samples/trust-page-v1-current.html \
    --out-json tools/trust_page/samples/sample_record-current.json

PYTHONPATH=.:tools/post-bind-ingest/src python3 -m tools.trust_page.build_sample \
    --frozen-record tools/trust_page/samples/sample_record-stale.json \
    --out-html tools/trust_page/samples/trust-page-v1-stale.html \
    --out-json tools/trust_page/samples/sample_record-stale.json

PYTHONPATH=.:tools/post-bind-ingest/src python3 -m pytest tools/trust_page/tests -q
```

`samples/` holds two frozen historical renders built from the **same recorded envelope** (golden fixture G1,
`are_total=79`, band **B**), differing only in observation clock: `-current` (a representative
in-window view) and `-stale` (real today, showing the decay feature). Per Trust Page spec
Red Line 6 a golden calibration fixture is **not external production proof** — every render is
watermarked internal. The frozen-record path does not invoke the repository's current rubric;
it preserves and labels the recorded scanner 0.4.0, rubric 1.2.0, corpus, and band provenance.

## Visual design (BEAAA-11928 locked design — applied)

`render.py`'s template is harmonized to the **BEAAA-11928 locked design system**
(BEAAA-11942, WI-3): navy verification card, **Copper Law** (copper only on the active
band + wordmark rule), **words-first band ladder** (Preferred A / Standard B /
Constrained C / Decline D, band-only, no raw 0–100 number), **freshness lifecycle
timeline** (Current → Aging → Due soon → Stale → Expired, active stop driven by the live
`stale_state`), and the Archivo / Inter / IBM Plex Mono type stack. Freshness color is
namespaced (`--fresh-*`) and kept separate from band color so "bands are tonal, never
traffic-lights" holds. Fonts are named with system fallbacks (no `@font-face` external
assets) to preserve the single-file, no-external-asset guarantee. **Data wiring and the
reproducibility envelope are unchanged by the visual harmonization** — the sample record
JSONs are byte-identical across the change; only the HTML differs.

## Not done here (separate owners)

- **Merge ordering:** requires WI-2 continuity to reach `master` (PR #23) before
  `build_sample.py` runs green end-to-end (the committed sample HTMLs are re-rendered
  from the unchanged sample record JSONs when `continuity_gate` is not yet present).
- **Outside-counsel review** — per BEAAA-11927, before any partner send.
- **Real partner intake scan** — this tool renders any conformant `score()` output; a real
  partner scan on the locked rubric replaces the golden fixture for an actual partner send
  (Trust Page Red Line 6). External send is gated under WI-3 lane BEAAA-11917.
