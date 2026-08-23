"""Мост whale_monitor → панель: читает events/whale_events.jsonl и публикует
события китов в общий контур (recent + WS). Только контекст: в trader киты
НЕ попадают — торговые решения по ним запрещены ТЗ."""
import asyncio
import json
import logging
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)


def _severity(amount_usd: float | None) -> str:
    if amount_usd is None:
        return "info"
    if amount_usd >= 10_000_000:
        return "critical"
    if amount_usd >= 5_000_000:
        return "warning"
    return "info"


def _to_panel(e: dict) -> dict:
    usd = e.get("amount_usd")
    detail = f"{e.get('amount', 0):g} {e.get('asset', '')}"
    if usd:
        detail += f" (${usd / 1e6:.1f}M)"
    who = [x for x in (e.get("from_entity"), e.get("to_entity")) if x]
    if who:
        detail += f" {'→'.join(who)}"
    detail += (f" [{e.get('subtype')}, {e.get('status')}, "
               f"conf={e.get('confidence')}]" )
    return {
        "ts": e.get("timestamp"),
        "type": "onchain",
        "subtype": e.get("subtype"),
        "symbol": e.get("chain"),
        "tx_hash": e.get("tx_hash"),
        "amount_usd": usd,
        "detail": detail,
        "severity": _severity(usd),
    }


async def whale_loop(path: str | Path, recent: deque, publish) -> None:
    """Хвост jsonl-файла: offset в памяти, обрезка файла сбрасывает хвост,
    битая строка не роняет цикл. Публикация через тот же Event Bus, что и маячки."""
    path = Path(path)
    offset = 0
    while True:
        try:
            if path.exists():
                size = path.stat().st_size
                offset = min(offset, size)          # файл усечён/пересоздан
                if size > offset:
                    with path.open("r", encoding="utf-8") as f:
                        f.seek(offset)
                        for line in f:
                            offset += len(line.encode("utf-8"))
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                ev = _to_panel(json.loads(line))
                            except (ValueError, KeyError, TypeError):
                                logger.warning("whale_bridge: мусорная строка")
                                continue
                            recent.append(ev)
                            publish(ev)
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("whale_bridge error")
            await asyncio.sleep(5)
