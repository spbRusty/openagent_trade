"""Контрактные тесты Gates (ТЗ §12): детерминированность, PASS/FAIL, порядок.

Ключевой контракт: синтетический юниверс с сильным устойчивым сигналом
проходит ПОЛНЫЙ шлюз; шум/пустые данные — отклоняются на первом же гейте.
"""
from datetime import datetime, timedelta

import polars as pl

from research.gates import gate_all, GateResult


def _strong_universe(days=500, n_risers=10, n_fallers=10, n_flat=5):
    """10 символов растут на 2%/день, 10 падают на 2%/день, 5 плоские.
    Momentum (long risers / short fallers) даёт +2% на сделку при издержках 0.15%.
    """
    start = datetime(2020, 1, 1)
    dates = [start + timedelta(days=i) for i in range(days)]
    frames = []
    for i in range(n_risers + n_fallers + n_flat):
        sym = f"SYM{i:02d}"
        trend = 1.02 if i < n_risers else (0.98 if i < n_risers + n_fallers else 1.0)
        base = 100.0 + i
        rows = [{"symbol": sym, "open_time": d,
                 "open": base * trend ** j, "close": base * trend ** (j + 1)}
                for j, d in enumerate(dates)]
        frames.append(pl.DataFrame(rows))
    return pl.concat(frames)


def test_gate_result_mechanics():
    g = GateResult()
    g.add("a", True, "ok")
    assert g.passed
    g.add("b", False, "bad")
    assert not g.passed
    assert g.fail_reason == "b"
    g.add("c", False, "worse")
    assert g.fail_reason == "b"  # фиксируется первая причина


def test_weak_data_fails_first_gate():
    """Мало/шум данных → отказ на market_neutral (первый гейт)."""
    times = [datetime(2020, 1, 1) + timedelta(minutes=1440 * i) for i in range(10)]
    df = pl.DataFrame({
        "symbol": ["A"] * 10,
        "open_time": times,
        "open": [100.0] * 10,
        "close": [100.0 + i for i in range(10)],
    })
    g = gate_all(df, 1, 1, "momentum", bar_minutes=1440)
    assert not g.passed
    assert g.fail_reason == "market_neutral"
    # первый результат в списке — market_neutral, статусы корректны
    assert g.results[0][0] == "market_neutral"
    assert g.results[0][1] is False


def test_strong_universe_passes_full_gate():
    """Синтетический устойчивый edge проходит весь шлюз (детерминированно)."""
    df = _strong_universe()
    g = gate_all(df, ret_window=1, hold=1, strategy="momentum", bar_minutes=1440)
    for name, ok, detail in g.results:
        assert ok, f"gate {name} не прошёл: {detail}"
    assert g.passed
    assert g.fail_reason is None