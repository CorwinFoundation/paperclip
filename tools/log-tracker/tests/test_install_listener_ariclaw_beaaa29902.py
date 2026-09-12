import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

from log_tracker.appliance_ingest_server import enroll_from_file

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
    assert stat.S_IMODE((tmp_path / "etc/backbond").stat().st_mode) == 0o750
    unit = first["etc/systemd/system/backbond-appliance-ingest.service"][0].decode()
    migrations = [part.split("/")[-1] for part in unit.split() if part.startswith("/opt/backbond/migrations/")]
    assert migrations == sorted(p.name for p in (REPO / "tools/log-tracker/migrations").glob("*.sql"))
    config = first["etc/backbond/config.yaml"][0].decode()
    assert 'name: "codex_rollout"' in config
    assert 'url: "http://127.0.0.1:8787/ingest"' in config
    assert 'tenant_id: "backbond"' in config
    assert 'source_id: "paperclip-codex-rollouts"' in config

    enrollment = json.loads(first["etc/backbond/appliance-enrollment.json"][0])
    assert enrollment[0]["tenant_id"] == "backbond"
    assert enrollment[0]["source_id"] == "paperclip-codex-rollouts"


def test_root_install_sets_runtime_ownership_and_enroll_pythonpath():
    script = SCRIPT.read_text()
    assert 'install -d -o root -g backbond -m 0750 "$ETC"' in script
    assert 'PYTHONPATH="$OPT/appliance-0.1.0" BACKBOND_CONFIG=' in script
    assert 'chown backbond:backbond "$VAR/appliance.key"' in script
    assert 'chmod 0600 "$VAR/appliance.key"' in script


def test_receiver_enrolls_every_entry_in_a_list(tmp_path):
    path = tmp_path / "appliance-enrollment.json"
    entries = [
        {"appliance_id": "ariclaw-1", "tenant_id": "backbond", "source_id": "rollouts",
         "public_key_pem": "key-1", "token_env": "TOKEN_ONE", "binding_id": "binding-1",
         "binding_effective_from": "1970-01-01T00:00:00Z"},
        {"appliance_id": "ariclaw-api-1", "tenant_id": "backbond", "source_id": "api",
         "public_key_pem": "key-2", "token_env": "TOKEN_TWO", "binding_id": "binding-2",
         "binding_effective_from": "1970-01-01T00:00:00Z"},
    ]
    path.write_text(json.dumps(entries))

    calls = []

    class Store:
        def enroll(self, **kwargs):
            calls.append(kwargs)

    enroll_from_file(Store(), path, environ={"TOKEN_ONE": "one", "TOKEN_TWO": "two"})
    assert [call["appliance_id"] for call in calls] == ["ariclaw-1", "ariclaw-api-1"]
    assert [call["token"] for call in calls] == ["one", "two"]


def test_install_preserves_other_enrolled_producers(tmp_path):
    enrolled = tmp_path / "etc/backbond/appliance-enrollment.json"
    enrolled.parent.mkdir(parents=True)
    enrolled.write_text(json.dumps({"appliance_id": "ariclaw-paperclip-api-1", "token_env": "OTHER"}))
    invoke(tmp_path)
    items = json.loads(enrolled.read_text())
    assert [item["appliance_id"] for item in items] == ["ariclaw-paperclip-api-1", "ariclaw-1"]
