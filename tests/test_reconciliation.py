"""Контрактные тесты reconciliation (ТЗ §17)."""
from trading.execution.interface import AccountState, Position
from trading.reconciliation import reconcile


def _state(cash=1000.0, positions=None):
    return AccountState(cash=cash, positions=positions or {})


def test_identical_states_no_issues():
    p = {"BTCUSDT": Position("BTCUSDT", 0.1, 100.0)}
    assert reconcile(_state(1000.0, p), _state(1000.0, p)) == []


def test_cash_mismatch():
    assert len(reconcile(_state(1000.0), _state(999.0))) == 1


def test_position_mismatch():
    a = _state(positions={"BTCUSDT": Position("BTCUSDT", 0.1, 100.0)})
    b = _state(positions={"BTCUSDT": Position("BTCUSDT", 0.2, 100.0)})
    assert any("BTCUSDT" in i for i in reconcile(a, b))


def test_position_only_on_one_side():
    a = _state(positions={"BTCUSDT": Position("BTCUSDT", 0.1, 100.0)})
    assert len(reconcile(a, _state())) == 1


def test_tolerance_respected():
    a = _state(1000.0, {"BTCUSDT": Position("BTCUSDT", 0.1000000001, 100.0)})
    assert reconcile(a, _state(1000.0, {"BTCUSDT": Position("BTCUSDT", 0.1, 100.0)}),
                     tolerance=1e-6) == []