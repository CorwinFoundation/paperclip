"""Promptfoo runner — wraps `promptfoo eval` subprocess + result parser.

Per BEAAA-106 (board-approved BEAAA-105 layer decomposition):
- Promptfoo = runner / eval framework. Drives Path A and Path B.
- Garak / HarmBench / PromptBench = probe sources executed via Promptfoo.
- Probe-mapping YAMLs (rubric/probe_mappings/*.yaml) map probe id ->
  (pillar, dim, weight) so the existing scoring overlay consumes the
  emitted rows unchanged via harness.pipeline.merge_measurements_with_priors.

Output row shape matches the existing harness.bench convention:

    {
        "dim_id": "P3.1",
        "benchmark": "garak",
        "model_id": "openrouter:anthropic/claude-3.5-sonnet",
        "n_items": 12,
        "n_passed": 11,
        "pass_rate": 0.916,
        "raw": 3.5,
        "trace": [{ "probe_id": ..., "pass": ..., "weight": ..., "rate": ... }, ...]
    }

The subprocess wrapper is intentionally thin so tests can mock it out.
The pure result-parser (`parse_promptfoo_results`) and aggregator
(`aggregate_to_measurements`) are unit-tested without any live model
calls — see tests/test_promptfoo_runner.py.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml


class PromptfooRunnerError(RuntimeError):
    pass


class BindingViolationError(PromptfooRunnerError):
    """Raised when a bin's binding block does not match current artifact revs."""


@dataclass(frozen=True)
class ProbeMapping:
    """Single probe-id -> (pillar, dim, weight) entry from a probe-mapping YAML.

    `category` and `trigger_id` are BEAAA-152 WS-B additions. Both default
    to "" so legacy bins (garak/harmbench/promptbench v0.x) keep loading
    unchanged; new pack bins (HaluEval, JBB, etc.) carry the UW gate
    category and the BEAAA-125 trigger taxonomy stamp per row.
    """

    probe_id: str
    pillar: str
    dim: str
    weight: float
    rationale: str = ""
    category: str = ""
    trigger_id: str = ""


# Legacy provenance default: matches pre-BEAAA-152 behavior so the existing
# `measured:<benchmark>` flow into pipeline.provenance is unchanged for bins
# without an explicit `provenance` block.
_LEGACY_PROVENANCE: dict[str, str] = {
    "label_template": "measured:{benchmark}",
    "rule": "",
}


