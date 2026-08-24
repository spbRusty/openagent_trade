"""Realtime-монитор: главный цикл (paper-only исполнительный контур).

- топ-N символов по turnover (REST), refresh каждые refresh_hours (перезапуск, если изменился)
- WS-подписка стаканов → маячки → журнал jsonl + ntfy
- opencode-хук по расписанию: snapshot → opencode run → журнал вердикта (параметры подстраиваются с опытом)
- SIGHUP → перезагрузка config/realtime.yaml без рестарта

Запуск: .venv/bin/python -m infrastructure.realtime.monitor
"""
import asyncio
import json
import logging
import signal
import sys
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.realtime import get, reload as reload_cfg
from config.settings import ROOT
from infrastructure.notifications import notify
from infrastructure.realtime.autotune import apply_saved, tune_loop
from infrastructure.realtime.advisor import (ask_opencode, build_context,
                                              apply_verdict, extract_verdict)
from infrastructure.realtime.beacons import BeaconDetector
from infrastructure.realtime.corr import CorrelationTracker
from infrastructure.realtime.external import fng_loop, rss_loop
from infrastructure.realtime.dashboard import Dashboard
from infrastructure.realtime.trader import BeaconTrader
from infrastructure.realtime.whale_bridge import whale_loop
from infrastructure.realtime.ws import BybitWS, top_symbols_by_turnover

logger = logging.getLogger(__name__)
_T0 = time.monotonic()

# strength -> уровень уведомления
_CRITICAL = {"wall": 8.0, "spread_expansion": 6.0, "imbalance": 0.85}
_RANK = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}
_TYPE_RU = {"wall": "стена", "imbalance": "дисбаланс", "spread_expansion": "спред"}


class Journal:
    """Аппенд-журнал jsonl (logs/realtime/{beacons,events,opencode}.jsonl)."""

    def __init__(self, base: Path):
        base.mkdir(parents=True, exist_ok=True)
        self._files = {n: open(base / f"{n}.jsonl", "a") for n in ("beacons", "events", "opencode")}

    def write(self, name: str, record: dict) -> None:
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record}
        self._files[name].write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._files[name].flush()


def _severity(beacon: dict) -> str:
    return "critical" if beacon["strength"] >= _CRITICAL[beacon["type"]] else "warning"


def _notify_if(level: str, event: str, data: dict, text: str | None = None) -> None:
    min_rank = _RANK.get(get()["notify"]["min_level"].upper(), 1)
    if _RANK[level] >= min_rank:
        notify(level, event, data, text)


def _digest_text(pending: list[dict], minutes: int) -> str:
    by_sym: dict[str, list] = {}
    for b in pending:
        by_sym.setdefault(b["symbol"], []).append(b)
    lines = [f"Маячков за {minutes} мин: {len(pending)}"]
    for sym, bs in sorted(by_sym.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:10]:
        kinds = "+".join(sorted({_TYPE_RU.get(b["type"], b["type"]) for b in bs}))
        lines.append(f"• {sym}: {kinds}, макс ×{max(b['strength'] for b in bs):g}")
    if len(by_sym) > 10:
        lines.append(f"• … ещё символов: {len(by_sym) - 10}")
    return "\n".join(lines)


async def _digest_loop(pending: list) -> None:
    while True:
        minutes = max(1, get()["notify"].get("digest_min", 30))
        await asyncio.sleep(minutes * 60)
        if not pending:
            continue
        level = "WARNING" if any(b.get("severity") == "critical" for b in pending) else "INFO"
        notify(level, f"Маячки: {len(pending)} за {minutes} мин", None,
               _digest_text(pending, minutes))
        pending.clear()


async def _corr_loop(state: dict) -> None:
    """Сэмпл цен для rolling-корреляции альтов с флагманом (справочное поле)."""
    while True:
        await asyncio.sleep(get()["correlation"]["sample_sec"])
        state["corr"].sample(dict(state["paper"].ex.last_price))


