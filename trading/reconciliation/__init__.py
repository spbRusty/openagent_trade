"""Reconciliation (ТЗ §17): сверка внутреннего состояния и биржи.

Если состояния расходятся — система должна остановить новые сделки и создать
alert. Здесь — детерминированная сверка; вызывающий код решает: halt risk
manager + CRITICAL-уведомление.
"""
from trading.execution.interface import AccountState


def reconcile(internal: AccountState, exchange: AccountState, tolerance: float = 1e-9) -> list[str]:
    """Вернуть список расхождений (пусто — состояния согласованы)."""
    issues = []
    if abs(internal.cash - exchange.cash) > tolerance:
        issues.append(f"cash: internal={internal.cash:.2f} exchange={exchange.cash:.2f}")
    symbols = set(internal.positions) | set(exchange.positions)
    for sym in sorted(symbols):
        pi = internal.positions.get(sym)
        pe = exchange.positions.get(sym)
        qi = pi.qty if pi else 0.0
        qe = pe.qty if pe else 0.0
        if abs(qi - qe) > tolerance:
            issues.append(f"position {sym}: internal={qi} exchange={qe}")
    return issues