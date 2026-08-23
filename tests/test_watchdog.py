"""Тесты сторожа: решение о рестарте и его исполнение."""
import importlib.util
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("watchdog", ROOT / "scripts" / "watchdog.py")
wd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wd)


def test_decide():
    assert wd.decide(active=True, age=60) is None            # свежо
    assert wd.decide(active=False, age=9999) is None         # остановлен руками — не трогаем
    assert wd.decide(active=True, age=None) is None          # файла нет — нечего рестартить
    reason = wd.decide(active=True, age=wd.STALE_SEC + 1)
    assert reason and "молчат" in reason


def test_main_restart_path(monkeypatch, tmp_path):
    beacon = tmp_path / "logs" / "realtime" / "beacons.jsonl"
    beacon.parent.mkdir(parents=True)
    beacon.write_text("{}\n")
    monkeypatch.setattr(wd, "ROOT", tmp_path)
    stale = time.time() - wd.STALE_SEC - 10
    os.utime(beacon, (stale, stale))                          # протухший mtime

    calls = []

    def fake_run(cmd, **k):
        calls.append(cmd[1])                                 # глагол systemctl
        if cmd[:2] == ["systemctl", "is-active"]:
            return subprocess.CompletedProcess(cmd, 0)       # active
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    notes = []
    monkeypatch.setattr(wd, "notify",
                        lambda *a, **k: notes.append(a[1]))
    assert wd.main() == 0
    assert "restart" in calls and notes == ["WATCHDOG_RESTART"]

    calls.clear()
    os.utime(beacon)                                          # свежий mtime
    assert wd.main() == 0 and "restart" not in calls          # ничего не делает
