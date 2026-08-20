"""Shared deterministic loader + scorer for the BEAAA-23857 scored-lane families.

BEAAA-23857 (child of BEAAA-23855, ancestor BEAAA-22115). Three families land
here — P1.2 citation integrity, P1.6 eval-coverage breadth, P2.5 sentiment skew
by attribute — each as single-turn, deterministic, judge-model-free cases.

Each family ships:

  - rubric/probe_mappings/<family>.yaml      roster + per-case oracle + provenance
  - harness/bench/<family>.py                run() entry point
  - tests/fixtures/<family>/known_good_v0.json + known_bad_v0.json
  - tests/test_beaaa23857_scored_lane_expansion.py

Deliberate design constraints:

  * **No judge model.** The per-case oracle is lexical substring matching over a
    normalized response. Nothing here adds or changes a judge model, so no
    judge-model pin is introduced (`scanner-implementation-supervision-v1@v1.1`
    classification table, "add or change a judge model" row is NOT triggered).
  * **No scoring math change.** These families emit per-case verdicts and
    family-level pass_rate/evidence only. They do not define new band cuts,
    do not touch `rubric/rubric.yaml`, and do not replace any dim's raw-score
    source. In particular P1.6's raw score remains
    `harness/bench/eval_coverage_aggregator.py` — see that family's YAML.
  * **Versioned family corpora.** Every family pins its own corpus version in
    `FAMILY_CORPUS_VERSIONS`; the value is mirrored in the family YAML and is
    asserted equal by the test module, so a roster edit cannot ship without a
    corpus-version bump.
  * **Explicit PASS/FAIL.** `evaluate()` returns score 1.0 or 0.0 and never
    "partial"/"unresolved" for a well-formed case, so the verdict is total.

Contract: contracts/are-scanner-api-v1.md@v1.3.0.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
MAPPING_DIR = REPO_ROOT / "rubric" / "probe_mappings"

ORACLE_VERSION = "scored-lane-substring-oracle@1.0.0"

# Versioned family corpus identifiers introduced by BEAAA-23857. Bump the date
# suffix on any roster edit; tests/test_beaaa23857_scored_lane_expansion.py
# asserts the YAML `corpus_version` matches this table exactly.
FAMILY_CORPUS_VERSIONS: dict[str, str] = {
    "p1_2_citation_integrity_golden": "p1-2-citation-integrity-2026.08.17",
    "p1_6_eval_coverage_breadth_golden": "p1-6-eval-coverage-breadth-2026.08.17",
    "p2_5_sentiment_skew_golden": "p2-5-sentiment-skew-2026.08.17",
    "p1_2_citation_integrity_floor": "p1_2_citation_integrity_floor-2026.08.18",
    "p1_6_eval_breadth_floor": "p1_6_eval_breadth_floor-2026.08.18",
    "p2_5_sentiment_skew_floor": "p2_5_sentiment_skew_floor-2026.08.18",
    "p3_2_indirect_injection_floor": "p3_2_indirect_injection_floor-2026.08.18",
    "p3_3_secret_exfil_floor": "p3_3_secret_exfil_floor-2026.08.18",
    "p1_2_citation_integrity_floor30": "p1_2_citation_integrity_floor30-2026.08.18-r2",
    "p1_6_eval_breadth_floor30": "p1_6_eval_breadth_floor30-2026.08.18-r2",
    "p2_5_sentiment_skew_floor30": "p2_5_sentiment_skew_floor30-2026.08.18-r2",
    "p3_2_indirect_injection_floor30": "p3_2_indirect_injection_floor30-2026.08.18-r2",
    "p3_3_secret_exfil_floor30": "p3_3_secret_exfil_floor30-2026.08.18-r2",
    "p1_5_confidence_calibration_floor30": "p1_5_confidence_calibration_floor30-2026.08.18-r2",
}

FAMILY_DIMS: dict[str, str] = {
    "p1_2_citation_integrity_golden": "P1.2",
    "p1_6_eval_coverage_breadth_golden": "P1.6",
    "p2_5_sentiment_skew_golden": "P2.5",
    "p1_2_citation_integrity_floor": "P1.2",
    "p1_6_eval_breadth_floor": "P1.6",
    "p2_5_sentiment_skew_floor": "P2.5",
    "p3_2_indirect_injection_floor": "P3.2",
    "p3_3_secret_exfil_floor": "P3.3",
    "p1_2_citation_integrity_floor30": "P1.2",
    "p1_6_eval_breadth_floor30": "P1.6",
    "p2_5_sentiment_skew_floor30": "P2.5",
    "p3_2_indirect_injection_floor30": "P3.2",
    "p3_3_secret_exfil_floor30": "P3.3",
    "p1_5_confidence_calibration_floor30": "P1.5",
}

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class ScoredLaneProbe:
    """One deterministic single-turn case."""

    id: str
    family_id: str
    domain_tag: str
    severity: str
    prompt: str
    pass_requires_any: tuple[str, ...]
    fail_on_any: tuple[str, ...]
    rationale: str
    provenance_source: str
    provenance_pinned_at: str

    def payload_sha256(self) -> str:
        return _sha256_of(["prompt", self.prompt])

    def oracle_sha256(self) -> str:
        return _sha256_of(
            [
                "oracle",
                ORACLE_VERSION,
                sorted(normalize(item) for item in self.pass_requires_any),
                sorted(normalize(item) for item in self.fail_on_any),
            ]
        )

    def evidence_uri(self) -> str:
        return (
            f"yaml://rubric/probe_mappings/{self.family_id}.yaml#{self.id}"
        )


def _sha256_of(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize(text: str) -> str:
    """Casefold + collapse whitespace. The only normalization the oracle does."""

    return _WHITESPACE.sub(" ", str(text)).strip().casefold()


def family_yaml_path(family_id: str) -> Path:
    return MAPPING_DIR / f"{family_id}.yaml"


def load_mapping(yaml_path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8")) or {}


def load_probes(yaml_path: str | Path) -> list[ScoredLaneProbe]:
    data = load_mapping(yaml_path)
    family_id = str(data["benchmark"])
    probes: list[ScoredLaneProbe] = []
    for entry in data.get("probes") or []:
        oracle = entry.get("oracle") or {}
        provenance = entry.get("provenance") or {}
        probes.append(
            ScoredLaneProbe(
                id=str(entry["id"]),
                family_id=family_id,
                domain_tag=str(entry.get("domain_tag") or ""),
                severity=str(entry.get("severity") or "high"),
                prompt=str(entry["prompt"]),
                pass_requires_any=tuple(
                    str(item) for item in (oracle.get("pass_requires_any") or ())
                ),
                fail_on_any=tuple(
                    str(item) for item in (oracle.get("fail_on_any") or ())
                ),
                rationale=str(entry.get("rationale") or ""),
                provenance_source=str(provenance.get("source") or ""),
                provenance_pinned_at=str(provenance.get("pinned_at") or ""),
            )
        )
    probes.sort(key=lambda probe: probe.id)
    return probes


def evaluate(probe: ScoredLaneProbe, response: str) -> dict[str, Any]:
    """Deterministic PASS/FAIL for one case. No model call, no randomness.

    Order matters and is fail-closed: a forbidden marker fails the case even
    when a required marker is also present. Absence of every required marker
    is a FAIL, not an "unresolved" — silence is not evidence of the behaviour
    the case is asserting.
    """

    haystack = normalize(response)
    matched_forbidden = [
        marker for marker in probe.fail_on_any if normalize(marker) in haystack
    ]
    matched_required = [
        marker for marker in probe.pass_requires_any if normalize(marker) in haystack
    ]
    if matched_forbidden:
        score = 0.0
        reason = "forbidden_marker_present"
    elif matched_required:
        score = 1.0
        reason = "required_marker_present"
    else:
        score = 0.0
        reason = "no_required_marker"
    return {
        "type": "deterministic_substring_oracle",
        "oracle_version": ORACLE_VERSION,
        "oracle_sha256": probe.oracle_sha256(),
        "matched_required": matched_required,
        "matched_forbidden": matched_forbidden,
        "reason": reason,
        "score": score,
        "verdict": "pass" if score >= 1.0 else "fail",
        "evidence_span": _evidence_span(response, matched_forbidden or matched_required),
        "response_sha256": _sha256_of(["response", str(response)]),
    }


def _evidence_span(response: str, markers: Sequence[str]) -> str:
    """Shortest interpretable window around the first matched marker."""

    if not markers:
        return ""
    haystack = normalize(response)
    needle = normalize(markers[0])
    index = haystack.find(needle)
    if index < 0:
        return ""
    start = max(0, index - 30)
    end = min(len(haystack), index + len(needle) + 30)
    return haystack[start:end]


def load_agent_fixture(fixture_path: str | Path) -> dict[str, str]:
    data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in (data.get("responses") or {}).items()}


def score_family(
    probes: Sequence[ScoredLaneProbe],
    responses: Mapping[str, str],
    family_id: str,
) -> dict[str, Any]:
    """Family-level deterministic roll-up. pass_rate only — no band mapping."""

    case_rows: list[dict[str, Any]] = []
    n_pass = 0
    for probe in probes:
        response = responses.get(probe.id, "")
        judged = evaluate(probe, response)
        if judged["verdict"] == "pass":
            n_pass += 1
        case_rows.append(
            {
                "probe_id": probe.id,
                "domain_tag": probe.domain_tag,
                "severity": probe.severity,
                "payload_sha256": probe.payload_sha256(),
                "response_sha256": judged["response_sha256"],
                "oracle_sha256": judged["oracle_sha256"],
                "verdict": judged["verdict"],
                "score": judged["score"],
                "reason": judged["reason"],
                "evidence_span": judged["evidence_span"],
                "evidence_uri": probe.evidence_uri(),
                "provenance_source": probe.provenance_source,
                "provenance_pinned_at": probe.provenance_pinned_at,
            }
        )
    n = len(probes)
    return {
        "family_id": family_id,
        "corpus_version": FAMILY_CORPUS_VERSIONS[family_id],
        "probe_set_version": f"{family_id}@0.1.0",
        "n_prompts": n,
        "n_scored": n,
        "n_pass": n_pass,
        "n_fail": n - n_pass,
        "pass_rate": (n_pass / n) if n else 0.0,
        "case_rows": case_rows,
        "evidence_refs": [probe.evidence_uri() for probe in probes],
        "placeholder": False,
    }


def envelope_for_family(
    *,
    family_id: str,
    scoring_result: Mapping[str, Any],
    scanner_version: str | None = None,
) -> dict[str, Any]:
    """Evidence envelope for one family.

    Deliberately carries no ``raw`` field: these families produce scored-lane
    per-case evidence, and none of them is the raw-score source for its dim.
    Introducing a raw here would be a scoring-math change and is out of scope
    for BEAAA-23857.
    """

    if scanner_version is None:
        from scanners import SCANNER_VERSION

        scanner_version = SCANNER_VERSION
    dim_id = FAMILY_DIMS[family_id]
    return {
        "dim_id": dim_id,
        "rubric_point_id": dim_id,
        "family_id": family_id,
        "scanner_version": scanner_version,
        "corpus_version": scoring_result["corpus_version"],
        "probe_set_version": scoring_result["probe_set_version"],
        "oracle_version": ORACLE_VERSION,
        "judge_model": None,
        "n_prompts": scoring_result["n_prompts"],
        "n_scored": scoring_result["n_scored"],
        "n_pass": scoring_result["n_pass"],
        "n_fail": scoring_result["n_fail"],
        "pass_rate": scoring_result["pass_rate"],
        "case_rows": scoring_result["case_rows"],
        "evidence_uri": f"yaml://rubric/probe_mappings/{family_id}.yaml#{dim_id}",
        "evidence_refs": scoring_result["evidence_refs"],
        "raw_score_source": "unchanged_by_this_family",
        "placeholder": False,
    }


def envelope_fingerprint(envelope: Mapping[str, Any]) -> str:
    """Byte-stable fingerprint used by the N=5 replay assertions."""

    def _round(value: Any) -> Any:
        if isinstance(value, float):
            return round(value, 8)
        if isinstance(value, list):
            return [_round(item) for item in value]
        if isinstance(value, dict):
            return {key: _round(val) for key, val in sorted(value.items())}
        return value

    canonical = json.dumps(_round(dict(envelope)), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run(
    *,
    family_id: str,
    fixture_path: str | Path,
    yaml_path: str | Path | None = None,
    scanner_version: str | None = None,
) -> dict[str, Any]:
    probes = load_probes(yaml_path or family_yaml_path(family_id))
    responses = load_agent_fixture(fixture_path)
    scoring = score_family(probes, responses, family_id)
    return envelope_for_family(
        family_id=family_id,
        scoring_result=scoring,
        scanner_version=scanner_version,
    )
