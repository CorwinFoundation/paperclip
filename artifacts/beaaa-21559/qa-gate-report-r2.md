# BEAAA-21559 independent QA gate — R2 reconciliation (2026-08-17)

Disposition: **CHANGES REQUESTED re-affirmed**. Candidate `0cb6e933…` is not approvable and must be re-rendered.

Candidate under test (unchanged bytes, re-fetched today):

| Artifact | Attachment | SHA-256 recomputed today | Matches record |
| --- | --- | --- | --- |
| Review binder (114 pp) | `e7453052` | `0cb6e933f4031ddd9a3953d948bac726a0be11640b2784df6c0adcf786e47660` | yes |
| Source-to-output manifest | `bc777516` | `24d124a5fae9eacfb0d9342800674475423c2859c11d99094da2232847e6de1f` | yes |
| Checksum manifest | `e35b3b03` | `be19929ea90d5c53c134b5edd8f2a2f44c4219ae308139d5c1f7309f66eb1b01` | n/a (not previously pinned) |
| Evidence manifest | `1554739d` | `38910e15e56bf7b691c4c0479411601a3aca19f32a88f3e6b80a8dbed0cca0fb` | n/a |
| Upload receipts | `28e6abe3` | `c7f04f44caa33c6190e32d1dad2e3f9d5bfc8987d3ec38410d1d75317e0035fb` | n/a |

No `SUPERSEDED` record is warranted: the producer has not handed back new bytes. The parent BEAAA-21554 has produced no work product since 2026-08-09T06:28Z.

Brand Backbone: v1.6, `a2ffc4f919b0f71a17b9f4506ac4edf11cd37956ff494f00d7a45f0b166ea090`, verified.

## What changed since the 2026-08-09 verdict

Blocking finding 1 of the original report said the Designer, CMO, and technical-owner exact-candidate decisions were pending. All three are now recorded, and they do not agree:

- BEAAA-21556 Designer — APPROVED, P0 0 / P1 0, binds `0cb6e933…`.
- BEAAA-21558 Technical/source-owner — APPROVED, binds `0cb6e933…` and all ten document hashes.
- BEAAA-21557 CMO — **CHANGES REQUIRED**, P1 on binder pages 9–10, with an explicit instruction to re-render and submit a new exact candidate and hash.

The reconciliation outcome is therefore not "reviews pending" but "one required reviewer rejects these bytes." Two approvals cannot carry a candidate that a third required reviewer has told the producer to re-render.

## Independent reproduction of the CMO P1

I did not take the CMO write-up on trust. Reproduced against the exact binder bytes:

- Outline entry at page 9 reads `3. Recommended BackBond protection architecture`, with children `3.1 Wrapper 1: Certified + Warranty` (p9) and `3.2 Wrapper 2: Vendor Master Policy` (p10). The bookmark wording is unchanged; a reader entering through the bookmark lands directly on the historical recommendation.
- Per-page text extraction for pages 7–21 (`pdftotext -f n -l n -layout`) finds no page-local supersession treatment. The recurring footer on every page of document 01 is `WORKING BRIEF | INTERNAL REVIEW`. Compare page 1, which does carry a status band: `PROTECTION PATH TBD - BOTH SCENARIOS ACTIVE`.
- Pages 7, 10, 11, 12, 13, 14, 15, 16, 17, 18, 20 and 21 contain none of `superseded`, `historical`, `TBD`, `both scenarios`, or `warranty-first`. The only occurrence of `Historical` on page 9 is the table column head `Historical term in the reviewed copy`, which labels the Virtual I deal terms, not the recommendation in section 3.
- The current-canon override is stated on binder pages 2, 5 and 6 only, that is, in front matter a page-level reader does not see.

The CMO's required change is absent from these bytes and cannot be added without re-rendering. Confirmed P1.

## Findings still open from the 2026-08-09 report

- P1 — No candidate-bound manual accessibility evidence: logical reading order, heading/table/alt-text semantics, assistive-technology smoke test. The candidate README concedes this: "Independent assistive-technology smoke testing remains a QA gate." Mechanical tagging metadata does not substitute.
- P1 — No governed typed release packet and no four distinct hash-bound approval files. `release_receipt` is null. The evidence manifest lists nine evidence archives and no approval or receipt object.
- P1 — Transfer to BEAAA-21498 cannot be cleared while the above remain open.

## Observation on the promotion to BEAAA-21498 (not a blocker)

The 24 candidate and evidence files were copied to BEAAA-21498 at 2026-08-09T06:36Z, after the CMO recorded CHANGES REQUIRED at 06:34Z and before any QA verdict. I checked what actually sits there rather than the description of it:

- Promoted binder `c91bdb61` hashes to `0cb6e933f4031ddd9a3953d948bac726a0be11640b2784df6c0adcf786e47660`. Byte-identical, no silent re-render.
- Promoted README `1e73b74e` hashes to `90ba31f8…`, matching the candidate checksum manifest, and states "internal review only; independent exact-candidate approvals pending", "not an immutable release and contains no release receipt", and that all four roles must name the exact bytes.

The staged copy is labelled truthfully and claims nothing it has not earned. BEAAA-21498 remains blocked. I record it so the next reader of that issue knows the files there are a staging copy of a rejected candidate, not an approved publication set.

## Verdict

CHANGES REQUESTED stands for binder `0cb6e933…`. External circulation remains unauthorized. No release receipt may be issued.

Unblock owner: Document Production Composer on BEAAA-21554. Required: re-render document 01 pages 7–21 with the page-local historical-source treatment and the renamed bookmark the CMO specified, preserving source wording; supply the manual accessibility and assistive-technology evidence and the typed review packet with four hash-bound approvals; publish a new immutable eleven-file manifest. This canonical gate is reused for the re-test and will record `SUPERSEDED: 0cb6e933… -> <new SHA>` when the new bytes arrive.

## Commands run

```
sha256sum on all five re-fetched candidate attachments
pdfinfo binder.pdf
python3 -c "pypdf outline walk with get_destination_page_number"
pdftotext -f N -l N -layout binder.pdf -   (pages 1-22)
sha256sum promoted_binder.pdf promoted_readme.md   (BEAAA-21498 copies)
scripts/verify-brand-backbone.sh
```

`brand_backbone_version: "v1.6"`
`brand_backbone_sha256: "a2ffc4f919b0f71a17b9f4506ac4edf11cd37956ff494f00d7a45f0b166ea090"`
`brand_backbone_verified: true`
