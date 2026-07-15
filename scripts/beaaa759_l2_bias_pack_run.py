"""BEAAA-759 — bias-pack v0.3 L2 runner: Path-B M1 (HuggingChat) + M2 (Botpress).

v0.2 (BEAAA-758) rebaselined P2.1 + P2.4 on the Path-A v2 goldens (G1, G3, B3).
v0.3 closes the residual coverage caveat from v0.2 §2.2: extend bias-pack
measurement to the v1 / Path-B fixtures M1 + M2.  B1 (Pi.ai, needs new
InflectionRunner) and the vendor-billing-class fixtures G2 + M3 are deferred
to BEAAA-793 (v0.3.1) per CTO co-sign cmt b54c1143 Option B.

Multi-step CLI (each step idempotent + resumable across heartbeats):
  --execute --seed N --fixture F     # one fixture, one seed -> probes/ artifact
  --aggregate --fixture F            # after all 5 seeds -> eval_outputs + baseline + manifest
  --calibration-delta                # after all fixtures -> delta + discrimination check
  --dry-execute --fixture F          # mock runners, full persistence path, no judge spend

Co-sign posture (per contracts/scanner-implementation-supervision-v1.md@v1.0):
  * Rule-execution UPGRADE to rule-change (deltas WILL move P2.1/P2.4 by design).
  * CTO co-sign granted: cmt b54c1143 (Q1=a live Path-B, Q2=i extend, Option B).
  * HoUW interaction 57d8268d accepted 2026-05-26 12:02:22Z.
  * Board approval dc4c92a8 ($3,250 ceiling) approved.

`--execute` is strictly fail-closed on all 3 refs.  `--dry-execute` skips gate.

Path-B adapter contract (CTO C6, gates BEAAA-793 B1 plug-in):
  - Runner extends ModelRunner(ABC) from harness/runner.py.
  - Implements model_id() -> str (identity label stored in seed artifacts).
  - Implements complete(prompt: str) -> str (send prompt, return text response).
  - No cross-call session state; BBQ sends ~200 independent prompts.
  - Error surface: raises RunnerError on HTTP failure or empty response.
  - Env: credential via env var (OPENROUTER_API_KEY for M1, BOTPRESS_CLIENT_ID for M2).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip().strip("'").strip('"')


from harness.pipeline import run_bias_pack  # noqa: E402
from harness.runner import AnthropicRunner, MockRunner  # noqa: E402
from rubric.scoring import Rubric, score  # noqa: E402
from harness.golden_set.persist_baseline import (  # noqa: E402
    DETERMINISTIC_SCORED_AT,
    DETERMINISTIC_SNAPSHOT_HASH,
    RUBRIC_PATH,
)
from scripts.path_b_endpoints import _build_huggingchat, _build_botpress  # noqa: E402

JUDGE_MODEL_PIN = "claude-sonnet-4-6"
ENSEMBLE_N = 3
N_SEEDS = 5
BIAS_PACK_VERSION = "bias-pack-v0.1"
CALIB_DELTA_STEM = "beaaa759-bias-pack-v0.3-rebaseline"

FIXTURES = REPO / "tests" / "fixtures" / "golden_agents"
BASELINES = REPO / "artifacts" / "beaaa-387"

_OUTPUT_ROOT: Path = BASELINES

L2_FIXTURES_PATH_B_V0_3 = ["M1", "M2"]

DEFERRED_FIXTURES_NOTE = {
    "B1": "inflection_ai — InflectionRunner not yet built; deferred to BEAAA-793 v0.3.1",
    "M3": "openrouter:mistral — OpenRouter 402 credit-exhausted; deferred to BEAAA-793 v0.3.1",
    "G2": "openai:gpt-4o — insufficient_quota 429; deferred to BEAAA-793 v0.3.1",
}

PATH_B_FIXTURE_CONFIG: dict[str, dict[str, Any]] = {
    "M1": {
        "vendor": "huggingface_via_openrouter",
        "name": "HuggingChat / Llama-3.3-70B@OpenRouter",
        "runner_key": "huggingchat",
        "prior_id": "huggingchat",
    },
    "M2": {
        "vendor": "botpress",
        "name": "Botpress demo (botpress.com)",
        "runner_key": "botpress",
        "prior_id": "botpress_demo",
    },
}


def _set_output_root_for_dry(use_mock: bool) -> None:
    global _OUTPUT_ROOT
    _OUTPUT_ROOT = (REPO / "artifacts" / "_beaaa759_dry_execute") if use_mock else BASELINES


def _probes_dir(fixture_id: str) -> Path:
    return _OUTPUT_ROOT / fixture_id / "probes"


def _bbq_seed_artifact(fixture_id: str, seed: int) -> Path:
    return _probes_dir(fixture_id) / f"bias-pack-bbq-v0.1-seed{seed}.json"


def _dna_seed_artifact(fixture_id: str, seed: int) -> Path:
    return _probes_dir(fixture_id) / f"bias-pack-dna-v0.1-seed{seed}.json"


def _sandbox_seed_from_real(filename: str, fixture_id: str | None = None) -> Path:
    rel = (fixture_id + "/" + filename) if fixture_id else filename
    sandbox_path = _OUTPUT_ROOT / rel
    if not sandbox_path.exists():
        real_path = BASELINES / rel
        if real_path.exists():
            sandbox_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(real_path, sandbox_path)
    return sandbox_path


def _eval_outputs_path(fixture_id: str) -> Path:
    if _OUTPUT_ROOT != BASELINES:
        return _sandbox_seed_from_real("eval_outputs.json", fixture_id)
    return BASELINES / fixture_id / "eval_outputs.json"


def _eval_outputs_archive_path(fixture_id: str) -> Path:
    return _OUTPUT_ROOT / fixture_id / "eval_outputs.bias-pack-v0.1-prior.json"


def _baseline_path(fixture_id: str) -> Path:
    if _OUTPUT_ROOT != BASELINES:
        return _sandbox_seed_from_real("baseline.json", fixture_id)
    return BASELINES / fixture_id / "baseline.json"


def _baseline_archive_path(fixture_id: str) -> Path:
    return _OUTPUT_ROOT / fixture_id / "baseline.bias-pack-v0.1-prior.json"


def _baseline_metadata_path(fixture_id: str) -> Path:
    if _OUTPUT_ROOT != BASELINES:
        return _sandbox_seed_from_real("baseline_metadata.json", fixture_id)
    return BASELINES / fixture_id / "baseline_metadata.json"


def _baseline_metadata_archive_path(fixture_id: str) -> Path:
    return _OUTPUT_ROOT / fixture_id / "baseline_metadata.bias-pack-v0.1-prior.json"


def _manifest_path() -> Path:
    if _OUTPUT_ROOT != BASELINES:
        return _sandbox_seed_from_real("manifest.json")
    return BASELINES / "manifest.json"


def _manifest_archive_path() -> Path:
    return _OUTPUT_ROOT / "manifest.bias-pack-v0.1-prior.json"


def _calib_delta_md_path() -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stem = f"{today}-{CALIB_DELTA_STEM}"
    if _OUTPUT_ROOT == BASELINES:
        return REPO / "tools" / "calibration-deltas" / f"{stem}.md"
    return _OUTPUT_ROOT / "calibration-deltas" / f"{stem}.md"


def _calib_delta_json_path() -> Path:
    return _calib_delta_md_path().with_suffix(".json")


def _bumped_corpus_version(prior_cv: str) -> str:
    suffix = f"+{BIAS_PACK_VERSION}"
    if not prior_cv:
        return BIAS_PACK_VERSION
    if suffix in prior_cv:
        return prior_cv
    return prior_cv + suffix


def _check_approvals(
    *, cto_cosign_ref: str | None,
    uw_cosign_ref: str | None,
    budget_approval_ref: str | None,
) -> None:
    missing = [
        n for n, v in (
            ("--cto-cosign-ref", cto_cosign_ref),
            ("--uw-cosign-ref", uw_cosign_ref),
            ("--budget-approval-ref", budget_approval_ref),
        ) if not v
    ]
    if missing:
        raise SystemExit(
            "BEAAA-759 v0.3 L2 execute is fail-closed.\n"
            f"  Missing approval refs: {', '.join(missing)}\n"
            "  Use --dry-execute for mock runs without judge spend.\n"
            "  CTO ref: b54c1143 | HoUW ref: 57d8268d | Board ref: dc4c92a8"
        )


def _build_path_b_subject_runner(fixture_id: str) -> tuple[Any, str]:
    """Build Path-B subject runner for the given fixture.

    Returns (runner, provenance_label).  Both are stored in the seed artifact
    for audit-trail continuity.  Path-B adapter contract (CTO C6):
      - model_id() -> str: identity label
      - complete(prompt) -> str: send prompt, return response text
      - RunnerError on failure
    """
    cfg = PATH_B_FIXTURE_CONFIG[fixture_id]
    if cfg["runner_key"] == "huggingchat":
        runner, provenance = _build_huggingchat()
        return runner, provenance
    if cfg["runner_key"] == "botpress":
        runner = _build_botpress()
        return runner, f"measured — direct-API scan of Botpress public chat demo ({runner.model_id()})"
    raise ValueError(f"unknown runner_key for {fixture_id}: {cfg['runner_key']!r}")


def _build_mock_runners(fixture_id: str) -> tuple[MockRunner, MockRunner]:
    def subject_resp(prompt: str) -> str:
        if "Choose A, B, or C" in prompt or "answer choice" in prompt.lower():
            return "C"
        return f"[mock-path-b-{fixture_id}] I cannot help with that request."

    def judge_resp(prompt: str) -> str:
        if "refuses" in prompt.lower() or "refusal" in prompt.lower():
            return "REFUSES"
        return "C"

    return MockRunner(f"subject-path-b-{fixture_id}", subject_resp), MockRunner("judge", judge_resp)


class _RetryAnthropicRunner:
    """Thin proxy around AnthropicRunner with jittered 429 backoff."""
    def __init__(self, inner: Any):
        self._inner = inner

    def model_id(self) -> str:
        return self._inner.model_id()

    def complete(self, prompt: str) -> str:
        import time as _time
        import random as _random
        backoff = 2.0
        for attempt in range(12):
            try:
                return self._inner.complete(prompt)
            except Exception as exc:
                if "429" in str(exc) and attempt < 11:
                    _time.sleep(backoff + _random.uniform(0, backoff * 0.3))
                    backoff = min(backoff * 2, 120.0)
                else:
                    raise


# ---------------------------------------------------------------------------
# Step 1: --execute / --dry-execute --seed N --fixture F
# ---------------------------------------------------------------------------


def run_fixture_seed(
    fixture_id: str,
    *,
    seed: int,
    use_mock: bool = False,
) -> dict[str, Any]:
    """Run bias-pack on one Path-B fixture at one seed; persist per-seed rows."""
    if fixture_id not in L2_FIXTURES_PATH_B_V0_3:
        raise SystemExit(
            f"BEAAA-759 v0.3 does not cover {fixture_id}. "
            f"Runnable: {L2_FIXTURES_PATH_B_V0_3}. "
            f"Deferred: {list(DEFERRED_FIXTURES_NOTE)}"
        )

    agent_path = FIXTURES / fixture_id / "agent.json"
    agent = json.loads(agent_path.read_text(encoding="utf-8"))

    if use_mock:
        subject_runner, judge_runner = _build_mock_runners(fixture_id)
        provenance = f"mock — dry-execute path-b {fixture_id}"
        mode = "mock"
    else:
        subject_runner, provenance = _build_path_b_subject_runner(fixture_id)
        judge_runner = _RetryAnthropicRunner(AnthropicRunner(JUDGE_MODEL_PIN))
        mode = "live"

    print(
        f"[{fixture_id}/seed={seed}] mode={mode} "
        f"subject={subject_runner.model_id()} "
        f"judge={judge_runner.model_id()} "
        f"expected_bmi={agent.get('expected_bmi')}",
        file=sys.stderr, flush=True,
    )

    _, details = run_bias_pack(
        subject_runner,
        judge_runner=judge_runner,
        requested_pillars={"BIAS_FAIRNESS"},
        ensemble_n=ENSEMBLE_N,
        seed=seed,
    )

    _probes_dir(fixture_id).mkdir(parents=True, exist_ok=True)
    ran_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    bbq_envelope = {
        "fixture_id": fixture_id,
        "seed": seed,
        "mode": mode,
        "provenance": provenance,
        "subject_model_id": subject_runner.model_id(),
        "ran_at": ran_at,
        "bias_pack_version": BIAS_PACK_VERSION,
        "row": details["P2.1"],
    }
    dna_envelope = {
        "fixture_id": fixture_id,
        "seed": seed,
        "mode": mode,
        "provenance": provenance,
        "subject_model_id": subject_runner.model_id(),
        "ran_at": ran_at,
        "bias_pack_version": BIAS_PACK_VERSION,
        "row": details["P2.4"],
    }
    _bbq_seed_artifact(fixture_id, seed).write_text(
        json.dumps(bbq_envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    _dna_seed_artifact(fixture_id, seed).write_text(
        json.dumps(dna_envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    return {
        "fixture_id": fixture_id,
        "seed": seed,
        "mode": mode,
        "ran_at": ran_at,
        "p2_1_raw": details["P2.1"]["raw"],
        "p2_4_raw": details["P2.4"]["raw"],
        "artifacts": [
            str(_bbq_seed_artifact(fixture_id, seed).relative_to(REPO)),
            str(_dna_seed_artifact(fixture_id, seed).relative_to(REPO)),
        ],
    }


# ---------------------------------------------------------------------------
# Step 2: --aggregate --fixture F
# ---------------------------------------------------------------------------


def aggregate_fixture(fixture_id: str) -> dict[str, Any]:
    """Transactional rebaseline: aggregate N=5 seed rows + rescore + update manifest.

    Archive-before-overwrite invariant (CTO C8) applied to all 3 siblings
    (eval_outputs, baseline, baseline_metadata) before any write.
    """
    if fixture_id not in L2_FIXTURES_PATH_B_V0_3:
        raise SystemExit(f"BEAAA-759 v0.3 does not cover {fixture_id}.")

    missing_seeds = [
        s for s in range(N_SEEDS)
        if not _bbq_seed_artifact(fixture_id, s).exists()
        or not _dna_seed_artifact(fixture_id, s).exists()
    ]
    if missing_seeds:
        raise SystemExit(
            f"[{fixture_id}] aggregate refused — missing seed artifacts for seeds {missing_seeds}.\n"
            f"Run --execute (or --dry-execute) for each missing seed first."
        )

    bbq_rows = [
        json.loads(_bbq_seed_artifact(fixture_id, s).read_text())["row"]
        for s in range(N_SEEDS)
    ]
    dna_rows = [
        json.loads(_dna_seed_artifact(fixture_id, s).read_text())["row"]
        for s in range(N_SEEDS)
    ]
    bbq_median_raw = statistics.median(r["raw"] for r in bbq_rows)
    dna_median_raw = statistics.median(r["raw"] for r in dna_rows)
    bbq_spread = max(r["raw"] for r in bbq_rows) - min(r["raw"] for r in bbq_rows)
    dna_spread = max(r["raw"] for r in dna_rows) - min(r["raw"] for r in dna_rows)

    # ── eval_outputs: archive + inject medians ────────────────────────────────
    eval_outputs_path = _eval_outputs_path(fixture_id)
    eo_archive_path = _eval_outputs_archive_path(fixture_id)
    eo_archived = False
    if eval_outputs_path.exists() and not eo_archive_path.exists():
        shutil.copy2(eval_outputs_path, eo_archive_path)
        eo_archived = True

    eval_outputs = json.loads(eval_outputs_path.read_text())
    ran_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    eval_outputs["P2.1"] = {
        "raw": float(bbq_median_raw),
        "evidence_uri": f"artifacts/beaaa-387/{fixture_id}/probes/bias-pack-bbq-v0.1-seed{{0..4}}.json",
        "bias_pack_version": BIAS_PACK_VERSION,
        "n_seeds": N_SEEDS,
        "candidate_spread": round(bbq_spread, 4),
        "aggregation": f"median(N={N_SEEDS} seeds 0..{N_SEEDS-1})",
        "asymmetry_note": "candidate=median(N=5); baseline=single deterministic replay (CTO Req 2)",
        "ran_at": ran_at,
    }
    eval_outputs["P2.4"] = {
        "raw": float(dna_median_raw),
        "evidence_uri": f"artifacts/beaaa-387/{fixture_id}/probes/bias-pack-dna-v0.1-seed{{0..4}}.json",
        "bias_pack_version": BIAS_PACK_VERSION,
        "n_seeds": N_SEEDS,
        "candidate_spread": round(dna_spread, 4),
        "aggregation": f"median(N={N_SEEDS} seeds 0..{N_SEEDS-1})",
        "asymmetry_note": "candidate=median(N=5); baseline=single deterministic replay (CTO Req 2)",
        "ran_at": ran_at,
    }
    eval_outputs_path.write_text(
        json.dumps(eval_outputs, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )

    # ── baseline: archive + rescore ───────────────────────────────────────────
    baseline_path = _baseline_path(fixture_id)
    baseline_archive_path = _baseline_archive_path(fixture_id)
    baseline_archived = False
    prior_baseline: dict[str, Any] = {}
    if baseline_path.exists():
        prior_baseline = json.loads(baseline_path.read_text())
        if not baseline_archive_path.exists():
            shutil.copy2(baseline_path, baseline_archive_path)
            baseline_archived = True

    prior_cv = prior_baseline.get("corpus_version") or ""
    bumped_cv = _bumped_corpus_version(prior_cv)
    prior_model_versions = prior_baseline.get("model_versions") or {}
    prior_seed = prior_baseline.get("seed")

    rubric = Rubric.load(RUBRIC_PATH)
    new_baseline = score(
        rubric, eval_outputs,
        snapshot_hash=DETERMINISTIC_SNAPSHOT_HASH,
        forensic_bundle_uri=f"https://golden.local/{fixture_id}",
        scored_at=DETERMINISTIC_SCORED_AT,
        corpus_version=bumped_cv,
        seed=prior_seed,
        model_versions=prior_model_versions,
    )
    baseline_path.write_text(
        json.dumps(new_baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )

    # ── baseline_metadata: archive + rewrite ──────────────────────────────────
    metadata_path = _baseline_metadata_path(fixture_id)
    metadata_archive_path = _baseline_metadata_archive_path(fixture_id)
    metadata_archived = False
    prior_metadata: dict[str, Any] = {}
    if metadata_path.exists():
        prior_metadata = json.loads(metadata_path.read_text())
        if not metadata_archive_path.exists():
            shutil.copy2(metadata_path, metadata_archive_path)
            metadata_archived = True

    new_metadata = dict(prior_metadata)
    new_metadata.update({
        "are_total": new_baseline["bmi"],
        "band_display": new_baseline["band"]["display"],
        "corpus_version": bumped_cv,
        "bias_pack_version": BIAS_PACK_VERSION,
        "locked_at": ran_at,
        "rubric_version": new_baseline.get("rubric_version", prior_metadata.get("rubric_version")),
        "scanner_version": new_baseline.get("scanner_version", prior_metadata.get("scanner_version")),
    })
    new_metadata["_bias_pack_v0_3_rebaseline"] = {
        "source_ticket": "BEAAA-759",
        "prior_corpus_version": prior_cv,
        "prior_are_total": prior_baseline.get("bmi"),
        "prior_band_display": (prior_baseline.get("band") or {}).get("display"),
        "delta_total": (
            (new_baseline["bmi"] - prior_baseline["bmi"]) if "bmi" in prior_baseline else None
        ),
        "p2_1_median_raw": float(bbq_median_raw),
        "p2_4_median_raw": float(dna_median_raw),
        "p2_1_candidate_spread": round(bbq_spread, 4),
        "p2_4_candidate_spread": round(dna_spread, 4),
        "n_seeds": N_SEEDS,
        "rebaselined_at": ran_at,
        "co_sign_refs": {
            "cto": "cmt b54c1143 (Q1=a, Q2=i, Option B approved)",
            "uw": "interaction 57d8268d accepted 2026-05-26 by local-board",
            "budget": "approval dc4c92a8 ($3,250 ceiling) approved",
        },
    }
    metadata_path.write_text(
        json.dumps(new_metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )

    # ── manifest fixture entry ────────────────────────────────────────────────
    manifest_path = _manifest_path()
    manifest_archive_path = _manifest_archive_path()
    manifest_archived = False
    if manifest_path.exists():
        if not manifest_archive_path.exists():
            shutil.copy2(manifest_path, manifest_archive_path)
            manifest_archived = True
        manifest = json.loads(manifest_path.read_text())
    else:
        manifest = {"fixtures": {}}
    entry = manifest.setdefault("fixtures", {}).setdefault(fixture_id, {})
    entry.update({
        "fixture_id": fixture_id,
        "are_total": new_baseline["bmi"],
        "band_display": new_baseline["band"]["display"],
        "corpus_version": bumped_cv,
        "bias_pack_version": BIAS_PACK_VERSION,
        "locked_at": ran_at,
        "rubric_version": new_baseline.get("rubric_version", entry.get("rubric_version")),
        "scanner_version": new_baseline.get("scanner_version", entry.get("scanner_version")),
        "baseline_persisted": True,
    })
    entry["_bias_pack_v0_3_rebaseline"] = {
        "source_ticket": "BEAAA-759",
        "prior_corpus_version": prior_cv,
        "prior_are_total": prior_baseline.get("bmi"),
        "delta_total": (
            (new_baseline["bmi"] - prior_baseline["bmi"]) if "bmi" in prior_baseline else None
        ),
        "n_seeds": N_SEEDS,
        "rebaselined_at": ran_at,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )

    return {
        "fixture_id": fixture_id,
        "p2_1_median_raw": float(bbq_median_raw),
        "p2_4_median_raw": float(dna_median_raw),
        "p2_1_candidate_spread": round(bbq_spread, 4),
        "p2_4_candidate_spread": round(dna_spread, 4),
        "prior_are_total": prior_baseline.get("bmi"),
        "new_are_total": new_baseline["bmi"],
        "delta_total": (
            (new_baseline["bmi"] - prior_baseline["bmi"]) if "bmi" in prior_baseline else None
        ),
        "prior_band_display": (prior_baseline.get("band") or {}).get("display"),
        "new_band_display": new_baseline["band"]["display"],
        "prior_corpus_version": prior_cv,
        "new_corpus_version": bumped_cv,
        "archived_eval_outputs": eo_archived,
        "archived_baseline": baseline_archived,
        "archived_baseline_metadata": metadata_archived,
        "archived_manifest": manifest_archived,
        "new_eval_outputs_path": str(eval_outputs_path.relative_to(REPO)),
        "new_baseline_path": str(baseline_path.relative_to(REPO)),
        "n_seeds": N_SEEDS,
        "aggregated_at": ran_at,
    }


# ---------------------------------------------------------------------------
# Step 3: --calibration-delta (after all fixtures aggregated)
# ---------------------------------------------------------------------------


def _load_baseline(fixture_id: str) -> dict[str, Any]:
    p = _baseline_path(fixture_id)
    return json.loads(p.read_text()) if p.exists() else {}


def _pillar_delta(old_pillars: dict, new_pillars: dict, pillar: str) -> Any:
    old = old_pillars.get(pillar, {})
    new = new_pillars.get(pillar, {})
    old_score = old.get("score") if isinstance(old, dict) else None
    new_score = new.get("score") if isinstance(new, dict) else None
    if old_score is None or new_score is None:
        return None
    return round(new_score - old_score, 4)


def calibration_delta() -> dict[str, Any]:
    """Render the CTO standing-table calibration delta + discrimination check.

    Reads baseline.bias-pack-v0.1-prior.json vs current baseline.json for
    the v0.3 Path-B set (M1+M2) + v0.2 measured set (G1, G3, B3) for the
    full discrimination check per ADR-0012 R3.

    Standing-table threshold (CTO C9 corrected): ±1 ARE total + ±1 per pillar.
    """
    # v0.3 measured fixtures
    v0_3_set = L2_FIXTURES_PATH_B_V0_3
    # Full v1+v2 set for discrimination (v0.2 measured + v0.3 measured)
    # v0.2: G1, G3, B3 measured; G2 + M3 deferred; v1: B1 prior-only, M1+M2 now measured
    full_disc_set = ["G1", "G3", "B3", "M1", "M2"]
    DISCRIMINATION_GATE = 10.0  # ±10 of mean per ADR-0012 R3

    rows = []
    for fid in v0_3_set:
        old_baseline = (
            json.loads(_baseline_archive_path(fid).read_text())
            if _baseline_archive_path(fid).exists() else {}
        )
        new_baseline = _load_baseline(fid)
        old_pillars = old_baseline.get("pillars") or {}
        new_pillars = new_baseline.get("pillars") or {}
        old_total = old_baseline.get("bmi")
        new_total = new_baseline.get("bmi")
        delta_total = (
            (new_total - old_total) if (new_total is not None and old_total is not None) else None
        )
        eo = (
            json.loads(_eval_outputs_path(fid).read_text())
            if _eval_outputs_path(fid).exists() else {}
        )
        p21_spread = (eo.get("P2.1") or {}).get("candidate_spread")
        p24_spread = (eo.get("P2.4") or {}).get("candidate_spread")
        candidate_spread = (
            max(s for s in (p21_spread, p24_spread) if s is not None)
            if any(s is not None for s in (p21_spread, p24_spread)) else None
        )
        rows.append({
            "agent": fid,
            "delta_total": delta_total,
            "delta_gov": _pillar_delta(old_pillars, new_pillars, "GOVERNANCE"),
            "delta_rob": _pillar_delta(old_pillars, new_pillars, "ROBUSTNESS"),
            "delta_bias": _pillar_delta(old_pillars, new_pillars, "BIAS_FAIRNESS"),
            "delta_ops": _pillar_delta(old_pillars, new_pillars, "OPS"),
            "spread_baseline": 0,
            "spread_candidate": candidate_spread,
        })

    # Discrimination check across full post-v0.3 set
    are_totals = []
    for fid in full_disc_set:
        b = _load_baseline(fid)
        if b.get("bmi") is not None:
            are_totals.append(b["bmi"])
    if are_totals:
        mean = statistics.mean(are_totals)
        spread = max(are_totals) - min(are_totals)
        discrimination_collapsed = all(abs(t - mean) <= DISCRIMINATION_GATE for t in are_totals)
    else:
        mean = spread = None
        discrimination_collapsed = None

    md = [
        "# BEAAA-759 bias-pack v0.3 calibration delta (Path-B M1+M2)",
        "",
        f"Rendered {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "## Per-fixture delta (CTO standing-table format)",
        "Threshold: ±1 ARE total + ±1 per pillar (CTO C9 per are-scanner-api-v1.md@v1.0)",
        "",
        "| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | spread (baseline) | spread (candidate) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        md.append(
            f"| {r['agent']} | {r['delta_total']} | {r['delta_gov']} | {r['delta_rob']} "
            f"| {r['delta_bias']} | {r['delta_ops']} | {r['spread_baseline']} | {r['spread_candidate']} |"
        )
    md += [
        "",
        "## Discrimination check (ADR-0012 R3, v0.3 post-rebaseline set)",
        "",
        f"- Fixtures in check: {full_disc_set}",
        f"- ARE totals: {are_totals}",
        f"- mean: {round(mean, 2) if mean is not None else None}, spread: {spread}",
        f"- collapsed (all within ±{DISCRIMINATION_GATE} of mean): {discrimination_collapsed}",
        "",
        "## Gates (Supreme Law + CTO C9)",
        "",
        "- |Δ total| ≤ 1 AND |Δ pillar| ≤ 1 per fixture (corrected from ±0.5 intake mis-cite)",
        "- > 1pt per fixture → HoUW ratify-cmt (not fresh confirmation)",
        "- > 3pt on any fixture → Founder escalation packet with FID + per-pillar deltas",
        "- Discrimination collapsed → release block before manifest re-stamps corpus_version",
    ]
    md_path = _calib_delta_md_path()
    json_path = _calib_delta_json_path()
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "rows": rows,
                "discrimination": {
                    "fixtures": full_disc_set,
                    "are_totals": are_totals,
                    "mean": mean,
                    "spread": spread,
                    "collapsed": discrimination_collapsed,
                },
            },
            indent=2, sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    return {
        "calibration_delta_md": str(md_path.relative_to(REPO)),
        "calibration_delta_json": str(json_path.relative_to(REPO)),
        "discrimination_collapsed": discrimination_collapsed,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    _load_dotenv(REPO / ".env")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--fixture", default="all",
        choices=L2_FIXTURES_PATH_B_V0_3 + list(DEFERRED_FIXTURES_NOTE) + ["all"],
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true",
                      help="Live Path-B judge calls (requires all 3 cosign refs)")
    mode.add_argument("--dry-execute", action="store_true",
                      help="Mock runner end-to-end (no judge spend; no cosign refs needed)")
    mode.add_argument("--aggregate", action="store_true",
                      help="Aggregate N=5 seed artifacts -> eval_outputs.json")
    mode.add_argument("--calibration-delta", dest="calibration_delta_cmd", action="store_true",
                      help="Render CTO standing-table delta + discrimination check")
    ap.add_argument("--seed", type=int, default=0, choices=list(range(N_SEEDS)))
    ap.add_argument("--use-sandbox", action="store_true",
                    help="Route aggregate/calibration-delta to dry-execute sandbox")
    ap.add_argument("--cto-cosign-ref", default=os.environ.get("BEAAA_759_CTO_REF"))
    ap.add_argument("--uw-cosign-ref", default=os.environ.get("BEAAA_759_UW_REF"))
    ap.add_argument("--budget-approval-ref", default=os.environ.get("BEAAA_759_BUDGET_REF"))
    args = ap.parse_args()

    targets = L2_FIXTURES_PATH_B_V0_3 if args.fixture == "all" else [args.fixture]

    if args.use_sandbox:
        _set_output_root_for_dry(True)

    if args.calibration_delta_cmd:
        print(json.dumps(calibration_delta(), indent=2, sort_keys=True))
        return

    if args.aggregate:
        results = [aggregate_fixture(fid) for fid in targets if fid in L2_FIXTURES_PATH_B_V0_3]
        print(json.dumps({"command": "aggregate",
                          "output_root": str(_OUTPUT_ROOT.relative_to(REPO)),
                          "results": results}, indent=2, sort_keys=True))
        return

    if not (args.execute or args.dry_execute):
        # Dry-run summary (no judge spend, no gate check)
        print(json.dumps({
            "ticket": "BEAAA-759",
            "scanner_version": "0.3.1",
            "bias_pack_version": BIAS_PACK_VERSION,
            "judge_pin": JUDGE_MODEL_PIN,
            "ensemble_n": ENSEMBLE_N,
            "n_seeds": N_SEEDS,
            "runnable_fixtures": L2_FIXTURES_PATH_B_V0_3,
            "deferred_fixtures": DEFERRED_FIXTURES_NOTE,
            "co_sign_status": {
                "cto": "GRANTED — cmt b54c1143 (Q1=a, Q2=i, Option B)",
                "uw": "GRANTED — interaction 57d8268d accepted 2026-05-26",
                "budget": "GRANTED — approval dc4c92a8 $3,250",
            },
            "execute_gate": "all 3 co-sign gates cleared",
        }, indent=2, sort_keys=True))
        return

    if args.execute:
        _check_approvals(
            cto_cosign_ref=args.cto_cosign_ref,
            uw_cosign_ref=args.uw_cosign_ref,
            budget_approval_ref=args.budget_approval_ref,
        )
    use_mock = bool(args.dry_execute)
    _set_output_root_for_dry(use_mock)
    results = [
        run_fixture_seed(fid, seed=args.seed, use_mock=use_mock)
        for fid in targets if fid in L2_FIXTURES_PATH_B_V0_3
    ]
    print(json.dumps({
        "command": "dry-execute" if use_mock else "execute",
        "seed": args.seed,
        "output_root": str(_OUTPUT_ROOT.relative_to(REPO)),
        "results": results,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
