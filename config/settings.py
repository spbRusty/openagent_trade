"""Пути, окружение, секреты. Единая точка входа конфигурации проекта.

Секреты — только из .env (не из кода). Пути к большим внешним данным — через
.env с разумным дефолтом.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env(path: Path):
    """Минимальный загрузчик .env (KEY=VALUE), без внешних зависимостей."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


_load_env(ROOT / ".env")

# --- Данные (ТЗ §7: raw/normalized/validated/features/metadata) ---
DATA_ROOT = ROOT / "data"
DATA_RAW = DATA_ROOT / "raw"
DATA_4H = DATA_RAW / "4h"
DATA_NORMALIZED = DATA_ROOT / "normalized"
DATA_VALIDATED = DATA_ROOT / "validated"
DATA_FEATURES = DATA_ROOT / "features"
DATA_METADATA = DATA_ROOT / "metadata"
# 1m klines (~640 МБ) лежат вне проекта, путь настраивается через .env
DATA_1M_DIR = Path(os.environ.get(
    "DATA_1M_DIR", str(Path.home() / "Документы/байбит/bybit_2.2/data/klines/linear")))

# --- Выходы ---
REPORTS_DIR = ROOT / "research" / "reports"
EXPERIMENTS_DIR = ROOT / "research" / "experiments"
LOGS_DIR = ROOT / "logs"

# --- Секреты (из .env) ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# --- Уведомления ntfy (ТЗ §20) ---
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
# Дневной бюджет отправок (лимиты бесплатного ntfy.sh), после исчерпания — тишина до завтра
NOTIFY_MAX_PER_DAY = int(os.environ.get("NOTIFY_MAX_PER_DAY", "120"))

# --- Bybit (ТЗ §18, §26) ---
BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "")

for _d in (DATA_4H, DATA_NORMALIZED, DATA_VALIDATED, DATA_FEATURES, DATA_METADATA,
           REPORTS_DIR, EXPERIMENTS_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)