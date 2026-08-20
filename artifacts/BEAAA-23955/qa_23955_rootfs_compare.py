#!/usr/bin/env python3
"""QA BEAAA-23955: independently reproduce the filesystem comparison claimed in
docs/deploy/scanner-image-c4a58caa-canonical.md at candidate 70e2632b.

Materialises both images' root filesystems by applying their layer tars in order
(with OCI/AUFS whiteout semantics) and records, per path, the entry type and a
content hash. Nothing is loaded into the docker daemon.
"""
import gzip
import hashlib
import io
import json
import os
import sys
import tarfile

PYC_HEADER = 16


def open_blob(path):
    with open(path, "rb") as fh:
        magic = fh.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rb")
    return open(path, "rb")


def apply_layers(root, layer_paths):
    """Return {path: entry} after applying layers in order."""
    fs = {}
    for lp in layer_paths:
        stream = open_blob(os.path.join(root, lp))
        with tarfile.open(fileobj=stream, mode="r|*") as tf:
            for m in tf:
                name = m.name
                if name.startswith("./"):
                    name = name[2:]
                name = name.rstrip("/")
                if not name:
                    continue
                base = os.path.basename(name)
                parent = os.path.dirname(name)
                if base == ".wh..wh..opq":
                    pref = (parent + "/") if parent else ""
                    for k in [k for k in fs if k.startswith(pref) and k != parent]:
                        del fs[k]
                    continue
                if base.startswith(".wh."):
                    target = os.path.join(parent, base[4:]) if parent else base[4:]
                    fs.pop(target, None)
                    for k in [k for k in fs if k.startswith(target + "/")]:
                        del fs[k]
                    continue
                if m.isreg():
                    data = tf.extractfile(m).read()
                    entry = {
                        "t": "f",
                        "sz": len(data),
                        "h": hashlib.sha256(data).hexdigest(),
                        "mode": m.mode,
                        "uid": m.uid,
                        "gid": m.gid,
                    }
                    if len(data) > PYC_HEADER:
                        entry["tail"] = hashlib.sha256(data[PYC_HEADER:]).hexdigest()
                        entry["head"] = data[:PYC_HEADER].hex()
                    if len(data) <= 65536:
                        entry["raw"] = data
                elif m.issym():
                    entry = {"t": "l", "h": m.linkname}
                elif m.islnk():
                    entry = {"t": "hl", "h": m.linkname}
                elif m.isdir():
                    entry = {"t": "d", "h": "", "mode": m.mode}
                else:
                    entry = {"t": "o%d" % m.type[0], "h": ""}
                fs[name] = entry
    return fs


def load(root):
    man = json.load(open(os.path.join(root, "manifest.json")))[0]
    cfg = json.load(open(os.path.join(root, man["Config"])))
    return apply_layers(root, man["Layers"]), cfg


def main():
    ci, ci_cfg = load(sys.argv[1])
    alt, alt_cfg = load(sys.argv[2])
    ci_paths, alt_paths = set(ci), set(alt)
    only_ci = sorted(ci_paths - alt_paths)
    only_alt = sorted(alt_paths - ci_paths)
    common = ci_paths & alt_paths

    identical, differing = [], []
    for p in sorted(common):
        a, b = ci[p], alt[p]
        if a["t"] == b["t"] and a.get("h") == b.get("h"):
            identical.append(p)
        else:
            differing.append(p)

    pyc_header_only, other_diff = [], []
    for p in differing:
        a, b = ci[p], alt[p]
        if (
            a["t"] == "f"
            and b["t"] == "f"
            and p.endswith(".pyc")
            and a.get("tail")
            and a.get("tail") == b.get("tail")
        ):
            pyc_header_only.append(p)
        else:
            other_diff.append(p)

    print("== entry counts ==")
    print("ci_entries       =", len(ci_paths))
    print("alt_entries      =", len(alt_paths))
    print("only_in_ci       =", len(only_ci), only_ci[:10])
    print("only_in_alt      =", len(only_alt), only_alt[:10])
    print("common           =", len(common))
    print("identical        =", len(identical))
    print("differing        =", len(differing))
    print("pyc_header_only  =", len(pyc_header_only))
    print("other_differing  =", len(other_diff))
    print()
    print("== non-pyc differing entries ==")
    for p in other_diff:
        a, b = ci[p], alt[p]
        print("  ", p, "type", a["t"], "/", b["t"], "size", a.get("sz"), "/", b.get("sz"))
    print()
    print("== /app differences ==")
    app = [p for p in differing if p.startswith("app/") or p == "app"]
    print("app_differing =", len(app))
    for p in app[:40]:
        print("   ", p)
    print()
    print("== named-file spot checks ==")
    for target in ("etc/group-", "etc/shadow", "etc/group", "etc/passwd"):
        if target in ci and target in alt:
            same = ci[target].get("h") == alt[target].get("h")
            print(f"  {target}: identical={same}")
            if not same and "raw" in ci[target] and "raw" in alt[target]:
                la = ci[target]["raw"].decode("utf-8", "replace").splitlines()
                lb = alt[target]["raw"].decode("utf-8", "replace").splitlines()
                for x, y in zip(la, lb):
                    if x != y:
                        print("     ci :", x)
                        print("     alt:", y)
                if len(la) != len(lb):
                    print("     LINE COUNT DIFFERS", len(la), len(lb))
    print()
    print("== pyc header sanity (first 5) ==")
    for p in pyc_header_only[:5]:
        print("  ", p, ci[p]["head"], "->", alt[p]["head"])
    print()
    print("== config comparison ==")
    print("ci_diff_ids ==alt_diff_ids:", ci_cfg["rootfs"]["diff_ids"] == alt_cfg["rootfs"]["diff_ids"])
    shared = [
        (i, x)
        for i, (x, y) in enumerate(zip(ci_cfg["rootfs"]["diff_ids"], alt_cfg["rootfs"]["diff_ids"]))
        if x == y
    ]
    print("positionally identical diff_ids:", len(shared), [i for i, _ in shared])
    print("ci_labels==alt_labels:", ci_cfg["config"].get("Labels") == alt_cfg["config"].get("Labels"))
    print()
    print("== release manifest / source commit ==")
    for p in sorted(ci_paths):
        if "release" in p.lower() and p.endswith(".json") and p.startswith("app"):
            print("  candidate manifest path:", p, "identical=", ci[p].get("h") == alt.get(p, {}).get("h"))
    for p in sorted(ci_paths):
        if "backbond-source-commit" in p:
            print("  ", p, "identical=", ci[p].get("h") == alt.get(p, {}).get("h"),
                  "content=", ci[p].get("raw", b"")[:80])


main()
