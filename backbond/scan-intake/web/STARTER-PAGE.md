# Starter page source checkpoint — BEAAA-42620

Maintained asset: `backbond/scan-intake/web/scan.js` maps to the public static asset `/scan/scan.js`. The script uses the existing page DOM and `https://api.backbond.ai/scan/v1` routes. This source change does not activate or publish that mapping. CTO integrates it with the serving page and deployment lane.

Run `node backbond/scan-intake/web/verify.cjs` from the repository root. The harness executes this actual script in a VM with fake fetch and timers; no external calls occur. Optional first argument selects another script for regression comparison.

Origin: Founder-authorized AriClaw `/tmp/backbond-starter-status-review-20261020` source and harness. This checkpoint preserves terminal correction, removes invented 100-case running fallback, labels processed work separately from verdicts, recognizes canonical `complete`, and suppresses unsupported customer-mail/inbox claims. Legacy `report_emailed` does not establish recipient, revision approval or provider receipt.

Terminal refresh handling now retains confirmed scan facts through fetch failures and ignores later nonterminal execution snapshots. Incomplete, failed, interrupted and cancelled execution continue status polling so later report work can be surfaced once the backend projection is integrated. The 23-scenario harness covers terminal disconnect/recovery and stale running callbacks; these are deterministic browser-script tests, not backend lifecycle proof.

The page now consumes optional `lifecycle.version: starter_lifecycle@1` from the existing `/scan/v1/scan-status` response. Intake, report and delivery are rendered independently of execution. Missing fields, unknown enum values and unsupported versions remain unconfirmed. Detailed CEO/provider/recipient information stays out of customer copy. Customer provider-acceptance copy is “Report prepared for BackBond review.” Operator evidence remains separate. Terminal execution remains visible while lifecycle updates continue, including after stale running callbacks.

Verification: the harness covers every contract enum, missing/unknown fields, unsupported versions, and lifecycle advancement while retaining incomplete execution. Optional third argument is a backend-generated fixture JSON with `response` (actual public status response) and `expected_text` (nonempty array of expected page strings): `node backbond/scan-intake/web/verify.cjs backbond/scan-intake/web/scan.js /path/to/backend-fixture.json`.

Composed backend tests now generate real public-route responses for the exact maintained consumer, including persisted metadata and pending/acknowledged/queued capabilities. Fixture transport does not establish live acceptance. Desktop/mobile previews remain for existing Designer 42591 and QA 42593. No release readiness is claimed.

20 October shell correction: `web/index.html` maps to `/scan/index.html` alongside this script. It is derived from the captured current serving shell and preserves form IDs, field semantics, stylesheet/logo URLs and the existing companion-script URL. Publish both maintained HTML and scan.js together through CTO integration. Referenced CSS, logo and companion JS remain existing serving assets, not newly bundled dependencies. The fixture renderer suppresses companion JS, so its navigation behavior is not validated by these renders.

The shell removes customer-email promises, obsolete library counts, score CTA and the insurance summary. Email is labelled as request contact information. Linked Terms and Privacy documents are unchanged; this copy revision does not certify their contents or deployed retention behavior. The actual accepted manifest remains authoritative for counts. No new backend transition or report authority is introduced.

CTO integration contract, 20 October: the status response now supplies `request_reference`
(a stable non-authorizing BB- digest of the persisted reservation),
`accepted_scope_version` (the scope in the acknowledged BAC intake payload, or null),
`last_confirmed_progress_at` (persisted local observation time when processed_count
increased above zero, or null), `queue_acknowledged` and `cancel_available`.
The last progress timestamp survives restart and does not advance for heartbeats,
unchanged counts or regressions. It is an observation timestamp, not an upstream
execution timestamp. Null values must display unavailable. Queue confirmation
requires both durable BAC acknowledgement and current membership in the execution
queue; status=queued alone may mean intake acknowledgement is still pending.
Internal case IDs, request tokens and registry identifiers must stay out of copy.
Page source 5b57b87cb is composed with the persisted backend projection.
Source composition is not page approval or deployment evidence.
20 October Designer corrections: publish maintained index.html, scan.js, scan.css and status.css together to the corresponding /scan/ paths. Existing /marketing-assets/scanner-shell.css, scanner-shell.js and backbond-logo.png remain required serving dependencies. Active requests suppress hero/scope/form instructions and lead with request status. Customer text uses cases, hides registry identifiers and never treats heartbeat/activity as advancement. Public reference, accepted version and actual advancement timestamp consume the safe persisted projection when supplied and otherwise display unavailable; accepted_pack_total is displayed only when supplied. Cancellation requires both queue acknowledgement and the explicit cancel capability; queued status alone renders confirmation pending. Provider acceptance customer copy is Report prepared for BackBond review. No operator pane is introduced.

Publish web/fonts/*.ttf to /scan/fonts/ alongside all four serving files. Fonts are packaged from the existing approved management-report assets. Preview bundle: /tmp/beaaa-42620-designer-corrections-20261020; companion JS remains suppressed, so full navigation is unverified.

20 October metadata wiring: consumes request_reference only in the public BB-hex shape, accepted_scope_version in the versioned Starter manifest shape, and ISO last_confirmed_progress_at separately from heartbeat_at. Missing/invalid metadata stays unavailable. Confirmed queue copy requires status queued AND queue_acknowledged true; cancellation additionally requires cancel_available true. Truthy strings never establish capabilities. Backend roundtrip test test_page_metadata.py is run on CTO composition d18bdb0 with this exact consumer: unacknowledged, acknowledged-only, execution-queued, and persisted advancement timestamp. No live calls. CTO must copy this scan.js and verify.cjs byte-identically to tests/fixtures/starter-page-v1 and use approved provider acceptance copy: Report prepared for BackBond review.
