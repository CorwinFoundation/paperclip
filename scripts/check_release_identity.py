from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.backbond_deploy_agent import DeployRejected, validate_release_identity


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _configured_image(state_dir: Path) -> str:
    for line in _read_text(state_dir / "scanner-edge.env").splitlines():
        if line.startswith("SCANNER_EDGE_IMAGE="):
            return line.split("=", 1)[1].strip()
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check BackBond Scanner release identity ledger consistency")
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/backbond-deploy-agent"))
    parser.add_argument("--identity", type=Path)
    parser.add_argument("--running-container-revision", required=True)
    parser.add_argument("--internal-version-sha", required=True)
    args = parser.parse_args(argv)

    identity_path = args.identity or args.state_dir / "release_identity.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    if not isinstance(identity, dict):
        print("release identity mismatch: identity must be a JSON object", file=sys.stderr)
        return 2
    expected = dict(identity)
    observed = dict(identity)
    observed["configured_image"] = _configured_image(args.state_dir)
    observed["running_container_revision"] = args.running_container_revision
    observed["internal_version_sha"] = args.internal_version_sha
    observed["current_version"] = _read_text(args.state_dir / "current_version")
    observed["last_activated_sequence"] = int(_read_text(args.state_dir / "last_activated_sequence"))
    try:
        for field in (
            "configured_image",
            "running_container_revision",
            "internal_version_sha",
            "current_version",
            "last_activated_sequence",
        ):
            if observed.get(field) != expected.get(field):
                raise DeployRejected(f"{field} observed value does not match release identity ledger")
        validate_release_identity(observed)
    except DeployRejected as exc:
        print(f"release identity mismatch: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({field: observed[field] for field in sorted(observed)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
