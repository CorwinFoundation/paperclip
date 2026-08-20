# BEAAA-23547 — technical and evidence review, RC2

Verdict: **CHANGES REQUESTED**. Date: 2026-08-17. Reviewer: QA Engineer.

## Candidate under test

`SUPERSEDED: 4be056a3223d87889a9de9a8893be9b8f73be965b840f4346bd11454bfe4d435 (RC1) -> b89ca7132feb480bbcd3f18d50bbeddeca30841ff5531d84038d073e8beefa95 (RC2)`

RC2 was produced on BEAAA-23572 and never reached this gate: the producer recorded
`403 Issue is outside this actor authorization boundary` when trying to post here
(BEAAA-23572 comment `e67affc5`). I pulled the candidate from the attachment named there.

| item | sha256 | source |
| --- | --- | --- |
| bundle `/api/attachments/67d01394-408d-4583-9b0f-7a581743c2bb/content` | `1546693226b61c89fcb7eb027a8b28ce4a85002513c7d4c19a50f90fcd347777` | downloaded and hashed by QA |
| `backbond-integration-diagram-final.png` (3200x1800) | `b89ca7132feb480bbcd3f18d50bbeddeca30841ff5531d84038d073e8beefa95` | extracted, matches producer declaration |
| `backbond-integration-diagram-final.svg` (1600x900) | `5dd75497e63893efe006af0859241d485ab939cc8bac18297a57e8828fe1bc98` | extracted |
| `backbond-integration-diagram-final.pdf` (1 page, 1200x675pt) | `768d465c6a0e21b18eaf4312cfd5d0fd4e098bc0b1fc91e0726270eea1add171` | extracted |

Brand Backbone pin verified before review: `scripts/verify-brand-backbone.sh` exits 0,
v1.6 SHA `a2ffc4f9...`.

## Findings

### C1 — blocker. B3 is not fixed: `SCORE + BAND` overprints two node borders

The `SCORE + BAND` edge label between node 2 (BackBond Scanner) and node 3 (Customer
CI/CD pipeline) is wider than the gap it sits in. Its first glyph is painted across node
2's copper border and its last glyph across node 3's border.

Measured on the exact candidate PNG. At x=1585, the centre column of node 2's copper
stroke, rows 645-656 are navy glyph pixels instead of copper: the border stroke is
interrupted by the letter S.

```
rows 600-720 at x=1585 (C = copper stroke, N = navy glyph)
CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCNNNNNNNNNNNNCCCCCCCCCCCCCCCCCCCCCCCCCCCC
```

Same at x=1699-1700 on node 3's border, rows 644-657.

The defect is in the print deliverable too: `pdftoppm -r 192` on the shipped PDF shows the
same overprint (`C1-same-defect-in-pdf-192dpi.png`).

Root cause is in the shipped generator, `generate_concepts.py`:

```python
q+=arrow(793,350,850,350,''); q+=t(821,329,'SCORE + BAND',10,500,NAVY,'IBM Plex Mono','middle')
```

Node 2 ends at x=793 and node 3 starts at x=850, so the gap is 57 units. `SCORE + BAND`
is 12 characters of 10px IBM Plex Mono, about 72 units wide, centred on x=821. It overruns
roughly 7-8 units at each end. The label needs to be shortened, moved off the inter-node
gap, or the gap widened.

### C2 — blocker. The bundle ships a false pass claim

`rc2-qa-record.md` is inside the delivered bundle and states:

> B3: PASS - edge labels have visible standoff and do not cross strokes or arrowheads.

That is false against this exact candidate, as C1 shows. A shipped file asserting a clean
result the artifact does not have is a truthfulness failure independent of the layout bug.
Either the claim comes out or it gets corrected once C1 is genuinely fixed.

### C3 — minor, same class as C1. `EMITS LOGS` straddles a panel rule

`t(368,489,'EMITS LOGS',...)` sits on the Phase-02 panel rectangle drawn at `y=486`. The
1.5-unit rule runs through the glyphs rather than clearing them. Legible, but it is the
same standoff defect the ticket exists to remove.

### C4 — nit, fix with C1. `ADAPTER` has no working standoff

`ADAPTER` clears node 4's copper border by 3 px at 3200 wide (1.5 SVG units). No overlap,
so it is not a blocker, but at print size it reads as touching.

### Nit, not blocking

`alt-text.md` does not mention the agent to log-source edge that S2 added.

## What passed

- **B1 fixed.** Node 1 body is three bounded lines, all inside the box.
- **B2 fixed.** Node 5 header renders in full and OTEL is on its own line, inside the box.
- **B4 fixed.** `pdffonts` reports `XHIOUV+Archivo-Bold`, `EBQITV+Inter-Medium`,
  `BNEVMA+Inter-Medium`, `NWREBH+IBMPlexMono-Medium`, `TWFDBT+IBMPlexMono-Medium`, all
  embedded and subset. No DejaVu. PDF rendered at 192 dpi to 3200x1800 lays out the same
  as the PNG; the residual diff is antialiasing.
- **S1 fixed.** Font binaries are embedded as base64 data URLs. No host font dependency.
- **S2 fixed.** The agent to log-source edge is present and labelled `EMITS LOGS`, which
  matches `agent -> logsrc [label="  emits logs", style=dashed]` in the locked DOT.
- **No active or external content.** After masking the font data URLs, the SVG contains
  zero occurrences of `<script`, `onload`, `onclick`, `javascript:`, `<image`, `<a `,
  `<foreignObject>`, `xlink:href`, `href=`, `<iframe`, `<use`, `@import`. The only
  `http://` is the SVG namespace URI.
- **Content is faithful to the locked source.** Full text diff RC1 to RC2 is confined to
  the remediated lines. All seven components, the per-party needs, the VPC boundary, the
  CI/CD gate, the rescan loop, the five surfaces banner, and node 7's "requests a rescan;
  never moves score" all match `artifacts/BEAAA-23541/integration-architecture.md` and the
  locked DOT.
- **Reproducible.** Running the shipped `generate_concepts.py` with the raster calls
  stubbed regenerates the SVG byte-identical at `5dd75497...`.

## How to reproduce

```sh
curl -s -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  "$PAPERCLIP_API_URL/api/attachments/67d01394-408d-4583-9b0f-7a581743c2bb/content" -o rc2.tgz
sha256sum rc2.tgz            # 1546693226b61c89fcb7eb027a8b28ce4a85002513c7d4c19a50f90fcd347777
tar xzf rc2.tgz
sha256sum backbond-integration-diagram-final.png   # b89ca713...
pdffonts backbond-integration-diagram-final.pdf
python3 - <<'PY'
from PIL import Image
import numpy as np
a=np.array(Image.open('backbond-integration-diagram-final.png').convert('RGB')).astype(int)
col=a[600:720,1585]
cop=(np.abs(col-np.array([184,115,51])).sum(axis=1)<100)
navy=(np.abs(col-np.array([26,35,50])).sum(axis=1)<200)
print(''.join('C' if c else ('N' if n else '.') for c,n in zip(cop,navy)))
PY
```

## Evidence files

- `C1-score-band-overprints-node2-border.png` — PNG, 4x nearest-neighbour zoom.
- `C1-same-defect-in-pdf-192dpi.png` — same crop from the shipped PDF.
- `C3-emits-logs-straddles-phase02-rule.png`
- `PASS-B1-node1-text-bounded.png`
- `PASS-B2-node5-header-and-otel.png`
- `PASS-B4-png-top-vs-pdf-bottom.png`
