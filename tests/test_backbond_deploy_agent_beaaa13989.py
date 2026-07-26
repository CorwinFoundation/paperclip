from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import subprocess
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

from scripts import backbond_deploy_agent as agent
from scripts import check_release_identity


COMMIT_SHA = "a" * 40


class FakeActivator:
    def __init__(self, acceptance_result: dict | None = None, acceptance_passed: bool | None = None) -> None:
        if acceptance_result is None:
            acceptance_result = _acceptance_result(passed=True)
        if acceptance_passed is not None:
            acceptance_result = dict(acceptance_result)
            acceptance_result["passed"] = acceptance_passed
        self.acceptance_result = acceptance_result
        self.activated = False
        self.rolled_back = False
        self.identities: list[dict] = []
        self.identity_overrides: dict = {}

    def snapshot_current(self) -> str:
        return "previous-sha"

    def activate_tarball(self, tarball_path: Path, candidate: agent.Candidate) -> str:
        self.activated = True
        return candidate.commit_sha

    def run_acceptance(self, activated_version: str) -> dict:
        result = dict(self.acceptance_result)
        result["version"] = activated_version
        return result

    def rollback(self, previous_version: str) -> str:
        self.rolled_back = True
        assert previous_version == "previous-sha"
        return "rolled_back"

    def commit_release_identity(
        self,
        *,
        candidate: agent.Candidate,
        activated_version: str,
        terminal_receipt_sha256: str,
        status: str,
    ) -> dict:
        commit_sha = COMMIT_SHA if status == "activated" else str(activated_version)
        image_digest = candidate.image_digest
        identity = {
            "schema_version": agent.RELEASE_IDENTITY_SCHEMA,
            "environment": "production",
            "host": "srv1749248",
            "candidate_id": candidate.candidate_id,
            "commit_sha": commit_sha,
            "image_digest": image_digest,
            "configured_image": image_digest,
            "running_container_revision": commit_sha,
            "internal_version_sha": commit_sha,
            "current_version": commit_sha,
            "last_activated_sequence": candidate.sequence,
            "terminal_receipt_sha256": terminal_receipt_sha256,
            "status": status,
            "recorded_at": "2026-07-13T20:00:00Z",
        }
        identity.update(self.identity_overrides)
        agent.validate_release_identity(identity)
        self.identities.append(identity)
        return identity


def _write_tarball(tmp_path: Path, body: bytes = b"image") -> Path:
    path = tmp_path / "candidate.tar"
    path.write_bytes(body)
    return path


