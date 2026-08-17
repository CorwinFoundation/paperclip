# BEAAA-23872 — QA exact-candidate gate, Band deck v0.2-r3

**Verdict: CHANGES REQUESTED.** Two release blockers, one high, two records.

## Candidate under test

| item | value |
|---|---|
| attachment | `345d3484-63bc-4e4f-8e0d-f8ce1f4500db` |
| PPTX SHA-256 | `310247c31396b966bd561b64c40157d619ccf38f8606624427f132da50b6999f` (byte-exact match to ticket) |
| size | 714,632 B, 57 parts, 4 slides |
| supersedes | `3b0417902c5817bcd565d64ebcc5fcab0dd87d6a9ee05eb6603f40d4008e9865` (attachment `615a19ba`, APPROVED on BEAAA-23299) |
| QA render PDF SHA-256 | `4f23379fd23b5ca9c78a52817c63ce9895d787a92e497f0903d766894dfcf5d8` |
| Brand Backbone | `scripts/verify-brand-backbone.sh` exit 0, v1.6 `a2ffc4f9…` |

## Render recipe (reproducible)

Headless LibreOffice Impress 7.4 in `debian:bookworm-slim`, brand fonts (Archivo, Inter, IBM Plex Mono)
fetched from `google/fonts` at run time; same recipe used on BEAAA-23299 so the two renders are comparable.

```
docker run --rm -v <workdir>:/work debian:bookworm-slim bash /work/in.sh
soffice --headless --norestore --convert-to pdf:impress_pdf_Export --outdir /work /work/cand.pptx
pdftoppm -r 150 -png cand.pdf slide ; pdftoppm -r 300 -png -f 4 -l 4 cand.pdf s4hi
```

Output: 4 pages, 960×540 pt (16:9). Fonts embedded and subset, no tofu:
`IBMPlexMono-Regular`, `Archivo-SemiBold`, `Inter-Regular`.

## Findings

### F1 — BLOCKER (P0). Adapter card text overflows and the CMO claim boundary renders invisible.

`TextBox 13` inside `Rounded Rectangle 11` (BAND EVIDENCE ADAPTER) carries
`"Proposed: agent, execution, room and message events\nTool and outcome fields require confirmation"`
at 18 pt Inter in a box `cx=2194560` EMU (2.40 in) with `lIns/rIns=0` and `spAutoFit`.
It wraps to **7 lines**. The card interior below the label is `3694176 − 2779776 = 914400` EMU = **72 pt**;
seven 18 pt lines need ~151 pt.

The card fill is Ink Navy and the body run is white. Everything past line 3 leaves the card and lands on
the Warm Paper background **still white**.

- Measured spill: ink rows `y=1220…1495` px at 300 dpi, i.e. **0.94 in below the card bottom edge**.
- Measured contrast of the spilled glyphs against the sampled page background `RGB(252,251,248)`: **1.03 : 1**
  (WCAG AA body-text floor is 4.5 : 1).
- What a reader actually sees in the card: `Proposed: agent, execution, room and message`.
- What is invisible: `events`, and the whole of `Tool and outcome fields require confirmation`.

Consequence: the adapter box presents a truncated normalization claim with the tool/outcome boundary
missing. This is the exact reader misreading CMO comment `199a00b4` asked to remove, so the slide fails the
correction it is being handed over as implementing. Not a cosmetic defect.

Evidence: `F1-adapter-overflow-310247c3.png`, `qa-slide4-310247c3.png`.

### F2 — BLOCKER (P0). FALLBACK heading wraps and overprints body line 1.

`TextBox 27` `"Scheduled evidence bundle"` at 20 pt Archivo SemiBold in `cx=2880360` EMU (3.15 in) wraps to two
lines. The heading box top is `y=4343400` and the body box `TextBox 28` starts at `y=4773168`, so only
`429768` EMU = **33.8 pt** is available; two 20 pt lines need 48 pt. Line 2 `bundle` prints over body line 1
`If Band can provide it, use`.

Ink-band row profile of the FALLBACK column against the RECOMMENDED column as control (binarised vs page
background, 300 dpi, threshold |Δ|>60):

```
FALLBACK column        RECOMMENDED column (control)
rows   36.. 97  h= 62   rows   36.. 97  h= 62   kicker
rows  136..244  h=109   rows  172..229  h= 58   <-- heading: 109 px fused vs 58 px clean
rows  262..335  h= 74   rows  264..335  h= 72
rows  354..409  h= 56   rows  352..425  h= 74
rows  442..515  h= 74   rows  443..499  h= 57
```

The control column resolves one heading line as a 58 px band. The FALLBACK column has a single fused 109 px
band in the same slot. Six text lines in that column collapse into four ink bands.

This is the **third recurrence of one failure class**: R1 blocker was the slide-4 title wrapping into the
swimlane label, R2 blocker was the column heading wrapping into body line 1, R3 is the fallback heading
wrapping into body line 1.

Evidence: `F2-fallback-heading-collision-310247c3.png`.

### F3 — HIGH. LOCAL BOUNDARY card text overflows the card.

`TextBox 17` `"Redact sensitive content\nKeep detailed evidence near Band"` at 18 pt in the same 2.40 in box
wraps to 4 lines, needs 86.4 pt against 72 pt of card interior. The line `evidence near Band` renders wholly
outside the rounded rectangle, on the page background. Slate on Warm Paper so it stays legible, but the
card boundary — which is the visual carrier of the local-custody argument — no longer contains its own text.

