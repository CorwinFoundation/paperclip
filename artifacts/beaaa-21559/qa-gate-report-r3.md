# BEAAA-21559 independent QA gate — R3, successor candidate (2026-08-17)

`SUPERSEDED: 0cb6e933f4031ddd9a3953d948bac726a0be11640b2784df6c0adcf786e47660 -> 268f24b3e2414a3cd9f25c99151f42776865d4d18b84fd751aa55cf2e9f740d7`

Disposition: **APPROVED for the internal-review scope** on binder `268f24b3…` (114 pages), with two P2 findings recorded. External circulation remains unauthorized and this approval does not authorize it.

## Exact bytes tested

All eleven PDFs re-downloaded from the producer attachments and verified against the candidate manifest `BackBond_Sinan_Materials_11_PDF_SHA256SUMS.txt`: `sha256sum -c` returns OK on 11 of 11. Binder `268f24b3…`, document 01 `1f034234…`, documents 09 and 10 unchanged at `f653738e…` and `5d18c7c3…`.

Brand Backbone v1.6 pin verified, `verify-brand-backbone.sh` exit 0, `a2ffc4f919b0f71a17b9f4506ac4edf11cd37956ff494f00d7a45f0b166ea090`.

## Governed preflight

`preflight_pdf.py --require-accessible --require-embedded-fonts --minimum-font-pt 7 --allowed-font Archivo --allowed-font Inter --allowed-font IBMPlexMono`, with `--require-a4` for documents 01-09 and without it for the 16:9 deck and the mixed-format binder: **11 of 11 pass, 0 errors, 0 warnings**.

`visual_integrity_gate.py` on the binder: **pass**. Renderer output hash equals the candidate hash. Weighted-median body text below the 9 pt target on 22 of 113 scored pages, ratio 0.1947, under the 0.25 hard stop. The prior candidate scored 21 of 113, ratio 0.1858; the only pages whose weighted median moved are 3 (9.4 → 8.6) and 4 (10.0 → 9.0), both from the new supersession text.

## The CMO P1 fix, measured

- Page-local historical-source band present on binder pages **4 through 21** — 18 pages, a superset of the required 7-21. Text: `HISTORICAL SOURCE - SOURCE RECOMMENDATION SUPERSEDED FOR CURRENT DIRECTION. LAUNCH PATH TBD; WARRANTY-FIRST AND DIRECT-TO-INSURANCE BOTH ACTIVE.` In R2 these pages carried only `WORKING BRIEF | INTERNAL REVIEW`.
- Outline entry renamed from `3. Recommended BackBond protection architecture` to `3. Historical source protection architecture (superseded for current direction; launch path TBD)`. The visible in-document heading is unchanged, which is correct: the CMO required the bookmark renamed and the source wording preserved.
- Full-resolution inspection of the changed pages at 150 DPI shows the band sits in the footer zone without collision, clipping or overlap.

The CMO P2 is addressed on binder page 3 with a frozen-page reading note naming pages 103 and 105-114 as reference previews and directing readers to the exact PDFs. Page 103 repeats `f653738e…`; the deck preview pages repeat `5d18c7c3…`.

## Source preservation and change-scope control

- Binder old-to-new per-page raster comparison at 72 DPI: exactly pages **3 through 21** changed; the other **95 of 114 pages are pixel-identical** to the candidate that Designer, CMO, the technical owner and this gate already inspected.
- Documents 02-08 changed bytes but are **text-identical and per-page raster-identical** to the superseded candidate. Documents 09 and 10 are byte-identical to the frozen inputs.
- Document 01 reading-order diff (`pdftotext` without `-layout`, whitespace-normalised): the only changes are the front-matter banner reword and the new per-page footer band on all 18 pages. **No body or source-wording change.**

## Accessibility, audited independently

Read from the PDF structure tree rather than taken from the producer report, and the numbers agree exactly:

