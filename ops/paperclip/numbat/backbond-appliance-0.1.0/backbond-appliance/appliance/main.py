"""BackBond drop-in appliance — CLI.

Commands:
  enroll     register the appliance, emit its public key (one-time)
  run        read substrate -> analyze locally -> emit signed no-content batches
  selftest   run the no-content invariant against a content-laden fixture

The partner runs one of these. They instrument nothing. Content never leaves.
"""
from __future__ import annotations
import argparse, json, os, re, sys, glob
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appliance.config import load_config
from appliance.readers import get_reader, available as readers_available
from appliance.pipeline import normalize, redact, ContentLeak
from appliance.rules.engine import RuleSet, Allowlist
from appliance.rules.cel_engine import CelRuleSet, CelAllowlist, is_cel_pack
from appliance.batch import DEFAULT_MAX_BATCH_BYTES, BatchBuilder, Signer, verify_batch
from appliance.replay_manifest import build_replay_manifest, canonical_manifest_bytes


EXIT_OK = 0
EXIT_NO_SUBSTRATE = 2
EXIT_REJECTED = 3        # receiver refused the batch terminally (e.g. unknown_appliance)
EXIT_UNDELIVERED = 4     # receiver unreachable after bounded retries


class ShipRejected(RuntimeError):
    """Terminal rejection. Redelivering these bytes is pointless until something
    changes on the receiver (enrollment, auth, scope), so the batch is parked
    rather than rebuilt."""

    def __init__(self, code: int, reason: str, seq):
        self.code, self.reason, self.seq = code, reason, seq
        super().__init__(f"receiver rejected batch: http {code} {reason} seq{seq}")


class ShipUnavailable(RuntimeError):
    """Delivery failed transiently after the bounded retries."""

    def __init__(self, detail: str, seq):
        self.detail, self.seq = detail, seq
        super().__init__(f"shipping failed: {detail} seq{seq}")


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
    tenant_id = os.environ.get("BACKBOND_TENANT_ID", ship.get("tenant_id", ""))
    source_id = os.environ.get("BACKBOND_SOURCE_ID", ship.get("source_id", ""))
    if not tenant_id or not source_id:
        raise ValueError("HTTP shipping requires tenant_id and source_id")
    raw = json.dumps(batch).encode()
    body, headers = raw, {"Content-Type": "application/json",
                          "Authorization": f"Bearer {token}",
                          "X-BackBond-Tenant": tenant_id,
                          "X-BackBond-Source": source_id}
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
                raise ShipRejected(e.code, _reject_reason(e), batch["batch_seq"])
            last = f"http {e.code}"
        except (urllib.error.URLError, OSError) as e:
            last = str(e)
        if attempt < retries:
            time.sleep(delay); delay *= 2
    raise ShipUnavailable(f"after {retries} retries: {last}", batch["batch_seq"])


def _reject_reason(e) -> str:
    """The receiver's own error string, so the diagnostic names the cause
    (``unknown_appliance``) and not just the status code. The journal is not
    readable by every seat; stderr is."""
    try:
        raw = e.read()
    except Exception:
        return "no_reason_in_response"
    text = raw.decode("utf-8", "replace") if raw else ""
    try:
        doc = json.loads(text)
    except Exception:
        return (text.strip()[:200] or "no_reason_in_response")
    if isinstance(doc, dict):
        return str(doc.get("error") or doc.get("reason") or doc.get("message") or doc)[:200]
    return str(doc)[:200]


def _dead_letter_dir(cfg) -> str:
    """Where batches that were built but not accepted wait for redelivery."""
    d = cfg.get("ship", {}).get("dead_letter_dir") or os.path.join(cfg["state_dir"], "dead-letter")
    return os.path.expanduser(d)


def _dead_letter_pending(cfg) -> list:
    return sorted(glob.glob(os.path.join(_dead_letter_dir(cfg), "batch-*.json")))


def _dead_letter_put(batch, cfg) -> str:
    """Park a built batch. Written to a temp name and renamed, so a tick that dies
    mid-write never leaves a half-batch that the next tick would try to deliver."""
    d = _dead_letter_dir(cfg)
    os.makedirs(d, exist_ok=True)
    tag = re.sub(r"[^0-9A-Za-z_.-]", "-", str(batch.get("batch_hash", ""))[:16]) or "nohash"
    p = os.path.join(d, f"batch-{batch['batch_seq']:06d}-{tag}.json")
    tmp = f"{p}.tmp"
    with open(tmp, "w") as fh:
        json.dump(batch, fh)
    os.replace(tmp, p)
    return p


