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
from scanners import DEFAULT_SEED, SCANNER_VERSION
from harness.contract_envelope import to_contract_output
from harness.applicability import (
    ApplicabilityError,
    decide,
    load_policy,
    validate_for_scoring,
)


# Hard size caps. The CLI runs on attacker-controlled inputs (system_prompt,
# tool_inventory, attestations all come from the scanned agent's owner), so
# every read is bounded. The numbers are deliberately generous for legitimate
# inputs (10MB system prompt, 5MB tool inventory) and immediately ridiculous
# for adversarial ones.
_MAX_FILE_BYTES = 10 * 1024 * 1024
_MAX_AGENT_ID_LEN = 128
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")
_TRUSTED_ARCHITECTURE_FLAGS_ENV = "BACKBOND_TRUSTED_ARCHITECTURE_FLAGS_JSON"
# Dim IDs accepted on the CLI input surface. Two non-overlapping shapes:
# legacy `P<n>.<m>` (rubric v1.0.0) and fully-qualified `Pillar.FAMILY.SUB`
# (rubric v1.0.1, strict 3-segment all-caps). The union is a true superset
# of the legacy regex on legacy inputs. See ADR-0027 (BEAAA-2198).
_DIM_ID_RE = re.compile(
    r"^(?:P\d+\.\d+|[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*)$"
)

# Post-BEAAA-470: the rubric pillar keys are now the contract pillar names
# directly (OPS / BIAS_FAIRNESS / ROBUSTNESS / GOVERNANCE). `subscores`
# emitted by this CLI is therefore contract-named at the source; the
# `subscores_contract_named` ADR-0005 workaround is removed (was a
# pre-rename bridge view that grouped P1+P3→ROBUSTNESS / P4→GOVERNANCE_OPS
# under the old internal P1..P4 keys). See ADR-0006.


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
    applicability_decisions: dict[str, dict[str, Any]] | None
    applicability_policy_version: str | None
    # BEAAA-14431 (F11): the scan's declared architecture flags, read off the
    # applicability sidecar. Load-bearing for fail-closed validation of
    # caller-asserted N/A against harness.applicability.decide() — a caller
    # cannot suppress a dim the policy engine independently rules FIRES.
    architecture_flags: dict[str, bool] | None
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


def _legacy_replay_applicability_enabled(invocation: dict[str, Any] | None) -> bool:
    """Return true only for explicitly non-binding legacy replay."""
    if not isinstance(invocation, dict):
        return False
    if invocation.get("applicability_mode") == "legacy_replay":
        return True
    return invocation.get("non_binding_compatibility") is True


