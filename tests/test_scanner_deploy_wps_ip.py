"""Exercise the edge Python probe without curl or external network traffic."""
import io
from pathlib import Path
from unittest.mock import patch

import pytest

HELPER = Path(__file__).resolve().parents[1] / "scripts/deploy/scanner-deploy.sh"
PROBE = HELPER.read_text().split("<<'PY_WPS_IP'\n", 1)[1].split("\nPY_WPS_IP", 1)[0]


@pytest.mark.parametrize("body,expected", [(b"203.0.113.10\n", "203.0.113.10"), (b"2001:db8::1", "2001:db8::1")])
def test_direct_probe_uses_python_and_disables_proxies(body, expected, capsys):
    class Opener:
        def open(self, url, timeout):
            assert url == "https://api.ipify.org"
            assert timeout == 10
            return io.BytesIO(body)

    def build(handler):
        assert handler.proxies == {}
        return Opener()

    with patch("urllib.request.build_opener", build):
        exec(PROBE, {})
    assert capsys.readouterr().out == expected + "\n"
    command = HELPER.read_text().split("diagnostics_wp_s()", 1)[1].split("PY_WPS_IP", 1)[0]
    assert "exec python3 -" in command
    assert "exec curl" not in command
    assert "unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy" in command


@pytest.mark.parametrize("body", [b"not-an-ip", b"203.0.113.10\nsecret", b"x" * 100, b"\xff"])
def test_invalid_response_emits_no_receipt_or_response(body, capsys):
    with patch("urllib.request.OpenerDirector.open", return_value=io.BytesIO(body)):
        with pytest.raises(SystemExit) as failure:
            exec(PROBE, {})
    assert failure.value.code == 1
    assert capsys.readouterr().out == ""


def test_network_error_is_sanitized(capsys):
    with patch("urllib.request.OpenerDirector.open", side_effect=OSError("secret")):
        with pytest.raises(SystemExit):
            exec(PROBE, {})
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
