"""Telegram-канал нотификаций (ТЗ §20). stdlib urllib, без зависимостей."""
import json
import logging
import urllib.request

from infrastructure.notifications.format import format_event

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramChannel:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)

    def send(self, level: str, event: str, data: dict | None = None,
             text: str | None = None) -> None:
        if not self.enabled:
            return
        body = json.dumps({"chat_id": self.chat_id,
                           "text": text or format_event(level, event, data)}).encode()
        req = urllib.request.Request(_BASE_URL.format(token=self.bot_token),
                                     data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                logger.error("Telegram: HTTP %s", resp.status)