def _drain_dead_letter(cfg) -> list:
    """Retry *delivery* of already-built batches. Deliberately touches neither the
    reader nor the rule engine: the whole point of BEAAA-36046 is that a rejected
    batch must not cost a second normalize+rules pass over the same window."""
    results = []
    for path in _dead_letter_pending(cfg):
        with open(path) as fh:
            batch = json.load(fh)
        results.append(_ship(batch, cfg))
        os.unlink(path)
    return results


def _report_undelivered(cfg, err, parked: int, redelivery: bool = False) -> int:
    """One line on stderr naming the cause and the appliance, and the exit code that
    goes with it. No traceback: a traceback told us nothing for 2,300 runs."""
    reason = getattr(err, "reason", None) or getattr(err, "detail", None) or str(err)
    code = getattr(err, "code", None)
    what = "redelivery" if redelivery else "delivery"
    sys.stderr.write(
        f"ERROR: {what} refused for appliance_id={cfg['appliance_id']}: {reason}"
        + (f" (http {code})" if code else "")
        + f"; {parked} batch(es) parked in {_dead_letter_dir(cfg)}"
        + "; reader checkpoint NOT advanced (no evidence dropped)"
        + "; next tick retries delivery only, it does not re-read the window.\n")
    if isinstance(err, ShipRejected) and reason == "unknown_appliance":
        sys.stderr.write(
            f"HINT: the receiver is up but does not know appliance_id="
            f"{cfg['appliance_id']}. Enroll it (root: /etc/backbond/appliance-enrollment.json) "
            f"or run `appliance.main selftest` to assert admission.\n")
    return EXIT_REJECTED if isinstance(err, ShipRejected) else EXIT_UNDELIVERED


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
    # Delivery before discovery. A batch that was built but never accepted is
    # redelivered before any new window is read; re-reading would redo normalize
    # and the rule engine over events already analyzed, which is exactly the spin
    # BEAAA-36046 removes. On a clean drain we fall through, so the tick that heals
    # is also the tick that finally advances the checkpoint.
    pending = _dead_letter_pending(cfg)
    if pending and not dry:
        try:
            results = _drain_dead_letter(cfg)
        except (ShipRejected, ShipUnavailable) as e:
            return _report_undelivered(cfg, e, len(_dead_letter_pending(cfg)), redelivery=True)
        print(json.dumps({"redelivered": len(results), "results": results[:3]}, indent=2))

    reader = get_reader(cfg["reader"]["name"])(cfg["reader"])
    if not reader.discover():
        sys.stderr.write(
            f"ERROR: reader '{cfg['reader']['name']}' found no substrate at "
            f"{cfg['reader'].get('path','?')}. Not scanning silently — set reader.path "
            f"explicitly or check read access. (available readers: {readers_available()})\n")
        return EXIT_NO_SUBSTRATE
    rules = _build_ruleset(cfg)
    allowlist = _build_allowlist(cfg)
    signer = Signer(os.path.expanduser(cfg["signing"]["key_path"]), cfg["signing"]["key_id"])
    builder = BatchBuilder(cfg["appliance_id"], signer,
                           os.path.expanduser(cfg["state_dir"]), cfg["producer"])

    max_events = int(cfg.get("ship", {}).get("max_events_per_batch", 5000))
    max_bytes = int(cfg.get("ship", {}).get("max_batch_bytes", DEFAULT_MAX_BATCH_BYTES))
    checkpoint = None
    if hasattr(reader, "read_window"):
        reader.configure_checkpoint(os.path.expanduser(cfg["state_dir"]))
        raw_events, checkpoint = reader.read_window(max_events)
    else:
        raw_events = reader.read()

    emit_events, determinations = [], []
    total, leaks, suppressed = 0, 0, 0
    suppressed_by_rule: dict = {}
    for raw in raw_events:
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
    batches = builder.build(emit_events, determinations, window, max_events=max_events,
                            max_bytes=max_bytes, persist=checkpoint is None)
    shipped = []
    if not dry:
        failure = None
        for i, b in enumerate(batches):
            try:
                shipped.append(_ship(b, cfg))
            except (ShipRejected, ShipUnavailable) as e:
                # Park this batch and every one behind it. The work is already done;
                # the next tick owes delivery, not another pass over the substrate.
                for undelivered in batches[i:]:
                    _dead_letter_put(undelivered, cfg)
                failure = e
                break
        if failure is not None:
            # The checkpoint stays put on purpose: these events are parked, not
            # accepted, and the window stays claimable until the receiver takes them.
            return _report_undelivered(cfg, failure, len(batches) - len(shipped))
        if checkpoint is not None:
            builder.commit()
            reader.commit(checkpoint)
    summary = {"read": total, "emitted_events": len(emit_events),
               "net_determinations": len(determinations),
               "suppressed_by_allowlist": suppressed,
               "suppressed_by_rule": suppressed_by_rule,
               "dropped_content_leaks": leaks,
               "batches": len(batches),
               "shipped": shipped[:3] + (["..."] if len(shipped) > 3 else [])}
    print(json.dumps(summary, indent=2))
    return EXIT_OK


