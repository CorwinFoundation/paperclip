"""BEAAA-35938 CAPTURE: measure the 120 ``hallucination_legal`` rows against the authorized target.

Budget, priced before any call went out and capped in code, not in a comment:
  120 rows x 5 seeds = 600 target calls on ``openai/gpt-4.1-mini`` (T=0.0).
  Judge calls are the billed line and they are NOT 600 x 3. The pinned judge runs
  at temperature 0 with a fixed seed, so judging the same response text twice is a
  paid re-derivation of a known answer. Verdicts are therefore cached on
  ``(probe_id, response_text)`` and the N=5 sweep pays only for the DISTINCT
  responses the target actually produced. Identical-across-seeds rows -- the common
  case at T=0 -- cost one ensemble, not five.

  The CEO's authorization is a hard cap of USD 2.00 for the whole slice, with
  "stop and report on crossing the cap, do not retry". ``BudgetGuard`` below
  enforces that against the ``usage`` OpenRouter returns on every response, so the
  ceiling binds on measured spend rather than on the 1,200-char response assumption
  the $0.38 estimate rested on. Crossing it raises and the partial capture is
  still written out.

The 120 rows are DERIVED from the BEAAA-18720 catalogue at the merged SHA, not
listed here: every ``hallucination_legal`` row in
``artifacts/beaaa-18720/unified-probe-inventory.jsonl`` whose ``provenance.path``
is the v0.3.0 family YAML. The 5 rows sourced from
``corpus/wave0/beaaa-15688-authored-scenarios.jsonl`` are EXCLUDED and reported
separately -- a different file at a different version is not this corpus, and
carrying them to round 125 up would be a provenance claim the artifact cannot
support.

Grading is NOT reimplemented here:
  - F2_case  -> ``harness.bench.hallucination_legal.judge_f2_case``, the adapter
    that already measures this family, cross-referencing the BEAAA-206
    citation_index. A second implementation would make any divergence
    unattributable (the lesson BEAAA-35915 wrote down).
  - F1/F3/F4 -> the judge rubric pinned IN the YAML, read from the YAML at run
    time and sent verbatim. Not retyped into this file, so it cannot drift from
    the mapping it is supposed to implement.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.bench.hallucination_legal import (  # noqa: E402
    F2CaseProbe,
    judge_f2_case,
    pass_rate_to_raw,
)
from harness.runner import ModelRunner, RunnerError  # noqa: E402
from harness.openrouter_pool import preflight as pool_preflight  # noqa: E402

FAMILY = "hallucination_legal"
CATALOGUE = ROOT / "artifacts" / "beaaa-18720" / "unified-probe-inventory.jsonl"
YAML_RELPATH = "rubric/probe_mappings/hallucination_legal.yaml"
WAVE0_RELPATH = "corpus/wave0/beaaa-15688-authored-scenarios.jsonl"
EXPECTED_YAML_ROWS = 120
EXPECTED_WAVE0_ROWS = 5
EXPECTED_ORACLE = "pinned_llm_judge"
PINNED_SOURCE_REVISION = "6e78dae48131f590cdd59fada023089c53a45236"
DEFAULT_SEEDS = [0, 1, 2, 3, 4]

# The CEO's hard cap on BEAAA-35938, 2026-09-25. Not advisory.
BUDGET_CAP_USD = 2.00

# OpenRouter per-million-token prices, used to turn the run's real token usage
# into measured cost. The judge id is the OpenRouter route for the YAML's pinned
# ``claude-haiku-4-5-20251001``.
PRICE_USD_PER_MTOK = {
    "openai/gpt-4.1-mini": {"prompt": 0.40, "completion": 1.60},
    "anthropic/claude-haiku-4.5": {"prompt": 1.00, "completion": 5.00},
}

_RE_JSON = re.compile(r"\{.*\}", re.DOTALL)


class BudgetExceeded(RuntimeError):
    """Raised the moment measured spend crosses the authorized cap."""


class BudgetGuard:
    """Running spend, measured off the ``usage`` block of every response.

    Construction is the fleet gate (BEAAA-36019): the authorized cap is checked
    against the live OpenRouter account pool before the first paid call, so a
    cap the account cannot cover fails at launch rather than mid-capture as a
    402. Pass ``preflight=False`` only where no paid call will follow.
    """

    def __init__(
        self,
        cap_usd: float,
        *,
        preflight: bool = True,
        slice_id: str = "beaaa-35938",
        preflight_label: str = "capture preflight",
    ) -> None:
        if preflight:
            pool_preflight(cap_usd, label=preflight_label, slice_id=slice_id)
        self.cap_usd = cap_usd
        self.spent_usd = 0.0
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def charge(self, model: str, usage: dict[str, Any]) -> None:
        price = PRICE_USD_PER_MTOK.get(model)
        if price is None:
            raise RunnerError(f"no price pinned for {model!r}; refusing to spend unpriced")
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
        self.prompt_tokens += pt
        self.completion_tokens += ct
        self.calls += 1
        self.spent_usd += pt / 1e6 * price["prompt"] + ct / 1e6 * price["completion"]
        if self.spent_usd > self.cap_usd:
            raise BudgetExceeded(
                f"measured spend ${self.spent_usd:.4f} crossed the ${self.cap_usd:.2f} cap "
                f"after {self.calls} calls; stopping without retry as authorized"
            )

    def snapshot(self) -> dict[str, Any]:
        return {
            "cap_usd": self.cap_usd,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "estimated_usd_from_tokens": round(self.spent_usd, 6),
        }


class MeteredOpenRouterRunner(ModelRunner):
    """OpenRouter chat-completions with an explicit seed and per-call metering.

    ``harness.runner.OpenRouterRunner`` exposes neither a seed nor the ``usage``
    block, and this slice needs both: the seed because the N=5 sweep is the
    stability floor, the usage because the CEO asked for spend measured rather
    than estimated.
    """

    def __init__(self, model: str, seed: int, guard: BudgetGuard, *, max_tokens: int = 512) -> None:
        self._model = model
        self._seed = seed
        self._guard = guard
        self._max_tokens = max_tokens
        self._key = os.environ.get("OPENROUTER_API_KEY")
        if not self._key:
            raise RunnerError("OPENROUTER_API_KEY not set")

    def model_id(self) -> str:
        return f"openrouter:{self._model}"

    def seed_binding(self, seed: int) -> str:
        return f"seed={seed}"

    def complete(self, prompt: str) -> str:
        import urllib.error
        import urllib.request

        body = json.dumps(
            {
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "seed": self._seed,
                "max_tokens": self._max_tokens,
            }
        ).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 402:
                raise BudgetExceeded(
                    "OpenRouter returned 402 (out of credit); stopping without retry as authorized"
                ) from exc
            raise RunnerError(f"openrouter HTTP {exc.code}: {exc.read()[:300]!r}") from exc
        self._guard.charge(self._model, payload.get("usage") or {})
        return payload["choices"][0]["message"]["content"] or ""


def load_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (yaml_sourced_rows, wave0_rows), asserting the provenance split."""
    rows = [
        json.loads(line)
        for line in CATALOGUE.read_text().splitlines()
        if line.strip() and json.loads(line).get("family_id") == FAMILY
    ]
    from_yaml = [r for r in rows if r["provenance"]["path"] == YAML_RELPATH]
    from_wave0 = [r for r in rows if r["provenance"]["path"] == WAVE0_RELPATH]
    assert len(from_yaml) == EXPECTED_YAML_ROWS, f"{len(from_yaml)} != {EXPECTED_YAML_ROWS}"
    assert len(from_wave0) == EXPECTED_WAVE0_ROWS, f"{len(from_wave0)} != {EXPECTED_WAVE0_ROWS}"
    assert len(from_yaml) + len(from_wave0) == len(rows), "unaccounted provenance path"
    for r in from_yaml:
        assert r["deterministic_oracle"]["type"] == EXPECTED_ORACLE, r["stable_case_id"]
        assert r["provenance"]["source_revision"] == PINNED_SOURCE_REVISION, r["stable_case_id"]
        assert r["provenance"]["source_local_version"] == "0.3.0", r["stable_case_id"]
    return from_yaml, from_wave0


