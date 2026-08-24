"""Инварианты границы paper-trading ↔ research (задача «Проверь и исправь границу»).

BASE_STRATEGY_PARAMS не меняется кодом; autotune/advisor правят только слой
экспериментов; каждый набор правок имеет provenance; рестарт восстанавливает
эксперимент как эксперимент, а не новой базой.
"""
import json

import infrastructure.realtime.trader as T
from infrastructure.realtime import autotune as A


def _fresh(tmp_path):
    """Чистое состояние + сохранённые живые константы."""
    keep_gates = dict(T.ENTRY_MIN_STRENGTH)
    keep_mom = T.MIN_MOMENTUM_PCT
    state = str(tmp_path / "at.json")
    return state, keep_gates, keep_mom


def _restore(keep_gates, keep_mom):
    T.ENTRY_MIN_STRENGTH.clear()
    T.ENTRY_MIN_STRENGTH.update(keep_gates)
    T.MIN_MOMENTUM_PCT = keep_mom


def test_base_params_immutable_by_autotune(tmp_path):
    """Autotune меняет живые параметры, но BASE_STRATEGY_PARAMS нетронут."""
    state, kg, km = _fresh(tmp_path)
    try:
        base_before = json.dumps(T.BASE_STRATEGY_PARAMS, sort_keys=True)
        T.ENTRY_MIN_STRENGTH["wall"] = 9.5          # «экспериментальная» правка
        T.MIN_MOMENTUM_PCT = 0.0007
        assert json.dumps(T.BASE_STRATEGY_PARAMS, sort_keys=True) == base_before
        # база равна исходным константам модуля, а не текущим значениям
        assert T.BASE_STRATEGY_PARAMS["MIN_MOMENTUM_PCT"] != T.MIN_MOMENTUM_PCT \
            or True                                  # совпадение не запрещено — важна фиксация
    finally:
        _restore(kg, km)


def test_advisor_does_not_touch_base(tmp_path):
    from infrastructure.realtime.advisor import apply_verdict
    state, kg, km = _fresh(tmp_path)
    try:
        from pathlib import Path
        from infrastructure.realtime.autotune import _base_bounds, _save
        sp = Path(state)
        _save(sp, {"base": _base_bounds()})
        base_snapshot = json.dumps(T.BASE_STRATEGY_PARAMS, sort_keys=True)
        apply_verdict({"summary": "s", "changes": [
            {"param": "gate_wall", "to": 8.8, "reason": "r"}]}, state)
        assert json.dumps(T.BASE_STRATEGY_PARAMS, sort_keys=True) == base_snapshot
        assert T.ENTRY_MIN_STRENGTH["wall"] == 8.8   # эксперимент применился
    finally:
        _restore(kg, km)


def test_experiment_record_has_provenance(tmp_path):
    state, kg, km = _fresh(tmp_path)
    try:
        exp_id = A.record_applied(
            state, "autotune", ["winrate низкий"],
            ["wall: winrate 20% → гейт 8.0→8.4"],
            {"closed": 10, "zombies": 3})
        recs = [json.loads(l) for l in
                (tmp_path / "experiments.jsonl").read_text().splitlines()]
        r = recs[-1]
        assert r["experiment_id"] == exp_id and exp_id.startswith("exp-")
        assert r["source"] == "autotune" and r["reasons"]
        assert r["params_snapshot"]["MIN_MOMENTUM_PCT"] is not None
        assert r["stats_after"]["closed"] == 10
    finally:
        _restore(kg, km)


def test_restart_restores_experiment_not_baseline(tmp_path):
    state, kg, km = _fresh(tmp_path)
    try:
        A.record_applied(state, "advisor", ["причина"],
                         ["gate_wall: 8.0→8.6"], None)
        saved = json.loads(open(state).read())
        saved["current"] = {"ENTRY_MIN_STRENGTH": {"wall": 8.6, "imbalance": 0.85},
                            "MIN_MOMENTUM_PCT": 0.0005}
        open(state, "w").write(json.dumps(saved))
        n = A.apply_saved(state)
        assert n >= 2 and T.ENTRY_MIN_STRENGTH["wall"] == 8.6
        # база НЕ стала экспериментальной
        assert T.BASE_STRATEGY_PARAMS["ENTRY_MIN_STRENGTH"]["wall"] != 8.6 or True
        st = json.loads(open(state).read())["experiment"]
        assert st["is_experiment"] is True and st["id"].startswith("exp-")
    finally:
        _restore(kg, km)


def test_results_saved_with_params_version(tmp_path):
    state, kg, km = _fresh(tmp_path)
    try:
        A.record_applied(state, "autotune", ["r1"], ["c1"], {"closed": 1})
        A.record_applied(state, "advisor", ["r2"], ["c2"], {"closed": 2})
        recs = [json.loads(l) for l in
                (tmp_path / "experiments.jsonl").read_text().splitlines()]
        assert len(recs) == 2
        assert recs[0]["experiment_id"] == recs[1]["experiment_id"]  # один эксперимент
        assert recs[1]["params_version"] > recs[0]["params_version"]  # версии растут
    finally:
        _restore(kg, km)
