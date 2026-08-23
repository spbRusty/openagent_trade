"""Единый интерфейс исполнения (ТЗ §15, §18).

Paper и Live реализуют один контракт; разница — только в implementation
execution layer, а не в стратегии.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Order:
    id: str
    symbol: str
    side: str                 # BUY / SELL
    qty: float
    price: float | None       # None = market order
    kind: str = "market"
    status: str = "new"       # new → filled / cancelled
    created_at: str = field(default_factory=_now)


@dataclass
class Fill:
    order_id: str
    symbol: str
    side: str
    price: float
    qty: float
    fee: float
    latency_ms: int
    ts: str = field(default_factory=_now)


@dataclass
class Position:
    symbol: str
    qty: float                # signed: >0 long, <0 short
    entry_price: float
    opened_at: str = field(default_factory=_now)


@dataclass
class AccountState:
    cash: float
    positions: dict[str, Position]
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0

    @property
    def equity(self) -> float:
        return self.cash + self.unrealized_pnl


class ExecutionInterface(ABC):
    """Контракт, общий для Paper и Live. Стратегия не знает специфику биржи."""

    @abstractmethod
    def place_order(self, symbol: str, side: str, qty: float, price: float | None = None) -> Fill: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    def get_order(self, order_id: str) -> Order | None: ...

    @abstractmethod
    def get_position(self, symbol: str) -> Position | None: ...

    @abstractmethod
    def get_balance(self) -> AccountState: ...