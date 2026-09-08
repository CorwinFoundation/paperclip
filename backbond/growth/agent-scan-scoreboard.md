# Agent Scan scoreboard

One row per week, appended on Friday. Newest row at the bottom.

Created empty under BEAAA-30615. The first row is BEAAA-29869's (WO-4) to write.

## Rules

- A cell holds a number that came from a source an agent can re-read, or it holds `—`. An unavailable number is `—` plus a line in Notes saying what was missing. Do not estimate and do not carry a previous week forward.
- Source names the thing the numbers were read from: a script in `scripts/`, a query, a file path, an issue key. A row without a source is not a row.
- Week ending is the Friday date in ISO form.
- WO-4 owns the metric set. If these columns are wrong for the numbers it actually produces, change the header in the same PR that adds the first row and say so in the PR body.

## Weekly rows

| Week ending | Scans run | Scores issued | Targets in pipeline | Notes | Source |
| --- | --- | --- | --- | --- | --- |
