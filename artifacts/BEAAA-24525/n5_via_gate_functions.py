#!/usr/bin/env python3
"""BEAAA-24525 N=5 evidence.

`scripts/release_gate_n5.py` will not run master -> candidate for this change.
`_frozen_input_hashes` (41207e40, BEAAA-17557) hashes each roster agent's whole
`baseline.json`, so any edit to that file -- including one to a field the gate
never feeds the CLI -- trips the archive-identity check before a single seed is
scored. Reproduced verbatim:

    ValueError: frozen input hash mismatch between baseline and candidate archives

That check is a proxy for the question the gate actually needs answered: are the
scoring inputs identical on both sides? This script answers that question
directly and then runs the gate's own N=5 loop, using the gate's own functions
(_archive_commit / _prepare_input_dir / _run_cli / _validate_contract_payload /
_candidate_values / AgentGateResult), so the numbers below are the gate's
measurement and not a re-implementation of it.

Two claims, in order:
  A. Input identity  -- every file `_prepare_input_dir` hands `bin/are-scan` is
     byte-identical between the two trees, for every roster agent.
  B. N=5 delta       -- seeds 0..4, both sides, total and all four pillars.

Claim A is the stronger statement. Claim B is the measurement the acceptance
criterion asks for. A green A with a non-zero B would mean the CLI is
non-deterministic, not that this change reached scoring.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from scripts import release_gate_n5 as G  # noqa: E402

BASELINE_SHA = "5410126335a0e3a71b698b2803e0afaa96dc797c"
CANDIDATE_SHA = "aa4b8752058247512f882172a475d855f7ba3056"


def _tree_hashes(root: pathlib.Path) -> dict[str, str]:
    return {
        str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest()
        for f in sorted(root.rglob("*"))
        if f.is_file()
    }


def main() -> int:
    report: dict = {
        "issue": "BEAAA-24525",
        "baseline_sha": BASELINE_SHA,
        "candidate_sha": CANDIDATE_SHA,
        "roster": list(G.ROSTER),
        "seeds": list(G.SEEDS),
        "pillars": list(G.PILLARS),
    }
    with tempfile.TemporaryDirectory(prefix="bb-24525-n5-") as tmp:
        root = pathlib.Path(tmp)
        baseline_repo, candidate_repo = root / "baseline", root / "candidate"
        G._archive_commit(BASELINE_SHA, baseline_repo)
        G._archive_commit(CANDIDATE_SHA, candidate_repo)

        # -- claim A ------------------------------------------------------
        frozen_b = G._frozen_input_hashes(baseline_repo)
        frozen_c = G._frozen_input_hashes(candidate_repo)
        report["frozen_input_paths_differing"] = sorted(
            k for k in frozen_b if frozen_b[k] != frozen_c[k]
        )

        inputs: dict[str, pathlib.Path] = {}
        for side, repo in (("baseline", baseline_repo), ("candidate", candidate_repo)):
            for agent_id in G.ROSTER:
                inputs[f"{side}/{agent_id}"] = G._prepare_input_dir(
                    agent_id, root / f"in-{side}", repo / "artifacts" / "beaaa-387"
                )
        hb, hc = _tree_hashes(root / "in-baseline"), _tree_hashes(root / "in-candidate")
        report["cli_input_file_count"] = len(hb)
        report["cli_input_files_differing"] = sorted(k for k in hb if hb.get(k) != hc.get(k))
        report["cli_input_identical"] = hb == hc and sorted(hb) == sorted(hc)

        # -- claim B ------------------------------------------------------
        agents: dict[str, dict] = {}
        errors: list[str] = []
        for agent_id in G.ROSTER:
            result = G.AgentGateResult(agent_id=agent_id)
            for side, repo, values in (
                ("baseline", baseline_repo, result.baseline_values),
                ("candidate", candidate_repo, result.candidate_values),
            ):
                for seed in G.SEEDS:
                    out = root / "outputs" / side / agent_id / f"seed-{seed}.json"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    code, stdout, stderr = G._run_cli(
                        repo, agent_id, seed, inputs[f"{side}/{agent_id}"], out
                    )
                    if code != 0:
                        result.errors.append(
                            f"{agent_id}/{side}/seed-{seed}: bin/are-scan failed: "
                            f"{stderr.strip() or stdout.strip() or f'exit {code}'}"
                        )
                        continue
                    payload = json.loads(out.read_text(encoding="utf-8"))
                    result.errors.extend(
                        G._validate_contract_payload(payload, agent_id=agent_id, seed=seed)
                    )
                    values.append(G._candidate_values(payload))
            errors.extend(result.errors)
            agents[agent_id] = {
                "errors": result.errors,
                "baseline_spread": result.baseline_spread,
                "candidate_spread": result.candidate_spread,
                "deltas": result.deltas,
                "spread_passed": result.spread_passed,
                "delta_passed": result.delta_passed,
                "passed": result.passed,
                "baseline_values": result.baseline_values,
                "candidate_values": result.candidate_values,
            }
        report["agents"] = agents
        report["errors"] = errors

    all_deltas = [
        abs(v) for a in report["agents"].values() for v in (a["deltas"] or {}).values()
    ]
    report["max_abs_delta"] = max(all_deltas) if all_deltas else None
    report["all_deltas_zero"] = bool(all_deltas) and all(d == 0.0 for d in all_deltas)
    report["n5_passed"] = all(a["passed"] for a in report["agents"].values())

    out_path = REPO / "artifacts" / "BEAAA-24525" / "n5-evidence.json"
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"baseline  {BASELINE_SHA}")
    print(f"candidate {CANDIDATE_SHA}")
    print(f"A: frozen-input paths differing : {report['frozen_input_paths_differing']}")
    print(f"A: CLI input files compared     : {report['cli_input_file_count']}")
    print(f"A: CLI input files differing    : {report['cli_input_files_differing']}")
    print(f"A: CLI input byte-identical     : {report['cli_input_identical']}")
    print()
    hdr = ["total", *G.PILLARS]
    print(f"{'agent':6}" + "".join(f"{h:>16}" for h in hdr) + "   spread(b/c)")
    for agent_id, a in report["agents"].items():
        row = "".join(f"{a['deltas'].get(k, float('nan')):>16.4f}" for k in hdr)
        sb = max(a["baseline_spread"].values()) if a["baseline_spread"] else float("nan")
        sc = max(a["candidate_spread"].values()) if a["candidate_spread"] else float("nan")
        print(f"{agent_id:6}{row}   {sb:.2f}/{sc:.2f}" + ("  ERRORS" if a["errors"] else ""))
    print()
    print(f"max |delta|      : {report['max_abs_delta']}")
    print(f"all deltas zero  : {report['all_deltas_zero']}")
    print(f"N=5 passed       : {report['n5_passed']}")
    print(f"errors           : {report['errors'] or 'none'}")
    print(f"written          : {out_path.relative_to(REPO)}")
    return 0 if (report["n5_passed"] and report["all_deltas_zero"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
