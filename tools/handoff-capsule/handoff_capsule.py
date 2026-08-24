#!/usr/bin/env python3
"""Build, publish, fetch, and verify durable Paperclip Handoff Capsules.

The capsule is deliberately independent of a shared Git remote.  A code capsule
contains a self-contained Git bundle plus any declared test/evidence files.  The
compressed payload is split below Paperclip's attachment limit, uploaded to the
producer issue, and addressed by a small attachment-backed work product.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile


SCHEMA = "paperclip.handoff-capsule/v1"
INDEX_SCHEMA = "paperclip.handoff-capsule-index/v1"
DEFAULT_API = "http://127.0.0.1:3100"
DEFAULT_PART_BYTES = 8 * 1024 * 1024
FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)


class CapsuleError(RuntimeError):
    pass


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(command: list[str], cwd: Path | None = None, *, capture: bool = True) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        raise CapsuleError(f"command failed: {' '.join(command)}\n{stderr.strip()}") from exc
    return result.stdout if capture else ""


def safe_name(value: str) -> str:
    name = value.replace("\\", "/").strip("/")
    if not name or name.startswith(".") or ".." in Path(name).parts:
        raise CapsuleError(f"unsafe capsule path: {value!r}")
    return name


def parse_named_file(value: str) -> tuple[str, Path]:
    if "=" in value:
        name, raw_path = value.split("=", 1)
    else:
        raw_path = value
        name = Path(value).name
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise CapsuleError(f"artifact does not exist: {path}")
    return safe_name(name), path


def write_deterministic_zip(path: Path, files: list[tuple[str, Path]], manifest: dict[str, object]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        manifest_info = zipfile.ZipInfo("handoff-manifest-v1.json", FIXED_ZIP_TIME)
        manifest_info.external_attr = 0o100644 << 16
        archive.writestr(manifest_info, canonical_json(manifest) + b"\n")
        for name, source in sorted(files, key=lambda item: item[0]):
            info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
            info.external_attr = 0o100644 << 16
            with source.open("rb") as handle:
                archive.writestr(info, handle.read(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def split_file(path: Path, output_dir: Path, capsule_id: str, part_bytes: int) -> list[dict[str, object]]:
    if part_bytes < 1024:
        raise CapsuleError("part size must be at least 1024 bytes")
    parts: list[dict[str, object]] = []
    with path.open("rb") as source:
        index = 0
        while True:
            data = source.read(part_bytes)
            if not data:
                break
            name = f"handoff-{capsule_id}.zip.part-{index:03d}"
            target = output_dir / name
            target.write_bytes(data)
            parts.append({"name": name, "byte_size": len(data), "sha256": sha256_bytes(data)})
            index += 1
    if not parts:
        raise CapsuleError("capsule archive was empty")
    return parts


def git_payload(repo: Path, candidate: str, base: str | None, staging: Path) -> tuple[dict[str, object], list[tuple[str, Path]]]:
    repo = repo.resolve()
    candidate_sha = run(["git", "rev-parse", f"{candidate}^{{commit}}"], repo).strip()
    base_sha = run(["git", "rev-parse", f"{base}^{{commit}}"], repo).strip() if base else None
    mirror = staging / "repo.git"
    run(["git", "clone", "--mirror", "--no-hardlinks", str(repo), str(mirror)])
    bundle_ref = f"refs/handoff/{candidate_sha}"
    run(["git", "update-ref", bundle_ref, candidate_sha], mirror)
    bundle = staging / "candidate.bundle"
    run(["git", "bundle", "create", str(bundle), bundle_ref], mirror)
    run(["git", "bundle", "verify", str(bundle)], mirror)
    patch = staging / "candidate.patch"
    if base_sha:
        patch.write_bytes(subprocess.check_output(["git", "diff", "--binary", f"{base_sha}..{candidate_sha}"], cwd=repo))
    else:
        patch.write_bytes(subprocess.check_output(["git", "show", "--binary", "--format=fuller", candidate_sha], cwd=repo))
    show = staging / "candidate.txt"
    show.write_text(run(["git", "show", "--stat", "--format=fuller", candidate_sha], repo), encoding="utf-8")
    metadata: dict[str, object] = {
        "candidate_ref": candidate,
        "candidate_sha": candidate_sha,
        "base_ref": base,
        "base_sha": base_sha,
        "bundle_ref": bundle_ref,
        "bundle_path": "git/candidate.bundle",
        "patch_path": "git/candidate.patch",
        "show_path": "git/candidate.txt",
    }
    return metadata, [
        ("git/candidate.bundle", bundle),
        ("git/candidate.patch", patch),
        ("git/candidate.txt", show),
    ]


def build_capsule(
    *,
    output_dir: Path,
    producer_issue: str,
    qa_issue: str,
    artifacts: list[str],
    repo: Path | None = None,
    candidate: str | None = None,
    base: str | None = None,
    commands: list[str] | None = None,
    prerequisites: list[str] | None = None,
    created_at: str | None = None,
    part_bytes: int = DEFAULT_PART_BYTES,
) -> Path:
    if producer_issue.strip().upper() == qa_issue.strip().upper():
        raise CapsuleError("producer and reviewer issues must be different")
    output_dir.mkdir(parents=True, exist_ok=True)
    named_files = [parse_named_file(value) for value in artifacts]
    names = [name for name, _ in named_files]
    if len(names) != len(set(names)):
        raise CapsuleError("duplicate artifact names")
    with tempfile.TemporaryDirectory(prefix="handoff-capsule-") as raw_staging:
        staging = Path(raw_staging)
        payloads: list[tuple[str, Path]] = [(f"artifacts/{name}", path) for name, path in named_files]
        git_metadata: dict[str, object] | None = None
        if repo or candidate or base:
            if repo is None or candidate is None:
                raise CapsuleError("--repo and --candidate must be supplied together")
            git_metadata, git_files = git_payload(repo, candidate, base, staging)
            payloads.extend(git_files)
        if not payloads:
            raise CapsuleError("capsule must contain at least one artifact or Git candidate")
        file_records = [
            {
                "path": name,
                "byte_size": source.stat().st_size,
                "sha256": sha256_file(source),
                "media_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
            }
            for name, source in sorted(payloads, key=lambda item: item[0])
        ]
        unsigned_manifest: dict[str, object] = {
            "schema": SCHEMA,
            "created_at": created_at or utc_now(),
            "producer_issue": producer_issue,
            "qa_issue": qa_issue,
            "git": git_metadata,
            "files": file_records,
            "verification_commands": list(commands or []),
            "prerequisite_capsule_ids": list(prerequisites or []),
            "policy": {
                "free_text_sha_is_deliverable": False,
                "exact_bytes_required": True,
                "independent_review_required": True,
            },
        }
        capsule_id = sha256_bytes(canonical_json(unsigned_manifest))
        manifest = dict(unsigned_manifest)
        manifest["capsule_id"] = capsule_id
        manifest_sha = sha256_bytes(canonical_json(manifest))
        archive = staging / f"handoff-{capsule_id}.zip"
        write_deterministic_zip(archive, payloads, manifest)
        parts = split_file(archive, output_dir, capsule_id, part_bytes)
        index: dict[str, object] = {
            "schema": INDEX_SCHEMA,
            "capsule_id": capsule_id,
            "producer_issue": producer_issue,
            "qa_issue": qa_issue,
            "manifest_sha256": manifest_sha,
            "archive_sha256": sha256_file(archive),
            "archive_byte_size": archive.stat().st_size,
            "candidate_sha": git_metadata.get("candidate_sha") if git_metadata else None,
            "parts": parts,
        }
        index_path = output_dir / f"handoff-{capsule_id}.index.json"
        index_path.write_bytes(canonical_json(index) + b"\n")
    return index_path


def load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CapsuleError(f"expected JSON object: {path}")
    return value


def verify_index(index_path: Path, *, extract_dir: Path | None = None, verify_git: bool = True) -> dict[str, object]:
    index = load_json(index_path)
    if index.get("schema") != INDEX_SCHEMA:
        raise CapsuleError("unsupported capsule index schema")
    capsule_id = str(index.get("capsule_id") or "")
    parts = index.get("parts")
    if not capsule_id or not isinstance(parts, list) or not parts:
        raise CapsuleError("index is missing capsule id or parts")
    with tempfile.TemporaryDirectory(prefix="handoff-verify-") as raw_temp:
        temp = Path(raw_temp)
        archive = temp / "capsule.zip"
        with archive.open("wb") as output:
            for record in parts:
                if not isinstance(record, dict):
                    raise CapsuleError("invalid part record")
                name = safe_name(str(record.get("name") or ""))
                if Path(name).name != name:
                    raise CapsuleError(f"capsule part must be a basename: {name}")
                part = index_path.parent / name
                if not part.is_file():
                    raise CapsuleError(f"missing capsule part: {part}")
                if part.stat().st_size != record.get("byte_size"):
                    raise CapsuleError(f"part size mismatch: {part.name}")
                actual = sha256_file(part)
                if actual != record.get("sha256"):
                    raise CapsuleError(f"part checksum mismatch: {part.name}")
                output.write(part.read_bytes())
        if archive.stat().st_size != index.get("archive_byte_size"):
            raise CapsuleError("archive size mismatch")
        if sha256_file(archive) != index.get("archive_sha256"):
            raise CapsuleError("archive checksum mismatch")
        unpack = temp / "unpacked"
        unpack.mkdir()
        with zipfile.ZipFile(archive) as capsule:
            names: set[str] = set()
            for member in capsule.infolist():
                name = safe_name(member.filename)
                if name in names:
                    raise CapsuleError(f"duplicate ZIP member: {name}")
                names.add(name)
                destination = (unpack / member.filename).resolve()
                if unpack.resolve() not in destination.parents and destination != unpack.resolve():
                    raise CapsuleError(f"unsafe ZIP member: {member.filename}")
            capsule.extractall(unpack)
        manifest_path = unpack / "handoff-manifest-v1.json"
        manifest = load_json(manifest_path)
        if manifest.get("schema") != SCHEMA:
            raise CapsuleError("unsupported capsule manifest schema")
        if manifest.get("producer_issue") != index.get("producer_issue") or manifest.get("qa_issue") != index.get("qa_issue"):
            raise CapsuleError("index and manifest issue identities do not match")
        manifest_for_id = dict(manifest)
        actual_id = str(manifest_for_id.pop("capsule_id", ""))
        if actual_id != capsule_id or sha256_bytes(canonical_json(manifest_for_id)) != capsule_id:
            raise CapsuleError("capsule id does not match manifest")
        if sha256_bytes(canonical_json(manifest)) != index.get("manifest_sha256"):
            raise CapsuleError("manifest checksum mismatch")
        manifest_files = manifest.get("files", [])
        if not isinstance(manifest_files, list) or not manifest_files:
            raise CapsuleError("manifest contains no payload files")
        payload_names: set[str] = set()
        for record in manifest_files:
            if not isinstance(record, dict):
                raise CapsuleError("invalid manifest file record")
            name = safe_name(str(record.get("path") or ""))
            if name in payload_names:
                raise CapsuleError(f"duplicate manifest file record: {name}")
            payload_names.add(name)
            payload = (unpack / name).resolve()
            if unpack.resolve() not in payload.parents:
                raise CapsuleError(f"unsafe payload path: {name}")
            if not payload.is_file() or payload.stat().st_size != record.get("byte_size") or sha256_file(payload) != record.get("sha256"):
                raise CapsuleError(f"payload checksum mismatch: {record.get('path')}")
        git_metadata = manifest.get("git")
        if verify_git and isinstance(git_metadata, dict):
            bundle = unpack / str(git_metadata["bundle_path"])
            verify_repo = temp / "verify.git"
            run(["git", "init", "--bare", "-q", str(verify_repo)])
            run(["git", "bundle", "verify", str(bundle)], verify_repo)
        if extract_dir:
            extract_dir.mkdir(parents=True, exist_ok=True)
            for child in unpack.iterdir():
                target = extract_dir / child.name
                if child.is_dir():
                    shutil.copytree(child, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(child, target)
        return {
            "ok": True,
            "capsule_id": capsule_id,
            "candidate_sha": (git_metadata or {}).get("candidate_sha") if isinstance(git_metadata, dict) else None,
            "producer_issue": manifest.get("producer_issue"),
            "qa_issue": manifest.get("qa_issue"),
            "file_count": len(manifest.get("files", [])),
            "commands": manifest.get("verification_commands", []),
        }


class PaperclipClient:
    def __init__(self, api_url: str, company_id: str, api_key: str = "", run_id: str = "") -> None:
        self.api_url = api_url.rstrip("/")
        self.company_id = company_id
        self.api_key = api_key
        self.run_id = run_id

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.run_id:
            headers["X-Paperclip-Run-Id"] = self.run_id
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def request(self, method: str, path: str, body: dict[str, object] | None = None) -> object:
        data = canonical_json(body) if body is not None else None
        req = urllib.request.Request(
            f"{self.api_url}{path}", data=data, method=method,
            headers=self._headers("application/json" if data is not None else None),
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise CapsuleError(f"Paperclip {method} {path} failed ({exc.code}): {detail}") from exc
        return json.loads(payload) if payload else None

    def upload(self, issue_id: str, path: Path, content_type: str = "application/octet-stream") -> dict[str, object]:
        boundary = f"----handoff-{uuid.uuid4().hex}"
        header = (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
        body = header + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(
            f"{self.api_url}/api/companies/{self.company_id}/issues/{issue_id}/attachments",
            data=body,
            method="POST",
            headers=self._headers(f"multipart/form-data; boundary={boundary}"),
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise CapsuleError(f"attachment upload failed ({exc.code}): {detail}") from exc
        if not isinstance(result, dict) or not result.get("id"):
            raise CapsuleError("attachment upload returned no id")
        return result

    def download(self, path: str, destination: Path) -> None:
        req = urllib.request.Request(f"{self.api_url}{path}", headers=self._headers())
        with urllib.request.urlopen(req, timeout=120) as response, destination.open("wb") as output:
            shutil.copyfileobj(response, output)


def publish_index(client: PaperclipClient, index_path: Path, *, route: bool = False) -> dict[str, object]:
    # Never turn a locally tampered or incomplete capsule into a durable work
    # product. Publication is a trust boundary, not just an upload helper.
    verify_index(index_path)
    index = load_json(index_path)
    producer = str(index["producer_issue"])
    qa_issue = str(index["qa_issue"])
    published_path = index_path.with_name(index_path.stem.replace(".index", "") + ".published.index.json")
    resumed: dict[str, object] = {}
    if published_path.is_file():
        candidate = load_json(published_path)
        if candidate.get("capsule_id") == index.get("capsule_id"):
            resumed = candidate
    resumed_parts = {
        str(record.get("sha256")): record
        for record in resumed.get("parts", [])
        if isinstance(record, dict) and record.get("attachment_id") and record.get("sha256")
    }
    uploaded_parts: list[dict[str, object]] = []
    for record in index["parts"]:
        assert isinstance(record, dict)
        prior = resumed_parts.get(str(record.get("sha256")))
        if prior:
            uploaded_parts.append(prior)
            continue
        source = index_path.parent / str(record["name"])
        attachment = client.upload(producer, source)
        published = dict(record)
        published.update(
            {
                "attachment_id": attachment["id"],
                "content_path": attachment.get("contentPath"),
                "download_path": attachment.get("downloadPath") or f"/api/attachments/{attachment['id']}/content?download=1",
            }
        )
        uploaded_parts.append(published)
        checkpoint = dict(index)
        checkpoint["parts"] = uploaded_parts
        checkpoint["published_at"] = resumed.get("published_at") or utc_now()
        published_path.write_bytes(canonical_json(checkpoint) + b"\n")
    published_index = dict(index)
    published_index["parts"] = uploaded_parts
    published_index["published_at"] = resumed.get("published_at") or utc_now()
    published_index["publication_id"] = sha256_bytes(canonical_json(published_index))
    published_path.write_bytes(canonical_json(published_index) + b"\n")
    index_attachment_id = str(resumed.get("index_attachment_id") or "")
    if index_attachment_id:
        index_attachment: dict[str, object] = {"id": index_attachment_id}
    else:
        index_attachment = client.upload(producer, published_path, "application/json")
        published_index["index_attachment_id"] = index_attachment["id"]
        published_path.write_bytes(canonical_json(published_index) + b"\n")
    summary = {
        "schema": INDEX_SCHEMA,
        "capsule_id": index["capsule_id"],
        "producer_issue": producer,
        "qa_issue": qa_issue,
        "candidate_sha": index.get("candidate_sha"),
        "part_count": len(uploaded_parts),
        "index_attachment_id": index_attachment["id"],
    }
    work_product_body: dict[str, object] = {
        "type": "artifact",
        "provider": "paperclip",
        "title": f"Handoff Capsule v1 index {str(index['capsule_id'])[:12]}",
        "status": "ready_for_review",
        "reviewState": "none",
        "isPrimary": True,
        "healthStatus": "healthy",
        "summary": json.dumps(summary, sort_keys=True),
        "metadata": {"attachmentId": index_attachment["id"]},
    }
    if client.run_id:
        work_product_body["createdByRunId"] = client.run_id
    existing_products = client.request("GET", f"/api/issues/{producer}/work-products")
    work_product = None
    if isinstance(existing_products, list):
        for existing in existing_products:
            if not isinstance(existing, dict) or not str(existing.get("title") or "").startswith("Handoff Capsule v1 index "):
                continue
            try:
                existing_summary = json.loads(str(existing.get("summary") or "{}"))
            except json.JSONDecodeError:
                continue
            if existing_summary.get("capsule_id") == index.get("capsule_id"):
                work_product = existing
                break
    if work_product is None:
        work_product = client.request("POST", f"/api/issues/{producer}/work-products", work_product_body)
    comment = (
        f"Handoff Capsule v1 published for reviewer issue `{qa_issue}`.\n\n"
        f"- capsule_id: `{index['capsule_id']}`\n"
        f"- parts: `{len(uploaded_parts)}`\n"
        f"- index attachment: `/api/attachments/{index_attachment['id']}/content`\n\n"
        "This attachment-backed work product is the deliverable. A free-text SHA is not a substitute."
    )
    client.request("POST", f"/api/issues/{producer}/comments", {"body": comment})
    if route:
        producer_state = client.request("GET", f"/api/issues/{producer}")
        reviewer_state = client.request("GET", f"/api/issues/{qa_issue}")
        if not isinstance(producer_state, dict) or not isinstance(reviewer_state, dict):
            raise CapsuleError("could not load producer/reviewer routing state")
        producer_other_blockers = sorted(
            str(value.get("id"))
            for value in producer_state.get("blockedBy", [])
            if isinstance(value, dict) and value.get("id") and str(value.get("id")) != str(reviewer_state.get("id"))
        )
        reviewer_other_blockers = sorted(
            str(value.get("id"))
            for value in reviewer_state.get("blockedBy", [])
            if isinstance(value, dict) and value.get("id") and str(value.get("id")) != str(producer_state.get("id"))
        )
        client.request(
            "PATCH", f"/api/issues/{producer}",
            {
                "status": "blocked" if producer_other_blockers else "in_review",
                "blockedByIssueIds": producer_other_blockers,
                "comment": "Capsule published; routing to independent QA while preserving unrelated prerequisites.",
            },
        )
        client.request(
            "PATCH", f"/api/issues/{qa_issue}",
            {
                "status": "blocked" if reviewer_other_blockers else "todo",
                "blockedByIssueIds": reviewer_other_blockers,
                "comment": f"Durable capsule `{index['capsule_id']}` is available. Verify its bytes before reviewing.",
            },
        )
    return {"index_attachment": index_attachment, "work_product": work_product, "published_index": str(published_path)}


def fetch_index(client: PaperclipClient, attachment_id: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / f"attachment-{attachment_id}.published.index.json"
    client.download(f"/api/attachments/{attachment_id}/content", index_path)
    index = load_json(index_path)
    for record in index.get("parts", []):
        if not isinstance(record, dict):
            raise CapsuleError("invalid published part record")
        content_path = str(record.get("content_path") or "")
        if not content_path:
            attachment = str(record.get("attachment_id") or "")
            content_path = f"/api/attachments/{attachment}/content"
        client.download(content_path, output_dir / str(record["name"]))
    return index_path


def client_from_args(args: argparse.Namespace) -> PaperclipClient:
    company_id = str(args.company_id or "").strip()
    if not company_id:
        raise CapsuleError("--company-id or PAPERCLIP_COMPANY_ID is required")
    return PaperclipClient(
        args.api_url,
        company_id,
        args.api_key,
        args.run_id,
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build a local capsule")
    build.add_argument("--output-dir", required=True, type=Path)
    build.add_argument("--producer-issue", required=True)
    build.add_argument("--qa-issue", required=True)
    build.add_argument("--artifact", action="append", default=[], help="[archive/name=]PATH")
    build.add_argument("--repo", type=Path)
    build.add_argument("--candidate")
    build.add_argument("--base")
    build.add_argument("--verification-command", action="append", default=[])
    build.add_argument("--prerequisite-capsule", action="append", default=[])
    build.add_argument("--created-at")
    build.add_argument("--part-bytes", type=int, default=DEFAULT_PART_BYTES)

    verify = sub.add_parser("verify", help="verify a local or fetched capsule")
    verify.add_argument("index", type=Path)
    verify.add_argument("--extract-dir", type=Path)
    verify.add_argument("--no-git-verify", action="store_true")

    for name in ("publish", "fetch-verify"):
        item = sub.add_parser(name)
        item.add_argument("--api-url", default=os.environ.get("PAPERCLIP_API_URL", DEFAULT_API))
        item.add_argument("--company-id", default=os.environ.get("PAPERCLIP_COMPANY_ID"))
        item.add_argument("--api-key", default=os.environ.get("PAPERCLIP_API_KEY", ""))
        item.add_argument("--run-id", default=os.environ.get("PAPERCLIP_RUN_ID", ""))
        if name == "publish":
            item.add_argument("index", type=Path)
            item.add_argument("--route", action="store_true")
        else:
            item.add_argument("--index-attachment-id", required=True)
            item.add_argument("--output-dir", required=True, type=Path)
            item.add_argument("--extract-dir", type=Path)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "build":
            result = build_capsule(
                output_dir=args.output_dir,
                producer_issue=args.producer_issue,
                qa_issue=args.qa_issue,
                artifacts=args.artifact,
                repo=args.repo,
                candidate=args.candidate,
                base=args.base,
                commands=args.verification_command,
                prerequisites=args.prerequisite_capsule,
                created_at=args.created_at,
                part_bytes=args.part_bytes,
            )
            print(result)
        elif args.command == "verify":
            print(json.dumps(verify_index(args.index, extract_dir=args.extract_dir, verify_git=not args.no_git_verify), indent=2))
        elif args.command == "publish":
            print(json.dumps(publish_index(client_from_args(args), args.index, route=args.route), indent=2, default=str))
        elif args.command == "fetch-verify":
            index = fetch_index(client_from_args(args), args.index_attachment_id, args.output_dir)
            print(json.dumps(verify_index(index, extract_dir=args.extract_dir), indent=2))
    except (CapsuleError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"handoff-capsule: {exc}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
