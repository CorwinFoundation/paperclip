import gzip
import importlib
import json
import types
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
APPLIANCE = REPO / "ops/paperclip/numbat/backbond-appliance-0.1.0/backbond-appliance"


if "celpy" not in sys.modules:
    celpy = types.ModuleType("celpy")

    class CELEvalError(Exception):
        pass

    class _NoopProgram:
        def evaluate(self, *_args, **_kwargs):
            return False

    class _NoopEnvironment:
        def compile(self, *_args, **_kwargs):
            return None

        def program(self, *_args, **_kwargs):
            return _NoopProgram()

    celpy.CELEvalError = CELEvalError
    celpy.Environment = _NoopEnvironment
    celpy.json_to_cel = lambda value: value
    sys.modules["celpy"] = celpy

sys.path.insert(0, str(APPLIANCE))
main = importlib.import_module("appliance.main")


class Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_ship_http_sends_configured_scope_headers_and_preserves_payload(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setenv("BACKBOND_SHIP_TOKEN", "secret-token")
    monkeypatch.delenv("BACKBOND_TENANT_ID", raising=False)
    monkeypatch.delenv("BACKBOND_SOURCE_ID", raising=False)
    batch = {"batch_seq": 223, "sealed": "unchanged"}
    cfg = {"ship": {"mode": "http", "url": "http://receiver/ingest",
                    "tenant_id": "backbond", "source_id": "paperclip-codex-rollouts",
                    "gzip": True, "retries": 4, "timeout_sec": 17}}

    assert main._ship_http(batch, cfg) == "http 200 seq223"
    request = captured["request"]
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers["authorization"] == "Bearer secret-token"
    assert headers["x-backbond-tenant"] == "backbond"
    assert headers["x-backbond-source"] == "paperclip-codex-rollouts"
    assert headers["content-encoding"] == "gzip"
    assert json.loads(gzip.decompress(request.data)) == batch
    assert captured["timeout"] == 17


def test_ship_http_environment_scope_overrides_config(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured["request"] = request
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setenv("BACKBOND_SHIP_TOKEN", "token")
    monkeypatch.setenv("BACKBOND_TENANT_ID", "tenant-from-env")
    monkeypatch.setenv("BACKBOND_SOURCE_ID", "source-from-env")
    cfg = {"ship": {"url": "http://receiver/ingest", "tenant_id": "tenant-config",
                    "source_id": "source-config", "gzip": False, "retries": 0}}

    assert main._ship_http({"batch_seq": 1}, cfg) == "http 200 seq1"
    headers = {key.lower(): value for key, value in captured["request"].header_items()}
    assert headers["x-backbond-tenant"] == "tenant-from-env"
    assert headers["x-backbond-source"] == "source-from-env"
