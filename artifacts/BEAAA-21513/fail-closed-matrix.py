#!/usr/bin/env python3
"""BEAAA-21513 QA: exercise verify_scanner_image.py main() end-to-end + fail-closed matrix.

Builds realistic fixtures, then drives the REAL CLI entrypoint the workflow calls
(scripts/verify_scanner_image.py) as a subprocess with the exact argument shape from
.github/workflows/scanner-image-readonly-verify.yml.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

WT = Path(os.environ["WT"])
SCRIPT = WT / "scripts/verify_scanner_image.py"

# Values pinned in the candidate workflow.
REPO_IMG = "ghcr.io/corwinfoundation/are-scanner"
TAG = "sha-7850289333ffc1de6eebea9c2201e0f1059c4c12"
DIGEST = "sha256:e955a2812015cf7da487c2e266e0a7f3fc9478cdafc2580dc0e02df53d96df9b"
REVISION = "7850289333ffc1de6eebea9c2201e0f1059c4c12"
MANIFEST_SHA = "2417a7b22c1ad82c7b3ad68eb815b03b6345be6b1c25bbcd54fc756a85957ddc"
RUN_ID = "30672001992"
ARTIFACT_ID = "8809120470"
RUN_URL = "https://github.com/CorwinFoundation/are-scanner/actions/runs/999"

WORK = Path("/tmp/qa21513_fix")


def build_fixtures(base: Path) -> dict:
    base.mkdir(parents=True, exist_ok=True)

    # 1. A source release manifest whose sha256 IS the pinned EXPECTED_MANIFEST_SHA256.
    #    We cannot forge a preimage, so we compute the real sha of our fixture and use
    #    that as the "expected" in the happy path; the mismatch case uses the pinned one.
    manifest = base / "frozen-release-manifest.json"
    manifest.write_text(json.dumps({"release": "backbond-scanner", "revision": REVISION}, indent=2))
    real_manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()

    # 2. image-inspect.json in `docker image inspect` shape (a list of one).
    inspect = base / "image-inspect.json"
    inspect.write_text(json.dumps([{
        "Id": DIGEST,
        "RepoDigests": [f"{REPO_IMG}@{DIGEST}"],
        "Config": {"Labels": {
            "org.opencontainers.image.revision": REVISION,
            "ai.backbond.release.manifest-sha256": real_manifest_sha,
        }},
    }], indent=2))

    # 3. An Actions artifact zip containing the image tar + its bundled checksum.
    tar_bytes = b"\x1f\x8b" + b"fake scanner-edge-image tar payload" * 64
    tar_sha = hashlib.sha256(tar_bytes).hexdigest()
    zip_path = base / "source-artifact.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("dev-image/scanner-edge-image.tar", tar_bytes)
        z.writestr("dev-image/scanner-edge-image.tar.sha256",
                   f"{tar_sha}  dev-image/scanner-edge-image.tar\n")
    real_zip_sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()

    return {
        "manifest": manifest, "inspect": inspect, "zip": zip_path,
        "manifest_sha": real_manifest_sha, "zip_sha": real_zip_sha, "tar_sha": tar_sha,
    }


def run(fx: dict, evidence: Path, **over) -> tuple[int, str, str]:
    a = {
        "--inspect-json": str(fx["inspect"]),
        "--artifact-zip": str(fx["zip"]),
        "--source-manifest": str(fx["manifest"]),
        "--evidence": str(evidence),
        "--repository": REPO_IMG,
        "--tag": TAG,
        "--resolved-digest": DIGEST,
        "--expected-digest": DIGEST,
        "--expected-revision": REVISION,
        "--expected-manifest-sha256": fx["manifest_sha"],
        "--expected-artifact-sha256": fx["zip_sha"],
        "--source-run": RUN_ID,
        "--source-artifact": ARTIFACT_ID,
        "--verification-run-url": RUN_URL,
    }
    a.update(over)
    argv = [sys.executable, str(SCRIPT)]
    for k, v in a.items():
        argv += [k, v]
    p = subprocess.run(argv, capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def seed_fail_evidence(path: Path) -> None:
    """Reproduce the workflow's 'Initialize fail-closed evidence' step."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": "backbond-scanner-readonly-image-verification@1",
        "status": "FAIL",
        "tag_reference": f"{REPO_IMG}:{TAG}",
        "expected_digest": DIGEST,
        "source_run": RUN_ID,
        "source_artifact": ARTIFACT_ID,
        "verification_run_url": RUN_URL,
    }, indent=2) + "\n")


