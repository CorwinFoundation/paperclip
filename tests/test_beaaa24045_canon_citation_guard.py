"""Acceptance tests for the phantom-citation repository hygiene gate."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("phantom_guard", REPO / "scripts" / "check_no_phantom_canon_citation.py")
assert spec and spec.loader
guard = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = guard
spec.loader.exec_module(guard)


def test_repository_is_clean_and_allowlist_is_exact() -> None:
    assert guard.scan(REPO) == []
    assert guard.stale_allowlist_entries(REPO) == []
    assert len(guard.ALLOWLIST) == 9
    assert all(reason.strip() for reason in guard.ALLOWLIST.values())


def test_guard_rejects_new_offender_and_reports_remedy(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    offender = tmp_path / "new.md"
    offender.write_text(f"citation: `{guard.FORBIDDEN_SUBSTRING}.md`\n", encoding="utf-8")
    assert guard.scan(tmp_path) == [("new.md", 1, f"citation: `{guard.FORBIDDEN_SUBSTRING}.md`")]
    assert guard.main(["--repo-root", str(tmp_path)]) == 1
    assert guard.CANONICAL_FORM in capsys.readouterr().err


def test_sibling_of_allowlisted_path_is_not_exempt(tmp_path: Path) -> None:
    relative = next(iter(guard.ALLOWLIST))
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_text(guard.FORBIDDEN_SUBSTRING, encoding="utf-8")
    sibling = target.parent / "sibling.md"
    sibling.write_text(guard.FORBIDDEN_SUBSTRING, encoding="utf-8")
    assert guard.scan(tmp_path) == [(sibling.relative_to(tmp_path).as_posix(), 1, guard.FORBIDDEN_SUBSTRING)]


def test_missing_root_is_usage_error(tmp_path: Path) -> None:
    assert guard.main(["--repo-root", str(tmp_path / "missing")]) == 2
