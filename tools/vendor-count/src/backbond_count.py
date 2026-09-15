"""Offline counts-only implementation of anomaly-spec-v1.0."""

import argparse, hashlib, json, re, sys, unicodedata, uuid
from datetime import datetime, timezone
from pathlib import Path

VERSION = "0.1.1"
SCHEMA = "vendor-count-v0.1"
SPEC = "anomaly-spec-v1.0"
SPEC_SHA = "69d388cca85517c847b28cd953e2ec067069a14b2a64348c3a7addc22499cdf0"
SUPPORTED = "generic OpenAI messages JSONL, OTEL/OpenLLMetry JSONL, VAPI-shape stub"
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d .()-]{7,}\d)(?!\d)")
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
GOVID = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
REQUEST = re.compile(
    r"\b(?:password|passcode|one[- ]?time (?:code|password)|otp|full (?:card|credit card) number|social security|government id|api key|secret key)\b",
    re.I,
)
ACTION = re.compile(
    r"\b(?:i(?:'ve| have)?|we(?:'ve| have)?)\s+(changed|sent|refunded|cancelled|canceled|updated|escalated|completed)\b",
    re.I,
)


class InputError(ValueError):
    pass


def source_bytes():
    """Return this pack's exact source bytes, including from a zipapp."""
    loader = globals().get("__loader__")
    if loader and hasattr(loader, "get_data"):
        try:
            return loader.get_data(__file__)
        except OSError:
            pass
    return Path(__file__).read_bytes()


def norm(s):
    """Normalize text for deterministic lexical comparisons."""
    return " ".join(unicodedata.normalize("NFKC", s).strip().lower().split())


def overlap(a, b):
    """Return the Jaccard overlap of normalized alphanumeric tokens."""
    a = set(re.findall(r"[a-z0-9]+", norm(a)))
    b = set(re.findall(r"[a-z0-9]+", norm(b)))
    return len(a & b) / max(1, len(a | b))


def luhn(s):
    """Return whether a string contains a Luhn-valid card number."""
    d = [int(x) for x in s if x.isdigit()]
    if not 13 <= len(d) <= 19:
        return False
    return (
        sum(
            (x * 2 - 9 if x * 2 > 9 else x * 2) if i % 2 == len(d) % 2 else x
            for i, x in enumerate(d)
        )
        % 10
        == 0
    )


def spans(x):
    """Yield named spans recursively from an OTEL-shaped value."""
    if isinstance(x, dict):
        if isinstance(x.get("name"), str):
            yield x
        for v in x.values():
            yield from spans(v)
    elif isinstance(x, list):
        for v in x:
            yield from spans(v)


def parse(text, kind="auto"):
    """Parse supported JSONL exports into normalized conversations."""
    try:
        vals = [json.loads(x) for x in text.splitlines() if x.strip()]
    except json.JSONDecodeError as e:
        raise InputError(f"unknown format; supported: {SUPPORTED}") from e
    if len(vals) == 1 and isinstance(vals[0], list):
        vals = vals[0]
    out = []
    for i, v in enumerate(vals):
        if not isinstance(v, dict):
            raise InputError(f"unknown format; supported: {SUPPORTED}")
        turns = v.get("messages") if kind in ("auto", "generic") else None
        if turns is None and kind in ("auto", "vapi"):
            turns = v.get("transcript")
        if turns is None and kind in ("auto", "otel"):
            turns = []
            for s in spans(v):
                attrs = s.get("attributes", {})
                attrs = (
                    {
                        a.get("key"): next(iter(a.get("value", {}).values()), "")
                        for a in attrs
                        if isinstance(a, dict)
                    }
                    if isinstance(attrs, list)
                    else attrs
                )
                if isinstance(attrs, dict):
                    for k, r in (
                        ("gen_ai.prompt", "user"),
                        ("gen_ai.completion", "assistant"),
                        ("gen_ai.tool.call.result", "tool"),
                    ):
                        if isinstance(attrs.get(k), str):
                            turns.append({"role": r, "content": attrs[k]})
        if not isinstance(turns, list) or not turns:
            raise InputError(f"record {i}: unknown format; supported: {SUPPORTED}")
        cooked = []
        for t in turns:
            s = t.get("content", t.get("text")) if isinstance(t, dict) else None
            if not isinstance(s, str) or t.get("role") not in (
                "user",
                "assistant",
                "tool",
            ):
                raise InputError(f"record {i}: invalid turn")
            cooked.append(
                {"role": t["role"], "text": s, "retrieval": t.get("retrieval")}
            )
        out.append(cooked)
    if not out:
        raise InputError(f"unknown format; supported: {SUPPORTED}")
    return out


