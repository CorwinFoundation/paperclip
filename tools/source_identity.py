"""Source identity helpers for git checkouts and exported archives."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


SOURCE_COMMIT_ENV = "BACKBOND_SOURCE_COMMIT_SHA"
SOURCE_COMMIT_FILE = ".backbond-source-commit"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _valid_sha(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value if _SHA_RE.fullmatch(value) else None


def repo_head(repo: Path | None = None) -> str:
    """Return the exact source commit for a git checkout or git archive.

    `git archive` intentionally excludes `.git`, so release-archive tests cannot
    depend on `git rev-parse HEAD`. Archives produced from this repository carry
    `.backbond-source-commit` with `export-subst`, which expands to the archived
    commit SHA. The environment variable exists only as an explicit harness
    override for non-git copies.
    """
    root = (repo or Path(__file__).resolve().parents[1]).resolve()
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    env_sha = _valid_sha(os.environ.get(SOURCE_COMMIT_ENV))
    if env_sha is not None:
        return env_sha

    archive_sha = _valid_sha((root / SOURCE_COMMIT_FILE).read_text(encoding="utf-8"))
    if archive_sha is not None:
        return archive_sha

    raise RuntimeError(
        "cannot resolve source commit: not a git checkout and no valid "
        f"{SOURCE_COMMIT_FILE} / {SOURCE_COMMIT_ENV}"
    )
