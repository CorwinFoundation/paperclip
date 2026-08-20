"""QA BEAAA-24005 criteria 3 + 4 harness.

Version-agnostic on purpose: calls run() with ONLY the arguments that exist on
both dc113b6e (baseline) and 325cc81f (candidate). No corpus_yaml, no explicit
pins -- so we observe the DEFAULT pin behaviour, which is the defect surface.

Run from the root of whichever tree is under test:
    python3 /tmp/qa_c34.py <label> <outdir>
"""
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path.cwd()))

from harness.bench import factuality_golden as p11  # noqa: E402
from harness.bench import p1_4_refusal_golden as p14  # noqa: E402
from harness.runner import MockRunner  # noqa: E402

LABEL = sys.argv[1]
OUTDIR = Path(sys.argv[2])
OUTDIR.mkdir(parents=True, exist_ok=True)

JUDGE_REPLY = (
    "ANSWER_MATCH: YES\n"
    "PLAUSIBILITY: YES\n"
    "FALSE_SPECIFICITY: NO\n"
    "ACTION: ANSWERED\n"
    "FAILURE_MODE: none\n"
    "EVIDENCE_SPAN:"
)

subject = MockRunner("subject", lambda prompt: "A fixed deterministic subject answer.")
judge = MockRunner("judge", lambda prompt: JUDGE_REPLY)

REPORT = {"label": LABEL, "dims": {}}

for dim, mod, yaml_rel in (
    ("P1.1", p11, "rubric/probe_mappings/p1_1_factuality_golden.yaml"),
    ("P1.4", p14, "rubric/probe_mappings/p1_4_refusal_golden.yaml"),
):
    ypath = Path.cwd() / yaml_rel
    yaml_pin = yaml.safe_load(ypath.read_text())["reproducibility"]["probe_set_version"]

    probes = mod.load_probes(ypath)[:2]  # two-probe slice: offline + fast
    env = mod.run(subject, judge, probes, seed=42)

    REPORT["dims"][dim] = {
        "yaml_says": yaml_pin,
        "emitted_corpus_version": env["corpus_version"],
        "emitted_probe_set_version": env["probe_set_version"],
        "emitted_benchmark": env["benchmark"],
        "top_level_key_count": len(env.keys()),
        "top_level_keys": sorted(env.keys()),
        "n_probes_in_slice": len(probes),
    }

    # Full envelope, deterministically serialised for the criterion-4 diff.
    (OUTDIR / f"envelope-{dim}-{LABEL}.json").write_text(
        json.dumps(env, indent=2, sort_keys=True, default=str) + "\n"
    )

(OUTDIR / f"report-{LABEL}.json").write_text(json.dumps(REPORT, indent=2) + "\n")

for dim, d in REPORT["dims"].items():
    print(
        f"[{LABEL}] {dim}: yaml={d['yaml_says']} "
        f"emitted_corpus_version={d['emitted_corpus_version']} "
        f"emitted_probe_set_version={d['emitted_probe_set_version']} "
        f"benchmark={d['emitted_benchmark']} "
        f"top_level_keys={d['top_level_key_count']}"
    )
