#!/usr/bin/env bash
# BackBond appliance installer — the partner runs this once.
# It installs deps, generates the signing key, prints the enrollment record, and
# runs the no-content self-test. It reads nothing until you point it at a substrate.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CONFIG="${1:-$HERE/config.yaml}"

echo "== BackBond appliance install =="
command -v python3 >/dev/null || { echo "python3 required"; exit 1; }

echo "-- installing dependencies (user scope) --"
python3 -m pip install --user -q -r "$HERE/requirements.txt"

if [ ! -f "$CONFIG" ]; then
  echo "-- no config found; copying example to $CONFIG (edit reader.path before running) --"
  cp "$HERE/config.example.yaml" "$CONFIG"
fi

echo "-- no-content self-test (must PASS before anything ships) --"
BACKBOND_CONFIG="$CONFIG" python3 -m appliance.main selftest -c "$CONFIG"

echo "-- enrollment record (send this to BackBond to register the appliance) --"
BACKBOND_CONFIG="$CONFIG" python3 -m appliance.main enroll -c "$CONFIG"

cat <<EOF

Install complete. Next:
  1. Edit $CONFIG -> set reader.path to where your agent activity is written (READ access only).
  2. Dry run:   python3 -m appliance.main run -c "$CONFIG" --dry-run
  3. Run:       python3 -m appliance.main run -c "$CONFIG"
Content never leaves this host. Only counts, action classes, findings, and hashes are shipped.
EOF
