# Findings classification correction, 24 October 2026

This directory is the source overlay for the installed `backbond-findings-report`
skill. Apply the six matching files to the existing skill, preserving its
template, fonts, logo, examples and profile configuration. Save a private copy
of the prior files and compare their recorded SHA256 values before replacement.
The source-controlled overlay changes selection, not the report approval roles.

`answers_normal_requests` now supplies business candidates under
`policy_application_error`. A complete supplied policy and facts can prove a
wrong scenario decision without owner documents. Qualify the business impact;
do not imply a connected action, actual loss, or verified deployed policy.

`adjudication_review.py` binds an append-only report review to the original
request and exact exchanges. The evidence ZIP keeps original labels and counts
and adds separately checksummed correction and reviewed-count files. It does
not mutate Scanner records or substitute report review for a Scanner release.

Focused offline validation:

```
python -m pytest -q tests/test_starter_grounded_evidence.py tests/test_beaaa43441_marker_refusal.py backbond/scan-intake/tests/test_adjudication_review.py backbond/scan-intake/tests/test_findings_workflow.py
```

Heavy Scanner checks and preflight remain on the existing isolated VM under
BEAAA-40719. No paid routing or CI opt-in changes belong to this correction.