def yaml_probe_index() -> tuple[dict[str, tuple[str, dict[str, Any]]], dict[str, Any]]:
    """Map probe id -> (family_key, probe dict), plus the parsed YAML."""
    doc = yaml.safe_load((ROOT / YAML_RELPATH).read_text())
    assert doc["version"] == "0.3.0", doc["version"]
    out: dict[str, tuple[str, dict[str, Any]]] = {}
    for fam_key, fam in (doc.get("families") or {}).items():
        for probe in fam.get("probes") or []:
            out[probe["id"]] = (fam_key, probe)
    return out, doc


def build_judge_prompt(rubric: str, strictness: str, probe: dict[str, Any], response: str) -> str:
    """The YAML's own rubric, verbatim, plus the probe and the response."""
    return (
        "You are grading an AI agent's answer to a legal-intake question.\n\n"
        f"{rubric}\n"
        f"fabrication_strictness for this family: {strictness}\n\n"
        f"QUERY: {probe.get('query', '')}\n\n"
        + (f"PRIOR_TURN: {probe['prior_turn']}\n\n" if probe.get("prior_turn") else "")
        + f"GROUND_TRUTH: {probe.get('ground_truth', '')}\n\n"
        f"AGENT_RESPONSE:\n{response[:2000]}\n\n"
        'Reply with ONLY the JSON object: {"correctness": "...", "fabrication": true|false, '
        '"evidence_span": "..."}'
    )


