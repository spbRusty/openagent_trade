"""Контрактные тесты BeaconTrader: маячок → RiskManager → PaperExecutor."""
from datetime import datetime, timedelta, timezone

import pytest

from infrastructure.realtime.trader import BeaconTrader
from trading.execution.paper import PaperExecutor
from trading.risk.manager import RiskManager


def mk(tmp_path, **risk):
    ex = PaperExecutor(1000.0, 0.00075, 0.0005, 50, tmp_path / "acc.json")
    rm = RiskManager(risk.get("max_position_usd", 5.0),
                     risk.get("max_exposure_usd", 20.0),
                     risk.get("max_open_positions", 3),
                     risk.get("max_daily_loss", 5.0),
                     risk.get("max_drawdown", 0.20))
    return BeaconTrader(ex, rm)


def beacon(sym="X", side="buy", price=100.0, type_="wall", strength=9.0):
    return {"symbol": sym, "side": side, "price": price, "type": type_,
            "strength": strength}


def ffwd(t):
    """Имитация паузы между входами (глобальный гейт пропускает)."""
    t.last_entry = 0.0


def test_entry_long_with_slippage_and_fee(tmp_path):
    t = mk(tmp_path)
    t.on_beacon(beacon())
    p = t.ex.positions["X"]
    assert p.qty > 0 and p.entry_price == pytest.approx(100 * 1.0005)
    assert t.ex.cash == pytest.approx(1000 - 5 * 0.00075)


def test_short_fills_lower(tmp_path):
    t = mk(tmp_path)
    t.on_beacon(beacon(side="sell"))
    p = t.ex.positions["X"]
    assert p.qty < 0 and p.entry_price == pytest.approx(100 * 0.9995)


def test_opposite_beacon_closes_in_profit(tmp_path):
    t = mk(tmp_path)
    t.on_beacon(beacon(price=100))
    t.on_beacon(beacon(side="sell", price=110))
    closed = [tr for tr in t.ex.trades if tr["symbol"] == "X"]
    assert closed and closed[0]["pnl"] > 0       # лонг закрыт в плюс
    assert t.ex.get_position("X") is None        # реверс подавлен глобальной паузой входов
    ffwd(t)
    t.reentry["X"] = 0                           # истёк и посимвольный кулдаун
    t.on_beacon(beacon(sym="X", side="sell", price=110))
    assert t.ex.positions["X"].qty < 0                     # зеркальность работает после пауз


def test_spread_expansion_not_traded(tmp_path):
    t = mk(tmp_path)
    t.on_beacon({"symbol": "Z", "type": "spread_expansion",
                 "side": "both", "strength": 9,
                 "spread_pct": 0.5, "median_pct": 0.1})   # реальная форма: без price
    assert not t.ex.positions


def test_max_open_positions_rejects_extra(tmp_path):
    t = mk(tmp_path)
    for i in range(4):
        ffwd(t)
        t.on_beacon(beacon(sym=f"S{i}"))
    assert len(t.ex.positions) == 3


def test_daily_loss_halts_and_blocks_entries(tmp_path):
    t = mk(tmp_path, max_daily_loss=1.0)
    t.on_beacon(beacon())                        # long X @ ~100.05
    t.on_beacon(beacon(side="sell", price=80))   # фиксация убытка ≈ −1 USD
    t.on_beacon(beacon(sym="Y"))                 # тик: kill switch + попытка входа
    assert t.risk.killed
    assert t.ex.get_position("Y") is None        # вход после хальта отклонён


def test_resume_next_day(tmp_path, monkeypatch):
    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc) + timedelta(days=1)

    t = mk(tmp_path, max_daily_loss=1.0)         # день = сегодня (до патча)
    monkeypatch.setattr("infrastructure.realtime.trader.datetime", FakeDT)
    t.risk.halt()
    t.on_beacon(beacon(sym="Y"))                 # наступил новый день
    assert not t.risk.killed and t.ex.get_position("Y") is not None


def test_timeout_closes_stale_position(tmp_path):
    t = mk(tmp_path)
    t.on_beacon(beacon())
    pos = t.ex.positions["X"]
    pos.opened_at = (datetime.now(timezone.utc)
                     - timedelta(minutes=11)).isoformat(timespec="seconds")
    t.on_beacon(beacon(sym="Y"))
    assert t.ex.get_position("X") is None