def _validate_caller_applicability(
    rubric: Rubric,
    decisions: dict[str, dict[str, Any]],
    architecture_flags: dict[str, bool] | None,
) -> None:
    """BEAAA-14431 (F11): fail-closed check of caller-asserted N/A waivers.

    The applicability sidecar is scanned-agent-authored input. A dim marked
    ``fires: false`` is dropped from its pillar mean (Option C re-norm), so an
    unvalidated waiver lets an agent mark its worst dims N/A and inflate
    ``are_total`` (BEAAA-14413 F11: ~8 pts, traced only by a coverage_caveat).

    Authority is the policy engine, NOT the caller. Every caller-asserted
    ``fires: false`` MUST (i) be an active rubric dim AND (ii) match a dim
    ``harness.applicability.decide()`` independently rules N/A for THIS scan's
    architecture_flags. Any waiver the engine says should FIRE — or any
    unknown/inactive dim, or a sidecar that omits the flags the engine needs —
    is a contract breach and fails closed (ScoringError → CliInputError, exit
    2). Precedent: the corpus_version pin at rubric/scoring.py ("if caller
    omits, they intended to omit, and that is a contract breach"); an
    out-of-policy waiver is the same class of breach.

    Raises ScoringError on any breach; returns None when every asserted N/A is
    independently confirmed by the policy engine (byte-identical scoring).
    """
    asserted_na = sorted(
        did
        for did, decision in decisions.items()
        if isinstance(decision, dict) and decision.get("fires") is False
    )
    if not asserted_na:
        # No dim is being suppressed → no inflation surface → nothing to gate.
        return

    # The sidecar and everything in it are caller-controlled.  In particular,
    # its architecture_flags cannot independently authorize waivers.  The
    # service/runner must inject the scanner-observed flags through its trusted
    # process environment (or, in replay tooling, after verifying the signed
    # evidence bundle).  Never fall back to the sidecar value.
    trusted_raw = os.environ.get(_TRUSTED_ARCHITECTURE_FLAGS_ENV)
    if not trusted_raw:
        raise ScoringError(
            "applicability sidecar asserts N/A for dims "
            f"{asserted_na} but no trusted scanner architecture evidence was "
            f"provided via {_TRUSTED_ARCHITECTURE_FLAGS_ENV}; the policy "
            "engine cannot independently confirm any waiver — fail-closed "
            "(BEAAA-14431 F11)"
        )

    try:
        trusted_flags = json.loads(trusted_raw)
    except json.JSONDecodeError as exc:
        raise ScoringError(
            f"trusted scanner architecture evidence in "
            f"{_TRUSTED_ARCHITECTURE_FLAGS_ENV} is invalid JSON — fail-closed"
        ) from exc
    if not isinstance(trusted_flags, dict):
        raise ScoringError("trusted scanner architecture evidence must be an object")

    try:
        policy = load_policy()
        engine_decisions = decide(policy, trusted_flags)
    except ApplicabilityError as exc:
        # Missing/unknown/non-bool flags, or a policy schema violation. The
        # engine could not rule → we cannot confirm → fail-closed.
        raise ScoringError(
            "applicability sidecar architecture_flags rejected by the policy "
            f"engine ({exc}) — caller-asserted N/A cannot be confirmed, "
            "fail-closed (BEAAA-14431 F11)"
        ) from exc

    active = set(rubric.active_dim_ids)
    for did in asserted_na:
        if did not in active:
            raise ScoringError(
                f"applicability waiver for {did!r} rejected: not an active "
                "rubric dim (unknown/inactive N/A assertion) — fail-closed "
                "(BEAAA-14431 F11)"
            )
        engine = engine_decisions.get(did)
        if engine is None or engine.fires:
            raise ScoringError(
                f"applicability waiver for {did!r} rejected: the policy engine "
                "independently rules this dim FIRES for the scan's declared "
                "architecture_flags — out-of-policy N/A inflation blocked "
                "(BEAAA-14431 F11)"
            )


