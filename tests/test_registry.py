"""Контрактные тесты Strategy Registry (ТЗ §14): lifecycle и запрещённые переходы."""
import pytest

from memory import db
from strategies import registry


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Каждый тест — на чистой БД во временном файле."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")


def test_register_and_get():
    registry.register("S1", "Test Strat", hypothesis_id="H3")
    s = registry.get("S1")
    assert s["name"] == "Test Strat"
    assert s["hypothesis_id"] == "H3"
    assert s["status"] == "IDEA"


def test_full_lifecycle():
    registry.register("S2", "Flow")
    for status in ["RESEARCHING", "TESTED", "CANDIDATE", "PAPER", "APPROVED"]:
        registry.transition("S2", status)
    registry.transition("S2", "LIVE", approval="human")
    for status in ["SUSPENDED", "RETIRED"]:
        registry.transition("S2", status)
    assert registry.get("S2")["status"] == "RETIRED"


def test_live_requires_human_approval():
    registry.register("S6", "Approval")
    for status in ["RESEARCHING", "TESTED", "CANDIDATE", "PAPER", "APPROVED"]:
        registry.transition("S6", status)
    with pytest.raises(ValueError):
        registry.transition("S6", "LIVE")  # без approval — запрещено (ТЗ §27)
    registry.transition("S6", "LIVE", approval="operator")
    assert registry.get("S6")["status"] == "LIVE"


def test_illegal_transition_rejected():
    registry.register("S3", "Bad")
    registry.transition("S3", "RESEARCHING")
    with pytest.raises(ValueError):
        registry.transition("S3", "LIVE")  # RESEARCHING -> LIVE: перескок недопустим


def test_failed_is_terminal():
    registry.register("S4", "Term")
    registry.transition("S4", "RESEARCHING")
    registry.transition("S4", "FAILED")
    with pytest.raises(ValueError):
        registry.transition("S4", "PAPER")


def test_transition_unknown_strategy():
    with pytest.raises(ValueError):
        registry.transition("NOPE", "LIVE")


def test_list_filter_by_status():
    registry.register("S5", "List")
    live = registry.list_all(status="IDEA")
    assert any(s["id"] == "S5" for s in live)
    assert registry.list_all(status="LIVE") == []