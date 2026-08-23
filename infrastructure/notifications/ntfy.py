"""ntfy-канал нотификаций (ТЗ §20): Publish-as-JSON — POST на корень {server}/,
topic внутри тела (иначе ntfy считает JSON обычным текстом сообщения)."""
import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

_PRIORITY = {"INFO": 2, "WARNING": 3, "CRITICAL": 5, "TRADE": 3, "RESEARCH": 2}
_TAGS = {"INFO": "information_source", "WARNING": "warning", "CRITICAL": "rotating_light",
         "TRADE": "chart_with_upwards_trend", "RESEARCH": "microscope"}


def _flat(v) -> str:
    """dict/list → строка без python-repr (фигурные скобки и кавычки не для людей)."""
    if isinstance(v, dict):
        return ", ".join(f"{k}={_flat(x)}" for k, x in v.items())
    if isinstance(v, (list, tuple)):
        return ", ".join(_flat(x) for x in v)
    return f"{v}"


class NtfyChannel:
    def __init__(self, topic: str, server: str = "https://ntfy.sh"):
        self.topic = topic
        self.server = server.rstrip("/")
        self.enabled = bool(topic)

    def send(self, level: str, event: str, data: dict | None = None,
             text: str | None = None) -> None:
        if not self.enabled:
            return
        if text:
            lines = [text]
        else:
            lines = [event]
            if data:
                lines += [f"• {k}: {_flat(v)}" for k, v in data.items()]
        payload = json.dumps({
            "topic": self.topic, "title": event, "message": "\n".join(lines),
            "priority": _PRIORITY.get(level, 3),
            "tags": [_TAGS.get(level, "information_source")],
        }).encode()
        req = urllib.request.Request(f"{self.server}/", data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                logger.error("ntfy: HTTP %s", resp.status)