def _write_docker_archive(tmp_path: Path, *, commit_sha: str = COMMIT_SHA, layer: bytes = b"layer") -> tuple[Path, str]:
    layer_digest = hashlib.sha256(layer).hexdigest()
    config = {
        "architecture": "amd64",
        "os": "linux",
        "config": {"Labels": {"org.opencontainers.image.revision": commit_sha}},
        "rootfs": {"type": "layers", "diff_ids": [f"sha256:{layer_digest}"]},
    }
    config_bytes = json.dumps(config, separators=(",", ":")).encode("utf-8")
    config_digest = hashlib.sha256(config_bytes).hexdigest()
    manifest_bytes = json.dumps(
        [{
            "Config": f"blobs/sha256/{config_digest}",
            "RepoTags": [f"backbond-scanner-live:{commit_sha}"],
            "Layers": [f"blobs/sha256/{layer_digest}"],
        }],
        separators=(",", ":"),
    ).encode("utf-8")
    archive_path = tmp_path / "docker-image.tar"
    with tarfile.open(archive_path, "w") as archive:
        for name, body in (
            ("manifest.json", manifest_bytes),
            (f"blobs/sha256/{config_digest}", config_bytes),
            (f"blobs/sha256/{layer_digest}", layer),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    return archive_path, f"sha256:{config_digest}"


def _loaded_image(*, image_id: str, commit_sha: str = COMMIT_SHA, layer: bytes = b"layer") -> dict:
    return {
        "Id": image_id,
        "Os": "linux",
        "Architecture": "amd64",
        "RootFS": {"Layers": [f"sha256:{hashlib.sha256(layer).hexdigest()}"]},
        "Config": {"Labels": {"org.opencontainers.image.revision": commit_sha}},
    }


def _acceptance_result(*, passed: bool = True, terminal_status: str = "complete") -> dict:
    return {
        "passed": passed,
        "terminal_status": terminal_status,
        "scan_log_pins": {
            "forensic_bundle_uri": "scanner://evidence/cand-1/bundle.json",
            "snapshot_hash": "sha256:" + "1" * 64,
            "probe_set_hash": "sha256:" + "2" * 64,
        },
        "customer_envelope": {
            "scan_id": "scan-cand-1",
            "scanner_version": "1.2.0",
            "corpus_version": "2026-07-14",
        },
        "components": [
            {"name": "health", "passed": True},
            {"name": "scan_log_pins", "passed": True},
            {"name": "customer_envelope", "passed": True},
        ],
    }


def _candidate(tmp_path: Path, **overrides) -> agent.Candidate:
    tarball = _write_tarball(tmp_path)
    signature = tmp_path / "signature.bundle"
    signature.write_bytes(b"sigstore-bundle")
    payload = {
        "candidate_id": "cand-1",
        "commit_sha": COMMIT_SHA,
        "image_digest": "ghcr.io/backbond/scanner@sha256:" + "a" * 64,
        "tarball_url": f"file://{tarball}",
        "tarball_sha256": hashlib.sha256(tarball.read_bytes()).hexdigest(),
        "sequence": 7,
        "expires_at": "2026-07-13T21:00:00Z",
        "environment": "production",
        "host": "srv1749248",
        "signature_ref": f"file://{signature}",
        "signature_sha256": hashlib.sha256(signature.read_bytes()).hexdigest(),
        "approval_interaction_id": "3fb03d77-c910-47ca-af6d-9562259f8001",
        "deploy_token_scope": "scanner:deploy:production:srv1749248:cand-1",
    }
    payload.update(overrides)
    return agent.Candidate.from_mapping(payload)


def _lease(**overrides) -> agent.DeployLease:
    payload = {
        "lease_id": "lease-1",
        "candidate_id": "cand-1",
        "environment": "production",
        "host": "srv1749248",
        "sequence": 7,
        "expires_at": "2026-07-13T21:00:00Z",
        "token_scope": "scanner:deploy:production:srv1749248:cand-1",
        "record_receipt_path": "/api/release-deploy-authorizations/lease-1/deploy-record-receipt",
    }
    payload.update(overrides)
    return agent.DeployLease.from_mapping(payload)


def _config(tmp_path: Path) -> agent.DeployConfig:
    env_path = tmp_path / "etc" / "backbond" / "scanner-edge.env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    return agent.DeployConfig(environment="production", host="srv1749248", state_dir=tmp_path, service_env_path=env_path)


def _run(tmp_path: Path, candidate: agent.Candidate, lease: agent.DeployLease, activator: FakeActivator):
    return agent.run_once(
        candidate=candidate,
        lease=lease,
        config=_config(tmp_path),
        activator=activator,
        signature_verifier=lambda _candidate, _tarball, _bundle: True,
        now=dt.datetime(2026, 7, 13, 20, 0, tzinfo=dt.timezone.utc),
        operator_run_identity="test-run",
    )


def test_rejects_wrong_digest_before_activation(tmp_path: Path) -> None:
    activator = FakeActivator()
    record = _run(tmp_path, _candidate(tmp_path, tarball_sha256="0" * 64), _lease(), activator)

    assert record["status"] == "rejected"
    assert "sha256 does not match" in record["failure_reason"]
    assert activator.activated is False


def test_rejects_expired_token_before_activation(tmp_path: Path) -> None:
    activator = FakeActivator()
    record = _run(tmp_path, _candidate(tmp_path, expires_at="2026-07-13T19:59:59Z"), _lease(), activator)

    assert record["status"] == "rejected"
    assert "expired" in record["failure_reason"]
    assert activator.activated is False


def test_rejects_wrong_host_environment_before_activation(tmp_path: Path) -> None:
    activator = FakeActivator()
    record = _run(tmp_path, _candidate(tmp_path, host="srv-wrong"), _lease(), activator)

    assert record["status"] == "rejected"
    assert "host does not match" in record["failure_reason"]
    assert activator.activated is False


def test_rejects_signature_failure_before_activation(tmp_path: Path) -> None:
    activator = FakeActivator()
    record = agent.run_once(
        candidate=_candidate(tmp_path),
        lease=_lease(),
        config=_config(tmp_path),
        activator=activator,
        signature_verifier=lambda _candidate, _tarball, _bundle: False,
        now=dt.datetime(2026, 7, 13, 20, 0, tzinfo=dt.timezone.utc),
        operator_run_identity="test-run",
    )

    assert record["status"] == "rejected"
    assert "signature verification failed" in record["failure_reason"]
    assert activator.activated is False


def test_acceptance_failure_rolls_back_and_posts_failed_record(tmp_path: Path) -> None:
    activator = FakeActivator(acceptance_passed=False)
    posted = []

    record = agent.run_once(
        candidate=_candidate(tmp_path),
        lease=_lease(),
        config=_config(tmp_path),
        activator=activator,
        signature_verifier=lambda _candidate, _tarball, _bundle: True,
        post_record=posted.append,
        now=dt.datetime(2026, 7, 13, 20, 0, tzinfo=dt.timezone.utc),
        operator_run_identity="test-run",
    )

    assert record["status"] == "failed"
    assert record["rollback_status"] == "rolled_back"
    assert record["activated_service_version"] == "previous-sha"
    assert activator.activated is True
    assert activator.rolled_back is True
    assert posted == [record]


def test_complete_terminal_acceptance_record_contains_required_fields(tmp_path: Path) -> None:
    record = _run(
        tmp_path,
        _candidate(tmp_path),
        _lease(),
        FakeActivator(_acceptance_result(terminal_status="complete")),
    )

    assert record["status"] == "activated"
    for key in {
        "commit_sha",
        "image_digest",
        "signature_verification_result",
        "approval_interaction_id",
        "deploy_token_scope",
        "on_box_reverification_result",
        "acceptance_result",
        "rollback_status",
        "activated_service_version",
        "operator_run_identity",
    }:
        assert key in record
    assert "secret" not in record["deploy_token_scope"].lower()
    acceptance = record["acceptance_result"]
    assert acceptance["terminal_status"] == "complete"
    assert set(acceptance["scan_log_pins"]) == {
        "forensic_bundle_uri",
        "snapshot_hash",
        "probe_set_hash",
    }
    assert acceptance["scan_log_pins"]["snapshot_hash"].startswith("sha256:")
    assert acceptance["customer_envelope"]["scan_id"] == "scan-cand-1"
    assert all(component["passed"] is True for component in acceptance["components"])
    assert record["terminal_receipt_sha256"].startswith("sha256:")
    assert record["release_identity"]["configured_image"] == record["image_digest"]
    assert record["release_identity"]["running_container_revision"] == record["commit_sha"]
    assert record["release_identity"]["internal_version_sha"] == record["commit_sha"]
    assert record["release_identity"]["current_version"] == record["commit_sha"]
    assert record["release_identity"]["last_activated_sequence"] == 7


def test_release_identity_mismatch_rolls_back_without_terminal_success(tmp_path: Path) -> None:
    activator = FakeActivator()
    activator.identity_overrides = {"internal_version_sha": "wrong-sha"}
    record = _run(tmp_path, _candidate(tmp_path), _lease(), activator)

    assert record["status"] == "failed"
    assert record["rollback_status"] == "failed_closed"
    assert "release identity mismatch" in record["failure_reason"]
    assert activator.rolled_back is True
    assert not any(identity["status"] == "activated" for identity in activator.identities)


def test_partial_terminal_acceptance_can_activate_when_evidence_components_pass(tmp_path: Path) -> None:
    record = _run(
        tmp_path,
        _candidate(tmp_path),
        _lease(),
        FakeActivator(_acceptance_result(terminal_status="partial")),
    )

    assert record["status"] == "activated"
    assert record["acceptance_result"]["passed"] is True
    assert record["acceptance_result"]["terminal_status"] == "partial"
    assert all(component["passed"] is True for component in record["acceptance_result"]["components"])


def test_passed_acceptance_without_terminal_evidence_rolls_back(tmp_path: Path) -> None:
    activator = FakeActivator({"passed": True})
    record = _run(tmp_path, _candidate(tmp_path), _lease(), activator)

    assert record["status"] == "failed"
    assert record["rollback_status"] == "rolled_back"
    assert "terminal_status complete/partial" in record["failure_reason"]
    assert activator.rolled_back is True


def test_accepts_release_candidate_relay_camel_case_fields(tmp_path: Path) -> None:
    tarball = _write_tarball(tmp_path)
    (tmp_path / "signature.bundle").write_bytes(b"sigstore-bundle")
    candidate = agent.Candidate.from_mapping(
        {
            "candidateId": "cand-1",
            "commitSha": COMMIT_SHA,
            "imageDigest": "ghcr.io/backbond/scanner@sha256:" + "a" * 64,
            "stagedTarballUrl": f"file://{tarball}",
            "stagedTarballSha256": hashlib.sha256(tarball.read_bytes()).hexdigest(),
            "sequence": 7,
            "expiresAt": "2026-07-13T21:00:00Z",
            "environment": "production",
            "targetHost": "srv1749248",
            "signatureBundleRef": f"file://{tmp_path / 'signature.bundle'}",
            "signatureBundleSha256": hashlib.sha256((tmp_path / "signature.bundle").read_bytes()).hexdigest(),
            "approvalInteractionId": "3fb03d77-c910-47ca-af6d-9562259f8001",
            "deployTokenScope": "scanner:deploy:production:srv1749248:cand-1",
        }
    )
    lease = agent.DeployLease.from_mapping(
        {
            "leaseId": "lease-1",
            "releaseCandidateId": "cand-1",
            "environment": "production",
            "targetHost": "srv1749248",
            "sequence": 7,
            "expiresAt": "2026-07-13T21:00:00Z",
            "tokenScope": "scanner:deploy:production:srv1749248:cand-1",
            "deployRecordReceiptPath": "/api/release-deploy-authorizations/lease-1/deploy-record-receipt",
        }
    )

    record = _run(tmp_path, candidate, lease, FakeActivator())

    assert record["status"] == "activated"
    assert record["commit_sha"] == COMMIT_SHA


def test_accepts_staged_artifact_path_but_still_requires_tarball_sha() -> None:
    try:
        agent.Candidate.from_mapping(
            {
                "candidateId": "cand-1",
                "commitSha": COMMIT_SHA,
                "imageDigest": "ghcr.io/backbond/scanner@sha256:" + "a" * 64,
                "stagedArtifactPath": "/api/assets/asset-1/content",
                "sequence": 7,
                "expiresAt": "2026-07-13T21:00:00Z",
                "environment": "production",
                "targetHost": "srv1749248",
                "signatureBundleRef": "file:///tmp/signature.bundle",
                "signatureBundleSha256": "c" * 64,
                "approvalInteractionId": "3fb03d77-c910-47ca-af6d-9562259f8001",
                "deployTokenScope": "scanner:deploy:production:srv1749248:cand-1",
            }
        )
    except agent.DeployRejected as exc:
        assert "tarball_sha256" in str(exc)
    else:
        raise AssertionError("expected missing tarball sha rejection")


def test_paperclip_client_uses_release_candidate_paths(monkeypatch) -> None:
    calls = []
    approved_lease_fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "paperclip-approved-lease-v1.json").read_text(encoding="utf-8")
    )

    class FakeResponse:
        def __init__(self, body: dict) -> None:
            self.body = json.dumps(body).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self) -> bytes:
            return self.body

    def fake_urlopen(req: Request, timeout: int):
        calls.append((req.get_method(), req.full_url, getattr(req, "data", None), dict(req.header_items())))
        if req.get_method() == "GET":
            return FakeResponse(approved_lease_fixture)
        if req.get_method() == "POST":
            return FakeResponse({"ok": True})
        raise HTTPError(req.full_url, 405, "method", {}, None)

    monkeypatch.setattr(agent, "_urlopen_no_redirect", fake_urlopen)
    client = agent.PaperclipClient(
        "https://paperclip.example",
        "token",
        "run-1",
        deploy_authorization_id="99999999-9999-4999-8999-999999999999",
        deploy_authorization_token="pcdeploy_token/with-specials",
    )

    lease, candidate = client.poll_approved_lease("srv1749248", "production")
    assert candidate.tarball_url == "/api/release-deploy-authorizations/99999999-9999-4999-8999-999999999999/staged-artifact"
    assert candidate.signature_ref == "/api/release-deploy-authorizations/99999999-9999-4999-8999-999999999999/staged-signature-bundle"
    assert lease.record_receipt_path == "/api/release-deploy-authorizations/99999999-9999-4999-8999-999999999999/deploy-record-receipt"
    posted = client.post_deploy_record({"candidate_id": candidate.candidate_id, "lease_id": lease.lease_id})

    assert posted == {"ok": True}
    assert calls[0][0] == "GET"
    assert calls[0][1] == (
        "https://paperclip.example/api/release-candidates/approved-lease"
        "?authorizationId=99999999-9999-4999-8999-999999999999"
    )
    assert "token" not in calls[0][1].lower()
    assert calls[0][3]["X-paperclip-deploy-token"] == "pcdeploy_token/with-specials"
    assert calls[1][0] == "POST"
    assert calls[1][1] == "https://paperclip.example/api/release-candidates/deploy-records"
    posted_payload = json.loads(calls[1][2].decode("utf-8"))
    assert posted_payload["authorizationId"] == "99999999-9999-4999-8999-999999999999"
    assert "token" not in posted_payload
    assert calls[1][3]["X-paperclip-deploy-token"] == "pcdeploy_token/with-specials"
    assert posted_payload["status"] == "failed"


