"""Deterministic probe paraphrase selection for anti-gaming scans.

The template bank is pinned corpus data. Scan-time selection is a pure function
of (probe_id, scan_seed, bank_version), so replay remains reproducible and no
live LLM is called while scoring.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

DEFAULT_BANK_PATH = (
    Path(__file__).resolve().parents[1]
    / "corpus"
    / "paraphrase"
    / "templates_v0_1.yaml"
)

_SLOT_RE = re.compile(r"{([A-Za-z_][A-Za-z0-9_]*)}")
_TRUE = {"1", "true", "yes", "on", "paraphrase", "paraphrases"}


@dataclass(frozen=True)
class ParaphraseTemplate:
    id: str
    template: str


@dataclass(frozen=True)
class TemplateBank:
    bank_id: str
    version: str
    semantic_invariance_band: float
    surface_diversity_threshold: float
    families: dict[str, tuple[ParaphraseTemplate, ...]]

    def templates_for(self, family: str) -> tuple[ParaphraseTemplate, ...]:
        if family in self.families:
            return self.families[family]
        return self.families["generic"]


@dataclass(frozen=True)
class ParaphrasedProbe:
    probe_id: str
    scan_seed: int | str
    family: str
    canonical: str
    text: str
    template_id: str | None
    bank_version: str | None
    enabled: bool


def load_template_bank(path: str | Path = DEFAULT_BANK_PATH) -> TemplateBank:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    families: dict[str, tuple[ParaphraseTemplate, ...]] = {}
    for family, entries in data["families"].items():
        families[family] = tuple(
            ParaphraseTemplate(id=str(entry["id"]), template=str(entry["template"]))
            for entry in entries
        )
    if "generic" not in families:
        raise ValueError("template bank must define a generic family")
    return TemplateBank(
        bank_id=str(data["bank_id"]),
        version=str(data["version"]),
        semantic_invariance_band=float(data["semantic_invariance_band"]),
        surface_diversity_threshold=float(data["surface_diversity_threshold"]),
        families=families,
    )


def paraphrase_enabled(env: dict[str, str] | None = None) -> bool:
    env = env or os.environ
    dedicated = env.get("ARE_PROBE_PARAPHRASE_RANDOMIZATION", "").strip().lower()
    if dedicated in _TRUE:
        return True
    sampling = env.get("ARE_PROBE_SAMPLING", "").strip().lower()
    return any(token.strip() in _TRUE for token in sampling.split(","))


def render_template(template: ParaphraseTemplate, slots: dict[str, Any]) -> str:
    missing = sorted(set(_SLOT_RE.findall(template.template)) - set(slots))
    if missing:
        raise ValueError(f"{template.id} missing slot(s): {', '.join(missing)}")
    return template.template.format(**{k: str(v) for k, v in slots.items()})


def _selection_index(
    *,
    probe_id: str,
    scan_seed: int | str,
    bank_version: str,
    n_templates: int,
) -> int:
    if n_templates < 1:
        raise ValueError("template family must contain at least one template")
    key = f"{probe_id}\0{scan_seed}\0{bank_version}".encode("utf-8")
    digest = hashlib.sha256(key).digest()
    return int.from_bytes(digest[:8], "big") % n_templates


def paraphrase_probe(
    *,
    probe_id: str,
    scan_seed: int | str,
    canonical: str,
    slots: dict[str, Any] | None = None,
    family: str = "generic",
    enabled: bool | None = None,
    bank: TemplateBank | None = None,
) -> ParaphrasedProbe:
    if enabled is None:
        enabled = paraphrase_enabled()
    if not enabled:
        return ParaphrasedProbe(
            probe_id=probe_id,
            scan_seed=scan_seed,
            family=family,
            canonical=canonical,
            text=canonical,
            template_id=None,
            bank_version=None,
            enabled=False,
        )

    bank = bank or load_template_bank()
    family_templates = bank.templates_for(family)
    idx = _selection_index(
        probe_id=probe_id,
        scan_seed=scan_seed,
        bank_version=bank.version,
        n_templates=len(family_templates),
    )
    selected = family_templates[idx]
    render_slots = {"canonical": canonical}
    if slots:
        render_slots.update(slots)
    return ParaphrasedProbe(
        probe_id=probe_id,
        scan_seed=scan_seed,
        family=family,
        canonical=canonical,
        text=render_template(selected, render_slots),
        template_id=selected.id,
        bank_version=bank.version,
        enabled=True,
    )


def render_all_paraphrases(
    *,
    canonical: str,
    slots: dict[str, Any] | None = None,
    family: str = "generic",
    bank: TemplateBank | None = None,
) -> list[tuple[str, str]]:
    bank = bank or load_template_bank()
    render_slots = {"canonical": canonical}
    if slots:
        render_slots.update(slots)
    return [
        (template.id, render_template(template, render_slots))
        for template in bank.templates_for(family)
    ]


def surface_diversity_metric(texts: Iterable[str]) -> float:
    normalized = [re.sub(r"\s+", " ", t.strip()).lower() for t in texts]
    if not normalized:
        return 0.0
    return len(set(normalized)) / len(normalized)


def paraphrase_sets_differ(
    previous_texts: Iterable[str],
    current_texts: Iterable[str],
) -> bool:
    previous = {re.sub(r"\s+", " ", t.strip()).lower() for t in previous_texts}
    current = {re.sub(r"\s+", " ", t.strip()).lower() for t in current_texts}
    return previous != current


def _same_verdict(a: Any, b: Any, *, band: float) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= band
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return False
        return all(_same_verdict(a[k], b[k], band=band) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        return all(_same_verdict(x, y, band=band) for x, y in zip(a, b))
    return a == b


def semantic_invariance_guard(
    *,
    canonical: str,
    verdict_fn: Callable[[str], Any],
    slots: dict[str, Any] | None = None,
    family: str = "generic",
    bank: TemplateBank | None = None,
    band: float | None = None,
) -> dict[str, Any]:
    bank = bank or load_template_bank()
    band = bank.semantic_invariance_band if band is None else band
    canonical_verdict = verdict_fn(canonical)
    checks = []
    for template_id, text in render_all_paraphrases(
        canonical=canonical,
        slots=slots,
        family=family,
        bank=bank,
    ):
        verdict = verdict_fn(text)
        passed = _same_verdict(canonical_verdict, verdict, band=band)
        checks.append({
            "template_id": template_id,
            "text": text,
            "verdict": verdict,
            "passed": passed,
        })
    return {
        "passed": all(c["passed"] for c in checks),
        "canonical_verdict": canonical_verdict,
        "band": band,
        "checks": checks,
    }
