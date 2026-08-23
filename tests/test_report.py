"""Тесты локального отчёта: агрегация статистики, рендер HTML, стаканы."""
from datetime import datetime
from zoneinfo import ZoneInfo

from infrastructure.realtime.cup import bar
from infrastructure.realtime.report import _cup_html, _render, _stats

MSK = ZoneInfo("Europe/Moscow")


def _b(ts: str, symbol: str, type_: str = "wall", severity: str = "critical") -> dict:
    return {"ts": ts, "symbol": symbol, "type": type_, "side": "buy",
            "strength": 5.0, "severity": severity, "detail": "price=1"}


def test_stats_counts_today_and_hour():
    now = datetime(2026, 8, 21, 15, 0, 0, tzinfo=MSK)
    beacons = [
        _b("2026-08-21T06:00:00+00:00", "BTCUSDT"),            # сегодня (09:00 МСК), не в часе
        _b("2026-08-21T11:30:00+00:00", "ETHUSDT"),            # в последнем часе (14:30 МСК)
        _b("2026-08-20T20:00:00+00:00", "BTCUSDT", severity="warning"),  # вчера
    ]
    st = _stats(beacons, now)
    assert st["total"] == 3 and st["today"] == 2 and st["hour"] == 1
    assert st["types_today"]["wall"] == 2
    assert st["sev_today"]["critical"] == 2
    assert st["top_symbols"][0] == ("BTCUSDT", 1)


def test_render_contains_sections_and_escapes():
    now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=MSK)
    page = _render([_b("2026-08-21T09:00:00+00:00", "<X&Y>")], "active",
                   "Fri 2026-08-21 01:58:22 MSK", now)
    assert "Что делает монитор" in page and "Последние маячки" in page
    assert "&lt;X&amp;Y&gt;" in page  # символ экранирован
    assert "cooldown: " in page       # живой конфиг подставлен


# --- стаканы ---

def test_cup_html_renders_ladder():
    b = _b("2026-08-21T09:00:00+00:00", "BTCUSDT")
    b["ladder"] = {"bids": [[100.0, 5.0], [99.0, 1.0]], "asks": [[101.0, 2.0]]}
    cup = _cup_html(b)
    assert "BTCUSDT" in cup and "100" in cup and "bar" in cup
    assert _cup_html({**b, "ladder": None}) == ""


def test_cup_bar_sqrt_scale():
    assert bar(9, 9) == "█" * 28
    assert bar(4, 9) == "█" * int((4 / 9) ** 0.5 * 28)  # sqrt-шкала как в старом стакане
    assert bar(0, 9) == ""
