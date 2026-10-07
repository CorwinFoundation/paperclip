#!/usr/bin/env python3
"""Lint the live full-scan registry against ``unique-test-spec/1.0.0``.

BEAAA-38921. Spec: ``docs/full-scan/unique-test-spec-v1.md``.

This module audits; it never adds, removes or edits a live case. It has two
halves:

* ``extract_live_records`` loads the registry the full-scan worker executes
  (``api.full_scan_worker.load_default_full_scan_registry_rows``) and dry-runs
  every row through the production dispatcher
  (``RegisteredCaseExecutor._execute_case``) with a recording stub target and a
  stub judge. The prompts it records are the prompts a real scan would send;
  nothing here re-implements prompt materialization or grading.
* ``lint_records`` applies the spec checks to normalized ``LintRecord``
  objects, so tests can seed malformed records without touching the registry.

Usage::

    python -m tools.unique_test_lint --out-dir artifacts/beaaa-38921
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_VERSION = "unique-test-spec/1.1.0"
SPEC_DOC = "docs/full-scan/unique-test-spec-v1.md"

KEEP, REWRITE, DROP = "keep", "rewrite", "drop"

# Reason codes. Every check emits exactly one of these names.
R_NOT_EXECUTABLE = "not_executable"
R_TARGET_NOT_EXERCISED = "target_not_exercised"
R_PROMPT_MISSING = "prompt_missing"
R_PROMPT_PLACEHOLDER = "prompt_placeholder"
R_PROMPT_TEMPLATE_VARIATION = "prompt_template_variation"
R_PROMPT_SLUG_ONLY = "prompt_slug_only"
R_PROMPT_THIRD_PERSON = "prompt_third_person_scenario"
R_PROMPT_STRUCTURED_SPEC = "prompt_structured_spec"
R_SCAFFOLD_EVAL = "scaffolding_eval_framing"
R_SCAFFOLD_EXPECTED = "scaffolding_expected_answer"
R_SCAFFOLD_INTERNAL_ID = "scaffolding_internal_id"
R_RUNNER_SCAFFOLD = "runner_render_scaffolding"
R_GRADING_MISSING = "grading_rule_missing"
R_AREA_MISSING = "area_missing"
R_FAMILY_MISSING = "family_missing"
R_SEVERITY_MISSING = "severity_missing"
R_SOURCE_MISSING = "source_missing"
R_LICENCE_MISSING = "licence_missing"
R_CAPABILITY_UNDECLARED = "capability_undeclared"
R_CAPABILITY_NOT_ENFORCED = "capability_declared_not_enforced"
R_DUP_EXACT = "duplicate_exact"
R_DUP_NEAR = "near_duplicate"
R_DUP_TRANSLATION = "translation_variant"
R_DUP_GENERATED = "generated_variant"

DROP_REASONS = frozenset(
    {R_NOT_EXECUTABLE, R_PROMPT_MISSING, R_DUP_EXACT, R_DUP_NEAR, R_DUP_TRANSLATION, R_DUP_GENERATED}
)
# Defects fixable by recording a metadata field, without touching prompt,
# grading or runner. Used only to rank repair targets.
METADATA_ONLY_REASONS = frozenset(
    {R_SEVERITY_MISSING, R_SOURCE_MISSING, R_LICENCE_MISSING, R_CAPABILITY_UNDECLARED}
)

SEVERITY_VALUES = frozenset(
    {"critical", "high", "medium", "low", "info", "sev1", "sev2", "sev3", "sev4"}
)
NEAR_DUP_JACCARD = 0.8
SHINGLE_WORDS = 3

# --- prompt content patterns -------------------------------------------------
# Only scaffolding is matched. A realistic prompt that says "password",
# "ignore", "test", "system prompt" or "secret" is not rejected for the word.
_PLACEHOLDER_RES = (
    re.compile(r"\[\s*(insert|placeholder|todo|tbd|your [a-z ]+ here)\b", re.I),
    re.compile(r"(?<!\$)\{\{?\s*[A-Za-z_][A-Za-z0-9_]*\s*\}?\}"),
    re.compile(r"<\s*(placeholder|insert[^>]*|todo|tbd)\s*>", re.I),
    re.compile(r"\b(TODO|TBD|FIXME|XXX)\b"),
    re.compile(r"\blorem ipsum\b", re.I),
    re.compile(r"(?<![\w.])@placeholder\b", re.I),
)
_VARIATION_RE = re.compile(r"\bvariation\s+\d+\b", re.I)
_SLUG_RE = re.compile(r"`[a-z0-9]+(?:[-_][a-z0-9]+){2,}`")
# A scenario narrated about a user instead of a message from one. "A customer
# whose account ... Write a reply" is a realistic task and is not matched: a
# person subject is flagged only when followed by a reporting verb.
_THIRD_PERSON_RE = re.compile(
    r"^\s*(?:(?:a|an|the)\s+(?:user|requester|customer|caller)\s+"
    r"(?:asks|asked|requests|requested|insists|attempts|wants|says|supplies|provides)\b"
    r"|(?:a|the)\s+(?:retrieved document|customer ticket|evaluation ledger|evidence)\s"
    r"|two otherwise identical\b)",
    re.I,
)
_EVAL_FRAMING_RES = (
    re.compile(r"\bthis is (?:a|an|only a) (?:test|evaluation|benchmark|drill)\b", re.I),
    re.compile(r"\byou are being (?:tested|evaluated|graded|benchmarked)\b", re.I),
    re.compile(r"\bas an? (?:evaluator|grader|judge)\b", re.I),
    re.compile(
        r"\b(?:evaluate|execute|run|prepare|assess)\b[^.\n]{0,40}\b(?:synthetic|bounded|supplied|following)\b"
        r"[^.\n]{0,40}\b(?:case|scenario|fixture)\b",
        re.I,
    ),
    re.compile(r"\bsynthetic (?:case|scenario|fixture|test|corpus|input|record|output|token)s?\b", re.I),
    re.compile(r"\bevaluation (?:case|scenario|conversation|harness|fixture)\b", re.I),
    re.compile(r"\bbehaviou?ral evaluation case\b", re.I),
    re.compile(r"\bsupporting-family test\b", re.I),
    re.compile(r"\b(?:target risk item|control group|observable fields)\b", re.I),
    re.compile(r"^\s*case \d+\s*:", re.I | re.M),
    re.compile(r"\bred[- ]team (?:test|exercise|probe)\b", re.I),
)
_EXPECTED_RES = (
    re.compile(r"\b(?:expected|gold|reference)[ _](?:answer|output|response|label|behaviou?r|disposition|verdict)\b", re.I),
    re.compile(r"\bexpected_response_label\b|\bexpected_label\b"),
    re.compile(r"\b(?:pass|fail)(?:es)? (?:if|when|only if)\b", re.I),
    re.compile(r"\bjudge_rubric\b|\b(?:grading|scoring|judge|evaluation) rubric\b", re.I),
    re.compile(r"\brequires? (?:a )?(?:safe )?refusal\b", re.I),
    re.compile(r"\bdecline and redirect\b", re.I),
    re.compile(r"\bsafe bounded disposition\b", re.I),
    re.compile(r"\bmust preserve policy behaviou?r\b", re.I),
    re.compile(r"\bintent label\b", re.I),
)
_INTERNAL_ID_RES = (
    re.compile(r"\bBEAAA-\d+\b"),
    re.compile(r"\b(?:stable_case_id|case_id|probe_id|test_id)\b"),
    re.compile(r"\bseed \d+\b", re.I),
)
# Text the runner itself adds when rendering a record into a delivered prompt.
_RUNNER_RENDER_RES = (
    re.compile(r"Continue the same evaluation conversation", re.I),
    re.compile(r"Execute the following evaluation scenario", re.I),
    re.compile(r"^(?:USER|SYSTEM EVENT|ASSISTANT OBSERVATION|USER TURN \d+|FINAL QUERY|PRIOR USER CONTEXT|USER QUERY|FIXTURE|INSTRUCTION):", re.M),
)
_LANG_ID_RE = re.compile(
    r"(?:^|[._-])(?:lang[=_:-]?)?(en|es|fr|de|pt|it|ja|zh|ko|ar|hi|ru|nl|pl|tr|vi|th|sw|he)$"
)


@dataclass(frozen=True)
class LintRecord:
    """One live registry row, normalized for linting."""

    family: str
    case_id: str
    area: str = ""
    source: str = ""
    licence: str = ""
    severity: str = ""
    capabilities: tuple[str, ...] = ()
    capability_declared: bool = False
    capability_enforced: bool = True
    grading_rule: str = ""
    prompts: tuple[str, ...] = ()
    delivered_prompts: tuple[str, ...] = ()
    executable: bool = True
    target_exercised: bool = True
    structured_spec_prompt: bool = False
    language: str = ""
    variant_base: str = ""
    scored: bool = False
    source_text: str = ""
    note: str = ""
    # A form-assembler variant names the unique test it was generated from
    # (BEAAA-38985). It is never a new unique test.
    parent_unique_test_id: str = ""
    stratum: str = ""

    @property
    def record_id(self) -> str:
        return f"{self.family}/{self.case_id}"


@dataclass
class LintResult:
    record: LintRecord
    reasons: list[str] = field(default_factory=list)
    dup_of: str = ""
    unique_test_id: str = ""

    @property
    def disposition(self) -> str:
        if any(reason in DROP_REASONS for reason in self.reasons):
            return DROP
        return REWRITE if self.reasons else KEEP


# --- checks ------------------------------------------------------------------

def _any(patterns: Sequence[re.Pattern[str]], text: str) -> list[str]:
    hits = []
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            hits.append(match.group(0))
    return hits


def prompt_content_reasons(
    prompts: Sequence[str], *, source_text: str = ""
) -> list[str]:
    """Content checks on authored prompt text.

    ``source_text`` is the record's source file. When a scaffolding phrase does
    not occur in it, the runner added the phrase, and the reason is
    ``runner_render_scaffolding`` instead of a content defect.
    """

    reasons: list[str] = []
    joined = "\n".join(str(p) for p in prompts)
    if not joined.strip():
        return [R_PROMPT_MISSING]

    def classify(code: str, hits: list[str]) -> None:
        if not hits:
            return
        if source_text and not any(hit in source_text for hit in hits):
            reasons.append(R_RUNNER_SCAFFOLD)
        else:
            reasons.append(code)

    if _any(_PLACEHOLDER_RES, joined):
        reasons.append(R_PROMPT_PLACEHOLDER)
    if _VARIATION_RE.search(joined):
        reasons.append(R_PROMPT_TEMPLATE_VARIATION)
    if _SLUG_RE.search(joined):
        reasons.append(R_PROMPT_SLUG_ONLY)
    if any(_THIRD_PERSON_RE.search(str(p)) for p in prompts):
        reasons.append(R_PROMPT_THIRD_PERSON)
    classify(R_SCAFFOLD_EVAL, _any(_EVAL_FRAMING_RES, joined))
    classify(R_SCAFFOLD_EXPECTED, _any(_EXPECTED_RES, joined))
    classify(R_SCAFFOLD_INTERNAL_ID, _any(_INTERNAL_ID_RES, joined))
    return sorted(set(reasons))


def runner_render_reasons(delivered: Sequence[str], authored: Sequence[str]) -> list[str]:
    """Scaffolding present in the delivered prompt but absent from authored text."""

    authored_text = "\n".join(authored)
    patterns = _RUNNER_RENDER_RES + _EVAL_FRAMING_RES + _EXPECTED_RES + _INTERNAL_ID_RES
    for prompt in delivered:
        for pattern in patterns:
            for match in pattern.finditer(prompt):
                if match.group(0) not in authored_text:
                    return [R_RUNNER_SCAFFOLD]
    return []


def record_reasons(record: LintRecord) -> list[str]:
    """All spec checks except duplicate detection."""

    reasons: list[str] = []
    if not record.executable:
        reasons.append(R_NOT_EXECUTABLE)
    elif not record.target_exercised:
        reasons.append(R_TARGET_NOT_EXERCISED)
    reasons.extend(prompt_content_reasons(record.prompts, source_text=record.source_text))
    if record.structured_spec_prompt:
        reasons.append(R_PROMPT_STRUCTURED_SPEC)
    reasons.extend(runner_render_reasons(record.delivered_prompts, record.prompts))
    if not record.grading_rule.strip():
        reasons.append(R_GRADING_MISSING)
    if not record.area.strip():
        reasons.append(R_AREA_MISSING)
    if not record.family.strip():
        reasons.append(R_FAMILY_MISSING)
    if record.severity.strip().casefold() not in SEVERITY_VALUES:
        reasons.append(R_SEVERITY_MISSING)
    if not record.source.strip():
        reasons.append(R_SOURCE_MISSING)
    if not record.licence.strip():
        reasons.append(R_LICENCE_MISSING)
    if not record.capability_declared:
        reasons.append(R_CAPABILITY_UNDECLARED)
    elif record.capabilities and not record.capability_enforced:
        reasons.append(R_CAPABILITY_NOT_ENFORCED)
    return sorted(set(reasons))


# --- duplicate detection -----------------------------------------------------

def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    # A "Variation 07" counter is template numbering, not content. Other
    # digits are content ("atomic number 79" vs "74") and are kept.
    text = _VARIATION_RE.sub(" ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def shingles(text: str, k: int = SHINGLE_WORDS) -> frozenset[str]:
    words = normalize_text(text).split()
    if len(words) <= k:
        return frozenset({" ".join(words)}) if words else frozenset()
    return frozenset(" ".join(words[i : i + k]) for i in range(len(words) - k + 1))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def detect_language(record: LintRecord) -> str:
    if record.language:
        return record.language.casefold()
    match = _LANG_ID_RE.search(record.case_id)
    return match.group(1) if match else ""


def variant_base(record: LintRecord) -> str:
    """Base id shared by translation/repeat variants; '' if none is marked."""

    if record.variant_base:
        return f"{record.family}/{record.variant_base}"
    if _LANG_ID_RE.search(record.case_id):
        return f"{record.family}/{_LANG_ID_RE.sub('', record.case_id)}"
    return ""


def assign_duplicates(
    results: list[LintResult], *, threshold: float = NEAR_DUP_JACCARD
) -> None:
    """Mark every non-canonical member of a duplicate group, in place.

    Canonical order: fewest non-duplicate defects first, then scored before
    unscored, then id. A record is compared only against canonical records, so
    a group counts once whatever its size.
    """

    for result in results:
        parent = result.record.parent_unique_test_id
        if parent:
            result.reasons.append(R_DUP_GENERATED)
            result.dup_of = parent
    order = sorted(
        (
            r
            for r in results
            if "\n".join(r.record.prompts).strip() and not r.record.parent_unique_test_id
        ),
        key=lambda r: (len(r.reasons), not r.record.scored, r.record.record_id),
    )
    canonical_by_text: dict[str, str] = {}
    canonical_by_base: dict[str, str] = {}
    canonical_shingles: dict[str, frozenset[str]] = {}
    postings: dict[str, list[str]] = defaultdict(list)
    for result in order:
        record = result.record
        joined = "\n".join(record.prompts)
        norm = normalize_text(joined)
        base = variant_base(record)
        if base and base in canonical_by_base:
            result.reasons.append(R_DUP_TRANSLATION)
            result.dup_of = canonical_by_base[base]
            continue
        if norm in canonical_by_text:
            result.reasons.append(R_DUP_EXACT)
            result.dup_of = canonical_by_text[norm]
            continue
        if R_PROMPT_SLUG_ONLY in result.reasons:
            # A slug-named stub differs from its siblings only by the slug,
            # which is the scenario. That is a rewrite, not a repeat.
            canonical_by_text[norm] = record.record_id
            continue
        sh = shingles(joined)
        overlap: Counter[str] = Counter()
        for s in sh:
            for other in postings.get(s, ()):
                overlap[other] += 1
        best_id, best = "", 0.0
        for other, inter in overlap.items():
            other_sh = canonical_shingles[other]
            score = inter / (len(sh) + len(other_sh) - inter)
            if score > best or (score == best and other < best_id):
                best_id, best = other, score
        if best >= threshold:
            result.reasons.append(R_DUP_NEAR)
            result.dup_of = best_id
            continue
        canonical_by_text[norm] = record.record_id
        if base:
            canonical_by_base[base] = record.record_id
        canonical_shingles[record.record_id] = sh
        for s in sh:
            postings[s].append(record.record_id)
    for result in results:
        result.reasons = sorted(set(result.reasons))


def lint_records(records: Iterable[LintRecord]) -> list[LintResult]:
    results = [LintResult(record=r, reasons=record_reasons(r)) for r in records]
    seen: set[str] = set()
    for result in results:
        if result.record.record_id in seen:
            raise ValueError(f"duplicate record id: {result.record.record_id}")
        seen.add(result.record.record_id)
    assign_duplicates(results)
    resolve_unique_ids(results)
    return results


def stratum_key(record: LintRecord) -> str:
    """Budgeting key for the form assembler: explicit stratum, else area/family."""

    return record.stratum or f"{record.area or '(none)'}/{record.family}"


def resolve_unique_ids(results: list[LintResult]) -> None:
    """Point every record at the canonical record it counts as, in place.

    A canonical record is its own unique test. A duplicate or generated variant
    takes the id at the end of its ``dup_of`` chain; a parent id that is not in
    the registry is still the unique test the variant belongs to.
    """

    by_id = {r.record.record_id: r for r in results}
    for result in results:
        current, seen = result, set()
        while current.dup_of and current.dup_of not in seen:
            seen.add(current.record.record_id)
            nxt = by_id.get(current.dup_of)
            if nxt is None:
                result.unique_test_id = current.dup_of
                break
            current = nxt
        else:
            result.unique_test_id = current.record.record_id


# --- outputs -----------------------------------------------------------------

CSV_FIELDS = (
    "id", "area", "family", "source", "licence", "severity", "capabilities",
    "grading_rule", "disposition", "reasons", "dup_of", "unique_test_id", "stratum_key",
)


def csv_rows(results: Sequence[LintResult]) -> list[dict[str, str]]:
    rows = []
    for result in sorted(results, key=lambda r: r.record.record_id):
        rec = result.record
        rows.append(
            {
                "id": rec.record_id,
                "area": rec.area,
                "family": rec.family,
                "source": rec.source,
                "licence": rec.licence,
                "severity": rec.severity,
                "capabilities": ";".join(rec.capabilities),
                "grading_rule": rec.grading_rule,
                "disposition": result.disposition,
                "reasons": ";".join(result.reasons),
                "dup_of": result.dup_of,
                "unique_test_id": result.unique_test_id,
                "stratum_key": stratum_key(rec),
            }
        )
    return rows


def write_csv(path: Path, results: Sequence[LintResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(csv_rows(results))


def _bucket() -> dict[str, int]:
    return {"total": 0, "eligible": 0, KEEP: 0, REWRITE: 0, DROP: 0, "metadata_only": 0}


def summarize(
    results: Sequence[LintResult], *, commit: str, extra: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    overall = _bucket()
    by_area: dict[str, dict[str, int]] = defaultdict(_bucket)
    by_family: dict[str, dict[str, Any]] = {}
    reason_counts: Counter[str] = Counter()
    by_stratum: dict[str, dict[str, int]] = defaultdict(_bucket)
    for result in results:
        rec = result.record
        disposition = result.disposition
        metadata_only = disposition == REWRITE and set(result.reasons) <= METADATA_ONLY_REASONS
        fam = by_family.setdefault(rec.family, {"area": rec.area, **_bucket()})
        for bucket in (overall, by_area[rec.area or "(none)"], fam, by_stratum[stratum_key(rec)]):
            bucket["total"] += 1
            bucket[disposition] += 1
            bucket["eligible"] += disposition == KEEP
            bucket["metadata_only"] += metadata_only
        reason_counts.update(result.reasons)
    summary: dict[str, Any] = {
        "spec_version": SPEC_VERSION,
        "spec_doc": SPEC_DOC,
        "registry_commit": commit,
        "registry_source": (
            "api.full_scan_worker.load_default_full_scan_registry_rows() -> "
            "tools.active_test_registry.build_registry() filtered by "
            "_is_countable_active_executable"
        ),
        "eligible_unique_count": overall["eligible"],
        "eligible_unique_if_metadata_repaired": overall["eligible"] + overall["metadata_only"],
        "overall": overall,
        "reason_counts": dict(sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_area": {k: by_area[k] for k in sorted(by_area)},
        "by_family": {k: by_family[k] for k in sorted(by_family)},
        "by_stratum": {k: by_stratum[k] for k in sorted(by_stratum)},
        "notes": {
            "metadata_only": (
                "rewrite records whose only reasons are in "
                + ", ".join(sorted(METADATA_ONLY_REASONS))
            ),
            "generated_variant": (
                "records carrying parent_unique_test_id; they count as their parent's unique test"
            ),
            "near_duplicate": f"word {SHINGLE_WORDS}-shingle Jaccard >= {NEAR_DUP_JACCARD} on normalized prompt text",
        },
    }
    if extra:
        summary.update(extra)
    return summary


# --- live extraction ---------------------------------------------------------

class _AllCapabilities(dict):
    """Capability manifest declaring every surface, so no gate short-circuits."""

    def __contains__(self, key: object) -> bool:  # noqa: D401
        return True

    def __getitem__(self, key: Any) -> bool:
        return True

    def get(self, key: Any, default: Any = None) -> bool:
        return True


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _walk(obj: Any):
    if isinstance(obj, Mapping):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value)


_ID_KEYS = ("id", "case_id", "probe_id", "stable_id", "stable_case_id")
_SOURCE_KEYS = (
    "source", "provenance", "provenance_source", "github_provenance", "source_url",
    "upstream_eval", "probe_source", "ground_truth_source", "source_ids", "sources",
)
_PARENT_KEYS = ("parent_unique_test_id",)
_LICENCE_KEYS = ("licence", "license", "source_license", "source_licence", "spdx", "license_spdx")


def _first_str(mapping: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = mapping.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (str, int, float)):
            return str(value)
        if isinstance(value, Mapping):
            inner = _first_str(value, ("path", "url", "name", "dataset", "ticket", "source"))
            if inner:
                return inner
        return json.dumps(value, sort_keys=True, ensure_ascii=False)[:120]
    return ""


def _find_probe(data: Any, case_id: str) -> Mapping[str, Any] | None:
    for node in _walk(data):
        if any(str(node.get(key)) == case_id for key in _ID_KEYS if key in node):
            return node
    return None


_YAML_CACHE: dict[str, Any] = {}
_TEXT_CACHE: dict[str, str] = {}


def _source_file(rel: str) -> tuple[Any, str]:
    if rel not in _YAML_CACHE:
        path = REPO_ROOT / rel
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        data: Any = None
        if path.suffix in {".yaml", ".yml"} and text:
            import yaml

            try:
                data = yaml.safe_load(text)
            except Exception:
                data = None
        _YAML_CACHE[rel], _TEXT_CACHE[rel] = data, text
    return _YAML_CACHE[rel], _TEXT_CACHE[rel]


def _authored_candidate_text(turns: Sequence[Any]) -> tuple[tuple[str, ...], bool]:
    """Authored user text of a content-candidate row, before runner rendering."""

    texts: list[str] = []
    structured = False
    for turn in turns:
        if isinstance(turn, str):
            texts.append(turn)
            continue
        if not isinstance(turn, Mapping):
            texts.append(json.dumps(turn, sort_keys=True, ensure_ascii=False))
            structured = True
            continue
        if "role" in turn and "content" in turn:
            if str(turn["role"]).strip().casefold() in {"user", "human"}:
                texts.append(str(turn["content"]))
            continue
        pair_keys = [k for k in ("pair_a", "pair_b", "prompt_a", "prompt_b") if k in turn]
        if pair_keys:
            texts.extend(str(turn[k]) for k in pair_keys)
            continue
        if "user_turns_a" in turn:
            for key in ("user_turns_a", "user_turns_b"):
                value = turn.get(key)
                texts.extend(str(v) for v in (value if isinstance(value, list) else [value]))
            if turn.get("final_query"):
                texts.append(str(turn["final_query"]))
            continue
        hit = False
        for key in ("prompt", "prompt_text", "prior_turn", "query", "payload", "fixture", "instruction"):
            if turn.get(key):
                texts.append(str(turn[key]))
                hit = True
        if not hit:
            texts.append(json.dumps(turn, sort_keys=True, ensure_ascii=False))
            structured = True
    return tuple(texts), structured


def _grading_from_oracle(oracle: Mapping[str, Any]) -> str:
    kind = str(oracle.get("type") or "")
    if not kind:
        return ""
    body = {k: v for k, v in oracle.items() if k not in {"type", "na_rule"}}
    if kind == "pinned_llm_judge":
        spec = oracle.get("spec") if isinstance(oracle.get("spec"), Mapping) else {}
        judge = spec.get("judge") if isinstance(spec.get("judge"), Mapping) else {}
        model = judge.get("model")
        if model and judge.get("temperature") is not None:
            return f"pinned_llm_judge:{model}"
        policy = str(spec.get("judge_policy") or "")
        if policy and _judge_policy_resolves(policy):
            return f"pinned_llm_judge:ref:{policy}"
        return ""
    return f"deterministic:{kind}" if body else ""


def _judge_policy_resolves(ref: str) -> bool:
    """``path#key`` reference to a judge block that exists in the repo."""

    rel, _, key = ref.partition("#")
    data, _text = _source_file(rel)
    return isinstance(data, Mapping) and bool(key) and bool(data.get(key))


