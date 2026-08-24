"""Автономная самонастройка пейпера: раз в сутки (конфигурируемо) система
анализирует свои сделки за окно и двигает пороги в ограниченных пределах.
Каждая правка публикуется на панель и в журнал. Границы жёсткие: тюнер не
может выйти за [0.75×, 1.35×] от исходных значений — дрейф в мусор невозможен.

Правила v0 (детерминированные):
- winrate типа < floor при достаточной выборке  → поднять гейт силы на +5%
- winrate типа > ceiling                        → опустить на −5% (не душить край)
- доля зомби-входов (закрылись по таймауту) высокая → требовать больше импульса
- входов нет дольше starve_hours                → ослабить все гейты на −5%
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

from infrastructure.realtime import trader as T


def _load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))


def _base_bounds():
    """Исходные константы как база для границ дрейфа."""
    return {"ENTRY_MIN_STRENGTH": dict(T.ENTRY_MIN_STRENGTH),
            "MIN_MOMENTUM_PCT": T.MIN_MOMENTUM_PCT}


def day_stats(trader: T.BeaconTrader, window_hours: int = 24) -> dict:
    """Статистика окна: входы × закрытые сделки по символу+времени."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=window_hours)).isoformat(timespec="seconds")
    entries_path = trader.ex.persist_path.parent / "entries.jsonl"
    entries = []
    if entries_path.exists():
        for line in entries_path.read_text().splitlines():
            try:
                e = json.loads(line)
                if e["ts"] >= cutoff:
                    entries.append(e)
            except ValueError:
                continue
    trades = [t for t in trader.ex.trades
              if t.get("closed_at", "") >= cutoff]
    stats = {}
    used = set()
    for tr in trades:
        # последний вход по этому символу не позже открытия позиции
        cand = [e for e in entries if e["symbol"] == tr["symbol"]
                and e["ts"] <= tr.get("opened_at", "")]
        if not cand:
            continue
        e = max(cand, key=lambda x: x["ts"])
        s = stats.setdefault(e["type"], {"n": 0, "wins": 0})
        s["n"] += 1
        s["wins"] += tr["pnl"] > 0
        if tr.get("closed_at", "") and tr.get("opened_at", ""):
            age = (datetime.fromisoformat(tr["closed_at"])
                   - datetime.fromisoformat(tr["opened_at"])).total_seconds()
            if age >= T.POSITION_TIMEOUT_SEC - 5:
                stats.setdefault("_zombies", {"n": 0})["n"] += 1
        used.add(id(e))
    closed_n = len([t for t in trades])
    last_entry = max((e["ts"] for e in entries), default=None)
    return {"types": {k: v for k, v in stats.items() if not k.startswith("_")},
            "zombies": stats.get("_zombies", {}).get("n", 0),
            "closed": closed_n,
            "entries": len(entries),
            "last_entry_ts": last_entry}


