"""Контрактные тесты PaperExecutor (ТЗ §15): fills, slippage, fees, позиции,
PnL, персистентность состояния."""
import pytest

from trading.execution.paper import PaperExecutor


def _ex(path, initial=1000.0):
    return PaperExecutor(initial, fee_rate=0.00075, slippage_pct=0.0005,
                         latency_ms=50, persist_path=path)


def test_notify_only_on_close(monkeypatch, tmp_path):
    """Открытие молчит; закрытие шлёт одно сообщение: pnl, число сделок, винрейт."""
    got = []
    monkeypatch.setattr("trading.execution.paper.notify",
                        lambda *a, **k: got.append((a, k)))
    ex = _ex(tmp_path / "n.json")
    ex.mark_price("BTCUSDT", 100.0)
    ex.place_order("BTCUSDT", "BUY", 1.0)
    assert got == []                       # открытие — без уведомления
    ex.mark_price("BTCUSDT", 101.0)
    ex.place_order("BTCUSDT", "SELL", 1.0)
    assert len(got) == 1
    args, kwargs = got[0]
    assert args[:2] == ("TRADE", "TRADE_CLOSED")
    payload = args[2]
    assert payload["pnl"] > 0 and payload["trades"] == 1 and payload["winrate"] == 100.0
    assert "🟢" in kwargs["text"] and "баланс" in kwargs["text"]  # маркер и итоговый баланс


def test_buy_sell_roundtrip(tmp_path):
    ex = _ex(tmp_path / "a.json")
    ex.mark_price("BTCUSDT", 100.0)
    fill_buy = ex.place_order("BTCUSDT", "BUY", 1.0)
    assert ex.get_position("BTCUSDT").qty == 1.0
    assert fill_buy.price == 100.0 * 1.0005  # slippage вверх при покупке

    ex.mark_price("BTCUSDT", 110.0)
    ex.place_order("BTCUSDT", "SELL", 1.0)
    assert ex.get_position("BTCUSDT") is None

    entry = 100.0 * 1.0005
    exit = 110.0 * 0.9995
    pnl = exit - entry
    fees = entry * 0.00075 + exit * 0.00075
    assert abs(ex.trades[0]["pnl"] - pnl) < 1e-9
    assert abs(ex.get_balance().cash - (1000.0 + pnl - fees)) < 1e-6


def test_short_position(tmp_path):
    ex = _ex(tmp_path / "a.json")
    ex.mark_price("BTCUSDT", 100.0)
    ex.place_order("BTCUSDT", "SELL", 1.0)
    assert ex.get_position("BTCUSDT").qty == -1.0
    ex.mark_price("BTCUSDT", 90.0)
    ex.place_order("BTCUSDT", "BUY", 1.0)
    assert ex.get_position("BTCUSDT") is None
    entry = 100.0 * 0.9995
    exit = 90.0 * 1.0005
    assert abs(ex.trades[0]["pnl"] - (entry - exit)) < 1e-9  # прибыль на падении


def test_partial_close_keeps_remaining(tmp_path):
    ex = _ex(tmp_path / "a.json")
    ex.mark_price("BTCUSDT", 100.0)
    ex.place_order("BTCUSDT", "BUY", 2.0)
    ex.mark_price("BTCUSDT", 110.0)
    ex.place_order("BTCUSDT", "SELL", 1.0)
    pos = ex.get_position("BTCUSDT")
    assert pos.qty == 1.0
    assert pos.entry_price == 100.0 * 1.0005  # средняя цена не меняется
    assert len(ex.trades) == 1


def test_unrealized_pnl_and_equity(tmp_path):
    ex = _ex(tmp_path / "a.json")
    ex.mark_price("BTCUSDT", 100.0)
    ex.place_order("BTCUSDT", "BUY", 1.0)
    ex.mark_price("BTCUSDT", 110.0)
    state = ex.get_balance()
    assert abs(state.unrealized_pnl - (110.0 - 100.0 * 1.0005)) < 1e-9
    assert abs(state.equity - (state.cash + state.unrealized_pnl)) < 1e-9


def test_market_order_requires_price(tmp_path):
    ex = _ex(tmp_path / "a.json")
    with pytest.raises(ValueError):
        ex.place_order("BTCUSDT", "BUY", 1.0)


def test_persistence_restores_state(tmp_path):
    path = tmp_path / "a.json"
    ex1 = _ex(path)
    ex1.mark_price("BTCUSDT", 100.0)
    ex1.place_order("BTCUSDT", "BUY", 1.0)
    ex1.mark_price("BTCUSDT", 110.0)
    ex1.place_order("BTCUSDT", "SELL", 1.0)
    assert path.exists()

    ex2 = _ex(path)  # загрузка из файла
    assert ex2.get_position("BTCUSDT") is None
    assert abs(ex2.get_balance().cash - ex1.get_balance().cash) < 1e-9
    assert len(ex2.trades) == 1
    assert ex2.trades[0]["pnl"] == ex1.trades[0]["pnl"]


def test_cancel_unknown_order(tmp_path):
    ex = _ex(tmp_path / "a.json")
    assert ex.cancel_order("nope") is False