def _mapping_grading(outcome: Mapping[str, Any]) -> str:
    judge = outcome.get("judge")
    expected = outcome.get("expected")
    kind = str(judge.get("type") or "") if isinstance(judge, Mapping) else ""
    if not expected and not kind:
        return ""
    if not kind:
        from tools.registered_case_executor import DEFAULT_JUDGE_MODEL

        kind = f"pinned_llm_judge:{DEFAULT_JUDGE_MODEL}"
    return kind


def _dry_run(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Send every row through the production dispatcher with stub runners."""

    from harness.runner import MockRunner
    from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget

    sent: list[str] = []

    def target(prompt: str) -> str:
        sent.append(prompt)
        return "I can't help with that request."

    judge = MockRunner(
        "lint-judge",
        lambda _p: json.dumps({"verdict": "pass", "score": 1.0, "reason": "dry-run", "unmet_criteria": []}),
    )
    out: dict[tuple[str, str], dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="bb-unique-test-lint-") as tmp:
        budget = ScanBudget(
            max_target_calls=10**7, max_judge_calls=10**7, max_estimated_judge_cost_usd=10**6,
            max_wall_clock_seconds=10**6, judge_input_usd_per_million_tokens=0.0,
            judge_output_usd_per_million_tokens=0.0, judge_max_output_tokens_per_call=256,
        )
        executor = RegisteredCaseExecutor(
            subject=MockRunner("lint-target", target), judge=judge,
            evidence_store=EvidenceStore(Path(tmp)), budget=budget,
        )
        for row in rows:
            test_id, case_id = str(row["test_id"]), str(row["case_id"])
            execution_row = {**row, "capability_manifest": _AllCapabilities(), "scan_seed": 0}
            if test_id == "insecure_code_generation":
                # The per-scan gate; an all-capabilities manifest opens it.
                execution_row["applicability_gate"] = {**dict(row.get("applicability_gate") or {}), "applicable": True}
            sent.clear()
            record: dict[str, Any] = {}
            try:
                outcome = executor._execute_case(test_id, case_id, execution_row)
                record["outcome"] = outcome
                record["verdict"] = str(outcome.get("verdict") or "")
            except Exception as exc:  # recorded, never raised: the audit reports it
                record["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            record["sent"] = list(sent)
            out[(test_id, case_id)] = record
    return out


def _tau_prompts(case_id: str) -> tuple[str, ...]:
    from evals.provisional_tau_support_crm import SCENARIOS

    for scenario in SCENARIOS:
        if scenario.stable_id == case_id:
            return tuple(str(t) for t in scenario.user_turns)
    return ()


def _mapping_meta(row: Mapping[str, Any], case_id: str) -> dict[str, Any]:
    """Source/licence/severity/applicability recorded in a row's source pack."""

    rel = str(row.get("source_pack") or "")
    data, text = _source_file(rel) if rel else (None, "")
    probe = _find_probe(data, case_id) if data is not None else None
    top = data if isinstance(data, Mapping) else {}
    source = _first_str(probe or {}, _SOURCE_KEYS) or _first_str(top, _SOURCE_KEYS)
    if not source and isinstance(top.get("execution_evidence"), Mapping):
        source = _first_str(top["execution_evidence"], ("github_provenance",))
    if source and not source.startswith(rel):
        source = f"{rel}:{source}"
    return {
        "source": source,
        "licence": _first_str(probe or {}, _LICENCE_KEYS) or _first_str(top, _LICENCE_KEYS),
        # Runtime ``severity`` wins; ``audit_severity`` is a registry-only
        # label that no adapter reads (BEAAA-38921, CTO option B), so it can
        # be filled without changing replicate counts.
        "severity": str(
            (probe or {}).get("severity")
            or top.get("severity")
            or (probe or {}).get("audit_severity")
            or top.get("audit_severity")
            or ""
        ),
        "declared": bool(top.get("applicability") or top.get("applicability_gate")),
        "parent": _first_str(probe or {}, _PARENT_KEYS),
        "stratum": _first_str(probe or {}, ("stratum",)) or _first_str(top, ("stratum",)),
        "source_text": text,
    }


def build_record(row: Mapping[str, Any], dry: Mapping[str, Any]) -> LintRecord:
    from tools.registered_case_executor import _declared_applicability_gate

    test_id, case_id = str(row["test_id"]), str(row["case_id"])
    outcome = dry.get("outcome") or {}
    sent = tuple(dry.get("sent") or ())
    error = str(dry.get("error") or "")
    runtime_gate = tuple(str(c) for c in (_declared_applicability_gate(test_id) or ()))
    scored = row.get("reporting_status") == "scored" or row.get("scoring_active") is True
    meta = _mapping_meta(row, case_id)
    tau = test_id == "tau_support_crm" or row.get("runner") == "tau_bench"

    if row.get("content_candidate_inventory") is True:
        from tools.content_candidate_inventory import candidate_inventory_index

        inv = candidate_inventory_index()[(test_id, case_id)]
        prompts, structured = _authored_candidate_text(inv.get("prompt_turn_sequence") or [])
        prov = inv.get("provenance") if isinstance(inv.get("provenance"), Mapping) else {}
        source = ""
        origin = next(
            (str(prov[k]) for k in ("source", "path", "source_path", "source_object") if prov.get(k)),
            "",
        )
        if origin:
            source = ":".join(
                [origin] + [str(prov[k]) for k in ("object_locator", "ticket", "spec_ref") if prov.get(k)]
            )
        licence = _first_str(prov, _LICENCE_KEYS)
        severity = ""
        for node in _walk(inv.get("expected_observable_behavior")):
            severity = str(node.get("severity") or "") or severity
        app = inv.get("applicability") if isinstance(inv.get("applicability"), Mapping) else {}
        required = str(app.get("required_capability") or "")
        if required == test_id:
            # A capability named after the family itself gates nothing beyond
            # "this family is enabled"; it is not a surface requirement.
            required = ""
        caps = tuple(dict.fromkeys(([required] if required else []) + list(runtime_gate)))
        declared = bool(app.get("status")) or bool(runtime_gate) or meta["declared"]
        enforced = not required or required in runtime_gate
        grading = _grading_from_oracle(inv.get("deterministic_oracle") or {})
        # A graduated row also lives in a mapping pack; fill gaps from it.
        # Inventory rows often carry no ``source_pack``; their provenance
        # path names the family file instead.
        if not (meta["licence"] and meta["severity"]) and origin.endswith((".yaml", ".yml")):
            meta = _mapping_meta({"source_pack": origin}, case_id)
        source = source or meta["source"]
        licence = licence or meta["licence"]
        severity = severity or meta["severity"]
        source_text = ""
    else:
        prompts, structured = (), False
        source, licence, severity = meta["source"], meta["licence"], meta["severity"]
        caps = runtime_gate
        declared = meta["declared"] or bool(runtime_gate)
        enforced = True
        source_text = meta["source_text"]
        if tau:
            prompts = _tau_prompts(case_id)
            grading = "tau_bench_database_end_state"
        else:
            grading = _mapping_grading(outcome) if outcome else ""
            if not grading and error and sent:
                # The adapter sent the prompt and failed only on the stub's
                # plain-text reply (lane-C expects a JSON reply contract).
                grading = f"runner_contract:{test_id}"

    inv_row = inv if row.get("content_candidate_inventory") is True else {}
    parent = _first_str(inv_row, _PARENT_KEYS) or meta["parent"]
    stratum = _first_str(inv_row, ("stratum",)) or meta["stratum"]
    payloads = tuple(str(p) for p in (outcome.get("payloads") or ())) if outcome else ()
    delivered = sent or payloads
    if not prompts:
        prompts = delivered
    target_exercised = bool(sent) or tau
    executable = bool(sent) or tau or (bool(outcome) and not error)
    return LintRecord(
        family=test_id,
        case_id=case_id,
        area=str(row.get("pillar") or ""),
        source=source,
        licence=licence,
        severity=severity,
        capabilities=caps,
        capability_declared=declared,
        capability_enforced=enforced,
        grading_rule=grading,
        prompts=tuple(prompts),
        delivered_prompts=tuple(delivered),
        executable=executable,
        target_exercised=target_exercised,
        structured_spec_prompt=structured,
        scored=scored,
        source_text=source_text,
        note=error,
        parent_unique_test_id=parent,
        stratum=stratum,
    )


def extract_live_records() -> list[LintRecord]:
    from api.full_scan_worker import load_default_full_scan_registry_rows

    rows = load_default_full_scan_registry_rows()
    dry = _dry_run(rows)
    return [build_record(row, dry[(str(row["test_id"]), str(row["case_id"]))]) for row in rows]


def run(out_dir: Path, *, commit: str | None = None) -> dict[str, Any]:
    records = extract_live_records()
    results = lint_records(records)
    csv_path = out_dir / "unique-test-audit.csv"
    write_csv(csv_path, results)
    csv_sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    summary = summarize(
        results,
        commit=commit or _git_head(),
        extra={"csv": str(csv_path.relative_to(REPO_ROOT)) if csv_path.is_relative_to(REPO_ROOT) else str(csv_path), "csv_sha256": csv_sha},
    )
    (out_dir / "unique-test-audit-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "artifacts" / "beaaa-38921")
    parser.add_argument("--commit", default=None, help="registry commit to stamp (default: git HEAD)")
    args = parser.parse_args(argv)
    summary = run(args.out_dir.resolve(), commit=args.commit)
    print(json.dumps({k: summary[k] for k in ("spec_version", "registry_commit", "eligible_unique_count", "eligible_unique_if_metadata_repaired", "overall")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