def test_paperclip_client_requires_deploy_authorization_for_live_routes() -> None:
    client = agent.PaperclipClient("https://paperclip.example", "api-token", "run-1")

    try:
        client.poll_approved_lease("srv1749248", "production")
    except agent.DeployRejected as exc:
        assert "authorization id/token required" in str(exc)
    else:
        raise AssertionError("expected missing deploy authorization rejection")


def test_translates_on_box_record_to_paperclip_deploy_record() -> None:
    payload = agent.to_paperclip_deploy_record(
        {
            "status": "activated",
            "commit_sha": COMMIT_SHA,
            "operator_run_identity": "run-1",
            "approval_interaction_id": "approval-1",
            "deploy_token_scope": "scanner:deploy:production:srv1749248:cand-1",
            "image_digest": "ghcr.io/backbond/scanner@sha256:" + "a" * 64,
            "signature_verification_result": "passed",
            "on_box_reverification_result": "passed",
            "acceptance_result": {"passed": True},
            "rollback_status": "not_needed",
            "activated_service_version": COMMIT_SHA,
        },
        authorization_id="auth-1",
    )

    assert payload["authorizationId"] == "auth-1"
    assert "token" not in payload
    assert payload["status"] == "succeeded"
    assert payload["commitSha"] == COMMIT_SHA
    assert payload["healthStatus"] == "passed"
    assert payload["metadata"]["deployTokenScope"] == "scanner:deploy:production:srv1749248:cand-1"


