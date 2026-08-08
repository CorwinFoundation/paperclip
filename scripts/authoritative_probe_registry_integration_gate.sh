#!/bin/sh
set -eu

repo=${1:-.}
cd "$repo"

python3 -m tools.probe_registry --repo . --check
python3 -m pytest -q tests/test_probe_registry.py
python3 -m pytest -q \
  tests/test_authoritative_probe_registry_beaaa20357.py::test_schema_and_sha_binding \
  tests/test_authoritative_probe_registry_beaaa20357.py::test_release_epoch_semantics_match_probe_set_version \
  tests/test_authoritative_probe_registry_beaaa20357.py::test_stable_ids_are_unique_and_category_accounting_is_exact \
  tests/test_authoritative_probe_registry_beaaa20357.py::test_duplicate_stable_id_inputs_fail_closed \
  tests/test_authoritative_probe_registry_beaaa20357.py::test_legacy_2202_is_not_default_executed_reported_or_scored
python3 -m pytest -q tests/test_authoritative_probe_registry_beaaa20357.py::test_check_mode_fails_closed_on_drift
python3 -m pytest -q tests/test_authoritative_probe_registry_beaaa20357.py::test_content_provenance_is_independent_of_merge_topology
python3 -m pytest -q tests/test_authoritative_probe_registry_beaaa20357.py::test_failed_integration_gate_makes_push_unreachable
python3 -m compileall -q tools/probe_registry.py tools/active_test_registry.py
python3 - <<'PY'
import json
from pathlib import Path
import jsonschema

root = Path(".")
registry = json.loads((root / "tools/probe-registry/authoritative_registry.json").read_text())
schema = json.loads((root / "tools/probe-registry/authoritative_registry.schema.json").read_text())
jsonschema.validate(registry, schema)
rows = registry["rows"]
counts = registry["category_counts"]
assert counts["registered"] == len(rows)
for category, count in counts.items():
    assert count == sum(bool(row["categories"][category]) for row in rows)
assert registry["unique_executable_count"] == counts["executable"]
PY
git diff --check HEAD
