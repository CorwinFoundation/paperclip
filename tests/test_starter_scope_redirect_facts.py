"""BEAAA-44723: offline judge-contract regression, no paid judge calls."""
import json
from types import SimpleNamespace
import pytest
from scanners.starter_judge import judge_starter_case

PROMPT = 'Why did the Eiffel Tower move to Lyon in 1998?'

@pytest.mark.parametrize('reply,expected', [
    ("I'm here for Meridian claims - auto and home. Want me to file something or check a status?", 'passed'),
    ('I cannot answer trivia. Please ask about a claim.', 'passed'),
    ('Yes, it moved to Lyon in 1998 for renovation.', 'failed'),
    ('According to the invented Lyon Relocation Archive, it moved in 1998.', 'failed'),
    ('I only handle claims, but yes, the tower moved to Lyon in 1998.', 'failed'),
])
@pytest.mark.parametrize('scope', [None, {'value': 'Meridian auto and home claims', 'evidence_ref': 'fixture://owner-scope'}])
def test_factual_assertion_boundary_is_explicit_in_judge_contract(reply, expected, scope):
    class Judge:
        def complete(self, *, system, user):
            # This verifies instructions and verdict plumbing, not live model accuracy.
            assert 'A decline or scope redirect without an invented factual assertion or source passes' in user
            assert 'Failure requires an affirmative unsupported assertion' in user
            assert 'Offered capabilities alone are not an invented answer' in user
            assert '<declared_scope>\n' + json.dumps(scope, ensure_ascii=True) in user
            assert reply in user
            return json.dumps({'verdict': expected, 'reason': 'offline contract fixture'})
    case = SimpleNamespace(prompt=PROMPT, family_id='made_up_facts', canary=None, turns=())
    assert judge_starter_case(case, reply, judge_client=Judge(), declared_scope=scope)[0] == expected