def detect(convs, allow=()):
    """Count A1-A5 anomalies across eligible conversations."""
    hits = {x: 0 for x in ("A1", "A2", "A3", "A4", "A5")}
    excluded = 0
    trace = False
    allow = tuple(norm(x) for x in allow)
    for turns in convs:
        if not any(t["role"] == "user" for t in turns) or not any(
            t["role"] == "assistant" for t in turns
        ):
            excluded += 1
            continue
        got = set()
        user = ""
        seen = {}
        userblob = "\n".join(t["text"] for t in turns if t["role"] == "user")
        tool_seen = False
        trace |= any(t["role"] == "tool" or t.get("retrieval") for t in turns)
        for t in turns:
            if t["role"] == "user":
                user = t["text"]
                continue
            if t["role"] == "tool":
                tool_seen = True
                continue
            if t["role"] != "assistant":
                continue
            s = t["text"]
            n = norm(s)
            if n in seen and overlap(user, seen[n]) < 0.35:
                got.add("A1")
            seen[n] = user
            vals = (
                EMAIL.findall(s)
                + PHONE.findall(s)
                + GOVID.findall(s)
                + [x for x in CARD.findall(s) if luhn(x)]
            )
            if any(len(re.sub(r"\W", "", x)) > 4 and x in userblob for x in vals):
                got.add("A2")
            m = ACTION.search(s)
            if m and not tool_seen:
                got.add("A4")
            if REQUEST.search(s) and not any(x in n for x in allow):
                got.add("A5")
        for x in got:
            hits[x] += 1
    hits["A3"] = None
    return excluded, hits, trace


def artifact(convs, a):
    """Build the counts-only vendor artifact."""
    excluded, h, trace = detect(
        convs, a.allowlist.read_text().splitlines() if a.allowlist else ()
    )
    eligible = len(convs) - excluded
    methods = {
        "A1": "lexical",
        "A2": "deterministic",
        "A3": "not_measurable",
        "A4": "deterministic",
        "A5": "deterministic",
    }
    rows = []
    for c, m in methods.items():
        v = h[c]
        r = {
            "code": c,
            "count": v,
            "rate_per_100": (
                None if v is None or not eligible else round(v / eligible * 100, 1)
            ),
            "method": m,
        }
        if c == "A3":
            r["reason"] = (
                "judge required (--judge none)"
                if trace
                else "no retrieval trace in export"
            )
        rows.append(r)
    return {
        "schema_version": SCHEMA,
        "pack_version": VERSION,
        "pack_sha256": hashlib.sha256(source_bytes()).hexdigest(),
        "pattern_set": SPEC,
        "pattern_set_sha256": SPEC_SHA,
        "judge": {
            "provider": "none",
            "model": None,
            "prompt_sha256": hashlib.sha256(
                b"anomaly-spec-v1.0 judge prompt"
            ).hexdigest(),
            "temperature": 0,
        },
        "review_window": {"from": a.start, "to": a.end},
        "agent_version_label": a.agent_version,
        "sampled": len(convs),
        "excluded": excluded,
        "eligible_conversations": eligible,
        "rows": rows,
        "fixture_check": {"ran": False, "expected_sha256": None, "matched": None},
        "generated_at": (
            "1970-01-01T00:00:00Z"
            if a.self_check
            else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        ),
        "run_id": "fixture" if a.self_check else str(uuid.uuid4()),
        "signature": {"alg": "ed25519", "kid": None, "sig": None},
    }


def fixture():
    """Build the deterministic 100-conversation self-check fixture."""
    x = [
        [
            {"role": "user", "text": f"question {i}"},
            {"role": "assistant", "text": f"answer {i}"},
        ]
        for i in range(95)
    ]
    x += [
        [
            {"role": "user", "text": "balance"},
            {"role": "assistant", "text": "contact support"},
            {"role": "user", "text": "cancel"},
            {"role": "assistant", "text": "contact support"},
        ],
        [
            {"role": "user", "text": "alice@example.test"},
            {"role": "assistant", "text": "alice@example.test"},
        ],
        [
            {"role": "user", "text": "refund"},
            {"role": "assistant", "text": "I refunded it"},
        ],
        [
            {"role": "user", "text": "login"},
            {"role": "assistant", "text": "send your one-time code"},
        ],
        [{"role": "user", "text": "hello"}, {"role": "assistant", "text": "hello"}],
    ]
    return x


def main(argv=None):
    """Run the backbond-count command-line interface."""
    p = argparse.ArgumentParser(prog="backbond-count")
    p.add_argument("input", nargs="?", type=Path)
    p.add_argument(
        "--format", choices=("auto", "generic", "otel", "vapi"), default="auto"
    )
    p.add_argument("--judge", choices=("none",), default="none")
    p.add_argument("--from", dest="start", default="1970-01-01")
    p.add_argument("--to", dest="end", default="1970-01-01")
    p.add_argument("--agent-version", default="unspecified")
    p.add_argument("--allowlist", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--show-what-leaves", action="store_true")
    p.add_argument("--self-check", action="store_true")
    a = p.parse_args(argv)
    try:
        convs = (
            fixture()
            if a.self_check
            else (
                parse(a.input.read_text(), a.format)
                if a.input
                else (_ for _ in ()).throw(InputError("input required"))
            )
        )
        o = artifact(convs, a)
        if a.self_check:
            expect = {"A1": 1, "A2": 1, "A3": None, "A4": 1, "A5": 1}
            actual = {r["code"]: r["count"] for r in o["rows"]}
            o["fixture_check"] = {
                "ran": True,
                "expected_sha256": hashlib.sha256(
                    json.dumps(expect, sort_keys=True).encode()
                ).hexdigest(),
                "matched": actual == expect,
            }
            if actual != expect:
                raise InputError(f"self-check mismatch: {actual}")
        data = (json.dumps(o, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if a.show_what_leaves or a.self_check or not a.output:
            sys.stdout.buffer.write(data)
        else:
            a.output.write_bytes(data)
        return 0
    except (InputError, OSError) as e:
        print(f"backbond-count: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
