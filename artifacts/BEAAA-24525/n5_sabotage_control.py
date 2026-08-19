#!/usr/bin/env python3
"""BEAAA-24525 sabotage control for the N=5 harness.

`n5_via_gate_functions.py` reports 0.0000 on every agent and pillar. A harness
that can only ever print 0.0000 proves nothing, so this runs the same loop with
one deliberate perturbation of a real scoring input on the candidate side and
requires a non-zero delta to fall out.

Perturbation: G1's `eval_outputs.json` on the candidate tree only, one numeric
raw nudged. Nothing else changes. If the delta stays 0.0000 the harness is
blind and the clean run above is worthless.

Also asserts the negative half: the band block this ticket edits is NOT a
scoring input, so writing a wrong band into the candidate's baseline.json must
leave every delta at 0.0000.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from scripts import release_gate_n5 as G  # noqa: E402

BASELINE_SHA = "5410126335a0e3a71b698b2803e0afaa96dc797c"
CANDIDATE_SHA = "aa4b8752058247512f882172a475d855f7ba3056"
AGENT = "G1"


def _first_numeric_raw(doc):
    """Return (path, value) of the first float raw in G1's eval outputs."""
    stack = [([], doc)]
    while stack:
        path, node = stack.pop(0)
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "raw" and isinstance(v, (int, float)) and not isinstance(v, bool) and v:
                    return path + [k], v
                stack.append((path + [k], v))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                stack.append((path + [i], v))
    raise SystemExit("control is inert: no numeric raw found in G1 eval_outputs.json")


def _set(doc, path, value):
    node = doc
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


def _n5(repo_side, input_dir, agent_id):
    vals = []
    with tempfile.TemporaryDirectory(prefix="bb-24525-ctl-out-") as t:
        for seed in G.SEEDS:
            out = pathlib.Path(t) / f"seed-{seed}.json"
            code, stdout, stderr = G._run_cli(repo_side, agent_id, seed, input_dir, out)
            if code != 0:
                raise SystemExit(f"bin/are-scan failed: {stderr.strip() or stdout.strip()}")
            vals.append(G._candidate_values(json.loads(out.read_text(encoding="utf-8"))))
    return vals


def main() -> int:
    import statistics

    def deltas(b, c):
        return {
            k: statistics.median(v[k] for v in c) - statistics.median(v[k] for v in b)
            for k in ("total", *G.PILLARS)
        }

    with tempfile.TemporaryDirectory(prefix="bb-24525-ctl-") as tmp:
        root = pathlib.Path(tmp)
        base, cand = root / "baseline", root / "candidate"
        G._archive_commit(BASELINE_SHA, base)
        G._archive_commit(CANDIDATE_SHA, cand)
        b387_base = base / "artifacts" / "beaaa-387"
        b387_cand = cand / "artifacts" / "beaaa-387"

        in_base = G._prepare_input_dir(AGENT, root / "in-base", b387_base)
        base_vals = _n5(base, in_base, AGENT)

        # --- control 1: perturb a real scoring input on the candidate side ---
        ev = b387_cand / AGENT / "eval_outputs.json"
        doc = json.loads(ev.read_text(encoding="utf-8"))
        path, old = _first_numeric_raw(doc)
        new = 0.0 if old else 1.0
        _set(doc, path, new)
        ev.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        in_sab = G._prepare_input_dir(AGENT, root / "in-sab", b387_cand)
        sab_d = deltas(base_vals, _n5(cand, in_sab, AGENT))
        ev.write_text(
            (base / "artifacts" / "beaaa-387" / AGENT / "eval_outputs.json").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )

        # --- control 2: write a wrong band; it must NOT move a score ---
        bl = b387_cand / AGENT / "baseline.json"
        doc = json.loads(bl.read_text(encoding="utf-8"))
        doc["band"] = {
            "bands_version": "1.0",
            "display": "D-",
            "letter": "D",
            "modifier": "-",
            "modifier_factor": 1.10,
            "posture": "Decline / layered-excess only",
        }
        bl.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        in_band = G._prepare_input_dir(AGENT, root / "in-band", b387_cand)
        band_d = deltas(base_vals, _n5(cand, in_band, AGENT))

    print(f"control 1 (scoring input moved): {AGENT} raw at {'.'.join(map(str, path))} "
          f"{old} -> {new}")
    print(f"          deltas: {json.dumps(sab_d)}")
    c1 = any(v != 0.0 for v in sab_d.values())
    print(f"          harness detects a real move: {c1}   <- must be True")
    print()
    print("control 2 (band block set to a wrong D-, nothing else)")
    print(f"          deltas: {json.dumps(band_d)}")
    c2 = all(v == 0.0 for v in band_d.values())
    print(f"          band is not a scoring input: {c2}   <- must be True")
    print()
    ok = c1 and c2
    print(f"CONTROLS {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
