"""Структурированная память проекта (ТЗ §22): SQLite.

Единый источник правды о том, какие гипотезы проверялись, что показали
эксперименты и почему гипотезы/стратегии отклонены. LLM-память не заменяет.
"""
import json
import sqlite3
from datetime import datetime, timezone

from config.settings import ROOT

DB_PATH = ROOT / "memory" / "openagent.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hypotheses (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    strategy TEXT NOT NULL,
    description TEXT,
    economic_rationale TEXT,
    expected_failure_modes TEXT,
    status TEXT NOT NULL DEFAULT 'IDEA',
    verdict TEXT,
    last_experiment_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    pipeline TEXT NOT NULL,
    params TEXT NOT NULL,
    result TEXT NOT NULL,
    hypothesis_id TEXT,
    verdict TEXT,
    created_at TEXT NOT NULL,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS strategies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '1.0',
    hypothesis_id TEXT,
    status TEXT NOT NULL DEFAULT 'IDEA',
    params TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


# --- Hypotheses ---

def upsert_hypothesis(conn, h: dict) -> None:
    now = _now()
    conn.execute(
        """INSERT INTO hypotheses (id, name, strategy, description, economic_rationale,
             expected_failure_modes, status, verdict, last_experiment_id, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, strategy=excluded.strategy, description=excluded.description,
             economic_rationale=excluded.economic_rationale,
             expected_failure_modes=excluded.expected_failure_modes,
             status=excluded.status, verdict=excluded.verdict,
             last_experiment_id=excluded.last_experiment_id, updated_at=excluded.updated_at""",
        (h["id"], h["name"], h["strategy"], h.get("description"), h.get("economic_rationale"),
         h.get("expected_failure_modes"), h.get("status", "IDEA"), h.get("verdict"),
         h.get("last_experiment_id"), now, now),
    )
    conn.commit()


def set_hypothesis_verdict(conn, hypothesis_id: str, status: str, verdict: str,
                           experiment_id: str | None = None) -> None:
    conn.execute(
        "UPDATE hypotheses SET status=?, verdict=?, last_experiment_id=COALESCE(?, last_experiment_id), updated_at=? WHERE id=?",
        (status, verdict, experiment_id, _now(), hypothesis_id),
    )
    conn.commit()


def get_hypothesis(conn, hypothesis_id: str) -> dict | None:
    cur = conn.execute("SELECT * FROM hypotheses WHERE id=?", (hypothesis_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def list_hypotheses(conn, status: str | None = None) -> list[dict]:
    q = "SELECT * FROM hypotheses"
    args = ()
    if status:
        q += " WHERE status=?"
        args = (status,)
    cur = conn.execute(q, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# --- Experiments ---

def record_experiment_db(conn, experiment_id: str, pipeline: str, params: dict,
                         result: str, hypothesis_id: str | None = None,
                         verdict: str | None = None, payload: dict | None = None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO experiments (id, pipeline, params, result, hypothesis_id, verdict, created_at, payload) VALUES (?,?,?,?,?,?,?,?)",
        (experiment_id, pipeline, json.dumps(params, ensure_ascii=False, default=str),
         result, hypothesis_id, verdict, _now(),
         json.dumps(payload, ensure_ascii=False, default=str) if payload else None),
    )
    conn.commit()


def recent_experiments(conn, limit: int = 20) -> list[dict]:
    cur = conn.execute("SELECT * FROM experiments ORDER BY created_at DESC LIMIT ?", (limit,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def experiments_for_hypothesis(conn, hypothesis_id: str, limit: int = 50) -> list[dict]:
    """История прогонов гипотезы (ТЗ §22): контекст для Critic."""
    cur = conn.execute(
        "SELECT id, result, created_at FROM experiments WHERE hypothesis_id=? "
        "ORDER BY created_at DESC LIMIT ?", (hypothesis_id, limit))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# --- Strategies ---

def register_strategy(conn, strategy_id: str, name: str, hypothesis_id: str | None = None,
                      params: dict | None = None) -> None:
    now = _now()
    conn.execute(
        "INSERT OR REPLACE INTO strategies (id, name, hypothesis_id, status, params, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (strategy_id, name, hypothesis_id, "IDEA",
         json.dumps(params, ensure_ascii=False, default=str) if params else None, now, now),
    )
    conn.commit()


def get_strategy(conn, strategy_id: str) -> dict | None:
    cur = conn.execute("SELECT * FROM strategies WHERE id=?", (strategy_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def set_strategy_status(conn, strategy_id: str, status: str) -> None:
    conn.execute("UPDATE strategies SET status=?, updated_at=? WHERE id=?",
                 (status, _now(), strategy_id))
    conn.commit()


def list_strategies(conn, status: str | None = None) -> list[dict]:
    q = "SELECT * FROM strategies"
    args = ()
    if status:
        q += " WHERE status=?"
        args = (status,)
    cur = conn.execute(q, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]