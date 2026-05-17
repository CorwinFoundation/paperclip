"""BEAAA-484 — P4 GOVERNANCE Tier B questionnaire intake.

Loads the questionnaire spec, normalises a vendor packet into the canonical
P4 attestation form shape consumed by `scripts.score_p4_attestation.score_form`,
and exposes the GOV.* <-> P4.* mapping used by the review wrapper.

Wraps; does not fork. All scoring math lives in score_p4_attestation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
QUESTIONNAIRE_SPEC_PATH = Path(__file__).resolve().parent / "questionnaire_v0_1.yaml"

# Bumped in lockstep with the questionnaire YAML.
QUESTIONNAIRE_SPEC_VERSION = "0.1.0"
PROBE_SET_VERSION = f"p4_questionnaire_v{QUESTIONNAIRE_SPEC_VERSION}"


def load_questionnaire_spec(path: Path | None = None) -> dict[str, Any]:
    return yaml.safe_load((path or QUESTIONNAIRE_SPEC_PATH).read_text(encoding="utf-8"))


_SPEC = load_questionnaire_spec()

# Canonical GOVERNANCE rubric-point IDs in the order
# contracts/are-scanner-api-v1.md@v1.0 GOVERNANCE row lists them.
GOV_RUBRIC_POINTS: tuple[str, ...] = tuple(rp["id"] for rp in _SPEC["rubric_points"])

# GOV.<X> -> internal scoring dim_id (P4.<n>).
GOV_TO_P4: dict[str, str] = {rp["id"]: rp["dim_id"] for rp in _SPEC["rubric_points"]}

# GOV.<X> -> vendor-facing questionnaire section name.
GOV_TO_SECTION: dict[str, str] = {rp["id"]: rp["questionnaire_section"] for rp in _SPEC["rubric_points"]}

# Per-section canonical attestation block key (P4_1, P4_2, ...).
_SECTION_TO_BLOCK: dict[str, str] = {
    name: section["canonical_block"]
    for name, section in _SPEC["sections"].items()
}


def vendor_packet_to_attestation_form(packet: dict[str, Any]) -> dict[str, Any]:
    """Convert a Tier B vendor questionnaire packet into the canonical
    attestation form consumed by score_p4_attestation.score_form.

    Expected packet shape (vendor-facing):

        {
          "envelope": { vendor_handle, agent_id, attested_at, attested_by,
                        signature_block, form_variant, material_change_log? },
          "sections": {
            "audit_trail": {...},
            "change_management": {...},
            ...
          }
        }

    Output shape matches `harness/fixtures/p4_attestation_pilot_v1.json`
    (the v0.1-LOCKED form schema). Missing sections are dropped — the
    scorer treats absent dim blocks as raw=0 tier=0.
    """
    envelope = packet.get("envelope") or {}
    sections = packet.get("sections") or {}

    form: dict[str, Any] = {
        "version": "0.1.0",
        "customer_id": envelope.get("vendor_handle"),
        "agent_id": envelope.get("agent_id"),
        "attested_at": envelope.get("attested_at"),
        "attested_by": envelope.get("attested_by"),
        "signature_block": envelope.get("signature_block"),
        "form_variant": envelope.get("form_variant", _SPEC.get("form_variant_default", "full_v0")),
        "material_change_log": envelope.get("material_change_log") or [],
        "dims": {},
    }

    for section_name, block in sections.items():
        canonical_block_key = _SECTION_TO_BLOCK.get(section_name)
        if not canonical_block_key:
            # Unknown section — drop quietly; spec validation surfaces the issue.
            continue
        form["dims"][canonical_block_key] = block

    # The scorer expects all six dim block keys present (missing == empty attestation).
    for canonical_block_key in _SECTION_TO_BLOCK.values():
        form["dims"].setdefault(canonical_block_key, {})

    return form
