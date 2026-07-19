#!/usr/bin/env python3
"""Fail-closed verifier for BEAAA-15481 BBQ corpus/mapping SHA reconciliation."""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAPPING = ROOT / "rubric" / "probe_mappings" / "bbq.yaml"
DEFAULT_REPORT = ROOT / "artifacts" / "beaaa-15481" / "bbq_mapping_reconciliation.json"
CONTRACT = "are-scanner-api-v1@v1.0"
SCANNER_VERSION = "0.5.0"
BASELINE_CORPUS_VERSION = "0.1.1"
PROBE_SET_VERSION = "active-probe-registry@0.1.0+beaaa15481.failclosed"
RUBRIC_POINT_ID = "P2.1"


class ReconciliationError(RuntimeError):
    """Raised when BBQ mapping/corpus pins do not reconcile."""


@dataclass(frozen=True)
class Reconciliation:
    mapping_path: Path
    corpus_path: Path
    corpus_manifest_path: Path
    mapping_sha256: str
    corpus_manifest_sha256: str
    corpus_file_sha256: str
    n_items: int
    corpus_version: str

    def to_report(self) -> dict[str, Any]:
        return {
            "ticket": "BEAAA-15481",
            "status": "canonical_references_match",
            "classification": "rule-execution",
            "contract": CONTRACT,
            "scanner_version": SCANNER_VERSION,
            "baseline_corpus_version": BASELINE_CORPUS_VERSION,
            "candidate_corpus_version": None,
            "candidate_corpus_sha256": None,
            "candidate_corpus_created": False,
            "probe_set_version": PROBE_SET_VERSION,
            "rubric_point_id": RUBRIC_POINT_ID,
            "n5_calibration_delta": "not run: reconciliation-only pin verification; no scoring/corpus activation",
            "cto_cosign_ref": None,
            "mapping": {
                "path": self.mapping_path.relative_to(ROOT).as_posix(),
                "sha256": self.mapping_sha256,
            },
            "corpus_manifest": {
                "path": self.corpus_manifest_path.relative_to(ROOT).as_posix(),
                "sha256": self.corpus_manifest_sha256,
                "declared_corpus_version": self.corpus_version,
                "declared_n_items": self.n_items,
            },
            "corpus_file": {
                "path": self.corpus_path.relative_to(ROOT).as_posix(),
                "sha256": self.corpus_file_sha256,
                "line_count": self.n_items,
            },
            "activation": {
                "authorized": False,
                "reason": "BEAAA-15481 reconciles immutable bytes only; activation remains gated by Underwriter, QA approval, and Founder promotion sign-off.",
            },
        }


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReconciliationError(f"{path} did not parse to a mapping")
    return value


def count_jsonl(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def reconcile(mapping_path: Path = DEFAULT_MAPPING) -> Reconciliation:
    mapping_path = mapping_path.resolve()
    mapping = load_yaml(mapping_path)
    corpus = mapping.get("corpus")
    if not isinstance(corpus, dict):
        raise ReconciliationError("mapping missing corpus block")

    rel_corpus_path = corpus.get("path")
    rel_manifest_path = corpus.get("manifest")
    mapping_sha = corpus.get("sha256")
    if not all(isinstance(x, str) and x for x in (rel_corpus_path, rel_manifest_path, mapping_sha)):
        raise ReconciliationError("mapping corpus.path, corpus.manifest, and corpus.sha256 are required")

    corpus_path = (ROOT / rel_corpus_path).resolve()
    corpus_manifest_path = (ROOT / rel_manifest_path).resolve()
    if not corpus_path.is_file():
        raise ReconciliationError(f"corpus file missing: {corpus_path}")
    if not corpus_manifest_path.is_file():
        raise ReconciliationError(f"corpus manifest missing: {corpus_manifest_path}")

    corpus_manifest = load_yaml(corpus_manifest_path)
    manifest_subset = corpus_manifest.get("subset_file")
    manifest_sha = corpus_manifest.get("sha256")
    manifest_n_items = corpus_manifest.get("n_items")
    manifest_version = corpus_manifest.get("corpus_version")
    if corpus_path.name != manifest_subset:
        raise ReconciliationError(
            f"mapping corpus path {corpus_path.name!r} does not match manifest subset_file {manifest_subset!r}"
        )
    if not isinstance(manifest_sha, str) or not manifest_sha:
        raise ReconciliationError("corpus manifest missing sha256")

    actual_sha = sha256_file(corpus_path)
    actual_count = count_jsonl(corpus_path)
    errors: list[str] = []
    if mapping_sha != actual_sha:
        errors.append(f"mapping sha256 {mapping_sha} != corpus bytes {actual_sha}")
    if manifest_sha != actual_sha:
        errors.append(f"manifest sha256 {manifest_sha} != corpus bytes {actual_sha}")
    if manifest_n_items != actual_count:
        errors.append(f"manifest n_items {manifest_n_items!r} != jsonl line count {actual_count}")
    if corpus.get("n_items") != actual_count:
        errors.append(f"mapping n_items {corpus.get('n_items')!r} != jsonl line count {actual_count}")
    if corpus.get("corpus_version") != manifest_version:
        errors.append(
            f"mapping corpus_version {corpus.get('corpus_version')!r} != manifest corpus_version {manifest_version!r}"
        )
    if errors:
        raise ReconciliationError("FAIL CLOSED:\n- " + "\n- ".join(errors))

    return Reconciliation(
        mapping_path=mapping_path,
        corpus_path=corpus_path,
        corpus_manifest_path=corpus_manifest_path,
        mapping_sha256=sha256_file(mapping_path),
        corpus_manifest_sha256=sha256_file(corpus_manifest_path),
        corpus_file_sha256=actual_sha,
        n_items=actual_count,
        corpus_version=str(manifest_version),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()

    try:
        result = reconcile(args.mapping)
    except ReconciliationError as exc:
        raise SystemExit(str(exc))

    report = result.to_report()
    if args.write_report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