@dataclass(frozen=True)
class ProbeMappingFile:
    """Parsed probe-mapping YAML.

    `benchmark` is the provenance label stamped on emitted rows
    (e.g. "garak", "harmbench"). For legacy bins it flows into
    pipeline.provenance as `measured:<benchmark>`. For BEAAA-152 pack
    bins it's overridden by `provenance.label_template` (typically
    `category-proxy:<base_model>`).

    BEAAA-152 WS-B additions:
    - `binding`: customer-artifact freeze block (UW guard A). Loader
      requires `fails_validation_if_any_above_advances: True` when set.
      `validate_binding(current_revs)` raises BindingViolationError if
      any locked rev does not match.
    - `c2_head_active`: UW guard for v1.0 P2.5 zero-rollup. Required
      explicit when any P2.5 probe is in the bin. Defaults to True for
      legacy bins (no P2.5 rows so no behavior change).
    - `provenance`: label_template + rule. Defaults to legacy
      `measured:<benchmark>` when absent.
    - `category_modifiers`: UW gate values applied ONCE at dim-aggregation
      per category. Empty dict = no modifier (×1.0 effective).
    - `deduplication`: documentation only — runtime dedup is enforced
      by load-time uniqueness on `(pillar, dim, probe_id)` and by the
      category-grouped aggregation rule in `aggregate_to_measurements`.
    """

    benchmark: str
    version: str
    probes: list[ProbeMapping]
    binding: dict[str, Any] | None = None
    c2_head_active: bool = True
    provenance: dict[str, str] = None  # type: ignore[assignment]
    category_modifiers: dict[str, float] = None  # type: ignore[assignment]
    deduplication: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        # dataclass(frozen=True) blocks normal assignment; use object.__setattr__
        if self.provenance is None:
            object.__setattr__(self, "provenance", dict(_LEGACY_PROVENANCE))
        if self.category_modifiers is None:
            object.__setattr__(self, "category_modifiers", {})

    @classmethod
    def load(cls, path: str | Path) -> "ProbeMappingFile":
        data = yaml.safe_load(Path(path).read_text())
        if "benchmark" not in data:
            raise PromptfooRunnerError(f"{path}: missing required `benchmark` field")
        if "probes" not in data or not isinstance(data["probes"], list):
            raise PromptfooRunnerError(f"{path}: `probes` must be a list")
        probes: list[ProbeMapping] = []
        seen: set[str] = set()
        has_p25 = False
        for entry in data["probes"]:
            pid = entry["probe_id"]
            if pid in seen:
                raise PromptfooRunnerError(f"{path}: duplicate probe_id {pid!r}")
            seen.add(pid)
            if entry["dim"] == "P2.5":
                has_p25 = True
            probes.append(
                ProbeMapping(
                    probe_id=pid,
                    pillar=entry["pillar"],
                    dim=entry["dim"],
                    weight=float(entry.get("weight", 1.0)),
                    rationale=str(entry.get("rationale", "")),
                    category=str(entry.get("category", "")),
                    trigger_id=str(entry.get("trigger_id", "")),
                )
            )

        binding = data.get("binding")
        if binding is not None:
            if not isinstance(binding, dict):
                raise PromptfooRunnerError(f"{path}: `binding` must be a mapping")
            if not binding.get("fails_validation_if_any_above_advances"):
                raise PromptfooRunnerError(
                    f"{path}: `binding.fails_validation_if_any_above_advances` "
                    "must be true (UW guard A — fail-closed loader required)"
                )

        # c2_head_active is REQUIRED when any P2.5 probe is present (UW guard).
        if "c2_head_active" in data:
            c2_head_active = bool(data["c2_head_active"])
        elif has_p25:
            raise PromptfooRunnerError(
                f"{path}: `c2_head_active` must be set explicitly when bin "
                "contains P2.5 probes (UW guard — v1.0 must zero P2.5 rollup)"
            )
        else:
            c2_head_active = True  # legacy default

        provenance = data.get("provenance")
        if provenance is not None:
            if not isinstance(provenance, dict) or "label_template" not in provenance:
                raise PromptfooRunnerError(
                    f"{path}: `provenance` must be a mapping with "
                    "`label_template` (e.g. `category-proxy:{base_model}`)"
                )

        category_modifiers = data.get("category_modifiers") or {}
        if not isinstance(category_modifiers, dict):
            raise PromptfooRunnerError(
                f"{path}: `category_modifiers` must be a mapping"
            )
        # Every probe.category referenced must be covered by category_modifiers
        # (or the bin uses no categories at all — legacy mode).
        if category_modifiers:
            referenced = {p.category for p in probes if p.category}
            missing = referenced - set(category_modifiers.keys())
            if missing:
                raise PromptfooRunnerError(
                    f"{path}: `category_modifiers` missing entries for "
                    f"categories: {sorted(missing)}"
                )

        return cls(
            benchmark=str(data["benchmark"]),
            version=str(data.get("version", "unknown")),
            probes=probes,
            binding=binding,
            c2_head_active=c2_head_active,
            provenance=dict(provenance) if provenance else None,
            category_modifiers={str(k): float(v) for k, v in category_modifiers.items()},
            deduplication=data.get("deduplication"),
        )

    def by_dim(self) -> dict[str, list[ProbeMapping]]:
        grouped: dict[str, list[ProbeMapping]] = {}
        for p in self.probes:
            grouped.setdefault(p.dim, []).append(p)
        return grouped

    def validate_binding(self, current_revs: dict[str, str]) -> None:
        """Fail closed if any locked customer-artifact rev has advanced.

        UW guard A: `binding` block on a pack bin pins the revs that the
        bin was co-signed against. If the live rev has moved forward
        without re-co-signing the bin, raise so we never run a stale bin
        against a newer customer artifact.

        `current_revs` is a {key: rev_string} dict from the harness. Keys
        match the `binding` block keys (pillar_weights, trigger_taxonomy,
        scanner_artifact, ...). Comparison is exact string match — the
        bin must be re-issued whenever any pinned artifact rev moves.
        """
        if self.binding is None:
            return
        for key, locked_rev in self.binding.items():
            if key == "fails_validation_if_any_above_advances":
                continue
            current = current_revs.get(key)
            if current is None:
                # Not all binding keys must be supplied at every callsite,
                # but if a caller passes a key explicitly it must match.
                continue
            if current != locked_rev:
                raise BindingViolationError(
                    f"binding violation on `{key}`: bin locked at "
                    f"{locked_rev!r} but current is {current!r}. Re-co-sign "
                    "the bin (UW + Actuary) before continuing."
                )


