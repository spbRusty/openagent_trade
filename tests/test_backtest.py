"""Контрактные тесты бэктеста: no-lookahead, цены входа/выхода, пробелы.

Главный контракт (ТЗ §9): сигнал на close t, вход open t+1, выход open t+1+H.
Если этот контракт ломается — все исследования выше недействительны.
"""
from datetime import datetime, timedelta

import polars as pl

from research.backtest import prepare_trades
from config.research import COST_ROUND_TRIP


def _df(opens, times, sym="B"):
    return pl.DataFrame({
        "symbol": [sym] * len(times),
        "open_time": times,
        "open": [float(o) for o in opens],
        "close": [float(o) for o in opens],
    })


def test_no_lookahead_entry_exit_prices():
    """Вход ровно open t+1, выход ровно open t+1+H, время непрерывно."""
    times = [datetime(2020, 1, 1) + timedelta(minutes=i) for i in range(6)]
    df = _df([100, 101, 102, 103, 104, 105], times)

    tr = prepare_trades(df, ret_window=1, hold=1, strategy="momentum", bar_minutes=1)

    assert tr.height == 3  # t=1..3 (t=0: null ret_w; t=4: нет выхода)
    # Для каждой сделки: entry = open t+1, exit = open t+2, время без пробелов
    for row in tr.iter_rows(named=True):
        assert row["entry_time"] - row["open_time"] == timedelta(minutes=1)
        assert row["exit_time"] - row["open_time"] == timedelta(minutes=2)
        assert row["exit_price"] == row["entry_price"] + 1.0  # open растёт на 1

    # Конкретная сделка t=1: entry open[2]=102, exit open[3]=103
    r = tr.row(0, named=True)
    assert r["entry_time"] == times[2]
    assert r["exit_time"] == times[3]
    assert r["entry_price"] == 102.0
    assert r["exit_price"] == 103.0
    gross = 103.0 / 102.0 - 1.0
    assert abs(r["gross_ret"] - gross) < 1e-12
    assert abs(r["net_ret"] - (gross - COST_ROUND_TRIP)) < 1e-12


def test_momentum_vs_reversion_sign():
    times = [datetime(2020, 1, 1) + timedelta(minutes=i) for i in range(6)]
    df = _df([100, 101, 102, 103, 104, 105], times)

    mom = prepare_trades(df, 1, 1, "momentum", bar_minutes=1)
    rev = prepare_trades(df, 1, 1, "reversion", bar_minutes=1)
    assert mom.height == rev.height == 3
    # Противоположные знаки на тех же сделках
    assert (mom["gross_ret"].to_numpy() == -rev["gross_ret"].to_numpy()).all()


def test_gaps_are_excluded():
    """Пропуск бара ломает непрерывность — сделка с пробелом не допускается."""
    times = [datetime(2020, 1, 1) + timedelta(minutes=i) for i in [0, 1, 3, 4, 5]]
    df = _df([100, 101, 102, 103, 104], times)

    tr = prepare_trades(df, ret_window=1, hold=1, strategy="momentum", bar_minutes=1)

    # Выживает только t=2 (3 мин): entry 4 мин, exit 5 мин. t=1 имеет пробел 1->3.
    assert tr.height == 1
    r = tr.row(0, named=True)
    assert r["entry_time"] == times[3]
    assert r["exit_time"] == times[4]