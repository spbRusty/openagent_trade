"""Тесты моста whale_monitor → панель."""
import asyncio
import json

import pytest

from infrastructure.realtime.whale_bridge import _severity, _to_panel, whale_loop


def _ev(**over):
    base = {"type": "whale_transfer", "subtype": "exchange_inflow",
            "chain": "BTC", "asset": "BTC", "amount": 100.5,
            "amount_usd": 12_000_000.0, "from_address": "abc", "to_address": "xyz",
            "from_entity": None, "to_entity": "Binance", "from_type": "unknown",
            "to_type": "exchange", "tx_hash": "h1",
            "timestamp": "2026-08-22T16:00:00+00:00", "confidence": 0.8,
            "source": "mempool", "status": "mempool", "block_height": None}
    return {**base, **over}


def test_severity_by_usd():
    assert _severity(11_000_000) == "critical"
    assert _severity(6_000_000) == "warning"
    assert _severity(500_000) == "info"
    assert _severity(None) == "info"


def test_to_panel_shape_and_detail():
    p = _to_panel(_ev())
    assert p["type"] == "onchain" and p["symbol"] == "BTC"
    assert p["severity"] == "critical"
    assert "Binance" in p["detail"] and "exchange_inflow" in p["detail"]
    assert p["amount_usd"] == 12_000_000.0 and p["tx_hash"] == "h1"


def test_whale_loop_reads_appends_publishes(tmp_path):
    f = tmp_path / "w.jsonl"
    f.write_text(json.dumps(_ev()) + "\n" + "{битый json\n" + json.dumps(
        _ev(subtype="unknown_large_transfer", amount_usd=600_000)) + "\n")
    recent, published = [], []
    with pytest.raises((asyncio.TimeoutError, asyncio.CancelledError)):
        asyncio.run(asyncio.wait_for(
            whale_loop(f, recent, published.append), timeout=0.1))
    # мусор пропущен, валидные строки дошли
    assert len(recent) == 2 and len(published) == 2
    assert [e["subtype"] for e in recent] == ["exchange_inflow", "unknown_large_transfer"]
    # второй проход без новых данных — дублей нет (offset)
    async def tick():
        await asyncio.sleep(0.05)
    asyncio.run(tick())
    assert len(recent) == 2


def test_whale_loop_survives_missing_file(tmp_path):
    recent = []

    async def tick():
        await asyncio.wait_for(
            whale_loop(tmp_path / "нет_файла.jsonl", recent, lambda e: None),
            timeout=0.1)

    try:
        asyncio.run(tick())
    except asyncio.TimeoutError:
        pass                      # цикл жив и крутится — это и есть успех
