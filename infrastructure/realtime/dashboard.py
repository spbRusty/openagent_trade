"""Real-time веб-панель (Задача.md): страница + WS-поток маячков на одном порту.

Event Bus живёт в процессе монитора: publish() из главного цикла рассылает
маячки всем подключённым браузерам. Стек: websockets (уже зависимость
Bybit-клиента), новых пакетов нет. Медленный клиент отваливается по таймауту
отправки и не тормозит сканер.
"""
import asyncio
import json
import statistics
import time
from pathlib import Path

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from infrastructure.realtime import hypoview

_PAGE = Path(__file__).with_name("dashboard.html")


class Dashboard:
    def __init__(self, detector, recent, paper=None):
        self.detector = detector
        self.recent = recent
        self.paper = paper
        self.started = time.time()
        self._clients: set = set()
        self._tasks: set = set()

    # --- Event Bus ---
    def publish(self, beacon: dict) -> None:
        # конверт "msg", не "type": у маячка своё поле type (wall/imbalance/…)
        msg = json.dumps({"msg": "event", **beacon}, ensure_ascii=False)
        for ws in list(self._clients):
            t = asyncio.create_task(self._send(ws, msg))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

    async def _send(self, ws, msg: str) -> None:
        try:
            await asyncio.wait_for(ws.send(msg), 5)
        except Exception:
            self._clients.discard(ws)

    # --- HTTP: одна страница; /ws — апгрейд до WebSocket ---
    def _process_request(self, conn, req):
        if req.path == "/":
            body = _PAGE.read_bytes()  # читаю на каждый запрос — правки без рестарта
            return Response(200, "OK",
                            Headers([("Content-Type", "text/html; charset=utf-8"),
                                     ("Content-Length", str(len(body)))]), body)
        if req.path != "/ws":
            return conn.respond(404, "not found")
        return None

    # --- данные панели символа ---
    @staticmethod
    def _walls(levels: list[tuple[float, float]]) -> list[list]:
        sizes = [s for _, s in levels]
        med = statistics.median(sizes) if sizes else 0.0
        return [[p, s, round(s / med, 1) if med else 0.0]
                for p, s in sorted(levels, key=lambda x: -x[1])[:3]]

    def book_payload(self, symbol: str) -> dict:
        book = self.detector.books.get(symbol)
        # дельты Bybit помечают удаление уровней нулевым размером — прочь их
        bids = sorted(((p, s) for p, s in book.bids.items() if s > 0),
                      reverse=True)[:10] if book else []
        asks = sorted(((p, s) for p, s in book.asks.items() if s > 0))[:10] if book else []
        if not bids or not asks:
            return {"msg": "book", "symbol": symbol, "ok": False}
        bid_vol, ask_vol = sum(s for _, s in bids), sum(s for _, s in asks)
        return {"msg": "book", "symbol": symbol, "ok": True,
                "bid": bids[0][0], "ask": asks[0][0],
                "mid": round((bids[0][0] + asks[0][0]) / 2, 8),
                "imbalance": round(bid_vol / ask_vol, 2) if ask_vol else None,
                "bids": [[p, s] for p, s in bids], "asks": [[p, s] for p, s in asks],
                "wall_bids": self._walls(bids), "wall_asks": self._walls(asks)}

    # --- WS-обработчик ---
    async def _handler(self, ws) -> None:
        self._clients.add(ws)
        try:
            snap = {"msg": "snapshot", "events": list(self.recent)[-100:],
                    "symbols": len(self.detector.books),
                    "uptime_sec": int(time.time() - self.started)}
            if self.paper:
                snap["account"] = self.paper.summary()
                snap["research"] = hypoview.snapshot()
            await ws.send(json.dumps(snap, ensure_ascii=False, default=str))
            async for raw in ws:
                req = json.loads(raw)
                if req.get("msg") == "book":
                    await ws.send(json.dumps(self.book_payload(req["symbol"]),
                                             ensure_ascii=False))
        except Exception:
            pass
        finally:
            self._clients.discard(ws)

    async def run(self, port: int) -> None:
        async def heartbeat():
            while True:
                await asyncio.sleep(10)
                msg = {"msg": "heartbeat"}
                if self.paper:
                    msg["account"] = self.paper.summary()
                    msg["research"] = hypoview.snapshot()
                for ws in list(self._clients):
                    await self._send(ws, json.dumps(msg))

        hb = asyncio.create_task(heartbeat())
        try:
            async with serve(self._handler, "127.0.0.1", port,
                             process_request=self._process_request):
                await asyncio.Future()
        finally:
            hb.cancel()
