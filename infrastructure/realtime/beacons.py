"""Маячки стакана (дух bybit_cup_analizer, реализация — стандартные метрики microstructure).

Детекторы на каждом обновлении стакана:
- imbalance: |Σbid - Σask| / Σ(bid+ask) по top_levels → давление покупки/продажи
- wall: уровень в top_levels с размером ≥ wall_ratio × медиана → стена
- spread_expansion: спред / медианный спред ≥ spread_ratio → расширение

Маячок: {'symbol', 'type', 'side', 'strength', 'detail'}. Cooldown на (symbol, type).
"""
import time
from collections import deque


class BookState:
    """Стакан одного символа: снапшот заменяет, delta обновляет уровни."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.spread_hist = deque(maxlen=200)

    def apply(self, update: dict) -> None:
        if update["type"] == "snapshot":
            self.bids = {float(p): float(s) for p, s in update["bids"]}
            self.asks = {float(p): float(s) for p, s in update["asks"]}
        else:  # delta: [price, size(, action)], action 0 = delete, 1 = insert/update
            # Bybit иногда присылает [price, size] без action; size 0 = удаление:
            # призрачные нулевые уровни не копим — иначе словарь растёт бесконечно
            for dst, rows in ((self.bids, update["bids"]), (self.asks, update["asks"])):
                for row in rows:
                    p, s = float(row[0]), float(row[1])
                    if s <= 0 or (len(row) > 2 and int(row[2]) == 0):
                        dst.pop(p, None)
                    else:
                        dst[p] = s

    def top(self, side: str, n: int) -> list:
        items = sorted(self.bids.items(), reverse=True) if side == "bids" else sorted(self.asks.items())
        return [(p, s) for p, s in items[:n]]


class BeaconDetector:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.books: dict[str, BookState] = {}
        self._last: dict[tuple, float] = {}

    def _ok(self, symbol: str, btype: str, now: float) -> bool:
        key = (symbol, btype)
        if now - self._last.get(key, 0.0) < self.cfg["cooldown_sec"]:
            return False
        self._last[key] = now
        return True

    def update(self, update: dict) -> list[dict]:
        sym = update["symbol"]
        book = self.books.setdefault(sym, BookState(sym))
        book.apply(update)
        now = time.time()
        return (self._imbalance(book, now) + self._wall(book, now)
                + self._spread(book, now))

    def _imbalance(self, book: BookState, now: float) -> list[dict]:
        n = self.cfg["top_levels"]
        bids, asks = book.top("bids", n), book.top("asks", n)
        # тонкий стакан (меньше top_levels уровней с одной стороны) — не сигнал, а шум
        if len(bids) < n or len(asks) < n:
            return []
        sb = sum(s for _, s in bids)
        sa = sum(s for _, s in asks)
        total = sb + sa
        if total == 0 or not self._ok(book.symbol, "imbalance", now):
            return []
        imb = abs(sb - sa) / total
        if imb < self.cfg["imbalance_threshold"]:
            return []
        return [{"symbol": book.symbol, "type": "imbalance",
                 "side": "buy" if sb > sa else "sell", "strength": round(imb, 3),
                 "bids": round(sb), "asks": round(sa),
                 "detail": f"bids={sb:.0f} asks={sa:.0f}"}]

    def _wall(self, book: BookState, now: float) -> list[dict]:
        out = []
        for side in ("bids", "asks"):
            levels = book.top(side, self.cfg["top_levels"])
            if len(levels) < 3:
                continue
            sizes = [s for _, s in levels]
            med = sorted(sizes)[len(sizes) // 2]
            if med <= 0 or not self._ok(book.symbol, "wall", now):
                continue
            price, size = max(levels, key=lambda x: x[1])
            ratio = size / med
            if ratio >= self.cfg["wall_ratio"]:
                out.append({"symbol": book.symbol, "type": "wall",
                            "side": "buy" if side == "bids" else "sell",
                            "strength": round(ratio, 1), "price": price,
                            "detail": f"price={price} size={size:.0f} med={med:.0f}"})
        return out

    def _spread(self, book: BookState, now: float) -> list[dict]:
        if not book.bids or not book.asks:
            return []
        best_bid, best_ask = max(book.bids), min(book.asks)
        if best_ask <= best_bid:
            return []
        mid = (best_bid + best_ask) / 2
        sp = (best_ask - best_bid) / mid
        hist = book.spread_hist
        hist.append(sp)
        if len(hist) < 20 or not self._ok(book.symbol, "spread", now):
            return []
        med = sorted(hist)[len(hist) // 2]
        if med <= 0:
            return []
        ratio = sp / med
        if ratio >= self.cfg["spread_ratio"]:
            return [{"symbol": book.symbol, "type": "spread_expansion",
                     "side": "both", "strength": round(ratio, 1),
                     "spread_pct": round(sp * 100, 3), "median_pct": round(med * 100, 3),
                     "detail": f"spread={sp:.4%} med={med:.4%}"}]
        return []


_SIDE_RU = {"buy": "покупку", "sell": "продажу"}


def humanize(b: dict) -> tuple[str, str]:
    """Маячок → (заголовок, тело) на человеческом русском для уведомлений."""
    sym = b["symbol"]
    side = _SIDE_RU.get(b["side"], b["side"])
    if b["type"] == "wall":
        title = f"Стена на {side}: {sym}"
        body = (f"Стена на {side} у цены {b['price']:g}\n"
                f"Объём: {b['strength']:g}× обычного уровня\n\n"
                "Крупный ордер в стакане удерживает цену — вероятен отскок.")
    elif b["type"] == "imbalance":
        title = f"Дисбаланс стакана: {sym}"
        body = (f"Давление {'покупателей' if b['side'] == 'buy' else 'продавцов'}\n"
                f"Биды {b['bids']:g} против асков {b['asks']:g}\n\n"
                "Одна сторона стакана сильно перевешивает — вероятно движение цены.")
    else:  # spread_expansion
        title = f"Спред расширился: {sym}"
        body = (f"Спред {b['spread_pct']:.2f}% (обычно {b['median_pct']:.2f}%)\n\n"
                "Стакан разрежен — вероятна волатильность или новости.")
    return title, body