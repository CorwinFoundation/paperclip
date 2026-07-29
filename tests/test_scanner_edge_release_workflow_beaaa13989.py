from pathlib import Path

# BEAAA-18854 reconciliation: `test_current_release_workflow_emits_offline_verifiable_sigstore_bundle`
# was retired here. It asserted the cosign/sigstore/release-candidate ceremony inside
# `.github/workflows/scanner-edge-image.yml`. Canonical master commit a1069cc
# ("Stop legacy deploy ceremony in development mode", authored by CorwinFoundation) converted
# that workflow into the dev-image builder and SUSPENDED the production release ceremony; the
# new dev-mode contract is now pinned by tests/test_scanner_edge_dev_mode_artifact.py
# (test_suspended_production_ceremony_is_absent). The deploy-side systemd credential-handoff
# controls below survive because infra/live-edge/* still exist and remain valid.

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "infra" / "live-edge" / "backbond-deploy-agent.service"
LAUNCHER = ROOT / "infra" / "live-edge" / "run-backbond-deploy-agent"


def test_systemd_unit_uses_run_only_credential_handoff() -> None:
    unit = UNIT.read_text(encoding="utf-8")
    launcher = LAUNCHER.read_text(encoding="utf-8")
    assert "LoadCredentialEncrypted=deploy-token:" in unit
    assert "LoadCredentialEncrypted=paperclip-api-key:" in unit
    assert "LoadCredential=deploy-authorization-id:/run/" in unit
    assert "LoadCredentialEncrypted=acceptance-admin-token:" in unit
    assert "CREDENTIALS_DIRECTORY/deploy-token" in launcher
    assert "BACKBOND_DEPLOY_TOKEN=" not in unit
    assert "PAPERCLIP_API_KEY=" not in unit
    assert "BACKBOND_DEPLOY_AUTHORIZATION_ID=" not in unit
    assert "?authorizationId={authorization_id}" in unit
    assert "{token}" not in unit
    assert "NoNewPrivileges=yes" in unit
    assert "ReadWritePaths=/var/lib/backbond-deploy-agent /run /etc/backbond" in unit
    assert "UMask=0077" in unit
    assert "StateDirectory=backbond-deploy-agent" in unit
    assert "StateDirectoryMode=0700" in unit
    assert "BACKBOND_SCAN_LOG_DB=/var/lib/are-scanner/scan_log.sqlite3" in unit


def test_launcher_never_places_secret_in_argv() -> None:
    launcher = LAUNCHER.read_text(encoding="utf-8")
    exec_line = launcher.split("exec ", 1)[1]
    assert "BACKBOND_DEPLOY_TOKEN" not in exec_line
    assert "--token" not in exec_line
    assert "export" not in launcher
    assert "--deploy-token-credential" in exec_line
