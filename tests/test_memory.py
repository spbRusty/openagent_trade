"""Контрактные тесты memory (ТЗ §22): запись/чтение экспериментов и гипотез."""
import json

from memory import db


def test_record_experiment_and_read_back(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with db.connect() as conn:
        db.record_experiment_db(conn, "exp1", "hypotheses", {"W": 5, "H": 10},
                                "NO_CANDIDATE", hypothesis_id="H1", payload={"best": "H3"})
    with db.connect() as conn:
        exps = db.recent_experiments(conn)
    assert len(exps) == 1
    assert exps[0]["id"] == "exp1"
    assert exps[0]["hypothesis_id"] == "H1"
    assert json.loads(exps[0]["params"]) == {"W": 5, "H": 10}


def test_hypothesis_upsert_and_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with db.connect() as conn:
        db.upsert_hypothesis(conn, {"id": "H1", "name": "N", "strategy": "momentum",
                                    "description": "d", "economic_rationale": "r",
                                    "expected_failure_modes": "f"})
        db.set_hypothesis_verdict(conn, "H1", "FAILED", "max overlap t=1.70", "exp9")
    with db.connect() as conn:
        h = db.get_hypothesis(conn, "H1")
    assert h["status"] == "FAILED"
    assert h["verdict"] == "max overlap t=1.70"
    assert h["last_experiment_id"] == "exp9"


def test_list_hypotheses_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with db.connect() as conn:
        db.upsert_hypothesis(conn, {"id": "H1", "name": "A", "strategy": "momentum"})
        db.upsert_hypothesis(conn, {"id": "H2", "name": "B", "strategy": "reversion"})
        db.set_hypothesis_verdict(conn, "H2", "FAILED", "t=0.5")
    with db.connect() as conn:
        failed = db.list_hypotheses(conn, status="FAILED")
    assert [h["id"] for h in failed] == ["H2"]


def test_upsert_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with db.connect() as conn:
        db.upsert_hypothesis(conn, {"id": "H1", "name": "A", "strategy": "momentum"})
        db.upsert_hypothesis(conn, {"id": "H1", "name": "A", "strategy": "momentum"})
    with db.connect() as conn:
        assert len(db.list_hypotheses(conn)) == 1