"""BEAAA-39654: server-render the first response of one report token.

Input: a report API JSON document (as returned by
https://api.backbond.ai/scan/v1/reports/<token>) and its token. Output: a
static page derived from scan/r/index.html with the title, target host, report
date and headline counts already in the HTML, so non-JS clients and link
previews see the report instead of an empty template. The page keeps the same
stylesheet, script and element ids; scan/report.js hydrates it as usual and
removes the server-rendered summary after a successful render.

Counts use the live viewer's semantics (combined-viewer.js `combinedTests` and
report-baseline.js): only `pass` and `fail` are adjudicated; every other
verdict (could_not_run, inconclusive, unverified, ...) is an evidence gap and is
never counted as a pass. When the API document carries `counts`, the computed
values must agree with it or generation fails.

Every JSON value is treated as adversarial and HTML-escaped. The output has no
inline script or inline style, and never includes a score. Deterministic: same
inputs, same bytes.

Usage:
  python3 prerender.py REPORT.json TOKEN [--out PATH ...] [--expect k=v,...]
Without --out, writes scan/r/<TOKEN>.html and scan/r/<TOKEN>/index.html.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / 'scan' / 'r' / 'index.html'
COMBINED_VIEWER = ROOT / 'combined-viewer.js'
TOKEN_RE = re.compile(r'^[a-f0-9]{32}$')
STARTER_PACKS = {'starter-100@1', 'starter-100@2', 'starter-100@3', 'starter-100@4', 'starter-voice-50@1'}
COMBINED_EXPECTED = {'starter-100@3': 100, 'starter-voice-50@1': 60, 'codex-targeted-20@1': 60}
COMBINED_HOST = 'learning.kaltura.com'
VOICE_ROSTER = [('VDN', 10, 'in_domain_normal'), ('VPF', 5, 'product_fabrication'), ('VASR', 5, 'asr_shaped_input'),
                ('VML', 15, 'multilingual_guardrails'), ('VSS', 5, 'screen_share_readback'),
                ('VSP', 5, 'summary_poisoning'), ('VGD', 5, 'goals_data_collection'),
                ('VHD', 5, 'human_disclosure'), ('VAA', 5, 'abuse_toward_agent')]
TARGETED_ALLOWED = {f'{p}{i:02d}' for p, n in (('S', 6), ('I', 6), ('U', 4), ('P', 2), ('F', 2)) for i in range(1, n + 1)}
TARGETED_ID = re.compile(r'^KFR-([SIUPF]\d{2})-R([123])$')
GAP_LABELS = {'could_not_run': 'could not run', 'connection_error': 'connection error',
              'inconclusive': 'inconclusive', 'unverified': 'unverified'}


class PrerenderError(ValueError):
    pass


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _voice_roster() -> dict[str, str]:
    return {f'{prefix}-{i:02d}': family for prefix, count, family in VOICE_ROSTER for i in range(1, count + 1)}


def combined_tests(data: dict) -> list[dict]:
    """Python port of combined-viewer.js combinedTests: fail closed on any mismatch."""
    if data.get('endpoint_host') != COMBINED_HOST:
        raise PrerenderError('combined target')
    sets = data.get('result_sets')
    if not isinstance(sets, list) or len(sets) != 3:
        raise PrerenderError('combined slots')
    packs: set[str] = set()
    ids: set[str] = set()
    out: list[dict] = []
    for pack in sets:
        version = pack.get('pack_version') if isinstance(pack, dict) else None
        tests = pack.get('tests') if isinstance(pack, dict) else None
        if (version not in COMBINED_EXPECTED or version in packs or not isinstance(tests, list)
                or pack.get('status') != 'complete' or len(tests) != COMBINED_EXPECTED[version]):
            raise PrerenderError('combined pack')
        packs.add(version)
        if version == 'starter-voice-50@1':
            roster = _voice_roster()
            if any(roster.get(t.get('id')) != t.get('family') for t in tests):
                raise PrerenderError('combined voice roster')
        if version == 'codex-targeted-20@1':
            scenarios: dict[str, set[str]] = {}
            for t in tests:
                match = TARGETED_ID.match(str(t.get('id') or t.get('test_id') or ''))
                if not match or match.group(1) not in TARGETED_ALLOWED:
                    raise PrerenderError('targeted identity')
                scenarios.setdefault(match.group(1), set()).add(match.group(2))
            if len(scenarios) != 20 or any(len(r) != 3 for r in scenarios.values()):
                raise PrerenderError('targeted repetitions')
        for t in tests:
            test_id = str(t.get('id') or t.get('test_id') or '')
            if not test_id or test_id in ids:
                raise PrerenderError('duplicate combined identity')
            ids.add(test_id)
            out.append({**t, 'id': test_id, 'pack_version': version})
    return out


def compute_counts(data: dict) -> dict:
    if not isinstance(data, dict):
        raise PrerenderError('schema')
    combined = data.get('report_kind') == 'starter_extensions'
    if combined:
        tests = combined_tests(data)
    else:
        tests = data.get('tests')
        if not isinstance(tests, list) or data.get('pack_version') not in STARTER_PACKS:
            raise PrerenderError('schema')
    raw = [t.get('verdict') if isinstance(t, dict) else None for t in tests]
    passed = sum(1 for v in raw if v == 'pass')
    failed = sum(1 for v in raw if v == 'fail')
    gaps: dict[str, int] = {}
    for v in raw:
        if v in ('pass', 'fail'):
            continue
        key = GAP_LABELS.get(v if isinstance(v, str) else '', 'other')
        gaps[key] = gaps.get(key, 0) + 1
    scenarios = set()
    for t in tests:
        test_id = str(t.get('id') or t.get('test_id') or '')
        match = TARGETED_ID.match(test_id) if t.get('pack_version') == 'codex-targeted-20@1' else None
        scenarios.add('KFR-' + match.group(1) if match else test_id)
    counts = {'scenarios': len(scenarios), 'executions': len(tests), 'passed': passed, 'failed': failed,
              'gaps': len(tests) - passed - failed, 'gap_breakdown': dict(sorted(gaps.items())),
              'adjudicable': passed + failed}
    api = data.get('counts')
    if isinstance(api, dict):
        if (api.get('passed'), api.get('failed'), api.get('could_not_run')) != (passed, failed, counts['gaps']):
            raise PrerenderError(f'computed counts disagree with API counts: {api!r}')
    return counts


def _viewer_combined_meta() -> str:
    source = COMBINED_VIEWER.read_text(encoding='utf-8')
    match = re.search(r"\$\('business-meta'\)\.textContent='([^'\\]*)'", source)
    if not match:
        raise PrerenderError('combined viewer business-meta literal not found')
    return match.group(1)


def _long_date(when: datetime) -> str:
    months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
              'October', 'November', 'December']
    return f'{when.day} {months[when.month - 1]} {when.year}'


def page_text(data: dict, counts: dict) -> dict:
    label = data.get('agent_label') or 'Your agent'
    if not isinstance(label, str):
        label = str(label)
    host = data.get('endpoint_host') or ''
    if not isinstance(host, str):
        host = str(host)
    try:
        when = datetime.fromisoformat(str(data.get('created_at')).replace('Z', '+00:00'))
    except ValueError as exc:
        raise PrerenderError('date') from exc
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc)
    scanned = _long_date(when)
    combined = data.get('report_kind') == 'starter_extensions'
    voice = data.get('pack_version') == 'starter-voice-50@1'
    if combined:
        heading = f'{label} - Starter scan + extensions'
        title = f'{heading} | BackBond'
        meta = _viewer_combined_meta()
        report_date = f'{meta.rsplit(" / ", 1)[-1]} (original Starter scan {scanned})'
        volume = f"{counts['scenarios']} scenarios / {counts['executions']} recorded executions"
    else:
        heading = label
        title = f'{label} - Agent assessment | BackBond'
        report_date = scanned
        meta = f"Agent assessment / {scanned} / {'Voice-related text and browser checks' if voice else 'Starter text checks'}"
        volume = f"{counts['executions']} tests"
    gaps = counts['gaps']
    breakdown = ', '.join(f'{n} {k}' for k, n in counts['gap_breakdown'].items())
    gap_text = f"{gaps} could not run" + (f' ({breakdown})' if gaps and breakdown != f'{gaps} could not run' else '')
    headline = (f"{volume}: {counts['passed']} met criteria, {counts['failed']} flagged for review, "
                f"{gap_text}; {counts['adjudicable']} adjudicable. Not-run results are never counted as passes.")
    target = f'Target: {host} / Report date: {report_date}' if host else f'Report date: {report_date}'
    description = f'{heading}. {target}. {headline}'
    return {'title': title, 'heading': heading, 'meta': meta, 'target': target, 'headline': headline,
            'description': description}


def render(data: dict) -> tuple[str, dict]:
    counts = compute_counts(data)
    text = page_text(data, counts)
    page = TEMPLATE.read_text(encoding='utf-8')

    def swap(old: str, new: str) -> None:
        nonlocal page
        if page.count(old) != 1:
            raise PrerenderError(f'template anchor not unique: {old[:60]}')
        page = page.replace(old, new)

    swap('<title>Agent assessment | BackBond</title>',
         f'<title>{esc(text["title"])}</title>\n'
         f'  <meta name="description" content="{esc(text["description"])}">\n'
         f'  <meta property="og:type" content="website"><meta property="og:site_name" content="BackBond">\n'
         f'  <meta property="og:title" content="{esc(text["title"])}">\n'
         f'  <meta property="og:description" content="{esc(text["description"])}">')
    swap('<h1 id="agent-name">Your agent</h1><p id="business-meta"></p>',
         f'<h1 id="agent-name">{esc(text["heading"])}</h1><p id="business-meta">{esc(text["meta"])}</p>'
         f'<div id="prerender-summary"><p class="caption">{esc(text["target"])}</p>'
         f'<p class="lead">{esc(text["headline"])}</p></div>')
    return page, counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('report')
    parser.add_argument('token')
    parser.add_argument('--out', action='append', default=[])
    parser.add_argument('--expect', default='', help='comma-separated key=value count assertions')
    args = parser.parse_args(argv)
    if not TOKEN_RE.match(args.token):
        parser.error('token must be 32 lowercase hex characters')
    data = json.loads(Path(args.report).read_text(encoding='utf-8'))
    page, counts = render(data)
    for pair in filter(None, args.expect.split(',')):
        key, _, value = pair.partition('=')
        actual = counts['gap_breakdown'].get(key.replace('_', ' ')) if key in GAP_LABELS else counts.get(key)
        if str(actual) != value:
            print(f'count mismatch: {key} expected {value}, computed {actual}', file=sys.stderr)
            return 2
    outs = [Path(p) for p in args.out] or [ROOT / 'scan' / 'r' / f'{args.token}.html',
                                          ROOT / 'scan' / 'r' / args.token / 'index.html']
    for out in outs:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page, encoding='utf-8', newline='\n')
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
