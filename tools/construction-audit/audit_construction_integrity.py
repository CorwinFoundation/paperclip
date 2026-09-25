#!/usr/bin/env python3
"""BEAAA-35660 construction-integrity predicate.

Applies the signatures in ``construction_signatures.v1.json`` to the
construction of every row in the canonical active-test registry — the same
registry that decides ``scoring_active``, so the 479 production-scored rows are
addressed by the identical field the scorer reads.

A row is flagged when a signature regex matches its construction. Judgement is
not an input. A row whose construction cannot be read reports ``indeterminate``
with a reason and is never counted as clear.

Construction means only the text that actually reaches the target: prompt-bearing
literals in the constructor code, and prompt-bearing values in the row's corpus
record. Registry bookkeeping is not construction — see ``prompt_extract``.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from prompt_extract import python_prompt_literals, record_prompt_text  # noqa: E402
from tools.active_test_registry import is_canonical_scored_row  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SIGS = HERE / "construction_signatures.v1.json"
CANDIDATE_INVENTORY = REPO / "artifacts" / "beaaa-18720" / "unified-probe-inventory.jsonl"


def load_signatures() -> list[dict]:
    spec = json.loads(SIGS.read_text(encoding="utf-8"))
    for sig in spec["signatures"]:
        sig["compiled"] = [re.compile(p, re.IGNORECASE) for p in sig["patterns"]]
    return spec["signatures"]


def registry_rows(rows_path: Path) -> list[dict]:
    data = json.loads(rows_path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data["rows"]


def _read(path: Path, cache: dict) -> str | None:
    key = str(path)
    if key not in cache:
        try:
            cache[key] = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
        except OSError:
            cache[key] = None
    return cache[key]


def build_constructor_index() -> dict[str, list[str]]:
    """basename of a source pack -> constructor files whose prompt text builds it."""
    tracked = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "scripts", "harness", "evals",
         "engineering", "api", "determination", "tools"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    index: dict[str, list[str]] = {}
    scan_cache: dict = {}
    for rel in tracked:
        if not rel.endswith(".py"):
            continue
        text = _read(REPO / rel, scan_cache) or ""
        if not text:
            continue
        for token in re.findall(r"[\w./-]+\.(?:yaml|yml|jsonl|json)", text):
            index.setdefault(Path(token).name, []).append(rel)
    return index


RUNNER_MODULES = {
    "bench_wrapper": ["harness/lane_c_adapter.py"],
    "garak_wrapper": ["harness/lane_c_adapter.py"],
}


def resolve_constructors(row: dict, pack: str, ctor_index: dict) -> tuple[list[str], list[str]]:
    """Constructor files for a row, by source-pack reference, pack stem, then runner."""
    routes: list[str] = []
    found: list[str] = []
    if pack:
        by_ref = sorted(set(ctor_index.get(Path(pack).name, [])))[:12]
        if by_ref:
            routes.append("source_pack_reference")
            found.extend(by_ref)
        stem_mod = f"harness/bench/{Path(pack).stem}.py"
        if (REPO / stem_mod).is_file() and stem_mod not in found:
            routes.append("pack_stem_module")
            found.append(stem_mod)
    for mod in RUNNER_MODULES.get(row.get("runner") or "", []):
        if (REPO / mod).is_file() and mod not in found:
            routes.append("runner_dispatch")
            found.append(mod)
    return found, routes


def load_candidate_index() -> dict[str, dict]:
    """case identity -> BEAAA-18720 inventory record."""
    out: dict[str, dict] = {}
    if not CANDIDATE_INVENTORY.is_file():
        return out
    with CANDIDATE_INVENTORY.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            for key in ("case_id", "probe_id", "id", "dedupe_sha256"):
                val = rec.get(key)
                if isinstance(val, str):
                    out.setdefault(val, rec)
    return out


def load_pack_records(path: Path, cache: dict) -> dict[str, dict]:
    """case_id -> record, for a probe-mapping yaml or corpus jsonl."""
    key = ("pack", str(path))
    if key in cache:
        return cache[key]
    records: dict[str, dict] = {}
    text = _read(path, cache)
    if text is not None:
        try:
            if path.suffix in (".yaml", ".yml"):
                import yaml
                data = yaml.safe_load(text) or {}
                buckets = []
                if isinstance(data, dict):
                    for value in data.values():
                        if isinstance(value, list):
                            buckets.extend(v for v in value if isinstance(v, dict))
                elif isinstance(data, list):
                    buckets = [v for v in data if isinstance(v, dict)]
                for rec in buckets:
                    for field in ("case_id", "id", "probe_id", "name"):
                        val = rec.get(field)
                        if isinstance(val, str):
                            records.setdefault(val, rec)
            elif path.suffix == ".jsonl":
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    for field in ("case_id", "id", "probe_id"):
                        val = rec.get(field)
                        if isinstance(val, str):
                            records.setdefault(val, rec)
        except Exception:
            records = {}
    cache[key] = records
    return records


def current_score(row: dict) -> dict:
    return {
        "priced_score_weight": row.get("priced_score_weight"),
        "score_input": row.get("score_input"),
        "saved_verdict": row.get("saved_verdict"),
        "weight": row.get("weight"),
        "score_contribution": row.get("score_contribution"),
        "effective_underwriting_weight": row.get("effective_underwriting_weight"),
    }


def audit(rows: list[dict]) -> dict:
    sigs = load_signatures()
    ctor_index = build_constructor_index()
    candidates = load_candidate_index()
    cache: dict = {}

    ledger = []
    for row in rows:
        case_id = row.get("case_id") or ""
        pack = row.get("source_pack") or ""
        texts: list[tuple[str, str, str]] = []
        reasons: list[str] = []

        # 1. the row's own corpus record, prompt-bearing values only
        rec = None
        if pack:
            rec = load_pack_records(REPO / pack, cache).get(case_id)
        if rec is None and row.get("case_class") == "content_candidate_probe":
            rec = candidates.get(case_id) or candidates.get(
                row.get("candidate_dedupe_sha256") or ""
            )
        if rec is not None:
            for chunk in record_prompt_text(rec):
                if isinstance(chunk, str) and chunk.strip():
                    texts.append((f"{pack or 'beaaa-18720 inventory'}#{case_id}", chunk, "target"))
        else:
            reasons.append(
                f"no corpus record for case_id {case_id!r} in {pack or '<no source_pack>'}"
            )

        # 2. constructor code, prompt-bearing literals only, tagged by channel
        ctors, routes = resolve_constructors(row, pack, ctor_index)
        for ctor in ctors:
            lit_key = ("lits", ctor)
            if lit_key not in cache:
                src = _read(REPO / ctor, cache)
                cache[lit_key] = python_prompt_literals(src) if src else []
            for lit in cache[lit_key]:
                texts.append((ctor, lit["text"], lit["channel"]))
        if not ctors:
            reasons.append(f"no constructor code located for source pack {pack or '<none>'}")

        target_hits, judge_hits = [], []
        for sig in sigs:
            for where, text, channel in texts:
                hit = next((rx.pattern for rx in sig["compiled"] if rx.search(text)), None)
                if hit:
                    entry = {
                        "signature": sig["id"],
                        "matched_in": where,
                        "channel": channel,
                        "pattern": hit,
                        "excerpt": text[:220],
                    }
                    (target_hits if channel == "target" else judge_hits).append(entry)
                    break

        has_target_text = any(c == "target" for _, _, c in texts)
        if target_hits:
            verdict = "flagged"
        elif not has_target_text:
            verdict = "indeterminate"
            reasons.append("no target-channel construction text could be read")
        elif not ctors:
            verdict = "indeterminate"
        else:
            verdict = "clear"
        matched = target_hits

        ledger.append({
            "case_id": case_id,
            "test_id": row.get("test_id"),
            "family": row.get("category"),
            "pillar": row.get("pillar"),
            "rubric_point": row.get("risk_or_rubric_id"),
            "runner": row.get("runner"),
            "source_pack": pack,
            "scoring_active": bool(row.get("scoring_active")),
            "is_canonical_scored_row": is_canonical_scored_row(row),
            "dev_scoring_active": bool(row.get("dev_scoring_active")),
            "reporting_status": row.get("reporting_status"),
            "current_score": current_score(row),
            "construction_sources": sorted({w for w, _, _ in texts}),
            "constructor_routes": routes,
            "target_text_read": has_target_text,
            "judge_channel_hits": [h["signature"] for h in judge_hits],
            "judge_channel_detail": judge_hits[:3],
            "verdict": verdict,
            "signatures_matched": [m["signature"] for m in matched],
            "match_detail": matched,
            "indeterminate_reasons": reasons if verdict == "indeterminate" else [],
        })
    return {"rows": ledger}


def summarise(ledger: list[dict]) -> dict:
    def tally(subset):
        out = {"total": len(subset), "flagged": 0, "clear": 0, "indeterminate": 0}
        for e in subset:
            out[e["verdict"]] += 1
        return out

    prod = [e for e in ledger if e["scoring_active"]]
    canonical = [e for e in ledger if e["is_canonical_scored_row"]]
    dev = [e for e in ledger if e["dev_scoring_active"]]
    sig_counts: dict[str, int] = {}
    for e in ledger:
        for s in e["signatures_matched"]:
            sig_counts[s] = sig_counts.get(s, 0) + 1
    judge_only = [e for e in ledger if e["judge_channel_hits"]]
    return {
        "all_registry_rows": tally(ledger),
        "judge_channel_matches_not_the_defect": {
            "all_rows": len(judge_only),
            "production_scored": len([e for e in judge_only if e["scoring_active"]]),
        },
        "production_scored_479": tally(prod),
        "canonical_scored_432": tally(canonical),
        "dev_scored": tally(dev),
        "signature_hits_all_rows": dict(sorted(sig_counts.items())),
        "signature_hits_production_scored": {
            s: sum(1 for e in prod if s in e["signatures_matched"])
            for s in sorted(sig_counts)
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", default="/tmp/atr_rows.json",
                    help="output of active_test_registry.py --registry-out")
    ap.add_argument("--out", default="artifacts/beaaa-35660/construction_ledger.json")
    args = ap.parse_args()

    rows = registry_rows(Path(args.rows))
    result = audit(rows)
    summary = summarise(result["rows"])
    payload = {
        "issue": "BEAAA-35660",
        "signature_spec": "tools/construction-audit/construction_signatures.v1.json",
        "row_source": "tools/active_test_registry.py --registry-out",
        "rows_audited": len(result["rows"]),
        "summary": summary,
        "rows": result["rows"],
    }
    dest = REPO / args.out
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
