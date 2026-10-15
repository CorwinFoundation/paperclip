#!/usr/bin/env python3
"""BEAAA-41114: Scanner Quality Report v1 (markdown + xlsx) from retained rows.

Report-only (``score_input=false``). Offline: it reads retained attempt rows,
never calls a model and never changes scoring. Per-unique-test outcomes come
from :func:`scripts.beaaa41114_report_aggregate.aggregate_unique_tests`, so a
registered case with no row is ``not_run`` and a case missing any of its K
seeds is ``could_not_run``.

Lanes and their decision method:

* conv-en / OSS (K=3, seeds 42/43/44): ``canary`` and ``must_include`` without
  a judge call are *deterministic*; ``judge``, ``pair`` and must_include
  fallbacks are *judged* by the two-judge fail-closed panel (disagreement is
  ``unresolved``).
* Full Scan H (K=5): deterministic end state. An attempt whose injection was
  never delivered to the agent is ``could_not_run`` (``injection_not_delivered``),
  never pass.
* Full Scan U (K=5): deterministic end state on the E sandbox.

On the chat-only VAPI target the action lanes (H, U) are emitted
``not_applicable`` per BEAAA-40217: no attempt is made and none counts as pass.

Wilson 95% intervals are over unique tests in the row (denominator stated).

CLI::

    python3 scripts/beaaa41114_quality_report.py --out artifacts/BEAAA-41114/report
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.beaaa41114_report_aggregate import OUTCOMES, aggregate_unique_tests  # noqa: E402

RUN = Path("artifacts/BEAAA-41114/full-run")
REF = "openai/gpt-4.1-mini"
REPORTED = ("pass", "fail", "flaky", "unresolved", "could_not_run", "not_applicable", "not_run")


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        gz = path.with_name(path.name + ".gz")
        if not gz.exists():
            return []
        path = gz
    opener = gzip.open if path.suffix == ".gz" else open
    out = []
    with opener(path, "rt") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


# --------------------------------------------------------------------------
# Per-lane row normalisation
# --------------------------------------------------------------------------

def conv_attempt(row: dict[str, Any]) -> dict[str, Any]:
    judged = bool(row.get("judges"))
    judge_v = [j.get("verdict") for j in row.get("judges") or []]
    rejudged = bool(row.get("rejudged"))
    if rejudged:
        # A re-judged row (scripts/beaaa41114_rejudge_402.py) copies its original; only the
        # judges re-sent in that pass are new spend. Target and VAPI cost stay on the original.
        cost = sum(float(j.get("cost_usd") or 0) for j in row.get("judges") or [] if j.get("rejudged"))
        vapi = 0.0
    else:
        cost = float(row.get("target_cost_usd") or 0) + sum(float(j.get("cost_usd") or 0) for j in row.get("judges") or [])
        vapi = float(row.get("vapi_cost_usd") or 0) + float(row.get("vapi_cost_usd_b") or 0)
    return {"case_id": row["case_id"], "seed": row["seed"], "verdict": row["verdict"],
            "family": row.get("family") or "", "source": row.get("source") or "",
            "method": "judged" if judged else "deterministic",
            "judge_pair": tuple(judge_v) if len(judge_v) == 2 else None,
            "rejudged": rejudged, "openrouter_usd": cost, "vapi_usd": vapi}


def _usage_cost(row: dict[str, Any]) -> float:
    return sum(float((x.get("usage") or {}).get("cost") or 0)
               for x in row.get("raw_responses") or [] if isinstance(x, dict))


def h_attempt(row: dict[str, Any]) -> dict[str, Any]:
    verdict = row["verdict"] if row.get("injection_delivered") else "could_not_run"
    return {"case_id": row["stable_id"], "seed": row["seed"], "verdict": verdict,
            "family": row.get("surface") or "", "source": row.get("source") or "",
            "method": "deterministic", "judge_pair": None,
            "openrouter_usd": _usage_cost(row), "vapi_usd": 0.0}


def u_attempt(row: dict[str, Any]) -> dict[str, Any]:
    return {"case_id": row["stable_id"], "seed": row["seed"], "verdict": row["verdict"],
            "family": row.get("family") or "", "source": "backbond_written_sandbox",
            "method": "deterministic", "judge_pair": None,
            "openrouter_usd": _usage_cost(row), "vapi_usd": 0.0}


def conv_universe(corpus: str) -> list[tuple[str, str, str, str]]:
    """(case_id, family, source, method_hint) for every registered case."""
    from scripts import beaaa38922_conv_live_run as conv
    cases = conv.OSS_CASES if corpus == "oss" else conv.ALL_CASES
    out = []
    for c in cases:
        src = (getattr(c, "provenance", None) or {}).get("source", "backbond_written")
        out.append((c.case_id, c.family_id, src, c.check))
    return out


def h_universe(h_rows: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    # The 813-case run is offset 500 / limit 813 of the H corpus; its universe
    # is the requested slice. Every requested case has at least one row.
    seen: dict[str, tuple[str, str, str, str]] = {}
    for r in h_rows:
        seen.setdefault(r["stable_id"], (r["stable_id"], r.get("surface") or "", r.get("source") or "", "end_state"))
    return list(seen.values())


def u_universe() -> list[tuple[str, str, str, str]]:
    from evals import full_scan_e_second_wave as u
    seen: dict[str, Any] = {}
    for c in u.SECOND_WAVE_CASES:
        seen.setdefault(c.uniqueness_key, c)
    return [(c.stable_id, c.family, "backbond_written_sandbox", "end_state") for c in seen.values()]


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def lane_table(target: str, lane: str, universe: list[tuple[str, str, str, str]],
               attempts: list[dict[str, Any]], seeds: list[int],
               not_applicable: bool = False) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    hint: dict[str, str] = {}
    for cid, fam, src, check in universe:
        groups[(fam, src)].append(cid)
        # Static method for cases with no retained attempt: judge/pair are
        # judged; canary, must_include (primary check) and end_state are not.
        hint[cid] = "judged" if check in ("judge", "pair") else "deterministic"
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in attempts:
        by_case[a["case_id"]].append(a)
    out = []
    for (fam, src), ids in sorted(groups.items()):
        if not_applicable:
            agg_counts = Counter({"not_applicable": len(ids)})
            atts: list[dict[str, Any]] = []
        else:
            atts = [a for cid in ids for a in by_case.get(cid, [])]
            agg = aggregate_unique_tests(
                [{"case_id": a["case_id"], "seed": a["seed"], "verdict": a["verdict"]} for a in atts],
                expected_case_ids=ids, seeds=seeds)
            agg_counts = Counter(agg["counts"])
        n = len(ids)
        methods = Counter()
        for cid in ids:
            ms = {a["method"] for a in by_case.get(cid, [])} or {hint[cid]}
            methods["judged" if "judged" in ms else "deterministic"] += 1
        superseded = {(a["case_id"], a["seed"]) for a in atts if a.get("rejudged")}
        pairs = [a["judge_pair"] for a in atts if a["judge_pair"]
                 and (a.get("rejudged") or (a["case_id"], a["seed"]) not in superseded)]
        # Agreement is measured only where both judges answered; judge errors are counted apart.
        answered = [p for p in pairs if set(p) <= {"pass", "fail"}]
        row: dict[str, Any] = {
            "target": target, "lane": lane, "family": fam, "source": src, "unique_tests": n,
            "method_deterministic": methods["deterministic"],
            "method_judged": methods["judged"],
            "attempts": sum(1 for a in atts if not a.get("rejudged")),
            "judged_attempts_two_judges": len(answered),
            "judge_agree": sum(1 for p in answered if p[0] == p[1]),
            "judge_unresolved": sum(1 for p in answered if p[0] != p[1]),
            "judge_could_not_run": len(pairs) - len(answered),
            "openrouter_usd": round(sum(a["openrouter_usd"] for a in atts), 6),
            "vapi_usd": round(sum(a["vapi_usd"] for a in atts), 6),
        }
        for o in REPORTED:
            row[o] = agg_counts.get(o, 0)
            ci = wilson(row[o], n)
            row[f"{o}_wilson95"] = f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci else ""
        out.append(row)
    return out


def totals(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    agg: dict[tuple, dict[str, Any]] = {}
    numeric = ("unique_tests", "method_deterministic", "method_judged", "attempts",
               "judged_attempts_two_judges", "judge_agree", "judge_unresolved", "judge_could_not_run",
               "openrouter_usd", "vapi_usd") + REPORTED
    for r in rows:
        k = tuple(r[x] for x in keys)
        t = agg.setdefault(k, {x: r[x] for x in keys} | {n: 0 for n in numeric})
        for n in numeric:
            t[n] += r[n]
    out = []
    for t in agg.values():
        for o in REPORTED:
            ci = wilson(t[o], t["unique_tests"])
            t[f"{o}_wilson95"] = f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci else ""
        t["openrouter_usd"] = round(t["openrouter_usd"], 4)
        t["vapi_usd"] = round(t["vapi_usd"], 4)
        out.append(t)
    return out


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

COLS = ["target", "lane", "family", "source", "unique_tests", "method_deterministic", "method_judged",
        "pass", "pass_wilson95", "fail", "fail_wilson95", "flaky", "flaky_wilson95",
        "unresolved", "unresolved_wilson95", "could_not_run", "could_not_run_wilson95",
        "not_applicable", "not_applicable_wilson95", "not_run",
        "attempts", "judged_attempts_two_judges", "judge_agree", "judge_unresolved", "judge_could_not_run",
        "openrouter_usd", "vapi_usd"]


def md_table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


def write_xlsx(path: Path, sheets: dict[str, tuple[list[str], list[dict[str, Any]]]]) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, (cols, rows) in sheets.items():
        ws = wb.create_sheet(name[:31])
        ws.append(cols)
        for r in rows:
            ws.append([r.get(c, "") for c in cols])
        ws.freeze_panes = "A2"
    # Deterministic bytes: fixed workbook timestamps.
    import datetime as _dt
    fixed = _dt.datetime(2026, 10, 14)
    wb.properties.created = fixed
    wb.properties.modified = fixed
    wb.save(path)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


JUDGE_402_COLS = ["target", "lane", "rows_with_judge_402", "of_which_could_not_run",
                  "of_which_unresolved_mixed_panel", "rejudged_successors", "still_pending"]


def _has_402(row: dict[str, Any]) -> bool:
    return any("402" in str(j.get("error") or "") for j in row.get("judges") or [])


def judge_402_row(target: str, lane: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Attempts whose judge panel hit OpenRouter HTTP 402, counted apart from verdict buckets.

    Both judges failing gives ``could_not_run``. One judge failing next to an answered judge
    gives ``unresolved``, because ``resolve_panel`` treats any mixed panel as disagreement.
    ``rejudged_successors`` are rows appended by scripts/beaaa41114_rejudge_402.py; an affected
    attempt is still pending until one exists.
    """
    affected = [r for r in rows if not r.get("rejudged") and _has_402(r)]
    redone = {(r["case_id"], r["seed"]) for r in rows if r.get("rejudged")}
    return {"target": target, "lane": lane, "rows_with_judge_402": len(affected),
            "of_which_could_not_run": sum(1 for r in affected if r.get("verdict") == "could_not_run"),
            "of_which_unresolved_mixed_panel": sum(1 for r in affected if r.get("verdict") == "unresolved"),
            "rejudged_successors": sum(1 for r in affected if (r["case_id"], r["seed"]) in redone),
            "still_pending": sum(1 for r in affected if (r["case_id"], r["seed"]) not in redone)}


