"""BackBond Scanner v1 release evidence and model-pin gate.

Rule-execution guard for BEAAA-10189. This module validates release
candidate score envelopes without changing score shape, scoring math,
rubric weights, evidence URI semantics, or customer-data handling.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse


REQUIRED_PACKET_FIELDS = ("scanner_version", "corpus_version", "seed")
REQUIRED_EVIDENCE_FIELDS = (
    "rubric_point_id",
    "test_id",
    "artifact_uri",
    "verdict",
    "weight",
)
REQUIRED_EGRESS_NEGATIVE_CONTROL_FIELDS = (
    "timestamp",
    "scanner_version",
    "corpus_version",
    "seed",
    "execution_environment",
)
N5_SEEDS = frozenset(range(5))
SCORE_FIELDS = ("are_total", "GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS")

UNPINNED_MODEL_PATTERNS = (
    re.compile(r"(^|[-_/:])latest($|[-_/:])", re.IGNORECASE),
    re.compile(r"(^|[-_/:])default($|[-_/:])", re.IGNORECASE),
    re.compile(r"(^|[-_/:])mock($|[-_/:])", re.IGNORECASE),
    re.compile(r"(^|[-_/:])unpinned($|[-_/:])", re.IGNORECASE),
)


@dataclass(frozen=True)
class GateFinding:
    code: str
    packet: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "packet": self.packet, "detail": self.detail}


class ReleaseGateError(RuntimeError):
    def __init__(self, findings: list[GateFinding]):
        self.findings = findings
        super().__init__("\n".join(f"{f.code}: {f.packet}: {f.detail}" for f in findings))


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReleaseGateError([GateFinding("INVALID_JSON", str(path), str(exc))]) from exc
    if not isinstance(value, dict):
        raise ReleaseGateError([GateFinding("INVALID_JSON", str(path), "top-level JSON must be an object")])
    return value


def build_release_manifest(packet_paths: Iterable[Path]) -> dict[str, Any]:
    packets = [(path, load_json(path)) for path in packet_paths]
    scanner_versions: set[str] = set()
    corpus_versions: set[str] = set()
    rubric_versions: set[str] = set()
    primary_target_models: set[str] = set()
    judge_models: set[str] = set()
    seeds: set[int | str] = set()
    detector_versions: dict[str, str] = {}
    evidence_uri_index: dict[str, dict[str, str]] = {}

    for path, packet in packets:
        _add_if_present(scanner_versions, packet.get("scanner_version"))
        _add_if_present(corpus_versions, packet.get("corpus_version"))
        _add_if_present(rubric_versions, packet.get("rubric_version"))
        if "seed" in packet:
            seeds.add(packet["seed"])

        model_versions = packet.get("model_versions") or {}
        if isinstance(model_versions, dict):
            _add_if_present(primary_target_models, model_versions.get("primary"))
            _add_many(judge_models, model_versions.get("judges"))
            detectors = model_versions.get("detectors") or packet.get("detector_versions")
        else:
            detectors = packet.get("detector_versions")
        _merge_detector_versions(detector_versions, detectors)

        _add_many(judge_models, packet.get("judge_models"))
        _add_if_present(judge_models, packet.get("judge_model"))
        _add_many(primary_target_models, packet.get("primary_target_models"))
        _add_if_present(primary_target_models, packet.get("primary_target_model"))
        for idx, ref in enumerate(packet.get("evidence_refs") or []):
            if not isinstance(ref, dict) or not isinstance(ref.get("artifact_uri"), str):
                continue
            uri = ref["artifact_uri"]
            evidence_uri_index[uri] = {
                "artifact_uri": uri,
                "packet": str(path),
                "evidence_ref_index": str(idx),
            }

    return {
        "contract_version": "are-scanner-api-v1@v1.0",
        "scanner_versions": sorted(scanner_versions),
        "corpus_versions": sorted(corpus_versions),
        "rubric_versions": sorted(rubric_versions),
        "primary_target_models": sorted(primary_target_models),
        "detector_versions": dict(sorted(detector_versions.items())),
        "judge_models": sorted(judge_models),
        "seeds": sorted(seeds, key=lambda value: str(value)),
        "evidence_uri_index": [evidence_uri_index[uri] for uri in sorted(evidence_uri_index)],
    }


def validate_release(
    packet_paths: Iterable[Path],
    *,
    manifest_path: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    repo_root = (repo_root or Path.cwd()).resolve()
    packet_paths = [path.resolve() for path in packet_paths]
    manifest = load_json(manifest_path.resolve()) if manifest_path else {}
    indexed_uris = _manifest_evidence_uris(manifest)
    findings: list[GateFinding] = []

    used = {
        "scanner_versions": set(),
        "corpus_versions": set(),
        "rubric_versions": set(),
        "primary_target_models": set(),
        "detector_versions": {},
        "judge_models": set(),
        "seeds": set(),
    }
    evidence_count = 0

    for path in packet_paths:
        packet_label = _display_path(path, repo_root)
        packet = load_json(path)
        findings.extend(_validate_required_packet_fields(packet, packet_label))
        _collect_packet_pins(packet, used)

        evidence_refs = packet.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            findings.append(GateFinding("MISSING_EVIDENCE_REFS", packet_label, "evidence_refs must be a non-empty list"))
            continue
        evidence_count += len(evidence_refs)
        for idx, ref in enumerate(evidence_refs):
            ref_label = f"{packet_label}#evidence_refs[{idx}]"
            if not isinstance(ref, dict):
                findings.append(GateFinding("INVALID_EVIDENCE_REF", ref_label, "entry must be an object"))
                continue
            findings.extend(_validate_evidence_ref(ref, ref_label, path.parent, repo_root, indexed_uris))

    findings.extend(_validate_manifest_coverage(manifest, used) if manifest else [])
    findings.extend(_validate_egress_negative_control(manifest, repo_root) if manifest else [
        GateFinding(
            "MISSING_EGRESS_NEGATIVE_CONTROL",
            "release_manifest",
            "release manifest is required to carry egress_negative_control_artifact",
        )
    ])
    findings.extend(_validate_judge_pins(used["judge_models"]))
    findings.extend(_validate_n5_reproducibility(manifest))

    report = {
        "status": "fail" if findings else "pass",
        "packet_count": len(packet_paths),
        "evidence_ref_count": evidence_count,
        "findings": [finding.to_dict() for finding in findings],
        "used_pins": _jsonable_used_pins(used),
    }
    if findings:
        raise ReleaseGateError(findings)
    return report


def _validate_n5_reproducibility(manifest: dict[str, Any]) -> list[GateFinding]:
    """Require five seeded runs per reference agent and a <=1 point spread.

    The release manifest is the binding release input, so the invariant is
    checked here rather than being delegated to a particular CI workflow.
    """
    runs = manifest.get("reproducibility_runs")
    if not isinstance(runs, list) or not runs:
        return [GateFinding(
            "MISSING_N5_REPRODUCIBILITY_RUNS",
            "release_manifest",
            "reproducibility_runs must contain seeds 0..4 for every reference agent",
        )]

    by_agent: dict[str, list[dict[str, Any]]] = {}
    findings: list[GateFinding] = []
    for index, run in enumerate(runs):
        label = f"release_manifest#reproducibility_runs[{index}]"
        if not isinstance(run, dict) or not run.get("agent_id"):
            findings.append(GateFinding("INVALID_N5_RUN", label, "agent_id is required"))
            continue
        by_agent.setdefault(str(run["agent_id"]), []).append(run)

    for agent_id, agent_runs in sorted(by_agent.items()):
        seeds = [run.get("seed") for run in agent_runs]
        if len(agent_runs) != 5 or set(seeds) != N5_SEEDS or len(set(seeds)) != 5:
            findings.append(GateFinding(
                "INVALID_N5_SEEDS",
                agent_id,
                f"expected exactly seeds 0..4; got {sorted(seeds, key=str)}",
            ))
            continue

        values: dict[str, list[float]] = {field: [] for field in SCORE_FIELDS}
        for run in agent_runs:
            subscores = run.get("subscores")
            for field in SCORE_FIELDS:
                value = run.get(field) if field == "are_total" else (
                    subscores.get(field) if isinstance(subscores, dict) else None
                )
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    findings.append(GateFinding(
                        "INVALID_N5_SCORE",
                        agent_id,
                        f"seed={run.get('seed')} {field} must be numeric",
                    ))
                else:
                    values[field].append(float(value))

        for field, scores in values.items():
            if len(scores) != 5:
                continue
            spread = max(scores) - min(scores)
            if spread > 1.0:
                findings.append(GateFinding(
                    "N5_REPRODUCIBILITY_BREACH",
                    agent_id,
                    f"{field} spread={spread:.4f} exceeds 1.0000",
                ))
    return findings


def _validate_required_packet_fields(packet: dict[str, Any], packet_label: str) -> list[GateFinding]:
    findings: list[GateFinding] = []
    for field in REQUIRED_PACKET_FIELDS:
        if packet.get(field) in (None, ""):
            findings.append(GateFinding("MISSING_PACKET_PIN", packet_label, f"{field} is required"))
    model_versions = packet.get("model_versions")
    if not isinstance(model_versions, dict) or not model_versions:
        findings.append(GateFinding("MISSING_MODEL_VERSIONS", packet_label, "model_versions object is required"))
    return findings


def _validate_evidence_ref(
    ref: dict[str, Any],
    ref_label: str,
    packet_dir: Path,
    repo_root: Path,
    indexed_uris: set[str],
) -> list[GateFinding]:
    findings: list[GateFinding] = []
    for field in REQUIRED_EVIDENCE_FIELDS:
        if ref.get(field) in (None, ""):
            findings.append(GateFinding("MISSING_EVIDENCE_FIELD", ref_label, f"{field} is required"))
    artifact_uri = ref.get("artifact_uri")
    if not isinstance(artifact_uri, str) or not artifact_uri.strip():
        findings.append(GateFinding("EMPTY_ARTIFACT_URI", ref_label, "artifact_uri must be a non-empty string"))
        return findings
    if not _artifact_uri_reachable(artifact_uri, packet_dir, repo_root, indexed_uris):
        findings.append(GateFinding("UNREACHABLE_ARTIFACT_URI", ref_label, artifact_uri))
    return findings


def _artifact_uri_reachable(uri: str, packet_dir: Path, repo_root: Path, indexed_uris: set[str]) -> bool:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path)).exists()
    if parsed.scheme:
        return uri in indexed_uris

    candidate = Path(uri)
    if candidate.is_absolute():
        return candidate.exists()
    return (packet_dir / candidate).exists() or (repo_root / candidate).exists()


def _validate_manifest_coverage(manifest: dict[str, Any], used: dict[str, Any]) -> list[GateFinding]:
    findings: list[GateFinding] = []
    for key in ("scanner_versions", "corpus_versions", "rubric_versions", "primary_target_models", "judge_models", "seeds"):
        missing = set(used[key]) - set(manifest.get(key) or [])
        if missing:
            findings.append(GateFinding("MANIFEST_MISSING_PIN", "release_manifest", f"{key}: {sorted(missing, key=str)}"))
    manifest_detectors = manifest.get("detector_versions") or {}
    if not isinstance(manifest_detectors, dict):
        findings.append(GateFinding("INVALID_MANIFEST", "release_manifest", "detector_versions must be an object"))
        manifest_detectors = {}
    for name, version in used["detector_versions"].items():
        if manifest_detectors.get(name) != version:
            findings.append(GateFinding("MANIFEST_MISSING_PIN", "release_manifest", f"detector_versions.{name}={version!r}"))
    return findings


def _validate_judge_pins(judge_models: Iterable[str]) -> list[GateFinding]:
    findings: list[GateFinding] = []
    for judge in sorted(str(model) for model in judge_models):
        if any(pattern.search(judge) for pattern in UNPINNED_MODEL_PATTERNS):
            findings.append(GateFinding("UNPINNED_JUDGE_MODEL", "release_manifest", judge))
    return findings


def _validate_egress_negative_control(
    manifest: dict[str, Any],
    repo_root: Path,
) -> list[GateFinding]:
    artifact = manifest.get("egress_negative_control_artifact")
    if not isinstance(artifact, str) or not artifact:
        return [
            GateFinding(
                "MISSING_EGRESS_NEGATIVE_CONTROL",
                "release_manifest",
                "egress_negative_control_artifact is required",
            )
        ]

    path = Path(artifact)
    if not path.is_absolute():
        path = repo_root / path
    if not path.exists():
        return [
            GateFinding(
                "MISSING_EGRESS_NEGATIVE_CONTROL",
                artifact,
                "egress_negative_control_artifact is unreachable",
            )
        ]
    try:
        record = load_json(path)
    except ReleaseGateError as exc:
        return [
            GateFinding(
                "INVALID_EGRESS_NEGATIVE_CONTROL",
                artifact,
                finding.detail,
            )
            for finding in exc.findings
        ]

    findings: list[GateFinding] = []
    for field in REQUIRED_EGRESS_NEGATIVE_CONTROL_FIELDS:
        if record.get(field) in (None, ""):
            findings.append(GateFinding("INVALID_EGRESS_NEGATIVE_CONTROL", artifact, f"{field} is required"))
    if record.get("blocked") is not True:
        findings.append(GateFinding("INVALID_EGRESS_NEGATIVE_CONTROL", artifact, "blocked must be true"))
    if record.get("network_success") is True:
        findings.append(GateFinding("EGRESS_NEGATIVE_CONTROL_FAILED", artifact, "forbidden target reached network"))

    for key in ("scanner_versions", "corpus_versions", "seeds"):
        manifest_values = {str(value) for value in manifest.get(key) or []}
        record_key = key[:-1] if key != "seeds" else "seed"
        record_value = record.get(record_key)
        if record_value not in (None, "") and str(record_value) not in manifest_values:
            findings.append(
                GateFinding(
                    "EGRESS_NEGATIVE_CONTROL_PIN_MISMATCH",
                    artifact,
                    f"{record_key}={record_value!r} not present in release manifest {key}",
                )
            )
    return findings


def _collect_packet_pins(packet: dict[str, Any], used: dict[str, Any]) -> None:
    _add_if_present(used["scanner_versions"], packet.get("scanner_version"))
    _add_if_present(used["corpus_versions"], packet.get("corpus_version"))
    _add_if_present(used["rubric_versions"], packet.get("rubric_version"))
    if "seed" in packet and packet.get("seed") not in (None, ""):
        used["seeds"].add(packet["seed"])

    model_versions = packet.get("model_versions") or {}
    if isinstance(model_versions, dict):
        _add_if_present(used["primary_target_models"], model_versions.get("primary"))
        _add_many(used["judge_models"], model_versions.get("judges"))
        _merge_detector_versions(used["detector_versions"], model_versions.get("detectors"))

    _add_many(used["judge_models"], packet.get("judge_models"))
    _add_if_present(used["judge_models"], packet.get("judge_model"))
    _add_many(used["primary_target_models"], packet.get("primary_target_models"))
    _add_if_present(used["primary_target_models"], packet.get("primary_target_model"))
    _merge_detector_versions(used["detector_versions"], packet.get("detector_versions"))


def _manifest_evidence_uris(manifest: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for item in manifest.get("evidence_uri_index") or []:
        if isinstance(item, str):
            out.add(item)
        elif isinstance(item, dict) and isinstance(item.get("artifact_uri"), str):
            out.add(item["artifact_uri"])
    return out


def _merge_detector_versions(out: dict[str, str], value: Any) -> None:
    if isinstance(value, dict):
        for key, version in value.items():
            if key and version not in (None, ""):
                out[str(key)] = str(version)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item.get("name") and item.get("version"):
                out[str(item["name"])] = str(item["version"])
            elif isinstance(item, str):
                out[item] = item
    elif isinstance(value, str) and value:
        out[value] = value


def _add_many(out: set[str], value: Any) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _add_if_present(out, item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _add_if_present(out, item)
    else:
        _add_if_present(out, value)


def _add_if_present(out: set[str], value: Any) -> None:
    if value not in (None, ""):
        out.add(str(value))


def _jsonable_used_pins(used: dict[str, Any]) -> dict[str, Any]:
    return {
        "scanner_versions": sorted(used["scanner_versions"]),
        "corpus_versions": sorted(used["corpus_versions"]),
        "rubric_versions": sorted(used["rubric_versions"]),
        "primary_target_models": sorted(used["primary_target_models"]),
        "detector_versions": dict(sorted(used["detector_versions"].items())),
        "judge_models": sorted(used["judge_models"]),
        "seeds": sorted(used["seeds"], key=lambda value: str(value)),
    }


def _display_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root))
    except ValueError:
        return str(path)
