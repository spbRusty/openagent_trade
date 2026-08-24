"""Нотификационный слой: регулярный пуш счётчика маячков убран, редкие
отчёты и Risk Layer события остались (задача «Проанализируй текущую.txt»)."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import infrastructure.realtime.monitor as M
from infrastructure.realtime.monitor import _daily_report, _digest_text


def test_digest_30min_push_removed():
    """В коде не должно остаться отправки «Маячки: N за N мин» по таймеру 30 мин."""
    src = Path(M.__file__).read_text()
    assert "_digest_loop" not in src
    assert "Маячки: " not in src.replace("Маячков за", "")


def test_operational_report_sent_and_pending_cleared(monkeypatch):
    sent = []
    monkeypatch.setattr(M, "notify", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(M, "get", lambda: {"notify": {
        "min_level": "info", "report_operational_min": 5, "report_daily": False}})
    pending = [{"symbol": "BTCUSDT", "type": "wall", "severity": "warning",
                "strength": 9, "ts": "x"}]
    import asyncio

    async def run():
        # интервал через тестовый шов — никакой магии с часами
        task = asyncio.create_task(
            M._reports_loop(pending, None, Path("/nonexistent"),
                            op_interval_s=0.1))
        await asyncio.sleep(0.35)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(run())
    assert sent and "Оперативный отчёт" in sent[0][1]
    assert pending == []                       # статистика отдаана, буфер очищен


def test_daily_report_from_journal(tmp_path):
    now = datetime.now(timezone.utc)
    old = (now - timedelta(hours=30)).isoformat(timespec="seconds")
    fresh = (now - timedelta(hours=2)).isoformat(timespec="seconds")
    jl = tmp_path / "beacons.jsonl"
    jl.write_text("\n".join(json.dumps(r) for r in [
        {"ts": old, "type": "wall", "symbol": "OLD"},
        {"ts": fresh, "type": "wall", "symbol": "BTCUSDT"},
        {"ts": fresh, "type": "imbalance", "symbol": "BTCUSDT"},
        "{битая строка",
    ]) + "\n")

    class FakePaper:
        def summary(self):
            return {"trades": 7, "winrate": 0.43, "day_pnl": -0.31,
                    "balance": 998.6, "killed": False}

    txt = _daily_report(tmp_path, FakePaper())
    assert "Маячков за 24ч: 2" in txt          # старое и мусорное не считаются
    assert "wall=1" in txt and "imbalance=1" in txt
    assert "BTCUSDT×2" in txt
    assert "winrate 43%" in txt and "-0.3100" in txt


def test_halt_notifies_risk_layer(tmp_path):
    """Risk Layer / circuit breaker события идут в ntfy немедленно."""
    from infrastructure.realtime.trader import BeaconTrader
    from trading.risk.manager import RiskManager
    from trading.execution.paper import PaperExecutor
    sent = []
    monkey_target = "infrastructure.realtime.trader.notify"
    import infrastructure.realtime.trader as T
    orig = T.notify
    T.notify = lambda *a, **k: sent.append(a)
    try:
        ex = PaperExecutor(1000, 0.00075, 0.0005, 50, tmp_path / "a.json")
        tr = BeaconTrader(ex=ex, risk=RiskManager(5, 20, 3, 1.0, 0.2))
        tr._halt_for_today()
        assert sent and sent[0][:2] == ("CRITICAL", "RISK_HALT")
    finally:
        T.notify = orig
