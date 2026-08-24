"""LLM-советник: opencode раз в интервал получает контекст торговли и возвращает
вердикт СТРОГО в JSON. Применяются только параметры из белого списка, каждое
значение клампится в [0.75×, 1.35×] базы автотюнера — советник не может увести
систему за пределы безопасного дрейфа. Без JSON или с мусором — только журнал."""
import asyncio
import json
import logging
import re

from infrastructure.realtime import trader as T
from infrastructure.realtime.autotune import (_base_bounds, _load, _save,
                                              day_stats)

logger = logging.getLogger(__name__)

# белый список того, что советник может трогать: имя → (модуль, параметр)
PARAMS = {
    "gate_wall": ("ENTRY_MIN_STRENGTH", "wall"),
    "gate_imbalance": ("ENTRY_MIN_STRENGTH", "imbalance"),
    "min_momentum": ("MIN_MOMENTUM_PCT", None),
}


def build_context(trader: T.BeaconTrader, recent: list, beacons_1h: list) -> dict:
    return {
        "tunables_now": {"gate_wall": T.ENTRY_MIN_STRENGTH.get("wall"),
                         "gate_imbalance": T.ENTRY_MIN_STRENGTH.get("imbalance"),
                         "min_momentum_pct": T.MIN_MOMENTUM_PCT},
        "trade_stats_24h": day_stats(trader),
        "account": trader.summary(),
        "beacons_last_hour_by_type": {
            t: sum(1 for b in beacons_1h if b.get("type") == t)
            for t in ("wall", "imbalance", "spread_expansion")},
        "recent_beacons": [b for b in recent if b.get("type") in
                           ("wall", "imbalance")][-15:],
    }


def extract_verdict(text: str) -> dict | None:
    """Ищем JSON-вердикт с КОНЦА вывода: перед ним бывают логи инструментов,
    код и ANSI-коды. Берём последнюю сбалансированную фигурную скобку,
    у которой фрагмент парсится и похож на вердикт (changes/summary)."""
    starts = [m.start() for m in re.finditer(r"\{", text[-8000:])]
    for s in reversed(starts):
        seg = text[-8000:][s:]
        ends = [m.end() for m in re.finditer(r"\}", seg)]
        for e in reversed(ends[-60:]):
            try:
                v = json.loads(seg[:e])
            except ValueError:
                continue
            if isinstance(v, dict) and ("changes" in v or "summary" in v):
                return v
    return None


def apply_verdict(verdict: dict, state_path: str, publish=None) -> tuple[list, str]:
    """Клампит и применяет правки. Возвращает (применённые, сводка)."""
    saved = _load(state_path := __import__("pathlib").Path(state_path))
    if "base" not in saved:
        saved["base"] = _base_bounds()
        _save(state_path, saved)
    applied = []
    for ch in verdict.get("changes", [])[:5]:            # не больше 5 правок за раз
        name = ch.get("param")
        if name not in PARAMS:
            continue
        try:
            to = float(ch["to"])
        except (KeyError, TypeError, ValueError):
            continue
        attr, key = PARAMS[name]
        base_obj = saved["base"][attr]
        base = base_obj[key] if key else base_obj
        cur = getattr(T, attr)[key] if key else getattr(T, attr)
        lo, hi = base * 0.75, base * 1.35
        clamped = round(min(hi, max(lo, to)), 6 if attr == "MIN_MOMENTUM_PCT" else 3)
        if clamped == cur:
            continue
        reason = str(ch.get("reason", ""))[:80]
        if key:
            getattr(T, attr)[key] = clamped
        else:
            setattr(T, attr, clamped)
        line = f"{name}: {cur}→{clamped} ({reason})" + \
               (" [кламп]" if clamped != to else "")
        applied.append(line)
    saved["current"] = {"ENTRY_MIN_STRENGTH": dict(T.ENTRY_MIN_STRENGTH),
                        "MIN_MOMENTUM_PCT": T.MIN_MOMENTUM_PCT}
    saved["last_advisor"] = datetime_stamp()
    saved["changes_log"] = (saved.get("changes_log", []) + applied)[-50:]
    _save(state_path, saved)
    if applied:   # provenance: тот же реестр экспериментов, что у автотюнера
        from infrastructure.realtime.autotune import record_applied
        record_applied(str(state_path), "advisor",
                       [str(verdict.get("summary", ""))[:120]],
                       applied, saved.get("experiment", {}).get("last_stats"))
    return applied, str(verdict.get("summary", ""))[:160]


def datetime_stamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def ask_opencode(bin_path: str, prompt: str, timeout_sec: int) -> str | None:
    """Вывод — во временный ФАЙЛ, не в пайп: opencode плодит демонов, которые
    наследуют и держат пайпы (communicate() виснет до таймаута). Своя группа
    процессов: по таймауту убиваем всё дерево killpg, а не только родителя."""
    import os
    import signal
    import tempfile
    try:
        with tempfile.TemporaryFile() as tf:
            proc = await asyncio.create_subprocess_exec(
                bin_path, "run", prompt,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=tf, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True)
            try:
                await asyncio.wait_for(proc.wait(), timeout_sec)
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
                logger.warning("opencode timeout %ss — дерево убито", timeout_sec)
                return None
            tf.seek(0)
            return tf.read().decode(errors="replace")
    except OSError as e:
        logger.warning("opencode недоступен: %s", e)
        return None
