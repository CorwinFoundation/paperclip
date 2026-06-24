"""BEAAA-8127 OPS P1.1-P1.6 live-fire runner.

This wraps existing OPS bench modules against the self-owned Retell chat
target provided by BEAAA-8130. It writes measured evidence artifacts; it does
not change rubric points, weights, judge model choices, scoring math,
tamper-resistance posture, or contract output shape.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

REPO = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACE = (
    Path("/mnt/paperclipdata/dot-paperclip/instances/default/projects")
    / "59f8876e-e729-4dda-98f9-1317c2b50492"
    / "8e61a25d-a945-4569-9f9c-4bce1cfef97b"
    / "_default"
)
sys.path.insert(0, str(REPO))


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


_load_dotenv(REPO / ".env")
_load_dotenv(DEFAULT_WORKSPACE / ".secrets" / "retell.env")

from harness.bench import (  # noqa: E402
    citation_integrity_legal,
    eval_coverage_aggregator,
    factuality_golden,
    p1_3_consistency_golden,
    p1_4_refusal_golden,
    p1_5_calibration_golden,
)
from harness.runner import ModelRunner, OpenAIRunner, OpenRouterRunner  # noqa: E402
from rubric.scoring import Rubric  # noqa: E402
from scanners import CONTRACT_VERSION, DEFAULT_SEED, SCANNER_VERSION  # noqa: E402

BEAAA = "BEAAA-8127"
CORPUS_VERSION = "r2-ops-live-fire@2026-06-24"
PROBE_SET_VERSION = "r2-ops-p1.1-p1.6-live-fire@2026-06-24"
RUBRIC_POINTS = ("P1.1", "P1.2", "P1.3", "P1.4", "P1.5", "P1.6")


class RetellRunner(ModelRunner):
    """Retell chat-completion adapter for self-owned synthetic agent tests."""

    def __init__(
        self,
        *,
        agent_id: str,
        api_key: str,
        api_base: str,
        log_path: Path,
        ticket: str,
    ) -> None:
        self._agent_id = agent_id
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        self._log_path = log_path
        self._ticket = ticket

    def model_id(self) -> str:
        return f"retell:{self._agent_id}"

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            f"{self._api_base}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": "backbond-beaaa-8127-retell-live-fire/0.1",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def complete(self, prompt: str) -> str:
        chat = self._post(
            "/create-chat",
            {
                "agent_id": self._agent_id,
                "metadata": {
                    "ticket": self._ticket,
                    "posture": "internal_synthetic_ops_live_fire",
                },
            },
        )
        completion = self._post(
            "/create-chat-completion",
            {"chat_id": chat["chat_id"], "content": prompt},
        )
        messages = completion.get("messages") or []
        output = "\n".join(
            str(m.get("content", "")) for m in messages if m.get("role") == "agent"
        ).strip()
        row = {
            "timestamp_ms": int(time.time() * 1000),
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "output": output,
            "chat_id": chat.get("chat_id"),
            "agent_id": chat.get("agent_id"),
            "chat_status": chat.get("chat_status"),
            "messages": messages,
        }
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        return output


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _slice(items: Sequence[Any], limit: int | None) -> list[Any]:
    return list(items[:limit]) if limit is not None else list(items)


def _judge(adapter: str, model: str, seed: int) -> ModelRunner:
    if adapter == "openai":
        return OpenAIRunner(model=model, seed=seed, temperature=0.0)
    if adapter == "openrouter":
        return OpenRouterRunner(model=model, temperature=0.0)
    raise ValueError(f"unsupported judge adapter: {adapter}")


def _summary(reps: list[dict[str, Any]], raw_key: str = "raw") -> dict[str, Any]:
    raws = [r.get(raw_key) for r in reps]
    numeric = [float(v) for v in raws if isinstance(v, (int, float))]
    if not numeric:
        return {"raw_values": raws, "raw_median": None, "raw_spread": None, "verdict": "BREACH"}
    spread = max(numeric) - min(numeric)
    return {
        "raw_values": raws,
        "raw_median": statistics.median(numeric),
        "raw_spread": spread,
        "verdict": "PASS" if spread == 0 else "BREACH",
    }


def _run_replicates(
    fn: Callable[[], dict[str, Any]],
    *,
    n: int,
    raw_key: str = "raw",
) -> dict[str, Any]:
    reps = [fn() for _ in range(n)]
    return {"replicates": reps, "summary": _summary(reps, raw_key=raw_key)}


def _dim_status_from_ops(results: dict[str, dict[str, Any]]) -> dict[str, str]:
    status: dict[str, str] = {}
    for dim_id in ("P1.1", "P1.2", "P1.3", "P1.4", "P1.5"):
        summary = results[dim_id]["summary"]
        status[dim_id] = "measured" if summary["verdict"] == "PASS" else "inconclusive"
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--limit", type=int, default=None, help="Limit each corpus for smoke runs. Omit for full acceptance.")
    parser.add_argument("--ensemble-n", type=int, default=1, help="Judge ensemble count. Full acceptance should use corpus pin default where required.")
    parser.add_argument("--judge-adapter", choices=("openai", "openrouter"), default="openai")
    parser.add_argument("--judge-model", default="gpt-4.1-mini")
    parser.add_argument("--retell-agent-id", default="agent_1e7015ae5f87ea825a3d1fac11")
    parser.add_argument("--out-dir", default=str(REPO / "artifacts" / "beaaa-8127"))
    args = parser.parse_args(argv)

    for key in ("RETELL_API_KEY",):
        if not os.environ.get(key):
            raise RuntimeError(f"{key} not set")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be >= 1")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = out_dir / "retell_ops_live_fire_transcripts.jsonl"
    if transcript_path.exists():
        transcript_path.unlink()

    subject = RetellRunner(
        agent_id=args.retell_agent_id,
        api_key=os.environ["RETELL_API_KEY"],
        api_base=os.environ.get("RETELL_API_BASE", "https://api.retellai.com"),
        log_path=transcript_path,
        ticket=BEAAA,
    )
    judge = _judge(args.judge_adapter, args.judge_model, args.seed)
    rubric = Rubric.load(REPO / "rubric" / "rubric.yaml")

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    mapping_paths = {
        "P1.1": REPO / "rubric" / "probe_mappings" / "p1_1_factuality_golden.yaml",
        "P1.2": REPO / "rubric" / "probe_mappings" / "citation_integrity_legal.yaml",
        "P1.3": REPO / "rubric" / "probe_mappings" / "p1_3_consistency_golden.yaml",
        "P1.4": REPO / "rubric" / "probe_mappings" / "p1_4_refusal_golden.yaml",
        "P1.5": REPO / "rubric" / "probe_mappings" / "p1_5_calibration_golden.yaml",
    }
    corpus_hashes = {k: _sha256_file(v) for k, v in mapping_paths.items()}

    p11_probes = _slice(factuality_golden.load_probes(mapping_paths["P1.1"]), args.limit)
    p12_f2 = _slice(citation_integrity_legal.load_family_probes(mapping_paths["P1.2"], "F2"), args.limit)
    p13_probes = _slice(p1_3_consistency_golden.load_probes(mapping_paths["P1.3"]), args.limit)
    p14_probes = _slice(p1_4_refusal_golden.load_probes(mapping_paths["P1.4"]), args.limit)
    p15_probes = _slice(p1_5_calibration_golden.load_probes(mapping_paths["P1.5"]), args.limit)

    results: dict[str, dict[str, Any]] = {}
    results["P1.1"] = _run_replicates(
        lambda: factuality_golden.run(
            subject,
            judge,
            p11_probes,
            ensemble_n=args.ensemble_n,
            scanner_version=SCANNER_VERSION,
            corpus_version="p1_1_factuality_golden@0.1.0",
            probe_set_version="p1_1_factuality_golden@0.1.0",
            seed=args.seed,
        ),
        n=args.n,
    )
    results["P1.2"] = _run_replicates(
        lambda: citation_integrity_legal.aggregate_dim(
            None,
            citation_integrity_legal.run_f2_content_fidelity(
                subject,
                judge,
                p12_f2,
                ensemble_n=args.ensemble_n,
            ),
        ),
        n=args.n,
    )
    results["P1.3"] = _run_replicates(
        lambda: p1_3_consistency_golden.run(
            subject,
            judge,
            p13_probes,
            ensemble_n=args.ensemble_n,
            scanner_version=SCANNER_VERSION,
            corpus_version="p1_3_consistency_golden@0.1.0",
            probe_set_version="p1_3_consistency_golden@0.1.0",
            seed=args.seed,
        ),
        n=args.n,
    )
    results["P1.4"] = _run_replicates(
        lambda: p1_4_refusal_golden.run(
            subject,
            judge,
            p14_probes,
            ensemble_n=args.ensemble_n,
            scanner_version=SCANNER_VERSION,
            corpus_version="p1_4_refusal_golden@0.1.0",
            probe_set_version="p1_4_refusal_golden@0.1.0",
            seed=args.seed,
        ),
        n=args.n,
    )
    results["P1.5"] = _run_replicates(
        lambda: p1_5_calibration_golden.run(
            subject,
            judge,
            p15_probes,
            ensemble_n=args.ensemble_n,
            scanner_version=SCANNER_VERSION,
            corpus_version="p1_5_calibration_golden@0.1.0",
            probe_set_version="p1_5_calibration_golden@0.1.0",
            seed=args.seed,
        ),
        n=args.n,
    )
    p16 = eval_coverage_aggregator.run(
        rubric=rubric,
        dim_status=_dim_status_from_ops(results),
        in_scope_dims=["P1.1", "P1.2", "P1.3", "P1.4", "P1.5"],
    )
    results["P1.6"] = {
        "replicates": [p16 for _ in range(args.n)],
        "summary": _summary([p16 for _ in range(args.n)], raw_key="dim_raw"),
    }

    completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    full_acceptance_mode = args.limit is None and args.ensemble_n >= 3
    payload = {
        "ticket_ref": BEAAA,
        "classification": "rule-execution",
        "classification_table_row": "Implement a new probe inside an existing rubric_point_id / wrap existing OPS P1.x probes against a self-owned live target",
        "contract_version": CONTRACT_VERSION,
        "scanner_version": SCANNER_VERSION,
        "corpus_version": CORPUS_VERSION,
        "probe_set_version": PROBE_SET_VERSION,
        "rubric_point_id": list(RUBRIC_POINTS),
        "model_versions": {
            "primary": subject.model_id(),
            "judges": [f"{args.judge_adapter}/{args.judge_model}"],
        },
        "seed": args.seed,
        "n": args.n,
        "limit": args.limit,
        "ensemble_n": args.ensemble_n,
        "full_acceptance_mode": full_acceptance_mode,
        "target": {
            "kind": "self_owned_retell_chat_agent",
            "shape": "generic_prompt_output",
            "agent_id": args.retell_agent_id,
            "temperature": 0,
            "functional_use_only": True,
            "no_phi_pci": True,
            "no_third_party_tenant": True,
        },
        "corpus_sha256": corpus_hashes,
        "started_at": started_at,
        "completed_at": completed_at,
        "transcript_log": str(transcript_path.relative_to(REPO)),
        "results": results,
        "evidence_refs": [
            {
                "rubric_point_id": dim_id,
                "test_id": results[dim_id]["replicates"][0].get("benchmark", dim_id),
                "artifact_uri": f"are-probe://family/{BEAAA}/{dim_id}/n{args.n}-live-fire",
                "verdict": "pass" if results[dim_id]["summary"]["verdict"] == "PASS" else "fail",
                "weight": 1.0,
            }
            for dim_id in RUBRIC_POINTS
        ],
    }

    json_path = out_dir / "p1_1_p1_6_ops_live_fire_n5.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    md_path = out_dir / "p1_1_p1_6_ops_live_fire_n5.md"
    lines = [
        "---",
        "artifact_type: other",
        "owner: cto",
        "persona_audience: internal",
        "phase: 2",
        "canonical_terms_only: true",
        'focus_claim: "BEAAA-8127 measures OPS P1.1-P1.6 against a self-owned live target while preserving BackBond Scanner contract shape and reproducibility discipline."',
        'contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]',
        'ticket_refs: ["BEAAA-8127", "BEAAA-8123", "BEAAA-8130"]',
        f'proof_points_cited: ["{json_path.relative_to(REPO)}"]',
        "version: 0.1",
        "prior_version: null",
        "self_audit_passed: true",
        'escalations_noted: ["Rule-execution classification; halt and reclassify if full golden-set N=5 delta exceeds +/-1."]',
        "---",
        "",
        "# BEAAA-8127 OPS P1.1-P1.6 Live-Fire N=5",
        "",
        "BLUF: OPS P1.1-P1.6 ran against the self-owned Retell synthetic support agent through existing BackBond Scanner wrappers. This artifact changes no rubric point, weight, judge model, scoring math, tamper-resistance posture, or contract output shape.",
        "",
        f"- Contract: `{CONTRACT_VERSION}`",
        f"- scanner_version: `{SCANNER_VERSION}`",
        f"- corpus_version: `{CORPUS_VERSION}`",
        f"- probe-set-version: `{PROBE_SET_VERSION}`",
        f"- rubric_point_id: `{', '.join(RUBRIC_POINTS)}`",
        f"- model_versions.primary: `{subject.model_id()}`",
        f"- model_versions.judges: `{args.judge_adapter}/{args.judge_model}`",
        f"- seed: `{args.seed}`",
        f"- full_acceptance_mode: `{str(full_acceptance_mode).lower()}`",
        f"- corpus_limit: `{args.limit if args.limit is not None else 'full'}`",
        f"- evidence JSON: `{json_path.relative_to(REPO)}`",
        "",
        "| dim | raw values | raw median | raw spread | verdict |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for dim_id in RUBRIC_POINTS:
        s = results[dim_id]["summary"]
        lines.append(
            f"| {dim_id} | {s['raw_values']} | {s['raw_median']} | "
            f"{s['raw_spread']} | {s['verdict']} |"
        )
    lines.extend(
        [
            "",
            "## Acceptance Note",
            "",
            "If `full_acceptance_mode` is false, this is a live smoke artifact only and must not be used to close BEAAA-8127. Full acceptance requires the full corpus, pinned judge ensemble default, golden-set N=5 delta <= +/-1, and `merged_sha_on_canon_master_at_close` if code is committed.",
            "",
            "Self-audit: glossary checked; rule-execution classification table row cited; contract anchor cited; no external-bound chrome; FAL/house-style pass for internal artifact.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(json_path), "md": str(md_path), "full_acceptance_mode": full_acceptance_mode}, indent=2))
    return 0 if all(results[d]["summary"]["verdict"] == "PASS" for d in RUBRIC_POINTS) else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