def test_rejects_token_placeholder_in_request_url() -> None:
    client = agent.PaperclipClient(
        "https://paperclip.example", "api-token", deploy_authorization_id="auth", deploy_authorization_token="secret",
        approved_lease_path_template="/approved?token={token}",
    )
    try:
        client.poll_approved_lease("srv1749248", "production")
    except agent.DeployRejected as exc:
        assert "must not be placed" in str(exc)
    else:
        raise AssertionError("expected query-token rejection")


def test_rejects_sequence_replay_before_activation(tmp_path: Path) -> None:
    activator = FakeActivator()
    record = _run(tmp_path, _candidate(tmp_path, sequence=8), _lease(sequence=7), activator)
    assert record["status"] == "rejected"
    assert "sequence" in record["failure_reason"]
    assert activator.activated is False


def test_rejects_already_activated_sequence_replay(tmp_path: Path) -> None:
    (tmp_path / "last_activated_sequence").write_text("7", encoding="utf-8")
    activator = FakeActivator()
    record = _run(tmp_path, _candidate(tmp_path, sequence=7), _lease(sequence=7), activator)
    assert record["status"] == "rejected"
    assert "replayed" in record["failure_reason"]
    assert activator.activated is False


def test_acceptance_runner_exception_guarantees_rollback(tmp_path: Path) -> None:
    class RaisingActivator(FakeActivator):
        def run_acceptance(self, activated_version: str) -> dict:
            raise FileNotFoundError("acceptance runner missing")

    activator = RaisingActivator()
    record = _run(tmp_path, _candidate(tmp_path), _lease(), activator)
    assert record["status"] == "failed"
    assert record["rollback_status"] == "rolled_back"
    assert record["activated_service_version"] == "previous-sha"
    assert activator.rolled_back is True


def test_partial_activation_exception_guarantees_rollback(tmp_path: Path) -> None:
    class PartialActivationFailure(FakeActivator):
        def activate_tarball(self, tarball_path: Path, candidate: agent.Candidate) -> str:
            self.activated = True
            raise RuntimeError("systemctl restart failed after config replacement")

    activator = PartialActivationFailure()
    record = _run(tmp_path, _candidate(tmp_path), _lease(), activator)
    assert record["status"] == "failed"
    assert record["rollback_status"] == "rolled_back"
    assert activator.rolled_back is True


def test_staged_download_is_same_origin_and_header_authenticated(monkeypatch, tmp_path: Path) -> None:
    seen = []

    class BinaryResponse:
        def __init__(self): self.buffer = io.BytesIO(b"artifact")
        def __enter__(self): return self
        def __exit__(self, *_exc): return False
        def read(self, size=-1): return self.buffer.read(size)

    def fake_urlopen(req: Request, timeout: int):
        seen.append((req.full_url, dict(req.header_items())))
        return BinaryResponse()

    monkeypatch.setattr(agent, "_urlopen_no_redirect", fake_urlopen)
    client = agent.PaperclipClient("https://paperclip.example", "api", deploy_authorization_id="auth", deploy_authorization_token="pcdeploy_secret")
    target = tmp_path / "artifact"
    client.download_staged("/api/release-candidates/cand/staged-artifact", target)
    assert target.read_bytes() == b"artifact"
    assert seen[0][0] == "https://paperclip.example/api/release-candidates/cand/staged-artifact"
    assert seen[0][1]["X-paperclip-deploy-token"] == "pcdeploy_secret"
    assert "pcdeploy_secret" not in seen[0][0]
    try:
        client.download_staged("https://evil.example/artifact", target)
    except agent.DeployRejected as exc:
        assert "same-origin" in str(exc)
    else:
        raise AssertionError("expected cross-origin rejection")


