"""Общее форматирование нотификаций (ТЗ §20)."""
LEVELS = ("INFO", "WARNING", "CRITICAL", "TRADE", "RESEARCH")
EMOJI = {"INFO": "ℹ️", "WARNING": "⚠️", "CRITICAL": "🔴", "TRADE": "💱", "RESEARCH": "🔬"}


def format_event(level: str, event: str, data: dict | None) -> str:
    """Плоский текст для Telegram."""
    lines = [f"{EMOJI.get(level, '')} [{level}] {event}"]
    if data:
        lines.append("  " + ", ".join(f"{k}={v}" for k, v in data.items()))
    return "\n".join(lines)