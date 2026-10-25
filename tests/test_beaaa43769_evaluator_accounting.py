import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from scanners.evaluator_budget import EvaluatorBudget, EvaluatorBudgetError


def test_unlimited_concurrent_reservations_survive_unknown_and_restart(tmp_path):
    path = tmp_path / 'budget.sqlite'
    EvaluatorBudget(path, 'run')

    def reserve(_):
        EvaluatorBudget(path, 'run').reserve(12_000_000)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(reserve, range(8)))
    # A provider failure/unknown response cannot refund a committed reservation.
    with pytest.raises(TimeoutError):
        EvaluatorBudget(path, 'run').reserve(13_000_000)
        raise TimeoutError('unknown provider outcome')
    EvaluatorBudget(path, 'run').reserve(1)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT reserved_micro FROM evaluator_budget').fetchone() == (109_000_001,)
    with pytest.raises(EvaluatorBudgetError, match='identity changed'):
        EvaluatorBudget(path, 'run', limit_micro=10_000_000)


def test_finite_and_zero_remain_explicit(tmp_path):
    path = tmp_path / 'budget.sqlite'
    budget = EvaluatorBudget(path, 'finite', limit_micro=20_000_000)
    budget.reserve(20_000_000)
    with pytest.raises(EvaluatorBudgetError, match='exhausted'):
        EvaluatorBudget(path, 'finite', limit_micro=20_000_000).reserve(1)
    with pytest.raises(EvaluatorBudgetError, match='exhausted'):
        EvaluatorBudget(path, 'zero', limit_micro=0).reserve(1)


@pytest.mark.parametrize('value', [-1, True, 1.5, float('inf'), 2**63])
def test_invalid_limits(tmp_path, value):
    with pytest.raises(EvaluatorBudgetError):
        EvaluatorBudget(tmp_path / 'budget.sqlite', 'run', limit_micro=value)


@pytest.mark.parametrize('value', [None, 0, -1, True, 1.5, 2**63])
def test_unknown_or_invalid_cost_bounds(tmp_path, value):
    with pytest.raises(EvaluatorBudgetError):
        EvaluatorBudget(tmp_path / 'budget.sqlite', 'run').reserve(value)
