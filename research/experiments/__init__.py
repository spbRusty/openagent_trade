"""Реестр экспериментов (ТЗ §22, §24).

Каждый запуск пайплайна:
1) пишет JSON-артефакт в research/experiments/ (неизменяемый след),
2) пишет запись в memory SQLite (структурированная память, доступная AI).
"""
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from config.settings import EXPERIMENTS_DIR, ROOT
from memory import db


def make_experiment_id(pipeline: str, params: dict) -> str:
    h = hashlib.sha1(f"{pipeline}|{json.dumps(params, sort_keys=True)}".encode()).hexdigest()[:10]
    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{h}"


def _code_version() -> str:
    """Короткий git-rev для воспроизводимости (ТЗ §24); без git — 'dev'."""
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5,
                             cwd=ROOT).stdout.strip()
        return rev or "dev"
    except Exception:
        return "dev"


def record_experiment(experiment_id: str, payload: dict) -> Path:
    """Сохранить эксперимент: JSON-артефакт + запись в memory. Возвращает путь JSON."""
    record = {
        "experiment_id": experiment_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "code_version": _code_version(),
        **payload,
    }
    path = EXPERIMENTS_DIR / f"{experiment_id}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str))

    with db.connect() as conn:
        db.record_experiment_db(
            conn,
            experiment_id=experiment_id,
            pipeline=payload.get("pipeline", "unknown"),
            params=payload.get("params", {}),
            result=payload.get("result", "UNKNOWN"),
            hypothesis_id=payload.get("hypothesis_id"),
            verdict=payload.get("verdict"),
            payload=payload,
        )
    return path