def _admission_probe(cfg) -> int:
    """Assert the receiver will admit *this* appliance, at install time.

    The probe is a zero-event batch carrying one deliberately unknown envelope
    field, so the receiver always rejects it and nothing is ever ingested. What
    matters is *which* rejection comes back: the enrollment check runs before
    envelope validation, so ``unknown_appliance`` means this appliance is not
    enrolled, and any other reason means admission was reached. Accepting the
    probe also counts as admission.
    """
    if cfg.get("ship", {}).get("mode") != "http" or not cfg.get("ship", {}).get("url"):
        print("ADMISSION: SKIP (ship.mode is not http)")
        return 0
    signer = Signer(os.path.expanduser(cfg["signing"]["key_path"]), cfg["signing"]["key_id"])
    builder = BatchBuilder(cfg["appliance_id"], signer,
                           os.path.expanduser(cfg["state_dir"]), cfg["producer"])
    batch = builder._make_batch([], [], {"start": _now(), "end": _now()})
    batch["admission_probe"] = True          # guarantees rejection; nothing is ingested
    try:
        result = _ship_http(batch, cfg)
    except ShipRejected as e:
        if e.reason == "unknown_appliance":
            print("ADMISSION: FAIL")
            print(f"  receiver does not know appliance_id={cfg['appliance_id']}")
            print("  enroll it before enabling the timer, or every tick will fail")
            return 1
        print(f"ADMISSION: PASS (receiver knows this appliance; probe refused as {e.reason})")
        return 0
    except ShipUnavailable as e:
        print(f"ADMISSION: FAIL\n  receiver unreachable: {e.detail}")
        return 1
    print(f"ADMISSION: PASS ({result})")
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
    return _admission_probe(cfg)


def cmd_export_replay(cfg, batch_dir, output, start_seq=None, end_seq=None):
    paths = sorted(glob.glob(os.path.join(os.path.expanduser(batch_dir), "batch-*.json")))
    with_signatures = [json.load(open(path, encoding="utf-8")) for path in paths]
    with_signatures = [batch for batch in with_signatures
                       if (start_seq is None or batch.get("batch_seq", -1) >= start_seq)
                       and (end_seq is None or batch.get("batch_seq", -1) <= end_seq)]
    signer = Signer(os.path.expanduser(cfg["signing"]["key_path"]), cfg["signing"]["key_id"])
    manifest = build_replay_manifest(with_signatures, signer.public_pem(), verify_batch)
    with open(output, "wb") as fh:
        fh.write(canonical_manifest_bytes(manifest))
    print(json.dumps({"manifest": output, "batches": len(manifest["batches"]),
                      "artifact_sha256": manifest["artifact_sha256"]}, sort_keys=True))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="backbond-appliance")
    ap.add_argument("command", choices=["enroll", "run", "selftest", "export-replay"])
    ap.add_argument("-c", "--config", default=os.environ.get("BACKBOND_CONFIG", "config.yaml"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch-dir")
    ap.add_argument("--output")
    ap.add_argument("--start-seq", type=int)
    ap.add_argument("--end-seq", type=int)
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if args.command == "enroll":
        cmd_enroll(cfg); return 0
    if args.command == "run":
        return cmd_run(cfg, dry=args.dry_run)
    if args.command == "selftest":
        return cmd_selftest(cfg)
    if args.command == "export-replay":
        if not args.batch_dir or not args.output:
            ap.error("export-replay requires --batch-dir and --output")
        return cmd_export_replay(cfg, args.batch_dir, args.output, args.start_seq, args.end_seq)


if __name__ == "__main__":
    raise SystemExit(main())
