"""Run inside the 0.1.1 container: exercise call_agent with mock oversized agent.

ACs covered:
  AC7: oversized response raises with observed-byte-count, no body content.
  AC9 (live): smoke confirms call_agent rejects >cap body at HTTP boundary.
  AC10: BEAAA-5553-style smoke on Docker image.
"""
import json
import os
import sys

from are_connector.http import call_agent, EgressAllowlist
from are_connector.response_limits import (
    OversizedResponseError,
    max_response_bytes,
    MAX_RESPONSE_BYTES_ENV,
    DEFAULT_MAX_RESPONSE_BYTES,
)

AGENT = os.environ["AGENT_URL"]  # http://host:port/agent

results = []

def case(name, fn):
    try:
        out = fn()
        results.append({"name": name, "status": "PASS", "detail": out})
    except AssertionError as e:
        results.append({"name": name, "status": "FAIL", "detail": str(e)})
    except Exception as e:
        results.append({"name": name, "status": "ERROR", "detail": f"{type(e).__name__}: {e}"})

# allowlist allowing the mock agent + portal placeholder (only agent_endpoint used here)
os.environ["ALLOW_PLAINTEXT_AGENT_ENDPOINT"] = "1"
allow = EgressAllowlist(portal_base_url=AGENT, agent_endpoint=AGENT)

def t_under_cap_passes():
    # default cap = 1 MiB; request 64 KiB
    r = call_agent(f"{AGENT}?size=65536", {"prompt":"x"}, allow, timeout=10)
    assert isinstance(r, dict) and "response" in r, f"unexpected: {type(r).__name__}"
    assert len(r["response"]) == 65536 - 16, f"len={len(r['response'])}"
    return f"got dict with response len {len(r['response'])}"

def t_over_cap_rejected():
    try:
        call_agent(f"{AGENT}?size=2097152", {"prompt":"x"}, allow, timeout=10)
    except OversizedResponseError as e:
        # No body content in error: only metadata
        msg = str(e)
        assert "max_response_bytes=1048576" in msg, msg
        assert "observed_byte_count=" in msg, msg
        assert "xxxx" not in msg, "error must NOT leak response body content"
        return f"raised OversizedResponseError observed={e.observed_byte_count} cap={e.max_response_bytes}"
    raise AssertionError("expected OversizedResponseError but call returned")

def t_configurable_cap_honored():
    # Reduce cap to 32 KiB; 64 KiB body must be rejected.
    os.environ[MAX_RESPONSE_BYTES_ENV] = "32768"
    try:
        try:
            call_agent(f"{AGENT}?size=65536", {"prompt":"x"}, allow, timeout=10)
        except OversizedResponseError as e:
            assert e.max_response_bytes == 32768, e.max_response_bytes
            return f"cap reset to 32768 -> rejected observed={e.observed_byte_count}"
        raise AssertionError("expected rejection at 32 KiB cap")
    finally:
        os.environ.pop(MAX_RESPONSE_BYTES_ENV, None)

def t_default_cap_value():
    assert max_response_bytes() == DEFAULT_MAX_RESPONSE_BYTES == 1024*1024, "default 1 MiB"
    return f"default cap = {DEFAULT_MAX_RESPONSE_BYTES} bytes (1 MiB)"

def t_at_cap_edge_passes():
    cap = max_response_bytes()
    r = call_agent(f"{AGENT}?size={cap}", {"prompt":"x"}, allow, timeout=15)
    return f"at-cap {cap} bytes passed (response keys={list(r.keys())[:3]})"

case("default_cap_is_1MiB", t_default_cap_value)
case("under_cap_passes", t_under_cap_passes)
case("at_cap_edge_passes", t_at_cap_edge_passes)
case("over_cap_rejected_no_body_leak", t_over_cap_rejected)
case("configurable_cap_honored", t_configurable_cap_honored)

print(json.dumps({"summary": {"pass": sum(1 for r in results if r["status"]=="PASS"),
                              "fail": sum(1 for r in results if r["status"]!="PASS"),
                              "total": len(results)},
                  "results": results}, indent=2))
sys.exit(0 if all(r["status"]=="PASS" for r in results) else 1)