- Tagged, `/MarkInfo /Marked true`, `/Lang en`, `/ViewerPreferences /DisplayDocTitle true`, document title populated, 165 outline items.
- Headings 29 H1 / 103 H2 / 33 H3 across 165 heading elements, **no level skips**.
- 63 `/Table`, each with `/THead` and `/TBody`; 394 `/TR`, 250 `/TH`, 1248 `/TD`.
- 22 `/Figure`, **22 with `/Alt`, none missing, none placeholder**. The eleven frozen preview pages carry the full page copy as alt text, so the image-only pages are reachable by assistive technology rather than announced as a bare graphic.
- No page falls below 40 non-whitespace characters, so no blank pages.
- 70 internal link annotations, none without a destination.

## Role dispositions reconciled

Each receipt was fetched and hashed independently, not taken from the summary comment:

| Role | Issue | Receipt SHA-256 verified | Binds | Decision |
| --- | --- | --- | --- | --- |
| Designer | BEAAA-21556 | `b09355563c449aae5de264185442ea5fdd4b7fe8b8db8d43fdf6fbec0c94141c` | `268f24b3…` | APPROVED |
| CMO | BEAAA-21557 | `2e61635d996882c80998dcd2d4033cd18bb581e4512e45960718e1a8d13ffcc1` | `268f24b3…` | APPROVED (P1 and P2 both closed) |
| Technical / source owner | BEAAA-21558 | `c94d0372942c5f4942b0fc528bf0b57c2a2808ac54f2fde3ff9e706773d240b5` | `268f24b3…` | APPROVED |

All three record `release_authority: false` and `external_circulation_authorized: false`. No review mismatch remains.

Governed review packet: the four approval-file slots and the release-receipt slot all hash-match the packet manifest. The release receipt is `not_released_pending_independent_review` and remains unpopulated, which is correct until the producer records the four approvals.

## Findings

**P2-NONPORTABLE-LINKS.** 17 link annotations on binder pages 7, 19 and 20 (document 01 pages 4, 16 and 17) resolve to non-portable targets: 14 `file:///mnt/paperclipdata/.../workspaces/2ad2741a-c08b-4d28-b8aa-d32757938863/BEAAA-23838/...` paths and 3 `http://127.0.0.1:3100/BEAAA/issues/...` localhost URLs. They are dead for any reader and they embed the producer workspace path and agent id in the document. The superseded candidate carried the same 17 annotations on the same pages, so this is not a regression from the current fix — my own R2 gate did not check links, and I own that miss. Does not block internal review; **must be closed before any external circulation**. Owner: Document Production Composer.

**P2-STALE-PROMOTION-21498.** BEAAA-21498 still holds the 24 promoted files of the superseded candidate, and the most recent comment there presents them as Designer-approved for internal review. Those bytes are superseded and were CMO-rejected. The staging set should be refreshed to `268f24b3…` or annotated as superseded before BEAAA-21498 proceeds. Owner: Designer.

## Verdict

APPROVED for internal review, binder `268f24b3…` and the ten constituent hashes above. No external circulation. No release receipt is issued by this gate; the producer may populate the internal-review receipt once the four approvals are recorded, and the P2 link finding must be closed before any external release.

QA approval receipt: `artifacts/beaaa-21559/qa-approval-268f24b3.json`, SHA-256 `50157a25b3040c9fe27cacee08e2adfe169aba9fe23391a36322d98b10c2e208`.

## Commands run

```
sha256sum on 11 re-downloaded PDFs + 3 role receipts + 5 packet files
sha256sum -c BackBond_Sinan_Materials_11_PDF_SHA256SUMS.txt        (11/11 OK)
preflight_pdf.py  x11   (0 errors, 0 warnings)
visual_integrity_gate.py binder.pdf and the superseded binder
render_pdf_pages.py + make_contact_sheet.py   (114 pages, full contact sheet inspected)
pdftoppm -r 72 per-page raster hash compare, old vs new, binder and documents 02-08
pdftotext reading-order diff for document 01
pypdf structure-tree walk: MarkInfo, Lang, outline, H1/H2/H3, Table/THead/TBody/TR/TH/TD, Figure /Alt, link annotations
scripts/verify-brand-backbone.sh   (exit 0)
```

`brand_backbone_version: "v1.6"`
`brand_backbone_sha256: "a2ffc4f919b0f71a17b9f4506ac4edf11cd37956ff494f00d7a45f0b166ea090"`
`brand_backbone_verified: true`
