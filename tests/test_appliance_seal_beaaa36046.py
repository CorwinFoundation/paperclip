"""BEAAA-36046: the sealed appliance-0.1.0 artifact must match its unpacked tree.

install-listener-ariclaw.sh installs from ops/paperclip/numbat/backbond-appliance-0.1.0.zip,
not from the unpacked tree beside it. BEAAA-36046 edited the tree and left the zip alone, so
the fix could not reach the box and nothing failed. These tests close that hole: the zip, the
two sha256 manifests, and the provenance digests are all pinned to the tree.

Regenerate with: python3 ops/paperclip/numbat/reseal-appliance-0.1.0.py
"""
import hashlib
import json
import os
import zipfile

import pytest

NUMBAT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ops", "paperclip", "numbat",
)
TREE = os.path.join(NUMBAT, "backbond-appliance-0.1.0", "backbond-appliance")
ZIP = os.path.join(NUMBAT, "backbond-appliance-0.1.0.zip")
SRC_MANIFEST = os.path.join(NUMBAT, "backbond-appliance-0.1.0.source-tree.sha256")
TREE_MANIFEST = os.path.join(NUMBAT, "backbond-appliance-0.1.0.tree.sha256")
PROVENANCE = os.path.join(NUMBAT, "backbond-appliance-0.1.0.provenance.json")
PREFIX = "backbond-appliance/"
REGEN = "regenerate with: python3 ops/paperclip/numbat/reseal-appliance-0.1.0.py"


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _tree():
    """relative path -> sha256, for every regular file in the unpacked tree."""
    out = {}
    for root, dirs, files in os.walk(TREE):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".pyc"):
                continue
            p = os.path.join(root, f)
            out[os.path.relpath(p, TREE)] = _sha(p)
    return out


def _zip_members():
    """relative path -> sha256, for every file member of the sealed archive."""
    out = {}
    with zipfile.ZipFile(ZIP) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            assert info.filename.startswith(PREFIX), info.filename
            out[info.filename[len(PREFIX):]] = hashlib.sha256(z.read(info)).hexdigest()
    return out


def _manifest(path):
    """Parse `<sha>  ./<rel>` lines into relative path -> sha256."""
    out = {}
    with open(path, "rb") as fh:
        for line in fh.read().decode().splitlines():
            if not line.strip():
                continue
            digest, rel = line.split("  ", 1)
            assert rel.startswith("./"), rel
            out[rel[2:]] = digest
    return out


@pytest.fixture(scope="module")
def tree():
    return _tree()


def test_zip_members_match_unpacked_tree(tree):
    members = _zip_members()
    assert sorted(members) == sorted(tree), (
        "sealed archive and unpacked tree disagree on which files exist; "
        "only_in_zip=%s only_in_tree=%s; %s"
        % (sorted(set(members) - set(tree)), sorted(set(tree) - set(members)), REGEN)
    )
    differing = sorted(k for k in tree if members[k] != tree[k])
    assert not differing, (
        "sealed archive content is stale for %s -- the installer ships the zip, so an edit to "
        "the tree alone never reaches the box; %s" % (differing, REGEN)
    )


@pytest.mark.parametrize("manifest_path", [SRC_MANIFEST, TREE_MANIFEST])
def test_manifest_matches_unpacked_tree(manifest_path, tree):
    recorded = _manifest(manifest_path)
    assert recorded == tree, (
        "%s is stale; only_in_manifest=%s only_in_tree=%s differing=%s; %s"
        % (
            os.path.basename(manifest_path),
            sorted(set(recorded) - set(tree)),
            sorted(set(tree) - set(recorded)),
            sorted(k for k in set(recorded) & set(tree) if recorded[k] != tree[k]),
            REGEN,
        )
    )


def test_both_manifests_are_byte_identical():
    assert open(SRC_MANIFEST, "rb").read() == open(TREE_MANIFEST, "rb").read(), (
        "the source-tree and tree manifests describe the same unpacked tree and must not "
        "diverge; %s" % REGEN
    )


def test_manifest_lines_are_lc_all_c_sorted():
    """Pinned by provenance artifact.manifestAlgorithm."""
    rels = list(_manifest(SRC_MANIFEST))
    assert rels == sorted(rels), "manifest is not LC_ALL=C sorted by relative path; %s" % REGEN


def test_provenance_digests_match_the_sealed_artifact(tree):
    with open(PROVENANCE) as fh:
        prov = json.load(fh)
    art = prov["artifact"]
    assert art["archiveSha256"] == _sha(ZIP), (
        "provenance archiveSha256 does not describe the archive on disk; %s" % REGEN
    )
    assert art["sourceManifestSha256"] == _sha(SRC_MANIFEST), (
        "provenance sourceManifestSha256 does not describe the manifest on disk; %s" % REGEN
    )
    assert art["sourceFileCount"] == len(tree), (
        "provenance sourceFileCount says %s, tree holds %s; %s"
        % (art["sourceFileCount"], len(tree), REGEN)
    )


def test_historical_baseline_digests_are_untouched():
    """The Founder-delivered baseline is immutable; only the candidate artifact is re-sealed."""
    with open(PROVENANCE) as fh:
        prov = json.load(fh)
    baseline = prov["versionSeparation"]["historicalBaseline"]
    assert baseline["archiveSha256"] == (
        "f8d283d0013312c61faf9d4e4100b22167710cec8b7cf6a4f7e46079e2399e80"
    )
    assert baseline["fullOnBoxManifestSha256"] == (
        "44ebe1ad8584050482928c3a56f2923170a70920f1a7a238b6f625af08e298de"
    )


def test_reseal_is_byte_reproducible_from_the_tree(tmp_path):
    """The re-seal script must reproduce the committed archive exactly, or the pin is not a pin."""
    import shutil
    import subprocess
    import sys

    stage = tmp_path / "ops" / "paperclip" / "numbat"
    stage.mkdir(parents=True)
    shutil.copytree(
        os.path.join(NUMBAT, "backbond-appliance-0.1.0"),
        str(stage / "backbond-appliance-0.1.0"),
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for name in (
        "backbond-appliance-0.1.0.zip",
        "backbond-appliance-0.1.0.provenance.json",
        "reseal-appliance-0.1.0.py",
    ):
        shutil.copy2(os.path.join(NUMBAT, name), str(stage / name))
    subprocess.run(
        [sys.executable, str(stage / "reseal-appliance-0.1.0.py")],
        check=True, capture_output=True,
    )
    assert _sha(str(stage / "backbond-appliance-0.1.0.zip")) == _sha(ZIP), (
        "re-sealing the committed tree does not reproduce the committed archive byte for byte"
    )
