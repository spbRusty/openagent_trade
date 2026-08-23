"""Контрактные тесты нотификаций (ТЗ §20): единый сервис, каналы Telegram/ntfy,
отключение без секретов, устойчивость к сбоям канала."""
import json

from infrastructure import notifications
from infrastructure.notifications import ntfy, telegram


def test_format_event():
    text = notifications.format_event("CRITICAL", "RISK_LIMIT",
                                      {"symbol": "BTCUSDT", "reason": "drawdown"})
    assert "[CRITICAL]" in text
    assert "RISK_LIMIT" in text
    assert "symbol=BTCUSDT" in text


def test_unknown_level_ignored():
    svc = notifications.NotificationService(channels=[])
    svc.notify("BOGUS", "X", {})  # не должно упасть и не должно уйти в каналы


def test_daily_budget_stops_and_warns_once():
    calls = []

    class Ch:
        def send(self, level, event, data, text=None):
            calls.append(event)

    svc = notifications.NotificationService(channels=[Ch()], max_per_day=2)
    svc.notify("INFO", "A")
    svc.notify("INFO", "B")
    svc.notify("INFO", "C")  # за лимитом — вместо него предупреждение
    assert calls == ["A", "B", "NOTIFY_LIMIT"]
    svc.notify("INFO", "D")  # после предупреждения — тишина до завтра
    assert calls == ["A", "B", "NOTIFY_LIMIT"]


def test_notify_fans_out_and_tolerates_failures():
    calls = []

    class Ch:
        def __init__(self, fail=False):
            self.fail = fail

        def send(self, level, event, data, text=None):
            calls.append((level, event, data, text))
            if self.fail:
                raise RuntimeError("boom")

    svc = notifications.NotificationService(channels=[Ch(), Ch(fail=True)])
    svc.notify("TRADE", "TRADE_OPEN", {"symbol": "BTCUSDT"})
    assert len(calls) == 2
    assert calls[0][:2] == ("TRADE", "TRADE_OPEN")
    assert calls[0][2] == {"symbol": "BTCUSDT"}
    assert calls[0][3] is None


def test_telegram_disabled_without_token(monkeypatch):
    sent = []
    monkeypatch.setattr(telegram.urllib.request, "urlopen", lambda req, **kw: sent.append(req))
    ch = telegram.TelegramChannel("", "")
    ch.send("INFO", "X", {"a": 1})
    assert sent == []


def test_telegram_send_url_and_body(monkeypatch):
    captured = {}

    def fake_urlopen(req, **kw):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)

        class R:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fake_urlopen)
    ch = telegram.TelegramChannel("TOKEN", "CHAT")
    ch.send("INFO", "HEARTBEAT", {"balance": 100})
    assert captured["url"] == "https://api.telegram.org/botTOKEN/sendMessage"
    assert captured["body"]["chat_id"] == "CHAT"
    assert "HEARTBEAT" in captured["body"]["text"]
    assert "balance=100" in captured["body"]["text"]


def test_ntfy_disabled_without_topic(monkeypatch):
    sent = []
    monkeypatch.setattr(ntfy.urllib.request, "urlopen", lambda req, **kw: sent.append(req))
    ch = ntfy.NtfyChannel("")
    ch.send("INFO", "X", {"a": 1})
    assert sent == []


def test_ntfy_send_url_and_priority(monkeypatch):
    captured = {}

    def fake_urlopen(req, **kw):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)

        class R:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    monkeypatch.setattr(ntfy.urllib.request, "urlopen", fake_urlopen)
    ch = ntfy.NtfyChannel("mytopic")
    ch.send("CRITICAL", "SYSTEM_CRASH", {"reason": "oom"})
    assert captured["url"] == "https://ntfy.sh/"  # Publish-as-JSON — только корень
    assert captured["body"]["topic"] == "mytopic"
    assert captured["body"]["priority"] == 5
    assert captured["body"]["title"] == "SYSTEM_CRASH"
    assert captured["body"]["tags"] == ["rotating_light"]
    assert "reason: oom" in captured["body"]["message"]


def test_ntfy_unicode_title(monkeypatch):
    captured = {}

    def fake_urlopen(req, **kw):
        captured["body"] = json.loads(req.data)

        class R:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    monkeypatch.setattr(ntfy.urllib.request, "urlopen", fake_urlopen)
    ch = ntfy.NtfyChannel("mytopic")
    ch.send("WARNING", "Стена на покупку: BTCUSDT", None, "Стена на покупку у цены 72544.5")
    assert captured["body"]["title"] == "Стена на покупку: BTCUSDT"
    assert "72544.5" in captured["body"]["message"]


def test_ntfy_nested_data_without_repr(monkeypatch):
    captured = {}

    def fake_urlopen(req, **kw):
        captured["body"] = json.loads(req.data)

        class R:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    monkeypatch.setattr(ntfy.urllib.request, "urlopen", fake_urlopen)
    ch = ntfy.NtfyChannel("mytopic")
    ch.send("RESEARCH", "GATES_DONE", {
        "passed": [{"gate": "t_stat", "ok": True}, {"gate": "overlap", "ok": False}],
        "symbols": ["BTCUSDT", "ETHUSDT"],
    })
    msg = captured["body"]["message"]
    assert "{" not in msg and "'" not in msg and '"' not in msg and "[" not in msg
    assert "gate=t_stat, ok=True" in msg
    assert "symbols: BTCUSDT, ETHUSDT" in msg