"""BEAAA-35635: the pytest shard partition must be balanced and total.

The shards are the required check `python pytest` (via a fan-in job), so a
partition bug is a silently-unrun test, not a red build. These tests pin the
three properties that matter: every target runs exactly once, whole targets
stay whole, and the packing is deterministic across shard indices.
"""

from __future__ import annotations

import json

import pytest

from scripts import ci_shard_tests  # noqa: F401  (drop_ignored is called via the module)
from scripts.ci_shard_tests import (
    durations_from_junit,
    durations_from_log,
    load_durations,
    partition,
    target_file,
)

TARGETS = [f"tests/test_{i:02d}.py" for i in range(1, 24)]


def _all_shards(targets, shards, weights):
    return [partition(targets, shards, weights)[i] for i in range(shards)]


def test_every_target_lands_in_exactly_one_shard():
    bins = _all_shards(TARGETS, 6, {})
    flat = [t for b in bins for t in b]
    assert sorted(flat) == sorted(TARGETS)
    assert len(flat) == len(set(flat)), "a target may never run twice"


def test_node_ids_are_never_split_off_their_file_weight():
    targets = ["tests/a.py::TestX::test_one", "tests/a.py::TestX::test_two", "tests/b.py"]
    assert target_file(targets[0]) == "tests/a.py"
    bins = _all_shards(targets, 2, {"tests/a.py": 100.0, "tests/b.py": 1.0})
    flat = [t for b in bins for t in b]
    assert sorted(flat) == sorted(targets)


def test_heavy_files_are_spread_not_stacked():
    # Six 100s files and twelve 1s files over 6 shards: a balanced packer puts
    # one heavy file in each shard. Round-robin over an unsorted list would not.
    weights = {f"tests/heavy_{i}.py": 100.0 for i in range(6)}
    weights.update({f"tests/light_{i}.py": 1.0 for i in range(12)})
    bins = _all_shards(list(weights), 6, weights)
    for shard in bins:
        assert sum(1 for t in shard if "heavy" in t) == 1
    loads = [sum(weights[t] for t in shard) for shard in bins]
    assert max(loads) - min(loads) <= 1.0


def test_unknown_targets_get_the_median_weight_not_zero():
    weights = {"tests/known_a.py": 10.0, "tests/known_b.py": 10.0}
    targets = ["tests/known_a.py", "tests/known_b.py", "tests/new_c.py", "tests/new_d.py"]
    bins = _all_shards(targets, 2, weights)
    # A zero-weight default would pile both new files onto one shard.
    assert all(len(shard) == 2 for shard in bins)


def test_partition_is_deterministic():
    weights = {t: float(len(t) % 7) for t in TARGETS}
    assert _all_shards(TARGETS, 5, weights) == _all_shards(TARGETS, 5, weights)


def test_more_shards_than_targets_yields_empty_shards_not_an_error():
    bins = _all_shards(["tests/only.py"], 4, {})
    assert sum(len(b) for b in bins) == 1
    assert sum(1 for b in bins if not b) == 3


def test_load_durations_accepts_the_committed_wrapper_and_skips_dunders(tmp_path):
    path = tmp_path / "d.json"
    path.write_text(json.dumps({"__comment__": "x", "weights": {"tests/a.py": "2.5"}}))
    assert load_durations(path) == {"tests/a.py": 2.5}
    assert load_durations(tmp_path / "missing.json") == {}


def test_committed_map_covers_the_repository_test_files():
    weights = load_durations(__import__("pathlib").Path("ci/test-durations.json"))
    assert len(weights) > 100, "seed map went missing; shards would be unbalanced"
    assert all(k.startswith("tests/") for k in weights)


def test_durations_from_junit_sums_per_file(tmp_path):
    xml = tmp_path / "r.xml"
    xml.write_text(
        '<testsuites><testsuite>'
        '<testcase file="tests/a.py" classname="tests.a" name="t1" time="1.5"/>'
        '<testcase file="tests/a.py" classname="tests.a" name="t2" time="2.0"/>'
        '<testcase file="tests/b.py" classname="tests.b" name="t3" time="0.25"/>'
        "</testsuite></testsuites>"
    )
    assert durations_from_junit([xml]) == {"tests/a.py": 3.5, "tests/b.py": 0.25}


def test_durations_from_log_merges_marked_blocks_through_github_prefixes(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(
        "python pytest (1/6)\t2026-09-25T10:00:00.1234567Z ### CI_TEST_DURATIONS_JSON_BEGIN\n"
        'python pytest (1/6)\t2026-09-25T10:00:00.1234567Z {"tests/a.py": 3.5}\n'
        "python pytest (1/6)\t2026-09-25T10:00:00.1234567Z ### CI_TEST_DURATIONS_JSON_END\n"
        "python pytest (2/6)\t2026-09-25T10:01:00.1234567Z ### CI_TEST_DURATIONS_JSON_BEGIN\n"
        'python pytest (2/6)\t2026-09-25T10:01:00.1234567Z {"tests/b.py": 1.0, "tests/a.py": 1.0}\n'
        "python pytest (2/6)\t2026-09-25T10:01:00.1234567Z ### CI_TEST_DURATIONS_JSON_END\n"
    )
    # Same file seen twice keeps the larger reading, never the sum.
    assert durations_from_log(log) == {"tests/a.py": 3.5, "tests/b.py": 1.0}


def test_durations_from_log_refuses_a_log_without_markers(tmp_path):
    log = tmp_path / "empty.log"
    log.write_text("python pytest\t2026-09-25T10:00:00.0000000Z nothing here\n")
    with pytest.raises(SystemExit):
        durations_from_log(log)


def test_ignored_files_never_land_in_a_shard():
    """Expanding a directory makes every file an explicit pytest argument, and
    pytest runs an explicitly named file even under --ignore. The partition has
    to drop them, or the two inspect-ai files fail on every shard that gets
    them (BEAAA-35635, observed on run 36083846246 shards 1 and 5)."""
    ignores = ["tests/test_inspect_runner.py", "tests/unit/test_inspect_provenance_pin.py"]
    targets = [
        "tests/test_alpha.py",
        "tests/test_inspect_runner.py",
        "tests/test_inspect_runner.py::Klass::test_case",
        "tests/unit/test_inspect_provenance_pin.py",
        "tests/unit/test_other.py",
    ]
    assert ci_shard_tests.drop_ignored(targets, ignores) == [
        "tests/test_alpha.py",
        "tests/unit/test_other.py",
    ]
    assert ci_shard_tests.drop_ignored(targets, []) == targets


def test_ignore_matches_a_directory_root_not_a_path_prefix():
    kept = ci_shard_tests.drop_ignored(
        ["tests/unit/test_a.py", "tests/unittest_helper.py"], ["tests/unit"]
    )
    assert kept == ["tests/unittest_helper.py"]