def judge_402_section(report: dict[str, Any]) -> list[str]:
    rows = report.get("judge_402") or []
    if not any(r["rows_with_judge_402"] for r in rows):
        return []
    return ["## Judge HTTP 402 outage (OpenRouter credit, 2026-10-15)",
            "From about 15:15Z every judge call returned `HTTP 402 Payment Required`. These counts are",
            "attempt rows affected, not a verdict bucket. Before re-judging they sit in `could_not_run` (both",
            "judges failed) or `unresolved` (one judge failed next to an answered judge). Re-judged successors",
            "replace them in every count above; `still_pending` must be 0 for a final report.",
            "Re-judging re-sends each retained judge prompt to the same pinned model and uses the runner's own",
            "parser, so a judge that answers twice in one reply (e.g. `fail`, then a self-revision) still fails",
            "closed as `could-not-run`, exactly as in the original run; those count in `judge_could_not_run`.",
            "", md_table(rows, JUDGE_402_COLS), ""]


def build(run_dir: Path, vapi_dir: Path | None, vapi_label: str | None) -> dict[str, Any]:
    ref = run_dir / f"ref_{REF.replace('/', '_')}"
    tag = REF.replace("/", "_")
    conv_rows = read_rows(ref / "conv-en" / f"conv_live_{tag}_rows.jsonl")
    oss_rows = read_rows(ref / "oss" / f"oss_conv_live_{tag}_rows.jsonl")
    h_rows = read_rows(ref / "full_scan_h" / f"full_scan_h_live_{tag}_rows.jsonl")
    u_rows = read_rows(ref / "full_scan_u" / f"full_scan_u_live_{tag}_rows.jsonl")
    conv_u, oss_u, h_u, u_u = conv_universe("conv-en"), conv_universe("oss"), h_universe(h_rows), u_universe()

    targets: list[tuple[str, dict[str, list[dict[str, Any]]] | None]] = [
        (REF, {"conv-en": conv_rows, "oss": oss_rows})]
    if vapi_dir is not None:
        vtag = vapi_dir.name
        vtext = {
            "conv-en": read_rows(vapi_dir / "conv-en" / f"conv_live_{vtag}_rows.jsonl"),
            "oss": read_rows(vapi_dir / "oss" / f"oss_conv_live_{vtag}_rows.jsonl")}
        if not any(vtext.values()):
            # A given --vapi-dir with no rows must not render as a quiet "pending" draft.
            raise SystemExit(f"--vapi-dir {vapi_dir}: no retained VAPI rows found")
        targets.append((vapi_label or f"vapi:{vtag.removeprefix('vapi_')}", vtext))
    else:
        targets.append(("vapi:<pending>", None))

    detail: list[dict[str, Any]] = []
    judge_402 = [judge_402_row(t, lane, text[lane]) for t, text in targets if text is not None
                 for lane in ("conv-en", "oss")]
    for target, text in targets:
        for lane, universe in (("conv-en", conv_u), ("oss", oss_u)):
            if text is None:
                rows = lane_table(target, lane, universe, [], [42, 43, 44])
            else:
                rows = lane_table(target, lane, universe, [conv_attempt(r) for r in text[lane]], [42, 43, 44])
            detail.extend(rows)
        is_vapi = target != REF
        detail.extend(lane_table(target, "full_scan_h", h_u, [] if is_vapi else [h_attempt(r) for r in h_rows],
                                 [0, 1, 2, 3, 4], not_applicable=is_vapi))
        detail.extend(lane_table(target, "full_scan_u", u_u, [] if is_vapi else [u_attempt(r) for r in u_rows],
                                 [0, 1, 2, 3, 4], not_applicable=is_vapi))
    probe = vapi_dir / "balance_probe.json" if vapi_dir is not None else None
    return {"detail": detail, "judge_402": judge_402,
            "vapi_balance": json.loads(probe.read_text()) if probe is not None and probe.exists() else None,
            "by_target_lane": totals(detail, ("target", "lane")),
            "by_target_source": totals(detail, ("target", "source")),
            "attempt_rows": {"conv-en": len(conv_rows), "oss": len(oss_rows), "full_scan_h": len(h_rows),
                             "full_scan_u": len(u_rows)}}


