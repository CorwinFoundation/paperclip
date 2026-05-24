# PyInstaller spec for the design-partner ARE Scanner binary (BEAAA-567).
#
# Invoked by `infra/dist/build.py` with deterministic env vars set
# (PYTHONHASHSEED=0, SOURCE_DATE_EPOCH=<git ts>, TZ=UTC). Do not run
# this spec directly — the wrapping orchestrator is what stamps the
# build manifest and ensures the staged corpus + dist config are in
# place.
#
# Determinism flags this spec implies:
#
# * `--onefile` via EXE(bundle into a single file). Required by ADR-0011
#   §3.2.
# * `upx=False` (on EXE) — UPX produces non-deterministic compression in
#   some modes and changes binary contents per machine; we never use it.
#   This MUST live on the EXE() call here, not as a `--noupx` CLI flag in
#   build.py: PyInstaller rejects `--noupx` alongside a `.spec` entrypoint
#   (BEAAA-726).
# * `bootloader_ignore_signals=False`, `strip=True` on Linux.
# * `console=True` — the binary is a CLI; no GUI hooks.
#
# Hidden imports cover modules that PyInstaller's static analyzer misses
# because they are imported via importlib or lazy adapter loading inside
# `harness/runner.py` (anthropic, openai, google-genai, presidio).
# Pinning lives in `requirements.txt`; this list mirrors the lazy edges.
#
# `datas` bundles the rubric, the corpus stage (rendered by build.py),
# the EULA, and the rendered _dist_config.py. The dist config is the
# only file *generated* per build — every other entry is a verbatim
# copy from the source tree at the build-time git sha.
from __future__ import annotations

from pathlib import Path

# When PyInstaller loads a spec file, __file__ points at the spec; SPECPATH
# is the directory PyInstaller is invoked from (set by --workpath upstream).
SPEC_DIR = Path(SPECPATH).resolve()  # type: ignore[name-defined]
REPO = SPEC_DIR.parents[1]
STAGING = Path.cwd()  # build.py sets cwd to repo root; staging passed via env

datas = [
    (str(REPO / "rubric"), "rubric"),
    (str(REPO / "infra" / "dist" / "_dist_config.py"), "infra/dist"),
    (str(REPO / "infra" / "dist" / "eula.txt"), "infra/dist"),
    (str(REPO / "infra" / "license_validator"), "infra/license_validator"),
    (str(REPO / "bin" / "are-scan"), "bin"),
    (str(REPO / "contracts"), "contracts"),
]

# Each entry is a (src, dst-relative-to-bundle-root) pair. PyInstaller
# unpacks these to sys._MEIPASS at runtime; our entry script (and
# bin/are-scan) treat sys._MEIPASS as repo root when present.

hiddenimports = [
    # Scanner runtime packages — dynamically loaded via bin/are-scan's
    # sys.path manipulation + importlib chain; PyInstaller static analysis
    # does not traverse SourceFileLoader into bin/are-scan's imports.
    "harness",
    "harness.cli_runner",
    "harness.golden_set",
    "harness.runner",
    "harness.result_types",
    "rubric",
    "rubric.scoring",
    "rubric.bands",
    "rubric.applicability_policy",
    "scanners",
    "scanners.inspect_runner",
    "scanners.promptfoo_runner",
    "scanners.snapshot_hash",
    # SDKs lazy-imported by harness/runner.py adapters
    "anthropic",
    "openai",
    "google.genai",
    # Presidio PII scrubber (transitive: spaCy en_core_web_sm)
    "presidio_analyzer",
    "presidio_anonymizer",
    # Inspect AI dual-runner
    "inspect_ai",
    # Cryptography backend used by the BEAAA-569 attestation verifier
    "cryptography.hazmat.bindings._rust",
]

excludes = [
    # PyInstaller pulls these in by default — we do not need them and
    # they add ~10–30 MiB each. None are imported by any code path in
    # this binary's reachable graph (verified in S3 pinning child).
    "tkinter",
    "matplotlib",
    "PIL",
    "pytest",
    "IPython",
]


a = Analysis(  # noqa: F821 — PyInstaller injects Analysis into spec namespace
    [str(REPO / "infra" / "dist" / "are_scan_dist.py")],
    pathex=[str(REPO)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="are-scan-design-partner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,  # never UPX — non-deterministic
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
