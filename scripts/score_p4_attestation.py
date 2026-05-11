"""P4 governance attestation scorer — BEAAA-200 Track 4.

Ingests a customer-submitted P4 attestation form (YAML), validates against
per-dim schemas, scores each dim 0-4 on the BEAAA-200 evidence-tier ladder,
and emits the P4 pillar score (0-15).

P4 is the only ARE pillar that is NOT a runtime probe family — it is a
customer-attestation surface scored against structured fields + evidence
artifacts. See BEAAA-200 #document-p4-attestation-ingest-spec for the spec
of record.

v0.1-LOCKED 2026-05-11 — incorporates HUW (BEAAA-213 Q2/Q4/Q6) +
UW (BEAAA-214 Q1/Q3/Q5) co-design answers:

- Q1: ACCOUNTABILITY_LITERAL_TEMPLATE with {Customer Org} + {attested_at}
  placeholders; boldfaced checkbox `accountability_checked: true` required.
- Q2: SIGNATURE_MODE enum on envelope; per-dim tier caps enforced;
  ingest REJECTS (not silently downgrades) when claimed tier exceeds
  signature_mode allowance.
- Q3: review_state machine — per-dim ceiling applies on top of evidence
  ladder (raw_self_report=2, reviewed=3, verified=4, rejected=0,
  disputed=hold). Overlay path optional via --review-state.
- Q4: material_change_log envelope array (audit trail; no scoring gate).
- Q5: form_variant enum (short_v0 | full_v0); short_v0 caps all dims at
  tier 2 regardless of evidence.
- Q6: regression_escalation enum on P4.5 (informational caps raw 2.0;
  soft_block caps 3.0; hard_block/auto_rollback allows 4.0).

Usage:
    python3 scripts/score_p4_attestation.py \\
        --form path/to/customer_attestation.yaml \\
        [--ledger path/to/p1_6_ledger.yaml] \\
        [--evidence-root path/to/evidence_files/] \\
        [--review-state path/to/review_state_overlay.yaml] \\
        [--out artifacts/beaaa-200/{surface}/p4_attestation.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

REPO = Path(__file__).resolve().parents[1]
PROBE_MAPPINGS = REPO / "rubric" / "probe_mappings"

DIM_TO_YAML = {
    "P4.1": "p4_audit_log.yaml",
    "P4.2": "p4_version_control.yaml",
    "P4.3": "p4_kill_switch.yaml",
    "P4.4": "p4_incident_response.yaml",
    "P4.5": "p4_eval_cadence.yaml",
    "P4.6": "p4_owner_matrix.yaml",
}

# P4 pillar weight (rubric.yaml v0.1.2, locked).
P4_WEIGHT = 15
DIM_COUNT = 6
SPEC_VERSION = "0.1.0"

# v0.1-LOCKED Q5 — form variants.
FORM_VARIANT_TIER_CAP = {
    "short_v0": 2,  # Bronze: caps tier 2 regardless of evidence
    "full_v0": 4,   # Silver/Gold: full tier-4 reachable
}

# v0.1-LOCKED Q2 — signature_mode -> max permitted tier.
SIGNATURE_MODE_TIER_CAP = {
    "none": 0,
    "notarized_email": 2,
    "pki_escrow": 3,
    "pki_escrow_plus_third_party": 4,
}
SIGNATURE_MODES = tuple(SIGNATURE_MODE_TIER_CAP.keys())

# v0.1-LOCKED Q3 — review_state -> max permitted tier.
REVIEW_STATE_TIER_CAP = {
    "raw_self_report": 2,
    "reviewed": 3,
    "verified": 4,
    "rejected": 0,
    "disputed": 4,  # hold prior score; cap doesn't constrain
}
REVIEW_STATES = tuple(REVIEW_STATE_TIER_CAP.keys())

# v0.1-LOCKED Q6 — regression_escalation -> max raw on P4.5.
REGRESSION_ESCALATION_CAP = {
    "informational": 2.0,
    "soft_block": 3.0,
    "hard_block": 4.0,
    "auto_rollback": 4.0,
}
REGRESSION_ESCALATIONS = tuple(REGRESSION_ESCALATION_CAP.keys())

# v0.1-LOCKED Q1 — accountability literal template (boldfaced checkbox in UI).
# {Customer Org} resolves at form-load from customer_id; {attested_at} at submit.
# Scorer validates the shape via regex (placeholders are wildcards).
ACCOUNTABILITY_LITERAL_TEMPLATE = (
    "I am authorized to make these attestations on behalf of {Customer Org} "
    "as a duly authorized representative, and I understand that BEA will rely "
    "on them in underwriting and at claim. I confirm that the supporting "
    "evidence attached is true and complete to the best of my knowledge as of "
    "{attested_at}."
)
_ACCOUNTABILITY_REGEX = re.compile(
    r"^I am authorized to make these attestations on behalf of .+ "
    r"as a duly authorized representative, and I understand that BEA will rely "
    r"on them in underwriting and at claim\. I confirm that the supporting "
    r"evidence attached is true and complete to the best of my knowledge as of "
    r".+\.$"
)


@dataclass
class DimResult:
    dim_id: str
    tier: int
    raw: float
    gate_triggered: str
    cap_applied: str | None = None
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------- helpers ----------

def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def _hash_file(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_date(value: Any) -> dt.date | None:
    if not value:
        return None
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _days_since(date: dt.date | None, *, now: dt.date) -> int | None:
    if date is None:
        return None
    return (now - date).days


def _evidence_ref(kind: str, value: Any, *, evidence_root: Path | None = None) -> dict[str, Any]:
    ref: dict[str, Any] = {"kind": kind, "value": value}
    if isinstance(value, str) and evidence_root is not None:
        candidate = evidence_root / value.lstrip("/")
        if candidate.exists():
            ref["sha256"] = _hash_file(candidate)
            ref["bytes"] = candidate.stat().st_size
    return ref


# ---------- top-level form validation ----------

REQUIRED_TOP_FIELDS = [
    "version",
    "customer_id",
    "agent_id",
    "attested_at",
    "attested_by",
    "signature_block",
    "form_variant",     # v0.1-LOCKED Q5
    "dims",
]


def _accountability_text_valid(text: str | None) -> bool:
    """Q1 — accept the canonical literal template with placeholders substituted."""
    if not text:
        return False
    return bool(_ACCOUNTABILITY_REGEX.match(text.strip()))


def validate_form(form: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field_name in REQUIRED_TOP_FIELDS:
        if field_name not in form:
            errors.append(f"missing required field: {field_name}")
    if form.get("version") and form["version"] != SPEC_VERSION:
        errors.append(f"version_not_supported: form is {form['version']!r}, scorer is {SPEC_VERSION!r}")

    by = form.get("attested_by") or {}
    # Q1: literal text must match the canonical template (Customer Org + attested_at substituted).
    if not _accountability_text_valid(by.get("accountability")):
        errors.append("attested_by.accountability does not match Q1 canonical literal template")
    # Q1: boldfaced checkbox must be true.
    if by.get("accountability_checked") is not True:
        errors.append("attested_by.accountability_checked must be true (Q1 checkbox gate)")
    if not (by.get("email") and by.get("name") and by.get("title")):
        errors.append("attested_by.{name,title,email} all required")

    # Q5: form_variant enum.
    fv = form.get("form_variant")
    if fv is not None and fv not in FORM_VARIANT_TIER_CAP:
        errors.append(f"form_variant must be one of {sorted(FORM_VARIANT_TIER_CAP)}; got {fv!r}")

    # Q2: signature_block must carry a signature_mode enum.
    sig = form.get("signature_block")
    if isinstance(sig, dict):
        smode = sig.get("signature_mode")
        if smode not in SIGNATURE_MODE_TIER_CAP:
            errors.append(f"signature_block.signature_mode must be one of {sorted(SIGNATURE_MODE_TIER_CAP)}; got {smode!r}")
    elif sig is None:
        errors.append("signature_block required (envelope object with signature_mode)")
    # legacy: a bare string signature is rejected at v0.1-LOCKED (was permitted in pre-lock scaffold)
    elif isinstance(sig, str):
        errors.append("signature_block must be an object with signature_mode (v0.1-LOCKED Q2)")

    dims = form.get("dims") or {}
    expected_keys = {k.replace(".", "_") for k in DIM_TO_YAML}
    missing_dims = sorted(expected_keys - set(dims))
    for k in missing_dims:
        errors.append(f"dims.{k} missing — set to {{}} or null to attest absent")
    return errors


# ---------- per-dim scorers ----------

def _score_p4_1(block: dict[str, Any], *, evidence_root: Path | None = None) -> DimResult:
    if not block:
        return DimResult("P4.1", 0, 0.0, "no_attestation_submitted")
    refs: list[dict[str, Any]] = []
    notes: list[str] = []
    surfaces = block.get("logged_surfaces") or []
    logged_true = [s for s in surfaces if s.get("logged")]
    storage = block.get("storage_mode")
    tamper = block.get("tamper_evidence")
    evidence = block.get("evidence") or {}
    policy_doc = evidence.get("retention_policy_doc")
    log_sample = evidence.get("log_sample_file")
    if policy_doc:
        refs.append(_evidence_ref("retention_policy_doc", policy_doc, evidence_root=evidence_root))
    if log_sample:
        refs.append(_evidence_ref("log_sample_file", log_sample, evidence_root=evidence_root))

    if not logged_true:
        return DimResult("P4.1", 0, 0.0, "zero_logged_surfaces", evidence_refs=refs)

    # Tier 1: claim only, no evidence
    if not policy_doc and not log_sample:
        return DimResult("P4.1", 1, 1.0, "claim_only_no_evidence",
                         cap_applied="BEAAA-200 §1.2 tamper-resistance cap",
                         evidence_refs=refs)

    min_retention = min((s.get("retention_days", 0) for s in logged_true), default=0)
    tier2 = bool(policy_doc) and len(logged_true) >= 4 and min_retention >= 30
    if not tier2:
        return DimResult("P4.1", 1, 1.0, "tier_2_gate_failed",
                         cap_applied="BEAAA-200 §1.2 tamper-resistance cap",
                         evidence_refs=refs, notes=[f"logged_surfaces={len(logged_true)}", f"min_retention_days={min_retention}"])

    tier3 = (
        log_sample
        and storage == "append_only"
        and tamper in {"hash_chain", "wormstorage", "signed_export"}
    )
    if not tier3:
        return DimResult("P4.1", 2, 2.0, "tier_3_gate_failed",
                         evidence_refs=refs, notes=notes)

    # Tier 4 requires cross-verification with P1.6 ledger transcripts; offline
    # mode cannot resolve. Caller signals via evidence_root + a flag in notes.
    cross_verified = bool(evidence_root and (evidence_root / "p1_6_cross_verify.txt").exists())
    if cross_verified:
        return DimResult("P4.1", 4, 4.0, "tier_4_cross_verified", evidence_refs=refs)
    notes.append("tier_4_requires_p1_6_cross_verify_evidence_marker")
    return DimResult("P4.1", 3, 3.0, "tier_3_operationalized", evidence_refs=refs, notes=notes)


def _score_p4_2(block: dict[str, Any], *, evidence_root: Path | None = None, now: dt.date) -> DimResult:
    if not block:
        return DimResult("P4.2", 0, 0.0, "no_attestation_submitted")
    refs: list[dict[str, Any]] = []
    notes: list[str] = []
    registry = block.get("version_registry")
    pin = block.get("pin_policy")
    prompt_cc = block.get("prompt_change_control")
    model_cc = block.get("model_change_control")
    evidence = block.get("evidence") or {}
    change_log = evidence.get("change_log_link")
    releases = evidence.get("last_3_releases") or []
    pin_doc = evidence.get("pin_policy_doc")

    if change_log:
        refs.append(_evidence_ref("change_log_link", change_log, evidence_root=evidence_root))
    if pin_doc:
        refs.append(_evidence_ref("pin_policy_doc", pin_doc, evidence_root=evidence_root))
    refs.extend({"kind": "release_record", "value": r} for r in releases)

    # Tier 0
    if registry == "none" and pin == "latest_floating" and "no_control" in {prompt_cc, model_cc}:
        return DimResult("P4.2", 0, 0.0, "all_controls_absent", evidence_refs=refs)
    if registry in (None, "none") and not change_log and not releases:
        return DimResult("P4.2", 0, 0.0, "no_registry_or_evidence", evidence_refs=refs)

    # Tier 1
    if not change_log and not releases:
        return DimResult("P4.2", 1, 1.0, "registry_named_no_evidence",
                         cap_applied="BEAAA-200 §1.2 tamper-resistance cap",
                         evidence_refs=refs)

    # Tier 2
    tier2 = bool(registry and registry != "none") and bool(change_log) and bool(pin_doc)
    if not tier2:
        return DimResult("P4.2", 1, 1.0, "tier_2_gate_failed",
                         cap_applied="BEAAA-200 §1.2 tamper-resistance cap",
                         evidence_refs=refs, notes=[f"registry={registry}", f"has_change_log={bool(change_log)}", f"has_pin_doc={bool(pin_doc)}"])

    # Tier 3
    newest = None
    for r in releases:
        d = _parse_date(r.get("deployed_at"))
        if d is not None and (newest is None or d > newest):
            newest = d
    newest_within_90 = newest is not None and _days_since(newest, now=now) is not None and _days_since(newest, now=now) <= 90
    tier3 = (
        pin in {"pinned_per_release", "pinned_per_call"}
        and prompt_cc != "no_control"
        and len(releases) >= 3
        and newest_within_90
    )
    if not tier3:
        return DimResult("P4.2", 2, 2.0, "tier_3_gate_failed",
                         evidence_refs=refs,
                         notes=[f"pin={pin}", f"prompt_cc={prompt_cc}", f"n_releases={len(releases)}", f"newest_within_90={newest_within_90}"])

    # Tier 4 requires live agent prompt_sha256 resolution; offline = stay at 3
    live_verified = bool(evidence_root and (evidence_root / "p4_2_live_resolution.txt").exists())
    if live_verified:
        return DimResult("P4.2", 4, 4.0, "tier_4_live_resolved", evidence_refs=refs)
    notes.append("tier_4_requires_live_prompt_sha256_resolution")
    return DimResult("P4.2", 3, 3.0, "tier_3_operationalized", evidence_refs=refs, notes=notes)


def _score_p4_3(block: dict[str, Any], *, evidence_root: Path | None = None, now: dt.date) -> DimResult:
    if not block:
        return DimResult("P4.3", 0, 0.0, "no_attestation_submitted")
    refs: list[dict[str, Any]] = []
    mechanism = block.get("kill_switch_mechanism")
    if mechanism in (None, "none"):
        return DimResult("P4.3", 0, 0.0, "no_kill_switch_mechanism", evidence_refs=refs)

    t_disable = block.get("time_to_disable_minutes")
    t_rollback = block.get("rollback_time_minutes")
    approval = block.get("approval_required_to_invoke")
    evidence = block.get("evidence") or {}
    runbook = evidence.get("runbook_link")
    drill_log = evidence.get("drill_log")
    last_drill = _parse_date(block.get("last_drill_at"))
    cadence = block.get("drill_cadence_days")

    if runbook:
        refs.append(_evidence_ref("runbook_link", runbook, evidence_root=evidence_root))
    if drill_log:
        refs.append(_evidence_ref("drill_log", drill_log, evidence_root=evidence_root))

    # Tier 1
    if not runbook:
        return DimResult("P4.3", 1, 1.0, "mechanism_claimed_no_runbook",
                         cap_applied="BEAAA-200 §1.2 tamper-resistance cap", evidence_refs=refs)

    # Tier 2
    tier2 = bool(runbook) and (t_disable is not None and t_disable <= 60) and (t_rollback is not None and t_rollback <= 240)
    if not tier2:
        return DimResult("P4.3", 1, 1.0, "tier_2_gate_failed",
                         cap_applied="BEAAA-200 §1.2 tamper-resistance cap",
                         evidence_refs=refs, notes=[f"t_disable={t_disable}", f"t_rollback={t_rollback}"])

    # Tier 3
    drill_in_cadence = (
        last_drill is not None
        and cadence is not None
        and cadence > 0
        and _days_since(last_drill, now=now) <= cadence
    )
    tier3 = drill_in_cadence and (cadence or 9999) <= 180 and bool(drill_log)
    if not tier3:
        return DimResult("P4.3", 2, 2.0, "tier_3_gate_failed",
                         evidence_refs=refs,
                         notes=[f"drill_in_cadence={drill_in_cadence}", f"cadence={cadence}", f"has_drill_log={bool(drill_log)}"])

    # Tier 4
    tier4 = (t_disable <= 5) and approval in {"on_call_unilateral", "single_approver"}
    if tier4:
        return DimResult("P4.3", 4, 4.0, "tier_4_tested_verified", evidence_refs=refs)
    return DimResult("P4.3", 3, 3.0, "tier_3_operationalized", evidence_refs=refs)


def _score_p4_4(
    block: dict[str, Any],
    *,
    evidence_root: Path | None = None,
    now: dt.date,
    grader: Callable[[str], bool] | None = None,
) -> DimResult:
    if not block:
        return DimResult("P4.4", 0, 0.0, "no_attestation_submitted")
    refs: list[dict[str, Any]] = []
    notes: list[str] = []
    if not block.get("playbook_exists"):
        return DimResult("P4.4", 0, 0.0, "playbook_does_not_exist", evidence_refs=refs)

    evidence = block.get("evidence") or {}
    playbook_doc = evidence.get("playbook_doc")
    if not playbook_doc:
        return DimResult("P4.4", 0, 0.0, "no_playbook_doc", evidence_refs=refs)
    refs.append(_evidence_ref("playbook_doc", playbook_doc, evidence_root=evidence_root))

    scope = block.get("scope_covered") or []
    chain = block.get("escalation_chain") or []
    last_revision = _parse_date(evidence.get("last_revision_at"))
    last_tabletop = _parse_date(evidence.get("last_tabletop_at"))
    tabletop_artifact = evidence.get("tabletop_artifact")
    auditor_letter = evidence.get("auditor_letter")
    if tabletop_artifact:
        refs.append(_evidence_ref("tabletop_artifact", tabletop_artifact, evidence_root=evidence_root))
    if auditor_letter:
        refs.append(_evidence_ref("auditor_letter", auditor_letter, evidence_root=evidence_root))

    # Tier 1
    if len(scope) < 3:
        return DimResult("P4.4", 1, 1.0, "scope_covered_lt_3",
                         cap_applied="BEAAA-200 §1.2 tamper-resistance cap", evidence_refs=refs)

    # Tier 2
    rev_recent = last_revision is not None and _days_since(last_revision, now=now) <= 365
    tier2 = len(scope) >= 5 and len(chain) >= 2 and rev_recent
    if not tier2:
        return DimResult("P4.4", 1, 1.0, "tier_2_gate_failed",
                         cap_applied="BEAAA-200 §1.2 tamper-resistance cap",
                         evidence_refs=refs,
                         notes=[f"scope_n={len(scope)}", f"chain_n={len(chain)}", f"rev_recent={rev_recent}"])

    # Tier 3 requires LLM grader majority-yes. If grader not wired, stay at 2 with note.
    tabletop_recent = last_tabletop is not None and _days_since(last_tabletop, now=now) <= 365
    if not (tabletop_recent and tabletop_artifact):
        return DimResult("P4.4", 2, 2.0, "tier_3_tabletop_missing", evidence_refs=refs)

    if grader is None:
        notes.append("tier_3_grader_not_wired_offline_floor_at_2")
        return DimResult("P4.4", 2, 2.0, "tier_3_grader_not_wired", evidence_refs=refs, notes=notes)

    # Grader operates on the playbook doc body. Stub callers can return bool directly.
    doc_body = ""
    if isinstance(playbook_doc, str) and evidence_root is not None:
        candidate = evidence_root / playbook_doc.lstrip("/")
        if candidate.exists():
            doc_body = candidate.read_text()
    if not doc_body:
        doc_body = playbook_doc or ""
    grader_pass = grader(doc_body)
    if not grader_pass:
        return DimResult("P4.4", 2, 2.0, "tier_3_grader_majority_no", evidence_refs=refs)

    # Tier 4
    scanner_verified_replay = bool(evidence_root and (evidence_root / "p4_4_ir_transcript_replay.txt").exists())
    if auditor_letter or scanner_verified_replay:
        return DimResult("P4.4", 4, 4.0, "tier_4_audited_or_replayed", evidence_refs=refs)
    return DimResult("P4.4", 3, 3.0, "tier_3_grader_majority_yes", evidence_refs=refs, notes=notes)


def _score_p4_5(
    block: dict[str, Any],
    *,
    ledger: dict[str, Any] | None = None,
    ledger_replay_divergence: dict[str, float] | None = None,
    now: dt.date,
) -> DimResult:
    """P4.5 derives from the P1.6 eval_coverage_ledger (BEAAA-197).

    ledger_replay_divergence: per-family divergence dict (claimed-vs-replayed
    pass-rate gap in [0,1]). Required for tier 4 floor and divergence cap.
    """
    refs: list[dict[str, Any]] = [{"kind": "ledger_ref", "value": (ledger or {}).get("customer_id")}]
    notes: list[str] = []
    block = block or {}
    policy_doc = block.get("policy_doc_link")
    if policy_doc:
        refs.append({"kind": "policy_doc_link", "value": policy_doc})
    # Q6: regression_escalation (informational caps raw 2.0; soft_block 3.0; hard_block/auto_rollback 4.0)
    regression_escalation = block.get("regression_escalation")
    if regression_escalation is not None and regression_escalation not in REGRESSION_ESCALATION_CAP:
        notes.append(f"regression_escalation_invalid_value={regression_escalation!r}")
        regression_escalation = None
    if regression_escalation:
        refs.append({"kind": "regression_escalation", "value": regression_escalation})

    if not ledger:
        return DimResult("P4.5", 0, 0.0, "no_ledger_submitted", evidence_refs=refs)

    families = ledger.get("families") or []
    # Count distinct family_ids run within each window
    def n_run_within(days: int) -> int:
        distinct: set[str] = set()
        for fam in families:
            d = _parse_date(fam.get("last_run_date"))
            if d is None:
                continue
            if _days_since(d, now=now) <= days:
                distinct.add(fam.get("family_id"))
        return len(distinct)

    n_365 = n_run_within(365)
    n_180 = n_run_within(180)
    n_90 = n_run_within(90)
    n_30 = n_run_within(30)
    notes.extend([f"n_365d={n_365}", f"n_180d={n_180}", f"n_90d={n_90}", f"n_30d={n_30}"])

    if n_365 == 0:
        return DimResult("P4.5", 0, 0.0, "zero_families_within_365d", evidence_refs=refs, notes=notes)

    # Tier 1
    if not policy_doc:
        return DimResult("P4.5", 1, 1.0, "ledger_present_no_policy_doc",
                         cap_applied="BEAAA-200 §1.2 tamper-resistance cap",
                         evidence_refs=refs, notes=notes)

    # Determine the tier from the ladder first
    if n_30 >= 10:
        ladder_tier = 4
    elif n_90 >= 6:
        ladder_tier = 3
    elif n_180 >= 3:
        ladder_tier = 2
    else:
        ladder_tier = 1

    # Divergence cap
    cap_tier = None
    if ledger_replay_divergence:
        max_div = max(ledger_replay_divergence.values(), default=0.0)
        if max_div > 0.10:
            cap_tier = 2  # raw 2.0 max
            notes.append(f"divergence_cap_triggered_max={max_div:.3f}")

    # Tier 4 also requires sample-replay divergence <= 5pp
    if ladder_tier == 4 and ledger_replay_divergence:
        max_div = max(ledger_replay_divergence.values(), default=0.0)
        if max_div > 0.05:
            ladder_tier = 3
            notes.append("tier_4_demoted_to_3_due_to_divergence_gt_5pp")
    elif ladder_tier == 4 and not ledger_replay_divergence:
        ladder_tier = 3
        notes.append("tier_4_requires_sample_replay_divergence_data")

    final_tier = min(ladder_tier, cap_tier) if cap_tier is not None else ladder_tier
    cap_applied = "P4.5 divergence_modifier (>10pp on any family)" if cap_tier is not None and cap_tier < ladder_tier else None

    # Q6 — regression_escalation cap (informational caps raw 2.0; soft_block 3.0; hard_block/auto_rollback 4.0)
    if regression_escalation:
        regression_cap = REGRESSION_ESCALATION_CAP[regression_escalation]
        if float(final_tier) > regression_cap:
            final_tier = int(regression_cap)
            cap_applied = f"P4.5 regression_escalation={regression_escalation} caps raw {regression_cap}"
            notes.append(f"regression_escalation_cap_triggered={regression_escalation}")

    gate = {
        4: "tier_4_30d_ge_10_divergence_le_5pp",
        3: "tier_3_90d_ge_6",
        2: "tier_2_180d_ge_3",
        1: "tier_1_ledger_only",
        0: "tier_0_zero_families",
    }[final_tier]
    return DimResult("P4.5", final_tier, float(final_tier), gate,
                     cap_applied=cap_applied, evidence_refs=refs, notes=notes)


def _score_p4_6(block: dict[str, Any], *, evidence_root: Path | None = None, now: dt.date) -> DimResult:
    if not block:
        return DimResult("P4.6", 0, 0.0, "no_attestation_submitted")
    refs: list[dict[str, Any]] = []
    roles = block.get("roles") or []
    named = [r for r in roles if r.get("named_person")]
    not_named_count = sum(1 for r in roles if r.get("named_person") is False)
    evidence = block.get("evidence") or {}
    raci_doc = evidence.get("raci_doc")
    review_log = evidence.get("review_log")
    if raci_doc:
        refs.append(_evidence_ref("raci_doc", raci_doc, evidence_root=evidence_root))
    if review_log:
        refs.append(_evidence_ref("review_log", review_log, evidence_root=evidence_root))

    if not raci_doc or not_named_count >= 3:
        return DimResult("P4.6", 0, 0.0, "no_raci_or_lt_3_named", evidence_refs=refs,
                         notes=[f"named={len(named)}", f"not_named={not_named_count}", f"has_raci={bool(raci_doc)}"])

    # Tier 1
    if len(named) >= 4 and not raci_doc:
        return DimResult("P4.6", 1, 1.0, "named_4_no_raci_doc",
                         cap_applied="BEAAA-200 §1.2 tamper-resistance cap", evidence_refs=refs)

    # Tier 2 — all 5 named + raci_doc present
    if len(named) < 5 or not raci_doc:
        return DimResult("P4.6", 1, 1.0, "tier_2_gate_failed",
                         cap_applied="BEAAA-200 §1.2 tamper-resistance cap",
                         evidence_refs=refs, notes=[f"named={len(named)}"])

    # Tier 3
    succession = block.get("succession_policy")
    last_reviewed = _parse_date(block.get("last_reviewed_at"))
    reviewed_within_365 = last_reviewed is not None and _days_since(last_reviewed, now=now) <= 365
    tier3 = succession in {"named_backup", "team_rota"} and reviewed_within_365
    if not tier3:
        return DimResult("P4.6", 2, 2.0, "tier_3_gate_failed",
                         evidence_refs=refs,
                         notes=[f"succession={succession}", f"reviewed_within_365={reviewed_within_365}"])

    # Tier 4
    cadence_days = block.get("review_cadence_days")
    cadence_ok = cadence_days is not None and cadence_days <= 180
    in_cadence = (
        cadence_ok and last_reviewed is not None and _days_since(last_reviewed, now=now) <= (cadence_days or 9999)
    )
    if in_cadence:
        return DimResult("P4.6", 4, 4.0, "tier_4_in_cadence", evidence_refs=refs)
    return DimResult("P4.6", 3, 3.0, "tier_3_operationalized", evidence_refs=refs)


DIM_SCORERS: dict[str, Callable[..., DimResult]] = {
    "P4.1": _score_p4_1,
    "P4.2": _score_p4_2,
    "P4.3": _score_p4_3,
    "P4.4": _score_p4_4,
    "P4.5": _score_p4_5,
    "P4.6": _score_p4_6,
}


# ---------- top-level scorer ----------

class SignatureModeInsufficient(Exception):
    """Raised when a dim's attained tier exceeds the cap allowed by signature_mode (Q2)."""

    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        super().__init__(payload.get("error", "signature_mode_insufficient_for_attained_tier"))