def test_cosign_pins_exact_workflow_identity_issuer_and_digest(monkeypatch, tmp_path: Path) -> None:
    seen = []
    trusted_root = tmp_path / "trusted-root.json"
    trusted_root.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(agent, "TRUSTED_ROOT_PATH", trusted_root)
    monkeypatch.setattr(agent.subprocess, "run", lambda argv, **kwargs: seen.append(argv) or subprocess.CompletedProcess(argv, 0))
    candidate = _candidate(tmp_path)
    assert agent.cosign_verify(candidate, tmp_path / "candidate.tar", tmp_path / "signature.bundle") is True
    argv = seen[0]
    assert argv[1] == "verify-blob-attestation"
    assert argv[argv.index("--trusted-root") + 1] == str(trusted_root)
    assert argv[argv.index("--certificate-identity") + 1] == agent.EXPECTED_SIGNER_IDENTITY
    assert argv[argv.index("--certificate-oidc-issuer") + 1] == "https://token.actions.githubusercontent.com"
    assert argv[argv.index("--type") + 1] == "https://sigstore.dev/cosign/sign/v1"
    assert argv[argv.index("--digest") + 1] == "a" * 64
    assert argv[argv.index("--digestAlg") + 1] == "sha256"
    assert ".*" not in " ".join(argv)


def test_cosign_fails_closed_without_trusted_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(agent, "TRUSTED_ROOT_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(agent.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute cosign")))
    assert agent.cosign_verify(_candidate(tmp_path), tmp_path / "candidate.tar", tmp_path / "signature.bundle") is False


def test_rollback_failure_is_explicit_and_record_is_spooled(tmp_path: Path) -> None:
    class BrokenRollback(FakeActivator):
        def run_acceptance(self, activated_version: str) -> dict:
            raise RuntimeError("acceptance exploded")
        def rollback(self, previous_version: str) -> str:
            raise RuntimeError("rollback exploded")

    record = _run(tmp_path, _candidate(tmp_path), _lease(), BrokenRollback())
    assert record["rollback_status"] == "rollback_failed"
    assert "rollback failed" in record["failure_reason"]
    spooled = list((tmp_path / "deploy-record-spool").glob("*.json"))
    assert len(spooled) == 1
    assert json.loads(spooled[0].read_text())["record"]["rollback_status"] == "rollback_failed"


def test_post_failure_spools_and_retry_posts_without_activation(tmp_path: Path) -> None:
    def unavailable(_record):
        raise OSError("relay unavailable")

    record = agent.run_once(candidate=_candidate(tmp_path), lease=_lease(), config=_config(tmp_path), activator=FakeActivator(), signature_verifier=lambda *_: True, post_record=unavailable, now=dt.datetime(2026, 7, 13, 20, 0, tzinfo=dt.timezone.utc))
    assert record["record_post_status"] == "spooled_for_retry"
    posted = []
    class ReceiptClient:
        def post_receipt(self, path, entry): posted.append((path, entry))
    agent.retry_spooled_records(_config(tmp_path), ReceiptClient())
    assert len(posted) == 1
    assert posted[0][0] == "/api/release-deploy-authorizations/lease-1/deploy-record-receipt"
    assert not list((tmp_path / "deploy-record-spool").glob("*.json"))


def test_rejects_noncanonical_commit_sha_before_filesystem_use(tmp_path: Path) -> None:
    try:
        _candidate(tmp_path, commit_sha="../../candidate")
    except agent.DeployRejected as exc:
        assert "exact 40-character" in str(exc)
    else:
        raise AssertionError("expected invalid Git SHA rejection")


def test_live_env_path_is_activated_and_restored(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.service_env_path.write_text("# retained\nOTHER_SETTING=keep-me\nSCANNER_EDGE_IMAGE=repo/old@sha256:" + "b" * 64 + "\n")
    (tmp_path / "current_version").write_text("old-sha")
    archive, archive_image_id = _write_docker_archive(tmp_path)
    loaded_image_id = "sha256:" + "f" * 64

    def fake_run(argv, **_kwargs):
        stdout = json.dumps([_loaded_image(image_id=loaded_image_id)]) if argv[:3] == ["docker", "image", "inspect"] else ""
        return subprocess.CompletedProcess(argv, 0, stdout=stdout)

    monkeypatch.setattr(agent.subprocess, "run", fake_run)
    activator = agent.SystemdDockerActivator(config)
    previous = activator.snapshot_current()
    activator.activate_tarball(archive, _candidate(tmp_path))
    assert archive_image_id != loaded_image_id
    assert COMMIT_SHA in (tmp_path / "current_version").read_text()
    activated_env = config.service_env_path.read_text()
    assert f"SCANNER_EDGE_IMAGE={loaded_image_id}" in activated_env
    assert "repo/old" not in activated_env
    assert "# retained" in activated_env
    assert "OTHER_SETTING=keep-me" in activated_env
    activator.rollback(previous)
    assert "repo/old" in config.service_env_path.read_text()
    assert (tmp_path / "current_version").read_text() == "old-sha"


def test_loaded_image_runtime_mismatch_is_rejected(tmp_path: Path) -> None:
    archive, _archive_image_id = _write_docker_archive(tmp_path)
    loaded = _loaded_image(image_id="sha256:" + "f" * 64)
    loaded["Config"]["Labels"]["org.opencontainers.image.revision"] = "b" * 40
    try:
        agent.verify_loaded_image_matches_archive(archive, _candidate(tmp_path), loaded)
    except agent.DeployRejected as exc:
        assert "runtime field Labels" in str(exc)
    else:
        raise AssertionError("expected loaded image runtime mismatch rejection")


def test_authenticated_redirects_are_rejected_for_api_and_artifacts(tmp_path: Path) -> None:
    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302); self.send_header("Location", "http://127.0.0.1:9/exfil"); self.end_headers()
        def log_message(self, *_args): pass
    server = HTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        client = agent.PaperclipClient(f"http://127.0.0.1:{server.server_port}", "api-secret", deploy_authorization_id="auth", deploy_authorization_token="deploy-secret")
        for action in (lambda: client._request("GET", "/api/redirect"), lambda: client.download_staged("/api/artifact", tmp_path / "out")):
            try: action()
            except agent.DeployRejected as exc: assert "redirect rejected" in str(exc)
            else: raise AssertionError("expected redirect rejection")
    finally:
        server.shutdown(); thread.join()


