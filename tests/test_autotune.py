"""Тесты автотюнера: правила, границы дрейфа, персистентность."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import infrastructure.realtime.trader as T
from infrastructure.realtime.autotune import day_stats, tune_once


def mk(tmp_path, trades=(), entries=()):
    """Фейковый трейдер с нужной историей, без сети и реального исполнения."""
    _trades, _entries = list(trades), list(entries)

    class FakeEx:
        persist_path = tmp_path / "acc.json"
        trades = _trades

    class FakeTrader:
        ex = FakeEx()

    entries_path = tmp_path / "entries.jsonl"
    if _entries:
        entries_path.parent.mkdir(parents=True, exist_ok=True)
        entries_path.write_text(
            "\n".join(json.dumps(e) for e in _entries) + "\n")
    elif entries_path.exists():
        entries_path.unlink()          # пустая история = чистый файл
    return FakeTrader()


import json

CFG = {"state_path": None, "window_hours": 24,
       "rules": {"min_sample": 3, "winrate_floor": 0.35,
                 "winrate_ceiling": 0.60, "zombie_share_max": 0.50,
                 "starve_hours": 6}}


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _trade(sym, pnl, opened_min_ago, closed_sec_after_open):
    o = datetime.now(timezone.utc) - timedelta(minutes=opened_min_ago)
    c = o + timedelta(seconds=closed_sec_after_open)
    return {"symbol": sym, "pnl": pnl, "opened_at": _iso(o), "closed_at": _iso(c)}


def _entry(sym, btype="wall", strength=9.0, min_ago=30):
    return {"ts": _iso(datetime.now(timezone.utc) - timedelta(minutes=min_ago)),
            "symbol": sym, "type": btype, "strength": strength}


def test_day_stats_joins_entry_to_trade(tmp_path):
    tr = mk(tmp_path, trades=[_trade("BTC", +0.1, 60, 120)],
            entries=[_entry("BTC", min_ago=65)])
    st = day_stats(tr)
    assert st["types"]["wall"]["n"] == 1 and st["types"]["wall"]["wins"] == 1
    assert st["closed"] == 1 and st["zombies"] == 0


def test_low_winrate_raises_gate_and_clamped_to_bounds(tmp_path):
    old_gates = dict(T.ENTRY_MIN_STRENGTH)
    T.ENTRY_MIN_STRENGTH = dict(old_gates)
    try:
        # 4 сделки wall, все убыточные → winrate 0% < floor
        trades = [_trade(f"B{i}", -0.05, 100 - i * 10, 300) for i in range(4)]
        entries = [_entry(f"B{i}", min_ago=101 - i * 10) for i in range(4)]
        cfg = dict(CFG, state_path=str(tmp_path / "at.json"))
        t = mk(tmp_path, trades=trades, entries=entries)
        changes = tune_once(t, cfg)
        assert any("гейт" in c for c in changes)
        assert T.ENTRY_MIN_STRENGTH["wall"] > old_gates["wall"]
        # жёсткий прогон: гейт не может превысить 1.35× базы
        base = json.loads((tmp_path / "at.json").read_text())["base"]
        hi = base["ENTRY_MIN_STRENGTH"]["wall"] * 1.35
        for _ in range(50):
            tune_once(mk(tmp_path, trades=[_trade(f"B{i}", -0.05, 100, 300)
                                        for i in range(4)],
                         entries=[]), cfg)   # входов нет — правок по типам нет
        assert T.ENTRY_MIN_STRENGTH["wall"] <= hi + 1e-9
    finally:
        T.ENTRY_MIN_STRENGTH = old_gates


def test_high_winrate_lowers_gate_but_not_below_floor_bound(tmp_path):
    old_gates = dict(T.ENTRY_MIN_STRENGTH)
    T.ENTRY_MIN_STRENGTH = dict(old_gates)
    try:
        trades = [_trade(f"G{i}", +0.2, 100 - i * 10, 300) for i in range(4)]
        entries = [_entry(f"G{i}", min_ago=101 - i * 10) for i in range(4)]
        cfg = dict(CFG, state_path=str(tmp_path / "at.json"))
        changes = tune_once(mk(tmp_path, trades=trades, entries=entries), cfg)
        assert any("→ гейт" in c and "0.95" not in c.split("→")[0] for c in changes) or \
            any("winrate" in c for c in changes)
        assert T.ENTRY_MIN_STRENGTH["wall"] < old_gates["wall"]
        base_hi = json.loads((tmp_path / "at.json").read_text())["base"]
        lo = base_hi["ENTRY_MIN_STRENGTH"]["wall"] * 0.75
        assert T.ENTRY_MIN_STRENGTH["wall"] >= lo - 1e-9
    finally:
        T.ENTRY_MIN_STRENGTH = old_gates


def test_zombie_share_raises_momentum_requirement(tmp_path):
    saved_pct = T.MIN_MOMENTUM_PCT
    saved_gates = dict(T.ENTRY_MIN_STRENGTH)
    T.MIN_MOMENTUM_PCT = 0.0005
    try:
        # сделки живут ровно таймаут → зомби
        trades = [_trade(f"Z{i}", -0.01, 200 - i, 600) for i in range(4)]
        entries = [_entry(f"Z{i}", min_ago=201 - i) for i in range(4)]
        cfg = dict(CFG, state_path=str(tmp_path / "at.json"))
        changes = tune_once(mk(tmp_path, trades=trades, entries=entries), cfg)
        assert any("импульс" in c for c in changes)
        assert T.MIN_MOMENTUM_PCT > 0.0005
        assert T.MIN_MOMENTUM_PCT <= 0.0005 * 1.35 * (1.10 ** 3) + 1e-9
    finally:
        T.MIN_MOMENTUM_PCT = saved_pct
        T.ENTRY_MIN_STRENGTH.clear()
        T.ENTRY_MIN_STRENGTH.update(saved_gates)


def test_state_persists_base_across_runs(tmp_path):
    cfg = dict(CFG, state_path=str(tmp_path / "at.json"))
    t = mk(tmp_path)                      # пустая история — только фиксация базы
    tune_once(t, cfg)
    saved = json.loads((tmp_path / "at.json").read_text())
    assert "base" in saved and "wall" in saved["base"]["ENTRY_MIN_STRENGTH"]
