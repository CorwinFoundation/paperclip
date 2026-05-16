"""BEAAA-396 / BEAAA-387b — Path A v0.2.3 baselines for G1, G2, G3, B2, B3.

Runs the 4 measured-dim probe slate (P1.1 truthfulqa, P3.1 prompt_injection,
P3.2 indirect_injection, P3.3 secret_exfil) against the 5 live-API Path A
golden fixtures, merges with the appropriate per-vendor priors (17 prior-derived
dims), scores under v0.2.3, persists the baseline + sidecar + fixture-local
eval_outputs.json + expected_score.json, and flips the fixture's agent.json
from `pending_baseline_scan` to `live`.

Per-fixture probe artifacts are written to
`artifacts/beaaa-387/{fid}/probes/{benchname}.json` and referenced from each
measured dim's `evidence_uri` so the §2 calibration / §3 reproducibility /
§5 discrimination wiring (BEAAA-387d) can click through from score to evidence.

Usage:
    python3 scripts/path_a_baselines.py --fixture G1
    python3 scripts/path_a_baselines.py --fixture all
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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


_load_dotenv(REPO / ".env")

from harness.bench import indirect_injection, prompt_injection, secret_exfil, truthfulqa  # noqa: E402
from harness.golden_set.persist_baseline import (  # noqa: E402
    DETERMINISTIC_SCORED_AT,
    DETERMINISTIC_SNAPSHOT_HASH,
    persist_from_live_path_a,
    update_manifest,
)
from rubric.scoring import Rubric, score  # noqa: E402
from harness.pipeline import load_priors, merge_measurements_with_priors  # noqa: E402
from harness.runner import AnthropicRunner, GeminiRunner, OpenAIRunner, OpenRouterRunner  # noqa: E402

PRIORS_DIR = REPO / "scripts" / "agent_priors"
HARDENED_PROMPT_PATH = REPO / "harness" / "golden_set" / "system_prompt_v1.txt"
ANTI_SAFETY_PROMPT_PATH = REPO / "harness" / "golden_set" / "anti_safety_prompt_internal_v1.txt"
FIXTURES = REPO / "tests" / "fixtures" / "golden_agents"
BASELINES = REPO / "artifacts" / "beaaa-387"

CORPUS_VERSION = "path_a_v2_1@2026-05-15"

BENCHES = [
    ("truthfulqa-mc1-poc", REPO / "harness" / "fixtures" / "truthfulqa_mc1_sample.jsonl",
     truthfulqa.load_items, truthfulqa.run),
    ("prompt-injection-poc", REPO / "harness" / "fixtures" / "prompt_injection_poc.jsonl",
     prompt_injection.load_items, prompt_injection.run),
    ("indirect-injection-poc", REPO / "harness" / "fixtures" / "indirect_injection_poc.jsonl",
     indirect_injection.load_items, indirect_injection.run),
    ("secret-exfil-poc", REPO / "harness" / "fixtures" / "secret_exfil_poc.jsonl",
     secret_exfil.load_items, secret_exfil.run),
]


def _sha256_text(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def _sdk_version(name: str) -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version(name)
        except PackageNotFoundError:
            return "unknown"
    except Exception:
        return "unknown"


# Fixture configuration. system_prompt_kind:
#   hardened       — harness/golden_set/system_prompt_v1.txt (G1/G2/G3)
#   none           — no system prompt (B2)
#   anti_safety    — anti_safety_prompt_internal_v1.txt (B3, internal_only)
FIXTURE_CONFIG: dict[str, dict[str, Any]] = {
    "G1": {
        # Note: anthropic/claude-3.7-sonnet retired on OpenRouter (only 3.5-haiku
        # remains in the 3.x family — sub-on-retirement to closest current Sonnet
        # per path_a_v2_1.py precedent). Recorded in baseline_metadata.model_versions.
        "adapter": "openrouter",
        "model": "anthropic/claude-sonnet-4.6",
        "model_version_label": "anthropic/claude-sonnet-4.6@openrouter",
        "model_substitution_note": "claude-3.7-sonnet retired on OpenRouter; sub to claude-sonnet-4.6 (closest current Sonnet)",
        "system_prompt_kind": "hardened",
        "prior_id": "anthropic_claude",
        "vendor_sdk": ("httpx", "openrouter-http"),
    },
    "G2": {
        "adapter": "openai",
        "model": "gpt-4o",
        "model_version_label": "openai/gpt-4o",
        "system_prompt_kind": "hardened",
        "prior_id": "openai_chatgpt",
        "vendor_sdk": ("openai", "openai-python"),
    },
    "G3": {
        # Note: gemini-2.0-flash retired by Google ("no longer available to new
        # users") — sub to gemini-2.5-flash (current flagship Flash). Recorded in
        # baseline_metadata.model_versions.
        "adapter": "gemini",
        "model": "gemini-2.5-flash",
        "model_version_label": "google/gemini-2.5-flash",
        "model_substitution_note": "gemini-2.0-flash retired by Google; sub to gemini-2.5-flash (current flagship Flash)",
        "system_prompt_kind": "hardened",
        "prior_id": "google_gemini",
        "vendor_sdk": ("google-genai", "google-genai"),
        # Gemini SDK accepts seed; verified separately. If a future SDK build
        # rejects it, repro_gate widens the tolerance via seed_accepted=False.
        "seed_accepted": True,
    },
    "B2": {
        "adapter": "openrouter",
        "model": "mistralai/mistral-7b-instruct-v0.1",
        "model_version_label": "mistralai/mistral-7b-instruct-v0.1@openrouter",
        "system_prompt_kind": "none",
        "prior_id": "huggingchat",  # open-weights surface — closest available prior
        "vendor_sdk": ("httpx", "openrouter-http"),
    },
    "B3": {
        "adapter": "openai",
        "model": "gpt-3.5-turbo",
        "model_version_label": "openai/gpt-3.5-turbo",
        "system_prompt_kind": "anti_safety",
        "prior_id": "openai_chatgpt",
        "vendor_sdk": ("openai", "openai-python"),
        "internal_only": True,
    },
}


def _system_prompt(kind: str) -> tuple[str | None, str | None]:
    """Return (prompt_text, sha256_for_stamp). The sha256 is only stamped on
    the baseline for hardened fixtures (G1/G2/G3). B3's anti-safety prompt is
    hashed for repro but is internal_only — its hash is recorded in the
    sidecar but the prompt text never enters external artifacts."""
    if kind == "none":
        return None, None
    if kind == "hardened":
        p = HARDENED_PROMPT_PATH
        return p.read_text(encoding="utf-8"), _sha256_text(p)
    if kind == "anti_safety":
        p = ANTI_SAFETY_PROMPT_PATH
        return p.read_text(encoding="utf-8"), _sha256_text(p)
    raise ValueError(f"unknown system_prompt_kind: {kind}")


def _build_runner(cfg: dict[str, Any], system_prompt: str | None) -> Any:
    adapter = cfg["adapter"]
    if adapter == "openrouter":
        return OpenRouterRunner(model=cfg["model"], system_prompt=system_prompt)
    if adapter == "openai":
        return OpenAIRunner(model=cfg["model"], seed=42, system_prompt=system_prompt)
    if adapter == "anthropic":
        return AnthropicRunner(model=cfg["model"], system_prompt=system_prompt)
    if adapter == "gemini":
        return GeminiRunner(model=cfg["model"], seed=42, system_prompt=system_prompt)
    raise ValueError(f"unknown adapter: {adapter}")


RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"


def run_fixture(fixture_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    cfg = FIXTURE_CONFIG[fixture_id]
    agent = json.loads((FIXTURES / fixture_id / "agent.json").read_text())
    expected_range = agent.get("expected_bmi_range")
    print(f"[{fixture_id}] adapter={cfg['adapter']} model={cfg['model']} "
          f"system_prompt={cfg['system_prompt_kind']} expected={expected_range}",
          file=sys.stderr, flush=True)

    system_prompt, sysprompt_sha = _system_prompt(cfg["system_prompt_kind"])
    runner = _build_runner(cfg, system_prompt)

    probes_dir = BASELINES / fixture_id / "probes"
    probes_dir.mkdir(parents=True, exist_ok=True)

    measurements: list[dict[str, Any]] = []
    probe_uris: dict[str, str] = {}
    for bench_name, fixture_path, loader, run_fn in BENCHES:
        print(f"  [{fixture_id}] bench {bench_name}...", file=sys.stderr, flush=True)
        items = loader(fixture_path)
        m = run_fn(runner, items)
        # Persist per-probe artifact under artifacts/beaaa-387/{fid}/probes/.
        probe_path = probes_dir / f"{bench_name}.json"
        probe_payload = {
            "fixture_id": fixture_id,
            "bench": bench_name,
            "model_id": m["model_id"],
            "n_items": m["n_items"],
            "dim_id": m["dim_id"],
            "raw": m["raw"],
            "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "trace_summary": [
                {k: v for k, v in t.items() if k not in {"raw_response"}}
                for t in m.get("trace", [])
            ],
        }
        probe_path.write_text(json.dumps(probe_payload, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")
        measurements.append(m)
        probe_uris[m["dim_id"]] = f"artifacts/beaaa-387/{fixture_id}/probes/{bench_name}.json"
        print(f"  [{fixture_id}] {bench_name}: dim={m['dim_id']} raw={m['raw']} "
              f"n_items={m['n_items']}", file=sys.stderr, flush=True)

    prior_path = PRIORS_DIR / f"{cfg['prior_id']}.json"
    _, prior_scores = load_priors(prior_path)
    raw_scores, _provenance = merge_measurements_with_priors(measurements, prior_scores)

    # Build eval_outputs with evidence_uri pointing at the per-probe artifact for
    # the 4 measured dims, and at the prior file for the 17 prior-derived dims.
    eval_outputs: dict[str, dict[str, Any]] = {}
    for dim, raw in raw_scores.items():
        if dim in probe_uris:
            uri = probe_uris[dim]
        else:
            uri = f"scripts/agent_priors/{cfg['prior_id']}.json"
        eval_outputs[dim] = {"raw": float(raw), "evidence_uri": uri}

    sdk_pkg, sdk_label = cfg["vendor_sdk"]
    sdk_versions = {sdk_label: _sdk_version(sdk_pkg)}

    model_versions = {cfg["model_version_label"]: "live-api-as-of-2026-05-15"}

    if dry_run:
        # Score without persisting; emit a breach_report so the UW gate has the
        # full per-dim picture even when the total falls outside the calibration
        # band. Does NOT touch agent.json or eval_outputs.json in the fixture dir.
        rubric = Rubric.load(RUBRIC_PATH)
        actual = score(
            rubric, eval_outputs,
            snapshot_hash=DETERMINISTIC_SNAPSHOT_HASH,
            forensic_bundle_uri=f"https://golden.local/{fixture_id}",
            scored_at=DETERMINISTIC_SCORED_AT,
            corpus_version=CORPUS_VERSION,
            seed=42,
            model_versions=model_versions,
        )
        lo, hi = (int(expected_range[0]), int(expected_range[1])) if expected_range else (None, None)
        in_range = (lo is not None) and (lo <= actual["bmi"] <= hi)
        breach_report = {
            "fixture_id": fixture_id,
            "dry_run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "adapter": cfg["adapter"],
            "model": cfg["model"],
            "model_substitution_note": cfg.get("model_substitution_note"),
            "system_prompt_kind": cfg["system_prompt_kind"],
            "are_total": actual["bmi"],
            "band_display": actual["band"]["display"],
            "expected_bmi_range": list(expected_range) if expected_range else None,
            "in_range": in_range,
            "delta_from_range": (
                None if expected_range is None
                else (0 if in_range else (actual["bmi"] - lo if actual["bmi"] < lo else actual["bmi"] - hi))
            ),
            "measured_dims": {m["dim_id"]: {"raw": m["raw"], "n_items": m["n_items"]} for m in measurements},
            "pillar_scores": {p: pdata["score"] for p, pdata in actual["pillars"].items()},
            "prior_id": cfg["prior_id"],
            "corpus_version": CORPUS_VERSION,
            "scanner_version": actual["scanner_version"],
            "rubric_version": actual["rubric_version"],
        }
        BASELINES.mkdir(parents=True, exist_ok=True)
        breach_path = BASELINES / fixture_id / "breach_report.json"
        breach_path.parent.mkdir(parents=True, exist_ok=True)
        breach_path.write_text(json.dumps(breach_report, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
        print(f"[{fixture_id}] DRY-RUN are_total={actual['bmi']} band={actual['band']['display']} "
              f"in_range={in_range} delta={breach_report['delta_from_range']} "
              f"(range={expected_range})", file=sys.stderr, flush=True)
        return breach_report

    sidecar = persist_from_live_path_a(
        fixture_id,
        eval_outputs=eval_outputs,
        model_versions=model_versions,
        corpus_version=CORPUS_VERSION,
        seed=42,
        system_prompt_sha256=sysprompt_sha if cfg["system_prompt_kind"] != "none" else None,
        expected_bmi_range=expected_range,
        sdk_versions=sdk_versions,
        seed_accepted=cfg.get("seed_accepted", True),
        source_artifact=f"artifacts/beaaa-387/{fixture_id}/baseline.json",
        internal_only=bool(cfg.get("internal_only", False)),
    )
    # Surface vendor-side model substitution in the sidecar (not in
    # model_versions, which stays dict[str,str] for type cleanliness).
    if cfg.get("model_substitution_note"):
        from harness.golden_set.persist_baseline import _load_json, _write_json
        sidecar_path = BASELINES / fixture_id / "baseline_metadata.json"
        merged = _load_json(sidecar_path)
        merged["model_substitution_note"] = cfg["model_substitution_note"]
        _write_json(sidecar_path, merged)
        sidecar = merged

    # Promote agent.json: status -> live, drop the range, stamp expected_bmi
    # and expected_band singletons matching the actual measurement.
    actual_bmi = sidecar["are_total"]
    actual_band = sidecar["band_display"]
    promoted_agent = {
        **agent,
        "status": "live",
        "expected_bmi": actual_bmi,
        "expected_band": actual_band,
        "measurement_date": datetime.now(timezone.utc).date().isoformat(),
        "rubric_version_at_measurement": "0.1.2",
        "source_artifact": f"artifacts/beaaa-387/{fixture_id}/baseline.json",
    }
    # Preserve the range record for the calibration audit trail.
    promoted_agent["expected_bmi_range"] = list(expected_range) if expected_range else None
    promoted_agent.pop("baseline_scan_due", None)
    (FIXTURES / fixture_id / "agent.json").write_text(
        json.dumps(promoted_agent, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    manifest_entry = {
        "fixture_id": fixture_id,
        "slot": agent.get("slot"),
        "type": agent.get("type"),
        "vendor": agent.get("vendor"),
        "lock_source": "live_path_a",
        "baseline_persisted": True,
        "locked_at": sidecar["locked_at"],
        "scanner_version": sidecar["scanner_version"],
        "rubric_version": sidecar["rubric_version"],
        "corpus_version": sidecar["corpus_version"],
        "seed": sidecar["seed"],
        "are_total": sidecar["are_total"],
        "band_display": sidecar["band_display"],
        "expected_bmi": actual_bmi,
        "expected_band": actual_band,
        "expected_bmi_range": list(expected_range) if expected_range else None,
        "agent_status": "live",
    }
    if cfg.get("internal_only"):
        manifest_entry["internal_only"] = True
    update_manifest({fixture_id: manifest_entry})

    print(f"[{fixture_id}] persisted: are_total={actual_bmi} band={actual_band} "
          f"(range={expected_range})", file=sys.stderr, flush=True)
    return sidecar


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True,
                        choices=list(FIXTURE_CONFIG.keys()) + ["all"])
    parser.add_argument("--dry-run", action="store_true",
                        help="probe + score but DO NOT persist; write breach_report.json instead")
    args = parser.parse_args()

    targets = list(FIXTURE_CONFIG.keys()) if args.fixture == "all" else [args.fixture]
    results: dict[str, dict[str, Any]] = {}
    for fid in targets:
        try:
            results[fid] = run_fixture(fid, dry_run=args.dry_run)
        except Exception as exc:
            results[fid] = {"error": str(exc), "type": type(exc).__name__}
            print(f"[{fid}] FAILED: {type(exc).__name__}: {str(exc)[:200]}",
                  file=sys.stderr, flush=True)

    print(json.dumps({k: {"are_total": v.get("are_total"),
                          "band": v.get("band_display"),
                          "in_range": v.get("in_range"),
                          "error": v.get("error")}
                      for k, v in results.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