def test_corrupt_spool_is_quarantined_and_valid_receipt_continues(tmp_path: Path) -> None:
    spool = tmp_path / "deploy-record-spool"; spool.mkdir()
    (spool / "000-bad.json").write_text("{truncated")
    valid_record = {"lease_id":"lease-old","candidate_id":"cand-old","status":"activated"}
    agent._atomic_write_json(spool / "001-good.json", {"schema_version":1,"receipt_path":"/api/release-deploy-authorizations/lease-old/deploy-record-receipt","lease_id":"lease-old","candidate_id":"cand-old","record":valid_record})
    posted = []
    class Client:
        def post_receipt(self, path, entry): posted.append((path, entry))
    agent.retry_spooled_records(_config(tmp_path), Client())
    assert len(posted) == 1
    assert (spool / "quarantine" / "000-bad.json").exists()


def test_spool_retry_cannot_rebind_old_evidence_to_new_authorization(monkeypatch, tmp_path: Path) -> None:
    spool = tmp_path / "deploy-record-spool"; spool.mkdir()
    record = {"lease_id":"lease-old","candidate_id":"cand-old","status":"activated"}
    agent._atomic_write_json(spool / "old.json", {"schema_version":1,"receipt_path":"/api/release-deploy-authorizations/lease-old/deploy-record-receipt","lease_id":"lease-old","candidate_id":"cand-old","record":record})
    seen = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_exc): return False
        def read(self): return b"{}"
    def open_request(req, timeout): seen.append(req); return Response()
    monkeypatch.setattr(agent, "_urlopen_no_redirect", open_request)
    rotated = agent.PaperclipClient("https://paperclip.example", "service-api", deploy_authorization_id="auth-new", deploy_authorization_token="token-new")
    agent.retry_spooled_records(_config(tmp_path), rotated)
    assert len(seen) == 1
    assert seen[0].full_url.endswith("/api/release-deploy-authorizations/lease-old/deploy-record-receipt")
    headers = dict(seen[0].header_items())
    assert "X-paperclip-deploy-token" not in headers
    body = seen[0].data.decode()
    assert "auth-new" not in body and "token-new" not in body


def test_spool_failure_prevents_activation(monkeypatch, tmp_path: Path) -> None:
    activator = FakeActivator()
    monkeypatch.setattr(agent, "_atomic_write_json", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    try: _run(tmp_path, _candidate(tmp_path), _lease(), activator)
    except OSError: pass
    assert activator.activated is False
def test_exact_paperclip_fixture_is_flat_and_authorization_bound() -> None:
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "paperclip-approved-lease-v1.json").read_text(encoding="utf-8")
    )
    authorization_id = payload["authorizationId"]
    assert "lease" not in payload and "candidate" not in payload
    assert payload["stagedArtifactPath"] == f"/api/release-deploy-authorizations/{authorization_id}/staged-artifact"
    assert payload["stagedSignatureBundlePath"] == f"/api/release-deploy-authorizations/{authorization_id}/staged-signature-bundle"
    assert payload["deployRecordReceiptPath"] == f"/api/release-deploy-authorizations/{authorization_id}/deploy-record-receipt"


def test_receipt_payload_is_api_key_only_and_bound_to_raw_record() -> None:
    record = {
        "lease_id": "lease-1",
        "candidate_id": "cand-1",
        "status": "activated",
        "commit_sha": COMMIT_SHA,
        "image_digest": "ghcr.io/backbond/scanner@sha256:" + "a" * 64,
        "approval_interaction_id": "approval-1",
        "acceptance_result": {"passed": True},
        "rollback_status": "not_needed",
    }
    payload = agent.to_paperclip_deploy_receipt(
        {"lease_id": "lease-1", "candidate_id": "cand-1", "record": record}
    )
    assert payload["candidateId"] == "cand-1"
    assert payload["status"] == "succeeded"
    assert "authorizationId" not in payload
    assert "pcdeploy_" not in json.dumps(payload).lower()
    assert payload["metadata"]["deployRecord"] == record


def test_non_https_paperclip_origins_are_rejected_except_loopback() -> None:
    for api_url in (
        "http://paperclip.example",
        "https://user:secret@paperclip.example",
        "https://paperclip.example/api",
        "https://paperclip.example?token=secret",
    ):
        try:
            agent.PaperclipClient(api_url, "api-token")
        except agent.DeployRejected:
            pass
        else:
            raise AssertionError(f"expected Paperclip origin rejection for {api_url}")
    agent.PaperclipClient("http://127.0.0.1:3000", "api-token")
    agent.PaperclipClient("http://localhost:3000", "api-token")


def test_staged_artifact_rejects_query_fragment_and_userinfo(tmp_path: Path) -> None:
    client = agent.PaperclipClient(
        "https://paperclip.example",
        "api",
        deploy_authorization_id="auth",
        deploy_authorization_token="secret",
    )
    for artifact_url in (
        "/api/artifact?token=secret",
        "/api/artifact#fragment",
        "https://user@paperclip.example/api/artifact",
    ):
        try:
            client.download_staged(artifact_url, tmp_path / "out")
        except agent.DeployRejected:
            pass
        else:
            raise AssertionError(f"expected staged URL rejection for {artifact_url}")


