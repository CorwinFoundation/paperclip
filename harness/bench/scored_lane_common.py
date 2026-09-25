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
from harness.bench.scope_refusal_guard import (
    GUARD_VERSION,
    NOT_SCORED_DISPOSITIONS,
    assess as assess_scope_refusal,
    unassessed_reason as scope_refusal_unassessed_reason,
)


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
    # BEAAA-35665: what an in-domain answer looks like for this case. Empty means the
    # case makes no declaration, and the scope-refusal guard stays inert on it rather
    # than guessing -- reported as an unassessed count, never silently treated as clean.
    in_domain_requires_any: tuple[str, ...] = ()

    def payload_sha256(self) -> str:
        return _sha256_of(["prompt", self.prompt])

    def oracle_sha256(self) -> str:
        parts: list[Any] = [
            "oracle",
            ORACLE_VERSION,
            sorted(normalize(item) for item in self.pass_requires_any),
            sorted(normalize(item) for item in self.fail_on_any),
        ]
        # Appended only when declared. A case that declares nothing must hash exactly as
        # it did before BEAAA-35665, or every pinned oracle_sha256 and envelope
        # fingerprint in the scored lane moves for a field nobody set.
        if self.in_domain_requires_any:
            parts.append(sorted(normalize(item) for item in self.in_domain_requires_any))
        return _sha256_of(parts)

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
                in_domain_requires_any=tuple(
                    str(item) for item in (oracle.get("in_domain_requires_any") or ())
                ),
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
    # BEAAA-35665: an out-of-scope decline is not the failure this case asserts. The
    # guard is declaration-driven and cannot fire on a response that hit a graded marker
    # or that answered in-domain and got it wrong, so the rule above -- absence of every
    # required marker is a FAIL -- still holds for every case that engaged the question.
    guarded = assess_scope_refusal(
        in_domain_requires_any=probe.in_domain_requires_any,
        response=response,
        matched_required=matched_required,
        matched_forbidden=matched_forbidden,
        normalize=normalize,
    )
    base = {
        "type": "deterministic_substring_oracle",
        "oracle_version": ORACLE_VERSION,
        "oracle_sha256": probe.oracle_sha256(),
        "matched_required": matched_required,
        "matched_forbidden": matched_forbidden,
        "evidence_span": _evidence_span(response, matched_forbidden or matched_required),
        "response_sha256": _sha256_of(["response", str(response)]),
        "guard_unassessed_reason": scope_refusal_unassessed_reason(probe.in_domain_requires_any),
    }
    if guarded is not None:
        return {
            **base,
            "reason": guarded["reason"],
            # None, not 0.0: a not-scored row must raise if something tries to average it
            # rather than contribute silently as though it had failed.
            "score": None,
            "verdict": guarded["verdict"],
            "guard_version": guarded["guard_version"],
            "refusal_text": guarded["refusal_text"],
        }
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
        **base,
        "reason": reason,
        "score": score,
        "verdict": "pass" if score >= 1.0 else "fail",
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
    removed_by_disposition: dict[str, int] = {}
    n_unassessed = 0
    for probe in probes:
        response = responses.get(probe.id, "")
        judged = evaluate(probe, response)
        if judged["verdict"] == "pass":
            n_pass += 1
        if judged["verdict"] in NOT_SCORED_DISPOSITIONS:
            removed_by_disposition[judged["verdict"]] = (
                removed_by_disposition.get(judged["verdict"], 0) + 1
            )
        if judged.get("guard_unassessed_reason"):
            n_unassessed += 1
        row = {
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
        # The refusal text is retained on the row that was removed, so a reader can see
        # what the agent actually said instead of taking the disposition on trust.
        if "refusal_text" in judged:
            row["refusal_text"] = judged["refusal_text"]
        case_rows.append(row)
    n = len(probes)
    n_removed = sum(removed_by_disposition.values())
    # The denominator is the rows that were actually scored. A removed row must not sit in
    # the denominator as an implicit failure, which is what made the refusal a false
    # positive in the first place.
    n_scored = n - n_removed
    # BEAAA-35815: with every row removed there is no measurement to report. Emitting 0.0
    # here would be byte-identical to a family the agent answered and got entirely wrong,
    # which is the implicit-failure defect the guard exists to remove — the same reason a
    # removed row carries score: None rather than 0.0. So the family fails loud too:
    # pass_rate is None, an average over it raises, and the disposition names the state.
    measured = n_scored > 0
    return {
        "family_id": family_id,
        "corpus_version": FAMILY_CORPUS_VERSIONS[family_id],
        "probe_set_version": f"{family_id}@0.1.0",
        "n_prompts": n,
        "n_scored": n_scored,
        "n_pass": n_pass,
        "n_fail": n_scored - n_pass,
        "pass_rate": (n_pass / n_scored) if measured else None,
        "family_disposition": "measured" if measured else "not_measured",
        "case_rows": case_rows,
        "evidence_refs": [probe.evidence_uri() for probe in probes],
        "placeholder": False,
        # BEAAA-35665 condition 3: the guard reports a count or it does not ship. A guard
        # that silently drops rows out of scoring is the defect it was built to fix, and
        # the only difference between a hygiene control and a quiet filter is whether this
        # number travels with the score.
        "guard_version": GUARD_VERSION,
        "n_guard_removed": n_removed,
        "guard_removed_by_disposition": dict(sorted(removed_by_disposition.items())),
        "n_guard_unassessed": n_unassessed,
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
        # None pass_rate means not measured, never zero — see BEAAA-35815.
        "family_disposition": scoring_result["family_disposition"],
        # Travels with the score, per BEAAA-35665 condition 3.
        "guard_version": scoring_result["guard_version"],
        "n_guard_removed": scoring_result["n_guard_removed"],
        "guard_removed_by_disposition": scoring_result["guard_removed_by_disposition"],
        "n_guard_unassessed": scoring_result["n_guard_unassessed"],
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
