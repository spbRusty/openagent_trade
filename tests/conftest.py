"""Общая фикстура: тесты не должны стучаться в реальные каналы уведомлений."""
import pytest

from infrastructure import notifications


@pytest.fixture(autouse=True)
def _mute_global_notifier(monkeypatch):
    monkeypatch.setattr(notifications._service, "channels", [])
