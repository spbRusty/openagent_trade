"""Strategy Registry (ТЗ §14): lifecycle стратегий, бэкенд — memory SQLite.

Lifecycle:
IDEA → RESEARCHING → TESTED → FAILED
                         ↓
                     CANDIDATE → PAPER → APPROVED → LIVE → SUSPENDED → RETIRED

Для каждой стратегии хранятся: id, версия, гипотеза, статус, параметры,
история результатов (в memory/experiments).
"""
from memory import db

# допустимые переходы: текущий статус -> множество следующих
LIFECYCLE = {
    "IDEA": {"RESEARCHING"},
    "RESEARCHING": {"TESTED", "FAILED"},
    "TESTED": {"CANDIDATE", "FAILED"},
    "FAILED": set(),
    "CANDIDATE": {"PAPER", "FAILED"},
    "PAPER": {"APPROVED", "SUSPENDED", "FAILED"},
    "APPROVED": {"LIVE", "SUSPENDED"},
    "LIVE": {"SUSPENDED", "RETIRED"},
    "SUSPENDED": {"LIVE", "RETIRED", "PAPER"},
    "RETIRED": set(),
}


def register(strategy_id: str, name: str, hypothesis_id: str | None = None,
             params: dict | None = None) -> None:
    """Создать стратегию в статусе IDEA."""
    with db.connect() as conn:
        db.register_strategy(conn, strategy_id, name, hypothesis_id, params)


def transition(strategy_id: str, new_status: str, approval: str | None = None) -> None:
    """Перевести стратегию в новый статус. Незаконный переход -> ValueError.

    Переход в LIVE требует явного человеческого подтверждения (ТЗ §27:
    AI не должен самостоятельно включать стратегию в live).
    """
    with db.connect() as conn:
        s = db.get_strategy(conn, strategy_id)
        if s is None:
            raise ValueError(f"Стратегия {strategy_id} не зарегистрирована")
        allowed = LIFECYCLE.get(s["status"], set())
        if new_status not in allowed:
            raise ValueError(f"Незаконный переход {s['status']} -> {new_status} "
                             f"(допустимо: {sorted(allowed) or 'терминальное состояние'})")
        if new_status == "LIVE" and not approval:
            raise ValueError("Переход в LIVE требует человеческого подтверждения "
                             "(approval) — ТЗ §27")
        db.set_strategy_status(conn, strategy_id, new_status)


def get(strategy_id: str) -> dict | None:
    with db.connect() as conn:
        return db.get_strategy(conn, strategy_id)


def list_all(status: str | None = None) -> list[dict]:
    with db.connect() as conn:
        return db.list_strategies(conn, status)