def test_docker_digest_inspection_failure_leaves_service_configuration_unchanged(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    original = "OTHER_SETTING=keep\\nSCANNER_EDGE_IMAGE=repo/old@sha256:" + "b" * 64 + "\\n"
    config.service_env_path.write_text(original, encoding="utf-8")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["docker", "image", "inspect"]:
            raise subprocess.CalledProcessError(1, argv)
        return subprocess.CompletedProcess(argv, 0)

    archive, _image_id = _write_docker_archive(tmp_path)
    monkeypatch.setattr(agent.subprocess, "run", fake_run)
    activator = agent.SystemdDockerActivator(config)
    try:
        activator.activate_tarball(archive, _candidate(tmp_path))
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError("expected exact image digest inspection failure")
    assert config.service_env_path.read_text(encoding="utf-8") == original
    assert not any(call[:2] == ["systemctl", "restart"] for call in calls)


def test_dry_run_is_verification_only_and_never_constructs_activator(monkeypatch, tmp_path: Path) -> None:
    candidate = _candidate(tmp_path, expires_at="2099-07-13T21:00:00Z")
    lease = _lease(expires_at="2099-07-13T21:00:00Z")
    candidate_json = tmp_path / "candidate.json"
    lease_json = tmp_path / "lease.json"
    candidate_json.write_text(json.dumps(candidate.__dict__), encoding="utf-8")
    lease_json.write_text(json.dumps(lease.__dict__), encoding="utf-8")
    monkeypatch.setattr(agent, "cosign_verify", lambda *_args: True)
    monkeypatch.setattr(
        agent,
        "SystemdDockerActivator",
        lambda *_args: (_ for _ in ()).throw(AssertionError("activator constructed during dry run")),
    )
    state_dir = tmp_path / "state"
    result = agent.main(
        [
            "--candidate-json", str(candidate_json),
            "--lease-json", str(lease_json),
            "--state-dir", str(state_dir),
            "--environment", "production",
            "--host", "srv1749248",
            "--dry-run",
        ]
    )
    assert result == 0
    assert not state_dir.exists()


def test_local_fixture_activation_requires_dry_run(tmp_path: Path) -> None:
    candidate_json = tmp_path / "candidate.json"
    lease_json = tmp_path / "lease.json"
    candidate_json.write_text("{}", encoding="utf-8")
    lease_json.write_text("{}", encoding="utf-8")
    try:
        agent.main(["--candidate-json", str(candidate_json), "--lease-json", str(lease_json)])
    except agent.DeployRejected as exc:
        assert "verification-only" in str(exc) or "missing required fields" in str(exc)
    else:
        raise AssertionError("expected local fixture activation rejection")


def test_activation_pending_spool_blocks_retry_and_is_not_posted(tmp_path: Path) -> None:
    spool = tmp_path / "deploy-record-spool"
    spool.mkdir()
    record = {"lease_id": "lease-old", "candidate_id": "cand-old", "status": "activation_pending"}
    pending_path = spool / "pending.json"
    agent._atomic_write_json(
        pending_path,
        {
            "schema_version": 1,
            "receipt_path": "/api/release-deploy-authorizations/lease-old/deploy-record-receipt",
            "lease_id": "lease-old",
            "candidate_id": "cand-old",
            "record": record,
        },
    )
    posted = []

    class Client:
        def post_receipt(self, path, entry):
            posted.append((path, entry))

    try:
        agent.retry_spooled_records(_config(tmp_path), Client())
    except agent.DeployRejected as exc:
        assert "requires reconciliation" in str(exc)
    else:
        raise AssertionError("expected unresolved activation to block retry")
    assert posted == []
    assert pending_path.exists()


def _identity(commit_sha: str = COMMIT_SHA, *, sequence: int = 7) -> dict:
    image_digest = "sha256:" + "f" * 64
    return {
        "schema_version": agent.RELEASE_IDENTITY_SCHEMA,
        "environment": "production",
        "host": "srv1749248",
        "candidate_id": "cand-1",
        "commit_sha": commit_sha,
        "image_digest": image_digest,
        "configured_image": image_digest,
        "running_container_revision": commit_sha,
        "internal_version_sha": commit_sha,
        "current_version": commit_sha,
        "last_activated_sequence": sequence,
        "terminal_receipt_sha256": "sha256:" + "1" * 64,
        "status": "activated",
        "recorded_at": "2026-07-13T20:00:00Z",
    }


def test_pending_release_identity_recovers_only_matching_observed_state(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    identity = _identity()
    agent._atomic_write_json(tmp_path / "release_identity.pending.json", identity)
    config.service_env_path.write_text(f"SCANNER_EDGE_IMAGE={identity['configured_image']}\n", encoding="utf-8")
    (tmp_path / "current_version").write_text(COMMIT_SHA, encoding="utf-8")

    def fake_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=COMMIT_SHA)

    class VersionResponse:
        def __enter__(self): return self
        def __exit__(self, *_exc): return False
        def read(self): return json.dumps({"git_sha": COMMIT_SHA}).encode("utf-8")

    monkeypatch.setattr(agent.subprocess, "run", fake_run)
    monkeypatch.setattr(agent.urllib.request, "urlopen", lambda *_args, **_kwargs: VersionResponse())

    recovered = agent.SystemdDockerActivator(config).recover_pending_release_identity()

    assert recovered is not None
    assert recovered["status"] == "recovered"
    assert not (tmp_path / "release_identity.pending.json").exists()
    assert json.loads((tmp_path / "release_identity.json").read_text(encoding="utf-8"))["commit_sha"] == COMMIT_SHA


def test_pending_release_identity_fails_closed_on_observed_mismatch(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    identity = _identity()
    agent._atomic_write_json(tmp_path / "release_identity.pending.json", identity)
    config.service_env_path.write_text(f"SCANNER_EDGE_IMAGE={identity['configured_image']}\n", encoding="utf-8")
    (tmp_path / "current_version").write_text("b" * 40, encoding="utf-8")
    monkeypatch.setattr(agent.subprocess, "run", lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, stdout=COMMIT_SHA))

    class VersionResponse:
        def __enter__(self): return self
        def __exit__(self, *_exc): return False
        def read(self): return json.dumps({"git_sha": COMMIT_SHA}).encode("utf-8")

    monkeypatch.setattr(agent.urllib.request, "urlopen", lambda *_args, **_kwargs: VersionResponse())

    try:
        agent.SystemdDockerActivator(config).recover_pending_release_identity()
    except agent.DeployRejected as exc:
        assert "current_version" in str(exc)
    else:
        raise AssertionError("expected fail-closed pending recovery")
    assert (tmp_path / "release_identity.pending.json").exists()


def test_check_release_identity_passes_matching_state_and_fails_each_source(tmp_path: Path, capsys) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    identity = _identity()
    identity_path = state_dir / "release_identity.json"
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    (state_dir / "scanner-edge.env").write_text(f"SCANNER_EDGE_IMAGE={identity['configured_image']}\n", encoding="utf-8")
    (state_dir / "current_version").write_text(identity["current_version"], encoding="utf-8")
    (state_dir / "last_activated_sequence").write_text(str(identity["last_activated_sequence"]), encoding="utf-8")

    base_args = [
        "--state-dir", str(state_dir),
        "--identity", str(identity_path),
        "--running-container-revision", identity["running_container_revision"],
        "--internal-version-sha", identity["internal_version_sha"],
    ]
    assert check_release_identity.main(base_args) == 0
    capsys.readouterr()

    def _set_arg(args: list[str], flag: str, value: str) -> None:
        args[args.index(flag) + 1] = value

    cases = (
        lambda args: (state_dir / "scanner-edge.env").write_text("SCANNER_EDGE_IMAGE=sha256:" + "0" * 64 + "\n", encoding="utf-8"),
        lambda args: _set_arg(args, "--running-container-revision", "b" * 40),
        lambda args: _set_arg(args, "--internal-version-sha", "c" * 40),
        lambda args: (state_dir / "current_version").write_text("d" * 40, encoding="utf-8"),
        lambda args: (state_dir / "last_activated_sequence").write_text("8", encoding="utf-8"),
    )
    for mutate in cases:
        (state_dir / "scanner-edge.env").write_text(f"SCANNER_EDGE_IMAGE={identity['configured_image']}\n", encoding="utf-8")
        (state_dir / "current_version").write_text(identity["current_version"], encoding="utf-8")
        (state_dir / "last_activated_sequence").write_text(str(identity["last_activated_sequence"]), encoding="utf-8")
        args = list(base_args)
        mutate(args)
        assert check_release_identity.main(args) == 2
def test_rejects_receipt_path_not_bound_to_authorization_before_activation(tmp_path: Path) -> None:
    activator = FakeActivator()
    record = _run(
        tmp_path,
        _candidate(tmp_path),
        _lease(record_receipt_path="/api/release-deploy-authorizations/other/deploy-record-receipt"),
        activator,
    )
    assert record["status"] == "rejected"
    assert "receipt path is not bound" in record["failure_reason"]
    assert activator.activated is False


def test_poll_rejects_staged_paths_not_bound_to_authorization(monkeypatch) -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "paperclip-approved-lease-v1.json").read_text(encoding="utf-8")
    )
    fixture["stagedArtifactPath"] = "/api/release-deploy-authorizations/other/staged-artifact"

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_exc): return False
        def read(self): return json.dumps(fixture).encode("utf-8")

    monkeypatch.setattr(agent, "_urlopen_no_redirect", lambda *_args, **_kwargs: Response())
    client = agent.PaperclipClient(
        "https://paperclip.example",
        "api",
        deploy_authorization_id=fixture["authorizationId"],
        deploy_authorization_token="deploy-token",
    )
    try:
        client.poll_approved_lease("srv1749248", "production")
    except agent.DeployRejected as exc:
        assert "artifact path is not authorization-bound" in str(exc)
    else:
        raise AssertionError("expected authorization path binding rejection")