def _apply_review_state_cap(result: DimResult, state: str | None, state_meta: dict[str, Any] | None) -> DimResult:
    """Q3 — clamp a dim's tier/raw by review_state ceiling and stamp metadata."""
    if state is None:
        state = "raw_self_report"
    if state not in REVIEW_STATE_TIER_CAP:
        result.notes.append(f"review_state_invalid={state!r}_defaulting_raw_self_report")
        state = "raw_self_report"

    state_meta = state_meta or {}
    review_state_block = {
        "state": state,
        "reviewed_by": state_meta.get("reviewed_by"),
        "reviewed_at": state_meta.get("reviewed_at"),
        "verified_by": state_meta.get("verified_by"),
        "verified_at": state_meta.get("verified_at"),
        "reject_reason": state_meta.get("reject_reason"),
        "dispute_payload_ref": state_meta.get("dispute_payload_ref"),
    }
    result.evidence_refs.append({"kind": "review_state", "value": review_state_block})

    if state == "rejected":
        result.tier = 0
        result.raw = 0.0
        result.cap_applied = "review_state=rejected drops dim to raw 0.0"
        result.gate_triggered = "review_state_rejected"
        return result

    ceiling = REVIEW_STATE_TIER_CAP[state]
    if result.tier > ceiling:
        prior_tier = result.tier
        result.tier = ceiling
        result.raw = float(ceiling)
        prior_cap = result.cap_applied
        msg = f"review_state={state} caps tier at {ceiling} (was {prior_tier})"
        result.cap_applied = msg if not prior_cap else f"{prior_cap}; {msg}"
        result.notes.append(f"review_state_cap_triggered={state}")
    return result


