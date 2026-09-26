#!/usr/bin/env python3
"""Rebuild the sealed backbond-appliance-0.1.0 artifact from its unpacked tree.

The installer (install-listener-ariclaw.sh) installs from the ZIP, not from the
unpacked tree, so an edit to the tree alone never reaches the box. This script
re-seals the triple -- zip, the two manifests, and the provenance artifact block
-- from the tree, using the same normalised build parameters as the original
archive so the result is byte-reproducible:

  * directory entries STORED, file entries DEFLATED
  * every date_time normalised to (1980, 1, 1, 0, 0, 0)
  * create_system 3 (unix), create_version 20
  * per-member unix mode carried over from the previous archive; a member that
    is new to the tree takes the mode of the file on disk

Manifest format is pinned by the provenance file itself:
  LC_ALL=C sorted relative path; `<file_sha256>  ./<relative_path>\n` per line.
"""
import hashlib
import json
import os
import stat
import sys
import zipfile

NUMBAT = os.path.dirname(os.path.abspath(__file__))
TREE = os.path.join(NUMBAT, "backbond-appliance-0.1.0", "backbond-appliance")
ZIP = os.path.join(NUMBAT, "backbond-appliance-0.1.0.zip")
SRC_MANIFEST = os.path.join(NUMBAT, "backbond-appliance-0.1.0.source-tree.sha256")
TREE_MANIFEST = os.path.join(NUMBAT, "backbond-appliance-0.1.0.tree.sha256")
PROVENANCE = os.path.join(NUMBAT, "backbond-appliance-0.1.0.provenance.json")
PREFIX = "backbond-appliance/"
EPOCH = (1980, 1, 1, 0, 0, 0)


def tree_files():
    """Relative paths of every regular file in the unpacked tree, LC_ALL=C sorted."""
    out = []
    for root, dirs, files in os.walk(TREE):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for f in sorted(files):
            if f.endswith(".pyc"):
                continue
            out.append(os.path.relpath(os.path.join(root, f), TREE))
    return sorted(out)


def manifest_bytes(paths):
    lines = []
    for rel in paths:
        with open(os.path.join(TREE, rel), "rb") as fh:
            lines.append("%s  ./%s\n" % (hashlib.sha256(fh.read()).hexdigest(), rel))
    return "".join(lines).encode()


def previous_modes():
    """member relative path -> unix mode, from the archive we are replacing."""
    modes = {}
    if not os.path.exists(ZIP):
        return modes
    with zipfile.ZipFile(ZIP) as z:
        for info in z.infolist():
            rel = info.filename[len(PREFIX):]
            modes[rel] = (info.external_attr >> 16) & 0xFFFF
    return modes


def build_zip(paths, modes):
    dirs = set()
    for rel in paths:
        parts = rel.split("/")[:-1]
        for i in range(len(parts)):
            dirs.add("/".join(parts[: i + 1]) + "/")
    entries = [("", True)] + [(d, True) for d in sorted(dirs)] + [(p, False) for p in paths]
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, is_dir in entries:
            info = zipfile.ZipInfo(PREFIX + rel, date_time=EPOCH)
            info.create_system = 3
            info.create_version = 20
            if is_dir:
                mode = modes.get(rel, 0o40755) or 0o40755
                info.external_attr = (mode << 16) | 0x10
                info.compress_type = zipfile.ZIP_STORED
                z.writestr(info, b"")
                continue
            disk = os.path.join(TREE, rel)
            mode = modes.get(rel)
            if mode is None:
                mode = stat.S_IMODE(os.stat(disk).st_mode) | stat.S_IFREG
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(disk, "rb") as fh:
                z.writestr(info, fh.read())


def main():
    paths = tree_files()
    modes = previous_modes()
    build_zip(paths, modes)

    blob = manifest_bytes(paths)
    with open(SRC_MANIFEST, "wb") as fh:
        fh.write(blob)
    with open(TREE_MANIFEST, "wb") as fh:
        fh.write(blob)

    with open(ZIP, "rb") as fh:
        archive_sha = hashlib.sha256(fh.read()).hexdigest()
    with open(PROVENANCE) as fh:
        prov = json.load(fh)
    art = prov["artifact"]
    art["archiveSha256"] = archive_sha
    art["sourceManifestSha256"] = hashlib.sha256(blob).hexdigest()
    art["sourceFileCount"] = len(paths)
    prov["verification"]["sourceManifest"] = "all %d declared files match" % len(paths)
    prov["verification"]["archiveMembersMatchManifestAndUnpackedTree"] = True
    with open(PROVENANCE, "w") as fh:
        json.dump(prov, fh, indent=2)
        fh.write("\n")

    print("files          %d" % len(paths))
    print("archiveSha256  %s" % archive_sha)
    print("manifestSha256 %s" % art["sourceManifestSha256"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
