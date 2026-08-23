"""Контрактные тесты метрик (ТЗ §9) и block bootstrap (ТЗ §11)."""
import numpy as np
import pytest

from research.statistics import full_metrics, block_bootstrap_pvalue


def test_full_metrics_known_values():
    m = full_metrics(np.array([1.0, -1.0, 1.0, -1.0]))
    assert m["n"] == 4
    assert m["mean"] == 0.0
    assert m["winrate"] == 0.5
    assert m["total"] == 0.0
    assert m["profit_factor"] == 1.0  # выигрыш 2 / проигрыш 2


def test_full_metrics_constant_positive():
    m = full_metrics(np.full(10, 0.01))
    assert m["winrate"] == 1.0
    assert m["total"] == pytest.approx(0.1)
    assert m["profit_factor"] == float("inf")  # нет убытков
    assert m["max_dd"] == 0.0


def test_full_metrics_empty():
    m = full_metrics(np.array([]))
    assert m["n"] == 0


def test_bootstrap_positive_returns_reject_h0():
    """Все месяцы в плюсе → H0 (mean<=0) отвергается: p мал."""
    p, obs, _ = block_bootstrap_pvalue(np.full(60, 0.01), block=3, n_iter=2000)
    assert obs > 0
    assert p < 0.05


def test_bootstrap_zero_returns_accept_h0():
    """Нулевые доходности → H0 не отвергается: p = 1.0."""
    p, obs, _ = block_bootstrap_pvalue(np.zeros(60), block=3, n_iter=2000)
    assert obs == 0.0
    assert p == 1.0


def test_bootstrap_deterministic():
    """Фиксированный seed → воспроизводимый результат (ТЗ §24)."""
    r = np.random.default_rng(7).normal(0.001, 0.01, 100)
    p1 = block_bootstrap_pvalue(r, block=5, n_iter=500)[0]
    p2 = block_bootstrap_pvalue(r, block=5, n_iter=500)[0]
    assert p1 == p2