"""PyInstaller entry point for the design-partner binary (BEAAA-567).

Sequence:

1. Resolve baked-in dist config (partner_id, build_id, endpoint, license
   key, embedded public key). Missing config → refuse.
2. EULA acceptance: on first invocation, show the embedded EULA and
   require accept-click; record the acceptance under a per-user state
   path. Subsequent invocations check the record.
3. License gate (``infra.dist.license_gate.check_license``): TLS callback
   to the IFA endpoint, verify Ed25519 attestation. Any failure → exit
   ``GATE_FAIL_EXIT_CODE`` (4) without scoring.
4. Delegate to ``bin/are-scan``'s ``main()`` unchanged. Same argparse,
   same exit codes (0/1/2/3), same scoring math, same byte-identical
   output.

The gate runs *before* argparse so a binary configured with an invalid
license cannot be coerced into producing partial output by malformed
arguments. The EULA accept-record path is also frozen at build time to
prevent a partner from sharing a binary plus an "already accepted" state
file (the record carries the binary_build_id).
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Stand-up the repo root on sys.path the same way ``bin/are-scan`` does
# when run from a source checkout. When frozen by PyInstaller, the repo
# is unpacked under ``sys._MEIPASS``; both branches resolve identically.
if hasattr(sys, "_MEIPASS"):
    REPO = Path(sys._MEIPASS)
else:
    REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from infra.dist.license_gate import GATE_FAIL_EXIT_CODE, check_license  # noqa: E402


def _dist_config():
    from infra.dist import _dist_config as cfg  # type: ignore[attr-defined]

    return cfg


def _eula_state_path(build_id: str) -> Path:
    """Per-user state file recording EULA acceptance for this build.

    Path is XDG-style on linux and macOS so it persists across upgrades
    of the binary for the same partner but resets when a new build is
    issued (the file is keyed on ``binary_build_id``).
    """
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "ifa-scanner" / f"eula-{build_id}.json"


def _ensure_eula_accepted(*, interactive: bool) -> bool:
    cfg = _dist_config()
    eula_text = (REPO / "infra" / "dist" / "eula.txt").read_text(encoding="utf-8")
    state = _eula_state_path(cfg.BINARY_BUILD_ID)
    if state.exists():
        return True

    if not interactive or not sys.stdin.isatty():
        sys.stderr.write(
            "are-scan: EULA has not been accepted on this host. "
            "Run the binary once interactively to accept the EULA before "
            "scheduling automated scans.\n"
        )
        return False

    sys.stdout.write(eula_text)
    sys.stdout.write(
        "\nType 'I ACCEPT' (case sensitive) to record acceptance, or anything else to abort.\n> "
    )
    sys.stdout.flush()
    answer = sys.stdin.readline().strip()
    if answer != "I ACCEPT":
        sys.stderr.write("are-scan: EULA not accepted; refusing to score.\n")
        return False

    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps(
            {
                "binary_build_id": cfg.BINARY_BUILD_ID,
                "partner_id": cfg.PARTNER_ID,
                "accepted_at": datetime.now(tz=timezone.utc).isoformat(),
            },
            sort_keys=True,
        )
        + "\n"
    )
    state.chmod(0o600)
    return True


def main(argv: list[str] | None = None) -> int:
    # Step 1+2 — EULA. Done before any network call so a partner without
    # a prior accept record on a new machine sees the EULA, not an
    # unrelated network error.
    interactive = argv is None
    if not _ensure_eula_accepted(interactive=interactive):
        return GATE_FAIL_EXIT_CODE

    # Step 3 — license gate. One scan_id per binary invocation, regardless
    # of how many scans the underlying CLI ends up running (current CLI
    # runs a single scan per process; future multi-scan modes would need
    # one validate call per scan).
    scan_id = str(uuid.uuid4())
    outcome = check_license(scan_id=scan_id)
    if not outcome.ok:
        sys.stderr.write(
            f"are-scan: license gate refused this invocation: {outcome.reason}\n"
            "No scan was performed. Contact your IFA partner manager if the "
            "issue persists.\n"
        )
        return outcome.exit_code

    # Step 4 — delegate to bin/are-scan. We import it as a module rather
    # than execv'ing so the process boundary stays inside the gate. The
    # CLI's `main()` does its own argparse + exit-code mapping; we return
    # whatever it returns.
    #
    # Use SourceFileLoader explicitly because `bin/are-scan` has no `.py`
    # extension; the default spec_from_file_location would return None
    # without a loader hint, which our error path would catch as a bundle
    # defect. The explicit loader is the correct fix — the file is valid
    # Python regardless of its extension.
    from importlib.machinery import SourceFileLoader
    from importlib.util import module_from_spec, spec_from_loader

    cli_path = REPO / "bin" / "are-scan"
    if not cli_path.exists():
        sys.stderr.write("are-scan: internal error — cannot locate CLI module\n")
        return 3
    loader = SourceFileLoader("are_scan_cli", str(cli_path))
    spec = spec_from_loader("are_scan_cli", loader)
    if spec is None:
        sys.stderr.write("are-scan: internal error — cannot locate CLI module\n")
        return 3
    module = module_from_spec(spec)
    loader.exec_module(module)
    return int(module.main(argv))


if __name__ == "__main__":
    sys.exit(main())
