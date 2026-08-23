"""Jobs для scheduler (ТЗ §21).

Каждый job делает одно дело и шлёт нотификацию. Запуск через cron:
    .venv/bin/python -m infrastructure.scheduler.jobs --job <имя>
"""
import argparse
import json
from datetime import datetime, timezone

from config.settings import DATA_1M_DIR, DATA_METADATA, DATA_NORMALIZED, ROOT
from infrastructure.notifications import notify
from memory import db

JOBS: dict = {}


def _job(fn):
    JOBS[fn.__name__] = fn
    return fn


@_job
def data_update():
    from data.normalize import run_pipeline
    out = run_pipeline(DATA_1M_DIR, DATA_NORMALIZED / "1m", DATA_METADATA)
    m = json.loads(out.read_text())
    notify("INFO", "DATA_UPDATE", {"ok": len(m["symbols"]), "failed": len(m["failed"])})


@_job
def daily_research():
    from research.hypotheses import run
    run.main()  # пайплайн сам шлёт RESEARCH EXPERIMENT_DONE


@_job
def paper_monitor():
    path = ROOT / "data" / "paper" / "account.json"
    if not path.exists():
        notify("INFO", "PAPER_MONITOR", {"status": "no paper account yet"})
        return
    acc = json.loads(path.read_text())
    notify("TRADE", "PAPER_MONITOR", {
        "balance": round(acc["cash"], 2),
        "open_positions": len(acc["positions"]),
        "trades": len(acc["trades"]),
    })


@_job
def health_check():
    manifest = DATA_METADATA / "1m_manifest.json"
    if not manifest.exists():
        notify("CRITICAL", "HEALTH", {"status": "manifest missing"})
        return
    m = json.loads(manifest.read_text())
    age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(m["generated_at"])).total_seconds() / 86400
    with db.connect() as conn:
        exps = len(db.recent_experiments(conn))
        hyps = len(db.list_hypotheses(conn))
    notify("WARNING" if age_days > 7 else "INFO", "HEALTH",
           {"manifest_age_days": round(age_days, 1), "experiments": exps, "hypotheses": hyps})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True, choices=sorted(JOBS))
    args = ap.parse_args()
    JOBS[args.job]()


if __name__ == "__main__":
    main()