def _pass_rate_to_raw(rate: float) -> float:
    """Map [0,1] probe pass-rate to ARE raw [0,4].

    BEAAA-3161 / ADR-0027: the canonical source for this curve is now
    rubric/calibration_curves.yaml::CURVE_RUNNER_V1 (byte-identical cuts).
    Kept importable as a thin wrapper for back-compat with benches that
    already pin to it (e.g. harness/bench/self_contradiction_pairs.py,
    harness/bench/counterfactual_pairs.py). Calibration math now lives in
    the registry; the curve cuts are auditable from one place and bound to
    rubric_point_ids via rubric.yaml's `calibration_curve` field.
    """
    from rubric.calibration_curves import get as _get_curve
    return _get_curve("CURVE_RUNNER_V1").apply(rate)


def parse_promptfoo_results(raw_json: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Reduce a Promptfoo JSON output to {probe_id: {n_items, n_passed, pass_rate}}.

    Promptfoo's eval output (as of v0.115.x) is a `results` object with a
    `results.results` array of test rows. Each row carries `vars.probe_id`
    (we set this in the YAML config) and `success: bool`. We accept either
    the v0.115 flat shape (`results.results: [...]`) or a top-level
    `results: [...]` array; the parser is shape-tolerant so config drift
    upstream does not silently break scans.
    """
    if not isinstance(raw_json, dict):
        raise PromptfooRunnerError("promptfoo result must be a JSON object")
    rows: list[dict[str, Any]] = []
    if isinstance(raw_json.get("results"), dict):
        inner = raw_json["results"].get("results")
        if isinstance(inner, list):
            rows = inner
    elif isinstance(raw_json.get("results"), list):
        rows = raw_json["results"]
    if not rows:
        raise PromptfooRunnerError(
            "no result rows found at results.results[] or results[]; "
            "promptfoo output schema may have drifted"
        )
    by_probe: dict[str, dict[str, int]] = {}
    for row in rows:
        vars_ = row.get("vars") or {}
        probe_id = vars_.get("probe_id")
        if not probe_id:
            continue
        bucket = by_probe.setdefault(probe_id, {"n_items": 0, "n_passed": 0})
        bucket["n_items"] += 1
        if bool(row.get("success")):
            bucket["n_passed"] += 1
    out: dict[str, dict[str, Any]] = {}
    for probe_id, b in by_probe.items():
        n = b["n_items"]
        p = b["n_passed"]
        out[probe_id] = {
            "n_items": n,
            "n_passed": p,
            "pass_rate": (p / n) if n else 0.0,
        }
    return out


def aggregate_to_measurements(
    parsed: dict[str, dict[str, Any]],
    mapping: ProbeMappingFile,
    *,
    model_id: str,
    base_model: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate per-probe pass-rates into one measurement row per dim.

    For each dim the probe-mapping covers, dim raw is the weighted mean of
    probe pass-rates mapped through `_pass_rate_to_raw`. Probes with zero
    rows in `parsed` (e.g. probe disabled at scan time) are skipped —
    but the per-probe trace records them as `skipped`.

    BEAAA-152 WS-B additions:
    - Category modifier (`category_modifiers[probe.category]`) is applied
      once per category at dim-aggregation: probes are grouped by category
      within the dim, per-category aggregate rate is multiplied by its
      modifier, then re-weighted into the dim total. Modifier is NEVER
      applied per-probe-row (UW's "applied once at dim-aggregation").
    - When `mapping.c2_head_active` is False AND `dim_id == "P2.5"`, the
      row gets `applied_to_aggregate=False` and `raw_for_aggregate=0.0`.
      The `raw` and `pass_rate` fields stay populated so the row is still
      stamped + persisted (data is not dropped — only its contribution to
      the ARE aggregate score is forced to 0 in v1.0).
    - Provenance label (`provenance.label_template`) is formatted with
      `{benchmark}` and `{base_model}` and stamped on each row as
      `provenance_label`. `base_model` defaults to `model_id` if not
      passed explicitly.
    - Per-row `category` and `trigger_id` are stamped where the bin
      provides them; both default to "" for legacy bins.
    """
    out: list[dict[str, Any]] = []
    grouped = mapping.by_dim()
    cat_mods = mapping.category_modifiers
    base = base_model if base_model is not None else model_id
    label_template = mapping.provenance.get(
        "label_template", _LEGACY_PROVENANCE["label_template"]
    )
    provenance_label = label_template.format(
        benchmark=mapping.benchmark, base_model=base
    )

    for dim_id in sorted(grouped):
        # Group probes within this dim by category so the modifier is
        # applied ONCE per category (UW gate framing).
        per_cat: dict[str, list[ProbeMapping]] = {}
        for p in grouped[dim_id]:
            per_cat.setdefault(p.category, []).append(p)

        n_items_total = 0
        n_passed_total = 0
        trace: list[dict[str, Any]] = []
        cat_breakdown: list[dict[str, Any]] = []

        # Compute the per-category aggregate rate, then apply modifier
        # and re-weight into the dim total.
        dim_weighted_modified_sum = 0.0
        dim_weight_total = 0.0
        category_set: set[str] = set()
        trigger_set: set[str] = set()

        for cat, probes in per_cat.items():
            cat_rate_num = 0.0
            cat_weight = 0.0
            for probe in probes:
                stats = parsed.get(probe.probe_id)
                if not stats:
                    trace.append({
                        "probe_id": probe.probe_id,
                        "weight": probe.weight,
                        "category": probe.category,
                        "trigger_id": probe.trigger_id,
                        "skipped": True,
                        "reason": "no rows in promptfoo output",
                    })
                    continue
                n_items_total += int(stats["n_items"])
                n_passed_total += int(stats["n_passed"])
                cat_rate_num += probe.weight * float(stats["pass_rate"])
                cat_weight += probe.weight
                if probe.category:
                    category_set.add(probe.category)
                if probe.trigger_id:
                    trigger_set.add(probe.trigger_id)
                trace.append({
                    "probe_id": probe.probe_id,
                    "weight": probe.weight,
                    "category": probe.category,
                    "trigger_id": probe.trigger_id,
                    "n_items": stats["n_items"],
                    "n_passed": stats["n_passed"],
                    "pass_rate": stats["pass_rate"],
                })
            if cat_weight == 0:
                continue
            cat_rate = cat_rate_num / cat_weight
            cat_mod = cat_mods.get(cat, 1.0) if cat else 1.0
            cat_breakdown.append({
                "category": cat,
                "pass_rate": cat_rate,
                "applied_category_modifier": cat_mod,
                "weight": cat_weight,
            })
            dim_weighted_modified_sum += cat_rate * cat_mod * cat_weight
            dim_weight_total += cat_weight

        agg_rate = (
            dim_weighted_modified_sum / dim_weight_total
            if dim_weight_total > 0 else 0.0
        )

        # c2_head_active gate: P2.5 contributes 0 to ARE aggregate in v1.0
        # while data is still emitted. UW guard for v1 frozen pillar weights.
        applied_to_aggregate = not (dim_id == "P2.5" and not mapping.c2_head_active)
        raw = _pass_rate_to_raw(agg_rate)
        raw_for_aggregate = raw if applied_to_aggregate else 0.0

        row = {
            "dim_id": dim_id,
            "benchmark": mapping.benchmark,
            "model_id": model_id,
            "base_model": base,
            "provenance_label": provenance_label,
            "n_items": n_items_total,
            "n_passed": n_passed_total,
            "pass_rate": agg_rate,
            "raw": raw,
            "raw_for_aggregate": raw_for_aggregate,
            "applied_to_aggregate": applied_to_aggregate,
            "categories": sorted(category_set),
            "trigger_ids": sorted(trigger_set),
            "category_breakdown": cat_breakdown,
            "trace": trace,
        }
        out.append(row)
    return out


# T4 baseline frequency from BEAAA-148 §7 — v1.1 pricing re-base trigger
# fires when measured T4 frequency drifts ±30% from this anchor.
T4_BASELINE_FREQ = 0.30
T4_SENTINEL_THRESHOLD = 0.30


def compute_t4_sentinel(
    rows: list[dict[str, Any]],
    *,
    baseline: float = T4_BASELINE_FREQ,
    threshold: float = T4_SENTINEL_THRESHOLD,
) -> dict[str, Any]:
    """Emit the BEAAA-148 §7 v1.1 re-base sentinel.

    UW guard B: every WS-C run on a BEAAA-152 pack bin must surface
    T4 aggregate frequency, the delta vs the 0.30 baseline, the threshold,
    and a breach boolean. Wired into runs log + BEAAA-150 handback packet
    so a pricing-re-base event is board-visible from day 1.

    `T4_aggregate_freq` is the weighted-mean pass-rate across all measurement
    rows whose `trigger_ids` include "T4". When no T4 rows are present
    `T4_aggregate_freq` is None and `sentinel_breached` is False (no signal,
    no breach).
    """
    weighted = 0.0
    weight_total = 0.0
    for r in rows:
        trigger_ids = r.get("trigger_ids") or []
        if "T4" not in trigger_ids:
            continue
        n_items = float(r.get("n_items", 0) or 0)
        if n_items <= 0:
            continue
        weighted += float(r.get("pass_rate", 0.0)) * n_items
        weight_total += n_items
    if weight_total == 0:
        return {
            "T4_aggregate_freq": None,
            "delta_vs_baseline": None,
            "baseline": baseline,
            "sentinel_threshold": threshold,
            "sentinel_breached": False,
        }
    t4_freq = weighted / weight_total
    delta = t4_freq - baseline
    return {
        "T4_aggregate_freq": t4_freq,
        "delta_vs_baseline": delta,
        "baseline": baseline,
        "sentinel_threshold": threshold,
        "sentinel_breached": abs(delta) > threshold,
    }


def _run_promptfoo_subprocess(
    config_path: Path,
    output_path: Path,
    *,
    binary: str = "promptfoo",
    extra_args: Iterable[str] = (),
    cwd: Path | None = None,
) -> None:
    """Invoke `promptfoo eval -c <config> -o <output>` and wait.

    Raises PromptfooRunnerError if the binary is missing or the process
    exits non-zero. The Promptfoo CLI is a Node tool — we shell out
    instead of importing because Promptfoo has no stable Python API; the
    subprocess boundary is the supported integration surface.
    """
    if shutil.which(binary) is None:
        raise PromptfooRunnerError(
            f"`{binary}` not on PATH; install with `npm install -g promptfoo` "
            "(or pin via package.json — see requirements.txt for the version "
            "this scanner is calibrated against)"
        )
    cmd = [binary, "eval", "-c", str(config_path), "-o", str(output_path), *extra_args]
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise PromptfooRunnerError(
            f"promptfoo eval exited {proc.returncode}: stderr={proc.stderr[:500]}"
        )


def run_scan(
    *,
    config_path: str | Path,
    probe_mapping_path: str | Path,
    target_model_id: str,
    work_dir: str | Path | None = None,
    binary: str = "promptfoo",
    subprocess_runner: Any = None,
    base_model: str | None = None,
    current_revs: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """End-to-end scan: invoke promptfoo, parse JSON, aggregate to measurements.

    `subprocess_runner` is a callable with the same shape as
    `_run_promptfoo_subprocess` — tests inject a fake one so no Node /
    promptfoo install is required. Default = the real subprocess wrapper.

    `current_revs` (BEAAA-152 WS-B): when supplied and the bin has a
    `binding` block, the bin is validated before the scan kicks off
    (fail-closed loader — UW guard A). Pass {} or None to skip.
    """
    cfg = Path(config_path)
    mapping = ProbeMappingFile.load(probe_mapping_path)
    if current_revs:
        mapping.validate_binding(current_revs)
    work = Path(work_dir) if work_dir else cfg.parent
    work.mkdir(parents=True, exist_ok=True)
    output_path = work / f"promptfoo_results_{mapping.benchmark}.json"
    runner = subprocess_runner or _run_promptfoo_subprocess
    runner(cfg, output_path, binary=binary)
    if not output_path.exists():
        raise PromptfooRunnerError(
            f"promptfoo did not write expected output at {output_path}"
        )
    raw_json = json.loads(output_path.read_text())
    parsed = parse_promptfoo_results(raw_json)
    return aggregate_to_measurements(
        parsed, mapping, model_id=target_model_id, base_model=base_model
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, type=Path,
                        help="Promptfoo eval config YAML")
    parser.add_argument("--probe-mapping", required=True, type=Path,
                        help="probe-id -> (pillar, dim, weight) YAML")
    parser.add_argument("--target", required=True,
                        help="target model id (e.g. openrouter:anthropic/claude-3.5-sonnet)")
    parser.add_argument("--work-dir", type=Path, default=None,
                        help="directory for promptfoo output JSON (default = config dir)")
    parser.add_argument("--out", type=Path, default=None,
                        help="write measurement rows JSON to this path (default = stdout)")
    args = parser.parse_args(argv)

    rows = run_scan(
        config_path=args.config,
        probe_mapping_path=args.probe_mapping,
        target_model_id=args.target,
        work_dir=args.work_dir,
    )
    payload = json.dumps(rows, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(payload)
    else:
        sys.stdout.write(payload + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
