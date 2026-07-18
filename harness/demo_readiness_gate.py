"""Machine-readable demo coverage readiness gate for BEAAA-15495.

This gate does not score. It decides whether registry-listed demo cases have
enough real terminal evidence to enter a promotion-ready set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from api.jcs import canonicalize

SCHEMA_VERSION = "backbond-demo-coverage-readiness/v1"
PLAN_REVISION_ID = "fb8ce2fc-685f-46e1-87a6-04264e2148c2"
EXPECTED_SCANNER_VERSION = "0.5.0"
EXPECTED_CORPUS_VERSION = "0.1.1"
EXPECTED_CONTRACT = "are-scanner-api-v1@v1.0"
REQUIRED_SEEDS = (0, 1, 2, 3, 4)
MAX_SPREAD = 1.0

BAD_SOURCE_MARKERS = ("fixture", "synthetic", "deterministic-fixture-v1")
UNPINNED_MARKERS = ("latest", "unpinned", "unknown", "fixture")


@dataclass(frozen=True)
class RegistryCase:
    case_id: str
    rubric_point_id: str
    pillar: str
    status: str
    applicable: bool = True
    denominator_included: bool = True

    @property
    def required(self) -> bool:
        if not self.applicable or not self.denominator_included:
            return False
        return self.status in {"live", "executable", "required", "ready_candidate"}


@dataclass
class CaseAssessment:
    case: RegistryCase
    reasons: list[str] = field(default_factory=list)
    n5: dict[str, Any] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.case.required and not self.reasons


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_registry(path: Path) -> list[RegistryCase]:
    data = load_json(path)
    cases: list[RegistryCase] = []

    rows = data.get("rows")
    if isinstance(rows, list) and rows:
        for row in rows:
            case_id = _first_str(
                row,
                "case_id",
                "case_id_or_probe_id",
                "stable_executable_key",
                "id",
                "test_id",
                "entry_id",
                "runnable_entry",
            )
            rubric_point_id = _first_str(row, "rubric_point_id", "dimension_id", "rubric_id")
            if not case_id or not rubric_point_id:
                continue
            countable = row.get("countable_as_executable")
            status = str(row.get("status") or ("live" if countable is True else "not_executable"))
            cases.append(RegistryCase(
                case_id=case_id,
                rubric_point_id=rubric_point_id,
                pillar=_first_str(row, "pillar", "pillar_id") or _pillar_from_rubric(rubric_point_id),
                status=status,
                applicable=bool(row.get("applicable", True)),
                denominator_included=bool(row.get("denominator_included", countable is not False)),
            ))

    if cases:
        return sorted(cases, key=lambda c: (c.rubric_point_id, c.case_id))

    for point in data.get("active_rubric_points", []):
        rubric_point_id = point.get("rubric_point_id")
        if not rubric_point_id:
            continue
        status = str(point.get("status") or "")
        pillar = str(point.get("pillar") or _pillar_from_rubric(rubric_point_id))
        for entry in point.get("runnable_entries") or []:
            cases.append(RegistryCase(
                case_id=str(entry),
                rubric_point_id=str(rubric_point_id),
                pillar=pillar,
                status=status,
            ))
    return sorted(cases, key=lambda c: (c.rubric_point_id, c.case_id))


def load_evidence(paths: Iterable[Path], *, repo_root: Path) -> tuple[dict[str, dict[int, dict[str, Any]]], list[str]]:
    evidence: dict[str, dict[int, dict[str, Any]]] = {}
    errors: list[str] = []
    for path in sorted(_expand_paths(paths)):
        try:
            record = load_json(path)
        except Exception as exc:  # pragma: no cover - defensive path
            errors.append(f"malformed_evidence:{path}:{exc}")
            continue
        case_id = record.get("case_id")
        seed = record.get("seed")
        if not isinstance(case_id, str) or not case_id:
            errors.append(f"malformed_evidence:{path}:missing_case_id")
            continue
        if not isinstance(seed, int):
            errors.append(f"malformed_evidence:{path}:missing_integer_seed")
            continue
        record["_source_path"] = str(path)
        record["_source_sha256"] = sha256_file(path)
        record["_repo_root"] = str(repo_root)
        by_seed = evidence.setdefault(case_id, {})
        if seed in by_seed:
            errors.append(f"duplicate_evidence:{case_id}:seed_{seed}")
            by_seed[seed]["_duplicate_seed"] = True
            record["_duplicate_seed"] = True
        by_seed[seed] = record
    return evidence, errors


def evaluate_readiness(
    *,
    registry_path: Path,
    evidence_paths: Iterable[Path],
    calibration_path: Path,
    repo_root: Path,
    scanner_version: str = EXPECTED_SCANNER_VERSION,
    corpus_version: str = EXPECTED_CORPUS_VERSION,
    contract_version: str = EXPECTED_CONTRACT,
    generated_at: str | None = None,
) -> dict[str, Any]:
    registry_cases = load_registry(registry_path)
    evidence, evidence_errors = load_evidence(evidence_paths, repo_root=repo_root)
    registry_digest = sha256_file(registry_path)
    evidence_digest = _evidence_digest(evidence)
    generated_at = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")

    duplicate_case_ids = sorted(_duplicates(c.case_id for c in registry_cases))
    assessments: list[CaseAssessment] = []
    for case in registry_cases:
        assessment = CaseAssessment(case=case)
        if case.case_id in duplicate_case_ids:
            assessment.reasons.append("duplicate_registry_case_id")
        if not case.required:
            assessment.reasons.append("not_required_by_registry")
        records = evidence.get(case.case_id, {})
        _assess_seed_set(assessment, records)
        for seed in REQUIRED_SEEDS:
            record = records.get(seed)
            if record is None:
                continue
            assessment.reasons.extend(_record_reasons(
                record,
                case=case,
                repo_root=repo_root,
                scanner_version=scanner_version,
                corpus_version=corpus_version,
                contract_version=contract_version,
            ))
        assessment.n5 = _n5_table(case, records)
        if assessment.n5.get("total_spread") is None:
            assessment.reasons.append("missing_n5_total_scores")
        elif assessment.n5["total_spread"] > MAX_SPREAD:
            assessment.reasons.append("n5_total_spread_exceeds_1")
        bad_pillars = [
            pillar for pillar, spread in assessment.n5.get("pillar_spreads", {}).items()
            if spread > MAX_SPREAD
        ]
        if bad_pillars:
            assessment.reasons.append("n5_pillar_spread_exceeds_1:" + ",".join(sorted(bad_pillars)))
        assessments.append(assessment)

    golden = _assess_golden_regression(calibration_path)
    if not golden["clean"]:
        for assessment in assessments:
            if assessment.case.required:
                assessment.reasons.append("golden_set_regression_not_clean")

    ready_cases = [a.case.case_id for a in assessments if a.ready]
    rejected = [
        {
            "case_id": a.case.case_id,
            "rubric_point_id": a.case.rubric_point_id,
            "pillar": a.case.pillar,
            "reasons": sorted(set(a.reasons)),
        }
        for a in assessments
        if not a.ready
    ]
    n5_delta_table = [
        {
            "case_id": a.case.case_id,
            "rubric_point_id": a.case.rubric_point_id,
            "pillar": a.case.pillar,
            **a.n5,
        }
        for a in assessments
        if a.case.required
    ]
    output = {
        "schema_version": SCHEMA_VERSION,
        "plan_revision_id": PLAN_REVISION_ID,
        "ticket_ref": "BEAAA-15495",
        "contract_version": contract_version,
        "scanner_version": scanner_version,
        "corpus_version": corpus_version,
        "probe_set_version": "active-probe-registry",
        "rubric_point_id": sorted({c.rubric_point_id for c in registry_cases}),
        "generated_at": generated_at,
        "registry": {
            "path": str(registry_path),
            "sha256": registry_digest,
            "case_count": len(registry_cases),
            "required_case_count": sum(1 for c in registry_cases if c.required),
        },
        "evidence_store": {
            "sha256": evidence_digest,
            "case_count": len(evidence),
            "errors": sorted(evidence_errors),
        },
        "golden_set_regression": golden,
        "ready": bool(ready_cases) and not rejected and not evidence_errors,
        "ready_set": ready_cases,
        "rejected_cases": sorted(rejected, key=lambda r: (r["rubric_point_id"], r["case_id"])),
        "n5_delta_table": sorted(n5_delta_table, key=lambda r: (r["rubric_point_id"], r["case_id"])),
    }
    semantic_payload = dict(output)
    semantic_payload.pop("generated_at", None)
    output["semantic_sha256"] = hashlib.sha256(canonicalize(semantic_payload)).hexdigest()
    return output


def write_json(output: dict[str, Any]) -> str:
    return json.dumps(output, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BEAAA-15495 demo readiness gate")
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--evidence", required=True, nargs="+", type=Path)
    parser.add_argument("--calibration", required=True, type=Path)
    parser.add_argument("--repo-root", default=Path.cwd(), type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    report = evaluate_readiness(
        registry_path=args.registry,
        evidence_paths=args.evidence,
        calibration_path=args.calibration,
        repo_root=args.repo_root.resolve(),
    )
    text = write_json(report)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0 if report["ready"] else 1


def _first_str(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _pillar_from_rubric(rubric_point_id: str) -> str:
    return rubric_point_id.split(".", 1)[0]


def _expand_paths(paths: Iterable[Path]) -> list[Path]:
    expanded: list[Path] = []
    for path in paths:
        if path.is_dir():
            expanded.extend(p for p in path.rglob("*.json") if p.is_file())
        elif path.is_file():
            expanded.append(path)
    return expanded


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for value in values:
        if value in seen:
            dupes.add(value)
        seen.add(value)
    return dupes


def _evidence_digest(evidence: dict[str, dict[int, dict[str, Any]]]) -> str:
    rows = []
    for case_id, by_seed in sorted(evidence.items()):
        for seed, record in sorted(by_seed.items()):
            rows.append({
                "case_id": case_id,
                "seed": seed,
                "source_sha256": record.get("_source_sha256"),
            })
    return hashlib.sha256(canonicalize(rows)).hexdigest()


def _assess_seed_set(assessment: CaseAssessment, records: dict[int, dict[str, Any]]) -> None:
    seeds = sorted(records)
    if seeds != list(REQUIRED_SEEDS):
        assessment.reasons.append(
            "missing_required_seeds:" + ",".join(str(s) for s in REQUIRED_SEEDS if s not in records)
        )
    if any(record.get("_duplicate_seed") for record in records.values()):
        assessment.reasons.append("duplicate_evidence_seed")


def _record_reasons(
    record: dict[str, Any],
    *,
    case: RegistryCase,
    repo_root: Path,
    scanner_version: str,
    corpus_version: str,
    contract_version: str,
) -> list[str]:
    reasons: list[str] = []
    if record.get("rubric_point_id") != case.rubric_point_id:
        reasons.append("rubric_point_mismatch")
    if record.get("scanner_version") != scanner_version:
        reasons.append("scanner_version_mismatch")
    if record.get("corpus_version") != corpus_version:
        reasons.append("corpus_version_mismatch")
    if record.get("contract") not in (None, contract_version):
        reasons.append("contract_version_mismatch")
    if _contains_bad_marker(record):
        reasons.append("fixture_or_synthetic_evidence")
    if not (record.get("terminal") is True or record.get("terminal_at")):
        reasons.append("non_terminal_evidence")
    if not _target_response(record):
        reasons.append("missing_target_response")
    if not record.get("verdict"):
        reasons.append("missing_terminal_verdict")
    evidence_uri = _evidence_uri(record)
    if not evidence_uri:
        reasons.append("missing_evidence_uri")
    elif not _uri_reachable(evidence_uri, repo_root=repo_root, source_path=Path(record["_source_path"])):
        reasons.append("unreachable_evidence_uri")
    if not _content_digest(record):
        reasons.append("missing_content_digest")
    if not _judge_or_oracle_pinned(record):
        reasons.append("unpinned_judge_or_oracle")
    return reasons


def _target_response(record: dict[str, Any]) -> str | None:
    return _first_str(record, "target_response", "response", "target_output")


def _evidence_uri(record: dict[str, Any]) -> str | None:
    return _first_str(record, "evidence_uri", "artifact_uri")


def _content_digest(record: dict[str, Any]) -> str | None:
    return _first_str(record, "content_digest", "evidence_sha256", "response_sha256")


def _contains_bad_marker(record: dict[str, Any]) -> bool:
    text = json.dumps(record, sort_keys=True).lower()
    return any(marker in text for marker in BAD_SOURCE_MARKERS)


def _judge_or_oracle_pinned(record: dict[str, Any]) -> bool:
    jid = str(record.get("judge_oracle_id") or record.get("judge_id") or "")
    jver = str(record.get("judge_oracle_version") or record.get("judge_version") or "")
    oracle_mode = str(record.get("oracle_mode") or "")
    oracle = record.get("oracle")
    if oracle_mode == "deterministic_no_llm_judge" and isinstance(oracle, dict) and oracle.get("type"):
        return True
    if not jid or not jver:
        return False
    return not any(marker in f"{jid} {jver}".lower() for marker in UNPINNED_MARKERS)


def _uri_reachable(evidence_uri: str, *, repo_root: Path, source_path: Path) -> bool:
    if evidence_uri.startswith(("http://", "https://")):
        return False
    if evidence_uri.startswith("file://"):
        return Path(evidence_uri[7:]).exists()
    if evidence_uri.startswith(("evidence-store://", "are-probe://")):
        return True
    path = Path(evidence_uri)
    candidates = [path, repo_root / path, source_path.parent / path]
    return any(candidate.exists() for candidate in candidates)


def _score_total(record: dict[str, Any]) -> float | None:
    for key in ("total", "are_total", "backbond_score"):
        value = record.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    score = record.get("score")
    if isinstance(score, dict) and isinstance(score.get("total"), (int, float)):
        return float(score["total"])
    return None


def _pillar_scores(record: dict[str, Any]) -> dict[str, float]:
    raw = record.get("pillar_scores") or record.get("pillars") or {}
    out: dict[str, float] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, (int, float)):
                out[str(key)] = float(value)
            elif isinstance(value, dict) and isinstance(value.get("score"), (int, float)):
                out[str(key)] = float(value["score"])
    return out


def _n5_table(case: RegistryCase, records: dict[int, dict[str, Any]]) -> dict[str, Any]:
    totals = [_score_total(records[s]) for s in REQUIRED_SEEDS if s in records]
    complete_totals = [t for t in totals if t is not None]
    pillar_values: dict[str, list[float]] = {}
    for seed in REQUIRED_SEEDS:
        if seed not in records:
            continue
        for pillar, value in _pillar_scores(records[seed]).items():
            pillar_values.setdefault(pillar, []).append(value)
    pillar_spreads = {
        pillar: round(max(values) - min(values), 6)
        for pillar, values in pillar_values.items()
        if len(values) == len(REQUIRED_SEEDS)
    }
    return {
        "seeds_observed": sorted(records),
        "total_scores": complete_totals,
        "total_spread": (
            round(max(complete_totals) - min(complete_totals), 6)
            if len(complete_totals) == len(REQUIRED_SEEDS)
            else None
        ),
        "pillar_spreads": pillar_spreads,
    }


def _assess_golden_regression(path: Path) -> dict[str, Any]:
    base = {"path": str(path), "sha256": None, "clean": False, "reason": None}
    if not path.exists():
        base["reason"] = "missing_golden_set_regression_report"
        return base
    base["sha256"] = sha256_file(path)
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        clean = _markdown_n5_clean(text)
        base["clean"] = clean
        base["reason"] = None if clean else "golden_report_not_parseably_clean"
        return base
    status = str(data.get("status") or data.get("result") or "").lower()
    if status in {"pass", "passed", "clean"} or data.get("clean") is True:
        base["clean"] = True
        return base
    if data.get("breaches") in ([], None) and data.get("max_total_delta", 0) <= MAX_SPREAD:
        base["clean"] = True
        return base
    base["reason"] = "golden_report_not_clean"
    return base


def _markdown_n5_clean(text: str) -> bool:
    frontmatter_match = re.search(r"n5_calibration_delta:\s*[\"']([^\"']+)[\"']", text)
    if frontmatter_match:
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", frontmatter_match.group(1))]
        if nums and all(abs(n) <= MAX_SPREAD for n in nums):
            return True
    table_rows = [
        line for line in text.splitlines()
        if line.strip().startswith("|") and re.match(r"^\|\s*[0-4]\s*\|", line.strip())
    ]
    if len(table_rows) >= len(REQUIRED_SEEDS):
        values: list[float] = []
        for row in table_rows:
            values.extend(float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", row)[1:])
        if values and all(abs(n) <= MAX_SPREAD for n in values):
            return True
    return False


__all__ = ["evaluate_readiness", "load_registry", "main", "write_json"]
