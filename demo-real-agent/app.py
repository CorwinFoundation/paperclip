#!/usr/bin/env python3
"""Authenticated web service for the real 90-case agent evaluation."""
from __future__ import annotations

import argparse
from collections import deque
from datetime import UTC, datetime, timedelta
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import threading
import time
import uuid
from urllib.parse import parse_qs, urlparse

from real_scan import (
    RealScanConfig,
    execute_real_scan,
    judge_configured,
    registry_inventory,
    validate_public_configuration,
)


DEMO_DIR = Path(__file__).resolve().parent
INDEX = DEMO_DIR / "index.html"
ENV_FILE = Path("/etc/backbond/real-agent-demo.env")
BUDGET_MARKER = ".initial-evaluation-budget-consumed"


class EvaluationBudgetConsumed(RuntimeError):
    pass


def _load_env_file(path: Path = ENV_FILE) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#") or "=" not in clean:
            continue
        key, value = clean.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _access_token() -> str | None:
    return os.environ.get("DEMO_ACCESS_TOKEN") or None


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _deployment_metadata() -> dict[str, str]:
    return {
        "environment": os.environ.get("DEMO_ENVIRONMENT", "local"),
        "git_sha": os.environ.get("DEMO_GIT_SHA", "working-tree"),
        "deployed_at": os.environ.get("DEMO_DEPLOYED_AT", _utc_now()),
        "workflow": os.environ.get("DEMO_WORKFLOW", "Real 90-case agent demo"),
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

    def emit(self, kind: str, message: str, **details: object) -> None:
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
    def __init__(
        self,
        runs_dir: Path,
        *,
        max_jobs: int = 10,
        scan_runner=execute_real_scan,
    ) -> None:
        self.runs_dir = runs_dir.resolve()
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.budget_marker = self.runs_dir / BUDGET_MARKER
        self.max_jobs = max_jobs
        self.scan_runner = scan_runner
        self.jobs: dict[str, DemoJob] = {}
        self.job_order: deque[str] = deque()
        self.active_job_id: str | None = None
        self.latest_completed_id: str | None = None
        self.lock = threading.Lock()

    def evaluation_available(self) -> bool:
        with self.lock:
            return not self.budget_marker.exists()

    def start(self) -> DemoJob:
        with self.lock:
            if self.active_job_id:
                active = self.jobs.get(self.active_job_id)
                if active and active.status in {"queued", "running"}:
                    return active
            run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
            try:
                descriptor = os.open(
                    self.budget_marker,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError as exc:
                raise EvaluationBudgetConsumed(
                    "the approved initial evaluation budget has already been used"
                ) from exc
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"run_id": run_id, "reserved_at": _utc_now()}, handle)
                handle.write("\n")
            job = DemoJob(run_id, self.runs_dir / run_id)
            self.jobs[run_id] = job
            self.job_order.append(run_id)
            self.active_job_id = run_id
            threading.Thread(
                target=self._execute,
                args=(job,),
                daemon=True,
                name=f"real-scan-{run_id}",
            ).start()
            return job

    def get(self, run_id: str) -> DemoJob | None:
        with self.lock:
            return self.jobs.get(run_id)

    def latest(self) -> DemoJob | None:
        with self.lock:
            run_id = self.latest_completed_id or self.active_job_id
            return self.jobs.get(run_id) if run_id else None

    def _execute(self, job: DemoJob) -> None:
        job.status = "running"
        job.started_at = _utc_now()
        started = time.perf_counter()
        try:
            scan = self.scan_runner(job.output_dir, job.run_id, job.emit)
            job.result = {
                "scan": scan,
                "deployment": _deployment_metadata(),
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "evidence_id": job.run_id,
            }
            job.status = "complete"
            job.emit(
                "complete",
                "Real 90-case evaluation completed",
                stage="complete",
                state="passed" if not scan.get("incomplete") else "incomplete",
            )
        except Exception as exc:  # never include credentials in the error path
            job.error = f"{type(exc).__name__}: {str(exc)[:500]}"
            job.status = "failed"
            job.emit("error", job.error, stage="failed", state="failed")
        finally:
            job.finished_at = _utc_now()
            with self.lock:
                self.active_job_id = None
                if job.status == "complete":
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
            if (
                path.is_dir()
                and datetime.fromtimestamp(path.stat().st_mtime, UTC) < cutoff
                and path.name not in self.jobs
            ):
                shutil.rmtree(path, ignore_errors=True)


