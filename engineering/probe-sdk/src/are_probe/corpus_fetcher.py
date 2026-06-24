"""Fetch helpers for corpus source material.

Network fetches must use immutable commit SHAs. Branches, tags, and short SHAs
are intentionally rejected before URL construction.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from .corpus import CorpusError, assert_commit_sha


@dataclass(frozen=True)
class CorpusSource:
    repo: str
    path: str
    commit_sha: str


def github_raw_url(source: CorpusSource) -> str:
    """Build a raw GitHub URL pinned to a full source commit SHA."""
    assert_commit_sha(source.commit_sha, field="source.commit_sha")
    if not source.repo.startswith("https://github.com/"):
        raise CorpusError("source.repo must be an https://github.com/ URL")
    if not source.path or source.path.startswith("/"):
        raise CorpusError("source.path must be a repository-relative path")

    repo = source.repo.removesuffix("/")
    rel_path = quote(source.path, safe="/")
    return f"{repo}/raw/{source.commit_sha}/{rel_path}"