def test_state_survives_restart(tmp_path):
    t = mk(tmp_path)
    t.on_beacon(beacon())
    ex2 = PaperExecutor(1000.0, 0.00075, 0.0005, 50, tmp_path / "acc.json")
    assert "X" in ex2.positions
    assert ex2.cash == pytest.approx(t.ex.cash)


def test_imbalance_without_price_never_marks_or_crashes(tmp_path):
    """Реальная форма imbalance (ТЗ-контракт): нет price. Первым по символу —
    не должен ни упасть, ни открыть сделку без рыночной цены."""
    t = mk(tmp_path)
    t.on_beacon({"symbol": "I", "type": "imbalance", "side": "buy",
                 "strength": 0.9, "bids": 500, "asks": 100, "detail": ""})
    assert not t.ex.positions and "I" not in t.ex.last_price
    t.on_beacon({"symbol": "I", "type": "wall", "side": "buy",
                 "price": 50.0, "strength": 9})          # wall дал цену — вход открылся
    assert t.ex.get_position("I") is not None


def test_loss_streak_no_halt(tmp_path):
    """Тилт отключён (нужна непрерывная статистика): серия убытков НЕ хальтует,
    торговля продолжается, дневной стоп ($5) — единственный kill switch."""
    t = mk(tmp_path)
    for i in range(3):                        # три убыточных круга на разных символах
        ffwd(t)
        t.on_beacon(beacon(sym=f"T{i}", price=100))
        t.on_beacon(beacon(sym=f"T{i}", side="sell", price=95))
    assert not t.risk.killed                  # тильта больше нет
    ffwd(t)
    t.on_beacon(beacon(sym="W"))              # торговля продолжается
    assert "W" in t.ex.positions


def test_scanner_survives_paper_error(tmp_path):
    """Архитектурная изоляция: исключение в execution layer не убивает _handle,
    сканер продолжает обрабатывать следующие маячки."""
    import asyncio
    from collections import deque

    from infrastructure.realtime.monitor import _handle

    class FakeDet:
        books = {}

        def update(self, u):
            return [{"symbol": u["symbol"], "type": "wall", "side": "buy",
                     "price": 1.0, "strength": 9.0}]

    class FakeDash:
        def publish(self, b): pass

    class FakeJournal:
        def write(self, name, record): pass

    class FlakyPaper:
        def __init__(self):
            self.calls = 0
            self.marks = {}

        def mark(self, sym, px):
            self.marks[sym] = px

        def on_beacon(self, b):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")

    class NoCorr:
        def corr(self, sym):
            return None

    async def run():
        q = asyncio.Queue()
        state = {"detector": FakeDet(), "dash": FakeDash(), "paper": FlakyPaper(),
                 "corr": NoCorr()}
        task = asyncio.create_task(
            _handle(q, state, FakeJournal(), deque(maxlen=10), []))
        await q.put({"symbol": "A"})   # первый маячок: paper падает
        await q.put({"symbol": "B"})   # второй обязан обработаться
        await asyncio.sleep(0.05)
        task.cancel()
        return state["paper"].calls

    assert asyncio.run(run()) == 2


def test_trader_mark_updates_price(tmp_path):
    t = mk(tmp_path)
    t.mark("M", 55.5)
    assert t.ex.last_price["M"] == 55.5
    t.mark("M", None)                        # пустая цена не затирает актуальную
    assert t.ex.last_price["M"] == 55.5


