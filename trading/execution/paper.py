"""PaperExecutor — симуляция исполнения на реальных ценах (ТЗ §15).

Моделирует: ордера, fills, латентность, проскальзывание, комиссии, позиции,
баланс, PnL. Состояние счёта сохраняется в JSON после каждой мутации.
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from infrastructure.notifications import notify
from trading.execution.interface import AccountState, ExecutionInterface, Fill, Order, Position


class PaperExecutor(ExecutionInterface):
    def __init__(self, initial_balance: float, fee_rate: float, slippage_pct: float,
                 latency_ms: int, persist_path: Path):
        self.initial_balance = initial_balance
        self.fee_rate = fee_rate
        self.slippage_pct = slippage_pct
        self.latency_ms = latency_ms
        self.persist_path = persist_path
        self.cash = initial_balance
        self.orders: dict[str, Order] = {}
        self.positions: dict[str, Position] = {}
        self.trades: list[dict] = []
        self.last_price: dict[str, float] = {}
        if persist_path.exists():
            self._load()

    # --- контракт ExecutionInterface ---
    def place_order(self, symbol: str, side: str, qty: float, price: float | None = None) -> Fill:
        px = price if price is not None else self.last_price.get(symbol)
        if px is None:
            raise ValueError(f"no market price for {symbol}")
        slip = 1 + (self.slippage_pct if side == "BUY" else -self.slippage_pct)
        fill_px = px * slip
        fee = fill_px * qty * self.fee_rate
        order = Order(str(uuid.uuid4()), symbol, side, qty, price, "market", "filled")
        self.orders[order.id] = order
        self._apply(symbol, fill_px, qty, side, fee)
        self._persist()
        return Fill(order.id, symbol, side, fill_px, qty, fee, self.latency_ms)

    def cancel_order(self, order_id: str) -> bool:
        order = self.orders.get(order_id)
        if order is None or order.status == "filled":
            return False
        order.status = "cancelled"
        self._persist()
        return True

    def get_order(self, order_id: str) -> Order | None:
        return self.orders.get(order_id)

    def get_position(self, symbol: str) -> Position | None:
        return self.positions.get(symbol)

    def get_balance(self) -> AccountState:
        unrealized = sum(
            p.qty * (self.last_price.get(s, p.entry_price) - p.entry_price)
            for s, p in self.positions.items())
        realized = sum(t["pnl"] for t in self.trades)
        return AccountState(self.cash, dict(self.positions), unrealized, realized)

    # --- данные рынка ---
    def mark_price(self, symbol: str, price: float) -> None:
        self.last_price[symbol] = price

    # --- учёт ---
    def _apply(self, symbol: str, px: float, qty: float, side: str, fee: float):
        signed = qty if side == "BUY" else -qty
        pos = self.positions.get(symbol)
        self.cash -= fee
        if pos is None or pos.qty * signed >= 0:
            # открытие или увеличение
            if pos is None:
                pos = Position(symbol, 0.0, px)
                self.positions[symbol] = pos
            total = pos.qty + signed
            if total != 0:
                pos.entry_price = (pos.entry_price * pos.qty + px * signed) / total
            pos.qty = total
            if pos.qty == 0:
                del self.positions[symbol]
        else:
            # уменьшение/закрытие: реализуем PnL по закрытой части
            closed = min(abs(pos.qty), abs(signed))
            pnl = closed * (px - pos.entry_price) * (1 if pos.qty > 0 else -1)
            self.cash += pnl
            self.trades.append({"symbol": symbol, "qty": closed, "side": side,
                                "entry": pos.entry_price, "exit": px, "fee": fee, "pnl": pnl,
                                "opened_at": pos.opened_at,
                                "closed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            pos.qty += signed
            if pos.qty == 0:
                del self.positions[symbol]
            wins = sum(1 for t in self.trades if t["pnl"] > 0)
            wr = 100 * wins / len(self.trades)
            icon = "🟢" if pnl >= 0 else "🔴"
            held_sec = max(0, (datetime.now(timezone.utc)
                               - datetime.fromisoformat(pos.opened_at)).total_seconds())
            held = (f"{held_sec / 60:.0f} мин" if held_sec < 3600
                    else f"{held_sec / 3600:.0f} ч {held_sec % 3600 // 60:.0f} мин")
            notify("TRADE", "TRADE_CLOSED",
                   {"symbol": symbol, "pnl": round(pnl, 6),
                    "held_min": round(held_sec / 60),
                    "trades": len(self.trades), "winrate": round(wr, 1)},
                   text=f"{icon} закрыт {symbol}: pnl {pnl:+.4f}, держали {held} | "
                        f"всего сделок {len(self.trades)}, винрейт {wr:.0f}% | "
                        f"баланс {self.cash:.2f}")

    def _persist(self):
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "initial_balance": self.initial_balance, "cash": self.cash,
            "positions": {s: {"qty": p.qty, "entry_price": p.entry_price, "opened_at": p.opened_at}
                          for s, p in self.positions.items()},
            "trades": self.trades,
            "orders": {oid: {"symbol": o.symbol, "side": o.side, "qty": o.qty, "price": o.price,
                             "kind": o.kind, "status": o.status, "created_at": o.created_at}
                       for oid, o in self.orders.items()},
            "last_price": self.last_price,
        }
        self.persist_path.write_text(json.dumps(data, ensure_ascii=False, indent=1))

    def _load(self):
        d = json.loads(self.persist_path.read_text())
        self.initial_balance = d["initial_balance"]
        self.cash = d["cash"]
        self.positions = {s: Position(s, p["qty"], p["entry_price"], p["opened_at"])
                          for s, p in d["positions"].items()}
        self.trades = d["trades"]
        self.orders = {oid: Order(oid, o["symbol"], o["side"], o["qty"], o["price"],
                                  o["kind"], o["status"], o["created_at"])
                       for oid, o in d["orders"].items()}
        self.last_price = d["last_price"]