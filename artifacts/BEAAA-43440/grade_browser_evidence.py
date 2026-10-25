"""Run inside the existing Scanner container; stdin is captured public evidence.

Uses the installed registered grader. Does not dispatch targets or render reports.
All provider credentials stay in the existing container. No retries.
"""
import datetime
import hashlib
import json
import os
import pathlib
import sys
from decimal import Decimal

import httpx
from scanners import starter_judge as grader
from scanners.starter_pack_v4 import case_from_payload


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


job = json.load(sys.stdin)
assert len(job['rows']) <= 400
assert len({r['source_sha256'] for r in job['rows']}) == len(job['rows'])
key = os.environ.get('OPENROUTER_API_KEY', '').strip()
assert key, 'Existing judge credential unavailable; no grading performed'
source_hash = hashlib.sha256(pathlib.Path(grader.__file__).read_bytes()).hexdigest()
with httpx.Client(timeout=20) as public_client:
    models_response = public_client.get('https://openrouter.ai/api/v1/models')
    models_response.raise_for_status()
    model = next(m for m in models_response.json()['data'] if m['id'] == grader.JUDGE_MODEL)
pricing = model['pricing']
prompt_rate = Decimal(pricing['prompt'])
completion_rate = Decimal(pricing['completion'])
assert prompt_rate >= 0 and completion_rate >= 0
reserved = Decimal('0')
known_cost = Decimal('0')
calls = 0
unknown_cost_calls = 0
current = None
emit({'event': 'grading_start', 'at': now(), 'rows': len(job['rows']),
      'target_calls': 0, 'reports_generated': 0, 'source_sha256': source_hash,
      'model_requested': grader.JUDGE_MODEL, 'pricing_observed': pricing,
      'remaining_budget_usd': None, 'max_output_tokens': 512})


class MeteredJudge:
    def __init__(self):
        self.metadata = None

    def complete(self, *, system, user):
        global reserved, known_cost, calls, unknown_cost_calls
        # UTF-8 byte count deliberately overestimates token count. A fixed
        # allowance covers message wrappers; output is bounded separately.
        upper = ((len(system.encode()) + len(user.encode()) + 1024) * prompt_rate
                 + 512 * completion_rate)
        if calls >= job.get('remaining_call_allowance', 400):
            raise RuntimeError('Local evidence-grading call allowance exhausted')
        reserved += upper
        calls += 1
        self.metadata = {'call_reserved_usd': str(upper), 'cost_usd': None,
                         'model_requested': grader.JUDGE_MODEL}
        emit({'event': 'judge_call_started', 'at': now(), 'target': current['target'],
              'case_id': current['case_id'], 'source_sha256': current['source_sha256'],
              'call_reserved_usd': str(upper)})
        try:
            response = httpx.post(grader.JUDGE_URL,
                headers={'Authorization': 'Bearer ' + key},
                json={'model': grader.JUDGE_MODEL, 'temperature': grader.JUDGE_TEMPERATURE,
                      'seed': grader.JUDGE_SEED, 'max_tokens': 512,
                      'response_format': {'type': 'json_object'},
                      'messages': [{'role': 'system', 'content': system},
                                   {'role': 'user', 'content': user}]}, timeout=20)
            response.raise_for_status()
            data = response.json()
            usage = data.get('usage') or {}
            actual = usage.get('cost')
            if actual is not None:
                actual = Decimal(str(actual))
                known_cost += actual
                reserved += actual - upper
            else:
                unknown_cost_calls += 1
            answer = data['choices'][0]['message']['content']
            assert key not in answer, 'Credential found in output: withheld'
            self.metadata.update(generation_id=data.get('id'), model_returned=data.get('model'),
                                 usage=usage, cost_usd=str(actual) if actual is not None else None,
                                 raw_judge_reply=answer)
            return answer
        except Exception:
            # Keep the full reservation for an unknown provider result. The
            # registered grader records could_not_run; never retry this row.
            unknown_cost_calls += 1
            raise


for current in job['rows']:
    client = MeteredJudge()
    verdict, reason = grader.judge_starter_case(
        case_from_payload(current['case_payload']), current['reply'], judge_client=client)
    emit({'event': 'grade', 'at': now(), 'target': current['target'],
          'case_id': current['case_id'], 'source_sha256': current['source_sha256'],
          'recorded_verdict': verdict, 'reason': reason,
          'grading_mode': 'post_capture_registered_starter_grader',
          'grader_source_sha256': source_hash, 'provider_metadata': client.metadata,
          'deterministic_only': client.metadata is None})

emit({'event': 'grading_complete', 'at': now(), 'rows': len(job['rows']),
      'judge_calls': calls, 'target_calls': 0, 'reports_generated': 0,
      'known_provider_cost_usd': str(known_cost),
      'unknown_cost_calls': unknown_cost_calls, 'budget_consumed_or_reserved_usd': str(reserved)})
