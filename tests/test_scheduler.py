"""Контрактные тесты scheduler jobs (ТЗ §21): каждый job шлёт нотификацию,
health_check различает свежий/устаревший манифест."""
import json
from datetime import datetime, timedelta, timezone

import polars as pl

from infrastructure.scheduler import jobs


def _make_raw(tmp_path, symbol="BTCUSDT"):
    d = tmp_path / "raw"
    d.mkdir(parents=True, exist_ok=True)
    times = pl.datetime_range(pl.datetime(2025, 1, 1), pl.datetime(2025, 1, 1, 0, 4),
                              interval="1m", eager=True)
    pl.DataFrame({"open_time": times, "open": [100.0] * 5, "high": [101.0] * 5,
                  "low": [99.0] * 5, "close": [100.5] * 5, "volume": [1.0] * 5}) \
        .write_parquet(d / f"{symbol}_linear_1m.parquet")
    return d


def _manifest(tmp_path, generated_at: str):
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "1m_manifest.json"
    p.write_text(json.dumps({"generated_at": generated_at, "symbols": {"BTCUSDT": {}},
                             "failed": {}}))
    return p


def test_data_update_notifies(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(jobs, "notify", lambda lvl, ev, data: sent.append((lvl, ev, data)))
    monkeypatch.setattr(jobs, "DATA_1M_DIR", _make_raw(tmp_path))
    monkeypatch.setattr(jobs, "DATA_NORMALIZED", tmp_path / "out")
    monkeypatch.setattr(jobs, "DATA_METADATA", tmp_path / "meta")
    jobs.data_update()
    assert sent[0][0] == "INFO"
    assert sent[0][1] == "DATA_UPDATE"
    assert sent[0][2]["ok"] == 1


def test_paper_monitor_no_account(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(jobs, "notify", lambda lvl, ev, data: sent.append((lvl, ev, data)))
    monkeypatch.setattr(jobs, "ROOT", tmp_path)
    jobs.paper_monitor()
    assert sent[0][1] == "PAPER_MONITOR"
    assert sent[0][2]["status"] == "no paper account yet"


def test_paper_monitor_with_account(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(jobs, "notify", lambda lvl, ev, data: sent.append((lvl, ev, data)))
    acc_dir = tmp_path / "data" / "paper"
    acc_dir.mkdir(parents=True)
    (acc_dir / "account.json").write_text(json.dumps({"cash": 123.45, "positions": {"X": 1},
                                                      "trades": [1, 2]}))
    monkeypatch.setattr(jobs, "ROOT", tmp_path)
    jobs.paper_monitor()
    assert sent[0][0] == "TRADE"
    assert sent[0][2]["balance"] == 123.45
    assert sent[0][2]["open_positions"] == 1


def test_health_check_fresh_manifest_info(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(jobs, "notify", lambda lvl, ev, data: sent.append((lvl, ev, data)))
    monkeypatch.setattr(jobs, "DATA_METADATA", tmp_path)
    monkeypatch.setattr(jobs.db, "connect", lambda: _ctx())
    monkeypatch.setattr(jobs.db, "recent_experiments", lambda conn, limit=10: [])
    monkeypatch.setattr(jobs.db, "list_hypotheses", lambda conn: [])
    _manifest(tmp_path, datetime.now(timezone.utc).isoformat())
    jobs.health_check()
    assert sent[0][0] == "INFO"
    assert sent[0][2]["manifest_age_days"] == 0.0


def test_health_check_stale_manifest_warning(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(jobs, "notify", lambda lvl, ev, data: sent.append((lvl, ev, data)))
    monkeypatch.setattr(jobs.db, "connect", lambda: _ctx())
    monkeypatch.setattr(jobs.db, "recent_experiments", lambda conn, limit=10: [])
    monkeypatch.setattr(jobs.db, "list_hypotheses", lambda conn: [])
    _manifest(tmp_path, (datetime.now(timezone.utc) - timedelta(days=10)).isoformat())
    monkeypatch.setattr(jobs, "DATA_METADATA", tmp_path)
    jobs.health_check()
    assert sent[0][0] == "WARNING"
    assert sent[0][2]["manifest_age_days"] == 10.0


def test_health_check_missing_manifest_critical(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(jobs, "notify", lambda lvl, ev, data: sent.append((lvl, ev, data)))
    monkeypatch.setattr(jobs, "DATA_METADATA", tmp_path)
    jobs.health_check()
    assert sent[0][0] == "CRITICAL"


def test_all_jobs_registered():
    assert set(jobs.JOBS) == {"data_update", "daily_research", "paper_monitor", "health_check"}


def _ctx():
    class C:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def recent_experiments(self, limit=10):
            return []

        def list_hypotheses(self):
            return []

    return C()