"""BEAAA-2175 PR-A v2 — full-dict byte-equivalence calibration vs. master.

PR-A v1 (commit 8aa0800, REJECTED in BEAAA-2175 comment 954754f5) compared
only ``are_total`` and four pillar subscores between baseline and post-
refactor. That metric was insufficient: it missed two unauthorized changes
swept in from working-copy WIP (BEAAA-1682 WS-A ``_DIM_ID_RE`` regex
expansion; BEAAA-1686 WS-C ``cosign_metadata`` field). The metric reported
spread=0 and baseline_delta=0 while the contract output dict was materially
different.

PR-A v2 is a true verbatim lift from master ``6be43f1``, sourced via
``git show 6be43f1:harness/cli_runner.py``. This calibration script asserts
**full contract-output dict byte-equivalence** by serializing each run's
output via ``json.dumps(..., sort_keys=True)`` and comparing SHA-256 digests
across:

1. **Master baseline** — ``run_scan`` from a sibling worktree on commit
   ``6be43f1`` (``/tmp/are-scanner-master``). Computed by invoking the CLI
   as a subprocess so the import path resolves against the master tree.
2. **PR-A v2 candidate** — ``run_scan`` from this worktree
   (``/tmp/are-scanner-pra-v2`` on branch ``beaaa-2175-pr-a-v2``). Likewise
   via subprocess to keep the two paths symmetric.
3. **N=5 reproducibility** on PR-A v2 — five replicates against each
   golden; every replicate must hash to the same value.

Acceptance:
* Master baseline digest == PR-A v2 candidate digest, **per golden**.
* All 5 PR-A v2 replicates hash to the same value, **per golden**.

Any divergence → halt + escalate. A non-zero delta in this metric means
the refactor did NOT preserve the contract output dict byte-for-byte —
which is exactly what the v1 metric failed to enforce.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MASTER_REPO = Path("/tmp/are-scanner-master")
V2_REPO = Path("/tmp/are-scanner-pra-v2")
MASTER_COMMIT = "6be43f1"
V2_BRANCH = "beaaa-2175-pr-a-v2"
GOLDEN_AGENTS = ["B1", "G1", "G2", "M1", "M2", "M3"]
N_REPLICATES = 5
SEED = 42
FIXED_SCORED_AT = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc).isoformat()
OUTPUT_PATH = (
    V2_REPO / "tools" / "calibration-deltas"
    / "2026-06-03-beaaa2175-pra-v2-full-dict.json"
)


def _materialise_input_dir(agent: str, fixtures_root: Path, dst: Path) -> Path:
    agent_dir = dst / agent
    agent_dir.mkdir(parents=True)
    (agent_dir / "eval_results.json").write_text(
        (fixtures_root / agent / "eval_outputs.json").read_text()
    )
    applicability = fixtures_root / agent / "applicability.json"
    if applicability.exists():
        shutil.copy(applicability, agent_dir / "applicability.json")
    return agent_dir


def _run_scan_subprocess(
    repo: Path, agent: str, input_dir: Path, scored_at: str
) -> dict:
    """Invoke ``run_scan`` inside ``repo`` via a Python subprocess.

    Using a subprocess (vs. importing directly) means each worktree's
    ``sys.path[0]`` and module cache stay isolated — master's
    ``cli_runner`` and v2's ``cli_runner`` cannot accidentally co-exist
    in the same interpreter.
    """
    script = f"""
import json
import sys
from datetime import datetime
sys.path.insert(0, {str(repo)!r})
from harness.cli_runner import run_scan
out = run_scan(
    agent_id={agent!r},
    input_dir=__import__('pathlib').Path({str(input_dir)!r}),
    seed={SEED},
    corpus_version=None,
    rubric_path=__import__('pathlib').Path({str(repo / 'rubric' / 'rubric.yaml')!r}),
    scored_at=datetime.fromisoformat({scored_at!r}),
)
print(json.dumps(out, sort_keys=True))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def _digest(out: dict) -> str:
    return hashlib.sha256(
        json.dumps(out, sort_keys=True).encode("utf-8")
    ).hexdigest()


def main() -> int:
    fixtures = V2_REPO / "tests" / "fixtures" / "golden_agents"
    report = {
        "ticket": "BEAAA-2175",
        "phase": "PR-A v2 — full-dict byte-equivalence vs. master",
        "master_commit": MASTER_COMMIT,
        "v2_branch": V2_BRANCH,
        "fixed_scored_at": FIXED_SCORED_AT,
        "seed": SEED,
        "n_replicates": N_REPLICATES,
        "agents": {},
        "summary": {
            "all_master_eq_v2": True,
            "all_v2_replicates_identical": True,
            "agents_passed": 0,
            "agents_failed": [],
        },
    }

    for agent in GOLDEN_AGENTS:
        with tempfile.TemporaryDirectory() as tmp:
            scratch_root = Path(tmp)

            master_input = _materialise_input_dir(
                agent, fixtures, scratch_root / "master_input"
            )
            master_out = _run_scan_subprocess(
                MASTER_REPO, agent, master_input, FIXED_SCORED_AT
            )
            master_digest = _digest(master_out)

            v2_digests = []
            for i in range(N_REPLICATES):
                v2_input = _materialise_input_dir(
                    agent, fixtures, scratch_root / f"v2_input_{i}"
                )
                v2_out = _run_scan_subprocess(
                    V2_REPO, agent, v2_input, FIXED_SCORED_AT
                )
                v2_digests.append(_digest(v2_out))

        v2_replicates_identical = len(set(v2_digests)) == 1
        master_eq_v2 = master_digest == v2_digests[0]
        agent_pass = master_eq_v2 and v2_replicates_identical

        report["agents"][agent] = {
            "master_digest": master_digest,
            "v2_digests": v2_digests,
            "v2_replicates_identical": v2_replicates_identical,
            "master_eq_v2": master_eq_v2,
            "pass": agent_pass,
        }
        s = report["summary"]
        s["all_master_eq_v2"] = s["all_master_eq_v2"] and master_eq_v2
        s["all_v2_replicates_identical"] = (
            s["all_v2_replicates_identical"] and v2_replicates_identical
        )
        if agent_pass:
            s["agents_passed"] += 1
        else:
            s["agents_failed"].append(agent)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report["summary"], indent=2))
    print(f"\nReport: {OUTPUT_PATH.relative_to(V2_REPO)}")
    overall = (
        report["summary"]["all_master_eq_v2"]
        and report["summary"]["all_v2_replicates_identical"]
    )
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
