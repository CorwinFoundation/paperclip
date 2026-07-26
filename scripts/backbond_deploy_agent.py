from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Protocol

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from release_identity import release_identity_evidence

UTC = dt.timezone.utc
DEFAULT_APPROVED_LEASE_PATH_TEMPLATE = (
    "/api/release-candidates/approved-lease?authorizationId={authorization_id}"
)
DEFAULT_DEPLOY_RECORD_PATH = "/api/release-candidates/deploy-records"
EXPECTED_SIGNER_IDENTITY = "https://github.com/CorwinFoundation/are-scanner/.github/workflows/scanner-edge-image.yml@refs/heads/master"
EXPECTED_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
EXPECTED_SIGNATURE_PREDICATE_TYPE = "https://sigstore.dev/cosign/sign/v1"
TRUSTED_ROOT_PATH = Path("/etc/backbond/sigstore-trusted-root.json")


class DeployRejected(RuntimeError):
    pass


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DeployRejected(f"authenticated redirect rejected ({code})")


def _urlopen_no_redirect(request: urllib.request.Request, *, timeout: int):
    return urllib.request.build_opener(_RejectRedirects()).open(request, timeout=timeout)


REQUIRED_SCAN_LOG_PINS = ("forensic_bundle_uri", "snapshot_hash", "probe_set_hash")
REQUIRED_CUSTOMER_ENVELOPE_FIELDS = ("scan_id", "scanner_version", "corpus_version")


@dataclasses.dataclass(frozen=True)
class Candidate:
    candidate_id: str
    commit_sha: str
    image_digest: str
    tarball_url: str
    tarball_sha256: str
    sequence: int
    expires_at: str
    environment: str
    host: str
    signature_ref: str
    signature_sha256: str
    approval_interaction_id: str
    deploy_token_scope: str

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "Candidate":
        payload = normalize_candidate_payload(payload)
        required = {field.name for field in dataclasses.fields(cls)}
        missing = sorted(required - payload.keys())
        if missing:
            raise DeployRejected(f"candidate missing required fields: {', '.join(missing)}")
        candidate_id = str(payload["candidate_id"])
        if not re.fullmatch(r"[A-Za-z0-9._-]+", candidate_id):
            raise DeployRejected("candidate_id contains unsafe filesystem characters")
        commit_sha = str(payload["commit_sha"])
        if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
            raise DeployRejected("candidate commit_sha must be an exact 40-character lowercase Git SHA")
        return cls(
            candidate_id=candidate_id,
            commit_sha=commit_sha,
            image_digest=str(payload["image_digest"]),
            tarball_url=str(payload["tarball_url"]),
            tarball_sha256=str(payload["tarball_sha256"]),
            sequence=int(payload["sequence"]),
            expires_at=str(payload["expires_at"]),
            environment=str(payload["environment"]),
            host=str(payload["host"]),
            signature_ref=str(payload["signature_ref"]),
            signature_sha256=str(payload["signature_sha256"]),
            approval_interaction_id=str(payload["approval_interaction_id"]),
            deploy_token_scope=str(payload["deploy_token_scope"]),
        )


@dataclasses.dataclass(frozen=True)
class DeployLease:
    lease_id: str
    candidate_id: str
    environment: str
    host: str
    sequence: int
    expires_at: str
    token_scope: str
    record_receipt_path: str

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "DeployLease":
        payload = normalize_lease_payload(payload)
        required = {field.name for field in dataclasses.fields(cls)}
        missing = sorted(required - payload.keys())
        if missing:
            raise DeployRejected(f"lease missing required fields: {', '.join(missing)}")
        lease_id = str(payload["lease_id"])
        if not re.fullmatch(r"[A-Za-z0-9._-]+", lease_id):
            raise DeployRejected("lease_id contains unsafe URL characters")
        return cls(
            lease_id=lease_id,
            candidate_id=str(payload["candidate_id"]),
            environment=str(payload["environment"]),
            host=str(payload["host"]),
            sequence=int(payload["sequence"]),
            expires_at=str(payload["expires_at"]),
            token_scope=str(payload["token_scope"]),
            record_receipt_path=str(payload["record_receipt_path"]),
        )


@dataclasses.dataclass(frozen=True)
class DeployConfig:
    environment: str
    host: str
    state_dir: Path
    service_env_path: Path = Path("/etc/backbond/scanner-edge.env")
    service_name: str = "backbond-scanner-live"
    version_url: str = "http://127.0.0.1:8000/version"


RELEASE_IDENTITY_SCHEMA = "backbond-scanner-release-identity@1"
RELEASE_IDENTITY_FIELDS = (
    "configured_image",
    "running_container_revision",
    "internal_version_sha",
    "current_version",
    "last_activated_sequence",
    "terminal_receipt_sha256",
)


class Activator(Protocol):
    def snapshot_current(self) -> Any: ...

    def activate_tarball(self, tarball_path: Path, candidate: Candidate) -> str: ...

    def run_acceptance(self, activated_version: str) -> dict[str, Any]: ...

    def rollback(self, previous_version: Any) -> str: ...


