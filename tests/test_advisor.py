"""Тесты LLM-советника: парсинг вердикта, белый список, клампинг, персистентность."""
import json

import infrastructure.realtime.trader as T
from infrastructure.realtime.advisor import apply_verdict, extract_verdict


def test_extract_clean_json():
    v = extract_verdict('{"summary": "ок", "changes": []}')
    assert v["summary"] == "ok" or v["summary"] == "ок"


def test_extract_json_from_prose_and_fence():
    text = 'Вот мой вердикт:\n```json\n{"summary":"s","changes":[{"param":"gate_wall","to":9.0,"reason":"r"}]}\n```\nСпасибо.'
    v = extract_verdict(text)
    assert v and v["changes"][0]["param"] == "gate_wall"


def test_extract_garbage_returns_none():
    assert extract_verdict("никакого json тут нет") is None
    assert extract_verdict("{битый") is None


def _fresh_base(tmp_path):
    p = str(tmp_path / "at.json")
    from infrastructure.realtime.autotune import _base_bounds, _save
    from pathlib import Path
    _save(Path(p), {"base": _base_bounds()})
    return p


def test_apply_whitelist_and_clamp(tmp_path):
    saved_pct = T.MIN_MOMENTUM_PCT
    saved_gates = dict(T.ENTRY_MIN_STRENGTH)
    try:
        path = _fresh_base(tmp_path)
        verdict = {"summary": "тест", "changes": [
            {"param": "gate_wall", "to": 999.0, "reason": "наглость"},      # кламп вверх
            {"param": "hack_me", "to": 1.0, "reason": "не в списке"},       # отброс
            {"param": "min_momentum", "to": "мусор", "reason": "типы"},     # отброс
            {"param": "gate_imbalance", "to": 0.8, "reason": "чуть ниже"},  # ок
        ]}
        applied, summary = apply_verdict(verdict, path)
        assert len(applied) == 2
        base = json.load(open(path))["base"]
        hi = base["ENTRY_MIN_STRENGTH"]["wall"] * 1.35
        assert T.ENTRY_MIN_STRENGTH["wall"] == round(hi, 3)          # клампнут до границы
        assert T.ENTRY_MIN_STRENGTH["imbalance"] == 0.8
        assert any("[кламп]" in a for a in applied)                  # кламп виден
        log = json.load(open(path))["changes_log"]
        assert len(log) == 2                         # применённое в персистентном логе
    finally:
        T.MIN_MOMENTUM_PCT = saved_pct
        T.ENTRY_MIN_STRENGTH.clear()
        T.ENTRY_MIN_STRENGTH.update(saved_gates)


def test_apply_noop_when_same_value(tmp_path):
    path = _fresh_base(tmp_path)
    cur = T.ENTRY_MIN_STRENGTH["wall"]
    applied, _ = apply_verdict(
        {"summary": "", "changes": [{"param": "gate_wall", "to": cur, "reason": ""}]},
        path)
    assert applied == []


def test_extract_verdict_after_tool_noise():
    """Реальный кейс: логи инструментов и ANSI перед финальным JSON."""
    noise = ("INFO | run:43 - старт\n\x1b[0m\n"
             "d = min(self.risk.max_position_usd, self.ex.get_balance().cash)\n"
             '{"some": "tool output dict"}\n'
             '{"summary": "Пороги ок", "changes": [{"param": "min_momentum", '
             '"to": 0.00065, "reason": "зомби"}]}\n')
    v = extract_verdict(noise)
    assert v and v["changes"][0]["to"] == 0.00065
