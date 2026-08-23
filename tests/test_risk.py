"""Контрактные тесты Risk Manager (ТЗ §16): каждая проверка отклоняет ордер,
kill switch останавливает торговлю, отказы логируются."""
from trading.execution.paper import PaperExecutor
from trading.risk.manager import RiskManager


def _setup(tmp_path, **kw):
    ex = PaperExecutor(1000.0, fee_rate=0.00075, slippage_pct=0.0005,
                       latency_ms=50, persist_path=tmp_path / "a.json")
    params = dict(max_position_usd=50.0, max_exposure_usd=100.0,
                  max_open_positions=5, max_daily_loss=20.0, max_drawdown=0.2)
    params.update(kw)
    rm = RiskManager(**params)
    return ex, rm


def _check(ex, rm, qty=0.3, price=100.0, open_positions=0, day_pnl=0.0, peak=1000.0):
    return rm.check_order("BTCUSDT", "BUY", qty, price, ex.get_balance(),
                          prices={"BTCUSDT": price}, open_positions=open_positions,
                          day_realized_pnl=day_pnl, peak_equity=peak)


def test_allowed_when_fine(tmp_path):
    ex, rm = _setup(tmp_path)
    d = _check(ex, rm)
    assert d.allowed
    assert rm.rejections == []


def test_max_position_rejects(tmp_path):
    ex, rm = _setup(tmp_path)
    d = _check(ex, rm, qty=1.0, price=100.0)  # notional 100 > 50
    assert not d.allowed
    assert "notional" in d.reason


def test_max_exposure_rejects(tmp_path):
    ex, rm = _setup(tmp_path, max_exposure_usd=70.0)
    ex.mark_price("BTCUSDT", 100.0)
    ex.place_order("BTCUSDT", "BUY", 0.4)  # уже 40 в позиции
    d = _check(ex, rm, qty=0.5, price=100.0)  # notional 50 ок, но 40 + 50 = 90 > 70
    assert not d.allowed
    assert "exposure" in d.reason


def test_max_open_positions_rejects(tmp_path):
    ex, rm = _setup(tmp_path)
    d = _check(ex, rm, open_positions=5)  # 5 + 1 > 5
    assert not d.allowed
    assert "open positions" in d.reason


def test_daily_loss_rejects(tmp_path):
    ex, rm = _setup(tmp_path)
    d = _check(ex, rm, day_pnl=-21.0)
    assert not d.allowed
    assert "daily loss" in d.reason


def test_drawdown_rejects(tmp_path):
    ex, rm = _setup(tmp_path)
    ex.cash = 700.0  # equity 700 против пика 1000 → 30% просадка
    d = _check(ex, rm, peak=1000.0)
    assert not d.allowed
    assert "drawdown" in d.reason


def test_kill_switch(tmp_path):
    ex, rm = _setup(tmp_path)
    rm.halt()
    assert not _check(ex, rm).allowed
    assert "kill switch" in rm.rejections[-1]["reason"]
    rm.resume()
    assert _check(ex, rm).allowed


def test_rejections_logged(tmp_path):
    ex, rm = _setup(tmp_path)
    _check(ex, rm, qty=1.0, price=100.0)
    _check(ex, rm, qty=1.0, price=100.0)
    assert len(rm.rejections) == 2
    assert rm.rejections[0]["symbol"] == "BTCUSDT"