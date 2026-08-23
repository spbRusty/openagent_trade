"""Critic (ТЗ §4.4, §13): атакует объяснение результата, а не результат.

Gates проверяют результат. Critic ищет: multiple testing, selection bias,
regime dependence, hidden costs, нестабильность, ошибочную интерпретацию
значимости, альтернативные объяснения.

Детерминированный (gates/статистика не зависят от LLM, ТЗ §11). Запускается
после прогона, результат статистических тестов не меняет.

Вход: result (dict) — диагностика эксперимента.
Выход: CriticVerdict (verdict, confidence, identified_risks,
       alternative_explanations, required_tests, recommendation).
"""
from dataclasses import asdict, dataclass

MIN_MONTHS = 24
MIN_POS_MONTHS_RATIO = 0.6


@dataclass
class CriticVerdict:
    verdict: str                       # PROCEED / NEED_TESTS / REJECT
    confidence: float                  # 0..1
    identified_risks: list
    alternative_explanations: list
    required_tests: list
    recommendation: str

    def to_dict(self) -> dict:
        return asdict(self)


def review(result: dict) -> CriticVerdict:
    risks, alternatives, required = [], [], []

    gate_ok = result.get("gate_passed", False)
    if not gate_ok:
        risks.append(f"gate FAIL: {result.get('gate_fail_reason', '?')}")

    n_trials = result.get("n_trials", 1)
    if n_trials > 1:
        alternatives.append("edge может быть артефактом выбора лучшей комбинации (selection bias)")
        if result.get("oos_t") is None:
            # независимого подтверждения ещё не было — это блокирующий риск
            risks.append(f"multiple testing: best выбран из {n_trials} комбинаций (W,H) без OOS")
            required.append("подтверждение на независимых данных (OOS)")
        else:
            required.append("мониторинг стабильности edge в live/paper")

    n_months = result.get("n_months", 0)
    pos_months = result.get("pos_months", 0)
    if n_months < MIN_MONTHS:
        risks.append(f"короткая история: {n_months} месяцев")
        required.append("продлить историю до >=24 месяцев")
    elif n_months and pos_months / n_months < MIN_POS_MONTHS_RATIO:
        risks.append(f"regime dependence: плюс только в {pos_months}/{n_months} месяцев")
        alternatives.append("прибыль концентрирована в одном рыночном режиме")
        required.append("проверка по режимам (тренд/флэт, волатильность)")

    bp = result.get("bootstrap_p")
    if bp is not None and bp > 0.05:
        risks.append(f"block bootstrap не подтверждает значимость (p={bp:.3f})")
        required.append("увеличить выборку или пересмотреть сигнал")
    if bp is not None and bp > 0.05 and result.get("overlap_t", 0) >= 2:
        risks.append("ошибочная интерпретация значимости: overlap-t >=2 при bootstrap p>0.05")

    taker_ev = result.get("costs", {}).get("taker_0.15%", {}).get("ev", 0)
    if taker_ev < 0:
        risks.append("hidden costs: при taker-издержках edge исчезает")
        required.append("оценка реальных издержек исполнения")

    if not gate_ok:
        verdict, rec = "REJECT", f"отклонено gates: {result.get('gate_fail_reason', '?')}"
    elif risks:
        verdict, rec = "NEED_TESTS", "требуются дополнительные тесты: " + "; ".join(required[:2]) or "нет"
    else:
        verdict, rec = "PROCEED", "кандидат готов к paper trading"

    n = result.get("n", 0)
    confidence = 0.2
    if n >= 1000 and n_months >= 12:
        confidence = 0.5
    if n >= 10000 and n_months >= MIN_MONTHS and bp is not None and bp <= 0.05 and not risks:
        confidence = 0.8

    return CriticVerdict(verdict, confidence, risks, alternatives, required, rec)