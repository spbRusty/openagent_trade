"""Контракт пайплайна (ТЗ §11): параметры фиксируются на discovery ДО
любого обращения к validation/OOS. Главный инвариант проекта.

Spy на prepare_trades записывает порядок вызовов; утверждения:
1) все discovery-вызовы раньше первого val/oos-вызова;
2) val и oos считаются ровно один раз и с ОДНОЙ И ТОЙ ЖЕ (W, H, strategy) —
   freeze не подменяется после просмотра OOS.
"""
import asyncio
import sys
import types
from datetime import datetime, timedelta

import polars as pl
import pytest

from research.hypotheses import run as run_mod


def _synth_bars(days: int = 2750, symbols=("AAA", "BBB")) -> pl.DataFrame:
    """Дневные бары без пропусков 2019-07..2026-12 с трендом и шумом."""
    base = datetime(2019, 7, 1)
    frames = []
    for k, sym in enumerate(symbols):
        for i in range(days):
            t = base + timedelta(days=i)
            px = 100 * (1 + 0.002 * i + 0.01 * ((i * 7919 + k * 104729) % 100 - 50) / 50)
            frames.append({"symbol": sym, "open_time": t,
                           "open": px * 0.999, "close": px * 1.001})
    return pl.DataFrame(frames).with_columns(pl.col("open_time").dt.replace_time_zone(None))


@pytest.fixture
def pipeline_env(monkeypatch):
    calls = []

    real_prepare = run_mod.prepare_trades
    synth = _synth_bars()
    D = lambda s: datetime.fromisoformat(s)

    def kind_of(df):
        lo, hi = df["open_time"].min(), df["open_time"].max()
        if lo >= D(run_mod.PERIOD_OOS[0]):
            return "oos"
        if lo >= D(run_mod.PERIOD_VALIDATION[0]):
            return "validation"
        if hi < D(run_mod.PERIOD_VALIDATION[0]):
            return "discovery"
        return "full"

    def spy(df, W, H, strategy, bar_minutes=1440, cond=None):
        calls.append({"kind": kind_of(df), "W": W, "H": H, "strategy": strategy})
        return real_prepare(df, W, H, strategy, bar_minutes=bar_minutes, cond=cond)

    monkeypatch.setattr(run_mod, "load_daily_full", lambda: synth)
    monkeypatch.setattr(run_mod, "add_features", lambda d: d)
    monkeypatch.setattr(run_mod, "MIN_TRADES_DISCOVERY", 10)
    fake_h = types.SimpleNamespace(
        id="T_ORDER", name="order-check", strategy="momentum", cond=None,
        to_record=lambda: {"id": "T_ORDER", "name": "order-check",
                           "strategy": "momentum", "status": "IDEA"})
    monkeypatch.setattr(run_mod, "HYPOTHESES", [fake_h])
    monkeypatch.setattr(run_mod, "prepare_trades", spy)
    import research.gates as gates_mod
    monkeypatch.setattr(gates_mod, "prepare_trades", spy)
    monkeypatch.setattr(gates_mod, "prepare_trades_xs",
                        lambda *a, **k: real_prepare(*a, **k))
    monkeypatch.setattr(run_mod, "_sync_hypotheses_memory", lambda rows, eid: None)
    monkeypatch.setattr(run_mod, "record_experiment",
                        lambda eid, payload: types.SimpleNamespace(eid=eid))
    monkeypatch.setattr(run_mod, "_write_report", lambda *a, **k: None)
    yield calls


def test_freeze_precedes_validation_and_oos(pipeline_env):
    calls = pipeline_env
    run_mod.main()

    scoped = [(i, c["kind"]) for i, c in enumerate(calls) if c["kind"] != "full"]
    kinds = [k for _, k in scoped]
    assert "discovery" in kinds and "validation" in kinds and "oos" in kinds

    idx = {k: [i for i, kk in enumerate(kinds) if kk == k] for k in ("discovery", "validation", "oos")}
    assert max(idx["discovery"]) < min(idx["validation"]), \
        "обращение к validation до завершения discovery-свипа"
    assert max(idx["discovery"]) < min(idx["oos"]), \
        "обращение к OOS до завершения discovery-свипа"

    val = [c for c in calls if c["kind"] == "validation"]
    oos = [c for c in calls if c["kind"] == "oos"]
    assert len(val) == 1 and len(oos) == 1
    frozen_val = {k: val[0][k] for k in ("W", "H", "strategy")}
    frozen_oos = {k: oos[0][k] for k in ("W", "H", "strategy")}
    assert frozen_val == frozen_oos, "параметры изменились между validation и OOS"
