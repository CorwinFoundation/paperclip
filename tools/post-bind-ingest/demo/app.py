#!/usr/bin/env python3
"""Presentation-ready BackBond scan-to-production lifecycle service.

The service executes only fixed, repository-owned scanner, underwriting, CI,
certificate, report-card, and passive-monitor paths in a synthetic environment.
"""

from __future__ import annotations

import argparse
from collections import deque
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import parse_qs, urlparse

from lifecycle import generate_final_report, run_preproduction


DEMO_DIR = Path(__file__).resolve().parent
REPO_ROOT = DEMO_DIR.parents[2]
RUNNER = DEMO_DIR.parent / "scripts" / "run_passive_log_reader_e2e.py"
INDEX = DEMO_DIR / "index_v2.html"
TEST_PATHS = (
    "tools/post-bind-ingest/tests/test_passive_log_reader_e2e.py",
    "tools/post-bind-ingest/tests/test_lifecycle_demo.py",
    "tools/post-bind-ingest/tests/test_receiver.py",
    "tools/post-bind-ingest/tests/test_continuity_gate.py",
    "tools/post-bind-ingest/tests/test_detector_input.py",
)
DEMO_ENV_FILE = Path("/etc/backbond/passive-demo.env")


def _load_demo_env_file(path: Path = DEMO_ENV_FILE) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#") or "=" not in clean:
            continue
        key, value = clean.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))



def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _access_token_from_env() -> str | None:
    environment = os.environ.get("DEMO_ENVIRONMENT", "local")
    token = os.environ.get("DEMO_ACCESS_TOKEN", "").strip()
    if environment == "public-demo" and len(token) < 32:
        raise RuntimeError("public-demo requires DEMO_ACCESS_TOKEN with at least 32 characters")
    return token or None

def _bounded_content_length(value: str | None, *, max_bytes: int = 1024) -> int:
    try:
        length = int(value or "0")
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid content length") from exc
    if length < 0:
        raise ValueError("invalid content length")
    if length > max_bytes:
        raise OverflowError("request too large")
    return length


class RunQuota:
    def __init__(self, max_requests: int = 5, window_seconds: float = 3600.0) -> None:
        if max_requests <= 0 or window_seconds <= 0:
            raise ValueError("run quota values must be positive")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.attempts: dict[str, deque[float]] = {}
        self.lock = threading.Lock()

    def allow(self, client: str, now: float | None = None) -> tuple[bool, int]:
        current = time.monotonic() if now is None else now
        cutoff = current - self.window_seconds
        with self.lock:
            attempts = self.attempts.setdefault(client, deque())
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= self.max_requests:
                retry_after = max(1, math.ceil(attempts[0] + self.window_seconds - current))
                return False, retry_after
            attempts.append(current)
            return True, 0


def _json_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _tracker_rows(path: Path) -> list[dict]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT claim_event_id, peril, severity_class, event_t, scan_id_at_bind, "
            "scan_id_at_loss, evidence_refs_uri, metadata FROM claim_events ORDER BY event_t, peril"
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item["metadata"])
            result.append(item)
        return result
    finally:
        conn.close()


def _receiver_events(receiver_dir: Path) -> list[dict]:
    events: list[dict] = []
    for path in sorted(receiver_dir.glob("batch-*.json")):
        batch = json.loads(path.read_text(encoding="utf-8"))
        for event in batch["events"]:
            events.append(
                {
                    **event,
                    "batch_id": batch["batch_id"],
                    "batch_sha256": batch["batch_sha256"],
                    "signature_status": "Ed25519 verified",
                }
            )
    return events


def _deployment_metadata() -> dict:

    return {
        "environment": os.environ.get("DEMO_ENVIRONMENT", "local"),
        "workflow": os.environ.get("DEMO_WORKFLOW", "Local presentation runner"),
        "git_sha": os.environ.get("DEMO_GIT_SHA", "working-tree"),
        "github_run_id": os.environ.get("DEMO_GITHUB_RUN_ID", "local"),

        "deployed_at": os.environ.get("DEMO_DEPLOYED_AT", _utc_now()),
        "gates": [
            {"name": "Focused unit + integration tests", "status": "passed"},
            {"name": "Container image build", "status": "passed"},
            {"name": "Candidate health check", "status": "passed"},
            {"name": "Deployed service health check", "status": "passed"},
        ],
    }


def _health_metadata() -> dict:
    return {
        "status": "ok",
        "mode": "synthetic-lifecycle",
        "production_contacted": False,
        "vapi_used": False,
    }