class SystemdDockerActivator:
    def __init__(self, config: DeployConfig) -> None:
        self.config = config

    def snapshot_current(self) -> dict[str, Any]:
        current = self.config.state_dir / "current_version"
        env_file = self.config.service_env_path
        return {"version": current.read_text(encoding="utf-8").strip() if current.exists() else "unknown", "service_config": env_file.read_text(encoding="utf-8") if env_file.exists() else None}

    def activate_tarball(self, tarball_path: Path, candidate: Candidate) -> str:
        if not re.fullmatch(r"[^\s/@]+(?:/[^\s/@]+)+@sha256:[0-9a-f]{64}", candidate.image_digest):
            raise DeployRejected("candidate image_digest must be a repository@sha256 reference")
        images_dir = self.config.state_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        target = images_dir / f"{candidate.commit_sha}.tar"
        _atomic_copy_file(tarball_path, target)
        local_tag, _archive_image_id = verify_docker_archive_identity(target, candidate)
        subprocess.run(["docker", "load", "--input", str(target)], check=True)
        inspected = subprocess.run(
            ["docker", "image", "inspect", local_tag],
            check=True,
            text=True,
            capture_output=True,
        )
        try:
            loaded_images = json.loads(inspected.stdout)
        except json.JSONDecodeError as exc:
            raise DeployRejected("loaded Docker image inspect returned invalid JSON") from exc
        if not isinstance(loaded_images, list) or len(loaded_images) != 1 or not isinstance(loaded_images[0], dict):
            raise DeployRejected("loaded Docker image inspect returned an unexpected result")
        loaded_image_id = verify_loaded_image_matches_archive(target, candidate, loaded_images[0])
        env_file = self.config.service_env_path
        current_env = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
        self._write_identity_pending(candidate, status="activating")
        _atomic_write_text(
            env_file,
            _replace_env_assignment(current_env, "SCANNER_EDGE_IMAGE", loaded_image_id),
            default_mode=0o600,
        )
        subprocess.run(["systemctl", "restart", self.config.service_name], check=True)
        _atomic_write_text(self.config.state_dir / "current_version", candidate.commit_sha, default_mode=0o600)
        return candidate.commit_sha

    def run_acceptance(self, activated_version: str) -> dict[str, Any]:
        # Health/auth/staging-scan/envelope checks are delegated to the on-box
        # acceptance runner so production secrets never enter this script.
        runner = Path("/opt/backbond/are-scanner/scripts/deploy/deploy_agent_acceptance.py")
        completed = subprocess.run([str(runner), activated_version], check=False, text=True, capture_output=True)
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise DeployRejected("acceptance runner returned invalid JSON") from exc
        result["passed"] = completed.returncode == 0 and result.get("passed") is True
        return result

    def rollback(self, previous_version: dict[str, Any]) -> str:
        env_file = self.config.service_env_path
        prior_config = previous_version.get("service_config")
        if prior_config is None:
            _atomic_unlink(env_file)
        else:
            _atomic_write_text(env_file, str(prior_config), default_mode=0o600)
        subprocess.run(["systemctl", "restart", self.config.service_name], check=True)
        _atomic_write_text(self.config.state_dir / "current_version", str(previous_version["version"]), default_mode=0o600)
        return "rolled_back"

    def commit_release_identity(
        self,
        *,
        candidate: Candidate,
        activated_version: str,
        terminal_receipt_sha256: str,
        status: str,
    ) -> dict[str, Any]:
        identity = self.collect_release_identity(
            candidate=candidate,
            activated_version=activated_version,
            terminal_receipt_sha256=terminal_receipt_sha256,
            status=status,
        )
        validate_release_identity(identity)
        current = self.config.state_dir / "release_identity.json"
        if current.exists():
            _atomic_write_json(self.config.state_dir / "previous_release_identity.json", self._read_json(current))
        _atomic_write_json(current, identity)
        _atomic_write_text(self.config.state_dir / "current_version", str(identity["current_version"]), default_mode=0o600)
        _atomic_write_text(
            self.config.state_dir / "last_activated_sequence",
            str(identity["last_activated_sequence"]),
            default_mode=0o600,
        )
        _atomic_unlink(self.config.state_dir / "release_identity.pending.json")
        return identity

    def recover_pending_release_identity(self) -> dict[str, Any] | None:
        pending_path = self.config.state_dir / "release_identity.pending.json"
        if not pending_path.exists():
            return None
        pending = self._read_json(pending_path)
        if pending.get("schema_version") != RELEASE_IDENTITY_SCHEMA:
            raise DeployRejected("release identity pending marker has invalid schema")
        candidate = Candidate.from_mapping(
            {
                "candidate_id": pending.get("candidate_id"),
                "commit_sha": pending.get("commit_sha"),
                "image_digest": pending.get("image_digest"),
                "tarball_url": "file:///dev/null",
                "tarball_sha256": "0" * 64,
                "sequence": pending.get("last_activated_sequence"),
                "expires_at": "2099-01-01T00:00:00Z",
                "environment": pending.get("environment"),
                "host": pending.get("host"),
                "signature_ref": "file:///dev/null",
                "signature_sha256": "0" * 64,
                "approval_interaction_id": pending.get("approval_interaction_id", "pending-recovery"),
                "deploy_token_scope": pending.get(
                    "deploy_token_scope",
                    f"scanner:deploy:{pending.get('environment')}:{pending.get('host')}:{pending.get('candidate_id')}",
                ),
            }
        )
        terminal_receipt_sha256 = str(pending.get("terminal_receipt_sha256") or "sha256:" + "0" * 64)
        identity = self.collect_release_identity(
            candidate=candidate,
            activated_version=str(pending.get("commit_sha", "")),
            terminal_receipt_sha256=terminal_receipt_sha256,
            status="recovered",
        )
        validate_release_identity(identity)
        _atomic_write_json(self.config.state_dir / "release_identity.json", identity)
        _atomic_unlink(pending_path)
        return identity

    def collect_release_identity(
        self,
        *,
        candidate: Candidate,
        activated_version: str,
        terminal_receipt_sha256: str,
        status: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": RELEASE_IDENTITY_SCHEMA,
            "environment": self.config.environment,
            "host": self.config.host,
            "candidate_id": candidate.candidate_id,
            "commit_sha": activated_version,
            "image_digest": self._configured_image() or candidate.image_digest,
            "configured_image": self._configured_image(),
            "running_container_revision": self._running_container_revision(),
            "internal_version_sha": self._internal_version_sha(),
            "current_version": self._read_text(self.config.state_dir / "current_version"),
            "last_activated_sequence": candidate.sequence,
            "terminal_receipt_sha256": terminal_receipt_sha256,
            "status": status,
            "recorded_at": dt.datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }

    def _write_identity_pending(self, candidate: Candidate, *, status: str) -> None:
        _atomic_write_json(
            self.config.state_dir / "release_identity.pending.json",
            {
                "schema_version": RELEASE_IDENTITY_SCHEMA,
                "environment": self.config.environment,
                "host": self.config.host,
                "candidate_id": candidate.candidate_id,
                "commit_sha": candidate.commit_sha,
                "image_digest": candidate.image_digest,
                "last_activated_sequence": candidate.sequence,
                "approval_interaction_id": candidate.approval_interaction_id,
                "deploy_token_scope": candidate.deploy_token_scope,
                "status": status,
                "recorded_at": dt.datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            },
        )

    def _read_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise DeployRejected(f"{path.name} must contain a JSON object")
        return payload

    def _read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

    def _configured_image(self) -> str:
        if not self.config.service_env_path.exists():
            return ""
        for line in self.config.service_env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("SCANNER_EDGE_IMAGE="):
                return line.split("=", 1)[1].strip()
        return ""

    def _running_container_revision(self) -> str:
        completed = subprocess.run(
            [
                "docker",
                "inspect",
                self.config.service_name,
                "--format",
                "{{ index .Config.Labels \"org.opencontainers.image.revision\" }}",
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    def _internal_version_sha(self) -> str:
        try:
            with urllib.request.urlopen(self.config.version_url, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return ""
        if not isinstance(payload, dict):
            return ""
        for key in ("commit_sha", "commitSha", "git_sha", "sha", "version"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return ""


class PaperclipClient:
    def __init__(
        self,
        api_url: str,
        token: str,
        run_id: str | None = None,
        approved_lease_path_template: str = DEFAULT_APPROVED_LEASE_PATH_TEMPLATE,
        deploy_record_path: str = DEFAULT_DEPLOY_RECORD_PATH,
        deploy_authorization_id: str | None = None,
        deploy_authorization_token: str | None = None,
    ) -> None:
        _validate_paperclip_api_url(api_url)
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.run_id = run_id
        self.approved_lease_path_template = approved_lease_path_template
        self.deploy_record_path = deploy_record_path
        self.deploy_authorization_id = deploy_authorization_id
        self.deploy_authorization_token = deploy_authorization_token

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request_path = _validate_relative_api_path(path, label="Paperclip API route", allow_query=True)
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(f"{self.api_url}{request_path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        if self.run_id:
            req.add_header("X-Paperclip-Run-Id", self.run_id)
        if self.deploy_authorization_id:
            req.add_header("X-Deploy-Authorization-Id", self.deploy_authorization_id)
        if self.deploy_authorization_token:
            req.add_header("X-Paperclip-Deploy-Token", self.deploy_authorization_token)
        with _urlopen_no_redirect(req, timeout=20) as response:
            body = response.read()
        return json.loads(body.decode("utf-8")) if body else {}

    def poll_approved_lease(self, host: str, environment: str) -> tuple[DeployLease, Candidate]:
        path = self._format_path(host=host, environment=environment)
        payload = self._request("GET", path)
        lease = DeployLease.from_mapping(payload.get("lease", payload))
        candidate_payload = dict(payload.get("candidate", payload))
        required_staged = ("stagedArtifactPath", "stagedSignatureBundlePath", "stagedSignatureBundleSha256")
        missing = [key for key in required_staged if not candidate_payload.get(key)]
        if missing:
            raise DeployRejected(f"approved lease missing relay-staged field(s): {', '.join(missing)}")
        if not (candidate_payload.get("stagedArtifactSha256") or candidate_payload.get("stagedTarballSha256")):
            raise DeployRejected("approved lease missing relay-staged artifact SHA-256")
        expected_artifact = _authorization_api_path(lease.lease_id, "staged-artifact")
        expected_signature = _authorization_api_path(lease.lease_id, "staged-signature-bundle")
        if candidate_payload["stagedArtifactPath"] != expected_artifact:
            raise DeployRejected("approved lease staged artifact path is not authorization-bound")
        if candidate_payload["stagedSignatureBundlePath"] != expected_signature:
            raise DeployRejected("approved lease staged signature path is not authorization-bound")
        candidate_payload["tarball_url"] = candidate_payload["stagedArtifactPath"]
        candidate_payload["tarball_sha256"] = candidate_payload.get("stagedArtifactSha256") or candidate_payload["stagedTarballSha256"]
        candidate_payload["signature_ref"] = candidate_payload["stagedSignatureBundlePath"]
        candidate_payload["signature_sha256"] = candidate_payload["stagedSignatureBundleSha256"]
        return lease, Candidate.from_mapping(candidate_payload)

    def download_staged(self, path: str, dest: Path) -> None:
        parsed_api = urllib.parse.urlparse(self.api_url)
        parsed = urllib.parse.urlparse(path)
        if parsed.scheme:
            if parsed.username or parsed.password or parsed.params or parsed.query or parsed.fragment:
                raise DeployRejected("staged artifact URL must not contain userinfo, parameters, query, or fragment")
            if (parsed.scheme, parsed.netloc) != (parsed_api.scheme, parsed_api.netloc):
                raise DeployRejected("staged artifact must be same-origin with Paperclip")
            request_path = parsed.path
        else:
            request_path = _validate_relative_api_path(path, label="staged artifact path")
        if not request_path.startswith("/api/"):
            raise DeployRejected("staged artifact path must be a Paperclip API path")
        req = urllib.request.Request(f"{self.api_url}{request_path}", method="GET")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("X-Paperclip-Deploy-Token", self.deploy_authorization_token or "")
        with _urlopen_no_redirect(req, timeout=60) as response, dest.open("wb") as handle:
            shutil.copyfileobj(response, handle, length=1024 * 1024)

    def post_receipt(self, receipt_path: str, entry: dict[str, Any]) -> dict[str, Any]:
        request_path = _validate_relative_api_path(receipt_path, label="deploy record receipt")
        payload = to_paperclip_deploy_receipt(entry)
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        req = urllib.request.Request(f"{self.api_url}{request_path}", data=data, method="POST")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        with _urlopen_no_redirect(req, timeout=20) as response:
            body = response.read()
        return json.loads(body.decode("utf-8")) if body else {}

    def post_deploy_record(self, record: dict[str, Any]) -> dict[str, Any]:
        if not self.deploy_authorization_id or not self.deploy_authorization_token:
            raise DeployRejected("deploy authorization id/token required to post deploy record")
        return self._request("POST", self.deploy_record_path, to_paperclip_deploy_record(
            record,
            authorization_id=self.deploy_authorization_id,
        ))

    def _format_path(self, *, host: str, environment: str) -> str:
        if "{token}" in self.approved_lease_path_template:
            raise DeployRejected("deploy token must not be placed in the request URL")
        if "{authorization_id}" in self.approved_lease_path_template:
            if not self.deploy_authorization_id or not self.deploy_authorization_token:
                raise DeployRejected("deploy authorization id/token required to poll approved lease")
        return self.approved_lease_path_template.format(
            host=urllib.parse.quote(host, safe=""),
            environment=urllib.parse.quote(environment, safe=""),
            authorization_id=urllib.parse.quote(self.deploy_authorization_id or "", safe=""),
            token=urllib.parse.quote(self.deploy_authorization_token or "", safe=""),
        )


def _alias(payload: dict[str, Any], canonical: str, *aliases: str) -> None:
    if canonical in payload:
        return
    for alias in aliases:
        if alias in payload:
            payload[canonical] = payload[alias]
            return


def normalize_candidate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    _alias(normalized, "candidate_id", "candidateId", "id")
    _alias(normalized, "commit_sha", "commitSha")
    _alias(normalized, "image_digest", "imageDigest")
    _alias(normalized, "tarball_url", "stagedArtifactPath", "stagedTarballUrl", "tarballUrl", "artifactUrl")
    _alias(normalized, "tarball_sha256", "stagedArtifactSha256", "stagedTarballSha256", "tarballSha256", "tarballSha")
    _alias(normalized, "expires_at", "expiresAt")
    _alias(normalized, "signature_ref", "stagedSignatureBundlePath", "signatureRef", "signatureBundleRef")
    _alias(normalized, "signature_sha256", "stagedSignatureBundleSha256", "signatureSha256", "signatureBundleSha256")
    _alias(normalized, "approval_interaction_id", "approvalInteractionId")
    _alias(normalized, "deploy_token_scope", "deployTokenScope", "tokenScope")
    _alias(normalized, "host", "targetHost")
    if "deploy_token_scope" not in normalized and {"environment", "host", "candidate_id"} <= normalized.keys():
        normalized["deploy_token_scope"] = (
            f"scanner:deploy:{normalized['environment']}:{normalized['host']}:{normalized['candidate_id']}"
        )
    return normalized


def normalize_lease_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    _alias(normalized, "lease_id", "leaseId", "id", "deployAuthorizationId", "authorizationId")
    _alias(normalized, "candidate_id", "candidateId", "releaseCandidateId")
    _alias(normalized, "expires_at", "expiresAt")
    _alias(normalized, "token_scope", "tokenScope", "deployTokenScope")
    _alias(normalized, "host", "targetHost")
    _alias(normalized, "record_receipt_path", "deployRecordReceiptPath", "recordReceiptPath")
    if "token_scope" not in normalized and {"environment", "host", "candidate_id"} <= normalized.keys():
        normalized["token_scope"] = (
            f"scanner:deploy:{normalized['environment']}:{normalized['host']}:{normalized['candidate_id']}"
        )
    return normalized


def _authorization_api_path(authorization_id: str, suffix: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", authorization_id):
        raise DeployRejected("authorization id contains unsafe URL characters")
    return f"/api/release-deploy-authorizations/{authorization_id}/{suffix}"


def _validate_relative_api_path(path: str, *, label: str, allow_query: bool = False) -> str:
    parsed = urllib.parse.urlparse(path)
    if parsed.scheme or parsed.netloc or parsed.username or parsed.password:
        raise DeployRejected(f"{label} must use a same-origin Paperclip API path")
    if parsed.params or parsed.fragment or (parsed.query and not allow_query):
        raise DeployRejected(f"{label} contains disallowed URL components")
    if not parsed.path.startswith("/api/"):
        raise DeployRejected(f"{label} must be a Paperclip API path")
    return parsed.path + (("?" + parsed.query) if parsed.query else "")


def _validate_paperclip_api_url(api_url: str) -> None:
    parsed = urllib.parse.urlparse(api_url)
    if not parsed.scheme or not parsed.netloc or parsed.username or parsed.password:
        raise DeployRejected("Paperclip API URL must be an absolute origin without userinfo")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise DeployRejected("Paperclip API URL must not include a path, query, or fragment")
    host = parsed.hostname or ""
    try:
        loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise DeployRejected("Paperclip API URL must use HTTPS (HTTP is allowed only for loopback tests)")


def to_paperclip_deploy_record(
    record: dict[str, Any],
    *,
    authorization_id: str,
) -> dict[str, Any]:
    raw_status = str(record.get("status", "failed"))
    rollback_status = str(record.get("rollback_status", ""))
    if raw_status == "activated":
        relay_status = "succeeded"
    elif rollback_status == "rolled_back":
        relay_status = "rolled_back"
    else:
        relay_status = "failed"
    acceptance = record.get("acceptance_result") if isinstance(record.get("acceptance_result"), dict) else {}
    return {
        "authorizationId": authorization_id,
        "status": relay_status,
        "message": record.get("failure_reason") or f"deploy agent status: {raw_status}",
        "commitSha": record.get("commit_sha"),
        "healthStatus": "passed" if acceptance.get("passed") else "failed",
        "rollbackReason": record.get("failure_reason") if relay_status == "rolled_back" else None,
        "metadata": {
            "deployRecord": record,
            "operatorRunIdentity": record.get("operator_run_identity"),
            "approvalInteractionId": record.get("approval_interaction_id"),
            "deployTokenScope": record.get("deploy_token_scope"),
            "imageDigest": record.get("image_digest"),
            "signatureVerificationResult": record.get("signature_verification_result"),
            "onBoxReverificationResult": record.get("on_box_reverification_result"),
            "rollbackStatus": record.get("rollback_status"),
            "activatedServiceVersion": record.get("activated_service_version"),
            "terminalReceiptSha256": record.get("terminal_receipt_sha256"),
            "releaseIdentity": record.get("release_identity"),
        },
    }


def to_paperclip_deploy_receipt(entry: dict[str, Any]) -> dict[str, Any]:
    record = entry.get("record")
    if not isinstance(record, dict):
        raise DeployRejected("deploy receipt spool entry is missing its record")
    authorization_id = str(entry.get("lease_id", ""))
    candidate_id = str(entry.get("candidate_id", ""))
    if authorization_id != str(record.get("lease_id", "")) or candidate_id != str(record.get("candidate_id", "")):
        raise DeployRejected("deploy receipt spool binding mismatch")
    payload = to_paperclip_deploy_record(record, authorization_id=authorization_id)
    payload.pop("authorizationId", None)
    payload["candidateId"] = candidate_id
    return payload


def validate_acceptance_result(acceptance: dict[str, Any]) -> None:
    if not acceptance.get("passed"):
        return

    terminal_status = acceptance.get("terminal_status")
    if terminal_status not in {"complete", "partial"}:
        raise DeployRejected("acceptance result missing terminal_status complete/partial")

    pins = acceptance.get("scan_log_pins")
    if not isinstance(pins, dict):
        raise DeployRejected("acceptance result missing scan_log_pins")
    missing_pins = [key for key in REQUIRED_SCAN_LOG_PINS if not pins.get(key)]
    if missing_pins:
        raise DeployRejected(f"acceptance result missing scan_log pin(s): {', '.join(missing_pins)}")
    for key in ("snapshot_hash", "probe_set_hash"):
        if not str(pins[key]).startswith("sha256:"):
            raise DeployRejected(f"acceptance scan_log pin {key} must be sha256-prefixed")

    envelope = acceptance.get("customer_envelope")
    if not isinstance(envelope, dict):
        raise DeployRejected("acceptance result missing customer_envelope")
    missing_envelope = [key for key in REQUIRED_CUSTOMER_ENVELOPE_FIELDS if not envelope.get(key)]
    if missing_envelope:
        raise DeployRejected(f"acceptance result missing customer envelope field(s): {', '.join(missing_envelope)}")

    components = acceptance.get("components")
    if not isinstance(components, list) or not components:
        raise DeployRejected("acceptance result missing components")
    failed = [
        str(component.get("name", "<unnamed>"))
        for component in components
        if not isinstance(component, dict) or component.get("passed") is not True
    ]
    if failed:
        raise DeployRejected(f"acceptance component(s) did not pass: {', '.join(failed)}")


def parse_instant(value: str) -> dt.datetime:
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_release_identity(identity: dict[str, Any]) -> None:
    missing = [field for field in RELEASE_IDENTITY_FIELDS if not identity.get(field)]
    if missing:
        raise DeployRejected(f"release identity missing field(s): {', '.join(missing)}")
    commit_sha = str(identity.get("commit_sha", ""))
    image_digest = str(identity.get("image_digest", ""))
    if identity["configured_image"] != image_digest:
        raise DeployRejected("release identity mismatch: configured_image != image_digest")
    for field in ("running_container_revision", "internal_version_sha", "current_version"):
        if identity[field] != commit_sha:
            raise DeployRejected(f"release identity mismatch: {field} != commit_sha")
    sequence = identity.get("last_activated_sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        raise DeployRejected("release identity last_activated_sequence must be an integer")
    if not str(identity["terminal_receipt_sha256"]).startswith("sha256:"):
        raise DeployRejected("release identity terminal_receipt_sha256 must be sha256-prefixed")


def _sha256_stream(handle: Any) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _read_archive_json(archive: tarfile.TarFile, name: str, *, max_bytes: int) -> Any:
    try:
        member = archive.getmember(name)
    except KeyError as exc:
        raise DeployRejected(f"offline Docker archive missing {name}") from exc
    if not member.isfile() or member.size > max_bytes:
        raise DeployRejected(f"offline Docker archive has invalid {name}")
    handle = archive.extractfile(member)
    if handle is None:
        raise DeployRejected(f"offline Docker archive cannot read {name}")
    try:
        return json.loads(handle.read(max_bytes + 1).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeployRejected(f"offline Docker archive has invalid JSON in {name}") from exc


def verify_docker_archive_identity(tarball_path: Path, candidate: Candidate) -> tuple[str, str]:
    expected_tag = f"backbond-scanner-live:{candidate.commit_sha}"
    try:
        archive = tarfile.open(tarball_path, mode="r:*")
    except (tarfile.TarError, OSError) as exc:
        raise DeployRejected("candidate artifact is not a readable Docker archive") from exc
    with archive:
        seen: set[str] = set()
        for member in archive.getmembers():
            pure = PurePosixPath(member.name)
            if member.name in seen or pure.is_absolute() or ".." in pure.parts or "\\" in member.name:
                raise DeployRejected("offline Docker archive contains an unsafe or duplicate member")
            if not (member.isfile() or member.isdir()):
                raise DeployRejected("offline Docker archive contains a non-regular member")
            seen.add(member.name)

        manifest = _read_archive_json(archive, "manifest.json", max_bytes=1024 * 1024)
        if not isinstance(manifest, list) or len(manifest) != 1 or not isinstance(manifest[0], dict):
            raise DeployRejected("offline Docker archive must contain exactly one image manifest")
        image = manifest[0]
        if image.get("RepoTags") != [expected_tag]:
            raise DeployRejected("offline Docker archive tag does not match candidate commit")
        config_name = image.get("Config")
        layer_names = image.get("Layers")
        blob_pattern = re.compile(r"blobs/sha256/([0-9a-f]{64})")
        config_match = blob_pattern.fullmatch(str(config_name))
        if config_match is None or not isinstance(layer_names, list) or not layer_names:
            raise DeployRejected("offline Docker archive manifest has invalid config or layers")
        if len(set(map(str, layer_names))) != len(layer_names):
            raise DeployRejected("offline Docker archive manifest repeats a layer")
        config = _read_archive_json(archive, str(config_name), max_bytes=4 * 1024 * 1024)
        if not isinstance(config, dict):
            raise DeployRejected("offline Docker image config must be an object")
        config_handle = archive.extractfile(archive.getmember(str(config_name)))
        if config_handle is None or _sha256_stream(config_handle) != config_match.group(1):
            raise DeployRejected("offline Docker image config digest does not match its content")
        labels = config.get("config", {}).get("Labels", {})
        if labels.get("org.opencontainers.image.revision") != candidate.commit_sha:
            raise DeployRejected("offline Docker image revision label does not match candidate commit")
        if config.get("os") != "linux" or config.get("architecture") != "amd64":
            raise DeployRejected("offline Docker image platform must be linux/amd64")
        diff_ids = config.get("rootfs", {}).get("diff_ids")
        expected_diff_ids: list[str] = []
        for layer_name in layer_names:
            layer_match = blob_pattern.fullmatch(str(layer_name))
            if layer_match is None:
                raise DeployRejected("offline Docker archive layer path is invalid")
            layer_handle = archive.extractfile(archive.getmember(str(layer_name)))
            if layer_handle is None or _sha256_stream(layer_handle) != layer_match.group(1):
                raise DeployRejected("offline Docker layer digest does not match its content")
            expected_diff_ids.append(f"sha256:{layer_match.group(1)}")
        if diff_ids != expected_diff_ids:
            raise DeployRejected("offline Docker image rootfs does not match archive layers")
        return expected_tag, f"sha256:{config_match.group(1)}"


def verify_loaded_image_matches_archive(
    tarball_path: Path,
    candidate: Candidate,
    loaded: dict[str, Any],
) -> str:
    try:
        archive = tarfile.open(tarball_path, mode="r:*")
    except (tarfile.TarError, OSError) as exc:
        raise DeployRejected("candidate artifact is not a readable Docker archive") from exc
    with archive:
        manifest = _read_archive_json(archive, "manifest.json", max_bytes=1024 * 1024)
        image = manifest[0]
        config = _read_archive_json(archive, str(image["Config"]), max_bytes=4 * 1024 * 1024)

    loaded_id = str(loaded.get("Id", ""))
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", loaded_id):
        raise DeployRejected("loaded Docker image ID is invalid")
    if loaded.get("Os") != config.get("os") or loaded.get("Architecture") != config.get("architecture"):
        raise DeployRejected("loaded Docker image platform does not match the verified archive")
    if loaded.get("RootFS", {}).get("Layers") != config.get("rootfs", {}).get("diff_ids"):
        raise DeployRejected("loaded Docker image rootfs does not match the verified archive")
    archive_runtime = config.get("config")
    loaded_runtime = loaded.get("Config")
    if not isinstance(archive_runtime, dict) or not isinstance(loaded_runtime, dict):
        raise DeployRejected("loaded Docker image runtime config is invalid")
    for key, value in archive_runtime.items():
        if loaded_runtime.get(key) != value:
            raise DeployRejected(f"loaded Docker image runtime field {key} does not match the verified archive")
    if loaded_runtime.get("Labels", {}).get("org.opencontainers.image.revision") != candidate.commit_sha:
        raise DeployRejected("loaded Docker image revision label does not match candidate commit")
    return loaded_id


def download_tarball(url: str, dest: Path, opener: Callable[[str, Path], None] | None = None) -> Path:
    if opener:
        opener(url, dest)
        return dest
    if not (url.startswith("https://") or url.startswith("file://")):
        raise DeployRejected("candidate artifact URL must use https:// or file://")
    if url.startswith("file://"):
        source = Path(url.removeprefix("file://"))
        with source.open("rb") as source_handle, dest.open("wb") as dest_handle:
            shutil.copyfileobj(source_handle, dest_handle, length=1024 * 1024)
        return dest
    request = urllib.request.Request(url, method="GET")
    with _urlopen_no_redirect(request, timeout=60) as response, dest.open("wb") as dest_handle:
        shutil.copyfileobj(response, dest_handle, length=1024 * 1024)
    return dest


def verify_candidate_and_lease(
    candidate: Candidate,
    lease: DeployLease,
    config: DeployConfig,
    now: dt.datetime | None = None,
) -> None:
    now = now or dt.datetime.now(UTC)
    if lease.candidate_id != candidate.candidate_id:
        raise DeployRejected("lease candidate_id does not match candidate")
    if lease.environment != config.environment or candidate.environment != config.environment:
        raise DeployRejected("lease/candidate environment does not match this deploy agent")
    if lease.host != config.host or candidate.host != config.host:
        raise DeployRejected("lease/candidate host does not match this deploy agent")
    if lease.sequence != candidate.sequence:
        raise DeployRejected("lease sequence does not match candidate")
    if lease.token_scope != candidate.deploy_token_scope:
        raise DeployRejected("lease token scope does not match candidate deploy_token_scope")
    expected_receipt = _authorization_api_path(lease.lease_id, "deploy-record-receipt")
    if lease.record_receipt_path != expected_receipt:
        raise DeployRejected("deploy receipt path is not bound to this authorization")
    sequence_file = config.state_dir / "last_activated_sequence"
    if sequence_file.exists() and candidate.sequence <= int(sequence_file.read_text(encoding="utf-8").strip()):
        raise DeployRejected("candidate sequence is stale or replayed")
    if parse_instant(lease.expires_at) <= now or parse_instant(candidate.expires_at) <= now:
        raise DeployRejected("lease or candidate is expired")


def build_record(
    *,
    candidate: Candidate,
    lease: DeployLease,
    operator_run_identity: str,
    status: str,
    signature_verification_result: str,
    on_box_reverification_result: str,
    acceptance_result: dict[str, Any] | None,
    rollback_status: str,
    activated_service_version: str | None,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "lease_id": lease.lease_id,
        "commit_sha": candidate.commit_sha,
        "image_digest": candidate.image_digest,
        "signature_verification_result": signature_verification_result,
        "approval_interaction_id": candidate.approval_interaction_id,
        "deploy_token_scope": candidate.deploy_token_scope,
        "on_box_reverification_result": on_box_reverification_result,
        "acceptance_result": acceptance_result or {"passed": False, "skipped": True},
        "rollback_status": rollback_status,
        "activated_service_version": activated_service_version,
        "operator_run_identity": operator_run_identity,
        "status": status,
        "failure_reason": failure_reason,
        "recorded_at": dt.datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def _spool_path(config: DeployConfig, candidate: Candidate) -> Path:
    return config.state_dir / "deploy-record-spool" / f"{candidate.sequence:020d}-{candidate.candidate_id}.json"


def _spool_entry(lease: DeployLease, candidate: Candidate, record: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 1, "receipt_path": lease.record_receipt_path, "lease_id": lease.lease_id, "candidate_id": candidate.candidate_id, "record": record}


def _fsync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _atomic_write_text(path: Path, value: str, *, default_mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else default_mode
    try:
        with temp.open("w", encoding="utf-8") as handle:
            os.chmod(temp, mode)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        _fsync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(payload, sort_keys=True), default_mode=0o600)


def _atomic_copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with source.open("rb") as source_handle, temp.open("wb") as target_handle:
            os.chmod(temp, 0o600)
            shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        os.replace(temp, target)
        _fsync_directory(target.parent)
    finally:
        temp.unlink(missing_ok=True)


def _atomic_unlink(path: Path) -> None:
    if path.exists():
        path.unlink()
        _fsync_directory(path.parent)


def _replace_env_assignment(content: str, key: str, value: str) -> str:
    if "\n" in value or "\r" in value:
        raise DeployRejected(f"{key} contains a newline")
    assignment = f"{key}={value}\n"
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}=")
    output: list[str] = []
    replaced = False
    for line in content.splitlines(keepends=True):
        if pattern.match(line):
            if not replaced:
                output.append(assignment)
                replaced = True
            continue
        output.append(line)
    if not replaced:
        if output and not output[-1].endswith(("\n", "\r")):
            output[-1] += "\n"
        output.append(assignment)
    return "".join(output)


@contextmanager
def verified_candidate_artifact(
    *,
    candidate: Candidate,
    lease: DeployLease,
    config: DeployConfig,
    signature_verifier: Callable[[Candidate, Path, Path], bool],
    opener: Callable[[str, Path], None] | None,
    now: dt.datetime | None,
) -> Iterator[Path]:
    verify_candidate_and_lease(candidate, lease, config, now=now)
    with tempfile.TemporaryDirectory(prefix="backbond-deploy-") as tmp:
        tarball = download_tarball(candidate.tarball_url, Path(tmp) / "candidate.tar", opener=opener)
        if sha256_file(tarball) != candidate.tarball_sha256:
            raise DeployRejected("candidate tarball sha256 does not match Paperclip candidate")
        signature_bundle = download_tarball(candidate.signature_ref, Path(tmp) / "signature.bundle", opener=opener)
        if sha256_file(signature_bundle) != candidate.signature_sha256:
            raise DeployRejected("candidate signature bundle sha256 does not match Paperclip candidate")
        if not signature_verifier(candidate, tarball, signature_bundle):
            raise DeployRejected("candidate signature verification failed")
        yield tarball


def run_once(
    *,
    candidate: Candidate,
    lease: DeployLease,
    config: DeployConfig,
    activator: Activator,
    signature_verifier: Callable[[Candidate, Path, Path], bool],
    post_record: Callable[[dict[str, Any]], Any] | None = None,
    opener: Callable[[str, Path], None] | None = None,
    now: dt.datetime | None = None,
    operator_run_identity: str = "backbond-deploy-agent",
) -> dict[str, Any]:
    previous_version: Any = None
    spool_path = _spool_path(config, candidate)
    try:
        with verified_candidate_artifact(candidate=candidate, lease=lease, config=config, signature_verifier=signature_verifier, opener=opener, now=now) as tarball:
            previous_version = activator.snapshot_current()
            pending = build_record(candidate=candidate, lease=lease, operator_run_identity=operator_run_identity, status="activation_pending", signature_verification_result="passed", on_box_reverification_result="passed", acceptance_result=None, rollback_status="not_needed", activated_service_version=None)
            _atomic_write_json(spool_path, _spool_entry(lease, candidate, pending))
            try:
                activated = activator.activate_tarball(tarball, candidate)
                acceptance = activator.run_acceptance(activated)
                validate_acceptance_result(acceptance)
                if not acceptance.get("passed"):
                    raise DeployRejected("acceptance failed")
                config.state_dir.mkdir(parents=True, exist_ok=True)
                _atomic_write_text(config.state_dir / "last_activated_sequence", str(candidate.sequence), default_mode=0o600)
                record = build_record(
                    candidate=candidate,
                    lease=lease,
                    operator_run_identity=operator_run_identity,
                    status="activated",
                    signature_verification_result="passed",
                    on_box_reverification_result="passed",
                    acceptance_result=acceptance,
                    rollback_status="not_needed",
                    activated_service_version=activated,
                )
                record["terminal_receipt_sha256"] = canonical_json_sha256(record)
                if hasattr(activator, "commit_release_identity"):
                    record["release_identity"] = activator.commit_release_identity(
                        candidate=candidate,
                        activated_version=activated,
                        terminal_receipt_sha256=record["terminal_receipt_sha256"],
                        status="activated",
                    )
                _atomic_write_json(spool_path, _spool_entry(lease, candidate, record))
            except Exception as exc:
                try:
                    rollback_status = activator.rollback(previous_version)
                    failure_reason = str(exc)
                except Exception as rollback_exc:
                    rollback_status = "rollback_failed"
                    failure_reason = f"{exc}; rollback failed: {rollback_exc}"
                record = build_record(candidate=candidate, lease=lease, operator_run_identity=operator_run_identity, status="failed", signature_verification_result="passed", on_box_reverification_result="passed", acceptance_result=locals().get("acceptance"), rollback_status=rollback_status, activated_service_version=(previous_version or {}).get("version") if isinstance(previous_version, dict) else previous_version, failure_reason=failure_reason)
                record["terminal_receipt_sha256"] = canonical_json_sha256(record)
                if rollback_status == "rolled_back" and hasattr(activator, "commit_release_identity"):
                    try:
                        record["release_identity"] = activator.commit_release_identity(
                            candidate=candidate,
                            activated_version=str((previous_version or {}).get("version") if isinstance(previous_version, dict) else previous_version),
                            terminal_receipt_sha256=record["terminal_receipt_sha256"],
                            status="rolled_back",
                        )
                    except DeployRejected as identity_exc:
                        record["rollback_status"] = "failed_closed"
                        record["failure_reason"] = f"{record['failure_reason']}; release identity rollback failed: {identity_exc}"
                _atomic_write_json(spool_path, _spool_entry(lease, candidate, record))
    except Exception as exc:
        record = build_record(
            candidate=candidate,
            lease=lease,
            operator_run_identity=operator_run_identity,
            status="rejected",
            signature_verification_result="not_run",
            on_box_reverification_result="failed",
            acceptance_result=None,
            rollback_status="not_needed",
            activated_service_version=previous_version,
            failure_reason=str(exc),
        )
        if not isinstance(exc, DeployRejected):
            record["status"] = "failed"
        record["terminal_receipt_sha256"] = canonical_json_sha256(record)
        _atomic_write_json(spool_path, _spool_entry(lease, candidate, record))
    if post_record:
        try:
            post_record(record)
            _atomic_unlink(spool_path)
            record["record_post_status"] = "posted"
        except Exception:
            record["record_post_status"] = "spooled_for_retry"
    return record


def verify_only(
    *,
    candidate: Candidate,
    lease: DeployLease,
    config: DeployConfig,
    signature_verifier: Callable[[Candidate, Path, Path], bool],
    opener: Callable[[str, Path], None] | None = None,
    now: dt.datetime | None = None,
    operator_run_identity: str = "backbond-deploy-agent-dry-run",
) -> dict[str, Any]:
    try:
        with verified_candidate_artifact(candidate=candidate, lease=lease, config=config, signature_verifier=signature_verifier, opener=opener, now=now):
            return build_record(candidate=candidate, lease=lease, operator_run_identity=operator_run_identity, status="verified", signature_verification_result="passed", on_box_reverification_result="passed", acceptance_result=None, rollback_status="not_needed", activated_service_version=None)
    except Exception as exc:
        record = build_record(candidate=candidate, lease=lease, operator_run_identity=operator_run_identity, status="rejected", signature_verification_result="not_run", on_box_reverification_result="failed", acceptance_result=None, rollback_status="not_needed", activated_service_version=None, failure_reason=str(exc))
        if not isinstance(exc, DeployRejected):
            record["status"] = "failed"
        return record


def cosign_verify(candidate: Candidate, tarball_path: Path, bundle: Path) -> bool:
    del tarball_path
    if not re.fullmatch(r"[^\s/@]+(?:/[^\s/@]+)+@sha256:[0-9a-f]{64}", candidate.image_digest):
        return False
    if not TRUSTED_ROOT_PATH.is_file():
        return False
    image_sha256 = candidate.image_digest.rsplit("@sha256:", 1)[1]
    completed = subprocess.run([
        "cosign", "verify-blob-attestation", "--bundle", str(bundle),
        "--trusted-root", str(TRUSTED_ROOT_PATH),
        "--certificate-identity", EXPECTED_SIGNER_IDENTITY,
        "--certificate-oidc-issuer", EXPECTED_OIDC_ISSUER,
        "--type", EXPECTED_SIGNATURE_PREDICATE_TYPE,
        "--digest", image_sha256,
        "--digestAlg", "sha256",
    ], check=False, text=True, capture_output=True)
    return completed.returncode == 0


def retry_spooled_records(config: DeployConfig, client: PaperclipClient) -> None:
    spool_dir = config.state_dir / "deploy-record-spool"
    if not spool_dir.exists():
        return
    quarantine = spool_dir / "quarantine"
    for path in sorted(spool_dir.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            record = entry.get("record", {})
            if entry.get("schema_version") != 1 or not str(entry.get("receipt_path", "")).startswith("/api/"):
                raise ValueError("invalid spool schema")
            expected_receipt = _authorization_api_path(str(entry.get("lease_id", "")), "deploy-record-receipt")
            if entry.get("receipt_path") != expected_receipt:
                raise ValueError("spool receipt path is not authorization-bound")
            if entry.get("lease_id") != record.get("lease_id") or entry.get("candidate_id") != record.get("candidate_id"):
                raise ValueError("spool binding mismatch")
            status = record.get("status")
            if status == "activation_pending":
                raise DeployRejected(f"unresolved activation_pending deploy record requires reconciliation: {path.name}")
            if status not in {"activated", "failed", "rejected"}:
                raise ValueError("spool record is not terminal")
        except (OSError, ValueError, json.JSONDecodeError, AttributeError):
            quarantine.mkdir(parents=True, exist_ok=True)
            os.replace(path, quarantine / path.name)
            continue
        client.post_receipt(entry["receipt_path"], entry)
        _atomic_unlink(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BackBond Scanner approved deploy lease agent")
    parser.add_argument("--candidate-json", type=Path)
    parser.add_argument("--lease-json", type=Path)
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/backbond-deploy-agent"))
    parser.add_argument("--environment", default=os.environ.get("BACKBOND_DEPLOY_ENV", "production"))
    parser.add_argument("--host", default=os.environ.get("BACKBOND_DEPLOY_HOST", socket.gethostname()))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--deploy-token-credential", type=Path)
    parser.add_argument("--paperclip-api-key-credential", type=Path)
    parser.add_argument("--deploy-authorization-id-credential", type=Path)
    args = parser.parse_args(argv)

    config = DeployConfig(environment=args.environment, host=args.host, state_dir=args.state_dir)
    activator: SystemdDockerActivator | None = None
    credential_path = args.deploy_token_credential
    client: PaperclipClient | None = None
    deploy_token = credential_path.read_text(encoding="utf-8").splitlines()[0] if credential_path else None
    api_key = args.paperclip_api_key_credential.read_text(encoding="utf-8").splitlines()[0] if args.paperclip_api_key_credential else None
    authorization_id = args.deploy_authorization_id_credential.read_text(encoding="utf-8").splitlines()[0] if args.deploy_authorization_id_credential else None
    if bool(args.candidate_json) != bool(args.lease_json):
        raise DeployRejected("--candidate-json and --lease-json must be provided together")
    if args.candidate_json and args.lease_json:
        candidate = Candidate.from_mapping(json.loads(args.candidate_json.read_text(encoding="utf-8")))
        release_identity_evidence(REPO_ROOT, build_sha=candidate.commit_sha)
        lease = DeployLease.from_mapping(json.loads(args.lease_json.read_text(encoding="utf-8")))
        if not args.dry_run:
            raise DeployRejected("local candidate/lease JSON inputs are verification-only and require --dry-run")
    else:
        if not deploy_token or not api_key or not authorization_id:
            raise DeployRejected("deploy token, Paperclip API key, and authorization id credentials are required for live mode")
        client = PaperclipClient(
            api_url=os.environ["PAPERCLIP_API_URL"],
            token=api_key,
            run_id=os.environ.get("PAPERCLIP_RUN_ID"),
            approved_lease_path_template=os.environ.get(
                "BACKBOND_DEPLOY_LEASE_PATH_TEMPLATE",
                DEFAULT_APPROVED_LEASE_PATH_TEMPLATE,
            ),
            deploy_record_path=os.environ.get("BACKBOND_DEPLOY_RECORD_PATH", DEFAULT_DEPLOY_RECORD_PATH),
            deploy_authorization_id=authorization_id,
            deploy_authorization_token=deploy_token,
        )
        if not args.dry_run:
            activator = SystemdDockerActivator(config)
            activator.recover_pending_release_identity()
            retry_spooled_records(config, client)
        lease, candidate = client.poll_approved_lease(config.host, config.environment)
        release_identity_evidence(REPO_ROOT, build_sha=candidate.commit_sha)

    opener = client.download_staged if client is not None else None
    if args.dry_run:
        record = verify_only(
            candidate=candidate,
            lease=lease,
            config=config,
            signature_verifier=cosign_verify,
            opener=opener,
            operator_run_identity=os.environ.get("PAPERCLIP_RUN_ID", "manual-on-box-dry-run"),
        )
        print(json.dumps(record, indent=2, sort_keys=True))
        return 0 if record["status"] == "verified" else 2

    activator = activator or SystemdDockerActivator(config)
    post_record = None if client is None else client.post_deploy_record
    record = run_once(
        candidate=candidate,
        lease=lease,
        config=config,
        activator=activator,
        signature_verifier=cosign_verify,
        post_record=post_record,
        opener=opener,
        operator_run_identity=os.environ.get("PAPERCLIP_RUN_ID", "manual-on-box"),
    )
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0 if record["status"] == "activated" else 2


if __name__ == "__main__":
    raise SystemExit(main())
