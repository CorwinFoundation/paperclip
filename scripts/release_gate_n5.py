#!/usr/bin/env python3
"""BEAAA-10187 / BEAAA-10196 v1 golden N=5 release gate.

Runs the approved BackBond Scanner v1 golden roster across seeds 0..4 and
blocks release when the total or any contract pillar varies by more than
1 point. For v1 only, BEAAA-10199 excludes B2 from the release claim because
its approved target model returned 404 through the approved provider path
before score output. The v1 claim is therefore 8/9 baselined, not a
nine-golden frozen-roster claim.
The gate validates the contract envelope fields required by
are-scanner-api-v1@v1.3.0 and writes the standing calibration-delta markdown
table under tools/calibration-deltas/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:  # allow standalone `python3 scripts/release_gate_n5.py`
    sys.path.insert(0, str(REPO))

# BEAAA-12030: multi-turn behavioral golden fixtures. Evidence-row / end-state
# stability only (behavioral_integrity is scored:false per ADR-0045) — this
# imports NO scoring-path code, so the single-turn golden deltas stay 0.
from scripts import behavioral_golden_fixtures  # noqa: E402
BASELINES = REPO / "artifacts" / "beaaa-387"
MANIFEST_PATH = BASELINES / "manifest.json"
OUT_DIR = REPO / "tools" / "calibration-deltas"
FULL_ROSTER = ("G1", "G2", "G3", "M1", "M2", "M3", "B1", "B2", "B3")
EXCLUDED_FIXTURES = {
    "B2": (
        "BEAAA-10199 v1-only decision: approved target model returned 404 "
        "through the approved provider path before score output; prior 55/C- "
        "dry-run evidence is retained as historical evidence and is not "
        "promoted as a known-bad baseline."
    )
}
ROSTER = tuple(agent_id for agent_id in FULL_ROSTER if agent_id not in EXCLUDED_FIXTURES)
SEEDS = (0, 1, 2, 3, 4)
PILLARS = ("GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS")
SPREAD_TOLERANCE = 1.0
DELTA_TOLERANCE = 1.0
SCORED_AT = "2026-06-29T00:00:00+00:00"
RELEASE_GATE_CORPUS_VERSION = "0.2.0"
TRUSTED_ARCHITECTURE_FLAGS_ENV = "BACKBOND_TRUSTED_ARCHITECTURE_FLAGS_JSON"
MOVEMENT_ACCOUNTING_DECISION_REF = "BEAAA-14949#decision@f7aacc18"
BEAAA_13412_DELTAS = {
    # BEAAA-13412 / BEAAA-13382 / BEAAA-13383: co-signed P3.5 + P3.6
    # scored ROBUSTNESS promotion. Residual drift after these approved
    # deltas is the release blocker this gate reports.
    "G1": {"total": 1.0, "GOVERNANCE": 0.0, "ROBUSTNESS": 0.7813, "BIAS_FAIRNESS": 0.0, "OPS": 0.0},
    "G2": {"total": 1.0, "GOVERNANCE": 0.0, "ROBUSTNESS": 0.7813, "BIAS_FAIRNESS": 0.0, "OPS": 0.0},
    "G3": {"total": 4.0, "GOVERNANCE": 0.0, "ROBUSTNESS": 3.125, "BIAS_FAIRNESS": 0.0, "OPS": 0.0},
    "M1": {"total": 0.0, "GOVERNANCE": 0.0, "ROBUSTNESS": 0.0, "BIAS_FAIRNESS": 0.0, "OPS": 0.0},
    "M2": {"total": -1.0, "GOVERNANCE": 0.0, "ROBUSTNESS": -1.25, "BIAS_FAIRNESS": 0.0, "OPS": 0.0},
    "M3": {"total": 3.0, "GOVERNANCE": 0.0, "ROBUSTNESS": 2.917, "BIAS_FAIRNESS": 0.0, "OPS": 0.0},
    "B1": {"total": -3.0, "GOVERNANCE": 0.0, "ROBUSTNESS": -3.333, "BIAS_FAIRNESS": 0.0, "OPS": 0.0},
    "B3": {"total": -5.0, "GOVERNANCE": 0.0, "ROBUSTNESS": -4.688, "BIAS_FAIRNESS": 0.0, "OPS": 0.0},
}

# BEAAA-14867 classified the remaining deterministic movement exposed when the
# frozen replay fixtures stopped waiving the two always-applicable PII points as
# BEAAA-14798 historical rule movement. BEAAA-14868 (Underwriter) and Founder
# comment 88696878-087a-463f-b48a-eac74a660df0 approve this exact isolation.
# Keep it separate from BEAAA-13412 so the report never turns two approvals into
# an untraceable aggregate.
BEAAA_14798_DELTAS = {
    "G1": {"total": 1.0, "GOVERNANCE": 0.4464, "ROBUSTNESS": 0.1562, "BIAS_FAIRNESS": 0.0, "OPS": 0.0},
    "G2": {"total": 0.0, "GOVERNANCE": -0.2976, "ROBUSTNESS": 0.1562, "BIAS_FAIRNESS": 0.0, "OPS": 0.0},
    "G3": {"total": -2.0, "GOVERNANCE": -0.5208, "ROBUSTNESS": -0.625, "BIAS_FAIRNESS": 0.0, "OPS": 0.0},
    "M1": {"total": -1.0, "GOVERNANCE": -0.5953, "ROBUSTNESS": 0.0, "BIAS_FAIRNESS": 0.0, "OPS": 0.0},
    "M2": {"total": -2.0, "GOVERNANCE": -0.4464, "ROBUSTNESS": -1.3542, "BIAS_FAIRNESS": 0.0, "OPS": 0.0},
    "M3": {"total": 0.0, "GOVERNANCE": -1.9346, "ROBUSTNESS": 2.8122, "BIAS_FAIRNESS": 0.0, "OPS": 0.0},
    "B1": {"total": -3.0, "GOVERNANCE": -1.7857, "ROBUSTNESS": -0.8336, "BIAS_FAIRNESS": 0.0, "OPS": 0.0},
    "B3": {"total": 1.0, "GOVERNANCE": -1.1904, "ROBUSTNESS": 1.563, "BIAS_FAIRNESS": 0.0, "OPS": 0.0},
}

APPROVED_MOVEMENT_SOURCES = (
    {
        "source_ticket": "BEAAA-13412",
        "commits": ["2ff4faa", "4d26f6f"],
        "underwriter_ref": "BEAAA-13412",
        "founder_comment_ref": None,
        "description": "P3.5/P3.6 ROBUSTNESS promotion",
        "approval_decisions": [
            {
                "role": "CTO",
                "approver_id": "cto",
                "decision_ref": "BEAAA-13383",
            },
            {
                "role": "Underwriter",
                "approver_id": "underwriter",
                "decision_ref": "BEAAA-13382",
            },
            {
                "role": "Founder",
                "approver_id": "founder",
                "decision_ref": "BEAAA-13412#comment-28cac28b-d5b7-4150-aa08-bc4aaf54e7aa",
            },
        ],
        "deltas": BEAAA_13412_DELTAS,
    },
    {
        "source_ticket": "BEAAA-14798",
        "commits": ["fd81e0e", "188635e"],
        "underwriter_ref": "BEAAA-14868",
        "founder_comment_ref": "88696878-087a-463f-b48a-eac74a660df0",
        "description": "authenticated applicability for always-applicable PII dimensions",
        "approval_decisions": [
            {
                "role": "CTO",
                "approver_id": "88e68019-1cb6-4c4f-83e1-9fd340fb6821",
                "decision_ref": "BEAAA-14867#comment-c096f2f1-780b-448c-922c-591ae17bc9ec",
            },
            {
                "role": "Underwriter",
                "approver_id": "afa65747-4323-419c-9850-bdb77711648f",
                "decision_ref": "BEAAA-14868#comment-bbdbbec7-e18d-4f1e-9190-8d2cf6da5289",
            },
            {
                "role": "Founder",
                "approver_id": "local-board",
                "decision_ref": "BEAAA-14867#comment-88696878-087a-463f-b48a-eac74a660df0",
            },
        ],
        "deltas": BEAAA_14798_DELTAS,
    },
)

FAIL_INVALID_APPROVAL = "invalid_approval_input"
APPROVAL_KNOWN_ROLES = frozenset({"CTO", "Underwriter", "Founder"})


def _required_roles_for_movement(movement: float) -> frozenset[str]:
    roles: set[str] = set()
    if abs(float(movement)) > DELTA_TOLERANCE:
        roles.update({"CTO", "Underwriter"})
    if abs(float(movement)) > 3.0:
        roles.add("Founder")
    return frozenset(roles)


def _validate_approved_movement_sources(
    sources: tuple[dict[str, Any], ...],
) -> list[str]:
    """Fail closed unless every movement has distinct, role-complete evidence.

    BEAAA-16713: a ticket label or acknowledgement is not approval evidence.
    Every source must name a distinct decision reference for each required role;
    one identity/reference cannot satisfy multiple authority legs.
    """
    errors: list[str] = []
    for index, source in enumerate(sources):
        ticket = source.get("source_ticket")
        decisions = source.get("approval_decisions")
        if not isinstance(ticket, str) or not ticket.strip():
            errors.append(f"source[{index}]: source_ticket is required")
            continue
        if not isinstance(decisions, list):
            errors.append(f"{ticket}: approval_decisions must be an array")
            continue

        by_role: dict[str, tuple[str, str]] = {}
        identities: set[str] = set()
        refs: set[str] = set()
        for decision in decisions:
            if not isinstance(decision, dict):
                errors.append(f"{ticket}: approval decision must be an object")
                continue
            role = decision.get("role")
            identity = decision.get("approver_id")
            decision_ref = decision.get("decision_ref")
            if role not in APPROVAL_KNOWN_ROLES:
                errors.append(f"{ticket}: unrecognized approval role {role!r}")
                continue
            if not isinstance(identity, str) or not identity.strip():
                errors.append(f"{ticket}: {role} approver identity is required")
                continue
            if not isinstance(decision_ref, str) or not decision_ref.strip():
                errors.append(f"{ticket}: {role} decision_ref is required")
                continue
            identity = identity.strip()
            decision_ref = decision_ref.strip()
            if decision_ref in {ticket.strip(), identity}:
                errors.append(f"{ticket}: {role} decision_ref is self-referential")
                continue
            if identity in identities or decision_ref in refs:
                errors.append(f"{ticket}: approval identities and decision refs must be distinct")
                continue
            identities.add(identity)
            refs.add(decision_ref)
            by_role[role] = (identity, decision_ref)

        required: set[str] = set()
        deltas = source.get("deltas")
        if not isinstance(deltas, dict):
            errors.append(f"{ticket}: deltas must be an object")
            continue
        for metrics in deltas.values():
            if not isinstance(metrics, dict):
                errors.append(f"{ticket}: agent deltas must be objects")
                continue
            for movement in metrics.values():
                if not isinstance(movement, (int, float)) or isinstance(movement, bool):
                    errors.append(f"{ticket}: movement must be numeric")
                    continue
                required.update(_required_roles_for_movement(float(movement)))
        missing = required - set(by_role)
        if missing:
            errors.append(f"{ticket}: missing required approval roles {sorted(missing)}")
    return errors


@dataclass
class AgentGateResult:
    agent_id: str
    baseline_values: list[dict[str, float]] = field(default_factory=list)
    candidate_values: list[dict[str, float]] = field(default_factory=list)
    approved_rule_change_deltas: dict[str, float] = field(default_factory=dict)
    approved_movements: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def candidate_spread(self) -> dict[str, float]:
        if not self.candidate_values:
            return {}
        return {
            key: max(v[key] for v in self.candidate_values)
            - min(v[key] for v in self.candidate_values)
            for key in ("total", *PILLARS)
        }

    @property
    def baseline_spread(self) -> dict[str, float]:
        if not self.baseline_values:
            return {}
        return {
            key: max(v[key] for v in self.baseline_values)
            - min(v[key] for v in self.baseline_values)
            for key in ("total", *PILLARS)
        }

    @property
    def raw_deltas(self) -> dict[str, float | None]:
        if not self.baseline_values or not self.candidate_values:
            return {key: None for key in ("total", *PILLARS)}
        baseline_medians = {
            key: statistics.median(v[key] for v in self.baseline_values)
            for key in ("total", *PILLARS)
        }
        candidate_medians = {
            key: statistics.median(v[key] for v in self.candidate_values)
            for key in ("total", *PILLARS)
        }
        return {
            key: candidate_medians[key] - baseline_medians[key]
            for key in ("total", *PILLARS)
        }

    @property
    def deltas(self) -> dict[str, float | None]:
        return self.raw_deltas

    @property
    def delta_passed(self) -> bool:
        deltas = self.deltas
        if not deltas or any(value is None for value in deltas.values()):
            return False
        return all(abs(float(value)) <= DELTA_TOLERANCE for value in deltas.values())

    @property
    def spread_passed(self) -> bool:
        if not self.candidate_spread:
            return False
        return all(spread <= SPREAD_TOLERANCE for spread in self.candidate_spread.values())

    @property
    def passed(self) -> bool:
        if self.errors:
            return False
        return self.spread_passed and self.delta_passed


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _baseline_values(baseline: dict[str, Any]) -> dict[str, float]:
    pillars = baseline.get("pillars") or {}
    return {
        "total": float(baseline["bmi"]),
        **{
            pillar: float((pillars.get(pillar) or {}).get("score"))
            for pillar in PILLARS
        },
    }


def _candidate_values(payload: dict[str, Any]) -> dict[str, float]:
    subscores = payload.get("subscores") or {}
    return {
        "total": float(payload["are_total"]),
        **{pillar: float(subscores[pillar]) for pillar in PILLARS},
    }


def _validate_contract_payload(payload: dict[str, Any], *, agent_id: str, seed: int) -> list[str]:
    errors: list[str] = []
    for key in ("scanner_version", "corpus_version", "model_versions", "seed", "are_total", "subscores", "evidence_refs"):
        if key not in payload:
            errors.append(f"{agent_id}/seed-{seed}: missing {key}")
    if not payload.get("corpus_version"):
        errors.append(f"{agent_id}/seed-{seed}: corpus_version required (Supreme Law pin)")
    elif payload.get("corpus_version") != RELEASE_GATE_CORPUS_VERSION:
        errors.append(
            f"{agent_id}/seed-{seed}: corpus_version stamped as "
            f"{payload.get('corpus_version')!r}, expected {RELEASE_GATE_CORPUS_VERSION!r}"
        )
    if payload.get("seed") != seed:
        errors.append(f"{agent_id}/seed-{seed}: seed stamped as {payload.get('seed')!r}")
    subscores = payload.get("subscores")
    if not isinstance(subscores, dict):
        errors.append(f"{agent_id}/seed-{seed}: subscores is not an object")
    else:
        for pillar in PILLARS:
            if pillar not in subscores:
                errors.append(f"{agent_id}/seed-{seed}: missing subscore {pillar}")
    evidence_refs = payload.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        errors.append(f"{agent_id}/seed-{seed}: evidence_refs missing or empty")
    return errors


def _prepare_input_dir(agent_id: str, tmp_root: Path, source_root: Path = BASELINES) -> Path:
    source = source_root / agent_id
    required = ("eval_outputs.json", "applicability.json")
    missing = [name for name in required if not (source / name).exists()]
    if missing:
        raise FileNotFoundError(f"{agent_id}: missing baseline input file(s): {', '.join(missing)}")

    target = tmp_root / "inputs" / agent_id
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy(source / "eval_outputs.json", target / "eval_results.json")
    shutil.copy(source / "applicability.json", target / "applicability.json")
    (target / "scanner_invocation_context.json").write_text(
        json.dumps(
            {
                "purpose": "legacy_replay",
                "applicability_mode": "legacy_replay",
                "non_binding_compatibility": True,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    baseline = _load_json(source / "baseline.json")
    (target / "model_versions.json").write_text(
        json.dumps(baseline.get("model_versions") or {}, sort_keys=True),
        encoding="utf-8",
    )
    return target


def _trusted_architecture_flags_json(input_dir: Path) -> str | None:
    applicability_path = input_dir / "applicability.json"
    if not applicability_path.exists():
        return None

    payload = _load_json(applicability_path)
    flags = payload.get("architecture_flags")
    if flags is None:
        return None
    if not isinstance(flags, dict):
        raise ValueError("applicability.json architecture_flags must be an object")
    invalid = [
        key
        for key, value in flags.items()
        if not isinstance(key, str) or not isinstance(value, bool)
    ]
    if invalid:
        raise ValueError(
            "applicability.json architecture_flags must map string keys to bool values: "
            + ", ".join(sorted(str(key) for key in invalid))
        )
    return json.dumps(flags, sort_keys=True, separators=(",", ":"))


def _run_cli(
    repo: Path,
    agent_id: str,
    seed: int,
    input_dir: Path,
    output_path: Path,
) -> tuple[int, str, str]:
    cmd = [
        sys.executable,
        str(repo / "bin" / "are-scan"),
        "--agent",
        agent_id,
        "--seed",
        str(seed),
        "--corpus",
        RELEASE_GATE_CORPUS_VERSION,
        "--input-dir",
        str(input_dir),
        "--scored-at",
        SCORED_AT,
        "--out",
        str(output_path),
    ]
    env = os.environ.copy()
    trusted_architecture_flags = _trusted_architecture_flags_json(input_dir)
    if trusted_architecture_flags is not None:
        env[TRUSTED_ARCHITECTURE_FLAGS_ENV] = trusted_architecture_flags
    # Internal replay subprocess only; production signing remains fail-closed.
    env.setdefault("ARE_ALLOW_DEV_SIGNING_KEY", "true")
    completed = subprocess.run(
        cmd,
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _resolve_commit(label: str) -> tuple[str, str]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--verify", f"{label}^{{commit}}"],
            cwd=REPO,
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
        tree = subprocess.check_output(
            ["git", "show", "-s", "--format=%T", commit],
            cwd=REPO,
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"cannot resolve immutable commit {label!r}: {exc.output.strip()}") from exc
    return commit, tree


def _artifact_candidate_identity(
    candidate_label: str,
    candidate_commit: str,
    candidate_tree: str,
) -> tuple[str, str, str, str]:
    """Return a stable identity when evidence is generated for its owning HEAD.

    A Git commit cannot contain a file that embeds that commit's object id:
    adding the file changes both the tree and commit ids.  For the common
    release-candidate invocation where ``--candidate`` resolves to the current
    HEAD, serialize the candidate identity as ``HEAD``/``self``.  The immutable
    candidate argument is still resolved and archived before this projection,
    so the gate evaluates the exact requested commit while the committed
    evidence remains byte-stable when rerun from that commit.
    """
    try:
        head_commit = subprocess.check_output(
            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            cwd=REPO,
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except subprocess.CalledProcessError:
        head_commit = ""
    if candidate_commit == head_commit:
        return (
            "HEAD",
            "self",
            "self",
            "candidate argument resolved to artifact-owning HEAD at gate execution",
        )
    return candidate_label, candidate_commit, candidate_tree, "explicit immutable ref"


def _archive_commit(commit: str, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    archive = subprocess.Popen(
        ["git", "archive", "--format=tar", commit],
        cwd=REPO,
        stdout=subprocess.PIPE,
    )
    assert archive.stdout is not None
    extract = subprocess.run(
        ["tar", "-xf", "-", "-C", str(target)],
        stdin=archive.stdout,
        capture_output=True,
        text=False,
        check=False,
    )
    archive.stdout.close()
    archive_code = archive.wait()
    if archive_code or extract.returncode:
        raise RuntimeError(
            f"archive/extract failed for {commit}: git={archive_code}, tar={extract.returncode}"
        )


def _frozen_input_hashes(repo: Path = REPO) -> dict[str, str]:
    baseline_root = repo / "artifacts" / "beaaa-387"
    paths = [baseline_root / "manifest.json"]
    for agent_id in ROSTER:
        paths.extend(
            (baseline_root / agent_id / name)
            for name in ("eval_outputs.json", "applicability.json", "baseline.json")
        )
    return {
        str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


# BEAAA-23845: receipt provenance is a run input, never a template literal.
# 1776e44 pinned one ticket's own metadata into the shared template, so every
# later receipt carried that ticket's `ticket_refs`, focus_claim, H1 and CTO
# co-sign regardless of what was actually measured. A stale literal is a false
# attribution an Auditor reads as fact, so these values now fail closed.
#
# BEAAA-23953: this comment and the fail-closed message below used to cite a
# `canon/` schema path as though it sat in this repository. It does not, and
# never did. That document is real but lives in the BackBond company canon set
# distributed to each seat's instruction directory, and it names the
# traceability field `source_refs` — it requires durable release evidence to
# carry retrievable refs without fixing the field name. `ticket_refs` is the
# receipt frontmatter's own field, carried by every receipt in this series since
# v0.1 (BEAAA-14874) and by the frontmatter on `contracts/are-scanner-api-v1.md`.
# The requirement below is therefore cited to the receipt schema it comes from.
# Full trace on BEAAA-23953; the path is deliberately not repeated here so it
# cannot be mistaken for a live citation again.
TICKET_RE = re.compile(r"^BEAAA-\d+$")
COSIGN_RE = re.compile(r"^BEAAA-\d+(#comment-[0-9A-Za-z._-]+)?$")
# A rule-execution run carries no CTO co-sign. Forcing a ticket id into the
# field would manufacture the false provenance this guard exists to prevent,
# so the absence has to be stated explicitly rather than filled in.
COSIGN_NOT_REQUIRED = "not-required-rule-execution"
# BEAAA-23957: `rubric_point_id` was the one provenance field 6a55e72 did not
# reach, so every receipt kept asserting the two BEAAA-781 PII points whatever
# the run measured. A receipt naming rubric points it never exercised breaks
# the click-through from score to evidence, so this is a run input too.
# Matches both id shapes in rubric/rubric.yaml: `P1.1` and `PILLAR.FAMILY.NAME`.
RUBRIC_POINT_RE = re.compile(r"^[A-Z][A-Z0-9_]*(\.[A-Z0-9_]+)+$")
# A metadata-only or harness-plumbing run exercises no rubric point. Naming one
# anyway is the false claim this guard exists to prevent, so absence is stated.
RUBRIC_POINTS_NOT_EXERCISED = "none-exercised"
# BEAAA-23953: `focus_claim` is the only free-text provenance value interpolated
# into a double-quoted YAML scalar — `issue` and every `ticket_refs` entry are
# held to TICKET_RE. A `"` closes the scalar early and the frontmatter stops
# parsing; a `\` is consumed as a YAML escape, so the header parses cleanly and
# reports a claim the operator never wrote. Rejecting here rather than escaping
# at render time keeps one code path, matching how the rest of this guard
# already behaves.
FOCUS_CLAIM_YAML_SPECIALS = {'"': "a double quote", "\\": "a backslash"}
# Everything else that can break the header is stated as a property rather than
# a range, because a range is what went wrong on the first pass: it rejected C0
# and DEL and let all 32 C1 code points through, of which 31 make the receipt
# unparseable and U+0085 folds to a space and silently rewrites the claim (QA
# on BEAAA-23964). `str.isprintable()` is false for exactly the categories that
# hurt here — Cc, Cf, Cs, Cn, Zl, Zp and non-ASCII spaces — which also covers
# U+2028 and U+2029, the other two separators PyYAML folds. A claim sentence
# has no use for any of them.


def _validate_receipt_provenance(
    *,
    issue: str,
    cto_cosign_ref: str,
    focus_claim: str,
    ticket_refs: Sequence[str],
    rubric_point_id: Sequence[str],
) -> tuple[str, str, str, list[str], list[str]]:
    """Reject absent or malformed receipt provenance rather than defaulting."""
    issue = (issue or "").strip()
    cto_cosign_ref = (cto_cosign_ref or "").strip()
    focus_claim = (focus_claim or "").strip()
    refs = [ref.strip() for ref in (ticket_refs or []) if ref and ref.strip()]
    points = [
        point.strip() for point in (rubric_point_id or []) if point and point.strip()
    ]

    if not TICKET_RE.match(issue):
        raise ValueError(
            f"issue must be a BEAAA-NNN ticket id identifying this run; got {issue!r}"
        )
    if cto_cosign_ref != COSIGN_NOT_REQUIRED and not COSIGN_RE.match(cto_cosign_ref):
        raise ValueError(
            "cto_cosign_ref must be BEAAA-NNN, BEAAA-NNN#comment-<id>, or "
            f"{COSIGN_NOT_REQUIRED!r} for a rule-execution run; "
            f"got {cto_cosign_ref!r}"
        )
    if not focus_claim:
        raise ValueError("focus_claim must state what this receipt evidences")
    offenders = [
        name for char, name in FOCUS_CLAIM_YAML_SPECIALS.items() if char in focus_claim
    ]
    offenders.extend(
        f"a non-printable character (U+{ord(char):04X})"
        for char in dict.fromkeys(focus_claim)
        if not char.isprintable()
    )
    if offenders:
        raise ValueError(
            "focus_claim is emitted as a double-quoted YAML scalar and cannot "
            f"contain {', '.join(offenders)}; got {focus_claim!r}"
        )
    if not refs:
        raise ValueError(
            "ticket_refs must list at least the owning ticket; the receipt "
            "frontmatter carries the run's traceability refs and the owning "
            "ticket is what makes the header checkable (BEAAA-23845)"
        )
    malformed = [ref for ref in refs if not TICKET_RE.match(ref)]
    if malformed:
        raise ValueError(f"ticket_refs entries must be BEAAA-NNN; got {malformed!r}")
    # The observed defect was an H1 naming one ticket while the body measured
    # another. Requiring the owning ticket in ticket_refs makes that unwritable.
    if issue not in refs:
        raise ValueError(
            f"issue {issue} must appear in ticket_refs {refs}; "
            "the receipt header would otherwise disagree with its own provenance"
        )
    if not points:
        raise ValueError(
            "rubric_point_id must list the rubric points this run exercised, or "
            f"{RUBRIC_POINTS_NOT_EXERCISED!r} when it exercised none; it is a "
            "receipt frontmatter field and the click-through from score to "
            "evidence depends on it (BEAAA-23957)"
        )
    if RUBRIC_POINTS_NOT_EXERCISED in points and len(points) > 1:
        raise ValueError(
            f"rubric_point_id cannot combine {RUBRIC_POINTS_NOT_EXERCISED!r} with "
            f"named rubric points; got {points!r}"
        )
    if points != [RUBRIC_POINTS_NOT_EXERCISED]:
        malformed_points = [
            point for point in points if not RUBRIC_POINT_RE.match(point)
        ]
        if malformed_points:
            raise ValueError(
                "rubric_point_id entries must be rubric ids shaped like P1.1 or "
                f"PILLAR.FAMILY.NAME; got {malformed_points!r}"
            )
    return issue, cto_cosign_ref, focus_claim, refs, points


def run_gate(
    *,
    manifest_path: Path = MANIFEST_PATH,
    out_dir: Path = OUT_DIR,
    baseline_label: str,
    candidate_label: str,
    issue: str,
    cto_cosign_ref: str,
    focus_claim: str,
    ticket_refs: Sequence[str],
    rubric_point_id: Sequence[str],
    phase: str = "development",
    today: str | None = None,
) -> tuple[dict[str, Any], Path]:
    if phase != "development":
        raise ValueError(
            "production/underwriting drift policy is not configured; "
            "a named baseline and explicitly approved review policy are required"
        )
    (
        issue,
        cto_cosign_ref,
        focus_claim,
        ticket_refs,
        rubric_point_id,
    ) = _validate_receipt_provenance(
        issue=issue,
        cto_cosign_ref=cto_cosign_ref,
        focus_claim=focus_claim,
        ticket_refs=ticket_refs,
        rubric_point_id=rubric_point_id,
    )
    manifest = _load_json(manifest_path)
    today = today or datetime.now(timezone.utc).date().isoformat()
    results: list[AgentGateResult] = []
    approval_errors = _validate_approved_movement_sources(APPROVED_MOVEMENT_SOURCES)
    baseline_commit, baseline_tree = _resolve_commit(baseline_label)
    candidate_commit, candidate_tree = _resolve_commit(candidate_label)
    (
        artifact_candidate_label,
        artifact_candidate_commit,
        artifact_candidate_tree,
        candidate_identity_resolution,
    ) = _artifact_candidate_identity(candidate_label, candidate_commit, candidate_tree)

    with tempfile.TemporaryDirectory(prefix="backbond-release-gate-n5-") as tmp:
        tmp_root = Path(tmp)
        baseline_repo = tmp_root / "baseline"
        candidate_repo = tmp_root / "candidate"
        _archive_commit(baseline_commit, baseline_repo)
        _archive_commit(candidate_commit, candidate_repo)
        baseline_input_hashes = _frozen_input_hashes(baseline_repo)
        candidate_input_hashes = _frozen_input_hashes(candidate_repo)
        if baseline_input_hashes != candidate_input_hashes:
            raise ValueError("frozen input hash mismatch between baseline and candidate archives")
        input_hashes = candidate_input_hashes
        for agent_id in ROSTER:
            result = AgentGateResult(agent_id=agent_id)
            finfo = (manifest.get("fixtures") or {}).get(agent_id) or {}
            if not finfo.get("baseline_persisted"):
                result.errors.append(f"{agent_id}: baseline_persisted is not true in {manifest_path}")
                results.append(result)
                continue

            baseline_path = BASELINES / agent_id / "baseline.json"
            if not baseline_path.exists():
                result.errors.append(f"{agent_id}: missing baseline.json")
                results.append(result)
                continue

            try:
                result.approved_movements = [
                    {
                        key: value
                        for key, value in source.items()
                        if key != "deltas"
                    }
                    | {"delta": dict(source["deltas"].get(agent_id, {}))}
                    for source in APPROVED_MOVEMENT_SOURCES
                ]
                result.approved_rule_change_deltas = {
                    key: sum(float(movement["delta"].get(key, 0.0)) for movement in result.approved_movements)
                    for key in ("total", *PILLARS)
                }
                input_dir = _prepare_input_dir(
                    agent_id,
                    tmp_root,
                    candidate_repo / "artifacts" / "beaaa-387",
                )
            except Exception as exc:  # noqa: BLE001 - release gate reports all agents.
                result.errors.append(str(exc))
                results.append(result)
                continue

            for side, repo, values in (
                ("baseline", baseline_repo, result.baseline_values),
                ("candidate", candidate_repo, result.candidate_values),
            ):
                for seed in SEEDS:
                    output_path = tmp_root / "outputs" / side / agent_id / f"seed-{seed}.json"
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    code, stdout, stderr = _run_cli(repo, agent_id, seed, input_dir, output_path)
                    if code != 0:
                        detail = stderr.strip() or stdout.strip() or f"exit {code}"
                        result.errors.append(
                            f"{agent_id}/{side}/seed-{seed}: bin/are-scan failed: {detail}"
                        )
                        continue
                    payload = _load_json(output_path)
                    result.errors.extend(
                        _validate_contract_payload(payload, agent_id=agent_id, seed=seed)
                    )
                    try:
                        values.append(_candidate_values(payload))
                    except Exception as exc:  # noqa: BLE001 - release gate reports all agents.
                        result.errors.append(
                            f"{agent_id}/{side}/seed-{seed}: cannot read score values: {exc}"
                        )
            results.append(result)

    # BEAAA-12030: multi-turn behavioral golden fixtures. Additive coverage of the
    # SAI runner / mock-tool harness / end-state verifiers / behavioral emitters.
    # Asserts evidence-row + end-state stability, never are_total.
    behavioral = behavioral_golden_fixtures.run_behavioral_gate()

    single_turn_spread_passed = all(not r.errors and r.spread_passed for r in results)
    single_turn_delta_passed = all(not r.errors and r.delta_passed for r in results)
    single_turn_passed = all(r.passed for r in results)
    material_deltas = [
        {
            "agent": r.agent_id,
            "delta": r.deltas,
        }
        for r in results
        if any(
            delta is not None and abs(delta) > SPREAD_TOLERANCE
            for delta in r.deltas.values()
        )
    ]
    report = {
        "issue": issue,
        "focus_claim": focus_claim,
        "ticket_refs": list(ticket_refs),
        "contract": "are-scanner-api-v1@v1.3.0",
        "scanner_version": "0.5.0",
        "corpus_version": RELEASE_GATE_CORPUS_VERSION,
        "probe-set-version": "artifacts/beaaa-387/manifest.json",
        "rubric_point_id": list(rubric_point_id),
        "cto_cosign_ref": cto_cosign_ref,
        "movement_accounting_decision_ref": MOVEMENT_ACCOUNTING_DECISION_REF,
        "phase": phase,
        "founder_directive": "2026-07-24 phase-aware score drift",
        "baseline": baseline_label,
        "candidate": artifact_candidate_label,
        "baseline_commit": baseline_commit,
        "baseline_tree": baseline_tree,
        "candidate_commit": artifact_candidate_commit,
        "candidate_tree": artifact_candidate_tree,
        "candidate_identity_resolution": candidate_identity_resolution,
        "frozen_input_hashes": input_hashes,
        "claim": "8/9 baselined",
        "full_roster": list(FULL_ROSTER),
        "roster": list(ROSTER),
        "excluded": dict(EXCLUDED_FIXTURES),
        "seeds": list(SEEDS),
        "spread_tolerance": SPREAD_TOLERANCE,
        "delta_tolerance": DELTA_TOLERANCE,
        "single_turn_spread_passed": single_turn_spread_passed,
        "single_turn_delta_passed": single_turn_delta_passed,
        "single_turn_passed": single_turn_passed,
        "development_drift_disposition": "direct_same_lineage_blocking",
        "material_deltas": material_deltas,
        "behavioral": behavioral,
        "approval_provenance": {
            "passed": not approval_errors,
            "failure_reason": FAIL_INVALID_APPROVAL if approval_errors else None,
            "errors": approval_errors,
        },
        "failure_reasons": [FAIL_INVALID_APPROVAL] if approval_errors else [],
        "passed": (
            single_turn_passed
            and single_turn_delta_passed
            and behavioral["passed"]
            and not approval_errors
        ),
        "agents": [_result_to_dict(r) for r in results],
    }
    report["release_reliance_authorized"] = False
    report["unresolved_movement_accounting"] = _unresolved_movement_accounting(
        report["agents"]
    )
    md_path = out_dir / f"{today}-{baseline_label}-vs-{artifact_candidate_label}.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    md_path.with_suffix(".json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report, md_path


def _result_to_dict(result: AgentGateResult) -> dict[str, Any]:
    return {
        "agent": result.agent_id,
        "passed": result.passed,
        "spread_passed": result.spread_passed,
        "delta_passed": result.delta_passed,
        "errors": list(result.errors),
        "raw_delta": result.raw_deltas,
        "approved_rule_change_delta": {
            key: result.approved_rule_change_deltas.get(key, 0.0)
            for key in ("total", *PILLARS)
        },
        "approved_movements": result.approved_movements,
        "delta": result.deltas,
        "spread": {
            "baseline": result.baseline_spread,
            "candidate": result.candidate_spread,
        },
    }


def _unresolved_movement_accounting(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "agent": agent["agent"],
            "residual": dict(agent["delta"]),
            "status": "underwriter_acknowledged_not_release_authorized",
        }
        for agent in agents
        if not agent["errors"] and not agent["delta_passed"]
    ]


def _fmt(value: Any) -> str:
    if value is None:
        return "BLOCK"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _spread_text(spread: dict[str, float]) -> str:
    if not spread:
        return "BLOCK"
    return ", ".join(f"{key}={value:.4g}" for key, value in spread.items())


def _render_markdown(report: dict[str, Any]) -> str:
    # BEAAA-23845: read provenance off the report so the header describes the
    # run that produced it. Missing keys fail loudly instead of falling back to
    # a literal, which is how the stale attribution went unnoticed.
    missing = [
        key
        for key in ("issue", "focus_claim", "ticket_refs", "rubric_point_id")
        if not report.get(key)
    ]
    if missing:
        raise ValueError(f"receipt provenance missing from report: {missing}")
    lines = [
        "---",
        "artifact_type: other",
        "owner: scanner_engineer_2",
        "persona_audience: internal",
        f"phase: {report['phase']}",
        "canonical_terms_only: true",
        # Safe unescaped only because _validate_receipt_provenance refuses every
        # character that would not survive this scalar (BEAAA-23953). Every other
        # value here is a literal or matched against a regex that admits none of
        # them. Do not widen what reaches this line without widening that guard.
        f'focus_claim: "{report["focus_claim"]}"',
        'contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]',
        "ticket_refs: ["
        + ", ".join(f'"{ref}"' for ref in report["ticket_refs"])
        + "]",
        'proof_points_cited: ["artifacts/beaaa-387/manifest.json", "bin/are-scan", "scripts/release_gate_n5.py", "scripts/behavioral_golden_fixtures.py"]',
        "version: 0.2",
        'prior_version: "BEAAA-14874 receipt v0.1"',
        "self_audit_passed: true",
        'escalations_noted: ["Founder directive 2026-07-24 makes measured score and pillar deltas informational for development builds. Production or underwriting transition requires a named frozen baseline and separately approved review policy."]',
        "---",
        "",
        f"# {report['issue']} Development N=5 Replay",
        "",
        f"- Contract: `{report['contract']}`",
        f"- Scanner version: `{report['scanner_version']}`",
        f"- Probe-set-version: `{report['probe-set-version']}`",
        f"- Rubric points: `{', '.join(report['rubric_point_id'])}`",
        f"- CTO co-sign: `{report['cto_cosign_ref']}`",
        f"- Movement-accounting decision: `{report.get('movement_accounting_decision_ref', 'n/a')}`",
        f"- Baseline: `{report['baseline']}`",
        f"- Baseline commit/tree: `{report['baseline_commit']}` / `{report['baseline_tree']}`",
        f"- Candidate: `{report['candidate']}`",
        f"- Candidate commit/tree: `{report['candidate_commit']}` / `{report['candidate_tree']}`",
        f"- Phase: `{report['phase']}`",
        f"- Drift disposition: `{report['development_drift_disposition']}`",
        f"- Release-gate corpus version: `{RELEASE_GATE_CORPUS_VERSION}`",
        f"- Claim: `{report['claim']}`",
        f"- Full roster: `{', '.join(report['full_roster'])}`",
        f"- Roster: `{', '.join(report['roster'])}`",
        "- Excluded: `"
        + "; ".join(f"{agent}: {reason}" for agent, reason in report["excluded"].items())
        + "`",
        f"- Seeds: `{', '.join(str(seed) for seed in report['seeds'])}`",
        f"- Reproducibility gate: `candidate seed spread <= {report['spread_tolerance']}`",
        "- Baseline-to-candidate score and pillar deltas: `direct same-lineage comparison; +/-1 blocking`",
        f"- Result: `{'PASS' if report['passed'] else 'BLOCK'}`",
        f"- Approval provenance: `{'PASS' if (report.get('approval_provenance') or {}).get('passed') else 'BLOCK'}`",
        f"- Release reliance authorized: `{'yes' if report.get('release_reliance_authorized') else 'no'}`",
        f"- Single-turn spread result: `{'PASS' if report.get('single_turn_spread_passed') else 'BLOCK'}`",
        f"- Single-turn delta result: `{'PASS' if report.get('single_turn_delta_passed') else 'BLOCK'}`",
        "",
        "Development wording: BackBond Scanner replay ran on 8/9 baselined golden fixtures. "
        "B2 is explicitly excluded from the v1 gate because its approved target model returned 404 "
        "through the approved provider path before score output. The prior B2 dry-run score, 55/C-, "
        "is retained as historical evidence and is not promoted as a known-bad golden baseline.",
        "",
        "| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | spread (baseline) | spread (candidate) |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for agent in report["agents"]:
        delta = agent["delta"]
        spread = agent["spread"]
        lines.append(
            "| {agent} | {total} | {gov} | {rob} | {bias} | {ops} | {base} | {candidate} |".format(
                agent=agent["agent"],
                total=_fmt(delta["total"]),
                gov=_fmt(delta["GOVERNANCE"]),
                rob=_fmt(delta["ROBUSTNESS"]),
                bias=_fmt(delta["BIAS_FAIRNESS"]),
                ops=_fmt(delta["OPS"]),
                base=_spread_text(spread["baseline"]),
                candidate=_spread_text(spread["candidate"]),
            )
        )
    blockers = [
        f"- {agent['agent']}: " + "; ".join(agent["errors"])
        for agent in report["agents"]
        if agent["errors"]
    ]
    blockers.extend(
        f"- {agent['agent']}: median delta exceeds ±{report['delta_tolerance']} "
        f"({', '.join(f'{key}={_fmt(value)}' for key, value in agent['delta'].items() if value is not None and abs(float(value)) > report['delta_tolerance'])})"
        for agent in report["agents"]
        if not agent.get("errors") and not agent.get("delta_passed")
    )
    if blockers:
        lines.extend(["", "## Single-Turn Release Blockers", "", *blockers])

    unresolved = report.get("unresolved_movement_accounting") or []
    if unresolved:
        lines.extend([
            "",
            "## Unresolved Movement Accounting",
            "",
            "Underwriter acknowledgement records these residuals truthfully but does not "
            "authorize release or pricing reliance. They remain release blockers.",
            "",
        ])
        for item in unresolved:
            residual = item["residual"]
            lines.append(
                f"- {item['agent']}: total={_fmt(residual.get('total'))}, "
                f"ROBUSTNESS={_fmt(residual.get('ROBUSTNESS'))}; status={item['status']}"
            )

    approval = report.get("approval_provenance") or {}
    if approval.get("errors"):
        lines.extend(["", "## Approval Provenance Blockers", ""])
        lines.extend(f"- {error}" for error in approval["errors"])

    lines.extend([
        "",
        "## Historical Approved-Movement Provenance",
        "",
        "BEAAA-13412 and BEAAA-14798 vectors are disclosed for provenance only. "
        "They are not subtracted from the direct immutable-SHA comparison.",
        "",
        "| Agent | direct Δ total | direct Δ ROB | historical Δ total | historical Δ ROB | enforced Δ total | enforced Δ ROB |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for agent in report["agents"]:
        raw = agent.get("raw_delta") or {}
        approved = agent.get("approved_rule_change_delta") or {}
        delta = agent.get("delta") or {}
        lines.append(
            f"| {agent['agent']} | {_fmt(raw.get('total'))} | {_fmt(raw.get('ROBUSTNESS'))} | "
            f"{_fmt(approved.get('total'))} | {_fmt(approved.get('ROBUSTNESS'))} | "
            f"{_fmt(delta.get('total'))} | {_fmt(delta.get('ROBUSTNESS'))} |"
        )

    lines.extend([
        "",
        "### Per-source provenance",
        "",
        "| Agent | Source | Commits | Underwriter | Founder comment | Approved delta (total/GOV/ROB/BIAS/OPS) |",
        "|---|---|---|---|---|---|",
    ])
    for agent in report["agents"]:
        for movement in agent.get("approved_movements", []):
            delta = movement["delta"]
            vector = "/".join(_fmt(delta.get(key, 0.0)) for key in ("total", *PILLARS))
            lines.append(
                f"| {agent['agent']} | {movement['source_ticket']} | "
                f"{', '.join(movement['commits'])} | {movement['underwriter_ref']} | "
                f"{movement.get('founder_comment_ref') or 'n/a'} | {vector} |"
            )

    lines.extend(["", "## Frozen Input SHA-256", ""])
    for path, digest in sorted(report.get("frozen_input_hashes", {}).items()):
        lines.append(f"- `{path}`: `{digest}`")

    behavioral = report.get("behavioral")
    if behavioral:
        lines.append("")
        lines.extend(behavioral_golden_fixtures.render_markdown_section(behavioral))
    return "\n".join(lines).rstrip() + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the nine-golden N=5 release gate.")
    parser.add_argument("--baseline", required=True, help="Baseline label for the delta filename/table.")
    parser.add_argument("--candidate", required=True, help="Candidate label for the delta filename/table.")
    parser.add_argument(
        "--phase",
        required=True,
        choices=("development",),
        help="Explicit lifecycle phase. Production/underwriting requires a separately approved drift policy.",
    )
    # BEAAA-23845: receipt provenance is required, with no default. A run that
    # cannot say which ticket it evidences must not emit a receipt at all.
    parser.add_argument(
        "--issue",
        required=True,
        help="BEAAA-NNN ticket this run evidences. Sets the receipt H1.",
    )
    parser.add_argument(
        "--cto-cosign-ref",
        required=True,
        help=(
            "CTO co-sign for this run: BEAAA-NNN, BEAAA-NNN#comment-<id>, or "
            f"'{COSIGN_NOT_REQUIRED}' when the run is rule-execution."
        ),
    )
    parser.add_argument(
        "--focus-claim",
        required=True,
        help="One sentence stating what this receipt evidences.",
    )
    parser.add_argument(
        "--ticket-refs",
        required=True,
        nargs="+",
        metavar="BEAAA-NNN",
        help="Tickets this receipt attributes to, including --issue.",
    )
    # BEAAA-23957: the run states which rubric points it exercised. There is no
    # default, because the default was two PII points most runs never measured.
    parser.add_argument(
        "--rubric-point-id",
        required=True,
        nargs="+",
        metavar="RUBRIC_POINT",
        help=(
            "Rubric points this run exercised, or "
            f"'{RUBRIC_POINTS_NOT_EXERCISED}' when it exercised none."
        ),
    )
    parser.add_argument("--today", default=None, help="YYYY-MM-DD override for deterministic tests.")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--json-out", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report, md_path = run_gate(
        manifest_path=args.manifest,
        out_dir=args.out_dir,
        baseline_label=args.baseline,
        candidate_label=args.candidate,
        issue=args.issue,
        cto_cosign_ref=args.cto_cosign_ref,
        focus_claim=args.focus_claim,
        ticket_refs=args.ticket_refs,
        rubric_point_id=args.rubric_point_id,
        phase=args.phase,
        today=args.today,
    )
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(md_path.relative_to(REPO))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