def main() -> int:
    fx = build_fixtures(WORK)
    results = []

    # --- Case 0: happy path through the REAL entrypoint (never covered by producer tests)
    ev = WORK / "ev_pass/verification.json"
    seed_fail_evidence(ev)
    rc, out, err = run(fx, ev)
    doc = json.loads(ev.read_text())
    ok = rc == 0 and doc["status"] == "PASS"
    results.append(("C0 happy path -> exit0 + PASS evidence", ok, f"rc={rc} status={doc['status']}"))
    happy_doc = doc

    # --- Fail-closed matrix. Each must: exit 1 AND leave the seeded FAIL evidence intact.
    cases = [
        ("C1 resolved digest != expected", {"--resolved-digest": "sha256:" + "0" * 64},
         "tag resolved to an unexpected digest"),
        ("C2 frozen manifest sha mismatch", {"--expected-manifest-sha256": MANIFEST_SHA},
         "frozen source release-manifest SHA-256 mismatch"),
        ("C3 OCI revision label mismatch", {"--expected-revision": "f" * 40},
         "OCI label mismatch: org.opencontainers.image.revision"),
        ("C4 artifact archive sha mismatch", {"--expected-artifact-sha256": "0" * 64},
         "Actions artifact archive SHA-256 mismatch"),
    ]
    for name, over, want in cases:
        ev = WORK / f"ev_{name.split()[0]}/verification.json"
        seed_fail_evidence(ev)
        rc, out, err = run(fx, ev, **over)
        doc = json.loads(ev.read_text())
        ok = rc == 1 and doc["status"] == "FAIL" and want in err
        results.append((name, ok, f"rc={rc} status={doc['status']} err={err[:90]!r}"))

    # --- C5: inspect payload shape guard (multi-image / empty) - untested by producer
    for label, payload in (("empty list", []), ("two images", [{"Config": {}}, {"Config": {}}])):
        bad = WORK / f"inspect_{label.replace(' ', '_')}.json"
        bad.write_text(json.dumps(payload))
        ev = WORK / f"ev_shape_{label.replace(' ', '_')}/verification.json"
        seed_fail_evidence(ev)
        rc, out, err = run(fx, ev, **{"--inspect-json": str(bad)})
        doc = json.loads(ev.read_text())
        ok = rc == 1 and doc["status"] == "FAIL"
        results.append((f"C5 inspect payload {label} rejected", ok, f"rc={rc} err={err[:90]!r}"))

    # --- C6: artifact with a tampered inner tar but a matching bundled checksum
    #     (self-consistent forgery). The outer zip sha pin is the only thing that stops it.
    forged = WORK / "forged.zip"
    tar2 = b"MALICIOUS scanner-edge-image payload"
    with zipfile.ZipFile(forged, "w") as z:
        z.writestr("dev-image/scanner-edge-image.tar", tar2)
        z.writestr("dev-image/scanner-edge-image.tar.sha256",
                   f"{hashlib.sha256(tar2).hexdigest()}  dev-image/scanner-edge-image.tar\n")
    ev = WORK / "ev_forge/verification.json"
    seed_fail_evidence(ev)
    rc, out, err = run(fx, ev, **{"--artifact-zip": str(forged)})
    doc = json.loads(ev.read_text())
    ok = rc == 1 and doc["status"] == "FAIL"
    results.append(("C6 self-consistent forged artifact rejected", ok, f"rc={rc} err={err[:90]!r}"))

    # --- C7: does the PASS evidence bind the image tar to the verified image digest?
    tar_in_ev = json.dumps(happy_doc)
    binds = happy_doc["actions_artifact"].get("image_tar_sha256") is not None
    results.append(("C7 evidence records image_tar_sha256", binds,
                    f"tar_sha={happy_doc['actions_artifact'].get('image_tar_sha256','')[:16]}"))

    print("\n=== BEAAA-21513 fail-closed matrix (real CLI entrypoint) ===")
    bad = 0
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}\n        {detail}")
        bad += 0 if ok else 1
    print(f"\n{len(results) - bad}/{len(results)} checks passed")
    print("\n=== PASS-path evidence document ===")
    print(tar_in_ev[:900])
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