class DemoHandler(BaseHTTPRequestHandler):
    manager: JobManager
    rate_limit: dict[str, float] = {}
    rate_lock = threading.Lock()

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'",
        )

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

    def _request_token(self) -> str:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth.removeprefix("Bearer ").strip()
        return self.headers.get("X-Demo-Access-Token", "").strip()

    def _authorized_for_run(self) -> bool:
        token = _access_token()
        return not token or hmac.compare_digest(self._request_token(), token)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/":
            self._send_index()
            return
        if path == "/api/health":
            inventory = registry_inventory()
            config = RealScanConfig.from_env()
            self._send_json(
                {
                    "status": "ok",
                    "mode": "real-agent-90-case-scan",
                    "synthetic": False,
                    "external_target_enabled": True,
                    "target_host": urlparse(config.agent_endpoint).hostname,
                    "judge_configured": judge_configured(),
                    "evaluation_available": self.manager.evaluation_available(),
                    "active_case_count": inventory["active_case_count"],
                    "planned_target_calls": inventory["planned_target_calls"],
                    "planned_judge_calls": inventory["planned_judge_calls"],
                    "max_judge_cost_usd": config.max_estimated_judge_cost_usd,
                    "deployment": _deployment_metadata(),
                }
            )
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
            run_id = path[len(prefix):]
            job = self.manager.get(run_id)
            if not job:
                self._send_json({"error": "run_not_found"}, HTTPStatus.NOT_FOUND)
                return
            try:
                after = max(0, int(parse_qs(parsed.query).get("after", ["0"])[0]))
            except ValueError:
                self._send_json({"error": "invalid_after"}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(job.snapshot(after=after))
            return
        self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") != "/api/runs":
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        if not self._authorized_for_run():
            self._send_json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return
        raw_length = self.headers.get("Content-Length", "0") or "0"
        try:
            content_length = int(raw_length)
        except ValueError:
            self._send_json({"error": "invalid_content_length"}, HTTPStatus.BAD_REQUEST)
            return
        if content_length < 0 or content_length > 1024:
            self._send_json(
                {"error": "request_too_large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE
            )
            return
        if content_length:
            self.rfile.read(content_length)
        client = self.client_address[0]
        now = time.monotonic()
        with self.rate_lock:
            if now - self.rate_limit.get(client, 0.0) < 3:
                self._send_json(
                    {"error": "rate_limited", "retry_after_seconds": 3},
                    HTTPStatus.TOO_MANY_REQUESTS,
                )
                return
            self.rate_limit[client] = now
        try:
            job = self.manager.start()
        except EvaluationBudgetConsumed:
            self._send_json(
                {
                    "error": "initial_evaluation_budget_consumed",
                    "detail": "The approved one-run evaluation budget has already been used.",
                },
                HTTPStatus.TOO_MANY_REQUESTS,
            )
            return
        self._send_json({"run_id": job.run_id, "status": job.status}, HTTPStatus.ACCEPTED)


def main() -> int:
    _load_env_file()
    if os.environ.get("DEMO_ENVIRONMENT") == "public-demo":
        token = _access_token()
        if not token or len(token) < 32:
            raise SystemExit("public demo requires a 32+ character DEMO_ACCESS_TOKEN")
        validate_public_configuration(RealScanConfig.from_env())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--runs-dir", type=Path, default=Path.cwd() / ".real-demo-runs")
    args = parser.parse_args()
    DemoHandler.manager = JobManager(args.runs_dir)
    server = ThreadingHTTPServer((args.host, args.port), DemoHandler)
    server.daemon_threads = True
    print(f"BackBond real-agent demo ready: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
