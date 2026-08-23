"""Контрактные тесты веб-панели: расчёт книги символа и рассылка событий."""
import asyncio
import json
from collections import deque

from infrastructure.realtime.dashboard import Dashboard


class FakeBook:
    symbol = "X"
    bids = {100.0: 5.0, 99.0: 1.0, 101.0: 0.0}  # нулевой уровень — мусор дельт
    asks = {101.0: 2.0}


class FakeDet:
    books = {"X": FakeBook()}


def test_book_payload_math():
    d = Dashboard(FakeDet(), deque())
    p = d.book_payload("X")
    assert p["ok"] and p["bid"] == 100.0 and p["ask"] == 101.0
    assert p["mid"] == 100.5 and p["imbalance"] == 3.0  # 6/2 — топ-10 объёмы
    assert p["wall_bids"][0] == [100.0, 5.0, 1.7]       # крупнейший уровень ×медианы


def test_book_payload_missing_symbol():
    d = Dashboard(FakeDet(), deque())
    assert d.book_payload("NOPE")["ok"] is False


def test_publish_fans_out_to_clients():
    sent = []

    class Ws:
        async def send(self, msg):
            sent.append(json.loads(msg))

    async def main():
        d = Dashboard(FakeDet(), deque([{"ts": "t", "symbol": "X"}]))
        d._clients.add(Ws())
        d.publish({"symbol": "X", "strength": 2})
        await asyncio.gather(*d._tasks)
        snap_events = len(d.recent)

        dead = Ws()
        dead.send = None  # сломанный клиент не роняет рассылку
        d._clients.add(dead)
        d.publish({"symbol": "X", "strength": 3})
        await asyncio.sleep(0.05)
        return sent, snap_events

    sent, n = asyncio.run(main())
    assert [m["msg"] for m in sent] == ["event", "event"]
    assert sent[0]["strength"] == 2
    assert n == 1  # recent не тронут publish'ем
