"""Auditor-readable cosign metadata block — BEAAA-1686 / UW Cond 8.

Pinned at the contract `ScoreResponse` envelope surface so the Auditor and
the Underwriter intake can verify rule-change co-signs without spelunking
into per-probe `_summary.json` artifacts. Pure static manifest of the rule
changes the current scanner / rubric / applicability-policy combination
depends on; refreshed on every rule change (= every CTO co-sign captured
in `cto_cosign_ref` / `head_of_uw_cosign_ref` / `founder_cosign_ref`).

The block is additive in the contract output (BEAAA-796 v1.0.3 envelope
remains byte-stable; no field renamed, no field removed). Reads as a
flat append on top of the existing surface so the verdict envelope test
suite stays green.

Per `contracts/scanner-implementation-supervision-v1.md@v1.0` row 32
("`bin/are-scan` CLI behavior change that affects output JSON shape —
Rule change, YES + ADR (contract version bump)"), the addition itself
is a rule change. ADR-0019 (BEAAA-794) documents it. The contract version
remains at `are-scanner-api-v1@v1.0.3` because the field is additive on
top of the existing v1.0.3 surface (precedent: BEAAA-796 verdict block
was also additive without a major bump). A future Underwriter intake
consumer that requires this field MUST pin to v1.0.3+; pre-v1.0.3
consumers continue to ignore it.

Audit hook (per `agents/auditor-os.md`):

  1. Verify every entry's `cto_cosign_ref` resolves to a BEAAA comment
     or a BEAAA ticket attachment.
  2. Verify `head_of_uw_cosign_ref` resolves to the named cosign file
     (e.g. `BEAAA-1045_houw_cosign_v0.1.md`).
  3. Verify the `rubric_version` / `applicability_policy_version` /
     `scanner_version` in the block match the live ScoreResponse fields.
  4. Verify every rule-change entry has either an `adr_ref` or a documented
     "no-ADR" exemption.
"""
from __future__ import annotations

from typing import Any

# Snapshot of all rule changes active in the current build. Each entry
# describes one BEAAA rule-change ticket; the contract output emits a
# top-level `cosign_metadata.rule_changes[]` list aggregating these.
SCANNER_COSIGN_MANIFEST: list[dict[str, Any]] = [
    # ----- BEAAA-794 — ROBUSTNESS.TOOL_MISUSE.* rubric activation -----
    {
        "ticket": "BEAAA-794",
        "title": "ROBUSTNESS.TOOL_MISUSE.* rubric activation (UNAUTHORIZED_CALL + PARAM_TAMPER)",
        "classification": "rule-change",
        "classification_table_row": (
            "scanner-implementation-supervision-v1@v1.0 Row 23 "
            "(Add a new rubric_point_id to the canonical scoring sheet)"
        ),
        "rubric_point_ids": [
            "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL",
            "ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER",
        ],
        "rubric_version_at_activation": "1.0.1",
        "applicability_policy_version_at_activation": "0.1.1",
        "scanner_version_at_activation": "0.3.9",
        "probe_set_version": "agentdojo_tool_misuse@0.1.0",
        "upstream_eval_version": "agentdojo==0.1.35",
        "sanitization_layer": "harness.probe_sanitizer@beaaa-1683",
        "adr_ref": "docs/adr/adr-0019-beaaa794-tool-misuse-rubric-activation.md",
        "n5_calibration_delta_ref": (
            "tools/calibration-deltas/2026-06-01-beaaa794-tool-misuse-activation.md"
        ),
        "cto_cosign_ref": "comment-b4c564db-1bb0-43c6-bb30-f7cd1c7f2583",
        "head_of_uw_cosign_ref": "BEAAA-1045 + BEAAA-1045_houw_cosign_v0.1.md",
        "founder_cosign_ref": None,
        "founder_required": False,
        "founder_required_rationale": (
            "No pillar weight change; tamper-resistance posture is "
            "additively strengthened by the WS-B sanitization layer in path. "
            "Founder gate reserved for pillar-weight changes and "
            "tamper-resistance reductions per Scanner contract."
        ),
        "implementation_tickets": [
            "BEAAA-1682 (WS-A wrap + register + evidence plumbing)",
            "BEAAA-1683 (WS-B probe-rendering sanitization layer)",
            "BEAAA-1686 (WS-C N=5 calibration + ADR + uncovered_dim retirement + cosign block)",
        ],
    },
    # ----- BEAAA-1683 — WS-B probe-rendering sanitization layer -----
    # Recorded explicitly so the auditor can read the tamper-resistance
    # posture change as a discrete entry. CTO co-sign inherited from parent
    # BEAAA-794 §CTO Cond 4.
    {
        "ticket": "BEAAA-1683",
        "title": "WS-B probe-rendering sanitization layer (tamper-resistance posture strengthening)",
        "classification": "rule-change",
        "classification_table_row": (
            "scanner-implementation-supervision-v1@v1.0 Row 16 "
            "(Tamper-resistance posture change)"
        ),
        "rubric_point_ids": [
            "P3.2 (ROBUSTNESS.INDIRECT_INJECTION) — surface, not scoring math"
        ],
        "scanner_version_at_activation": "0.3.8",
        "sanitization_layer": "harness.probe_sanitizer@beaaa-1683",
        "adr_ref": "docs/adr/adr-0018-ws-b-probe-sanitization.md",
        "n5_calibration_delta_ref": (
            "tools/calibration-deltas/2026-06-01-beaaa1683-ws-b-probe-sanitization-n5.md"
        ),
        "cto_cosign_ref": (
            "Inherited from parent BEAAA-794 §CTO Cond 4 — "
            "/BEAAA/issues/BEAAA-794#document-cosign-trail"
        ),
        "head_of_uw_cosign_ref": "BEAAA-1045 + BEAAA-1045_houw_cosign_v0.1.md (parent)",
        "founder_cosign_ref": None,
        "founder_required": False,
        "founder_required_rationale": (
            "Additive tamper-resistance strengthening, not a reduction. "
            "Founder reduction-gate not triggered."
        ),
        "implementation_tickets": ["BEAAA-1683"],
    },
]


def build_cosign_metadata(
    *,
    scanner_version: str | None = None,
    contract_version: str | None = None,
    rubric_version: str | None = None,
    applicability_policy_version: str | None = None,
) -> dict[str, Any]:
    """Render the auditor-readable cosign metadata block for the envelope.

    The block is intentionally small and grep-friendly:

      cosign_metadata:
        auditor_readable: true
        block_version: "0.1.0"
        scanner_version: <live>
        contract_version: <live>
        rubric_version: <live>
        applicability_policy_version: <live>
        rule_changes: [ <entry>, ... ]   # one per BEAAA rule-change ticket

    Each `rule_changes[]` entry carries the four fields the auditor reads:
    `cto_cosign_ref`, `head_of_uw_cosign_ref`, `founder_cosign_ref` (or
    `null` + a `founder_required_rationale` explaining why), and `adr_ref`.
    """
    return {
        "auditor_readable": True,
        "block_version": "0.1.0",
        "block_ticket_ref": "BEAAA-1686 (WS-C of BEAAA-794) — UW Cond 8 pin",
        "scanner_version": scanner_version,
        "contract_version": contract_version,
        "rubric_version": rubric_version,
        "applicability_policy_version": applicability_policy_version,
        "rule_changes": [dict(entry) for entry in SCANNER_COSIGN_MANIFEST],
    }


__all__ = ["SCANNER_COSIGN_MANIFEST", "build_cosign_metadata"]