async def _handle(queue: asyncio.Queue, state: dict, journal: Journal,
                  recent: deque, pending: list) -> None:
    while True:
        update = await queue.get()
        for b in state["detector"].update(update):
            # ts нет от детектора (его ставил только Journal на диске) — метим сразу,
            # иначе падают панель и opencode-цикл (KeyError валил сервис)
            b.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="seconds"))
            sev = _severity(b)
            # стакан на момент сигнала — для визуализации в отчёте
            book = state["detector"].books.get(b["symbol"])
            if book:
                b["ladder"] = {"bids": book.top("bids", 10), "asks": book.top("asks", 10)}
            c = state["corr"].corr(b["symbol"])
            if c is not None:
                b["btc_corr"] = c   # справочное поле, в сделки не входит
            recent.append({**b, "severity": sev})
            journal.write("beacons", {"severity": sev, **b})
            pending.append(b)
            state["dash"].publish(b)
            try:
                state["paper"].on_beacon(b)
            except Exception:   # исполнительный слой не должен ронять сканер
                logger.exception("paper trader error on %s", b.get("symbol"))
        # цена — напрямую из потока стакана, на каждом апдейте книги
        book = state["detector"].books.get(update["symbol"])
        if book and book.bids and book.asks:
            state["paper"].mark(update["symbol"],
                                round((max(book.bids) + min(book.asks)) / 2, 10))