Evidence: `F3-localboundary-overflow-310247c3.png`.

### F4 — MEDIUM (record for CMO gate BEAAA-23870). Handoff text does not match the shipped bytes.

Parent comment `7e03492d` states the adapter language "explicitly requires **Band** confirmation for
tool/outcome fields". The shipped run is `Tool and outcome fields require confirmation`. The word *Band* is
not in the file.

Against the three replacements CMO comment `199a00b4` specified, the shipped strings are:

| CMO asked for | shipped | match |
|---|---|---|
| `Proposed normalization: agent, execution, room and message events` / `Tool and outcome fields require Band confirmation` | `Proposed: agent, execution, room and message events` / `Tool and outcome fields require confirmation` | no |
| `Best fit for a remote agent because Band documents real-time room and message events through its SDK and WebSocket channels.` | `Band documents real-time room and message events through its SDK and WebSocket channels.` | partial, leading clause dropped |
| `If Band can provide it, use a signed, version-bound bundle on a schedule or material change.` | identical | yes |

Whether the two paraphrases satisfy the intent is CMO's ruling, not QA's. QA records that the deviation
exists and that the handoff describes the file inaccurately.

### F5 — LOW (process). Governed structural preflight cannot see this class of defect.

`artifacts/BEAAA-23279/preflight-r3.json` reports `status: pass`, `off_canvas_objects: []`, `errors: []`,
`warnings: []` on a slide carrying three text overflows. The check reads declared shape geometry, which is
on-canvas; it does not model `spAutoFit` growth, so overflow past a declared box is invisible to it. A
PASS from that preflight is not evidence of fit. Recommend adding a rendered-raster overflow check to the
producer's pre-handoff loop.

### Carried-forward observation, not a regression on this SHA

The raster wordmark lockup `ppt/media/image1.png` reads "AGENT RISK RATINGS & INSURANCE" on every slide.
Media parts are byte-identical to the approved `3b041790`, so this is not new here; it is the open brand
claim item and it remains relevant to any Band-facing use of this deck.

## What passes

**Type floors (Designer BEAAA-23867 items 1, 2, 4).** Declared sizes meet the floors:

| element | prior | candidate | floor |
|---|---|---|---|
| title `TextBox 5` | 29 pt | **36 pt** | ≥36 |
| subtitle `TextBox 6` | 12 pt | **18 pt** | ≥18 |
| diagram body 9/13/17/21 | 11 pt | **18 pt** | ≥18 |
| decision headings 24/27/30 | 15 pt | **20 pt** | ≥18 |
| option body 25/28/31 | 10.5 pt | **18 pt** | ≥18 |
| footer `TextBox 32` | 10 pt | **12 pt** | ≥12 |

Utility labels stay smaller: 8 pt confidentiality, 9 pt page number / kickers, 10 pt eyebrow and card
labels. Whether the 10 pt card stage labels count as nonessential is Designer's call, flagged to BEAAA-23871.

**Structure.** Lower section reduced to three decision blocks (RECOMMENDED / FALLBACK / CONFIRM WITH BAND).
Four-stage listener path retained as the dominant composition. Provisional label, local-boundary/custody
language, and the outside-the-serving-request-path qualifier all retained. All objects native; no
flattening.

**Safety.** 57 parts, zero external relationships, no macro or OLE part, no `vbaProject` content type. Two
PNG media parts, both byte-identical to the approved predecessor.

**Slides 1–3 regression, clean, proven twice.**
1. Package byte-diff `3b041790` → `310247c3`: same 57 entries, no additions, no removals, exactly two parts
   differ — `ppt/slides/slide4.xml` (20331 → 19809 B) and `docProps/core.xml`. Slides 1–3, masters, layouts,
   theme and media are bit-identical.
2. Per-page raster hash of the two QA render PDFs at 150 dpi:

```
page 1: d3116ba9cae238b5  IDENTICAL
page 2: e3dd0bfa6c4a8bad  IDENTICAL
page 3: eae6bf63be255e75  IDENTICAL
page 4: 8b122331… -> 03299207…  DIFFERS (expected)
```

**Content truthfulness on the changed strings.** Every retained Band claim traces to the sources already
verified live on BEAAA-23299 and recorded in `artifacts/BEAAA-23279/claim-ledger.jsonl` C6–C8. The removed
"Native OTel, webhook, and log-sink support are not documented" caveat is still carried in
`notesSlide4.xml`, which is byte-identical, so Designer item 3 is satisfied for that item. The
deployment-burden and failure-mode detail Designer asked to relocate is in neither the notes nor the claim
ledger; it was deleted. Recorded for Designer gate BEAAA-23871.

## Reproduction

```
curl -s -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  "$PAPERCLIP_API_URL/api/attachments/345d3484-63bc-4e4f-8e0d-f8ce1f4500db/content" -o cand.pptx
sha256sum cand.pptx           # 310247c3…
# render per the recipe above, then open page 4 of the PDF, or:
pdftoppm -r 300 -png -f 4 -l 4 cand.pdf s4hi
```

F1 and F2 are visible without instrumentation at 150 dpi. F1's spilled text needs a non-white backdrop or a
contrast measurement to see at all, which is the point of the finding.
