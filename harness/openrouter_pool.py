"""BEAAA-36019: fleet-level pool preflight for every OpenRouter-spending entry point.

``BudgetGuard`` in the slice capture scripts binds one slice to its own
authorized cap. Nothing bound the fleet to the account, and the account is the
thing that runs out. On 2026-09-26 the CEO measured $12.64 left against a per-key
``limit_remaining`` of $21.01: a key cap above the account balance is not
spendable money, so anything reasoning from the key believes it has money it
does not have.

This module reads the live account pool before the first paid call and refuses
to start when the caller's authorized cap exceeds what is actually left. The
failure lands at launch with both numbers in the message rather than part-way
through a paid capture as a 402.

Every reading is appended to ``artifacts/beaaa-35938/pool-readings.jsonl``, the
file the BEAAA-35938 V7 budget check already reads, so that check keeps working
and the fleet burn rate stays derivable rather than reconstructed. Guard rows
carry a ``slice`` field; V7 scopes its draw ceiling to its own slice's rows.

Each reading also carries the seat key's counters, read at the same instant, and
a ``sole_drawer`` block comparing the two endpoints' *deltas* since the previous
reading. When they agree, nothing but this key drew on the account in the
interval. That is diagnostic, not a gate: a mismatch warns, it does not refuse,
because a second key spending the pool is a fact about the account rather than a
defect in the caller's cap.

CLI, for proving the guard without spending anything:

    python3 -m harness.openrouter_pool --cap-usd 2 --label "preflight check" --slice beaaa-36019
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

#: The file the BEAAA-35938 V7 budget check reads. Shared on purpose.
POOL_READINGS = ROOT / "artifacts" / "beaaa-35938" / "pool-readings.jsonl"

#: Slice that owns the three pre-BEAAA-36019 rows, which carry no ``slice`` key.
LEGACY_SLICE = "beaaa-35938"

#: CEO pre-capture reading on BEAAA-35938, 2026-09-25T20:19Z, before any call.
BASELINE_REMAINING_USD = 14.284500
BASELINE_LABEL = "CEO pre-capture reading, BEAAA-35938 2026-09-25T20:19Z"

CREDITS_URL = "https://openrouter.ai/api/v1/credits"
KEY_URL = "https://openrouter.ai/api/v1/key"

#: Tolerance for the sole-drawer check. The two endpoints have agreed to six
#: decimals on every interval measured so far.
SOLE_DRAWER_TOLERANCE_USD = 1e-6


class PoolExhausted(RuntimeError):
    """The account pool cannot cover the cap this run is authorized to spend."""


def read_pool(*, api_key: str | None = None, timeout: float = 30.0) -> dict[str, float]:
    """Read the live account pool. Never a cached file, never the key's cap.

    ``/api/v1/key`` reports ``limit_remaining`` against the key's own ceiling,
    which can sit above the account balance. Only ``/api/v1/credits`` reports
    money that can actually be spent.
    """
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise PoolExhausted("OPENROUTER_API_KEY not set; cannot read the account pool")
    req = urllib.request.Request(CREDITS_URL, headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)["data"]
    total_credits = float(data["total_credits"])
    total_usage = float(data["total_usage"])
    return {
        "total_credits": total_credits,
        "total_usage": total_usage,
        "remaining_usd": round(total_credits - total_usage, 6),
    }


def read_key(*, api_key: str | None = None, timeout: float = 30.0) -> dict[str, Any]:
    """Read the seat key's own counters, at the same instant as the pool.

    ``limit_remaining`` here is *not* spendable money and is never used as the
    ceiling. The key is read for two other reasons: ``usage`` gives the
    sole-drawer check something to compare the account against, and
    ``usage_daily`` / ``usage_weekly`` are OpenRouter's own windowed figures.

    Deliberately absent: any burn rate derived from a ``total_usage`` delta.
    This account is idle between runs and drains in discrete jumps, so
    averaging one pass across idle days invents a deadline that does not exist.
    Price the runs, not the days.
    """
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise PoolExhausted("OPENROUTER_API_KEY not set; cannot read the seat key")
    req = urllib.request.Request(KEY_URL, headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        d = json.load(resp)["data"]
    return {
        "key_label": d.get("label"),
        "key_usage": float(d["usage"]),
        "key_limit": d.get("limit"),
        "key_limit_remaining": d.get("limit_remaining"),
        "key_usage_daily": d.get("usage_daily"),
        "key_usage_weekly": d.get("usage_weekly"),
        "key_usage_monthly": d.get("usage_monthly"),
    }


def sole_drawer_delta(
    current: dict[str, Any], prior: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Did anything other than this key draw on the account since ``prior``?

    The two endpoints report different absolute totals -- the key's ``usage`` is
    this key's lifetime spend, the account's ``total_usage`` is every key's --
    so only their *deltas* are comparable. When they agree, this key is the only
    thing drawing on the account, which settles the "something else must have
    spent it" argument before it starts.

    Returns ``None`` when there is no prior reading carrying ``key_usage``.
    """
    if not prior or prior.get("key_usage") is None or current.get("key_usage") is None:
        return None
    d_key = float(current["key_usage"]) - float(prior["key_usage"])
    d_acct = float(current["total_usage"]) - float(prior["total_usage"])
    return {
        "since": prior.get("at"),
        "delta_key_usage_usd": round(d_key, 9),
        "delta_account_usage_usd": round(d_acct, 9),
        "unexplained_usd": round(d_acct - d_key, 9),
        "matched": abs(d_acct - d_key) <= SOLE_DRAWER_TOLERANCE_USD,
    }