async def _maintenance_loop(paper, interval_s: int = 15) -> None:
    """Таймауты и kill-switch по ЧАСАМ, а не по приходу маячков: между
    сигналами бывают паузы в минуты — без этого цикла ранний фейл не стреляет."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            paper._timeouts()
            paper._sync_kill()
        except Exception:
            logger.exception("maintenance error")


async def _opencode_loop(journal: Journal, recent: deque, state: dict) -> None:
    cfg = get()["opencode"]
    while True:
        await asyncio.sleep(cfg["interval_min"] * 60)
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
        beacons = [b for b in recent if b["ts"] >= cutoff]
        ctx = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "symbols": len(state["detector"].books),
            "beacons_last_hour": len(beacons),
            "beacons_by_type": {t: sum(1 for b in beacons if b["type"] == t)
                                for t in ("wall", "imbalance", "spread_expansion")},
            "top_symbols_by_beacons": sorted(
                {b["symbol"]: sum(1 for x in beacons if x["symbol"] == b["symbol"])
                 for b in beacons}.items(), key=lambda x: -x[1])[:10],
            "recent_beacons": beacons[-20:],
        }
        ctx["btc_correlation"] = {
            "anchor": get()["correlation"]["anchor"],
            "window_min": get()["correlation"]["window_min"],
            "by_symbol": state["corr"].snapshot({b["symbol"] for b in beacons}),
        }
        age = None
        if recent:
            age = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(recent[-1]["ts"])).total_seconds()
        ctx["health"] = {"uptime_sec": int(time.monotonic() - _T0),
                         "last_beacon_age_sec": int(age) if age is not None else None}
        ctx_path = ROOT / "logs" / "realtime" / "context.json"
        ctx_path.parent.mkdir(parents=True, exist_ok=True)
        ctx_path.write_text(json.dumps(ctx, ensure_ascii=False, indent=1))
        # контекст торговли для советника
        ctx.update(build_context(state["paper"], recent, beacons))
        ctx_path.write_text(json.dumps(ctx, ensure_ascii=False, indent=1))
        journal.write("events", {"event": "OPENCODE_START", "beacons": len(beacons)})
        out = await ask_opencode(cfg["bin"], cfg["prompt"], cfg["timeout_sec"])
        if out is None:
            journal.write("opencode", {"verdict": "UNAVAILABLE/TIMEOUT"})
            continue
        text = out[-6000:]
        verdict = extract_verdict(text)
        applied, summary = [], ""
        if verdict:
            try:
                applied, summary = apply_verdict(
                    verdict, str(ROOT / "data/paper/autotune.json"),
                    state["dash"].publish)
            except Exception:
                logger.exception("advisor apply error")
        journal.write("opencode", {"verdict": text[-4000:],
                                   "applied": applied,
                                   "summary": summary,
                                   "context": ctx_path.name})
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for line in applied:
            ev = {"ts": now_iso, "type": "autotune", "subtype": "llm",
                  "symbol": "SYSTEM", "severity": "warning", "detail": line[:160]}
            recent.append(ev)
            state["dash"].publish(ev)
        ev = {"ts": now_iso, "type": "autotune", "subtype": "report",
              "symbol": "SYSTEM", "severity": "info",
              "detail": ("советник: правок %d; " % len(applied)) + summary}
        recent.append(ev)
        state["dash"].publish(ev)


async def _refresh_loop(journal: Journal, cfg_get) -> None:
    """Каждые refresh_hours пересчитывать топ-N; изменился → выход (systemd перезапустит)."""
    cfg = get()["symbols"]
    while True:
        await asyncio.sleep(cfg["refresh_hours"] * 3600)
        new = top_symbols_by_turnover(cfg["top_k"], cfg["min_turnover_usd"])
        if new != cfg_get["symbols"]:
            journal.write("events", {"event": "SYMBOLS_CHANGED", "old": len(cfg_get["symbols"]),
                                     "new": len(new)})
            sys.exit(42)  # systemd Restart=always поднимет с новым топ-N


def _make_hup(journal: Journal, state: dict):
    def _hup():
        reload_cfg()
        state["detector"] = BeaconDetector(get()["beacons"])
        journal.write("events", {"event": "CONFIG_RELOADED"})
        logger.info("config reloaded (SIGHUP)")
    return _hup


async def amain() -> None:
    cfg = get()
    journal = Journal(ROOT / cfg["journal"]["dir"])
    state = {"detector": BeaconDetector(cfg["beacons"])}
    recent: deque = deque(maxlen=2000)
    paper = BeaconTrader()
    corr_cfg = cfg["correlation"]
    corr = CorrelationTracker(corr_cfg["anchor"],
                              corr_cfg["window_min"] * 60 // corr_cfg["sample_sec"])
    dash = Dashboard(state["detector"], recent, paper)
    state["dash"] = dash
    state["paper"] = paper
    state["corr"] = corr

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGHUP, _make_hup(journal, state))

    symbols = top_symbols_by_turnover(cfg["symbols"]["top_k"], cfg["symbols"]["min_turnover_usd"])
    if not symbols:
        logger.error("нет символов для мониторинга (top_k/min_turnover)")
        sys.exit(1)
    journal.write("events", {"event": "MONITOR_STARTED", "symbols": len(symbols),
                             "first": symbols[:5]})
    logger.info("monitor started: %d symbols", len(symbols))
    _notify_if("INFO", "MONITOR_STARTED", {"symbols": len(symbols)})

    apply_saved(str(ROOT / "data/paper/autotune.json"))
    queue: asyncio.Queue = asyncio.Queue(maxsize=5000)
    pending: list = []
    ws = BybitWS(symbols, cfg["ws"]["depth"], cfg["ws"]["base_url"],
                 cfg["ws"]["ping_interval"], tuple(cfg["ws"]["reconnect_backoff"]), queue)
    await asyncio.gather(ws.run(), _handle(queue, state, journal, recent, pending),
                         _digest_loop(pending), dash.run(cfg["dashboard"]["port"]),
                         _opencode_loop(journal, recent, state),
                         _corr_loop(state),
                         whale_loop(cfg["whale"]["events_path"], recent, dash.publish),
                         *([fng_loop(cfg["external"], recent, dash.publish)] if cfg["external"]["fng_enabled"] else []),
                         *([rss_loop(cfg["external"], recent, dash.publish)] if cfg["external"]["rss_enabled"] else []),
                         *([tune_loop(paper, recent, dash.publish, cfg["autotune"])] if cfg["autotune"]["enabled"] else []),
                         _maintenance_loop(paper),
                         _refresh_loop(journal, {"symbols": symbols}))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(amain())