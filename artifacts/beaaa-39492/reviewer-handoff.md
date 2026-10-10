# Full Scan I reviewer hand-off

**Current workbook (BEAAA-40024 item 4, parent BEAAA-39971): `artifacts/beaaa-39492/gold_sample_v2.xlsx`.** Label in v2's `labelling_order`, which works as follows:
1. The 38 `hallucinated_action` and `grounded_policy` rows come first, in their v1 order.
2. Next come 30 Full Scan M grounded-claims rows (`family = grounded_claims`, case ids `FMG-*`). The sandbox already checked these exactly, so the judge and your labels can be measured against exact ground truth. The machine's exact verdicts are on the separate `exact_check` sheet. Please do not open that sheet until you have labelled the row. It is not a label.
3. Every other v1 row follows, in v1 order. The remaining proxy-disagreement rows stay at the top of this block.

v1 is unchanged and kept for provenance. Rebuild v2 with `python3 scripts/beaaa40024_gold_sample_v2.py`. No labels were written by any agent.

---

Workbook: `artifacts/beaaa-39492/gold_sample_v1.xlsx`. The 21 proxy-disagreement rows are already placed at the top; do not reorder them.

Reviewer identities are pending Ari's assignment. No external annotator is authorized. Work only in the columns assigned to your reviewer ID and pass ID. For the current single-column workbook, enter `human_label` as exactly `pass`, `fail`, or `could_not_run`, and enter `human_note` as free text of no more than 200 characters. Do not edit judge outputs, evidence, ordering, or another reviewer's labels.

If repeat passes or multiple reviewers are used, copy the label columns using `reviewer__REVIEWER_ID__PASS_ID` names (for example, `reviewer__ari__pass1` and `reviewer__ari__pass2`). The agreement tool also accepts explicit mappings with `--reviewer REVIEWER_ID:PASS_ID:COLUMN`.

Return the completed workbook by attaching it to BEAAA-39492 or by email to the requester named on that issue. Keep the original filename stem and add the reviewer ID and pass ID. Do not run a VAPI judge or agent judge.

For adjudication, add `adjudication_label` and `adjudication_reason`. A contested row stays out of reference metrics until both contain a valid human decision and reason.
