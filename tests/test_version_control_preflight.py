from __future__ import annotations

import unittest

from tools.version_control_preflight import RepoState, evaluate


def _state(**overrides: object) -> RepoState:
    values: dict[str, object] = {
        "repository": "/repo",
        "branch": "beaaa-123-clean-worktree",
        "head": "b" * 40,
        "canonical_ref": "origin/master",
        "canonical_sha": "a" * 40,
        "clean": True,
        "canonical_is_ancestor": True,
        "upstream": "origin/beaaa-123-clean-worktree",
    }
    values.update(overrides)
    return RepoState(**values)  # type: ignore[arg-type]


class VersionControlPreflightTest(unittest.TestCase):
    def test_clean_feature_branch_based_on_canonical_passes(self) -> None:
        self.assertEqual(evaluate(_state()), [])

    def test_dirty_worktree_is_blocked(self) -> None:
        self.assertIn("dirty_worktree", evaluate(_state(clean=False)))

    def test_direct_master_work_is_blocked(self) -> None:
        self.assertIn("direct_default_branch_work", evaluate(_state(branch="master")))

    def test_detached_head_is_blocked(self) -> None:
        self.assertIn("detached_head", evaluate(_state(branch="HEAD")))

    def test_stale_branch_is_blocked(self) -> None:
        self.assertIn(
            "branch_not_based_on_current_canonical_ref",
            evaluate(_state(canonical_is_ancestor=False)),
        )


if __name__ == "__main__":
    unittest.main()
