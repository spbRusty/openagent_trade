"""Снимок исследовательского контура для панели: проверено / отсеяно / планируется."""
from pathlib import Path

import yaml

from memory.db import connect

PLANNED_PATH = Path(__file__).resolve().parents[2] / "research" / "hypotheses" / "planned.yaml"


def snapshot() -> dict:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, name, status, verdict, updated_at FROM hypotheses ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    checked = [{"id": r[0], "name": r[1], "status": r[2],
                "verdict": r[3] or "", "updated": (r[4] or "")[:10]} for r in rows]
    failed = [c for c in checked if c["status"] == "FAILED"]
    planned = []
    if PLANNED_PATH.exists():
        planned = yaml.safe_load(PLANNED_PATH.read_text()) or []
    return {"checked": checked, "failed": len(failed),
            "alive": len(checked) - len(failed), "planned": planned}
