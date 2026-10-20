"""BAC Analyst report tool: the only path from a BAC case to Starter scan evidence.

The tool never reads a scan by bare scan_id. It goes through the existing private
BAC case resolver (``BACIntake.resolve_evidence``), which requires an acknowledged
intake bound to the scan, a finished scan and the exact evidence digest, and which
returns ordinal verdicts only (no prompts, replies or target inputs).

``freeze_starter_input`` mirrors the Operations core ``freeze_report_input``
contract (immutable, versioned snapshot with its own sha256). Operations also
requires ``result_id``, ``comparison_id`` and ``retest_completed``; a Starter scan
is a single run with no comparison and no retest, so those are recorded as
explicitly not applicable instead of being invented.
"""
from __future__ import annotations

import hashlib
import json

INPUT_SCHEMA = 'starter_report_input@1'
VERDICTS = ('pass', 'fail', 'could_not_run')
NOT_APPLICABLE = {'status': 'not_applicable', 'reason': 'starter_single_scan'}


def canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def coverage(outcome, accepted_total, results) -> dict:
    """Confirmed coverage of the accepted Starter scope. ``complete`` is true only when the
    outcome is explicitly ``complete``, the accepted denominator was recorded by the Scanner
    (never inferred from the pack name), every accepted test is reported exactly once, and no
    test is could_not_run/unknown. Anything else stays unconfirmed (score/band N/A)."""
    accepted = accepted_total if (isinstance(accepted_total, int) and not isinstance(accepted_total, bool)
                                  and accepted_total > 0) else None
    usable = sum(1 for r in results if r['verdict'] in ('pass', 'fail'))
    unavailable = len(results) - usable
    ordinals = [r['ordinal'] for r in results]
    complete = (outcome == 'complete' and accepted is not None and len(results) == accepted
                and len(set(ordinals)) == accepted and unavailable == 0)
    return {'accepted_total': accepted, 'accepted_scope_confirmed': accepted is not None,
            'reported': len(results), 'usable': usable, 'unavailable': unavailable, 'complete': complete}


def freeze_starter_input(bac, store, *, request_id, case_id, scan_id, evidence_sha256) -> dict:
    """Return {'input': <frozen Starter report input>, 'input_sha256': ...}.

    Raises BACUnconfirmed (from the resolver) when the case binding or evidence digest
    does not hold; nothing is read from the scan before the resolver has confirmed it."""
    evidence = bac.resolve_evidence(store, request_id=request_id, case_id=case_id, scan_id=scan_id,
                                    revision_sha256=evidence_sha256)
    row = store.scan(scan_id)
    results = [{'ordinal': int(r['ordinal']), 'verdict': r['verdict'] if r['verdict'] in VERDICTS
                else 'could_not_run'} for r in evidence['results']]
    counts = {v: sum(1 for r in results if r['verdict'] == v) for v in VERDICTS}
    frozen_coverage = coverage(evidence['outcome'], bac.latest_progress(scan_id).get('accepted_pack_total'),
                               results)
    frozen = {
        'schema': INPUT_SCHEMA,
        'case_id': evidence['case_id'],
        'request_id': evidence['request_id'],
        'scan_id': evidence['scan_id'],
        'evidence_sha256': evidence['revision_sha256'],
        'outcome': evidence['outcome'],
        'pack_version': row['pack_version'],
        'results': results,
        'verdict_counts': counts,
        'coverage': frozen_coverage,
        'comparison': dict(NOT_APPLICABLE),
        'retest': dict(NOT_APPLICABLE),
        'operations_freeze': {'supported': False, 'missing': ['comparison_id', 'retest_completed']},
    }
    return {'input': frozen, 'input_sha256': hashlib.sha256(canonical(frozen).encode()).hexdigest()}