def _validate_eval_results(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict) or not payload:
        raise CliInputError("eval_results.json must be a non-empty object")
    out: dict[str, dict[str, Any]] = {}
    for dim_id, entry in payload.items():
        if not isinstance(dim_id, str) or not _DIM_ID_RE.match(dim_id):
            raise CliInputError(
                f"eval_results key {dim_id!r} not a "
                "P<n>.<m> or Pillar.FAMILY.SUB dim id"
            )
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

    # BEAAA-648: Option-C applicability sidecar. Mirrors the
    # artifacts/beaaa-387/<agent>/applicability.json layout the L1 test
    # wiring (tests/test_golden_agents.py::_rerun_score) and harness
    # aggregators (harness/golden_set/{calibration_delta,discrimination,
    # n5_band}.py) already consume. Absence → Phase-1 behaviour
    # (byte-identical to v0.2.3 callers; no N/A re-norm).
    applicability_payload = _read_optional_json(_hashed("applicability.json"))
    applicability_decisions: dict[str, dict[str, Any]] | None = None
    applicability_policy_version: str | None = None
    architecture_flags: dict[str, bool] | None = None
    if applicability_payload is not None:
        if not isinstance(applicability_payload, dict):
            raise CliInputError("applicability.json must be an object")
        decisions = applicability_payload.get("applicability_decisions")
        if decisions is not None:
            if not isinstance(decisions, dict):
                raise CliInputError(
                    "applicability.json.applicability_decisions must be an object"
                )
            for did, decision in decisions.items():
                if not isinstance(did, str) or not _DIM_ID_RE.match(did):
                    raise CliInputError(
                        f"applicability_decisions key {did!r} not a "
                        "P<n>.<m> or Pillar.FAMILY.SUB dim id"
                    )
                if not isinstance(decision, dict):
                    raise CliInputError(
                        f"applicability_decisions[{did}] must be an object"
                    )
                if "fires" in decision and not isinstance(decision["fires"], bool):
                    raise CliInputError(
                        f"applicability_decisions[{did}].fires must be bool"
                    )
            applicability_decisions = decisions
        policy_version = applicability_payload.get("policy_version")
        if policy_version is not None:
            if not isinstance(policy_version, str):
                raise CliInputError(
                    "applicability.json.policy_version must be a string"
                )
            applicability_policy_version = _validate_version(
                policy_version, label="applicability.json policy_version"
            )
        # BEAAA-14431 (F11): read the caller-declared architecture flags so
        # run_scan can independently re-derive the legitimate N/A set via
        # harness.applicability.decide() and reject any out-of-policy waiver.
        # Type-only validation here (bool map); the fail-closed semantics
        # (missing/unknown flags → reject) live in decide()'s _validate_flags.
        flags_payload = applicability_payload.get("architecture_flags")
        if flags_payload is not None:
            if not isinstance(flags_payload, dict):
                raise CliInputError(
                    "applicability.json.architecture_flags must be an object"
                )
            for fname, fval in flags_payload.items():
                if not isinstance(fname, str) or not isinstance(fval, bool):
                    raise CliInputError(
                        "applicability.json.architecture_flags must map "
                        f"string flag names to bools (offending: {fname!r})"
                    )
            architecture_flags = dict(flags_payload)

    return CliInputs(
        agent_id=agent_id,
        eval_results=eval_results,
        system_prompt=system_prompt,
        tool_inventory=tool_inventory,
        policy_attestations=policy_attestations,
        governance_attestations=governance_attestations,
        model_versions=model_versions,
        scanner_invocation_context=invocation,
        applicability_decisions=applicability_decisions,
        applicability_policy_version=applicability_policy_version,
        architecture_flags=architecture_flags,
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

    compatibility_applicability = _legacy_replay_applicability_enabled(
        inputs.scanner_invocation_context
    )
    applicability_decisions = (
        inputs.applicability_decisions if compatibility_applicability else None
    )
    applicability_policy_version = (
        inputs.applicability_policy_version if compatibility_applicability else None
    )

    try:
        # BEAAA-14431 (F11): before the honored sidecar can drop any dim from
        # the pillar mean, validate every caller-asserted N/A against the
        # authoritative policy engine. Out-of-policy waivers fail closed here.
        if applicability_decisions:
            _validate_caller_applicability(
                rubric, applicability_decisions, inputs.architecture_flags
            )
            trusted_flags = json.loads(
                os.environ[_TRUSTED_ARCHITECTURE_FLAGS_ENV]
            )
            applicability_decisions = validate_for_scoring(
                load_policy(),
                trusted_flags,
                applicability_decisions,
                rubric.active_dim_ids,
            )
            applicability_policy_version = applicability_decisions.policy_version
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
            applicability_decisions=applicability_decisions,
            applicability_policy_version=applicability_policy_version,
        )
    except ScoringError as exc:
        # Surface as input error — the scoring engine's domain errors map
        # to "your inputs are inconsistent with the rubric", not internal
        # bugs. Examples: missing dim, raw out of [0,4], out-of-policy waiver.
        raise CliInputError(f"scoring rejected: {exc}") from exc

    # BEAAA-14431 (F19 counterpart): when a sidecar carried N/A assertions but
    # was NOT honored (legacy-replay flag off / binding path), the discard is
    # otherwise invisible. Emit an additive diagnostic naming the dropped count
    # so the discard is observable in the evidence trail — no silent discard.
    if inputs.applicability_decisions and not compatibility_applicability:
        dropped = sorted(
            did
            for did, decision in inputs.applicability_decisions.items()
            if isinstance(decision, dict) and decision.get("fires") is False
        )
        if dropped:
            score_result.setdefault("diagnostics", []).append({
                "code": "applicability_sidecar_ignored",
                "detail": {
                    "dropped_decision_count": len(dropped),
                    "dropped_dims": dropped,
                    "reason": (
                        "applicability sidecar present but not honored: the "
                        "scanner invocation context did not enable the "
                        "non-binding legacy-replay applicability path, so the "
                        "caller-asserted N/A decisions were discarded and all "
                        "dims scored (BEAAA-14431 F19)."
                    ),
                },
            })

    return to_contract_output(
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
