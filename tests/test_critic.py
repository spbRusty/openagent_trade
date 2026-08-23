"""Контрактные тесты Critic (ТЗ §4.4, §13): детерминированные вердикты."""
from research.critic import review


def base_result(**kw):
    r = {"overlap_t": 2.5, "overlap_ev": 0.003, "n": 12000, "n_months": 30,
         "pos_months": 22, "bootstrap_p": 0.03, "n_trials": 12,
         "gate_passed": True, "gate_fail_reason": None, "oos_t": 2.1,
         "costs": {"taker_0.15%": {"ev": 0.002}, "maker_0.08%": {"ev": 0.003}}}
    r.update(kw)
    return r


def test_clean_result_proceeds():
    v = review(base_result())
    assert v.verdict == "PROCEED"
    assert v.confidence == 0.8
    assert v.identified_risks == []


def test_gate_fail_rejects():
    v = review(base_result(gate_passed=False, gate_fail_reason="oos_t<2"))
    assert v.verdict == "REJECT"
    assert any("gate FAIL" in r for r in v.identified_risks)


def test_bootstrap_above_005_needs_tests():
    v = review(base_result(bootstrap_p=0.12))
    assert v.verdict == "NEED_TESTS"
    assert any("bootstrap" in r for r in v.identified_risks)


def test_significance_misinterpretation_flagged():
    v = review(base_result(bootstrap_p=0.12, overlap_t=2.5))
    assert any("ошибочная интерпретация" in r for r in v.identified_risks)


def test_regime_dependence_flagged():
    v = review(base_result(pos_months=12, n_months=30))
    assert any("regime dependence" in r for r in v.identified_risks)


def test_hidden_costs_flagged():
    v = review(base_result(costs={"taker_0.15%": {"ev": -0.001}}))
    assert any("hidden costs" in r for r in v.identified_risks)
    assert v.verdict == "NEED_TESTS"


def test_multiple_testing_flagged_without_oos():
    v = review(base_result(oos_t=None))
    assert any("multiple testing" in r for r in v.identified_risks)
    assert any("selection bias" in a for a in v.alternative_explanations)


def test_multiple_testing_residual_with_oos():
    v = review(base_result(oos_t=2.1))
    assert not any("multiple testing" in r for r in v.identified_risks)
    assert any("selection bias" in a for a in v.alternative_explanations)


def test_short_history_flagged():
    v = review(base_result(n_months=10))
    assert any("короткая история" in r for r in v.identified_risks)


def test_confidence_drops_with_risks():
    v = review(base_result(bootstrap_p=0.12))
    assert v.confidence < 0.8
    assert v.verdict == "NEED_TESTS"


def test_to_dict_roundtrip():
    d = review(base_result()).to_dict()
    assert set(d) == {"verdict", "confidence", "identified_risks",
                      "alternative_explanations", "required_tests", "recommendation"}