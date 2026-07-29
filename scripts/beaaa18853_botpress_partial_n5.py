"""BEAAA-18853 — Botpress live-webchat partial-coverage N=5 driver (option B).

BLUF (rule-execution; contract are-scanner-api-v1@v1.3.0). Wires the pinned
Botpress Cloud webchat adapter (``scripts/scan_botpress_com.BotpressCloudRunner``,
Scanner repo pinned at commit cc3299038de248352c1e21852827b29dca12eca7) into the
current v1.3.0 ``live_probe`` scored path with ``k_replicates=5``. It runs only
the two dimensions that are cleanly black-box-observable on this marketing bot,
using the FROZEN family scorers (no invented scoring math, no re-purposed
mapping):

  * P3.1 direct prompt-injection resistance  -> harness.bench.prompt_injection.run
        (judge-free canary detector, adapter's own prompt_injection_poc fixture)
  * P1.4 out-of-scope / refusal accuracy     -> harness.bench.p1_4_refusal_golden.run
        (frozen golden refusal roster + frozen LLM judge)

Every other rubric dimension (governance, bias, ops, tool-misuse, multi-turn /
indirect injection, secret exfil, ...) is OUT OF SCOPE on this partial black-box
surface and is marked N/A by the SAME frozen applicability helper the production
API uses (``scanners.score_runtime._build_synthetic_applicability``); the
Underwriter prices uncovered dimensions at maximum risk per contract. This is a
PARTIAL-COVERAGE scan of a marketing/demo bot and does NOT support a bind
recommendation or a customer-deployment assessment.

Scoring + publish half is identical to the Syllable full-corpus driver
(BEAAA-18671): per seed assemble the per-dim ``eval_results`` from the frozen
raws, then run the EXACT frozen functions the CLI uses internally —
``rubric.scoring.score`` + ``harness.contract_envelope.to_contract_output`` — to
produce the v1.3.0 response envelope (are_total / band / subscores + evidence
refs) carrying the provenance pins ``scanner_version``, ``corpus_version``,
``model_versions``, ``seed``. N=5 reproducibility is verified as the max-min
spread of are_total and every subscore across the 5 replicates (±1 band).

Modes:
  --mode validate   Synthetic per-dim raws, ZERO network. Proves the
                    score+publish + spread + reconciliation composition.
  --mode live       Executes the authorized live run against the public bot.
                    Resumable at the (seed, family) boundary; a ``--max-seconds``
                    soft budget stops cleanly between families so an interrupted
                    heartbeat wastes no in-flight family.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness import cli_runner  # noqa: E402
from harness.bench import p1_4_refusal_golden, prompt_injection  # noqa: E402
from harness.contract_envelope import to_contract_output  # noqa: E402
from harness.runner import ModelRunner, OpenRouterRunner, RunnerError  # noqa: E402
from rubric.scoring import Rubric, score  # noqa: E402
from scanners import SCANNER_VERSION as SCORER_SCANNER_VERSION  # noqa: E402
from scanners.score_runtime import _build_synthetic_applicability  # noqa: E402
from scripts.scan_botpress_com import (  # noqa: E402
    ARCHITECTURE_FLAGS,
    BOT_ID,
    BotpressCloudRunner,
)

# ---------------------------------------------------------------------------
# Immutable pins (fail-closed)
# ---------------------------------------------------------------------------
SCANNER_GIT_COMMIT = "cc3299038de248352c1e21852827b29dca12eca7"
SCANNER_VERSION = SCORER_SCANNER_VERSION  # "0.5.0" scorer semver in the envelope
CORPUS_VERSION = "botpress-partial-blackbox@0.1.0"
JUDGE_MODEL = "anthropic/claude-haiku-4.5"
SUBJECT_LABEL = f"botpress-cloud:{BOT_ID}"
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
PI_FIXTURE = REPO / "harness" / "fixtures" / "prompt_injection_poc.jsonl"
P14_PACK = REPO / "rubric" / "probe_mappings" / "p1_4_refusal_golden.yaml"
SEEDS = (0, 1, 2, 3, 4)

# dim_id -> (frozen family key, expected case count, probe_set_version pin)
FAMILIES: tuple[tuple[str, str, int, str], ...] = (
    ("P3.1", "prompt_injection", 25, "prompt-injection-poc"),
    ("P1.4", "p1_4_refusal_golden", 20, "p1_4_refusal_golden@0.1.0"),
)
EXPECTED_CASE_COUNTS = {dim: n for dim, _k, n, _v in FAMILIES}
PROBE_SET_VERSIONS = {dim: v for dim, _k, _n, v in FAMILIES}

# Hard caps (fail-closed). 5 seeds x (25 + 20) = 225 logical subject calls.
CAP_SUBJECT_CALLS = 260  # 225 + retry-free headroom; retries counted separately
SPREAD_BAND = 1.0

# Driver-side transport reliability against the public Botpress bot. The adapter
# already rate-limits (~2 req/s) and opens a fresh session per prompt; we add a
# bounded retry on transient transport errors (Errno111 / SSE resets) so a live
# run survives momentary refusals without changing the pinned adapter.
INTER_CALL_PACING_SECONDS = float(os.environ.get("BEAAA18853_PACING_S", "0.5"))
MAX_DRIVER_RETRIES = int(os.environ.get("BEAAA18853_MAX_RETRIES", "5"))
RETRY_BACKOFF_BASE_SECONDS = 2.0
_TRANSIENT_MARKERS = (
    "connection refused", "no response within timeout", "timed out", "timeout",
    "reset", "sse error", "failed to establish", "111",
)


class RunGuardError(RuntimeError):
    """A fail-closed guard tripped (cap, count mismatch, or pin mismatch)."""


# ---------------------------------------------------------------------------
# Live subject with a target-call cap + bounded transient retry
# ---------------------------------------------------------------------------
class _CappedBotpressSubject(ModelRunner):
    def __init__(self, inner: ModelRunner, *, baseline_calls: int) -> None:
        self._inner = inner
        self.subject_calls = baseline_calls
        self.driver_retries = 0

    def model_id(self) -> str:
        return self._inner.model_id()

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        msg = str(exc).lower()
        return isinstance(exc, RunnerError) and any(m in msg for m in _TRANSIENT_MARKERS)

    def complete(self, prompt: str) -> str:
        if self.subject_calls >= CAP_SUBJECT_CALLS:
            raise RunGuardError(f"{CAP_SUBJECT_CALLS} subject-call cap reached")
        self.subject_calls += 1  # one logical call; retries excluded
        if INTER_CALL_PACING_SECONDS > 0:
            time.sleep(INTER_CALL_PACING_SECONDS)
        last: Exception | None = None
        for attempt in range(MAX_DRIVER_RETRIES + 1):
            try:
                return self._inner.complete(prompt)
            except Exception as exc:  # noqa: BLE001 - bounded transient retry
                if not self._is_transient(exc) or attempt == MAX_DRIVER_RETRIES:
                    raise
                last = exc
                self.driver_retries += 1
                delay = RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt)
                time.sleep(delay + random.uniform(0.0, delay * 0.25))
        raise last  # pragma: no cover


# ---------------------------------------------------------------------------
# Frozen family dispatch — NO scoring math here, only wrapping the frozen run()
# ---------------------------------------------------------------------------
def _run_family(dim_id: str, subject: ModelRunner, judge: ModelRunner, seed: int) -> dict[str, Any]:
    if dim_id == "P3.1":
        items = prompt_injection.load_items(PI_FIXTURE)
        if len(items) != EXPECTED_CASE_COUNTS["P3.1"]:
            raise RunGuardError(f"P3.1 fixture count {len(items)} != 25")
        return prompt_injection.run(subject, items)
    if dim_id == "P1.4":
        probes = p1_4_refusal_golden.load_probes(P14_PACK)
        if len(probes) != EXPECTED_CASE_COUNTS["P1.4"]:
            raise RunGuardError(f"P1.4 probe count {len(probes)} != 20")
        return p1_4_refusal_golden.run(
            subject, judge, probes, ensemble_n=1, seed=seed,
            scanner_version=SCANNER_VERSION,
        )
    raise RunGuardError(f"unknown dim {dim_id}")


# ---------------------------------------------------------------------------
# Collection — atomic, resumable at the (seed, family) boundary
# ---------------------------------------------------------------------------
def _seed_family_path(evidence_dir: Path, dim_id: str, seed: int) -> Path:
    return evidence_dir / "seed_family" / f"{dim_id}.seed{seed}.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(encoded)
    os.replace(tmp, path)
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def collect_live(
    evidence_dir: Path, max_seconds: float | None = None
) -> dict[tuple[int, str], dict[str, Any]]:
    started = time.time()
    # Resume baseline: subject calls already spent by finalized (seed, family).
    baseline = 0
    for seed in SEEDS:
        for dim_id, _k, n, _v in FAMILIES:
            if _seed_family_path(evidence_dir, dim_id, seed).exists():
                baseline += n
    subject = _CappedBotpressSubject(
        BotpressCloudRunner(label=SUBJECT_LABEL), baseline_calls=baseline
    )
    judge = OpenRouterRunner(JUDGE_MODEL)
    results: dict[tuple[int, str], dict[str, Any]] = {}

    for seed in SEEDS:
        for dim_id, _k, n, probe_set_version in FAMILIES:
            sf_path = _seed_family_path(evidence_dir, dim_id, seed)
            if sf_path.exists():  # resume: finalized, skip (no re-run, no reuse)
                results[(seed, dim_id)] = json.loads(sf_path.read_text())
                continue
            if max_seconds is not None and (time.time() - started) >= max_seconds:
                return results  # clean boundary stop
            if subject.subject_calls + n > CAP_SUBJECT_CALLS:
                raise RunGuardError(
                    f"would exceed {CAP_SUBJECT_CALLS} subject-call cap at "
                    f"{dim_id}/seed{seed} (spent={subject.subject_calls}, need={n})"
                )
            scored = _run_family(dim_id, subject, judge, seed)
            trace = scored.get("trace", [])
            if len(trace) != n:
                raise RunGuardError(
                    f"{dim_id}/seed{seed}: trace rows {len(trace)} != {n}"
                )
            raw = scored.get("raw")
            evidence_ref = f"evidence://beaaa-18853-botpress-n5/{dim_id}/seed{seed}.json"
            record = {
                "schema": "backbond.beaaa18853.seed-family-evidence/v1",
                "dim_id": dim_id,
                "seed": seed,
                "raw": raw,
                "inconclusive": raw is None,
                "inconclusive_reason": scored.get("inconclusive_reason"),
                "pass_rate": scored.get("pass_rate"),
                "resistance_rate": scored.get("resistance_rate"),
                "probe_set_version": probe_set_version,
                "subject_model_id": subject.model_id(),
                "judge_model_id": judge.model_id() if dim_id == "P1.4" else None,
                "case_count": len(trace),
                "evidence_ref": evidence_ref,
                "trace": trace,
            }
            sha = _atomic_write_json(sf_path, record)
            record["evidence_sha256"] = sha
            results[(seed, dim_id)] = record
    return results


def collect_synthetic(_evidence_dir: Path) -> dict[tuple[int, str], dict[str, Any]]:
    raws = {"P3.1": 4.0, "P1.4": 3.5}
    results: dict[tuple[int, str], dict[str, Any]] = {}
    for seed in SEEDS:
        for dim_id, _k, n, _v in FAMILIES:
            trace = [{"id": f"{dim_id}-{i}", "score": 1.0} for i in range(n)]
            results[(seed, dim_id)] = {
                "dim_id": dim_id, "seed": seed, "raw": raws[dim_id],
                "case_count": n, "trace": trace,
                "evidence_ref": f"evidence://validate/{dim_id}/seed{seed}.json",
            }
    return results


# ---------------------------------------------------------------------------
# SCORE + PUBLISH — wrap the frozen CLI score projection (identical to BEAAA-18671)
# ---------------------------------------------------------------------------
def score_seed_envelope(
    seed: int,
    results: dict[tuple[int, str], dict[str, Any]],
    work_dir: Path,
    scored_at: datetime,
) -> dict[str, Any]:
    eval_results: dict[str, dict[str, Any]] = {}
    for dim_id, _k, _n, _v in FAMILIES:
        rec = results[(seed, dim_id)]
        if rec.get("raw") is None:
            continue  # inconclusive -> omit -> marked N/A by applicability helper
        eval_results[dim_id] = {
            "raw": float(rec["raw"]),
            "evidence_uri": rec["evidence_ref"],
        }
    input_dir = work_dir / f"seed{seed}"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "eval_results.json").write_text(
        json.dumps(eval_results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    # Provenance: model_versions.json is read by cli_runner.load_inputs and
    # carried into the envelope's model_versions pin.
    (input_dir / "model_versions.json").write_text(
        json.dumps(
            {"subject": SUBJECT_LABEL, "judge": JUDGE_MODEL},
            indent=2, sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    agent_id = "botpress-com"
    inputs = cli_runner.load_inputs(agent_id, input_dir)
    snapshot_hash = cli_runner._build_snapshot_hash(inputs, seed)
    forensic_bundle_uri = f"cli://bin-are-scan/{inputs.agent_id}?snapshot={snapshot_hash}"
    rubric = Rubric.load(RUBRIC_PATH)
    applicability = _build_synthetic_applicability(rubric, set(inputs.eval_results))
    score_result = score(
        rubric,
        inputs.eval_results,
        snapshot_hash=snapshot_hash,
        forensic_bundle_uri=forensic_bundle_uri,
        scored_at=scored_at,
        scanner_version=SCANNER_VERSION,
        corpus_version=CORPUS_VERSION,
        seed=seed,
        model_versions=inputs.model_versions,
        applicability_decisions=applicability or None,
        applicability_policy_version=None,
    )
    return to_contract_output(
        inputs=inputs,
        score_result=score_result,
        seed=seed,
        corpus_version=CORPUS_VERSION,
        scanner_version=SCANNER_VERSION,
        scored_at=scored_at,
    )


def _flatten_numeric(prefix: str, value: Any, out: dict[str, float]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        out[prefix] = float(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            _flatten_numeric(f"{prefix}.{k}" if prefix else str(k), v, out)


def n5_spread(envelopes: dict[int, dict[str, Any]]) -> dict[str, Any]:
    are_totals = {s: float(env["are_total"]) for s, env in envelopes.items()}
    are_spread = max(are_totals.values()) - min(are_totals.values())
    sub_series: dict[str, dict[int, float]] = {}
    for seed, env in envelopes.items():
        flat: dict[str, float] = {}
        _flatten_numeric("", env.get("subscores", {}), flat)
        for key, val in flat.items():
            sub_series.setdefault(key, {})[seed] = val
    sub_spreads = {
        key: (max(series.values()) - min(series.values()))
        for key, series in sub_series.items()
    }
    breaches = {"are_total": are_spread} if are_spread > SPREAD_BAND else {}
    breaches.update({k: v for k, v in sub_spreads.items() if v > SPREAD_BAND})
    return {
        "band": SPREAD_BAND,
        "are_total_by_seed": are_totals,
        "are_total_spread": are_spread,
        "subscore_spread": sub_spreads,
        "breaches": breaches,
        "within_band": not breaches,
    }


def reconcile(results: dict[tuple[int, str], dict[str, Any]]) -> dict[str, Any]:
    expected = len(SEEDS) * sum(EXPECTED_CASE_COUNTS.values())
    terminal_units = len(results)
    evidenced_cases = sum(r["case_count"] for r in results.values())
    ok = (
        evidenced_cases == expected
        and terminal_units == len(SEEDS) * len(FAMILIES)
    )
    return {
        "expected_case_seeds": expected,
        "terminal_seed_families": terminal_units,
        "evidenced_case_seeds": evidenced_cases,
        "reconciled": ok,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("validate", "live"), required=True)
    ap.add_argument("--out", default=str(REPO / "artifacts" / "beaaa-18853" / "n5_run"))
    ap.add_argument("--iso-timestamp", default=None, help="scored_at ISO-8601 (UTC)")
    ap.add_argument("--max-seconds", type=float, default=None,
                    help="soft per-invocation budget (live); stop at (seed,family) boundary")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = out_dir / "evidence"
    scored_at = (
        datetime.fromisoformat(args.iso_timestamp.replace("Z", "+00:00"))
        if args.iso_timestamp else datetime.now(timezone.utc)
    )

    t0 = time.time()
    if args.mode == "live":
        results = collect_live(evidence_dir, max_seconds=args.max_seconds)
        finalized = len(results)
        total = len(SEEDS) * len(FAMILIES)
        if finalized < total:
            print(json.dumps({
                "mode": "live", "phase": "incomplete_resumable",
                "finalized_seed_families": finalized, "total_seed_families": total,
                "elapsed_seconds": round(time.time() - t0, 2), "out": str(out_dir),
            }, indent=2, sort_keys=True))
            return 0
    else:
        results = collect_synthetic(evidence_dir)

    envelopes = {
        seed: score_seed_envelope(seed, results, out_dir / "score", scored_at)
        for seed in SEEDS
    }
    (out_dir / "score").mkdir(parents=True, exist_ok=True)
    for seed, env in envelopes.items():
        (out_dir / "score" / f"envelope.seed{seed}.json").write_text(
            json.dumps(env, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    spread = n5_spread(envelopes)
    recon = reconcile(results)
    inconclusive = sorted({
        results[(seed, d)]["dim_id"]
        for seed in SEEDS for d, _k, _n, _v in FAMILIES
        if results[(seed, d)].get("raw") is None
    })

    manifest = {
        "schema": "backbond.beaaa18853.botpress-partial-n5/v1",
        "issue": "BEAAA-18853",
        "parent": "BEAAA-18609",
        "mode": args.mode,
        "classification": "rule-execution",
        "contract": "are-scanner-api-v1@v1.3.0",
        "scan_mode": "live_probe",
        "label": "live",
        "scanner_git_commit": SCANNER_GIT_COMMIT,
        "scanner_version": SCANNER_VERSION,
        "corpus_version": CORPUS_VERSION,
        "probe_set_versions": PROBE_SET_VERSIONS,
        "rubric_point_id": [d for d, _k, _n, _v in FAMILIES],
        "judge_model": JUDGE_MODEL,
        "subject": SUBJECT_LABEL,
        "bot_id": BOT_ID,
        "architecture_flags": ARCHITECTURE_FLAGS,
        "seeds": list(SEEDS),
        "coverage": (
            "PARTIAL black-box. Only P3.1 (direct injection) and P1.4 (OOS "
            "refusal) are observable on this marketing/demo bot; every other "
            "rubric dimension is N/A (priced at max risk). NOT a bind "
            "recommendation or customer-deployment assessment."
        ),
        "caps": {"subject_calls": CAP_SUBJECT_CALLS},
        "elapsed_seconds": round(time.time() - t0, 2),
        "reconciliation": recon,
        "n5_spread": spread,
        "raw_by_seed_dim": {
            f"seed{seed}": {
                results[(seed, d)]["dim_id"]: results[(seed, d)]["raw"]
                for d, _k, _n, _v in FAMILIES
            }
            for seed in SEEDS
        },
        "are_total_by_seed": spread["are_total_by_seed"],
        "inconclusive_dims": inconclusive,
        "score_emittable": bool(
            spread["within_band"] and recon["reconciled"] and not inconclusive
        ),
    }
    (out_dir / "full_suite_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(json.dumps({
        "mode": args.mode,
        "reconciled": recon["reconciled"],
        "within_band": spread["within_band"],
        "score_emittable": manifest["score_emittable"],
        "are_total_by_seed": spread["are_total_by_seed"],
        "are_total_spread": spread["are_total_spread"],
        "inconclusive_dims": inconclusive,
        "elapsed_seconds": manifest["elapsed_seconds"],
        "out": str(out_dir),
    }, indent=2, sort_keys=True))
    if not manifest["score_emittable"]:
        print("SCORE NOT EMITTABLE — no BackBond Score shipped for Botpress.")
        return 3 if args.mode == "live" else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
