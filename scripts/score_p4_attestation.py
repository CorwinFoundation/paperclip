"""P4 governance attestation scorer — BEAAA-200 Track 4.

Ingests a customer-submitted P4 attestation form (YAML), validates against
per-dim schemas, scores each dim 0-4 on the BEAAA-200 evidence-tier ladder,
and emits the P4 pillar score (0-15).

P4 is the only ARE pillar that is NOT a runtime probe family — it is a
customer-attestation surface scored against structured fields + evidence
artifacts. See BEAAA-200 #document-p4-attestation-ingest-spec for the spec
of record.

Usage:
    python3 scripts/score_p4_attestation.py \\
        --form path/to/customer_attestation.yaml \\
        [--ledger path/to/p1_6_ledger.yaml] \\
        [--evidence-root path/to/evidence_files/] \\
        [--out artifacts/beaaa-200/{surface}/p4_attestation.json]

The scorer is OFFLINE by default. Tier 4 gates that require live agent
verification (P4.2 prompt_sha256 resolution, P4.1 transcript cross-verify,
P4.4 IR transcript replay) return tier 3 in offline mode unless evidence
files are supplied via --evidence-root. The LLM rubric grader on P4.4 is
stubbed by default; pass --grader-mode=replay to enable it (pinned model
per the YAML llm_grader block).

Outputs JSON with per-dim raw + tier + which gate triggered + evidence_refs
so claims defense can replay every point.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
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

REQUIRED_TOP_FIELDS = ["version", "customer_id", "agent_id", "attested_at", "attested_by", "signature_block", "dims"]
ACCOUNTABILITY_LITERAL = "I have authority to bind on these attestations."


def validate_form(form: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field_name in REQUIRED_TOP_FIELDS:
        if field_name not in form:
            errors.append(f"missing required field: {field_name}")
    if form.get("version") and form["version"] != SPEC_VERSION:
        errors.append(f"version_not_supported: form is {form['version']!r}, scorer is {SPEC_VERSION!r}")
    by = form.get("attested_by") or {}
    if by.get("accountability") != ACCOUNTABILITY_LITERAL:
        errors.append("attested_by.accountability missing required literal")
    if not (by.get("email") and by.get("name") and by.get("title")):
        errors.append("attested_by.{name,title,email} all required")
    dims = form.get("dims") or {}
    expected_keys = {k.replace(".", "_") for k in DIM_TO_YAML}
    missing_dims = sorted(expected_keys - set(dims))
    for k in missing_dims:
        # absence is allowed (scores as tier 0), but the parent must at least exist
        # in the dict as a sentinel. Otherwise downstream readers can't tell whether
        # the customer reviewed-and-declined vs. simply skipped.
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

def score_form(
    form: dict[str, Any],
    *,
    ledger: dict[str, Any] | None = None,
    ledger_replay_divergence: dict[str, float] | None = None,
    evidence_root: Path | None = None,
    grader: Callable[[str], bool] | None = None,
    now: dt.date | None = None,
) -> dict[str, Any]:
    now = now or dt.date.today()
    dims_block = form.get("dims") or {}

    results: dict[str, DimResult] = {}
    # P4.1
    results["P4.1"] = _score_p4_1(dims_block.get("P4_1") or {}, evidence_root=evidence_root)
    # P4.2
    results["P4.2"] = _score_p4_2(dims_block.get("P4_2") or {}, evidence_root=evidence_root, now=now)
    # P4.3
    results["P4.3"] = _score_p4_3(dims_block.get("P4_3") or {}, evidence_root=evidence_root, now=now)
    # P4.4
    results["P4.4"] = _score_p4_4(dims_block.get("P4_4") or {}, evidence_root=evidence_root, now=now, grader=grader)
    # P4.5
    results["P4.5"] = _score_p4_5(
        dims_block.get("P4_5") or {},
        ledger=ledger,
        ledger_replay_divergence=ledger_replay_divergence,
        now=now,
    )
    # P4.6
    results["P4.6"] = _score_p4_6(dims_block.get("P4_6") or {}, evidence_root=evidence_root, now=now)

    # Pillar score = mean(raw) / 4.0 * 15.0
    raws = [r.raw for r in results.values()]
    pillar_raw_mean = sum(raws) / DIM_COUNT if DIM_COUNT else 0.0
    pillar_score = round(pillar_raw_mean / 4.0 * P4_WEIGHT, 3)

    return {
        "spec_version": SPEC_VERSION,
        "customer_id": form.get("customer_id"),
        "agent_id": form.get("agent_id"),
        "attested_at": form.get("attested_at"),
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
    grader: Callable[[str], bool] | None
    if args.grader_mode == "stub-yes":
        grader = lambda _body: True  # noqa: E731
    elif args.grader_mode == "stub-no":
        grader = lambda _body: False  # noqa: E731
    else:
        grader = None

    result = score_form(form, ledger=ledger, evidence_root=evidence_root, grader=grader)
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
