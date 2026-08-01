"""BackBond drop-in appliance — CLI.

Commands:
  enroll     register the appliance, emit its public key (one-time)
  run        read substrate -> analyze locally -> emit signed no-content batches
  selftest   run the no-content invariant against a content-laden fixture

The partner runs one of these. They instrument nothing. Content never leaves.
"""
from __future__ import annotations
import argparse, json, os, sys, glob
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appliance.config import load_config
from appliance.readers import get_reader, available as readers_available
from appliance.pipeline import normalize, redact, ContentLeak
from appliance.rules.engine import RuleSet, Allowlist
from appliance.rules.cel_engine import CelRuleSet, CelAllowlist, is_cel_pack
from appliance.batch import BatchBuilder, Signer


def _now() -> str:
    # wall-clock is fine in the real appliance; kept explicit for determinism in tests
    return datetime.now(timezone.utc).isoformat()


def _ship(batch, cfg):
    out = cfg["ship"].get("mode", "outbox")
    if out == "outbox":
        d = os.path.expanduser(cfg["ship"]["outbox_dir"]); os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"batch-{batch['batch_seq']:06d}.json")
        with open(p, "w") as fh:
            json.dump(batch, fh, indent=2)
        return p
    if out == "http":
        return _ship_http(batch, cfg)
    raise ValueError(f"unknown ship mode {out}")


def _ship_http(batch, cfg):
    """Hardened HTTP delivery (P0-4): gzip, bearer auth from env, bounded retries with
    exponential backoff. Redelivery is safe — the server dedupes on batch_hash."""
    import urllib.request, urllib.error, gzip, time
    ship = cfg["ship"]
    url = ship["url"]
    token = os.environ.get("BACKBOND_SHIP_TOKEN", "")
    raw = json.dumps(batch).encode()
    body, headers = raw, {"Content-Type": "application/json",
                          "Authorization": f"Bearer {token}"}
    if ship.get("gzip", True):
        body = gzip.compress(raw); headers["Content-Encoding"] = "gzip"
    retries = int(ship.get("retries", 4))
    delay = float(ship.get("backoff_base_sec", 0.5))
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=int(ship.get("timeout_sec", 30))) as r:
                return f"http {r.status} seq{batch['batch_seq']}"
        except urllib.error.HTTPError as e:
            # 4xx (except 429) are terminal — do not retry a rejected batch forever.
            if e.code != 429 and 400 <= e.code < 500:
                return f"http {e.code} (rejected) seq{batch['batch_seq']}"
            last = f"http {e.code}"
        except (urllib.error.URLError, OSError) as e:
            last = str(e)
        if attempt < retries:
            time.sleep(delay); delay *= 2
    return f"FAILED after {retries} retries: {last} seq{batch['batch_seq']}"


def cmd_enroll(cfg):
    signer = Signer(os.path.expanduser(cfg["signing"]["key_path"]), cfg["signing"]["key_id"])
    enrollment = {
        "appliance_id": cfg["appliance_id"],
        "key_id": cfg["signing"]["key_id"],
        "public_key_pem": signer.public_pem(),
        "reader": cfg["reader"]["name"],
        "rule_pack": os.path.basename(cfg["rules"]["pack"]),
        "evidence_tier": cfg.get("evidence_tier", "log-inferred"),
        "enrolled_at": _now(),
    }
    print(json.dumps(enrollment, indent=2))
    return enrollment


def _rules_dir():
    return os.path.join(os.path.dirname(__file__), "rules")


def _build_ruleset(cfg):
    path = os.path.join(_rules_dir(), os.path.basename(cfg["rules"]["pack"]))
    return CelRuleSet.from_yaml(path) if is_cel_pack(path) else RuleSet.from_yaml(path)


def _build_allowlist(cfg):
    al = cfg.get("rules", {}).get("allowlist")
    if not al:
        return None
    path = os.path.join(_rules_dir(), os.path.basename(al))
    return CelAllowlist.from_yaml(path) if is_cel_pack(path) else Allowlist.from_yaml(path)


