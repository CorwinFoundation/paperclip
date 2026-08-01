#!/usr/bin/env bash
# run-on-ariclaw.sh — Rung-2 real-data test of the BackBond appliance on the live
# Paperclip fleet. READ-ONLY: reads existing Codex rollout logs, analyzes locally,
# writes signed no-content batches to a local outbox. Ships NOTHING off the box.
# Nothing in any agent's codex-home is modified.
#
# Copy the appliance dir to the box and run:  bash run-on-ariclaw.sh
set -uo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
FLEET="/mnt/paperclipdata/dot-paperclip/instances/default/companies"
OUT="/mnt/paperclipdata/numbat/appliance-rung2-$(date -u +%F)"
mkdir -p "$OUT/outbox" "$OUT/state"

command -v python3 >/dev/null || { echo "python3 required"; exit 1; }
python3 -c "import cryptography,yaml" 2>/dev/null || pip3 install --user -q cryptography PyYAML

cat > "$OUT/config.yaml" <<YAML
appliance_id: "ariclaw-rung2"
evidence_tier: "log-inferred"
reader: {name: "codex_rollout", path: "$FLEET"}
rules: {pack: "coding_agent.cel.yaml", allowlist: "paperclip_allowlist.cel.yaml"}
ship: {mode: "outbox", outbox_dir: "$OUT/outbox", max_events_per_batch: 5000}
signing: {key_path: "$OUT/appliance.key", key_id: "ariclaw-rung2-key"}
state_dir: "$OUT/state"
YAML

export PYTHONPATH="$APP_DIR"   # so `-m appliance.main` resolves regardless of cwd/user

echo "== no-content selftest (must PASS) =="
python3 -m appliance.main selftest -c "$OUT/config.yaml" || exit 1

echo; echo "== RUN appliance over the real fleet (read-only, as current user) =="
# Root can read the fleet read-only; no user switch needed for a read-only pass.
python3 -m appliance.main run -c "$OUT/config.yaml"

echo; echo "== Rung-2 summary: findings by rule / agent (NO command text) =="
python3 - "$OUT/outbox" <<'PY'
import json, glob, sys, collections
by_rule = collections.Counter(); by_agent = collections.Counter(); by_sev = collections.Counter()
events = 0
for fp in glob.glob(sys.argv[1] + "/*.json"):
    b = json.load(open(fp)); events += len(b["events"])
    for d in b["determinations"]:
        by_rule[d["rule_id"]] += 1; by_agent[d["agent_id"]] += 1; by_sev[d["severity"]] += 1
print("events analyzed:", events)
print("findings by severity:", dict(by_sev))
print("findings by rule:"); [print(f"  {n:4d}  {r}") for r, n in by_rule.most_common()]
print("findings by agent:"); [print(f"  {n:4d}  {a}") for a, n in by_agent.most_common()]
print("\nReminder: these are the appliance's OWN findings on REAL fleet data. Compare to the")
print("Numbat Phase-0 classification to gauge true/false-positive alignment (Rung-2 read).")
print("Batches are in the outbox (metadata + hashes only). Post COUNTS to BEAAA, never raw batches.")
PY

echo; echo "== raw-shape sample (first command-bearing rollout line, for reader-mapping check) =="
python3 - "$FLEET" <<'PY'
import glob, sys, json
for fp in sorted(glob.glob(sys.argv[1] + "/**/rollout-*.jsonl", recursive=True))[:200]:
    for line in open(fp, errors="replace"):
        line = line.strip()
        if '"command"' in line or 'exec' in line:
            print(fp.split("/companies/")[-1][:60], "->")
            print("  keys:", sorted(json.loads(line).keys()) if line.startswith("{") else "n/a")
            sys.exit(0)
print("no command-bearing line found in first 200 rollout files")
PY
echo; echo "Done. Evidence dir: $OUT"
