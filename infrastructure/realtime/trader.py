"""Клей «маячок → сделка» поверх PaperExecutor + RiskManager (ТЗ §15-16).

Вход: направленные маячки (стена/дисбаланс) по своей стороне, размер —
max_position_usd из config/paper.yaml в пределах свободного кэша.
Выход: противоположный маячок, таймаут 10 мин (курс «бага»: «в сделке
больше 10 минут не сидеть») или kill switch по дневному убытку/просадке.
"""
import json
import time
from datetime import datetime, timezone

from config.paper import (ACCOUNT_PATH, FEE_RATE, INITIAL_BALANCE, LATENCY_MS,
                          MAX_DAILY_LOSS, MAX_DRAWDOWN, MAX_EXPOSURE_USD,
                          MAX_OPEN_POSITIONS, MAX_POSITION_USD, SLIPPAGE_PCT)
from trading.execution.paper import PaperExecutor
from trading.risk.manager import RiskManager

ENTRY_TYPES = {"wall", "imbalance"}   # spread_expansion не направленный
POSITION_TIMEOUT_SEC = 600
ENTRY_MIN_STRENGTH = {"wall": 8.0, "imbalance": 0.85}   # только critical-сигналы
REENTRY_COOLDOWN_SEC = 600              # повтор по символу не раньше 10 мин («второй подход критично»)
GLOBAL_ENTRY_GAP_SEC = 180              # пауза между любыми входами («первый импульс ≤ 2 раз в час»)
EARLY_FAIL_SEC = 120                    # через 2 мин решаем: импульс или выход
MIN_MOMENTUM_PCT = 0.0005
# BASE_STRATEGY_PARAMS — зафиксированная базовая стратегия. Меняется ТОЛЬКО
# руками через коммит. Autotune/advisor имеют право менять только живые
# константы выше (слой paper-экспериментов); путь «эксперимент → новая база»
# автоматом запрещён: только через research (Paper → Result → Research).
BASE_STRATEGY_PARAMS = {
    "ENTRY_MIN_STRENGTH": dict(ENTRY_MIN_STRENGTH),
    "MIN_MOMENTUM_PCT": MIN_MOMENTUM_PCT,
}               # +5 б.п. в нашу сторону — минимальный импульс