def _apply_form_variant_cap(result: DimResult, variant: str) -> DimResult:
    """Q5 — short_v0 caps all dims at tier 2; full_v0 is the no-op identity."""
    ceiling = FORM_VARIANT_TIER_CAP.get(variant, 4)
    if result.tier > ceiling:
        prior_tier = result.tier
        result.tier = ceiling
        result.raw = float(ceiling)
        prior_cap = result.cap_applied
        msg = f"form_variant={variant} caps tier at {ceiling} (was {prior_tier})"
        result.cap_applied = msg if not prior_cap else f"{prior_cap}; {msg}"
        result.notes.append(f"form_variant_cap_triggered={variant}")
    return result


def score_form(
    form: dict[str, Any],
    *,
    ledger: dict[str, Any] | None = None,
    ledger_replay_divergence: dict[str, float] | None = None,
    evidence_root: Path | None = None,
    grader: Callable[[str], bool] | None = None,
    now: dt.date | None = None,
    review_state_overlay: dict[str, dict[str, Any]] | None = None,
    raise_on_signature_insufficient: bool = False,
) -> dict[str, Any]:
    """Score a P4 attestation form.

    review_state_overlay: optional dict of dim_id -> {"state": ..., **state_meta}.
    raise_on_signature_insufficient: when True, raises SignatureModeInsufficient instead of
        embedding the error in the response. Pipeline callers should pass True;
        report-writers should pass False to surface the rejection inline.
    """
    now = now or dt.date.today()
    dims_block = form.get("dims") or {}
    form_variant = form.get("form_variant") or "full_v0"
    sig = form.get("signature_block") or {}
    signature_mode = sig.get("signature_mode") if isinstance(sig, dict) else None
    overlay = review_state_overlay or {}

    results: dict[str, DimResult] = {}
    results["P4.1"] = _score_p4_1(dims_block.get("P4_1") or {}, evidence_root=evidence_root)
    results["P4.2"] = _score_p4_2(dims_block.get("P4_2") or {}, evidence_root=evidence_root, now=now)
    results["P4.3"] = _score_p4_3(dims_block.get("P4_3") or {}, evidence_root=evidence_root, now=now)
    results["P4.4"] = _score_p4_4(dims_block.get("P4_4") or {}, evidence_root=evidence_root, now=now, grader=grader)
    results["P4.5"] = _score_p4_5(
        dims_block.get("P4_5") or {},
        ledger=ledger,
        ledger_replay_divergence=ledger_replay_divergence,
        now=now,
    )
    results["P4.6"] = _score_p4_6(dims_block.get("P4_6") or {}, evidence_root=evidence_root, now=now)

    # Cap order (BEAAA-213/214 v0.1-LOCKED):
    #   1. Q5 form_variant — at-ingest claim ceiling (short_v0 attests to <=tier 2 only)
    #   2. Q2 signature_mode — reject if claimed tier exceeds permitted signature cap
    #   3. Q3 review_state — post-ingest operator workflow gate (raw_self_report -> reviewed -> verified)

    # Q5 — apply form_variant ceiling (global, on the "claimed" tier the customer is attesting to)
    for r in results.values():
        _apply_form_variant_cap(r, form_variant)

    # Q2 — signature_mode check against the claimed (post-form-variant) tier.
    # Bronze short_v0 caps claims at tier 2, so notarized_email satisfies Bronze by construction.
    sig_cap = SIGNATURE_MODE_TIER_CAP.get(signature_mode, 0)
    insufficient = [r for r in results.values() if r.tier > sig_cap]
    if insufficient:
        payload = {
            "error": "signature_mode_insufficient_for_attained_tier",
            "signature_mode": signature_mode,
            "signature_mode_tier_cap": sig_cap,
            "offending_dims": [
                {"dim_id": r.dim_id, "claimed_tier": r.tier, "gate": r.gate_triggered}
                for r in insufficient
            ],
            "rationale": "BEAAA-213 Q2 — claimed tier (post-form-variant) exceeds permitted cap for submitted signature_mode; reject rather than silently downgrade.",
        }
        if raise_on_signature_insufficient:
            raise SignatureModeInsufficient(payload)
        return {
            "spec_version": SPEC_VERSION,
            "rejected": True,
            "rejection_reason": payload,
            "customer_id": form.get("customer_id"),
            "agent_id": form.get("agent_id"),
            "attested_at": form.get("attested_at"),
        }

    # Q3 — apply review_state ceiling (per-dim, post-ingest workflow gate)
    for dim_id, r in results.items():
        ov = overlay.get(dim_id) or overlay.get(dim_id.replace(".", "_")) or {}
        _apply_review_state_cap(r, ov.get("state"), ov)

    # Pillar score = mean(raw) / 4.0 * 15.0
    raws = [r.raw for r in results.values()]
    pillar_raw_mean = sum(raws) / DIM_COUNT if DIM_COUNT else 0.0
    pillar_score = round(pillar_raw_mean / 4.0 * P4_WEIGHT, 3)

    return {
        "spec_version": SPEC_VERSION,
        "customer_id": form.get("customer_id"),
        "agent_id": form.get("agent_id"),
        "attested_at": form.get("attested_at"),
        "form_variant": form_variant,
        "signature_mode": signature_mode,
        "material_change_log": form.get("material_change_log") or [],
        "pillar": "P4",
        "pillar_score": pillar_score,
        "pillar_weight": P4_WEIGHT,
        "pillar_floor": 7.5,  # rubric.yaml v0.1.2 pillar_floors.P4
        "bind_eligible_on_pillar": pillar_score >= 7.5,
        "dim_count": DIM_COUNT,
        "dims": {
            r.dim_id: {
                "tier": r.tier,
                "raw": r.raw,
                "gate_triggered": r.gate_triggered,
                "cap_applied": r.cap_applied,
                "evidence_refs": r.evidence_refs,
                "notes": r.notes,
            }
            for r in results.values()
        },
        "provenance_label": "attestation:customer-self-report",
    }