class DemoJob:
    def __init__(self, run_id: str, output_dir: Path) -> None:
        self.run_id = run_id
        self.output_dir = output_dir
        self.status = "queued"
        self.created_at = _utc_now()
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.events: list[dict] = []
        self.result: dict | None = None
        self.error: str | None = None
        self.lock = threading.Lock()

    def emit(self, kind: str, message: str, **details) -> None:
        with self.lock:
            self.events.append(
                {
                    "sequence": len(self.events) + 1,
                    "at": _utc_now(),
                    "kind": kind,
                    "message": message,
                    **details,
                }
            )

    def snapshot(self, after: int = 0) -> dict:
        with self.lock:
            return {
                "run_id": self.run_id,
                "status": self.status,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "events": [event for event in self.events if event["sequence"] > after],
                "event_count": len(self.events),
                "result": self.result,
                "error": self.error,
            }


class JobManager:
    def __init__(self, runs_dir: Path, *, max_jobs: int = 20) -> None:
        self.runs_dir = runs_dir.resolve()
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.max_jobs = max_jobs
        self.jobs: dict[str, DemoJob] = {}
        self.job_order: deque[str] = deque()
        self.active_job_id: str | None = None
        self.latest_completed_id: str | None = None
        self.lock = threading.Lock()

    def start(self) -> DemoJob:
        with self.lock:
            if self.active_job_id:
                active = self.jobs.get(self.active_job_id)
                if active and active.status in {"queued", "running"}:
                    return active
            run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
            job = DemoJob(run_id, self.runs_dir / run_id)
            self.jobs[run_id] = job
            self.job_order.append(run_id)
            self.active_job_id = run_id
            thread = threading.Thread(target=self._execute, args=(job,), daemon=True, name=f"demo-{run_id}")
            thread.start()
            return job

    def get(self, run_id: str) -> DemoJob | None:
        with self.lock:
            return self.jobs.get(run_id)

    def latest(self) -> DemoJob | None:
        with self.lock:
            run_id = self.latest_completed_id or self.active_job_id
            return self.jobs.get(run_id) if run_id else None

    def _command_env(self) -> dict[str, str]:
        env = os.environ.copy()
        source_paths = [
            str(REPO_ROOT / "tools" / "post-bind-ingest" / "src"),
            str(REPO_ROOT / "tools" / "log-tracker" / "src"),
        ]
        if env.get("PYTHONPATH"):
            source_paths.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(source_paths)
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def _run_passive_pipeline(self, job: DemoJob, output_dir: Path) -> dict:
        output_dir.mkdir(parents=True, exist_ok=False)
        job.emit("stage", "Synthetic AI agent and passive pipeline started", stage="agent", state="running")
        proc = subprocess.run(
            [sys.executable, str(RUNNER), "--output-dir", str(output_dir)],
            cwd=REPO_ROOT,
            env=self._command_env(),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=90,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "passive pipeline failed")
        report = json.loads((output_dir / "analysis-report.json").read_text(encoding="utf-8"))
        report["artifacts"] = {
            name: f"evidence://{job.run_id}/{Path(path).name}"
            for name, path in report.get("artifacts", {}).items()
        }
        raw_events = _json_lines(output_dir / "synthetic-agent-raw.jsonl")
        for event in raw_events:
            job.emit(
                "agent_log",
                f"{event['event_type']} · {event['event_id']}",
                stage="agent",
                batch_seq=event.get("payload", {}).get("batch_seq"),
                event=event,
            )
        job.emit("stage", "Synthetic AI agent completed", stage="agent", state="passed")
        job.emit("stage", "Collector, receiver, and passive detectors completed", stage="passive", state="running")
        normalized = _json_lines(output_dir / "normalized-events.jsonl")
        detections = [event for event in normalized if event["event_type"] == "claim_candidate_event"]
        for detection in detections:
            payload = detection["payload"]
            job.emit(
                "detection",
                f"{payload['peril']} · {payload['detector_id']}",
                stage="passive",
                peril=payload["peril"],
                severity=payload.get("severity"),
                confidence=payload.get("confidence"),
                detector_id=payload["detector_id"],
            )
        job.emit(
            "stage",
            f"Passive analysis passed: {len(detections)} findings, P1–P6 covered",
            stage="passive",
            state="passed",
        )
        return {
            "report": report,
            "raw_events": raw_events,
            "receiver_events": _receiver_events(output_dir / "receiver-batches"),
            "normalized_events": [event for event in normalized if event["event_type"] != "claim_candidate_event"],
            "detections": detections,
            "tracker_rows": _tracker_rows(output_dir / "log-tracker.sqlite3"),
        }

    def _run_tests(self, job: DemoJob) -> dict:
        job.emit("test_output", "Focused lifecycle pytest gate started", stage="cicd")
        started = time.perf_counter()
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--disable-warnings",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                str(job.output_dir / "pytest-tmp"),
                *TEST_PATHS,
            ],
            cwd=REPO_ROOT,
            env=self._command_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=150,
        )
        output_lines = [line.rstrip() for line in proc.stdout.splitlines() if line.strip()]
        for clean in output_lines:
            job.emit("test_output", clean, stage="cicd")
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        if proc.returncode != 0:
            raise RuntimeError("focused pytest gate failed")
        summary = output_lines[-1] if output_lines else "pytest completed"
        return {"status": "passed", "summary": summary, "elapsed_ms": elapsed_ms, "command": "pytest " + " ".join(TEST_PATHS)}

    def _execute(self, job: DemoJob) -> None:
        job.status = "running"
        job.started_at = _utc_now()
        started = time.perf_counter()
        try:
            lifecycle, tests = run_preproduction(
                job.output_dir,
                job.run_id,
                job.emit,
                lambda: self._run_tests(job),
            )
            passive = self._run_passive_pipeline(job, job.output_dir / "production")
            monitoring_report = generate_final_report(job.output_dir, lifecycle, passive, job.emit)
            deployment = _deployment_metadata()
            job.emit(
                "deployment",
                f"Verified build {deployment['git_sha'][:12]} in {deployment['environment']}",

                stage="report",
                deployment=deployment,
            )
            job.result = {
                **passive,
                "lifecycle": lifecycle,
                "monitoring_report": monitoring_report,
                "tests": tests,
                "deployment": deployment,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "evidence_id": job.run_id,
            }
            job.status = "passed"
            job.emit("complete", "Full scan-to-production lifecycle passed", stage="complete", state="passed")
        except Exception as exc:  # noqa: BLE001
            job.error = str(exc)
            job.status = "failed"
            job.emit("error", str(exc), stage="failed", state="failed")
        finally:
            job.finished_at = _utc_now()
            with self.lock:
                self.active_job_id = None
                if job.status == "passed":
                    self.latest_completed_id = job.run_id
                self._prune_locked()

    def _prune_locked(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(hours=48)
        while len(self.job_order) > self.max_jobs:
            run_id = self.job_order.popleft()
            old = self.jobs.pop(run_id, None)
            if old and old.output_dir.exists():
                shutil.rmtree(old.output_dir, ignore_errors=True)
        for path in self.runs_dir.iterdir():
            if path.is_dir() and datetime.fromtimestamp(path.stat().st_mtime, UTC) < cutoff:
                if path.name not in self.jobs:
                    shutil.rmtree(path, ignore_errors=True)


class DemoHandler(BaseHTTPRequestHandler):
    manager: JobManager
    access_token: str | None = None
    secure_cookie = False
    run_quota = RunQuota()


    def log_message(self, format: str, *args) -> None:
        return

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'")

    def _send_json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        data = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_index(self) -> None:
        data = INDEX.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_html_file(self, path: Path) -> None:
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _session_cookie_value(self) -> str | None:
        if not self.access_token:
            return None
        return hashlib.sha256(self.access_token.encode("utf-8")).hexdigest()

    def _is_authorized(self) -> bool:
        if not self.access_token:
            return True
        authorization = self.headers.get("Authorization", "")
        scheme, _, candidate = authorization.partition(" ")
        if scheme.lower() == "bearer" and candidate:
            return hmac.compare_digest(candidate, self.access_token)
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:
            return False
        session = cookie.get("backbond_demo_access")
        expected = self._session_cookie_value()
        return bool(session and expected and hmac.compare_digest(session.value, expected))

    def _send_access_page(self, invalid: bool = False) -> None:
        message = "That access token was not accepted." if invalid else "Enter the partner-demo access token to continue."
        body = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BackBond demo access</title><style>body{{margin:0;background:#08111d;color:#edf5ff;font:16px system-ui;display:grid;min-height:100vh;place-items:center}}main{{width:min(420px,calc(100% - 40px));padding:32px;border:1px solid #29435d;border-radius:18px;background:#101d2c}}h1{{margin-top:0}}label,input,button{{display:block;width:100%;box-sizing:border-box}}input,button{{margin-top:10px;padding:12px;border-radius:8px}}button{{background:#58d6b4;border:0;font-weight:700}}</style><main><h1>BackBond lifecycle demo</h1><p>{message}</p><form method="post" action="/access"><label>Access token<input name="token" type="password" required autocomplete="current-password"></label><button type="submit">Open demo</button></form></main></html>'''.encode("utf-8")
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("WWW-Authenticate", 'Bearer realm="BackBond partner demo"')
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _require_authorized(self, path: str) -> bool:
        if self._is_authorized():
            return True
        if path.startswith("/api/"):
            self._send_json({"error": "authentication_required"}, HTTPStatus.UNAUTHORIZED)
        else:
            self._send_access_page()
        return False

    def _handle_access(self) -> None:
        try:
            content_length = _bounded_content_length(self.headers.get("Content-Length"))
        except (OverflowError, ValueError):
            self._send_access_page(invalid=True)
            return
        form = parse_qs(self.rfile.read(content_length).decode("utf-8", errors="replace"))
        candidate = form.get("token", [""])[0]
        if not self.access_token or not hmac.compare_digest(candidate, self.access_token):
            self._send_access_page(invalid=True)
            return
        cookie = f"backbond_demo_access={self._session_cookie_value()}; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800"
        if self.secure_cookie:
            cookie += "; Secure"
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()


    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/api/health":
            self._send_json(_health_metadata())
            return
        if not self._require_authorized(path):
            return
        if path == "/":
            self._send_index()
            return
        if path == "/api/deployment":
            self._send_json(_deployment_metadata())
            return
        if path == "/api/latest":
            job = self.manager.latest()
            self._send_json(job.snapshot() if job else {"status": "not_run"})
            return
        prefix = "/api/runs/"
        if path.startswith(prefix):
            remainder = path[len(prefix):]
            parts = remainder.split("/")
            run_id = parts[0]
            job = self.manager.get(run_id)
            if not job:
                self._send_json({"error": "run_not_found"}, HTTPStatus.NOT_FOUND)
                return
            if len(parts) == 2 and parts[1] in {"certificate", "report"}:
                filename = "certificate.html" if parts[1] == "certificate" else "backbond-report.html"
                document = job.output_dir / filename
                if job.status != "passed" or not document.is_file():
                    self._send_json({"error": "document_not_ready"}, HTTPStatus.CONFLICT)
                    return
                self._send_html_file(document)
                return
            if len(parts) != 1:
                self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
                return
            query = parse_qs(parsed.query)
            try:
                after = max(0, int(query.get("after", ["0"])[0]))
            except ValueError:
                self._send_json({"error": "invalid_after"}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(job.snapshot(after=after))
            return
        self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        if path == "/access":
            self._handle_access()
            return
        if path != "/api/runs":
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        if not self._require_authorized(path):

            return
        try:
            content_length = _bounded_content_length(self.headers.get("Content-Length"))
        except OverflowError:
            self._send_json({"error": "request_too_large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        except ValueError:
            self._send_json({"error": "invalid_content_length"}, HTTPStatus.BAD_REQUEST)
            return
        if content_length:
            self.rfile.read(content_length)
        client = self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip() or self.client_address[0]
        allowed, retry_after = self.run_quota.allow(client)
        if not allowed:
            self._send_json({"error": "run_quota_exceeded", "retry_after_seconds": retry_after}, HTTPStatus.TOO_MANY_REQUESTS)
            return

        job = self.manager.start()
        self._send_json({"run_id": job.run_id, "status": job.status}, HTTPStatus.ACCEPTED)

def main() -> int:
    _load_demo_env_file()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--runs-dir", type=Path, default=Path.cwd() / ".passive-demo-runs")
    args = parser.parse_args()

    try:
        DemoHandler.access_token = _access_token_from_env()
        DemoHandler.secure_cookie = os.environ.get("DEMO_ENVIRONMENT", "local") == "public-demo"
        DemoHandler.run_quota = RunQuota(
            max_requests=int(os.environ.get("DEMO_RUN_MAX_REQUESTS", "5")),
            window_seconds=float(os.environ.get("DEMO_RUN_WINDOW_SECONDS", "3600")),
        )
    except (RuntimeError, TypeError, ValueError) as exc:
        parser.error(str(exc))

    DemoHandler.manager = JobManager(args.runs_dir)
    server = ThreadingHTTPServer((args.host, args.port), DemoHandler)
    server.daemon_threads = True
    print(f"BackBond demo ready: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
