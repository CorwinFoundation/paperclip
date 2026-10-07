# Site quickfix: starter-100@2 report (BEAAA-38895)

`starter-report.js` renders the Starter report from a scan result payload:
`renderStarterReport(payload, element)`. It shows per-area "x of 10 passed" in
the Ran / Passed / Needs attention / Could not run table, the Starter score line
when `starter_score` is present, and sent / reply / reason for each failed test
(prompt and reply appear only when the scan ran with `include_result_rows`).
Old starter-100@1 payloads render the table without a score line.

BEAAA-38898: both files also render starter-100@3 (the `answers_normal_requests`
area replaces `unsafe_output`). Old @1 and @2 payloads render as before.

BEAAA-38932: for starter-100@3 payloads only, both files add "What this Starter
scan tested", "What this scan did not look at" and the "Review Full Scan scope"
upgrade block directly under the results table (copy: BEAAA-38928
`messaging-draft` v0.2). Until `/scan/full/new` exists the button opens
`mailto:info@backbond.ai?...&subject=Full%20Scan%20scope`, the report's current
contact route.
