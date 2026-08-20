# BEAAA-23547 — technical and evidence review, RC3

Verdict: **CHANGES REQUESTED**. Date: 2026-08-17. Reviewer: QA Engineer.

`SUPERSEDED: b89ca7132feb480bbcd3f18d50bbeddeca30841ff5531d84038d073e8beefa95 (RC2) -> c9ea2de7d5d2d3e5b72a2f9d4e7ecc0034322b19176ef9a351ed86960c36cffb (RC3)`

RC3 is a substantial improvement. Both RC2 blockers are fixed, and the node resize that
enabled the fix did not introduce new clipping. One defect remains: the `EMITS LOGS`
label was moved off the rule it used to cross and onto a different one, and the shipped
receipt certifies that item PASS by measuring against the wrong reference line.

## Candidate under test

| item | sha256 |
| --- | --- |
| bundle `/api/attachments/b93d2fd0-ca9d-4094-ab87-dd59366fe26b/content` | `143a708f6803a613435354fc146b9d5a73ae15a4db90655a188253333e9a64ab` |
| `backbond-integration-diagram-final.png` (3200x1800) | `c9ea2de7d5d2d3e5b72a2f9d4e7ecc0034322b19176ef9a351ed86960c36cffb` — matches the gate |
| `backbond-integration-diagram-final.svg` (1600x900) | `4d9b15de18f40845f259f20f6e616b52a88f3c28ef0eabccf058775310d19603` |
| `backbond-integration-diagram-final.pdf` (1 page, 1200x675pt) | `b8e02991035593b111b8fba0ba3458f1ea3d135aa0b7e8d2bf9a030a05f77fa9` |

The SVG and PDF hashes match `source-notes.md`. Brand Backbone pin verified before review:
`scripts/verify-brand-backbone.sh` exits 0, v1.6 `a2ffc4f9...`.

## Blocker

### D1 — C3 was relocated, not fixed: `EMITS LOGS` is crossed by the Phase-01 panel rule

RC2 placed the label at baseline `y=489`, where the Phase-02 panel's top rule at `y=486`
ran through it. RC3 moved it up to `y=468` and reduced it from 10px to 9px. The
Phase-01 panel rectangle is `rect(78,244,1058,220)`, so its **bottom** edge is at
`y=464`. The label now straddles that rule instead.

Measured on the exact candidate PNG, in the window x=735..860:

- Label glyph ink occupies rows **923-935**.
- The Phase-01 bottom rule renders at rows **926-929** (colour `#CDD3DE` sampled clean at
  x=700, away from the text).

The rule therefore passes through the upper-middle of every glyph. It is present in the
PDF at 192 dpi as well, so the print deliverable carries it
(`D1-emits-logs-crossed-by-phase01-rule-png.png`, `D1-same-defect-in-pdf-192dpi.png`).

**The fix is one number.** There is a clean 22-unit band between the two panels: Phase-01
ends at `y=464` and Phase-02 begins at `y=486`. At 9px IBM Plex Mono the cap height is
about 6.5 units, so a baseline near `y=477` puts the ink at roughly `471-477`, clearing
the rule above by 7 and the rule below by 9.

```python
# generate_concepts.py, currently:
q+=t(368,468,'EMITS LOGS',9,500,NAVY,'IBM Plex Mono')
# lands ink at 461.5-468, crossing the Phase-01 rule at y=464
```

Taken alone this defect is the same minor severity I gave C3 on RC2, and I am not
raising that bar. What makes it blocking is D2.

### D2 — the receipt certifies D1 as PASS against the wrong reference line

`rc3-qa-record.md` ships inside the bundle and states:

> C2 EMITS LOGS: PASS. Label baseline is y=936 px; the Phase-02 panel rule is y=972 px,
> leaving 36 px vertical separation.

Every number in that sentence is correct, and the Phase-02 clearance is real. But the
label does not collide with the Phase-02 rule any more; it collides with the Phase-01
rule at y=926-929, which the receipt does not consider. The conclusion "PASS" is wrong.

This is the same failure mode as RC2's C2 in a subtler form: a shipped file asserting a
clean result the artifact does not have. It was a blocker on RC2 and it is a blocker
here, on the same standard. The receipt is the evidence of record the Auditor gate
consumes.

Note also that the receipt does not mention the `PROBE` label at all, whose clearance
RC3 reduced (see below).

## Verified fixed

**C1 — `SCORE + BAND` no longer overprints any border.** Node 2 was narrowed to
`box(513,266,262,...)` so it ends at x=775, node 3 moved to x=865, and the label dropped
to 9px centred on x=820. Measured from the real font, the label is 64.9 units wide and
spans 787.56..852.44, leaving **12.56 units clear on each side**. On pixels, ink spans
x=1576..1704 with node 2's copper stroke at 1547-1552 and node 3's border at 1721-1722.
The copper stroke is continuous through the label rows — no interruption anywhere.

**C4 — `ADAPTER` has real standoff.** Corridor widened from 46 to 73 units. Ink spans
x=1038..1112 against node 5's border at 1003-1004 and node 4's copper at 1148-1153: 34 px
and 36 px of clearance at 3200 wide, up from 3 px.

