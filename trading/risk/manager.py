"""Risk Manager (ТЗ §16): проверки перед каждым ордером + глобальный kill switch.

Поток: Signal → Risk checks → Order allowed? → NO: reject + log / YES: execution.
"""
from dataclasses import dataclass

from trading.execution.interface import AccountState


@dataclass
class RiskDecision:
    allowed: bool
    reason: str = ""


class RiskManager:
    def __init__(self, max_position_usd: float, max_exposure_usd: float,
                 max_open_positions: int, max_daily_loss: float, max_drawdown: float):
        self.max_position_usd = max_position_usd
        self.max_exposure_usd = max_exposure_usd
        self.max_open_positions = max_open_positions
        self.max_daily_loss = max_daily_loss
        self.max_drawdown = max_drawdown
        self.killed = False
        self.rejections: list[dict] = []

    def halt(self):
        """Глобальный kill switch."""
        self.killed = True

    def resume(self):
        self.killed = False

    def check_order(self, symbol: str, side: str, qty: float, price: float,
                    state: AccountState, prices: dict[str, float], open_positions: int,
                    day_realized_pnl: float, peak_equity: float) -> RiskDecision:
        if self.killed:
            return self._reject(symbol, "kill switch active")
        notional = price * qty
        if notional > self.max_position_usd:
            return self._reject(symbol, f"notional {notional:.2f} > max_position {self.max_position_usd:.2f}")
        exposure = sum(abs(p.qty) * prices.get(s, p.entry_price)
                       for s, p in state.positions.items()) + notional
        if exposure > self.max_exposure_usd:
            return self._reject(symbol, f"exposure {exposure:.2f} > max {self.max_exposure_usd:.2f}")
        if open_positions + 1 > self.max_open_positions:
            return self._reject(symbol, f"open positions {open_positions} >= max {self.max_open_positions}")
        if day_realized_pnl <= -self.max_daily_loss:
            return self._reject(symbol, f"daily loss {day_realized_pnl:.2f} exceeds limit")
        if peak_equity > 0 and (peak_equity - state.equity) / peak_equity >= self.max_drawdown:
            dd = (peak_equity - state.equity) / peak_equity
            return self._reject(symbol, f"drawdown {dd:.1%} >= max {self.max_drawdown:.0%}")
        return RiskDecision(True, "ok")

    def _reject(self, symbol: str, reason: str) -> RiskDecision:
        self.rejections.append({"symbol": symbol, "reason": reason})
        return RiskDecision(False, reason)