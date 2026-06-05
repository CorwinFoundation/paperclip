"""BEAAA-2199 full-dict byte-equivalence calibration vs. master 52c003e.

For every golden in B1/G1/G2/M1/M2/M3:

  1. Run bin/are-scan in the master baseline worktree (commit 52c003e)
     via subprocess.
  2. Run bin/are-scan in the BEAAA-2199 candidate worktree
     (branch beaaa-2199-pr) via subprocess.
  3. Diff the two JSON outputs key-by-key.
  4. Assert the diff set is exactly INTENDED_DELTAS == {"cosign_metadata"}.
     Anything else → halt + report.
  5. N=5 reproducibility on candidate: 5 replicates must hash identically,
     spread on are_total + every pillar must be 0.

Halt conditions (any halts merge):
  - unintended_diffs != [] (per agent)
  - missing_intended_diffs != [] (cosign_metadata absent on candidate)
  - n5_total_spread > 0 OR any n5_pillar_spread > 0 on candidate
  - master baseline run fails OR candidate run fails

Emits JSON to tools/calibration-deltas/<DATE>-beaaa2199-full-dict.json
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MASTER_REPO = Path("/tmp/are-scanner-master-baseline")
CANDIDATE_REPO = Path("/tmp/are-scanner-beaaa2199")
MASTER_COMMIT = "52c003e"
CANDIDATE_BRANCH = "beaaa-2199-pr"
GOLDEN_AGENTS = ["B1", "G1", "G2", "M1", "M2", "M3"]
N_REPLICATES = 5
SEED = 42
FIXED_SCORED_AT = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc).isoformat()
INTENDED_ADDED = {"cosign_metadata"}
# scanner_version bumps 0.3.10 → 0.3.11 per CTO Ask 4 (envelope-shape change
# requires consumers to distinguish 0.3.10 envelopes from 0.3.11+); the field
# already existed at master so it appears under `key_changed`, not `key_added`.
INTENDED_CHANGED = {"scanner_version"}
# Per-scan volatile keys excluded from the diff comparison — these legitimately
# differ between any two scans (timestamps, hashes derived from timestamps,
# URIs containing hashes). They are NOT a contract change.
VOLATILE_KEYS = {
    "run_timestamp",
    "scored_at",
    "snapshot_hash",
    "forensic_bundle_uri",
    "scan_id",
}

OUTPUT_PATH = (
    CANDIDATE_REPO / "tools" / "calibration-deltas"
    / "2026-06-05-52c003e-vs-beaaa2199-full-dict.json"
)


def _setup_inputs(agent: str, fixtures_root: Path, dst: Path) -> Path:
    agent_dir = dst / agent
    agent_dir.mkdir(parents=True)
    src = fixtures_root / agent
    (agent_dir / "eval_results.json").write_text(
        (src / "eval_outputs.json").read_text()
    )
    app_path = src / "applicability.json"
    if app_path.exists():
        (agent_dir / "applicability.json").write_text(app_path.read_text())
    return agent_dir


def _run_cli(repo: Path, agent: str, input_dir: Path) -> dict:
    out_path = input_dir / f"{agent}_out.json"
    r = subprocess.run(
        [
            sys.executable, str(repo / "bin" / "are-scan"),
            "--agent", agent, "--seed", str(SEED),
            "--input-dir", str(input_dir),
            "--scored-at", FIXED_SCORED_AT,
            "--out", str(out_path),
        ],
        capture_output=True, text=True, cwd=str(repo),
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"bin/are-scan exit {r.returncode} on {repo} agent={agent}: "
            f"stderr={r.stderr.strip()[:500]}"
        )
    return json.loads(out_path.read_text())


def _hash_dict(d: dict) -> str:
    return hashlib.sha256(
        json.dumps(d, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _strip_volatile(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in VOLATILE_KEYS}


def _full_dict_diff(master: dict, candidate: dict) -> dict:
    """Diff every top-level key, excluding volatile ones.

    Returns {
        "key_added":   [...],   # in candidate not master
        "key_removed": [...],   # in master not candidate
        "key_changed": [...],   # in both but value differs
    }
    """
    m = _strip_volatile(master)
    c = _strip_volatile(candidate)
    m_keys = set(m.keys())
    c_keys = set(c.keys())
    added = sorted(c_keys - m_keys)
    removed = sorted(m_keys - c_keys)
    common = m_keys & c_keys
    changed = sorted(k for k in common if m[k] != c[k])
    return {"key_added": added, "key_removed": removed, "key_changed": changed}


def _pillar_values(d: dict) -> dict:
    """Extract numeric pillar values for spread check."""
    out = {"are_total": d.get("are_total")}
    pillars = d.get("pillars") or {}
    for pid, pdata in pillars.items():
        out[f"pillar_{pid}_score"] = (pdata or {}).get("score")
    return out


def main() -> int:
    fixtures_root = CANDIDATE_REPO / "tests" / "fixtures" / "golden_agents"
    if not MASTER_REPO.exists():
        print(f"FATAL: master worktree missing at {MASTER_REPO}", file=sys.stderr)
        return 2
    if not CANDIDATE_REPO.exists():
        print(f"FATAL: candidate worktree missing at {CANDIDATE_REPO}", file=sys.stderr)
        return 2

    report = {
        "ticket": "BEAAA-2199",
        "master_commit": MASTER_COMMIT,
        "candidate_branch": CANDIDATE_BRANCH,
        "intended_added": sorted(INTENDED_ADDED),
        "intended_changed": sorted(INTENDED_CHANGED),
        "volatile_keys_excluded": sorted(VOLATILE_KEYS),
        "n_replicates": N_REPLICATES,
        "seed": SEED,
        "fixed_scored_at": FIXED_SCORED_AT,
        "per_agent": {},
        "halts": [],
        "all_pass": True,
    }

    with tempfile.TemporaryDirectory() as tmpd_master, \
            tempfile.TemporaryDirectory() as tmpd_candidate:
        tmp_master = Path(tmpd_master)
        tmp_cand = Path(tmpd_candidate)

        for agent in GOLDEN_AGENTS:
            agent_report: dict = {}
            try:
                master_dir = _setup_inputs(agent, fixtures_root, tmp_master)
                cand_dir = _setup_inputs(agent, fixtures_root, tmp_cand)
                master_out = _run_cli(MASTER_REPO, agent, master_dir)
                cand_out = _run_cli(CANDIDATE_REPO, agent, cand_dir)
                agent_report["master_envelope_keys"] = sorted(master_out.keys())
                agent_report["candidate_envelope_keys"] = sorted(cand_out.keys())
                diff = _full_dict_diff(master_out, cand_out)
                agent_report["full_dict_diff"] = diff
                observed_added = set(diff["key_added"])
                observed_changed = set(diff["key_changed"])
                observed_removed = set(diff["key_removed"])
                unintended_added = observed_added - INTENDED_ADDED
                unintended_changed = observed_changed - INTENDED_CHANGED
                missing_intended_added = INTENDED_ADDED - observed_added
                missing_intended_changed = INTENDED_CHANGED - observed_changed
                agent_report["unintended_added"] = sorted(unintended_added)
                agent_report["unintended_changed"] = sorted(unintended_changed)
                agent_report["unintended_removed"] = sorted(observed_removed)
                agent_report["missing_intended_added"] = sorted(missing_intended_added)
                agent_report["missing_intended_changed"] = sorted(missing_intended_changed)

                if unintended_added or unintended_changed or observed_removed:
                    msg = (
                        f"{agent}: unintended deltas — added={sorted(unintended_added)}, "
                        f"changed={sorted(unintended_changed)}, "
                        f"removed={sorted(observed_removed)}"
                    )
                    report["halts"].append(msg)
                    report["all_pass"] = False
                if missing_intended_added or missing_intended_changed:
                    msg = (
                        f"{agent}: intended deltas missing — "
                        f"added={sorted(missing_intended_added)}, "
                        f"changed={sorted(missing_intended_changed)}"
                    )
                    report["halts"].append(msg)
                    report["all_pass"] = False

                # cosign_metadata sanity
                cm = cand_out.get("cosign_metadata") or {}
                agent_report["cosign_metadata_present"] = "cosign_metadata" in cand_out
                agent_report["cosign_metadata_block_version"] = cm.get("block_version")
                agent_report["cosign_metadata_scanner_version"] = cm.get("scanner_version")
                agent_report["cosign_metadata_contract_version"] = cm.get("contract_version")
                agent_report["cosign_metadata_rule_changes_count"] = len(
                    cm.get("rule_changes") or []
                )

                # N=5 reproducibility on candidate
                hashes: list[str] = []
                pillar_collections: dict[str, list] = {}
                for _ in range(N_REPLICATES):
                    out = _run_cli(CANDIDATE_REPO, agent, cand_dir)
                    hashes.append(_hash_dict(_strip_volatile(out)))
                    for k, v in _pillar_values(out).items():
                        pillar_collections.setdefault(k, []).append(v)
                agent_report["n5_candidate_hashes_unique"] = len(set(hashes))
                spreads = {
                    k: (max(vs) - min(vs)) if all(isinstance(v, (int, float)) for v in vs) else None
                    for k, vs in pillar_collections.items()
                }
                agent_report["n5_spreads"] = spreads
                bad_spreads = {k: s for k, s in spreads.items() if s not in (0, None)}
                if len(set(hashes)) != 1 or bad_spreads:
                    report["halts"].append(
                        f"{agent}: N=5 reproducibility failed — unique_hashes={len(set(hashes))}, "
                        f"spreads={bad_spreads}"
                    )
                    report["all_pass"] = False
            except Exception as exc:
                agent_report["error"] = str(exc)
                report["halts"].append(f"{agent}: {exc}")
                report["all_pass"] = False

            report["per_agent"][agent] = agent_report

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(f"wrote {OUTPUT_PATH}")
    print(f"all_pass: {report['all_pass']}")
    if not report["all_pass"]:
        print("halts:")
        for h in report["halts"]:
            print(f"  - {h}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
