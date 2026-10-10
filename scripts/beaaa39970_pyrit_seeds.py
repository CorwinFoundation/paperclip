"""Pick 50 refused conv-en seeds for the PyRIT E2 slice (BEAAA-39970 item 2).

Candidates are single-turn, judge-checked full-scan-conv-en@0.3 cases whose
expectation is a refusal. Each is sent once to the target with its own system
prompt (temperature 0, seed 0) and graded by the 3-vote pressure judge against
its ``expect`` text. Only cases the target actually refused are eligible; 50 are
drawn round-robin across deployments in a fixed shuffled order (RNG 39970).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.full_scan_packs import PAID_PACKS  # noqa: E402
from scripts.beaaa38961_full_scan_e_live_run import Meter  # noqa: E402
from scripts.beaaa39970_full_scan_pressure_live_run import JUDGE_MODEL, _judge, _post  # noqa: E402

SOURCE_PACK = "full-scan-conv-en@0.3"


def deployment_of(system: str) -> str:
    head = system.split(".")[0].replace("You are ", "").strip()[:60]
    return f"{head} [{hashlib.sha256(system.encode()).hexdigest()[:8]}]"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="openai/gpt-4.1-mini")
    p.add_argument("--n", type=int, default=50)
    p.add_argument("--cap-usd", type=float, default=1.0)
    p.add_argument("--out", type=Path, default=Path("artifacts/BEAAA-39970/pyrit_seeds.json"))
    args = p.parse_args()
    key = os.environ["OPENROUTER_API_KEY"]
    pool = [c for c in PAID_PACKS[SOURCE_PACK]
            if c.check == "judge" and len(c.turns) == 1 and "refus" in str(c.expect).lower()]
    random.Random(39970).shuffle(pool)
    meter = Meter(args.model, args.cap_usd)

    def screen(c):
        body = _post(meter, key, {"model": args.model, "temperature": 0, "seed": 0, "messages": [
            {"role": "system", "content": c.system}, {"role": "user", "content": c.turns[0]}]})
        reply = str(((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        violated = _judge(meter, key, JUDGE_MODEL, str(c.expect), c.turns[0], reply)
        return c, reply, violated

    with ThreadPoolExecutor(12) as ex:
        screened = list(ex.map(screen, pool))
    by_dep: dict[str, list] = defaultdict(list)
    for c, reply, violated in screened:
        if not violated:
            by_dep[deployment_of(c.system)].append((c, reply))
    seeds = []
    deps = sorted(by_dep)
    while len(seeds) < args.n and any(by_dep.values()):
        for d in deps:
            if by_dep[d] and len(seeds) < args.n:
                c, reply = by_dep[d].pop(0)
                seeds.append({"case_id": c.case_id, "family_id": c.family_id, "deployment": d,
                              "system": c.system, "request": c.turns[0], "pass_rule": str(c.expect),
                              "turn1_refusal": reply})
    out = {"issue": "BEAAA-39970", "source_pack": SOURCE_PACK, "model": args.model, "judge_model": JUDGE_MODEL,
           "candidates": len(pool), "refused_at_turn1": sum(not v for _, _, v in screened),
           "complied_at_turn1": sum(v for _, _, v in screened), "rng_seed": 39970,
           "screen_usd": round(meter.usd, 4), "seeds": seeds}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "seeds"}, indent=2), len(seeds), "seeds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
