"""Hybrid pipeline — combine real benchmark measurements with directional
priors to produce a complete ARE raw_scores dict.

POC scope: only P1.1 has a real benchmark adapter. The other 20 dims fall
through to the prior. The output is a `(raw_scores, provenance)` tuple
where `provenance[dim_id]` is either "measured:<benchmark>" or "prior".

BEAAA-714: PyRIT P3.1 wire-up — `run_pyrit_p31` calls `pyrit_runner` for
P3.1 alongside the existing Garak path when the scan's requested_pillars
include ROBUSTNESS (P3). Aggregation reuses the canonical
`scanners.promptfoo_runner.aggregate_to_measurements` with the
`rubric/probe_mappings/pyrit.yaml` weights — weighted-mean over pyrit
probes → `_pass_rate_to_raw` byte-stable with Garak's P3.1 aggregation
(anti-fork "Wrap don't fork" per
contracts/scanner-implementation-supervision-v1.md@v1.0). Rule-execution
classification per supervision table row "Implement a new probe inside an
existing rubric_point_id" — no rubric weight / scoring math / judge model
change. Output rows append to the existing measurements list consumed by
`merge_measurements_with_priors`; the merge semantics stay byte-identical
to v0.3.0.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from harness.bench import bbq as _bbq
from harness.bench import donotanswer as _dna
from harness.runner import ModelRunner
from scanners import promptfoo_runner as _pf
from scanners.pyrit_runner import PyRITProbeItem, run_via_pyrit


REPO = Path(__file__).resolve().parents[1]
PYRIT_PROBE_MAPPING_PATH = REPO / "rubric" / "probe_mappings" / "pyrit.yaml"

# BEAAA-749: BIAS-FAIRNESS pack corpora pinned per probe_mappings YAMLs
# (corpus_version + sha256 stamped). Paths are absolute so the wiring works
# regardless of caller cwd. The bench modules own the load + judge calls;
# pipeline only owns the pillar gate + measurement-row reshape.
BBQ_CORPUS_PATH = REPO / "corpus" / "bias" / "bbq" / "bbq_subset_n200_seed42.jsonl"
BBQ_PROBE_MAPPING_PATH = REPO / "rubric" / "probe_mappings" / "bbq.yaml"
DONOTANSWER_CORPUS_PATH = (
    REPO / "corpus" / "bias" / "donotanswer" / "donotanswer_subset_n100_seed42.jsonl"
)
DONOTANSWER_PROBE_MAPPING_PATH = (
    REPO / "rubric" / "probe_mappings" / "donotanswer.yaml"
)

# BEAAA-714: pillar-name tokens that gate the PyRIT P3.1 path. The contract
# requested_pillars uses canonical pillar names (ROBUSTNESS); the ticket
# spec uses the internal "P3" shorthand. Both forms unlock the wiring so a
# caller passing either pillar identifier gets the same behaviour.
_P3_PILLAR_TOKENS = frozenset({"ROBUSTNESS", "P3"})

# BEAAA-749: pillar-name tokens that gate the BIAS-FAIRNESS pack
# (BBQ + DoNotAnswer). Same dual-token shape as the P3 gate above — callers
# may pass either the canonical pillar name (BIAS_FAIRNESS) or the internal
# shorthand (P2).
_P2_PILLAR_TOKENS = frozenset({"BIAS_FAIRNESS", "P2"})


def merge_measurements_with_priors(
    measurements: list[dict[str, Any]],
    priors: dict[str, float],
) -> tuple[dict[str, float], dict[str, str]]:
    raw_scores: dict[str, float] = dict(priors)
    provenance: dict[str, str] = {dim: "prior" for dim in priors}
    for m in measurements:
        dim = m["dim_id"]
        raw_scores[dim] = float(m["raw"])
        provenance[dim] = f"measured:{m['benchmark']}"
    return raw_scores, provenance


def load_priors(prior_path: str | Path) -> tuple[str, dict[str, float]]:
    data = json.loads(Path(prior_path).read_text())
    return data["display_name"], {k: float(v) for k, v in data["raw_scores"].items()}


def _pyrit_trace_to_parsed(
    trace: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Re-bin a `run_via_pyrit` trace into the canonical `parsed` shape.

    The canonical aggregator
    (`scanners.promptfoo_runner.aggregate_to_measurements`) consumes
    `{probe_id: {n_items, n_passed, pass_rate}}`. PyRIT's runner emits a
    flat per-item trace each carrying `probe_id` + `passed`; this grouper
    is the adapter so we do not fork the aggregator. Trace entries that
    lack a `probe_id` (defensive — should not happen for the in-tree
    runner) are skipped.
    """
    by_probe: dict[str, dict[str, int]] = {}
    for entry in trace:
        probe_id = entry.get("probe_id")
        if not probe_id:
            continue
        bucket = by_probe.setdefault(probe_id, {"n_items": 0, "n_passed": 0})
        bucket["n_items"] += 1
        if bool(entry.get("passed")):
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


