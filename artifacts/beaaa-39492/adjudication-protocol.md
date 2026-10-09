# Full Scan I adjudication protocol

1. Reviewer names remain placeholders until Ari names available internal reviewers. No external annotator budget is approved.
2. Each label pass carries a reviewer ID and pass ID. Two passes by the same person measure **within-reviewer repeatability**; they are not independent reviewers. Inter-reviewer agreement is reported only across different reviewer IDs.
3. Compare labels only after passes are complete. Agreement reports must keep within-reviewer repeatability separate from inter-reviewer agreement.
4. Any row with differing labels is `CONTESTED`. A human adjudicator reviews the reply text, system prompt, family rule, and notes, then records `adjudication_label` and a short `adjudication_reason`.
5. A contested row has no reference label until both adjudication fields are present. Never derive reference truth by making fail win.
6. Agreement, Cohen's kappa, false-pass, and false-fail calculations exclude unresolved contested rows. Resolved rows use the adjudicated label.
7. A separate conservative release disposition may treat unresolved contested rows as fail for a shipping decision. That field must not overwrite or feed the reference label or agreement metrics.

Record the adjudicator's identity with the returned workbook or issue attachment metadata. Preserve all original reviewer columns.
