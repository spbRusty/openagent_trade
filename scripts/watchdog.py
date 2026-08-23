#!/usr/bin/env python3
"""Сторож монитора: рестарт, если маячки замолчали (зомби-процесс).

Инцидент 23.08: event loop умер, процесс-зомби прожил 2 часа — systemd
не перезапускал, т.к. процесса «нет». Этот скрипт запускается root-таймером
каждые 5 минут и снаружи проверяет свежесть logs/realtime/beacons.jsonl.
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from infrastructure.notifications import notify  # noqa: E402

SERVICE = "realtime_monitor"
STALE_SEC = 15 * 60   # цикл маячков 10 мин; полтора цикла = деградация


def beacon_age_sec(path: Path) -> float | None:
    if not path.exists():
        return None
    return time.time() - path.stat().st_mtime


def decide(active: bool, age: float | None) -> str | None:
    """Причина рестарта или None если всё в порядке / вмешиваться не надо."""
    # ponytail: эвристика по mtime; при аномально тихом рынке возможен
    # ложный рестарт — лечится гистерезисом по журналу, добавлять при первом случае.
    if not active or age is None or age < STALE_SEC:
        return None
    return f"маячки молчат {age / 60:.0f} мин (порог {STALE_SEC // 60})"


def main() -> int:
    active = subprocess.run(
        ["systemctl", "is-active", "--quiet", SERVICE]).returncode == 0
    age = beacon_age_sec(ROOT / "logs" / "realtime" / "beacons.jsonl")
    reason = decide(active, age)
    if reason is None:
        return 0
    print(f"сторож: {reason}, рестарт {SERVICE}", flush=True)
    notify("CRITICAL", "WATCHDOG_RESTART",
           {"service": SERVICE, "reason": reason},
           f"Сторож: {reason}. Перезапускаю {SERVICE}.")
    return subprocess.run(["systemctl", "restart", SERVICE]).returncode


if __name__ == "__main__":
    sys.exit(main())
