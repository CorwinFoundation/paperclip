"""Underwriting-side post-processors that consume ARE Scanner output.

The Scanner emits `are_total`, `subscores`, `coverage_caveat`, `evidence_refs`
per contracts/are-scanner-api-v1.md@v1.0. Modules here run on top of that
contract output and produce UW-only annotations (e.g. admittance_class).
Contract output shape is not modified by anything in this package.
"""
