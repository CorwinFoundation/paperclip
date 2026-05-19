"""Deterministic build orchestrator for the design-partner binary.

Pipeline:

1. Resolve inputs (partner_id, license_endpoint_url, license_key,
   public_key_pem, scanner source git sha). Fail fast if any are missing.
2. Derive the per-partner ``WATERMARK_SALT`` as ``sha256(partner_id ||
   master_salt)[:16]`` so two builds for the same partner with the same
   master salt produce the same watermark (determinism gate 1).
3. Stage a build root:

   * Render ``_dist_config.py`` from the template.
   * Copy the canonical corpus into ``build/<build_id>/corpus/`` and
     write the watermark sidecar (``.watermark``) into each leaf. The
     watermark does **not** mix into the canonical
     ``corpus_snapshot_hash`` (ADR-0011 §3.5 / Supreme Law).
   * Copy the rubric + EULA + bin/are-scan + harness/ + scanners/ +
     license_validator client subset into the staging root.

4. Run PyInstaller with deterministic flags::

       PYTHONHASHSEED=0 \\
       SOURCE_DATE_EPOCH=<git commit ts> \\
       pyinstaller --clean --noconfirm --noupx --onefile \\
                   --strip \\
                   infra/dist/are_scan.spec

5. Emit the build manifest (``manifest.json``) recording: builder OS,
   interpreter sha256, pip freeze, git sha, ``SOURCE_DATE_EPOCH``,
   partner_id, build_id, binary sha256, canonical
   ``corpus_snapshot_hash``, watermark salt (kept server-side, NOT in
   the partner-facing manifest).
6. Optional verification step: re-run pipeline against a checkout in a
   fresh tmp dir and assert byte-identical binary sha256 (determinism
   gate 1 check; S1 close-out gate).

This module is a CLI; see ``__main__`` block at the bottom.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DIST_DIR = REPO / "infra" / "dist"
BUILD_ROOT = REPO / "build"
PYINSTALLER_VERSION = "6.11.1"  # pinned; matches infra/dist/README.md


@dataclass(frozen=True)
class BuildInputs:
    partner_id: str
    license_endpoint_url: str
    license_key: str
    public_key_pem: bytes
    master_salt: str
    git_sha: str
    git_commit_ts: int
    allow_insecure_endpoint: bool


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _derive_watermark_salt(partner_id: str, master_salt: str) -> str:
    payload = f"{partner_id}\x00{master_salt}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _derive_build_id(partner_id: str, git_sha: str, target_os: str) -> str:
    today = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
    short = git_sha[:8]
    return f"build-{today}-{target_os}-{short}-{partner_id}"


def _read_git_sha() -> str:
    return subprocess.check_output(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True
    ).strip()


def _read_git_commit_ts() -> int:
    return int(
        subprocess.check_output(
            ["git", "-C", str(REPO), "log", "-1", "--format=%ct"], text=True
        ).strip()
    )


def _validate_endpoint(url: str, allow_insecure: bool) -> None:
    if not url:
        raise SystemExit("build.py: --license-endpoint-url is required")
    if url.startswith("http://") and not allow_insecure:
        raise SystemExit(
            "build.py: refusing to build a partner-bound binary with an "
            "http:// endpoint. Pass --allow-insecure-endpoint to produce "
            "a 'debug' binary intended for the BEAAA-569 stub loop only."
        )


def _render_dist_config(*, inputs: BuildInputs, build_id: str, watermark: str) -> str:
    template = (DIST_DIR / "_dist_config.py.template").read_text(encoding="utf-8")
    pem_inline = inputs.public_key_pem.decode("utf-8").strip()
    return (
        template.replace("{{PARTNER_ID}}", inputs.partner_id)
        .replace("{{BINARY_BUILD_ID}}", build_id)
        .replace("{{LICENSE_ENDPOINT_URL}}", inputs.license_endpoint_url)
        .replace("{{LICENSE_KEY}}", inputs.license_key)
        .replace("{{WATERMARK_SALT}}", watermark)
        .replace("{{PUBLIC_KEY_PEM}}", pem_inline)
    )


def _canonical_corpus_hash(corpus_dir: Path) -> str:
    """Hash the canonical (un-watermarked) corpus tree.

    Walks the corpus in sorted order and feeds ``relpath\x00sha256(content)\n``
    for each leaf into a rolling SHA-256. Must equal the value stamped into
    score outputs across Path 1 / Path 2 / Path 3 (ADR-0011 §3.5).
    """
    h = hashlib.sha256()
    for path in sorted(p for p in corpus_dir.rglob("*") if p.is_file()):
        if path.name.endswith(".watermark"):
            continue  # side-channel; never in canonical hash
        rel = path.relative_to(corpus_dir).as_posix().encode("utf-8")
        h.update(rel + b"\x00")
        h.update(hashlib.sha256(path.read_bytes()).digest())
        h.update(b"\n")
    return h.hexdigest()


def _stage_corpus_with_watermark(*, source: Path, dest: Path, watermark: str) -> str:
    """Copy corpus to ``dest`` and drop one ``.watermark`` sidecar per leaf.

    Returns the canonical (watermark-free) hash of the staged tree.
    """
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)
    for leaf in dest.rglob("*"):
        if leaf.is_file() and not leaf.name.endswith(".watermark"):
            (leaf.parent / f"{leaf.name}.watermark").write_text(watermark + "\n")
    return _canonical_corpus_hash(dest)


def build(inputs: BuildInputs, *, target_os: str, build_root: Path) -> dict:
    git_sha = inputs.git_sha
    build_id = _derive_build_id(inputs.partner_id, git_sha, target_os)
    watermark = _derive_watermark_salt(inputs.partner_id, inputs.master_salt)
    staging = build_root / build_id
    staging.mkdir(parents=True, exist_ok=True)

    # 1. Stage corpus with watermark sidecars.
    corpus_src = REPO / "rubric" / "probe_mappings"  # placeholder: actual corpus path
    canonical_hash = _stage_corpus_with_watermark(
        source=corpus_src, dest=staging / "corpus", watermark=watermark
    )

    # 2. Render the dist config.
    rendered = _render_dist_config(
        inputs=inputs, build_id=build_id, watermark=watermark
    )
    dist_cfg_path = DIST_DIR / "_dist_config.py"
    dist_cfg_path.write_text(rendered, encoding="utf-8")

    # 3. PyInstaller invocation. Determinism flags:
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = "0"
    env["SOURCE_DATE_EPOCH"] = str(inputs.git_commit_ts)
    env["TZ"] = "UTC"

    spec_path = DIST_DIR / "are_scan.spec"
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--noupx",
            "--distpath",
            str(staging / "dist"),
            "--workpath",
            str(staging / "work"),
            str(spec_path),
        ],
        env=env,
        cwd=REPO,
    )

    # 4. Locate the binary + compute sha256.
    candidates = list((staging / "dist").glob("are-scan-*"))
    if len(candidates) != 1:
        raise SystemExit(
            f"build.py: expected exactly one binary in {staging/'dist'}, found {candidates}"
        )
    binary_path = candidates[0]
    binary_sha = _sha256_bytes(binary_path.read_bytes())

    # 5. Manifest.
    manifest = {
        "schema": "infra/dist/manifest_schema.json#v1",
        "partner_id": inputs.partner_id,
        "binary_build_id": build_id,
        "target_os": target_os,
        "git_sha": git_sha,
        "source_date_epoch": inputs.git_commit_ts,
        "pyinstaller_version": PYINSTALLER_VERSION,
        "python_version": sys.version.split()[0],
        "binary_sha256": binary_sha,
        "binary_size_bytes": binary_path.stat().st_size,
        "corpus_snapshot_hash": canonical_hash,
        "license_endpoint_url": inputs.license_endpoint_url,
        "license_endpoint_insecure_allowed": inputs.allow_insecure_endpoint,
        "built_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )

    # Cleanup the rendered dist config so it does not linger in the repo.
    dist_cfg_path.unlink(missing_ok=True)

    return manifest


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="infra.dist.build")
    p.add_argument("--partner-id", required=True)
    p.add_argument("--license-endpoint-url", required=True)
    p.add_argument("--license-key", required=True)
    p.add_argument("--public-key-pem", required=True, type=Path)
    p.add_argument("--master-salt", required=True, help="Operator-held salt; rotated separately.")
    p.add_argument("--target-os", default="x86_64-linux-gnu")
    p.add_argument("--build-root", default=str(BUILD_ROOT), type=Path)
    p.add_argument(
        "--allow-insecure-endpoint",
        action="store_true",
        help="Allow an http:// endpoint (for the BEAAA-569 stub loop only).",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    _validate_endpoint(args.license_endpoint_url, args.allow_insecure_endpoint)
    inputs = BuildInputs(
        partner_id=args.partner_id,
        license_endpoint_url=args.license_endpoint_url,
        license_key=args.license_key,
        public_key_pem=args.public_key_pem.read_bytes(),
        master_salt=args.master_salt,
        git_sha=_read_git_sha(),
        git_commit_ts=_read_git_commit_ts(),
        allow_insecure_endpoint=args.allow_insecure_endpoint,
    )
    manifest = build(inputs, target_os=args.target_os, build_root=args.build_root)
    sys.stdout.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
