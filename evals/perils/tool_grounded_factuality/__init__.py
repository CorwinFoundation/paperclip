"""Tool-grounded factuality pilot (BEAAA-12028, Gap-3 residual).

A business-factual assistant reads a mock pricing spreadsheet / inventory DB via
read-only tools; a deterministic verifier checks the agent's output contains no
FABRICATED numbers — every price / stock figure it states must be grounded in a
figure the tools actually returned. Same live-runner + pass^k + end-state pattern
as the tau-bench money-movement wrap, targeting `tool_grounded_factuality`
evidence rows at `evidence_grade = deployment_measured` (are-scanner-api-v1
v1.8.0). Reported-not-scored per ADR-0045 — no are_total / weight / band change.
"""