def aggregate_pyrit_p31_measurement(
    pyrit_row: dict[str, Any],
    *,
    model_id: str,
    mapping_path: str | Path = PYRIT_PROBE_MAPPING_PATH,
) -> list[dict[str, Any]]:
    """Aggregate a PyRIT runner row into canonical P3.1 measurement rows.

    Reuses `scanners.promptfoo_runner.aggregate_to_measurements` with the
    `rubric/probe_mappings/pyrit.yaml` weights — weighted-mean per dim →
    canonical `_pass_rate_to_raw` curve. Byte-stable with Garak's P3.1
    aggregation at the same pass rate (the cuts in `_pass_rate_to_raw`
    and `harness.bench.prompt_injection._resistance_to_raw` are
    intentionally identical; pyrit_runner stamps the same curve onto
    `pyrit_row['raw']` so callers comparing the raw aggregator row to
    `pyrit_row` see no drift at full coverage).
    """
    parsed = _pyrit_trace_to_parsed(pyrit_row.get("trace", []))
    mapping = _pf.ProbeMappingFile.load(mapping_path)
    return _pf.aggregate_to_measurements(parsed, mapping, model_id=model_id)


def run_pyrit_p31(
    runner: ModelRunner,
    items: list[PyRITProbeItem],
    *,
    requested_pillars: Iterable[str] | None,
    mapping_path: str | Path = PYRIT_PROBE_MAPPING_PATH,
) -> list[dict[str, Any]]:
    """Run PyRIT for P3.1 when ROBUSTNESS is in `requested_pillars`.

    BEAAA-714 wire-up: invoked alongside the Garak P3.1 path so PyRIT
    pass-rates flow into the measurements list returned to
    `merge_measurements_with_priors`. Gating mirrors the API edge's
    `scanner_invocation_context.requested_pillars` field — when
    ROBUSTNESS (or its internal alias P3) is absent the call is a no-op
    and no PyRIT subprocess work is done.

    Returns an empty list (caller-additive) rather than raising when the
    pillar gate is closed so callers can unconditionally extend their
    measurements list.
    """
    if requested_pillars is None:
        return []
    if not _P3_PILLAR_TOKENS.intersection(requested_pillars):
        return []
    pyrit_row = run_via_pyrit(runner, items)
    return aggregate_pyrit_p31_measurement(
        pyrit_row, model_id=runner.model_id(), mapping_path=mapping_path
    )


def _bias_row_to_measurement(row: dict[str, Any]) -> dict[str, Any]:
    """Reshape a bench-module rich result into the canonical measurement row.

    `harness.bench.bbq.run` and `harness.bench.donotanswer.run` return the
    full probe artifact (trace, per-category stats, judge metadata). The
    canonical `merge_measurements_with_priors` shape is the minimal
    `{dim_id, raw, benchmark}` triple; the full row is kept for artifact
    persistence under `details[dim_id]`. Byte-stable with the pyrit-pattern
    aggregator output (`{dim_id, raw, benchmark, ...}` accepts extra keys).
    """
    return {
        "dim_id": row["dim_id"],
        "raw": float(row["raw"]),
        "benchmark": row["benchmark"],
    }


def run_bias_pack(
    runner: ModelRunner,
    *,
    judge_runner: ModelRunner,
    requested_pillars: Iterable[str] | None,
    bbq_corpus_path: str | Path = BBQ_CORPUS_PATH,
    donotanswer_corpus_path: str | Path = DONOTANSWER_CORPUS_PATH,
    ensemble_n: int = 3,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run BIAS-FAIRNESS pack (BBQ → P2.1, DoNotAnswer → P2.4).

    BEAAA-749 rule-execution: wraps two upstream eval corpora as measured
    probes on existing rubric dims (no net-new rubric point, no weight
    change). Gating mirrors `run_pyrit_p31` — when BIAS_FAIRNESS (or its
    internal alias P2) is absent from ``requested_pillars`` the call is a
    no-op and no judge spend is incurred. Returns ``(measurements, details)``:

      - ``measurements`` is the canonical list ready for
        ``merge_measurements_with_priors`` — last-write-wins on ``dim_id``
        means a caller that also runs ``p2_emit.emit_p2_measurements``
        gets the BBQ/DoNotAnswer rows in place of paired-attr rows when
        the bias pack runs after p2_emit in the measurements list. Empty
        list when the pillar gate is closed (caller-additive).
      - ``details`` carries the full bench-module rows keyed by ``dim_id``
        for artifact persistence + evidence_refs construction.

    ``judge_runner`` is a separate pinned-judge ModelRunner — DoNotAnswer
    needs a distinct judge from the subject agent so it can classify the
    subject's response. Per Supreme Law judge-pinning: temperature=0,
    seed=42, ensemble-N=3 (defaults baked into the bench modules; pass
    a different ``ensemble_n`` only when intentionally re-budgeting).
    """
    if requested_pillars is None:
        return [], {}
    if not _P2_PILLAR_TOKENS.intersection(requested_pillars):
        return [], {}

    bbq_items = _bbq.load_items(bbq_corpus_path)
    bbq_row = _bbq.run(runner, bbq_items, ensemble_n=ensemble_n)

    dna_items = _dna.load_items(donotanswer_corpus_path)
    dna_row = _dna.run(
        runner, dna_items, judge_runner=judge_runner, ensemble_n=ensemble_n,
    )

    measurements = [
        _bias_row_to_measurement(bbq_row),
        _bias_row_to_measurement(dna_row),
    ]
    details = {
        "P2.1": bbq_row,
        "P2.4": dna_row,
    }
    return measurements, details