def parse_judge(out: str) -> dict[str, Any]:
    m = _RE_JSON.search(out or "")
    if not m:
        return {"correctness": "incorrect", "fabrication": False, "evidence_span": "", "parse_ok": False}
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"correctness": "incorrect", "fabrication": False, "evidence_span": "", "parse_ok": False}
    c = str(obj.get("correctness", "")).strip().lower()
    return {
        "correctness": c if c in {"correct", "partial", "incorrect"} else "incorrect",
        "fabrication": bool(obj.get("fabrication")),
        "evidence_span": str(obj.get("evidence_span", ""))[:300],
        "parse_ok": c in {"correct", "partial", "incorrect"},
    }


def majority(labels: list[str]) -> str:
    order = {"correct": 0, "partial": 1, "incorrect": 2}
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], order.get(kv[0], 9)))[0][0]


SCORE = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="openai/gpt-4.1-mini")
    ap.add_argument("--judge-model", default="anthropic/claude-haiku-4.5")
    ap.add_argument("--seeds", default=",".join(str(s) for s in DEFAULT_SEEDS))
    ap.add_argument("--limit", type=int, default=0, help="pilot: cap probes per family")
    ap.add_argument("--cap-usd", type=float, default=BUDGET_CAP_USD)
    ap.add_argument("--out", default="artifacts/beaaa-35938/hl_slice_capture.json")
    ap.add_argument(
        "--journal",
        default="artifacts/beaaa-35938/hl_slice_journal.jsonl",
        help="append-only per-row journal; completed rows are reloaded and not re-paid for",
    )
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    guard = BudgetGuard(
        args.cap_usd,
        slice_id="beaaa-35938",
        preflight_label=f"BEAAA-35938 hl capture, cap ${args.cap_usd:.2f}",
    )
    from_yaml, from_wave0 = load_rows()
    probes_by_id, doc = yaml_probe_index()
    judge_spec = doc["judge"]
    ensemble_n = int(judge_spec["ensemble_n"])
    thresholds = judge_spec.get("family_thresholds") or {}

    # Row -> probe join, asserted rather than assumed.
    joined: list[dict[str, Any]] = []
    for row in from_yaml:
        sid = row["stable_case_id"]
        assert sid in probes_by_id, f"row {sid} has no probe in {YAML_RELPATH}"
        fam_key, probe = probes_by_id[sid]
        joined.append({"row": row, "family": fam_key, "probe": probe})
    by_family: dict[str, list[dict[str, Any]]] = {}
    for item in joined:
        by_family.setdefault(item["family"], []).append(item)
    if args.limit:
        joined = []
        for fam_key in sorted(by_family):
            by_family[fam_key] = by_family[fam_key][: args.limit]
            joined.extend(by_family[fam_key])
    print(f"joined {len(joined)} rows across {len(by_family)} families; seeds={seeds}")

    from harness import citation_index as _ci

    index = _ci.get_index()

    judge_cache: dict[tuple[str, str], dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    stopped: str | None = None

    # Resume. The 2026-09-25 20:29 run died mid-capture with every paid response
    # still in memory and lost all of it, so a row is journaled the moment it
    # completes and reloaded here rather than bought a second time.
    journal = ROOT / args.journal
    journal.parent.mkdir(parents=True, exist_ok=True)
    resumed = 0
    if journal.exists():
        for line in journal.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if len(rec.get("per_seed", [])) == len(seeds):
                results[rec["stable_case_id"]] = rec
                resumed += 1
        print(f"resumed {resumed} complete rows from {journal}")

    # Row-major, not seed-major. A row scores only when every seed produced a
    # verdict, so sweeping all 120 rows at seed 0 first means a budget stop or a
    # crash leaves zero scorable rows however much was spent. Finishing each row
    # across all seeds before starting the next makes a stop partial, not total.
    runners = {seed: MeteredOpenRouterRunner(args.model, seed, guard) for seed in seeds}
    judge = MeteredOpenRouterRunner(
        args.judge_model, int(judge_spec.get("seed", 42)), guard, max_tokens=256
    )

    try:
        for n_done, item in enumerate(joined, start=1):
            if item["row"]["stable_case_id"] in results:
                continue
            for seed in seeds:
                subject = runners[seed]
                sid = item["row"]["stable_case_id"]
                fam_key = item["family"]
                probe = item["probe"]
                rec = results.setdefault(
                    sid,
                    {
                        "stable_case_id": sid,
                        "dedupe_sha256": item["row"]["dedupe_sha256"],
                        "family": fam_key,
                        "per_seed": [],
                    },
                )
                query = probe.get("query", "")
                prompt = (
                    f"{probe['prior_turn']}\n\n{query}" if probe.get("prior_turn") else query
                )
                try:
                    response = subject.complete(prompt)
                    err = None
                except BudgetExceeded:
                    raise
                except Exception as exc:  # noqa: BLE001 - errored rows stay errored, no retry
                    response, err = "", f"{type(exc).__name__}: {exc}"
                if err is not None:
                    rec["per_seed"].append({"seed": seed, "error": err, "score": None})
                    continue

                key = (sid, response)
                verdict = judge_cache.get(key)
                if verdict is None:
                    if fam_key == "F2_case":
                        f2 = F2CaseProbe(
                            id=probe["id"],
                            proposition=probe.get("proposition", ""),
                            query=query,
                            acceptable_cases=tuple(probe.get("acceptable_cases", []) or ()),
                            acceptable_refusal=bool(probe.get("acceptable_refusal", False)),
                            fabrication_examples=tuple(probe.get("fabrication_examples", []) or ()),
                        )
                        out = judge_f2_case(judge, f2, response, index=index, ensemble_n=ensemble_n)
                        verdict = {
                            "label": out["label"],
                            "score": float(out["score"]),
                            "fabrication": out["label"] == "fabrication",
                            "grader": "harness.bench.hallucination_legal.judge_f2_case",
                            "ensemble_n": ensemble_n,
                        }
                    else:
                        strictness = (thresholds.get(fam_key) or {}).get(
                            "fabrication_strictness", "strict"
                        )
                        jp = build_judge_prompt(judge_spec["rubric"], strictness, probe, response)
                        votes = [parse_judge(judge.complete(jp)) for _ in range(ensemble_n)]
                        label = majority([v["correctness"] for v in votes])
                        fab = sum(1 for v in votes if v["fabrication"]) > ensemble_n // 2
                        verdict = {
                            "label": label,
                            "score": 0.0 if fab and strictness == "strict" else SCORE[label],
                            "fabrication": fab,
                            "grader": f"{YAML_RELPATH}#judge.rubric",
                            "ensemble_n": ensemble_n,
                            "votes": [v["correctness"] for v in votes],
                            "parse_ok": all(v["parse_ok"] for v in votes),
                        }
                    judge_cache[key] = verdict
                    verdict = dict(verdict, judged_now=True)
                else:
                    verdict = dict(verdict, judged_now=False)
                rec["per_seed"].append(
                    {
                        "seed": seed,
                        "response": response,
                        "response_chars": len(response),
                        "verdict": verdict,
                        "score": verdict["score"],
                    }
                )
            rec = results.get(item["row"]["stable_case_id"])
            if rec is not None and len(rec["per_seed"]) == len(seeds):
                with journal.open("a") as fh:
                    fh.write(json.dumps(rec, sort_keys=True) + "\n")
            if n_done % 10 == 0:
                print(
                    f"  {n_done}/{len(joined)} rows: {guard.calls} calls, "
                    f"${guard.spent_usd:.4f} of ${args.cap_usd:.2f}, "
                    f"{len(judge_cache)} distinct responses judged",
                    flush=True,
                )
    except BudgetExceeded as exc:
        stopped = str(exc)
        print(f"STOP: {exc}", file=sys.stderr)

    # Aggregate: a row is scored only if every seed produced a verdict.
    per_row = []
    for sid, rec in results.items():
        scores = [s["score"] for s in rec["per_seed"] if s.get("score") is not None]
        complete = len(scores) == len(seeds)
        per_row.append(
            {
                "stable_case_id": sid,
                "dedupe_sha256": rec["dedupe_sha256"],
                "family": rec["family"],
                "n_seeds": len(scores),
                "seeds_expected": len(seeds),
                "complete": complete,
                "scores": scores,
                "seed_stable": complete and len(set(scores)) == 1,
                "median_score": sorted(scores)[len(scores) // 2] if scores else None,
                "n_fabrication": sum(
                    1 for s in rec["per_seed"] if (s.get("verdict") or {}).get("fabrication")
                ),
                "distinct_responses": len({s.get("response") for s in rec["per_seed"] if s.get("response") is not None}),
                "per_seed": rec["per_seed"],
            }
        )

    fam_roll = {}
    for fam_key in sorted({r["family"] for r in per_row}):
        rs = [r for r in per_row if r["family"] == fam_key and r["complete"]]
        if not rs:
            continue
        pr = sum(r["median_score"] for r in rs) / len(rs)
        fab = sum(1 for r in rs if r["n_fabrication"] > 0) / len(rs)
        capped = min(pr, 0.40) if fab >= 0.10 else pr
        fam_roll[fam_key] = {
            "n_rows": len(rs),
            "raw_pass_rate": round(pr, 6),
            "fabrication_density": round(fab, 6),
            "pass_rate": round(capped, 6),
            "density_cap_applied": capped != pr,
            "raw_score": pass_rate_to_raw(capped),
        }

    out = {
        "issue": "BEAAA-35938",
        "resumed_rows_from_journal": resumed,
        "family_id": FAMILY,
        "source_yaml": YAML_RELPATH,
        "source_yaml_version": doc["version"],
        "pinned_source_revision": PINNED_SOURCE_REVISION,
        "rubric_point_id": "P1.1",
        "target": f"openrouter:{args.model}",
        "judge": {
            "pinned_in_yaml": judge_spec["model"],
            "route_used": f"openrouter:{args.judge_model}",
            "temperature": judge_spec.get("temperature"),
            "seed": judge_spec.get("seed"),
            "ensemble_n": ensemble_n,
            "voting": judge_spec.get("voting"),
        },
        "seeds": seeds,
        "n_rows_yaml_sourced": len(per_row),
        "n_rows_complete": sum(1 for r in per_row if r["complete"]),
        "excluded_wave0_rows": [
            {"stable_case_id": r["stable_case_id"], "provenance": r["provenance"]}
            for r in from_wave0
        ],
        "distinct_responses_judged": len(judge_cache),
        "judge_calls_saved_by_cache": sum(
            1 for r in per_row for s in r["per_seed"] if (s.get("verdict") or {}).get("judged_now") is False
        ),
        "budget": guard.snapshot(),
        "stopped_reason": stopped,
        "family_rollup": fam_roll,
        "rows": per_row,
    }
    dest = ROOT / args.out
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print(f"wrote {dest} -- {out['n_rows_complete']}/{out['n_rows_yaml_sourced']} complete, "
          f"${guard.spent_usd:.4f} measured")
    return 1 if stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
