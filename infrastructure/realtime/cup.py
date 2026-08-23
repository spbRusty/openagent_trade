"""Живой стакан одного символа в терминале (ASCII, ANSI-цвета).

Запуск: .venv/bin/python -m infrastructure.realtime.cup BTCUSDT [уровней]
Выход: Ctrl+C. Переиспользует ws.py и beacons.py; маячки печатаются под стаканом.
"""
import asyncio
import sys

from config.realtime import get
from infrastructure.realtime.beacons import BeaconDetector, BookState
from infrastructure.realtime.ws import BybitWS

_BAR_W = 28


def bar(size: float, max_size: float) -> str:
    return "█" * int((size / max_size) ** 0.5 * _BAR_W) if max_size else ""


def draw(book: BookState, levels: int, last_beacon: str) -> None:
    bids, asks = book.top("bids", levels), book.top("asks", levels)
    out = ["\x1b[2J\x1b[H", f"{book.symbol}   (Ctrl+C — выход)"]
    if not bids or not asks:
        out.append("ждём снапшот…")
        print("\n".join(out), flush=True)
        return
    mx = max(s for _, s in bids + asks)
    mid = (bids[0][0] + asks[0][0]) / 2
    for p, s in reversed(asks):
        out.append(f"\x1b[31m{p:>14g}\x1b[0m \x1b[31m{bar(s, mx):{_BAR_W}}\x1b[0m {s:>12g}")
    out.append(f"\x1b[33m{'—' * 10} {mid:g} {'—' * 22}\x1b[0m")
    for p, s in bids:
        out.append(f"\x1b[32m{p:>14g}\x1b[0m \x1b[32m{bar(s, mx):{_BAR_W}}\x1b[0m {s:>12g}")
    if last_beacon:
        out.append(f"\n\x1b[33mмаячок: {last_beacon}\x1b[0m")
    print("\n".join(out), flush=True)


async def amain(symbol: str, levels: int) -> None:
    cfg = get()["ws"]
    queue: asyncio.Queue = asyncio.Queue(maxsize=5000)
    ws = BybitWS([symbol], cfg["depth"], cfg["base_url"],
                 cfg["ping_interval"], tuple(cfg["reconnect_backoff"]), queue)
    det = BeaconDetector(get()["beacons"])
    book = BookState(symbol)
    last_beacon = ""

    async def consume() -> None:
        nonlocal last_beacon
        while True:
            u = await queue.get()
            book.apply(u)
            found = det.update(u)
            if found:
                b = found[-1]
                last_beacon = f"{b['type']} {b['side']} ×{b['strength']}"

    task = asyncio.create_task(consume())
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(ws.run())
            while True:
                draw(book, levels, last_beacon)
                await asyncio.sleep(0.5)
    finally:
        task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(amain(sys.argv[1].upper(), int(sys.argv[2]) if len(sys.argv) > 2 else 15))
    except KeyboardInterrupt:
        pass