def versions() -> dict[str, Any]:
    from scripts import beaaa38922_conv_live_run as conv
    from scripts.beaaa41114_report_aggregate import __doc__ as _  # noqa: F401
    from evals import full_scan_e_second_wave as u
    return {
        "report_commit": git("rev-parse", "HEAD"),
        "master_base": git("merge-base", "HEAD", "origin/master"),
        "conv_en_pack_ids": conv.PACK_IDS, "oss_pack_ids": conv.OSS_PACK_IDS,
        "full_scan_u_corpus": getattr(u, "CORPUS_VERSION", "full_scan_e_action_second_wave@0.1.0-report-only"),
        "full_scan_h_slice": "beaaa39491 --offset 500 --limit 813 --k 5",
        "reference_target": REF, "judge_models": list(conv.JUDGE_MODELS),
        "conv_seeds": [42, 43, 44], "action_seeds": [0, 1, 2, 3, 4], "score_input": False,
    }


REPRO = """\
# reference target (OpenRouter openai/gpt-4.1-mini), retained rows resume with no repeat paid calls
python3 scripts/beaaa38922_conv_live_run.py --corpus conv-en --seeds 42 43 44 --cap-usd 16 --workers 12 --out artifacts/BEAAA-41114/full-run/ref_openai_gpt-4.1-mini/conv-en
python3 scripts/beaaa38922_conv_live_run.py --corpus oss --seeds 42 43 44 --cap-usd 12 --workers 12 --out artifacts/BEAAA-41114/full-run/ref_openai_gpt-4.1-mini/oss
PYTHONPATH=. python3 scripts/beaaa39491_full_scan_h_live_run.py --offset 500 --limit 813 --k 5 --cap-usd 8 --workers 8 --out artifacts/BEAAA-41114/full-run/ref_openai_gpt-4.1-mini/full_scan_h
PYTHONPATH=. python3 scripts/beaaa41114_full_scan_u_live_run.py --k 5 --cap-usd 4 --workers 8 --resume --out artifacts/BEAAA-41114/full-run/ref_openai_gpt-4.1-mini/full_scan_u
# VAPI target (BEAAA-39144 assistant, chat-only; H/U emitted not_applicable)
VAPI_API_KEY=... python3 scripts/beaaa38922_conv_live_run.py --adapter vapi --vapi-assistant-id <uuid> --corpus conv-en --seeds 42 43 44 --out artifacts/BEAAA-41114/full-run/vapi_<id8>/conv-en
VAPI_API_KEY=... python3 scripts/beaaa38922_conv_live_run.py --adapter vapi --vapi-assistant-id <uuid> --corpus oss --seeds 42 43 44 --out artifacts/BEAAA-41114/full-run/vapi_<id8>/oss
# report
python3 scripts/beaaa41114_quality_report.py --out artifacts/BEAAA-41114/report
"""