class BeaconTrader:
    def __init__(self, ex: PaperExecutor | None = None, risk: RiskManager | None = None):
        self.ex = ex or PaperExecutor(INITIAL_BALANCE, FEE_RATE, SLIPPAGE_PCT,
                                      LATENCY_MS, ACCOUNT_PATH)
        self.risk = risk or RiskManager(MAX_POSITION_USD, MAX_EXPOSURE_USD,
                                        MAX_OPEN_POSITIONS, MAX_DAILY_LOSS, MAX_DRAWDOWN)
        self.day = ""
        self.day_start_equity = 0.0
        self.day_start_realized = 0.0
        self.peak = 0.0
        self.losses: list[float] = []
        self.reentry: dict[str, float] = {}
        self.last_entry = 0.0
        self.halt_path = self.ex.persist_path.parent / "halt.txt"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.halt_path.exists():
            if self.halt_path.read_text().strip() == today:
                self.risk.halt()   # хальт живёт до конца дня, переживает рестарты
            else:
                self.halt_path.unlink()
        self._roll_day()

    def _halt_for_today(self) -> None:
        self.risk.halt()
        self.halt_path.parent.mkdir(parents=True, exist_ok=True)
        self.halt_path.write_text(datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    # --- панель ---
    def summary(self) -> dict:
        bal = self.ex.get_balance()
        trades = self.ex.trades
        wins = sum(1 for t in trades if t["pnl"] > 0)
        eq = round(bal.equity, 2)
        return {"balance": round(bal.cash, 2), "equity": eq,
                "open": len(bal.positions), "trades": len(trades),
                "winrate": round(100 * wins / len(trades)) if trades else None,
                "day_pnl": round(eq - self.day_start_equity, 2),
                "killed": self.risk.killed,
                # последние сделки: ставка = позиционная сторона (закрытие лонга = SELL)
                "recent": [{"symbol": t["symbol"],
                            "opened": t.get("opened_at", ""),
                            "closed": t.get("closed_at", ""),
                            "bet": "long" if t["side"] == "SELL" else "short",
                            "pnl": round(t["pnl"], 4)} for t in trades[-30:]]}

    # --- рыночная цена ---
    def mark(self, sym: str, px: float | None) -> None:
        """Цена приходит напрямую из потока стакана (monitor._handle),
        не из маячков: сигналы лишь используют актуальную цену."""
        if px:
            self.ex.mark_price(sym, px)

    # --- основной вход ---
    def on_beacon(self, b: dict) -> None:
        sym, side = b["symbol"], str(b.get("side", "")).upper()
        if "price" in b:   # у spread_expansion цены нет — mark'ать нечем
            self.ex.mark_price(sym, b["price"])
        self._roll_day()
        self._timeouts()
        self._sync_kill()
        pos = self.ex.get_position(sym)
        if pos and side in ("BUY", "SELL") and ("BUY" if pos.qty > 0 else "SELL") != side:
            self._close(sym)
            pos = None
        if not pos and b["type"] in ENTRY_TYPES and side in ("BUY", "SELL"):
            self._entry(sym, side, b["type"], float(b.get("strength") or 0))

    # --- риск ---
    def _roll_day(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.day == today:
            return
        rollover = self.day != ""          # смена дня в живом процессе, не первый запуск
        self.day = today
        bal = self.ex.get_balance()
        self.day_start_equity = bal.equity
        self.day_start_realized = bal.realized_pnl
        self.peak = bal.equity
        self.losses.clear()
        if rollover:
            if self.risk.killed:
                self.risk.resume()
            if self.halt_path.exists():
                self.halt_path.unlink()   # вчерашний хальт больше не действует

    def _day_realized(self) -> float:
        return self.ex.get_balance().realized_pnl - self.day_start_realized

    def _sync_kill(self) -> None:
        bal = self.ex.get_balance()
        if self._day_realized() <= -self.risk.max_daily_loss or \
                (self.peak > 0 and (self.peak - bal.equity) / self.peak >= self.risk.max_drawdown):
            if not self.risk.killed:
                self._halt_for_today()
            for s in list(bal.positions):
                self._close(s)
        self.peak = max(self.peak, bal.equity)

    # --- сделки ---
    def _entry(self, sym: str, side: str, btype: str, strength: float) -> None:
        now = time.time()
        if now - self.last_entry < GLOBAL_ENTRY_GAP_SEC:
            return
        if now - self.reentry.get(sym, 0) < REENTRY_COOLDOWN_SEC:
            return
        if strength < ENTRY_MIN_STRENGTH.get(btype, float("inf")):
            return
        px = self.ex.last_price.get(sym)
        if px is None:   # imbalance/spread не несут цены; ждём wall-маяк с ценой
            return
        size_usd = min(self.risk.max_position_usd, self.ex.get_balance().cash)
        if size_usd <= 0:
            return
        qty = size_usd / px
        dec = self.risk.check_order(sym, side, qty, px, self.ex.get_balance(),
                                    self.ex.last_price, len(self.ex.positions),
                                    self._day_realized(), self.peak)
        if dec.allowed:
            self.ex.place_order(sym, side, qty)
            self.last_entry = now
            self.reentry[sym] = now
            with open(self.ex.persist_path.parent / "entries.jsonl", "a") as f:
                f.write(json.dumps({
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "symbol": sym, "type": btype, "strength": strength}) + "\n")

    def _close(self, sym: str) -> None:
        p = self.ex.get_position(sym)
        if p and sym in self.ex.last_price:
            self.ex.place_order(sym, "SELL" if p.qty > 0 else "BUY", abs(p.qty))

    def _timeouts(self) -> None:
        now = datetime.now(timezone.utc).timestamp()
        for s, p in list(self.ex.positions.items()):
            age = now - datetime.fromisoformat(p.opened_at).timestamp()
            if age >= POSITION_TIMEOUT_SEC:
                self._close(s)
                continue
            mark = self.ex.last_price.get(s)
            if age >= EARLY_FAIL_SEC and mark is not None:
                sign = 1 if p.qty > 0 else -1
                # слиппедж смещает entry: у лонга он завышен, у шорта занижен,
                # иначе флэтовый шорт выглядит как «+5 б.п.» и живёт до таймаута
                slip_adj = self.ex.slippage_pct * sign
                move = (mark - p.entry_price) / p.entry_price * sign - slip_adj
                if move < MIN_MOMENTUM_PCT:   # минус или флэт — зомби, выходим
                    self._close(s)
