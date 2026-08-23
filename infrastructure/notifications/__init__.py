"""Единый сервис нотификаций (ТЗ §20).

NotificationService
├── Telegram
└── ntfy

Не отправляет весь лог — только категоризированные события:
INFO / WARNING / CRITICAL / TRADE / RESEARCH.
Каждый канал форматирует под свою среду (у ntfy — markdown, у TG — plain text).
"""
import logging
from datetime import date

from config.settings import (NOTIFY_MAX_PER_DAY, NTFY_SERVER, NTFY_TOPIC,
                             TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
from infrastructure.notifications.format import LEVELS, format_event
from infrastructure.notifications.ntfy import NtfyChannel
from infrastructure.notifications.telegram import TelegramChannel

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, channels: list | None = None, max_per_day: int | None = None):
        self.channels = channels if channels is not None else default_channels()
        self.max_per_day = max_per_day if max_per_day is not None else NOTIFY_MAX_PER_DAY
        self._day = date.today()
        self._count = 0

    def notify(self, level: str, event: str, data: dict | None = None,
               text: str | None = None) -> None:
        if level not in LEVELS:
            logger.warning("unknown notification level: %s", level)
            return
        if self._day != date.today():
            self._day, self._count = date.today(), 0
        if self._count >= self.max_per_day:
            return
        self._dispatch(level, event, data, text)
        self._count += 1
        if self._count == self.max_per_day:
            # последнее сообщение дня — предупреждение вместо молчаливого обрыва
            self._dispatch("WARNING", "NOTIFY_LIMIT", {"limit": self.max_per_day},
                           "Дневной лимит уведомлений исчерпан — тишина до завтра. "
                           "Все события пишутся в журнал.")

    def _dispatch(self, level: str, event: str, data: dict | None, text: str | None) -> None:
        for ch in self.channels:
            try:
                ch.send(level, event, data, text)
            except Exception as e:  # нотификации не должны ронять торговлю
                logger.error("notify via %s failed: %s", ch.__class__.__name__, e)


def default_channels() -> list:
    channels = []
    tg = TelegramChannel(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    ntfy = NtfyChannel(NTFY_TOPIC, NTFY_SERVER)
    if tg.enabled:
        channels.append(tg)
    if ntfy.enabled:
        channels.append(ntfy)
    if not channels:
        logger.warning("no notification channels configured (TELEGRAM_BOT_TOKEN / NTFY_TOPIC)")
    return channels


_service = NotificationService()


def notify(level: str, event: str, data: dict | None = None,
           text: str | None = None) -> None:
    """Отправить событие во все настроенные каналы. text — готовый человеческий текст."""
    _service.notify(level, event, data, text)