def test_handle_marks_price_from_book_stream(tmp_path):
    """Цена приходит напрямую из потока стакана: апдейт без единого маячка
    всё равно обновляет last_price у исполнителя."""
    import asyncio
    from collections import deque

    from infrastructure.realtime.monitor import _handle

    class FakeBook:
        bids = {100.1: 5.0}
        asks = {100.2: 5.0}

    class FakeDet:
        books = {"X": FakeBook()}

        def update(self, u):
            return []                        # апдейт без маячков

    class FakeDash:
        def publish(self, b): pass

    class FakeJournal:
        def write(self, name, record): pass

    class RecPaper:
        def __init__(self):
            self.marks = {}

        def mark(self, sym, px):
            self.marks[sym] = px

        def on_beacon(self, b): pass

    class NoCorr:
        def corr(self, sym):
            return None

    async def run():
        q = asyncio.Queue()
        paper = RecPaper()
        state = {"detector": FakeDet(), "dash": FakeDash(), "paper": paper,
                 "corr": NoCorr()}
        task = asyncio.create_task(
            _handle(q, state, FakeJournal(), deque(maxlen=10), []))
        await q.put({"symbol": "X"})
        await asyncio.sleep(0.05)
        task.cancel()
        return paper.marks

    assert asyncio.run(run())["X"] == pytest.approx((100.1 + 100.2) / 2)


def test_halt_persists_across_restart(tmp_path):
    """Дневной стоп хальтует и переживает рестарт (тильт отключён — это теперь
    единственный источник хальта наряду с просадкой)."""
    t1 = mk(tmp_path, max_daily_loss=1.0)
    t1.on_beacon(beacon())                       # лонг X @ ~100
    t1.on_beacon(beacon(side="sell", price=80))  # убыток ≈ −1 USD
    t1.on_beacon(beacon(sym="Y"))                # тик: _sync_kill видит убыток дня
    assert t1.risk.killed
    t2 = mk(tmp_path)                         # «рестарт»: тот же persist-путь
    assert t2.risk.killed                     # хальт пережил рестарт
    assert (tmp_path / "halt.txt").read_text().strip()  # дата хальта записана


def test_weak_signal_not_traded(tmp_path):
    """Гейт убеждённости: стена ниже critical-порога (×8) — входа нет."""
    t = mk(tmp_path)
    t.on_beacon(beacon(sym="W1", strength=5.0))
    assert not t.ex.positions
    t.on_beacon(beacon(sym="W2", strength=8.0))
    assert "W2" in t.ex.positions


def test_global_entry_gap(tmp_path):
    t = mk(tmp_path)
    t.on_beacon(beacon(sym="A", price=100))
    t.on_beacon(beacon(sym="B", price=100))     # раньше глобальной паузы
    assert "A" in t.ex.positions and "B" not in t.ex.positions


def test_reentry_cooldown_per_symbol(tmp_path):
    t = mk(tmp_path)
    t.on_beacon(beacon(sym="C", price=100))
    t.on_beacon(beacon(sym="C", side="sell", price=101))  # закрытие+разворот?
    closed = [x for x in t.ex.trades if x["symbol"] == "C"]
    assert closed and closed[0]["pnl"] > 0


def test_early_fail_exits_loser_before_timeout(tmp_path):
    t = mk(tmp_path)
    t.on_beacon(beacon(sym="E", price=100))
    pos = t.ex.positions["E"]
    pos.opened_at = (datetime.now(timezone.utc)
                     - timedelta(seconds=130)).isoformat(timespec="seconds")
    t.ex.mark_price("E", 99.0)                  # в минусе — зомби
    t.on_beacon(beacon(sym="F", price=50))
    assert t.ex.get_position("E") is None       # ранний выход до таймаута


def test_early_fail_exits_flat_zombie(tmp_path):
    """Флэт тоже зомби: через 2 мин нет +5 б.п. в нашу сторону — выходим,
    даже если позиция формально не в минусе (комиссии всё равно съедят)."""
    t = mk(tmp_path)
    t.on_beacon(beacon(sym="Z", price=100))     # вход ~100.05 (слиппедж)
    t.ex.mark_price("Z", 100.06)                # +1 б.п. от входа — импульса нет
    pos = t.ex.positions["Z"]
    pos.opened_at = (datetime.now(timezone.utc)
                     - timedelta(seconds=130)).isoformat(timespec="seconds")
    t.on_beacon(beacon(sym="Y", price=50))
    assert t.ex.get_position("Z") is None


def test_summary_shape(tmp_path):
    s = mk(tmp_path).summary()
    assert s["balance"] == 1000.0 and s["equity"] == 1000.0
    assert s["trades"] == 0 and s["winrate"] is None
    assert s["open"] == 0 and s["day_pnl"] == 0.0 and not s["killed"]
