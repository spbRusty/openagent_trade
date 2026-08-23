"""Тесты CorrelationTracker: синтетика +1/-1, warmup, якорь, контекст советника."""
import asyncio
import json
from collections import deque

import pytest

from infrastructure.realtime import monitor
from infrastructure.realtime.corr import CorrelationTracker


def _feed(tr: CorrelationTracker, series: dict) -> None:
    syms = list(series)
    for i in range(len(series[syms[0]])):
        tr.sample({s: v[i] for s, v in series.items()})


def test_opencode_context_includes_correlation(tmp_path, monkeypatch):
    """Регрессия: cfg в _opencode_loop — секция opencode; корреляции берутся
    из корня конфига. KeyError здесь валил весь gather (инцидент 23.08)."""
    import copy

    cfg = copy.deepcopy(monitor.get())
    cfg["opencode"]["interval_min"] = 0          # первый тик сразу
    monkeypatch.setattr(monitor, "get", lambda: cfg)
    monkeypatch.setattr(monitor, "ROOT", tmp_path)

    class FakeEx:
        persist_path = tmp_path / "nope" / "account.json"
        last_price = {}
        trades = []

        def get_balance(self):
            return type("B", (), {"equity": 1000.0, "cash": 1000.0,
                                  "positions": {}, "realized_pnl": 0.0})()

    class FakePaper:
        ex = FakeEx()
        day_start_equity = 1000.0
        risk = type("R", (), {"killed": False})()

        def summary(self):
            return {"balance": 1000.0}

    state = {"detector": type("D", (), {"books": {}})(),
             "corr": CorrelationTracker(), "paper": FakePaper()}
    recent = deque([{"ts": "2099-01-01T00:00:00+00:00", "symbol": "ALTUSDT",
                     "type": "wall", "severity": "critical"}])

    async def boom(*a, **k):   # рвём бесконечный цикл после первой итерации
        raise KeyboardInterrupt

    monkeypatch.setattr(monitor, "ask_opencode", boom)
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(monitor._opencode_loop(
            type("J", (), {"write": lambda self, *a: None})(), recent, state))
    ctx = json.loads((tmp_path / "logs" / "realtime" / "context.json").read_text())
    assert ctx["btc_correlation"]["anchor"] == cfg["correlation"]["anchor"]
    assert set(ctx["health"]) == {"uptime_sec", "last_beacon_age_sec"}
    assert isinstance(ctx["health"]["uptime_sec"], int)
    assert ctx["health"]["last_beacon_age_sec"] is not None


def test_perfect_inverse_and_anchor() -> None:
    tr = CorrelationTracker(max_samples=200)
    btc = [100 * (1 + 0.001 * i + (0.01 if i % 3 == 0 else -0.005)) for i in range(80)]
    _feed(tr, {"BTCUSDT": btc,
               "ALTUSDT": [p * 1.5 for p in btc],     # то же движение
               "INVUSDT": [300 - p for p in btc]})    # зеркальное
    assert tr.corr("ALTUSDT") > 0.99
    assert tr.corr("INVUSDT") < -0.99
    assert tr.corr("BTCUSDT") is None      # сам якорь не коррелирует с собой
    assert tr.corr("UNKNOWN") is None      # нет данных
    snap = tr.snapshot({"BTCUSDT", "ALTUSDT", "INVUSDT"})
    assert "BTCUSDT" not in snap and set(snap) == {"ALTUSDT", "INVUSDT"}


def test_warmup_and_missing_prices() -> None:
    tr = CorrelationTracker(max_samples=200)
    _feed(tr, {"BTCUSDT": [100 + i for i in range(10)],
               "ALTUSDT": [50 + i * 0.5 for i in range(10)]})
    assert tr.corr("ALTUSDT") is None      # меньше _MIN_PAIRS пар — корреляции нет

    tr2 = CorrelationTracker(max_samples=200)
    btc = [100 + i for i in range(40)]
    alt = [i % 5 for i in range(40)]       # в части тиков цены нет (px<=0 пропускается)
    for b, a in zip(btc, alt):
        prices = {"BTCUSDT": float(b)}
        if a > 0:
            prices["ALTUSDT"] = float(a)
        tr2.sample(prices)
    assert tr2._returns["BTCUSDT"].__len__() == len(btc)
    assert len(tr2._returns["ALTUSDT"]) < len(btc)   # короткий дек выравнивается хвостами
