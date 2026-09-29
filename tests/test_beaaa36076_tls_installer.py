from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


PATH = Path(__file__).parents[1] / "ops/stage-b-inbound/patch_tls_vhost.py"
spec = spec_from_file_location("patch_tls_vhost", PATH)
module = module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


VHOST = """
server {
    listen 443 ssl;
    server_name scanner.backbond.ai;
    location = /scanner/v1/release-identity { proxy_pass http://127.0.0.1:8077; }
    location = /scanner/v1/sla { proxy_pass http://127.0.0.1:8077; }
}
server {
    listen 80;
    server_name scanner.backbond.ai;
    return 404;
}
"""


def test_include_enters_tls_block_and_preserves_scanner_routes():
    output = module.patched(VHOST)
    first, second = output.split("server {", 2)[1:]
    assert module.INCLUDE in first
    assert module.INCLUDE not in second
    assert "location = /scanner/v1/release-identity" in output
    assert "location = /scanner/v1/sla" in output
    assert module.patched(output) == output


def test_ambiguous_or_missing_tls_layout_fails_closed():
    with pytest.raises(ValueError, match="expected_one_scanner_tls_block_got_0"):
        module.patched(VHOST.replace("443 ssl", "8443"))
    with pytest.raises(ValueError, match="expected_one_scanner_tls_block_got_2"):
        module.patched(VHOST + VHOST)