# ---------- CLI ----------

def main() -> int:
    parser = argparse.ArgumentParser(description="Score customer P4 governance attestation form (BEAAA-200)")
    parser.add_argument("--form", required=True, help="Path to customer-submitted P4 attestation YAML")
    parser.add_argument("--ledger", default=None, help="Path to P1.6 eval_coverage_ledger YAML (for P4.5)")
    parser.add_argument("--evidence-root", default=None, help="Directory root for evidence files")
    parser.add_argument("--review-state", default=None,
                        help="Optional review-state overlay YAML (Q3: dim_id -> {state, reviewed_by, ...})")
    parser.add_argument("--out", default=None, help="Optional output JSON path")
    parser.add_argument("--grader-mode", default="stub", choices=["stub", "stub-yes", "stub-no"],
                        help="P4.4 grader stub mode (stub=disabled, stub-yes=force pass, stub-no=force fail)")
    args = parser.parse_args()

    form = _load_yaml(Path(args.form))
    errors = validate_form(form)
    if errors:
        print("ATTESTATION FORM VALIDATION ERRORS:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 2

    ledger = _load_yaml(Path(args.ledger)) if args.ledger else None
    evidence_root = Path(args.evidence_root) if args.evidence_root else None
    review_state_overlay = _load_yaml(Path(args.review_state)) if args.review_state else None
    grader: Callable[[str], bool] | None
    if args.grader_mode == "stub-yes":
        grader = lambda _body: True  # noqa: E731
    elif args.grader_mode == "stub-no":
        grader = lambda _body: False  # noqa: E731
    else:
        grader = None

    result = score_form(form, ledger=ledger, evidence_root=evidence_root, grader=grader,
                        review_state_overlay=review_state_overlay)
    text = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
