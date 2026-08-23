"""Rolling-корреляция движения альткоинов с флагманом (BTC по умолчанию).

Справочный параметр, НЕ триггер сделок: поле btc_corr пишется в маячки,
срез by_symbol — в context.json для ревьюера/автотюнера.

Механика: периодический сэмпл последних цен (monitor._corr_loop берёт их из
потока стакана через PaperExecutor.last_price), доходности между тиками,
Pearson по скользящему окну через stdlib statistics.correlation.
"""
import statistics
from collections import deque

_MIN_PAIRS = 30   # минимум пар наблюдений, иначе корреляция не выдаётся


class CorrelationTracker:
    def __init__(self, anchor: str = "BTCUSDT", max_samples: int = 720):
        self.anchor = anchor
        self._max = max(_MIN_PAIRS + 1, max_samples)
        self._returns: dict[str, deque] = {}   # symbol -> доходность за тик (None если цены нет)
        self._prev: dict[str, float] = {}

    def sample(self, prices: dict[str, float]) -> None:
        """Один тик: снимок цен со всех символов (выравнивание по индексу тика)."""
        for sym, px in prices.items():
            if px <= 0:
                continue
            prev = self._prev.get(sym)
            ret = (px / prev - 1.0) if prev else None
            self._prev[sym] = px
            dq = self._returns.setdefault(sym, deque(maxlen=self._max))
            dq.append(ret)

    def corr(self, sym: str) -> float | None:
        """Pearson r доходностей sym против якоря по последним тикам; None если данных мало."""
        if sym == self.anchor:
            return None
        base, other = self._returns.get(self.anchor), self._returns.get(sym)
        if not base or not other:
            return None
        n = min(len(base), len(other))
        pairs = [(b, o) for b, o in zip(tuple(base)[-n:], tuple(other)[-n:])
                 if b is not None and o is not None]
        if len(pairs) < _MIN_PAIRS:
            return None
        try:
            return round(statistics.correlation(*zip(*pairs)), 3)
        except statistics.StatisticsError:
            return None

    def snapshot(self, syms=None) -> dict[str, float]:
        """{symbol: r} по списку символов (по умолчанию все, кроме якоря)."""
        return {s: r for s in (syms or self._returns)
                if s != self.anchor and (r := self.corr(s)) is not None}