def test_offline_archive_identity_verifies_config_layers_and_commit(tmp_path: Path) -> None:
    archive, image_id = _write_docker_archive(tmp_path)
    local_tag, verified_image_id = agent.verify_docker_archive_identity(archive, _candidate(tmp_path))
    assert local_tag == f"backbond-scanner-live:{COMMIT_SHA}"
    assert verified_image_id == image_id


def test_offline_archive_wrong_commit_is_rejected_before_docker_load(monkeypatch, tmp_path: Path) -> None:
    archive, _image_id = _write_docker_archive(tmp_path, commit_sha="b" * 40)
    calls = []
    monkeypatch.setattr(agent.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    try:
        agent.SystemdDockerActivator(_config(tmp_path)).activate_tarball(archive, _candidate(tmp_path))
    except agent.DeployRejected as exc:
        assert "tag does not match" in str(exc) or "revision label does not match" in str(exc)
    else:
        raise AssertionError("expected archive commit rejection")
    assert calls == []


def test_offline_archive_layer_tampering_is_rejected(tmp_path: Path) -> None:
    archive, _image_id = _write_docker_archive(tmp_path)
    rewritten = tmp_path / "tampered.tar"
    with tarfile.open(archive, "r") as original, tarfile.open(rewritten, "w") as output:
        for member in original.getmembers():
            handle = original.extractfile(member)
            body = handle.read() if handle is not None else b""
            if member.name.startswith("blobs/sha256/") and member.size == len(b"layer"):
                body = b"evil!"
            replacement = tarfile.TarInfo(member.name)
            replacement.size = len(body)
            output.addfile(replacement, io.BytesIO(body))
    try:
        agent.verify_docker_archive_identity(rewritten, _candidate(tmp_path))
    except agent.DeployRejected as exc:
        assert "layer digest" in str(exc)
    else:
        raise AssertionError("expected layer digest rejection")
