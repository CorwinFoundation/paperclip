import hashlib
import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "ops/paperclip/numbat/install-listener-ariclaw.sh"


def tree(root):
    return {str(p.relative_to(root)): (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
            for p in root.rglob("*") if p.is_file()}


def invoke(root, *, dry=False):
    env = {**os.environ, "BACKBOND_INSTALL_ROOT": str(root),
           "BACKBOND_INSTALL_SKIP_RUNTIME": "1", "BACKBOND_SHIP_TOKEN": "test-token"}
    if dry: env["DRY_RUN"] = "1"
    return subprocess.run(["bash", str(SCRIPT)], cwd=REPO, env=env,
                          text=True, capture_output=True, check=True)


def test_dry_run_verifies_sealed_zip_without_mutation(tmp_path):
    before = tree(tmp_path)
    result = invoke(tmp_path, dry=True)
    assert tree(tmp_path) == before
    assert "verified sealed archive" in result.stdout
    assert "sudo BACKBOND_SHIP_TOKEN=<token>" in result.stdout


def test_two_sandbox_installs_converge(tmp_path):
    invoke(tmp_path)
    first = tree(tmp_path)
    invoke(tmp_path)
    assert tree(tmp_path) == first
    assert first["etc/backbond/appliance-ingest.env"][1] == 0o600
    assert first["etc/backbond/appliance-enrollment.json"][1] == 0o640
    unit = first["etc/systemd/system/backbond-appliance-ingest.service"][0].decode()
    migrations = [part.split("/")[-1] for part in unit.split() if part.startswith("/opt/backbond/migrations/")]
    assert migrations == sorted(p.name for p in (REPO / "tools/log-tracker/migrations").glob("*.sql"))
    config = first["etc/backbond/config.yaml"][0].decode()
    assert 'name: "codex_rollout"' in config
    assert 'url: "http://127.0.0.1:8787/ingest"' in config
