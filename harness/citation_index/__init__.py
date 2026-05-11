"""Citation-existence index for P1.2 (BEAAA-206) and P1.1 F2 (BEAAA-205 H3).

Hash-gated, snapshot-pinned lookup over a hand-curated seed of US federal
canon plus a small slice of state appellate canon. Live fetching is not
performed at scan time — calibration discipline (BEAAA-108) requires a
byte-stable corpus.

Public API:
  load_index(...)        -> Index
  Index.lookup(text)     -> list[IndexHit]   # match by case_name or normalized_citation in text
  Index.is_in_index(spec)-> bool             # normalized-citation membership check
  Index.entries          -> tuple[IndexEntry, ...]
  IndexHashMismatch                          # raised on hash gate failure
"""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

_DATA_DIR = Path(__file__).resolve().parent / "data"
_DEFAULT_MANIFEST = _DATA_DIR / "MANIFEST.yaml"


class IndexHashMismatch(RuntimeError):
    """Raised when the on-disk CSV does not match the manifest hash."""


@dataclass(frozen=True)
class IndexEntry:
    kind: str               # case | statute | rule | treatise
    normalized_citation: str
    case_name: str          # may be "" for non-case kinds
    year: str               # may be "" for rules/treatise where N/A
    jurisdiction: str
    proposition: str
    bucket: str             # scotus_canon | usc_canon | frcp_canon | etc.


@dataclass(frozen=True)
class IndexHit:
    entry: IndexEntry
    matched_on: str         # "normalized_citation" | "case_name"
    match_span: str         # the substring of input text that matched


def _normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace and strip — for tolerant containment checks."""
    return re.sub(r"\s+", " ", text).strip()


def _normalize_reporter(text: str) -> str:
    """Tolerate `U. S.` ↔ `U.S.`, `F. 3d` ↔ `F.3d`, etc.

    We only collapse whitespace immediately following the dotted reporter
    abbreviations we know about; this preserves case names that contain
    periods (e.g. "Co. v.") and §-style statute identifiers.
    """
    out = text
    for left, right in (
        ("U. S.", "U.S."),
        ("U. S. C.", "U.S.C."),
        ("U.S. C.", "U.S.C."),
        ("F. 2d", "F.2d"),
        ("F. 3d", "F.3d"),
        ("F. 4th", "F.4th"),
        ("F. Supp.", "F. Supp."),
        ("N. Y.", "N.Y."),
        ("Cal. 3d", "Cal.3d"),
        ("Cal. 4th", "Cal.4th"),
    ):
        out = out.replace(left, right)
    return _normalize_whitespace(out)


@dataclass(frozen=True)
class Index:
    entries: tuple[IndexEntry, ...]
    manifest_version: str
    csv_sha256: str

    # ---- membership ----
    def is_in_index(self, spec: str) -> bool:
        target = _normalize_reporter(spec)
        for e in self.entries:
            if _normalize_reporter(e.normalized_citation) == target:
                return True
        return False

    # ---- text-level extraction ----
    def lookup(self, text: str) -> list[IndexHit]:
        """Return all entries whose normalized_citation OR case_name appears in text.

        De-duped by (entry, matched_on). Case insensitive case_name match;
        normalized_citation match is reporter-tolerant.
        """
        norm_text = _normalize_reporter(text)
        norm_text_lc = norm_text.lower()
        hits: list[IndexHit] = []
        seen: set[tuple[str, str]] = set()
        for e in self.entries:
            cite_norm = _normalize_reporter(e.normalized_citation)
            if cite_norm and cite_norm in norm_text:
                key = (e.normalized_citation, "normalized_citation")
                if key not in seen:
                    seen.add(key)
                    hits.append(IndexHit(entry=e, matched_on="normalized_citation", match_span=cite_norm))
            if e.case_name:
                cname = e.case_name.lower()
                if cname in norm_text_lc:
                    key = (e.normalized_citation, "case_name")
                    if key not in seen:
                        seen.add(key)
                        hits.append(IndexHit(entry=e, matched_on="case_name", match_span=e.case_name))
        return hits

    # ---- convenience ----
    def by_bucket(self, bucket: str) -> tuple[IndexEntry, ...]:
        return tuple(e for e in self.entries if e.bucket == bucket)

    def __len__(self) -> int:
        return len(self.entries)


def _load_manifest(manifest_path: Path) -> dict:
    return yaml.safe_load(manifest_path.read_text())


def _expected_hash(manifest: dict, source_path: Path) -> str:
    for src in manifest.get("source_files", []):
        if src.get("path") == source_path.name:
            return src["sha256"]
    raise IndexHashMismatch(f"manifest has no entry for {source_path.name}")


def load_index(manifest_path: Path | str | None = None) -> Index:
    """Load the citation index, gated on the manifest hash."""
    mpath = Path(manifest_path) if manifest_path else _DEFAULT_MANIFEST
    manifest = _load_manifest(mpath)
    csv_path = mpath.parent / manifest["source_files"][0]["path"]
    raw = csv_path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    expected = _expected_hash(manifest, csv_path)
    if actual != expected:
        raise IndexHashMismatch(
            f"{csv_path.name} hash {actual} does not match manifest hash {expected}"
        )
    with csv_path.open() as fh:
        entries = tuple(
            IndexEntry(
                kind=row["kind"],
                normalized_citation=row["normalized_citation"],
                case_name=row.get("case_name") or "",
                year=row.get("year") or "",
                jurisdiction=row.get("jurisdiction") or "",
                proposition=row.get("proposition") or "",
                bucket=row.get("bucket") or "",
            )
            for row in csv.DictReader(fh)
        )
    return Index(
        entries=entries,
        manifest_version=manifest.get("version", "unknown"),
        csv_sha256=actual,
    )


# Convenience module-level singleton-ish accessor for callers that want
# to share one loaded index across many probes in the same scan.
_CACHED: Index | None = None


def get_index(force_reload: bool = False) -> Index:
    global _CACHED
    if _CACHED is None or force_reload:
        _CACHED = load_index()
    return _CACHED


def reset_cache() -> None:
    """Test hook: drop the cached index."""
    global _CACHED
    _CACHED = None


def iter_entries(kinds: Iterable[str] | None = None) -> Iterable[IndexEntry]:
    """Yield entries from the cached index, optionally filtered by kind."""
    idx = get_index()
    kind_set = set(kinds) if kinds else None
    for e in idx.entries:
        if kind_set is None or e.kind in kind_set:
            yield e
