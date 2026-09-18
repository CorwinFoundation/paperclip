"""BEAAA-33708: build the run manifest and the ledger for round 2 of the C0 cohort.

Nothing new is decided here. The manifest and the ledger come from
``tools.beaaa33673_zero_build_slice`` exactly as BEAAA-33673 built them; this
script only supplies this round's paths and, critically, this round's
``roster_round``. ``build_ledger`` defaults that argument to the 33673 roster
round, and ``main()`` never passes it, so calling the module CLI here would hand
the verdict-spread gate a roster round that matches no row in this manifest and
silently mis-report the spread. That is the whole reason this file exists.

Every path and label is a flag with a round-1 default, so the same script drives
rounds 2 and 3 of this cohort and, more usefully, can be pointed at the COMMITTED
BEAAA-33673 artifacts to check that the chain still reproduces that round's
ledger byte for byte. A round script that cannot reproduce a known-good round is
not evidence about the round it just ran.
"""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import beaaa33673_zero_build_slice as Z

ROSTER_ROUND = "beaaa-33708-r1"
JUDGE_MODEL = "probe-only"
CAPTURE_LOG = Path("/tmp/bb33708out/capture-log.jsonl")
OUT_DIR = ROOT / "artifacts/beaaa-33708"
COHORT = OUT_DIR / "cohort-200.json"
MANIFEST = OUT_DIR / "run-manifest.json"
LEDGER = OUT_DIR / "ledger.json"
# The registry entry for this round names artifacts/beaaa-33708/controls.json, so the
# realized controls have to land in the repo beside the manifest they were scored
# against. Reading them from a scratch path and never persisting them would build a
# ledger whose control evidence is not committable and whose round cannot be declared.
CONTROLS = OUT_DIR / "controls.json"
# Both prior C0 rounds commit their roster capture log beside the manifest, and the
# standing line requires real execution evidence for anything scored. The log is the
# only record of what each of the 200 attempts actually returned, so leaving it in a
# scratch path would ship a scored round whose evidence is not in the repo.
ROSTER_LOG = OUT_DIR / "roster-capture-log.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-log", type=Path, default=CAPTURE_LOG)
    parser.add_argument("--controls", type=Path, default=None)
    parser.add_argument("--cohort", type=Path, default=COHORT)
    parser.add_argument("--roster-round", default=ROSTER_ROUND)
    parser.add_argument("--judge-model", default=JUDGE_MODEL)
    parser.add_argument("--manifest-out", type=Path, default=MANIFEST)
    parser.add_argument("--ledger-out", type=Path, default=LEDGER)
    parser.add_argument("--controls-out", type=Path, default=CONTROLS)
    parser.add_argument("--roster-log-out", type=Path, default=ROSTER_LOG)
    args = parser.parse_args()

    if args.capture_log.resolve() != args.roster_log_out.resolve():
        args.roster_log_out.write_bytes(args.capture_log.read_bytes())
        rows = sum(1 for _ in args.roster_log_out.open(encoding="utf-8"))
        print(f"roster log: {rows} attempts -> {args.roster_log_out}")

    manifest = Z.manifest_from_capture(args.capture_log, Z.PINNED_INVENTORY_PATH, args.judge_model)
    rounds = sorted({row["capture_round"] for row in manifest["evidence_rows"]})
    if rounds != [args.roster_round]:
        raise SystemExit(f"capture log carries rounds {rounds}, expected [{args.roster_round!r}]")
    args.manifest_out.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                                 encoding="utf-8")
    print(f"manifest: {len(manifest['evidence_rows'])} evidence rows -> {args.manifest_out}")

    if args.controls is not None and args.controls.resolve() != args.controls_out.resolve():
        records = json.loads(args.controls.read_text(encoding="utf-8"))
        args.controls_out.write_text(json.dumps(records, indent=1, sort_keys=True) + "\n",
                                     encoding="utf-8")
        print(f"controls: {len(records)} realized records -> {args.controls_out}")
        args.controls = args.controls_out

    ledger = Z.build_ledger(args.cohort, Z.PINNED_INVENTORY_PATH, args.manifest_out,
                            controls=args.controls, roster_round=args.roster_round)
    args.ledger_out.write_text(json.dumps(ledger, indent=1, sort_keys=True) + "\n",
                               encoding="utf-8")
    print(json.dumps({key: ledger[key] for key in
                      ("selected", "mapped", "evidenced", "scored", "dropped_duplicate", "held",
                       "tier1_families", "tier2_held_families")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