def vapi_spend_section(report: dict[str, Any]) -> list[str]:
    """VAPI spend per item from per-row vapi_cost_usd, plus the account-balance probe outcome."""
    rows = [r for r in report["by_target_lane"] if r["target"] not in (REF, "vapi:<pending>")]
    if not rows:
        return []
    out = ["## VAPI spend and account balance",
           "VAPI spend is the sum of each retained chat's `vapi_cost_usd` (per row in the XLSX attempt data).",
           ""]
    out += [f"- {r['lane']}: ${r['vapi_usd']:.4f}" for r in rows]
    out.append(f"- total: ${sum(r['vapi_usd'] for r in rows):.4f}")
    probe = report.get("vapi_balance")
    out.append("")
    out.append("Account balance before/after: " + (
        probe["conclusion"] + " Probe (" + probe["probed_at_utc"] + "): "
        + ", ".join(f"`{k}` → {v}" for k, v in probe["results"].items())
        if probe else "not probed."))
    out.append("")
    return out


def status_line(report: dict[str, Any]) -> str:
    if any(r["target"] == "vapi:<pending>" for r in report["detail"]):
        return "**Status: DRAFT — VAPI target not yet run; its text lanes show `not_run`.**"
    not_run = sum(int(r.get("not_run") or 0) for r in report["by_target_lane"])
    if not_run:
        return f"**Status: DRAFT — {not_run} unique tests still `not_run`.**"
    pending = sum(r["still_pending"] for r in report.get("judge_402") or [])
    if pending:
        return f"**Status: DRAFT — {pending} attempts await re-judging after the judge HTTP 402 outage.**"
    return "**Status: complete for both targets.**"


