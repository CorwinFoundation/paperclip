#!/usr/bin/env bash
# run_all_tests.sh — the CI gate (P0-5). Every suite must pass or the build fails.
# The no-content invariant is the non-negotiable gate: a content-leaking change here
# turns the build red. Run: bash run_all_tests.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
export PYTHONPATH="$HERE"

echo "== no-content invariant (selftest) =="
python3 -m appliance.main selftest -c config.example.yaml

echo; echo "== e2e (read→analyze→sign→no-content) =="
python3 tests/e2e.py

echo; echo "== loop (appliance→server→verify, tamper + content rejection) =="
python3 tests/loop_test.py

echo; echo "== parity + windowing + contract (CEL≡declarative, P0-1, P0-2) =="
python3 tests/parity_test.py

echo; echo "ALL GATES GREEN"
