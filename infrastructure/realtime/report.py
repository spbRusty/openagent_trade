"""Локальный HTML-отчёт о realtime-мониторе: что делает и что насчитал.

Запуск: .venv/bin/python -m infrastructure.realtime.report
→ генерирует logs/realtime/report.html (снимок на момент запуска) и открывает в браузере.
Только stdlib, без серверов и зависимостей.
"""
import html
import json
import subprocess
import webbrowser
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config.realtime import get
from config.settings import ROOT

MSK = ZoneInfo("Europe/Moscow")
_TYPE_RU = {"wall": "Стена", "imbalance": "Дисбаланс", "spread_expansion": "Спред"}
_SEV_RU = {"critical": "критический", "warning": "предупреждение"}

_WHAT_IT_DOES = [
    "Подключается по WebSocket к Bybit: стаканы (глубина 50) топ-50 символов по обороту.",
    "На каждом обновлении стакана ищет маячки: <b>стена</b> (уровень ≥ N× медианы), "
    "<b>дисбаланс</b> (перевес бидов/асков), <b>расширение спреда</b>.",
    "Повтор одного типа на символ подавляется cooldown'ом (сейчас {cooldown_sec} c).",
    "Всё пишет в журнал logs/realtime/beacons.jsonl; в ntfy — сделки и сводка маячков раз в {digest_min} мин.",
    "Раз в час отправляет сводку на opencode-ревью; SIGHUP перечитывает конфиг без рестарта.",
]


def _load(name: str) -> list[dict]:
    path = ROOT / get()["journal"]["dir"] / f"{name}.jsonl"
    if not path.exists():
        return []
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def _msk(ts: str) -> datetime:
    return datetime.fromisoformat(ts).astimezone(MSK)


def _service_status() -> tuple[str, str]:
    """(active/inactive, время последнего запуска) — systemctl без sudo."""
    try:
        active = subprocess.run(["systemctl", "is-active", "realtime_monitor"],
                                capture_output=True, text=True).stdout.strip()
        since = subprocess.run(["systemctl", "show", "realtime_monitor",
                                "-p", "ActiveEnterTimestamp", "--value"],
                               capture_output=True, text=True).stdout.strip()
        return active or "?", since
    except OSError:
        return "?", ""


def _stats(beacons: list[dict], now: datetime) -> dict:
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hour_ago = now - timedelta(hours=1)
    today = [b for b in beacons if _msk(b["ts"]) >= day_start]
    return {
        "total": len(beacons),
        "today": len(today),
        "hour": sum(1 for b in beacons if _msk(b["ts"]) >= hour_ago),
        "types_today": Counter(b["type"] for b in today),
        "sev_today": Counter(b.get("severity", "?") for b in today),
        "top_symbols": Counter(b["symbol"] for b in today).most_common(10),
    }


_CSS = """
body{font-family:sans-serif;margin:2rem auto;max-width:60rem;padding:0 1rem;
     color:#ddd;background:#141821;line-height:1.5}
h1{font-size:1.4rem} h2{font-size:1.1rem;margin-top:2rem;border-bottom:1px solid #333;
   padding-bottom:.3rem}
table{border-collapse:collapse;width:100%;font-size:.85rem}
th,td{border:1px solid #333;padding:.3rem .5rem;text-align:left}
th{background:#1d2330} tr:nth-child(even){background:#181d28}
.crit{color:#ff7b72;font-weight:bold}.warn{color:#d29922}
.kv{display:inline-block;margin:.15rem 1rem .15rem 0;background:#1d2330;
    padding:.2rem .6rem;border-radius:.3rem}
.muted{color:#888;font-size:.85rem}
.cups{display:flex;flex-wrap:wrap;gap:1rem}
.cup{background:#181d28;border:1px solid #333;padding:.5rem .7rem;border-radius:.3rem}
.cup h3{margin:.1rem 0 .4rem;font-size:.85rem}
.lvl{display:flex;align-items:center;font-size:.75rem;line-height:1.4}
.lvl .p{width:6.5rem;text-align:right;margin-right:.4rem;font-variant-numeric:tabular-nums}
.lvl .bar{display:block;height:.55rem;border-radius:2px;min-width:2px}
.lvl.a .bar{background:#ff4466}.lvl.b .bar{background:#00ff88}
.lvl .s{margin-left:.4rem;color:#888}
.mid{color:#ffd54d;font-weight:bold;margin:.15rem 0}
"""


def _esc(v) -> str:
    return html.escape(str(v))