def render(report: dict[str, Any], ver: dict[str, Any]) -> str:
    head = [
        "# BackBond Scanner Quality Report v1 (BEAAA-41114)",
        "",
        "Report-only: `score_input=false`. No Starter, rubric, BackBond Score, API, lane, pack or family change.",
        "Outcome counts are per **unique test** (counted once). `not_applicable` and `could_not_run` are never pass.",
        "Wilson 95% intervals use the unique tests in that row as the denominator.",
        "",
        status_line(report),
        "",
        "## Versions",
        "```json", json.dumps(ver, indent=1), "```",
        "",
        "## Retained attempt rows (reference target)",
        "```json", json.dumps(report["attempt_rows"], indent=1), "```",
        "",
        "## Notes on method",
        "- conv-en/OSS: K=3 seeds 42/43/44; pass^k means every seed passed; mixed pass/fail is flaky; any",
        "  seed could_not_run makes the test could_not_run; two-judge disagreement is unresolved (fail closed).",
        "- Full Scan H: an attempt whose injection was never delivered (the H runner exposes only E tools)",
        "  is could_not_run `injection_not_delivered`, not pass. A unique test needs all 5 seeds decided, so",
        "  a test with any undelivered seed is could_not_run. This is a runner coverage gap, not a target result.",
        "- Method split: a test is `judged` if any retained attempt was decided by the two-judge panel",
        "  (including a must_include judge fallback); unrun tests use their registered check type.",
        "- VAPI target: the assistant's stored configuration is used unchanged, so the case system prompt",
        "  is not delivered; canary cases whose canary exists only there are could_not_run",
        "  `target_system_prompt_fixed`. VAPI takes no seed, so K=3 there is repetition. H/U are",
        "  not_applicable on VAPI (chat-only, BEAAA-40217).",
        "",
        *vapi_spend_section(report),
        *judge_402_section(report),
        "## By target and lane",
        md_table(report["by_target_lane"], ["target", "lane"] + COLS[4:]),
        "",
        "## By target and source",
        md_table(report["by_target_source"], ["target", "source"] + COLS[4:]),
        "",
        "## By target, lane, family and source",
        md_table(report["detail"], COLS),
        "",
        "## Reproduction",
        "```sh", REPRO.rstrip(), "```",
        "",
    ]
    return "\n".join(head)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=str(RUN))
    ap.add_argument("--vapi-dir", default=None, help="retained VAPI run dir (vapi_<id8>)")
    ap.add_argument("--vapi-label", default=None)
    ap.add_argument("--out", default="artifacts/BEAAA-41114/report")
    args = ap.parse_args(argv)
    report = build(Path(args.run_dir), Path(args.vapi_dir) if args.vapi_dir else None, args.vapi_label)
    ver = versions()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    md = out / "scanner_quality_report_v1.md"
    xl = out / "scanner_quality_report_v1.xlsx"
    md.write_text(render(report, ver))
    write_xlsx(xl, {
        "by_target_lane": (["target", "lane"] + COLS[4:], report["by_target_lane"]),
        "by_target_source": (["target", "source"] + COLS[4:], report["by_target_source"]),
        "detail": (COLS, report["detail"]),
        "judge_http_402": (JUDGE_402_COLS, report["judge_402"]),
        "versions": (["key", "value"], [{"key": k, "value": json.dumps(v) if not isinstance(v, str) else v}
                                        for k, v in ver.items()]),
        "reproduction": (["command"], [{"command": c} for c in REPRO.splitlines()]),
    })
    sums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (md, xl)}
    (out / "SHA256SUMS").write_text("".join(f"{h}  {n}\n" for n, h in sums.items()))
    print(json.dumps(sums, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
