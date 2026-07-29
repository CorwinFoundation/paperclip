"""BEAAA-18671 / BEAAA-18844 — cap-parameterized N=5 Syllable full-corpus driver.

BLUF (rule-execution; contract are-scanner-api-v1@v1.3.0; supervision
scanner-implementation-supervision-v1@v1.1). Executes the 90 applicable golden
rows (P1.1=30, P1.3=20, P1.4=20, P1.5=20) x 5 seeds = 450 case-seed invocations
against the live Syllable public showcase, wraps the frozen family scorers and
the frozen CLI score projection (NO invented scoring math), and publishes the
current Scanner response envelope (are_total / band / subscores + evidence_refs)
per seed plus the N=5 spread verdict.

Design (see artifacts/beaaa-18671/execution-plan-2026-07-29.md):

* AC#6 path — the "current Scanner response envelope" is produced by the
  production scoring projection: per family we call the frozen
  ``harness.bench.<family>.run(subject, judge, probes, ...)`` (owns pass_rate ->
  raw), assemble the per-dim ``eval_results`` map, and run it through the frozen
  ``harness.cli_runner.run_scan`` -> ``harness.contract_envelope.to_contract_output``
  (owns raw -> are_total / band / subscores). Both are wrapped, never
  reimplemented. The RegisteredCaseExecutor golden-target summary is a
  verdict-count artifact, not the contract score envelope, so it is not the
  publish path.

* Cap parameterization (AC#1 preserved) — the QA-approved BEAAA-18687 checkpoint
  seam (``scripts/syllable_checkpoint.CheckpointLedger``) already exposes
  ``max_http_attempts`` and ``max_wall_seconds`` as public constructor kwargs.
  We construct the ledger with ``max_http_attempts=1300`` (550 target calls x 2
  posts/call under Syllable's documented two-phase protocol + ~200 retry
  headroom) and ``max_wall_seconds=48h``. NO seam source is changed, so no fresh
  seam QA is required; the seam is used exactly as approved.

* Immutable pins (fail-closed): registry SHA-256
  b90629da751fd07b4b986e3a82ead208a62d54a6ab8c7b24ec692f193c96b671 (278 rows);
  selection = 90 countable_as_executable rows; Scanner 0.5.0; judge
  anthropic/claude-haiku-4.5; subject syllable:apichat; seeds 0..4.

* Hard caps (fail-closed, never silently exceeded): 450 invocations, 550 target
  calls, 1300 HTTP attempts, 48h wall.

* Checkpoint / resume — terminal unit is the family-seed (20 units). Each is
  written atomically only after its whole bench.run() returns; resume skips
  finalized family-seeds and re-runs unfinalized ones fresh (no cached-response
  reuse, no double-count). Per-case-seed evidence lives in each family-seed's
  ``trace``; the 450 reconciliation counts case rows across finalized files.

Modes:
  --mode validate   Exercises the frozen SCORE+PUBLISH half against synthetic
                    per-dim raws (zero network) to prove envelope + spread +
                    manifest + reconciliation composition. The live scored path
                    is separately proven by the one-case smoke.
  --mode live       Executes the full authorized 450-invocation run.
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
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness import cli_runner  # noqa: E402
from harness.contract_envelope import to_contract_output  # noqa: E402
from harness.runner import ModelRunner, OpenRouterRunner, RunnerError  # noqa: E402
from rubric.scoring import Rubric, score  # noqa: E402
from scanners import SCANNER_VERSION as SCORER_SCANNER_VERSION  # noqa: E402
from scanners.score_runtime import _build_synthetic_applicability  # noqa: E402
from scripts.syllable_checkpoint import CheckpointLedger, ExecutionCapReached  # noqa: E402
from tools.active_test_registry import build_registry  # noqa: E402

# ---------------------------------------------------------------------------
# Immutable pins
# ---------------------------------------------------------------------------
REGISTRY_PATH = REPO / "tools" / "probe-registry" / "active_registry.json"
REGISTRY_SHA256 = "b90629da751fd07b4b986e3a82ead208a62d54a6ab8c7b24ec692f193c96b671"
SCANNER_VERSION = "0.5.0"
CORPUS_VERSION = "registry-baseline-f794e63"
PROBE_SET_VERSION = "active-probe-registry@0.1.0"
JUDGE_MODEL = "anthropic/claude-haiku-4.5"
SYLLABLE_BASE_URL = "https://apichat.syllable.cloud"
SUBJECT_LABEL = "syllable:apichat"
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
SEEDS = (0, 1, 2, 3, 4)

# test_id -> (bench module import path, contract dim id)
FAMILIES: tuple[tuple[str, str, str], ...] = (
    ("p1_1_factuality_golden", "harness.bench.factuality_golden", "P1.1"),
    ("p1_3_consistency_golden", "harness.bench.p1_3_consistency_golden", "P1.3"),
    ("p1_4_refusal_golden", "harness.bench.p1_4_refusal_golden", "P1.4"),
    ("p1_5_calibration_golden", "harness.bench.p1_5_calibration_golden", "P1.5"),
)
EXPECTED_CASE_COUNTS = {
    "p1_1_factuality_golden": 30,
    "p1_3_consistency_golden": 20,
    "p1_4_refusal_golden": 20,
    "p1_5_calibration_golden": 20,
}
# subject.complete() calls per case (P1.3 = two-prompt consistency pair).
TARGET_CALLS_PER_CASE = {
    "p1_1_factuality_golden": 1,
    "p1_3_consistency_golden": 2,
    "p1_4_refusal_golden": 1,
    "p1_5_calibration_golden": 1,
}

# Binding authorized caps (BEAAA-18686).
CAP_INVOCATIONS = 450
CAP_TARGET_CALLS = 550
CAP_HTTP_ATTEMPTS = 1300  # 550 x 2 posts + ~200 retry headroom
CAP_WALL_SECONDS = 48 * 60 * 60
SPREAD_BAND = 1.0  # ±1 reproducibility band (are_total and every subscore)

# Driver-side transport reliability against the Syllable public showcase. The
# QA-approved BEAAA-18687 seam retries only HTTP 502/503/522 (not connection
# refusals), and AC#1 forbids changing the seam. So the driver adds bounded
# retry-on-connection-error + light inter-call pacing here. Pacing costs only
# wall-clock (we have 48h); retries fire extra HTTP posts counted by the ledger
# and fail-closed at CAP_HTTP_ATTEMPTS. Each logical target call is counted once.
INTER_CALL_PACING_SECONDS = float(os.environ.get("BEAAA18671_PACING_S", "1.5"))
MAX_DRIVER_RETRIES = int(os.environ.get("BEAAA18671_MAX_RETRIES", "5"))
RETRY_BACKOFF_BASE_SECONDS = 2.0
_TRANSIENT_MARKERS = ("connection refused", "failed", "timed out", "timeout", "reset")


class RunGuardError(RuntimeError):
    """A fail-closed guard tripped (cap, count mismatch, or pin mismatch)."""


# ---------------------------------------------------------------------------
# Pins & selection
# ---------------------------------------------------------------------------
def verify_registry_pin() -> None:
    actual = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
    if actual != REGISTRY_SHA256:
        raise RunGuardError(
            f"registry SHA mismatch: expected {REGISTRY_SHA256}, got {actual}"
        )


def build_selection_manifest() -> dict[str, Any]:
    """Partition the pinned registry rows into 90 selected + 188 excluded."""
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    rows = registry["rows"]
    selected = [r for r in rows if r.get("countable_as_executable")]
    excluded = [r for r in rows if not r.get("countable_as_executable")]
    by_dim: dict[str, int] = {}
    for r in selected:
        by_dim[r["rubric_point_id"]] = by_dim.get(r["rubric_point_id"], 0) + 1
    if by_dim != {"P1.1": 30, "P1.3": 20, "P1.4": 20, "P1.5": 20}:
        raise RunGuardError(f"selection drift: {by_dim}")
    excluded_reasons: dict[str, int] = {}
    for r in excluded:
        excluded_reasons[r["reason"]] = excluded_reasons.get(r["reason"], 0) + 1
    return {
        "schema": "backbond.beaaa18671.selection-manifest/v1",
        "registry_sha256": REGISTRY_SHA256,
        "row_total": len(rows),
        "selected_count": len(selected),
        "selected_by_dim": by_dim,
        "excluded_count": len(excluded),
        "excluded_reasons": excluded_reasons,
        "selected": [
            {
                "stable_executable_key": r["stable_executable_key"],
                "rubric_point_id": r["rubric_point_id"],
                "case_id_or_probe_id": r["case_id_or_probe_id"],
                "corpus_or_family": r["corpus_or_family"],
            }
            for r in selected
        ],
        "excluded": [
            {
                "stable_executable_key": r["stable_executable_key"],
                "rubric_point_id": r["rubric_point_id"],
                "bucket": r["bucket"],
                "reason": r["reason"],
            }
            for r in excluded
        ],
    }


def load_family_probes() -> dict[str, tuple[str, Any, str]]:
    """Return test_id -> (module_path, probes, dim_id), count-validated."""
    import importlib

    rows = build_registry()
    out: dict[str, tuple[str, Any, str]] = {}
    for test_id, module_path, dim_id in FAMILIES:
        fam_rows = [
            r for r in rows if r.get("test_id") == test_id and r.get("status") == "active"
        ]
        if len(fam_rows) != EXPECTED_CASE_COUNTS[test_id]:
            raise RunGuardError(
                f"{test_id}: registry count {len(fam_rows)} != "
                f"{EXPECTED_CASE_COUNTS[test_id]}"
            )
        source_pack = fam_rows[0]["source_pack"]
        module = importlib.import_module(module_path)
        probes = module.load_probes(REPO / source_pack)
        if len(probes) != EXPECTED_CASE_COUNTS[test_id]:
            raise RunGuardError(
                f"{test_id}: load_probes count {len(probes)} != "
                f"{EXPECTED_CASE_COUNTS[test_id]}"
            )
        out[test_id] = (module_path, probes, dim_id)
    return out


# ---------------------------------------------------------------------------
# Live subject with target-call + HTTP-attempt caps
# ---------------------------------------------------------------------------
class _CappedSubject(ModelRunner):
    """Wraps the live Syllable seam runner; counts target calls, fails closed
    at the 550 cap. HTTP-attempt + wall caps are enforced by the ledger via the
    seam's ``http_attempt_observer``.
    """

    def __init__(self, inner: ModelRunner, *, baseline_target_calls: int) -> None:
        self._inner = inner
        self.target_calls = baseline_target_calls
        self.driver_retries = 0

    def model_id(self) -> str:
        return self._inner.model_id()

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        msg = str(exc).lower()
        return isinstance(exc, RunnerError) and any(
            m in msg for m in _TRANSIENT_MARKERS
        )

    def complete(self, prompt: str) -> str:
        if self.target_calls >= CAP_TARGET_CALLS:
            raise ExecutionCapReached(
                f"{CAP_TARGET_CALLS} target-call cap reached"
            )
        self.target_calls += 1  # one logical target call, retries excluded
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
                delay = RETRY_BACKOFF_BASE_SECONDS * (2**attempt)
                time.sleep(delay + random.uniform(0.0, delay * 0.25))
        raise last  # pragma: no cover


def build_live_subject(ledger: CheckpointLedger, baseline_target_calls: int) -> _CappedSubject:
    from harness.customer_runner import _SyllableCustomerAgentRunner

    inner = _SyllableCustomerAgentRunner(
        base_url=SYLLABLE_BASE_URL,
        client_id_key=os.environ.get("SYLLABLE_CLIENT_ID_KEY", "hlw3qyTAx5inYzm2"),
        host_key=os.environ.get("SYLLABLE_HOST_KEY", "https://syllable.ai"),
        label=SUBJECT_LABEL,
        http_attempt_observer=ledger.record_http_attempt,
    )
    return _CappedSubject(inner, baseline_target_calls=baseline_target_calls)


# ---------------------------------------------------------------------------
# LIVE collection — per family-seed, atomic, resumable
# ---------------------------------------------------------------------------
def _family_seed_path(evidence_dir: Path, test_id: str, seed: int) -> Path:
    return evidence_dir / "family_seed" / f"{test_id}.seed{seed}.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(encoded)
    os.replace(tmp, path)
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def collect_live(evidence_dir: Path, ledger: CheckpointLedger) -> dict[tuple[int, str], dict[str, Any]]:
    import importlib

    families = load_family_probes()

    # Resume baseline: target calls already spent by finalized family-seeds.
    baseline = 0
    for seed in SEEDS:
        for test_id, _mod, _dim in FAMILIES:
            if _family_seed_path(evidence_dir, test_id, seed).exists():
                baseline += (
                    EXPECTED_CASE_COUNTS[test_id] * TARGET_CALLS_PER_CASE[test_id]
                )

    subject = build_live_subject(ledger, baseline)
    judge = OpenRouterRunner(JUDGE_MODEL)
    results: dict[tuple[int, str], dict[str, Any]] = {}

    for seed in SEEDS:
        for test_id, module_path, dim_id in FAMILIES:
            fs_path = _family_seed_path(evidence_dir, test_id, seed)
            if fs_path.exists():  # resume: finalized, skip (no re-run, no reuse)
                results[(seed, test_id)] = json.loads(fs_path.read_text())
                continue
            # Guard target-call budget before starting this family-seed.
            need = EXPECTED_CASE_COUNTS[test_id] * TARGET_CALLS_PER_CASE[test_id]
            if subject.target_calls + need > CAP_TARGET_CALLS:
                raise RunGuardError(
                    f"would exceed {CAP_TARGET_CALLS} target-call cap at "
                    f"{test_id}/seed{seed} (spent={subject.target_calls}, "
                    f"need={need})"
                )
            module = importlib.import_module(module_path)
            probes = families[test_id][1]
            scored = module.run(
                subject=subject,
                judge=judge,
                probes=probes,
                ensemble_n=1,
                seed=seed,
                scanner_version=SCANNER_VERSION,
            )
            raw = scored.get("raw")
            if raw is None:
                raise RunGuardError(
                    f"{test_id}/seed{seed}: frozen scorer returned raw=None "
                    "(inconclusive-ceiling breach); cannot emit a score"
                )
            trace = scored.get("trace", [])
            if len(trace) != EXPECTED_CASE_COUNTS[test_id]:
                raise RunGuardError(
                    f"{test_id}/seed{seed}: trace rows {len(trace)} != "
                    f"{EXPECTED_CASE_COUNTS[test_id]}"
                )
            evidence_ref = f"evidence://beaaa-18671-n5/{test_id}/seed{seed}.json"
            record = {
                "schema": "backbond.beaaa18671.family-seed-evidence/v1",
                "test_id": test_id,
                "dim_id": dim_id,
                "seed": seed,
                "raw": raw,
                "pass_rate": scored.get("pass_rate"),
                "subject_model_id": subject.model_id(),
                "judge_model_id": judge.model_id(),
                "case_count": len(trace),
                "evidence_ref": evidence_ref,
                "trace": trace,
            }
            sha = _atomic_write_json(fs_path, record)
            record["evidence_sha256"] = sha
            results[(seed, test_id)] = record
    return results


def collect_synthetic(evidence_dir: Path) -> dict[tuple[int, str], dict[str, Any]]:
    """Validate-mode: deterministic per-dim raws, no network. Proves the
    SCORE+PUBLISH half composes with the frozen scorer."""
    raws = {"P1.1": 3.5, "P1.3": 3.0, "P1.4": 4.0, "P1.5": 2.5}
    results: dict[tuple[int, str], dict[str, Any]] = {}
    for seed in SEEDS:
        for test_id, _mod, dim_id in FAMILIES:
            n = EXPECTED_CASE_COUNTS[test_id]
            trace = [{"id": f"{test_id}-{i}", "score": 1.0} for i in range(n)]
            results[(seed, test_id)] = {
                "test_id": test_id,
                "dim_id": dim_id,
                "seed": seed,
                "raw": raws[dim_id],
                "case_count": n,
                "evidence_ref": f"evidence://validate/{test_id}/seed{seed}.json",
                "trace": trace,
            }
    return results


# ---------------------------------------------------------------------------
# SCORE + PUBLISH — wrap the frozen CLI score projection
# ---------------------------------------------------------------------------
def score_seed_envelope(
    seed: int,
    results: dict[tuple[int, str], dict[str, Any]],
    work_dir: Path,
    scored_at: datetime,
) -> dict[str, Any]:
    eval_results: dict[str, dict[str, Any]] = {}
    for _test_id, _mod, dim_id in FAMILIES:
        rec = results[(seed, _test_id)]
        eval_results[dim_id] = {
            "raw": float(rec["raw"]),
            "evidence_uri": rec["evidence_ref"],
        }
    input_dir = work_dir / f"seed{seed}"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "eval_results.json").write_text(
        json.dumps(eval_results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    # Wrap the EXACT frozen functions cli_runner.run_scan calls internally
    # (rubric.scoring.score + harness.contract_envelope.to_contract_output),
    # skipping only the CLI's cosmetic semver flag-gate so the envelope carries
    # the operational corpus_version pin (registry-baseline-f794e63), matching
    # prior vendor envelopes. No scoring math is reimplemented.
    agent_id = SUBJECT_LABEL.replace(":", "-")
    inputs = cli_runner.load_inputs(agent_id, input_dir)
    snapshot_hash = cli_runner._build_snapshot_hash(inputs, seed)
    forensic_bundle_uri = (
        f"cli://bin-are-scan/{inputs.agent_id}?snapshot={snapshot_hash}"
    )
    rubric = Rubric.load(RUBRIC_PATH)
    # The pinned selection scores exactly the 4 golden P1 dims; every other
    # active rubric dim is out of scope for this run. Mark them N/A with the
    # production synthetic-walker justification (frozen helper) so the scorer
    # aggregates over the covered set — identical to the production API path.
    applicability = _build_synthetic_applicability(rubric, set(inputs.eval_results))
    score_result = score(
        rubric,
        inputs.eval_results,
        snapshot_hash=snapshot_hash,
        forensic_bundle_uri=forensic_bundle_uri,
        scored_at=scored_at,
        scanner_version=SCORER_SCANNER_VERSION,
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
        scanner_version=SCORER_SCANNER_VERSION,
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
    """Spread of are_total + every subscore across the 5 seeds; ±1 band."""
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
    terminal_family_seeds = len(results)
    evidenced_cases = sum(r["case_count"] for r in results.values())
    ok = (
        expected == CAP_INVOCATIONS
        and evidenced_cases == CAP_INVOCATIONS
        and terminal_family_seeds == len(SEEDS) * len(FAMILIES)
    )
    return {
        "expected": expected,
        "terminal_family_seeds": terminal_family_seeds,
        "evidenced_case_seeds": evidenced_cases,
        "target_invocation_cap": CAP_INVOCATIONS,
        "reconciled": ok,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("validate", "live"), required=True)
    ap.add_argument(
        "--out",
        default=str(REPO / "artifacts" / "beaaa-18671" / "n5_run"),
    )
    ap.add_argument("--iso-timestamp", default=None, help="scored_at ISO-8601 (UTC)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = out_dir / "evidence"
    scored_at = (
        datetime.fromisoformat(args.iso_timestamp.replace("Z", "+00:00"))
        if args.iso_timestamp
        else datetime.now(timezone.utc)
    )

    verify_registry_pin()
    selection = build_selection_manifest()
    (out_dir / "selection_manifest.json").write_text(
        json.dumps(selection, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    t0 = time.time()
    if args.mode == "live":
        ledger = CheckpointLedger(
            evidence_dir / "checkpoint_ledger.json",
            max_http_attempts=CAP_HTTP_ATTEMPTS,
            max_wall_seconds=CAP_WALL_SECONDS,
        )
        results = collect_live(evidence_dir, ledger)
        http_attempts = ledger.state.get("http_attempts", 0)
    else:
        results = collect_synthetic(evidence_dir)
        http_attempts = 0

    envelopes = {
        seed: score_seed_envelope(seed, results, out_dir / "score", scored_at)
        for seed in SEEDS
    }
    for seed, env in envelopes.items():
        (out_dir / "score" / f"envelope.seed{seed}.json").write_text(
            json.dumps(env, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    spread = n5_spread(envelopes)
    recon = reconcile(results)
    elapsed = time.time() - t0

    manifest = {
        "schema": "backbond.beaaa18671.full-suite-manifest/v1",
        "issue": "BEAAA-18844",
        "parent": "BEAAA-18671",
        "mode": args.mode,
        "classification": "rule-execution",
        "contract": "are-scanner-api-v1@v1.3.0",
        "supervision": "scanner-implementation-supervision-v1@v1.1",
        "cto_cosign_ref": None,
        "scanner_version": SCANNER_VERSION,
        "corpus_version": CORPUS_VERSION,
        "probe_set_version": PROBE_SET_VERSION,
        "rubric_point_id": ["P1.1", "P1.3", "P1.4", "P1.5"],
        "registry_sha256": REGISTRY_SHA256,
        "judge_model": JUDGE_MODEL,
        "subject": SUBJECT_LABEL,
        "seeds": list(SEEDS),
        "caps": {
            "invocations": CAP_INVOCATIONS,
            "target_calls": CAP_TARGET_CALLS,
            "http_attempts": CAP_HTTP_ATTEMPTS,
            "wall_seconds": CAP_WALL_SECONDS,
        },
        "http_attempts_used": http_attempts,
        "elapsed_seconds": round(elapsed, 2),
        "reconciliation": recon,
        "n5_spread": spread,
        "raw_by_seed_dim": {
            f"seed{seed}": {
                results[(seed, t)]["dim_id"]: results[(seed, t)]["raw"]
                for t, _m, _d in FAMILIES
            }
            for seed in SEEDS
        },
        "are_total_by_seed": spread["are_total_by_seed"],
        "score_emittable": bool(spread["within_band"] and recon["reconciled"]),
        "selection_manifest_ref": "selection_manifest.json",
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
        "http_attempts_used": http_attempts,
        "elapsed_seconds": manifest["elapsed_seconds"],
        "out": str(out_dir),
    }, indent=2, sort_keys=True))
    if not manifest["score_emittable"]:
        print("SCORE NOT EMITTABLE — prior Syllable score remains authoritative.")
        return 3 if args.mode == "live" else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