def cmd_run(cfg, dry=False):
    reader = get_reader(cfg["reader"]["name"])(cfg["reader"])
    if not reader.discover():
        sys.stderr.write(
            f"ERROR: reader '{cfg['reader']['name']}' found no substrate at "
            f"{cfg['reader'].get('path','?')}. Not scanning silently — set reader.path "
            f"explicitly or check read access. (available readers: {readers_available()})\n")
        return 2
    rules = _build_ruleset(cfg)
    allowlist = _build_allowlist(cfg)
    signer = Signer(os.path.expanduser(cfg["signing"]["key_path"]), cfg["signing"]["key_id"])
    builder = BatchBuilder(cfg["appliance_id"], signer,
                           os.path.expanduser(cfg["state_dir"]), cfg["producer"])

    emit_events, determinations = [], []
    total, leaks, suppressed = 0, 0, 0
    suppressed_by_rule: dict = {}
    for raw in reader.read():
        total += 1
        norm = normalize(raw)
        for det in rules.determinations(norm):              # rules see content, locally
            if allowlist and allowlist.suppressed(det, norm):
                suppressed += 1
                suppressed_by_rule[det["rule_id"]] = suppressed_by_rule.get(det["rule_id"], 0) + 1
                continue
            determinations.append(det)
        try:
            emit_events.append(redact(norm))                # fail-closed no-content
        except ContentLeak as e:
            leaks += 1
            sys.stderr.write(f"DROPPED (fail-closed): {e}\n")
    window = {"start": _now(), "end": _now()}
    max_events = int(cfg.get("ship", {}).get("max_events_per_batch", 5000))
    batches = builder.build(emit_events, determinations, window, max_events=max_events)
    shipped = []
    if not dry:
        for b in batches:
            shipped.append(_ship(b, cfg))
    summary = {"read": total, "emitted_events": len(emit_events),
               "net_determinations": len(determinations),
               "suppressed_by_allowlist": suppressed,
               "suppressed_by_rule": suppressed_by_rule,
               "dropped_content_leaks": leaks,
               "batches": len(batches),
               "shipped": shipped[:3] + (["..."] if len(shipped) > 3 else [])}
    print(json.dumps(summary, indent=2))
    return 0


def cmd_selftest(cfg):
    """Prove the no-content invariant: feed a content-laden event, assert nothing
    content-bearing appears in the emittable projection."""
    from appliance.pipeline import CONTENT_FIELDS
    poison = {
        "type": "social_post", "id": "evt-1", "tenant_id": "smb-42", "agent_id": "social",
        "ts": _now(), "text": "SECRET customer message with PII 555-12-3456",
        "email": "victim@example.com", "customer_data": {"ssn": "078-05-1120"},
    }
    norm = normalize(poison)
    emitted = redact(norm)
    blob = json.dumps(emitted)
    problems = []
    for f in CONTENT_FIELDS:
        if f in emitted:
            problems.append(f"content field '{f}' present")
    for needle in ("SECRET", "victim@example.com", "078-05-1120", "555-12-3456"):
        if needle in blob:
            problems.append(f"content value '{needle}' leaked")
    if problems:
        print("NO-CONTENT INVARIANT: FAIL"); [print("  -", p) for p in problems]; return 1
    print("NO-CONTENT INVARIANT: PASS")
    print("  emittable fields:", sorted(emitted.keys()))
    print("  evidence_hash:", emitted["evidence_hash"])
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="backbond-appliance")
    ap.add_argument("command", choices=["enroll", "run", "selftest"])
    ap.add_argument("-c", "--config", default=os.environ.get("BACKBOND_CONFIG", "config.yaml"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if args.command == "enroll":
        cmd_enroll(cfg); return 0
    if args.command == "run":
        return cmd_run(cfg, dry=args.dry_run)
    if args.command == "selftest":
        return cmd_selftest(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
