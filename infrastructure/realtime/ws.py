"""WS-клиент Bybit V5 (публичный linear) для realtime-монитора.

Подписки: orderbook.<depth>.<symbol> для топ-N символов.
- снапшот → полная замена стакана
- delta → обновление уровней (action 0=delete, 1=update/insert)
- ping каждые ping_interval сек, reconnect с backoff
"""
import asyncio
import json
import logging

import websockets.asyncio.client

from exchanges.bybit import BybitAdapter

logger = logging.getLogger(__name__)

_TOPICS_PER_CONN = 10  # лимит Bybit: 10 подписок на одно соединение


def top_symbols_by_turnover(k: int, min_turnover_usd: float) -> list[str]:
    """Топ-k по turnover 24h из публичного REST (источник — тот же адаптер)."""
    adapter = BybitAdapter()
    rows = []
    for r in adapter._get("/v5/market/tickers", {"category": "linear"}).get("list", []):
        try:
            t = float(r.get("turnover24h") or 0)
        except (TypeError, ValueError):
            t = 0.0
        if t >= min_turnover_usd:
            rows.append((r["symbol"], t))
    rows.sort(key=lambda x: -x[1])
    return [s for s, _ in rows[:k]]


def parse_orderbook_update(msg: dict) -> dict | None:
    """Сообщение orderbook.* → {'symbol', 'type': snapshot|delta, 'bids', 'asks'}.

    bids/asks — списки [price, size, (action)]; для snapshot action нет.
    Возвращает None для не-orderbook сообщений.
    """
    topic = msg.get("topic", "")
    if not topic.startswith("orderbook."):
        return None
    data = msg.get("data") or {}
    if not isinstance(data, dict):
        return None
    return {
        "symbol": data.get("s", topic.rsplit(".", 1)[-1]),
        "type": msg.get("type"),
        "bids": data.get("b", []),
        "asks": data.get("a", []),
    }


class BybitWS:
    """Агрегирует N WS-соединений (по 10 подписок). События в asyncio.Queue."""

    def __init__(self, symbols: list[str], depth: int, base_url: str,
                 ping_interval: int, backoff: tuple, queue: asyncio.Queue):
        self.symbols = symbols
        self.depth = depth
        self.base_url = base_url
        self.ping_interval = ping_interval
        self.backoff = backoff
        self.queue = queue

    def _chunks(self):
        for i in range(0, len(self.symbols), _TOPICS_PER_CONN):
            yield self.symbols[i:i + _TOPICS_PER_CONN]

    async def _sub(self, symbols: list[str]) -> None:
        url = f"{self.base_url}?symbols={','.join(symbols)}"
        topics = [f"orderbook.{self.depth}.{s}" for s in symbols]
        backoff = self.backoff[0]
        while True:
            try:
                async with websockets.asyncio.client.connect(url,
                                                             ping_interval=None,
                                                             max_size=2 ** 24) as ws:
                    await ws.send(json.dumps({"op": "subscribe", "args": topics}))
                    logger.info("connected: %d symbols", len(symbols))
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("op") == "pong":
                            continue
                        if msg.get("success") is False:
                            logger.error("subscribe error: %s", raw[:200])
                            continue
                        parsed = parse_orderbook_update(msg)
                        if parsed:
                            await self.queue.put(parsed)
                    # соединение закрылось — reconnect
                    logger.warning("connection closed, reconnecting in %ss", backoff)
            except Exception as e:
                logger.error("ws error: %s; reconnect in %ss", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.backoff[1])

    async def run(self) -> None:
        await asyncio.gather(*(self._sub(chunk) for chunk in self._chunks()))


def connect_and_listen(symbols: list[str], cfg: dict, queue: asyncio.Queue) -> BybitWS:
    return BybitWS(symbols, cfg["depth"], cfg["base_url"], cfg["ping_interval"],
                   tuple(cfg["reconnect_backoff"]), queue)