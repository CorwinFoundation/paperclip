"""BEAAA-464 — shared library backing the `bin/are-scan` CLI.

The CLI is a delivery layer over the in-agent scoring stack — same rubric,
same `rubric.scoring.score`, same forensic stamps. This module:

  1. Reads input artifacts from a directory in the convention documented in
     the bundle's `contracts/are-scanner-api-v1.md@v1.0` Input section.
  2. Delegates scoring to `rubric.scoring.score` (no math here).
  3. Maps the scoring engine's output into the contract Output shape,
     additively (existing fields preserved; `are_total` / `subscores` /
     `evidence_refs` / `uncovered_dimensions` aliases added on top).

The CLI surface (and therefore this module) is adversarial-input code: every
read is size-bounded, every path is resolved and asserted to live under the
caller-supplied input directory, no shell-out, no symlink follow-through.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rubric.scoring import Rubric, ScoringError, score
from scanners import CONTRACT_VERSION, DEFAULT_SEED, SCANNER_VERSION


# Hard size caps. The CLI runs on attacker-controlled inputs (system_prompt,
# tool_inventory, attestations all come from the scanned agent's owner), so
# every read is bounded. The numbers are deliberately generous for legitimate
# inputs (10MB system prompt, 5MB tool inventory) and immediately ridiculous
# for adversarial ones.
_MAX_FILE_BYTES = 10 * 1024 * 1024
_MAX_AGENT_ID_LEN = 128
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")

# Pillar id → contract subscore key. The internal rubric uses P1..P4
# (Failure Surface / Bias & Fairness / Adversarial Robustness / Governance
# & Operations); the contract document names GOVERNANCE / ROBUSTNESS /
# BIAS_FAIRNESS / OPS. The two never agreed in v1.0 of the contract and
# this CLI does NOT renumber the underlying scoring engine — it would
# require a rubric/scoring change (pillar-weight Founder gate). Instead
# `subscores` is emitted with BOTH the internal pillar id and the closest
# contract name so the Underwriter can read either side and a follow-up
# ticket can rationalise the naming without a flag day.
#
# Mapping rationale: P1 (Failure Surface / hallucination) is the dominant
# loss-frequency pillar so it groups with the contract's ROBUSTNESS bucket
# alongside P3. The combined "robustness" view is a derived 2-pillar sum;
# pricing engine continues to read the per-internal-pillar subscore.
_PILLAR_TO_CONTRACT = {
    "P1": "ROBUSTNESS",        # Failure Surface (hallucination)
    "P2": "BIAS_FAIRNESS",
    "P3": "ROBUSTNESS",        # Adversarial Robustness
    "P4": "GOVERNANCE_OPS",    # Governance & Operations (contract splits)
}


class CliInputError(ValueError):
    """Input rejected at the CLI boundary. Maps to exit 2."""


class CliEvidenceError(ValueError):
    """Evidence trail incomplete. Maps to exit 1 (refuse-to-quote)."""


@dataclass(frozen=True)
class CliInputs:
    """Materialised input artifacts read off disk.

    Only `eval_results` is load-bearing for scoring math today. The other
    artifacts are persisted into the output as evidence references and
    hashed into the snapshot, but the scoring engine does not interpret
    them — this matches the existing in-agent path where attestations
    flow through `evidence_uri` strings, not structured fields.
    """

    agent_id: str
    eval_results: dict[str, dict[str, Any]]
    system_prompt: str | None
    tool_inventory: list[dict[str, Any]] | None
    policy_attestations: dict[str, Any] | None
    governance_attestations: dict[str, Any] | None
    model_versions: dict[str, Any] | None
    scanner_invocation_context: dict[str, Any] | None
    input_dir: Path
    file_sha256s: dict[str, str]


def _safe_join(base: Path, name: str) -> Path:
    """Resolve `name` under `base` and refuse path-traversal or symlinks
    that escape `base`. Returns the resolved absolute path or raises.
    """
    if not name or name.startswith(("/", "\\")) or ".." in Path(name).parts:
        raise CliInputError(f"input file name rejected (path traversal): {name!r}")
    candidate = (base / name)
    try:
        resolved = candidate.resolve(strict=False)
        base_resolved = base.resolve(strict=False)
    except OSError as exc:
        raise CliInputError(f"input path resolve failed for {name!r}: {exc}") from exc
    try:
        resolved.relative_to(base_resolved)
    except ValueError as exc:
        raise CliInputError(
            f"input file {name!r} resolves outside --input-dir"
        ) from exc
    return resolved


def _read_bytes(path: Path) -> bytes:
    if not path.exists() or not path.is_file():
        raise CliInputError(f"input file missing: {path}")
    size = path.stat().st_size
    if size > _MAX_FILE_BYTES:
        raise CliInputError(
            f"input file {path.name} is {size}B, exceeds {_MAX_FILE_BYTES}B cap"
        )
    with path.open("rb") as fh:
        return fh.read(_MAX_FILE_BYTES + 1)[:_MAX_FILE_BYTES]


def _read_optional_json(path: Path) -> Any:
    if not path.exists():
        return None
    raw = _read_bytes(path)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliInputError(f"{path.name} is not valid UTF-8 JSON: {exc}") from exc


def _read_required_json(path: Path) -> Any:
    if not path.exists():
        raise CliInputError(f"required input missing: {path.name}")
    return _read_optional_json(path)


def _read_optional_text(path: Path) -> str | None:
    if not path.exists():
        return None
    raw = _read_bytes(path)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CliInputError(f"{path.name} is not valid UTF-8: {exc}") from exc


def _validate_agent_id(agent_id: str) -> str:
    if not isinstance(agent_id, str) or not _AGENT_ID_RE.match(agent_id):
        raise CliInputError(
            "agent_id must match ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ "
            f"(got {agent_id!r})"
        )
    return agent_id


def _validate_version(version: str, *, label: str) -> str:
    if not isinstance(version, str) or not _SEMVER_RE.match(version):
        raise CliInputError(f"{label} must be semver (got {version!r})")
    return version


def _validate_seed(seed: Any) -> int:
    try:
        s = int(seed)
    except (TypeError, ValueError) as exc:
        raise CliInputError(f"--seed must be int (got {seed!r})") from exc
    if not (0 <= s < 2**63):
        raise CliInputError(f"--seed out of unsigned-64 range (got {s})")
    return s


def _validate_eval_results(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict) or not payload:
        raise CliInputError("eval_results.json must be a non-empty object")
    out: dict[str, dict[str, Any]] = {}
    for dim_id, entry in payload.items():
        if not isinstance(dim_id, str) or not re.match(r"^P\d+\.\d+$", dim_id):
            raise CliInputError(f"eval_results key {dim_id!r} not a P<n>.<m> dim id")
        if not isinstance(entry, dict) or "raw" not in entry:
            raise CliInputError(f"eval_results[{dim_id}] missing 'raw'")
        try:
            raw = float(entry["raw"])
        except (TypeError, ValueError) as exc:
            raise CliInputError(
                f"eval_results[{dim_id}].raw not numeric"
            ) from exc
        if not 0.0 <= raw <= 4.0:
            raise CliInputError(
                f"eval_results[{dim_id}].raw={raw} outside [0,4]"
            )
        evidence_uri = entry.get("evidence_uri", "")
        if not isinstance(evidence_uri, str):
            raise CliInputError(
                f"eval_results[{dim_id}].evidence_uri must be string"
            )
        out[dim_id] = {"raw": raw, "evidence_uri": evidence_uri}
    return out


def load_inputs(agent_id: str, input_dir: Path) -> CliInputs:
    """Read the input directory and validate every artifact.

    Required: eval_results.json. Everything else is optional and skipped
    silently if absent — the scoring engine doesn't read them; they are
    persisted to the output as evidence_refs (with SHA256s) for the
    Underwriter and any later auditor.
    """
    agent_id = _validate_agent_id(agent_id)
    if not input_dir.exists() or not input_dir.is_dir():
        raise CliInputError(f"--input-dir not a directory: {input_dir}")

    file_sha256s: dict[str, str] = {}

    def _hashed(name: str) -> Path:
        path = _safe_join(input_dir, name)
        if path.exists() and path.is_file():
            file_sha256s[name] = "sha256:" + hashlib.sha256(
                _read_bytes(path)
            ).hexdigest()
        return path

    eval_path = _hashed("eval_results.json")
    eval_results = _validate_eval_results(_read_required_json(eval_path))

    system_prompt = _read_optional_text(_hashed("system_prompt.txt"))
    tool_inventory = _read_optional_json(_hashed("tool_inventory.json"))
    if tool_inventory is not None and not isinstance(tool_inventory, list):
        raise CliInputError("tool_inventory.json must be a list")
    policy_attestations = _read_optional_json(_hashed("policy_attestations.json"))
    if policy_attestations is not None and not isinstance(policy_attestations, dict):
        raise CliInputError("policy_attestations.json must be an object")
    governance_attestations = _read_optional_json(
        _hashed("governance_attestations.json")
    )
    if (
        governance_attestations is not None
        and not isinstance(governance_attestations, dict)
    ):
        raise CliInputError("governance_attestations.json must be an object")
    model_versions = _read_optional_json(_hashed("model_versions.json"))
    if model_versions is not None and not isinstance(model_versions, dict):
        raise CliInputError("model_versions.json must be an object")
    invocation = _read_optional_json(_hashed("scanner_invocation_context.json"))
    if invocation is not None and not isinstance(invocation, dict):
        raise CliInputError("scanner_invocation_context.json must be an object")

    return CliInputs(
        agent_id=agent_id,
        eval_results=eval_results,
        system_prompt=system_prompt,
        tool_inventory=tool_inventory,
        policy_attestations=policy_attestations,
        governance_attestations=governance_attestations,
        model_versions=model_versions,
        scanner_invocation_context=invocation,
        input_dir=input_dir,
        file_sha256s=file_sha256s,
    )


def _build_snapshot_hash(inputs: CliInputs, seed: int) -> str:
    """Deterministic snapshot hash over the materialised inputs.

    Uses sorted-key JSON of the validated eval_results + sorted file
    sha256s + seed. Reproducibility-critical: must be byte-stable across
    runs with the same inputs (does NOT include the scoring timestamp).
    """
    digest = hashlib.sha256()
    digest.update(b"agent_id=")
    digest.update(inputs.agent_id.encode("utf-8"))
    digest.update(b"\n")
    digest.update(b"seed=")
    digest.update(str(seed).encode("ascii"))
    digest.update(b"\n")
    digest.update(b"eval_results=")
    digest.update(
        json.dumps(inputs.eval_results, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")
    )
    digest.update(b"\n")
    digest.update(b"files=")
    digest.update(
        json.dumps(inputs.file_sha256s, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")
    )
    return "sha256:" + digest.hexdigest()


def _to_contract_output(
    *,
    inputs: CliInputs,
    score_result: dict[str, Any],
    seed: int,
    corpus_version: str | None,
    scanner_version: str,
    scored_at: datetime,
) -> dict[str, Any]:
    """Map the in-agent scoring output to the contract Output shape.

    The existing fields (bmi, band, pillars, snapshot_hash, ...) are
    preserved exactly — the Underwriter pricing engine reads those today
    and a behavioural rename would be a contract change. The contract-
    named aliases are layered ON TOP so a CLI consumer that reads the
    documented shape works without forcing the in-agent path to refactor.
    """
    pillars = score_result.get("pillars", {})
    subscores: dict[str, float] = {}
    contract_subscores: dict[str, float] = {}
    evidence_refs: list[dict[str, Any]] = []
    uncovered: list[str] = []
    for pid, pdata in pillars.items():
        pscore = float(pdata.get("score", 0.0))
        subscores[pid] = pscore
        ckey = _PILLAR_TO_CONTRACT.get(pid, pid)
        contract_subscores[ckey] = round(
            contract_subscores.get(ckey, 0.0) + pscore, 4
        )
        for dim_id, dim_block in pdata.get("dimensions", {}).items():
            if dim_block.get("applicable") is False or dim_block.get("raw") is None:
                uncovered.append(dim_id)
                evidence_refs.append({
                    "rubric_point_id": dim_id,
                    "test_id": "N/A",
                    "artifact_uri": "",
                    "verdict": "n/a",
                    "weight": 0.0,
                    "na_justification": dim_block.get("na_justification"),
                    "na_justification_key": dim_block.get("na_justification_key"),
                })
                continue
            raw = float(dim_block.get("raw") or 0.0)
            evidence_uri = dim_block.get("evidence_uri") or ""
            verdict = (
                "pass" if raw >= 3.0
                else "partial" if raw >= 1.5
                else "fail"
            )
            evidence_refs.append({
                "rubric_point_id": dim_id,
                "test_id": dim_id,
                "artifact_uri": evidence_uri,
                "verdict": verdict,
                "weight": round(raw / 4.0, 4),
            })

    # Hard guarantee #2 from the contract: every active rubric point must
    # have a reachable artifact_uri. The CLI surface is the place to
    # enforce this — the in-agent path defers it to the Underwriter intake.
    missing_artifact = [
        ref["rubric_point_id"]
        for ref in evidence_refs
        if ref["verdict"] != "n/a" and not ref["artifact_uri"]
    ]
    if missing_artifact:
        raise CliEvidenceError(
            "evidence_refs incomplete: dims with empty artifact_uri: "
            + ",".join(sorted(missing_artifact))
        )

    bmi = int(score_result.get("bmi", 0))
    band = score_result.get("band", {})
    band_letter = band.get("display") or band.get("letter") or ""

    # Confidence proxy: 1.0 - share of N/A dims. Real confidence is the
    # Underwriter's call (loaded into pricing as worst-case per Hard
    # guarantee #2 of the consumer side); this is a deterministic stand-in
    # so the contract field is never absent.
    total_dims = sum(
        len(p.get("dimensions", {})) for p in pillars.values()
    ) or 1
    confidence = round(1.0 - (len(uncovered) / total_dims), 4)

    contract_output: dict[str, Any] = dict(score_result)
    contract_output.update({
        "agent_id": inputs.agent_id,
        "are_total": bmi,
        "subscores": {k: round(v, 4) for k, v in subscores.items()},
        "subscores_contract_named": contract_subscores,
        "band_letter": band_letter,
        "confidence": confidence,
        "evidence_refs": evidence_refs,
        "uncovered_dimensions": sorted(set(uncovered)),
        "scanner_version": scanner_version,
        "corpus_version": (
            corpus_version if corpus_version is not None
            else score_result.get("corpus_version", "")
        ),
        "model_versions": score_result.get("model_versions"),
        "seed": seed,
        "run_timestamp": scored_at.isoformat(),
        "contract_version": CONTRACT_VERSION,
        "input_file_sha256s": inputs.file_sha256s,
        "cli_meta": {
            "tool": "bin/are-scan",
            "issue": "BEAAA-464",
            "input_dir_name": inputs.input_dir.name,
            "has_system_prompt": inputs.system_prompt is not None,
            "tool_inventory_count": (
                len(inputs.tool_inventory)
                if inputs.tool_inventory is not None else None
            ),
            "has_policy_attestations": inputs.policy_attestations is not None,
            "has_governance_attestations": (
                inputs.governance_attestations is not None
            ),
            "scanner_invocation_context": inputs.scanner_invocation_context,
            "pillar_naming_note": (
                "subscores keyed by internal rubric pillar ids (P1..P4); "
                "subscores_contract_named groups them under the contract's "
                "GOVERNANCE_OPS / ROBUSTNESS / BIAS_FAIRNESS labels. The "
                "two are not 1:1 — internal P1 (Failure Surface) and P3 "
                "(Adversarial Robustness) both fold into contract "
                "ROBUSTNESS, and contract GOVERNANCE+OPS are jointly "
                "internal P4. See ADR-0005."
            ),
        },
    })
    return contract_output


def run_scan(
    *,
    agent_id: str,
    input_dir: Path,
    seed: int,
    corpus_version: str | None,
    rubric_path: Path,
    scored_at: datetime | None = None,
) -> dict[str, Any]:
    """Top-level entry point. Returns the contract-shaped output dict.

    Raises CliInputError (exit 2), CliEvidenceError (exit 1), or other
    exceptions (exit 3 — scanner internal error).
    """
    inputs = load_inputs(agent_id, input_dir)
    seed = _validate_seed(seed)
    if corpus_version is not None:
        corpus_version = _validate_version(corpus_version, label="--corpus")

    rubric = Rubric.load(rubric_path)
    snapshot_hash = _build_snapshot_hash(inputs, seed)
    scored_at = scored_at or datetime.now(timezone.utc)
    forensic_bundle_uri = (
        f"cli://bin-are-scan/{inputs.agent_id}?snapshot={snapshot_hash}"
    )

    try:
        score_result = score(
            rubric,
            inputs.eval_results,
            snapshot_hash=snapshot_hash,
            forensic_bundle_uri=forensic_bundle_uri,
            scored_at=scored_at,
            scanner_version=SCANNER_VERSION,
            corpus_version=corpus_version,
            seed=seed,
            model_versions=inputs.model_versions,
        )
    except ScoringError as exc:
        # Surface as input error — the scoring engine's domain errors map
        # to "your inputs are inconsistent with the rubric", not internal
        # bugs. Examples: missing dim, raw out of [0,4].
        raise CliInputError(f"scoring rejected: {exc}") from exc

    return _to_contract_output(
        inputs=inputs,
        score_result=score_result,
        seed=seed,
        corpus_version=corpus_version,
        scanner_version=SCANNER_VERSION,
        scored_at=scored_at,
    )


__all__ = [
    "CliEvidenceError",
    "CliInputError",
    "CliInputs",
    "load_inputs",
    "run_scan",
]