def _cup_html(b: dict) -> str:
    lad = b.get("ladder") or {}
    bids, asks = lad.get("bids") or [], lad.get("asks") or []
    if not bids or not asks:
        return ""
    mx = max(s for _, s in bids + asks) or 1

    def lvl(rows, cls):
        return "".join(
            f"<div class='lvl {cls}'><span class='p'>{p:g}</span>"
            f"<span class='bar' style='width:{(s / mx) ** 0.5 * 100:.0f}%'></span>"
            f"<span class='s'>{s:g}</span></div>"
            for p, s in rows)

    mid = (bids[0][0] + asks[0][0]) / 2
    return (f"<div class='cup'><h3>{_esc(b['symbol'])} · "
            f"{_TYPE_RU.get(b['type'], b['type'])} · {_msk(b['ts']):%H:%M:%S}</h3>"
            f"{lvl(reversed(asks), 'a')}<div class='mid'>{mid:g}</div>{lvl(bids, 'b')}</div>")


def _render(beacons: list[dict], active: str, since: str, now: datetime) -> str:
    cfg = get()
    bc = cfg["beacons"]
    st = _stats(beacons, now)
    types = " · ".join(f"{_TYPE_RU[t]}: {n}" for t, n in st["types_today"].most_common()) or "—"
    sevs = " · ".join(f"{_SEV_RU.get(s, s)}: {n}" for s, n in st["sev_today"].most_common()) or "—"
    what = "".join(f"<li>{w.format(cooldown_sec=bc['cooldown_sec'],
                                  digest_min=cfg['notify'].get('digest_min', 30))}</li>" for w in _WHAT_IT_DOES)
    tops = "".join(f"<span class='kv'>{_esc(s)}: {n}</span>" for s, n in st["top_symbols"]) \
        or "<span class='muted'>пока пусто</span>"
    rows = []
    for b in sorted(beacons, key=lambda x: x["ts"], reverse=True)[:30]:
        sev = b.get("severity", "warning")
        cls = "crit" if sev == "critical" else "warn"
        rows.append(
            f"<tr><td>{_msk(b['ts']):%H:%M:%S}</td><td>{_esc(b['symbol'])}</td>"
            f"<td>{_TYPE_RU.get(b['type'], b['type'])}</td><td>{_esc(b['side'])}</td>"
            f"<td>{_esc(b['strength'])}</td><td class='{cls}'>{_SEV_RU.get(sev, sev)}</td>"
            f"<td class='muted'>{_esc(b.get('detail', ''))}</td></tr>")
    table = ("".join(rows) if rows else
             "<tr><td colspan='7' class='muted'>маячков пока нет</td></tr>")
    with_ladder = [b for b in sorted(beacons, key=lambda x: x["ts"], reverse=True)
                   if b.get("ladder")]
    cups = ("".join(_cup_html(b) for b in with_ladder[:4]) or
            "<p class='muted'>снимков стакана ещё нет — появятся у маячков после обновления монитора</p>")
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Realtime-монитор — отчёт</title><style>{_CSS}</style></head><body>
<h1>Realtime-монитор Bybit — отчёт</h1>
<p class="muted">Сгенерирован: {now:%d.%m.%Y %H:%M:%S} МСК · снимок данных</p>

<h2>Что делает монитор</h2>
<ul>{what}</ul>

<h2>Статус</h2>
<p><span class="kv">Сервис: {_esc(active)}</span>
<span class="kv">Запущен: {_esc(since or '?')}</span></p>
<p><span class="kv">cooldown: {bc['cooldown_sec']} c</span>
<span class="kv">стена от: {bc['wall_ratio']}× медианы</span>
<span class="kv">дисбаланс от: {bc['imbalance_threshold']}</span>
<span class="kv">спред от: {bc['spread_ratio']}× медианы</span>
<span class="kv">ntfy: сделки + дайджест {cfg['notify'].get('digest_min', 30)} мин</span></p>

<h2>Результаты</h2>
<p><span class="kv">Сегодня: {st['today']}</span>
<span class="kv">За час: {st['hour']}</span>
<span class="kv">Всего в журнале: {st['total']}</span></p>
<p>По типам сегодня: {types}<br>По важности: {sevs}</p>
<p>Топ символов сегодня:<br>{tops}</p>

<h2>Последние маячки (30)</h2>
<table><tr><th>Время</th><th>Символ</th><th>Тип</th><th>Сторона</th>
<th>Сила</th><th>Важность</th><th>Детали</th></tr>{table}</table>

<h2>Стаканы последних сигналов</h2>
<div class="cups">{cups}</div>
</body></html>"""


def main() -> None:
    out = ROOT / get()["journal"]["dir"] / "report.html"
    now = datetime.now(MSK)
    active, since = _service_status()
    page = _render(_load("beacons"), active, since, now)
    out.write_text(page, encoding="utf-8")
    print(out)
    webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