def append_reading(record: dict[str, Any], *, path: Path = POOL_READINGS) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def readings_all(*, path: Path = POOL_READINGS) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def readings_for_slice(slice_id: str, *, path: Path = POOL_READINGS) -> list[dict[str, Any]]:
    """Rows this slice drew. Rows written before BEAAA-36019 belong to BEAAA-35938."""
    return [r for r in readings_all(path=path) if r.get("slice", LEGACY_SLICE) == slice_id]


def preflight(
    cap_usd: float,
    *,
    label: str,
    slice_id: str,
    api_key: str | None = None,
    record: bool = True,
    path: Path = POOL_READINGS,
    pool: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Refuse to start when ``cap_usd`` exceeds what the account actually has left.

    Returns the appended reading. Raises :class:`PoolExhausted` otherwise, with
    the cap and the pool remainder both named in the message.
    """
    if cap_usd <= 0:
        raise ValueError(f"cap_usd must be positive, got {cap_usd!r}")
    if pool is None:
        pool = read_pool(api_key=api_key)
        try:
            pool = {**pool, **read_key(api_key=api_key)}
        except (urllib.error.URLError, KeyError, ValueError) as exc:  # pragma: no cover
            # The key read is diagnostic, not the gate. Losing it must not let a
            # slice start unchecked, and must not stop one the pool can cover.
            pool = {**pool, "key_read_error": repr(exc)}
    remaining = float(pool["remaining_usd"])
    ok = cap_usd <= remaining
    reading = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "label": label,
        "slice": slice_id,
        "kind": "preflight",
        "cap_usd": round(float(cap_usd), 6),
        "decision": "pass" if ok else "refused",
        "remaining_usd": round(remaining, 6),
        "drawn_since_baseline_usd": round(BASELINE_REMAINING_USD - remaining, 6),
        "baseline_remaining_usd": BASELINE_REMAINING_USD,
        "baseline_label": BASELINE_LABEL,
        "distinct_rows_journaled": 0,
        "total_credits": pool["total_credits"],
        "total_usage": pool["total_usage"],
    }
    for field in (
        "key_label", "key_usage", "key_limit", "key_limit_remaining",
        "key_usage_daily", "key_usage_weekly", "key_usage_monthly", "key_read_error",
    ):
        if field in pool:
            reading[field] = pool[field]
    prior = next(
        (r for r in reversed(readings_all(path=path)) if r.get("key_usage") is not None), None
    )
    reading["sole_drawer"] = sole_drawer_delta(reading, prior)
    if record:
        append_reading(reading, path=path)
    if not ok:
        raise PoolExhausted(
            f"refusing to start {slice_id}: authorized cap ${cap_usd:.2f} exceeds the "
            f"OpenRouter account pool remainder ${remaining:.2f} "
            f"(total_credits ${pool['total_credits']:.2f} - total_usage "
            f"${pool['total_usage']:.2f}). The key's limit_remaining is not spendable "
            f"money. Top up the account or lower the cap before any paid call."
        )
    return reading


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cap-usd", type=float, required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--slice", dest="slice_id", required=True)
    ap.add_argument("--no-record", action="store_true", help="read and decide without appending")
    args = ap.parse_args(argv)
    try:
        reading = preflight(
            args.cap_usd, label=args.label, slice_id=args.slice_id, record=not args.no_record
        )
    except PoolExhausted as exc:
        print(f"REFUSED: {exc}")
        return 2
    sd = reading.get("sole_drawer")
    if sd and not sd["matched"]:
        print(
            f"WARNING: ${sd['unexplained_usd']:.6f} of account draw since {sd['since']} is not "
            f"attributable to {reading.get('key_label')}. Another key is spending this pool."
        )
    print("PASS: " + json.dumps(reading, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