def tune_once(trader: T.BeaconTrader, cfg: dict) -> list[str]:
    """Один цикл настройки. Возвращает список человекочитаемых правок."""
    base_path = Path(cfg["state_path"])
    saved = _load(base_path)
    if "base" not in saved:                      # первый запуск — фиксируем базу
        saved["base"] = _base_bounds()
        _save(base_path, saved)

    def clamp(cur, base, step):
        lo, hi = base * 0.75, base * 1.35
        nxt = min(hi, max(lo, cur * step))
        return nxt

    st = day_stats(trader, cfg.get("window_hours", 24))
    changes = []
    rules = cfg["rules"]
    strg = T.ENTRY_MIN_STRENGTH

    for btype, thr in list(strg.items()):
        s = st["types"].get(btype)
        base = saved["base"]["ENTRY_MIN_STRENGTH"].get(btype, thr)
        if s and s["n"] >= rules["min_sample"]:
            wr = s["wins"] / s["n"]
            if wr < rules["winrate_floor"]:
                nxt = clamp(thr, base, 1.05)
                if nxt != thr:
                    changes.append(f"{btype}: winrate {wr:.0%} (n={s['n']}) < "
                                   f"{rules['winrate_floor']:.0%} → гейт {thr}→{nxt:.2f}")
                    strg[btype] = round(nxt, 3)
            elif wr > rules["winrate_ceiling"]:
                nxt = clamp(thr, base, 0.95)
                if nxt != thr:
                    changes.append(f"{btype}: winrate {wr:.0%} (n={s['n']}) > "
                                   f"{rules['winrate_ceiling']:.0%} → гейт {thr}→{nxt:.2f}")
                    strg[btype] = round(nxt, 3)

    zshare = st["zombies"] / st["closed"] if st["closed"] else 0.0
    if st["closed"] >= rules["min_sample"] and \
            zshare > rules["zombie_share_max"]:
        cur, base = T.MIN_MOMENTUM_PCT, saved["base"]["MIN_MOMENTUM_PCT"]
        nxt = round(clamp(cur, base, 1.10), 6)
        if nxt != cur:
            changes.append(f"зомби {st['zombies']}/{st['closed']} > "
                           f"{rules['zombie_share_max']:.0%} → импульс "
                           f"{cur:.4f}→{nxt:.4f}")
            T.MIN_MOMENTUM_PCT = nxt

    if st["last_entry_ts"]:
        age_h = (datetime.now(timezone.utc)
                 - datetime.fromisoformat(st["last_entry_ts"])).total_seconds() / 3600
        if age_h >= rules["starve_hours"]:
            for btype, thr in list(strg.items()):
                base = saved["base"]["ENTRY_MIN_STRENGTH"].get(btype, thr)
                nxt = round(clamp(thr, base, 0.95), 3)
                if nxt != thr:
                    changes.append(f"голод {age_h:.0f}ч без входов → {btype} "
                                   f"{thr}→{nxt:.2f}")
                    strg[btype] = nxt

    saved["current"] = {"ENTRY_MIN_STRENGTH": dict(T.ENTRY_MIN_STRENGTH),
                        "MIN_MOMENTUM_PCT": T.MIN_MOMENTUM_PCT}
    summary = (f"автотюнинг: сделок {st['closed']} (зомби {st['zombies']}), "
               f"входов {st['entries']}; "
               + "; ".join(f"{k}={v['wins']}/{v['n']}"
                           for k, v in st["types"].items()))
    if changes:
        saved["last_applied"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        saved["changes_log"] = (saved.get("changes_log", []) + changes)[-50:]
        _save(base_path, saved)
    logger.info("%s | %s", summary, "; ".join(changes) or "правок нет")
    changes.append(summary)                       # сводка идёт последним событием
    return changes


async def tune_loop(trader: T.BeaconTrader, recent: list, publish, cfg: dict) -> None:
    interval = cfg["interval_min"] * 60
    while True:
        try:
            await asyncio.sleep(interval)
            for msg in tune_once(trader, cfg):
                sev = "info"
                ev = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "type": "autotune", "subtype": "report",
                      "symbol": "SYSTEM", "severity": sev, "detail": msg[:160]}
                recent.append(ev)
                publish(ev)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("tune_loop error")
            await asyncio.sleep(300)


def apply_saved(state_path: str) -> int:
    """Загрузить настроенные пороги при старте процесса (иначе рестарт сбрасывает
    тюнинг на дефолты). Возвращает число применённых значений."""
    saved = _load(Path(state_path))
    cur = saved.get("current")
    if not cur:
        return 0
    n = 0
    gates = cur.get("ENTRY_MIN_STRENGTH", {})
    for k, v in gates.items():
        if k in T.ENTRY_MIN_STRENGTH:
            T.ENTRY_MIN_STRENGTH[k] = float(v)
            n += 1
    mom = cur.get("MIN_MOMENTUM_PCT")
    if mom:
        T.MIN_MOMENTUM_PCT = float(mom)
        n += 1
    logger.info("загружен тюнинг: %s, momentum=%s",
                T.ENTRY_MIN_STRENGTH, T.MIN_MOMENTUM_PCT)
    return n