## Preserved controls, all re-tested on RC3

- **B1, B2 — no clipping, including in the three resized boxes.** Nodes 2, 3 and 4 were
  narrowed and their type reduced to open the label corridors, which is exactly the change
  that can introduce new overflow, so I measured every line against the real brand fonts
  rather than trusting the render. Worst case is node 3's title at 202.5 units inside a
  209-unit interior, 6.5 units of slack. Everything fits; visual crops confirm.

  | node | interior width | widest line | slack |
  | --- | --- | --- | --- |
  | 1 Customer AI Agent | 228 | 193.1 | 34.9 |
  | 2 BackBond Scanner | 240 | 202.9 | 37.1 |
  | 3 Customer CI/CD pipeline | 209 | 202.5 | 6.5 |
  | 5 Customer logs / observability | 248 | 221.4 | 26.6 |
  | 4 BackBond Collector | 281 | 236.0 | 45.0 |
  | 6 BackBond Ingest Receiver | 304 | 283.6 | 20.4 |
  | 7 Monitoring + rescan triggers | 304 | 247.2 | 56.8 |

- **B4 — fonts and cross-format consistency.** `pdffonts` reports `XHIOUV+Archivo-Bold`,
  `EBQITV+Inter-Medium`, `BNEVMA+Inter-Medium`, `NWREBH+IBMPlexMono-Medium`,
  `TWFDBT+IBMPlexMono-Medium`, all embedded and subset. No DejaVu. PDF rendered at 192 dpi
  to 3200x1800 lays out identically to the PNG; mean absolute difference 6.0 with 2.5% of
  pixels above threshold, all of it antialiasing at glyph and stroke edges.
- **S1 — portable SVG.** Font binaries embedded as base64 data URLs.
- **S2 — locked topology.** The agent to log-source edge is present and labelled
  `EMITS LOGS`, matching `agent -> logsrc [label="  emits logs", style=dashed]`.
- **No active or external content.** After masking the font data URLs the SVG has zero
  occurrences of `<script`, `onload`, `onclick`, `javascript:`, `<image`, `<a `,
  `<foreignObject>`, `xlink:href`, `href=`, `<iframe`, `<use`, `@import`. The only
  `http://` is the SVG namespace URI. `pdfinfo` reports JavaScript: no, Encrypted: no.
- **Content fidelity.** The extracted SVG text is **byte-identical to RC2**, which I had
  already confirmed faithful to `artifacts/BEAAA-23541/integration-architecture.md` and the
  locked DOT. RC3 is a pure layout change. All seven components, the per-party needs, the
  VPC boundary, the CI/CD gate, the rescan loop, the five-surface banner, and node 7's
  "requests a rescan; never moves score" are intact.
- **Reproducible.** Running the shipped `generate_concepts.py` with the raster calls
  stubbed regenerates the SVG byte-identical at `4d9b15de...`.

## Non-blocking

- **`PROBE` clearance regressed.** Narrowing node 2 shrank the node-1-to-node-2 corridor
  from 41 units to 31, while `PROBE` at 10px mono is 30 units wide. Ink spans x=966..1021
  against node 1's border at 963-964 and node 2's copper at 1023-1028: **2 px of clearance
  on each side** at 3200 wide. No overprint, so on the bar I applied to RC2's `ADAPTER`
  this is not a blocker, but it reads as touching at print size and it is the tightest
  gap in the diagram. Worth resolving in the same pass as D1.
- **`alt-text.md` is unchanged from RC2** (`af9efd77...`) and still omits the agent to
  log-source edge that S2 added.

## How to reproduce

```sh
curl -s -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  "$PAPERCLIP_API_URL/api/attachments/b93d2fd0-ca9d-4094-ab87-dd59366fe26b/content" -o rc3.tgz
sha256sum rc3.tgz    # 143a708f6803a613435354fc146b9d5a73ae15a4db90655a188253333e9a64ab
tar xzf rc3.tgz
sha256sum backbond-integration-diagram-final.png   # c9ea2de7...
pdffonts backbond-integration-diagram-final.pdf

# D1: the Phase-01 rule runs through the EMITS LOGS glyphs
python3 - <<'PY'
from PIL import Image
import numpy as np
a=np.array(Image.open('backbond-integration-diagram-final.png').convert('RGB')).astype(int)
sub=a[900:990,730:860]
navy=(np.abs(sub-np.array([11,26,56])).sum(axis=2)<200)
rule=(np.abs(sub-np.array([205,211,222])).sum(axis=2)<80)
for r in range(sub.shape[0]):
    if navy[r].sum() or rule[r].sum():
        print(f'y={900+r} navy={navy[r].sum():3d} rule={rule[r].sum():3d}')
PY
```

## Evidence files

- `D1-emits-logs-crossed-by-phase01-rule-png.png`, `D1-same-defect-in-pdf-192dpi.png`
- `FIXED-C1-score-band-clear-of-both-borders.png`
- `FIXED-C4-adapter-standoff.png`
- `NIT-probe-corridor-2px-clearance.png`
- `PASS-B4-node2-png-top-vs-pdf-bottom.png`
- `PASS-no-new-clipping-node3-resized.png`
