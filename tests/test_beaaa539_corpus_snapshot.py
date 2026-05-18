"""BEAAA-539 — compute_corpus_snapshot_hash unit tests.

Acceptance criteria (per ticket):
  1. Function exposed on scanners.snapshot_hash returning "sha256:<64-hex>".
  2. Deterministic across tarballs built at different times / member orders
     / archive metadata, given byte-identical file contents.
  3. Raises on missing path.
  4. Returns a canonical hash on a fixture tarball.
"""
from __future__ import annotations

import io
import re
import tarfile
import time
import unittest
from pathlib import Path

from scanners.snapshot_hash import compute_corpus_snapshot_hash

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def _make_tar(
    path: Path,
    files: list[tuple[str, bytes]],
    *,
    mtime: int,
    uid: int = 0,
    gid: int = 0,
    mode: int = 0o644,
    compression: str = "",
) -> None:
    open_mode = f"w:{compression}" if compression else "w"
    with tarfile.open(path, mode=open_mode) as tar:
        for name, data in files:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = mtime
            info.uid = uid
            info.gid = gid
            info.mode = mode
            tar.addfile(info, io.BytesIO(data))


_FILES = [
    ("probes/governance/p1.yaml", b"id: G.AUDIT.001\nprompts: [hello]\n"),
    ("probes/robustness/p1.yaml", b"id: R.PI.MULTI_TURN\nprompts: [hi]\n"),
    ("probes/bias/p1.yaml", b"id: B.BBQ.AGE\nprompts: []\n"),
    ("manifest.json", b'{"corpus_version":"0.3.0","probe_count":3}\n'),
]


class CorpusSnapshotHashTests(unittest.TestCase):
    def setUp(self) -> None:
        # tmpdir manually so the test runs without pytest-tmp_path coupling
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_returns_canonical_sha256_format(self) -> None:
        tar = self.tmp / "corpus.tar"
        _make_tar(tar, _FILES, mtime=1_700_000_000)
        result = compute_corpus_snapshot_hash(tar)
        self.assertRegex(result, _SHA256)

    def test_byte_identical_contents_yield_same_hash_across_mtime(self) -> None:
        """Different mtimes, same payload → identical hash."""
        tar_a = self.tmp / "a.tar"
        tar_b = self.tmp / "b.tar"
        _make_tar(tar_a, _FILES, mtime=1_600_000_000)
        # Make sure b is built later in wall-clock time, with a different mtime.
        time.sleep(0.01)
        _make_tar(tar_b, _FILES, mtime=1_800_000_000)
        self.assertEqual(
            compute_corpus_snapshot_hash(tar_a),
            compute_corpus_snapshot_hash(tar_b),
        )

    def test_member_order_does_not_affect_hash(self) -> None:
        tar_a = self.tmp / "ordered.tar"
        tar_b = self.tmp / "shuffled.tar"
        _make_tar(tar_a, _FILES, mtime=1_700_000_000)
        _make_tar(tar_b, list(reversed(_FILES)), mtime=1_700_000_000)
        self.assertEqual(
            compute_corpus_snapshot_hash(tar_a),
            compute_corpus_snapshot_hash(tar_b),
        )

    def test_archive_metadata_does_not_affect_hash(self) -> None:
        tar_a = self.tmp / "default.tar"
        tar_b = self.tmp / "alt_meta.tar"
        _make_tar(tar_a, _FILES, mtime=1_700_000_000, uid=0, gid=0, mode=0o644)
        _make_tar(
            tar_b, _FILES, mtime=1_700_000_000, uid=1000, gid=1000, mode=0o600
        )
        self.assertEqual(
            compute_corpus_snapshot_hash(tar_a),
            compute_corpus_snapshot_hash(tar_b),
        )

    def test_compression_format_does_not_affect_hash(self) -> None:
        tar_plain = self.tmp / "corpus.tar"
        tar_gz = self.tmp / "corpus.tar.gz"
        _make_tar(tar_plain, _FILES, mtime=1_700_000_000)
        _make_tar(tar_gz, _FILES, mtime=1_700_000_000, compression="gz")
        self.assertEqual(
            compute_corpus_snapshot_hash(tar_plain),
            compute_corpus_snapshot_hash(tar_gz),
        )

    def test_content_change_changes_hash(self) -> None:
        tar_a = self.tmp / "a.tar"
        tar_b = self.tmp / "b.tar"
        _make_tar(tar_a, _FILES, mtime=1_700_000_000)
        mutated = list(_FILES)
        mutated[0] = (mutated[0][0], mutated[0][1] + b"# extra\n")
        _make_tar(tar_b, mutated, mtime=1_700_000_000)
        self.assertNotEqual(
            compute_corpus_snapshot_hash(tar_a),
            compute_corpus_snapshot_hash(tar_b),
        )

    def test_filename_change_changes_hash(self) -> None:
        tar_a = self.tmp / "a.tar"
        tar_b = self.tmp / "b.tar"
        _make_tar(tar_a, _FILES, mtime=1_700_000_000)
        renamed = list(_FILES)
        renamed[0] = ("probes/governance/p1_renamed.yaml", renamed[0][1])
        _make_tar(tar_b, renamed, mtime=1_700_000_000)
        self.assertNotEqual(
            compute_corpus_snapshot_hash(tar_a),
            compute_corpus_snapshot_hash(tar_b),
        )

    def test_missing_path_raises_filenotfounderror(self) -> None:
        with self.assertRaises(FileNotFoundError):
            compute_corpus_snapshot_hash(self.tmp / "does-not-exist.tar")

    def test_canonical_hash_on_fixture_payload(self) -> None:
        """Pin the canonical hash for the _FILES fixture.

        Anchors the recipe: if the canonicalization ever changes, this test
        breaks and forces a contract review.
        """
        tar = self.tmp / "fixture.tar"
        _make_tar(tar, _FILES, mtime=1_700_000_000)
        result = compute_corpus_snapshot_hash(tar)
        # Preimage for _FILES, sorted by name (the recipe defined in the
        # module docstring of scanners.snapshot_hash). The constant below is
        # the pinned canonical hash; any drift forces a contract review.
        expected = (
            "sha256:68ef46f4d339a1df6276c52e220e9f421555db0dcefa237a860f56f2ce6d2a73"
        )
        self.assertEqual(result, expected)

    def test_directory_only_archive_yields_empty_preimage_hash(self) -> None:
        """A tarball containing only a directory entry hashes to sha256("")."""
        import hashlib as _hl
        tar = self.tmp / "dirs_only.tar"
        with tarfile.open(tar, mode="w") as t:
            info = tarfile.TarInfo(name="probes/")
            info.type = tarfile.DIRTYPE
            info.mtime = 1_700_000_000
            t.addfile(info)
        empty_hash = "sha256:" + _hl.sha256(b"").hexdigest()
        self.assertEqual(compute_corpus_snapshot_hash(tar), empty_hash)

    def test_symlink_member_raises_valueerror(self) -> None:
        tar = self.tmp / "with_symlink.tar"
        with tarfile.open(tar, mode="w") as t:
            info = tarfile.TarInfo(name="link")
            info.type = tarfile.SYMTYPE
            info.linkname = "manifest.json"
            t.addfile(info)
        with self.assertRaises(ValueError):
            compute_corpus_snapshot_hash(tar)


if __name__ == "__main__":
    unittest.main()
