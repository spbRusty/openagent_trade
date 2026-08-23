"""Контрактные тесты BybitAdapter (ТЗ §18): подпись HMAC, маппинг ответов,
retry транзиентных ошибок, отсутствие ключей не даёт торговать."""
import hashlib
import hmac
import json

import pytest

from exchanges.bybit import BybitAdapter, BybitError, InstrumentInfo


def _fake_ok(payload: dict):
    def fake(req, **kw):
        class R:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"retCode": 0, "result": payload}).encode()

        return R()

    return fake


def _capture_and_ok(payload: dict, captured: dict):
    def fake(req, **kw):
        captured["url"] = req.full_url
        captured["headers"] = {k: v for k, v in req.headers.items()}
        captured["data"] = req.data.decode() if req.data else None
        class R:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"retCode": 0, "result": payload}).encode()

        return R()

    return fake


def test_public_klines_without_keys(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _fake_ok({"list": [{"open": "100"}]}))
    a = BybitAdapter("", "")
    lst = a.klines("BTCUSDT")
    assert lst[0]["open"] == "100"


def test_place_order_signed_and_mapped(monkeypatch):
    captured = {}
    monkeypatch.setattr("urllib.request.urlopen",
                        _capture_and_ok({"orderId": "O1"}, captured))
    a = BybitAdapter("KEY", "SECRET")
    fill = a.place_order("BTCUSDT", "BUY", 0.01)
    assert fill.order_id == "O1"
    assert fill.symbol == "BTCUSDT"
    assert captured["url"].startswith("https://api.bybit.com/v5/order/create")
    body = json.loads(captured["data"])
    assert body["symbol"] == "BTCUSDT"
    assert body["side"] == "BUY"
    assert body["orderType"] == "Market"
    h = {k.lower(): v for k, v in captured["headers"].items()}
    assert h["x-bapi-api-key"] == "KEY"
    ts, rw = h["x-bapi-timestamp"], h["x-bapi-recv-window"]
    expected = hmac.new(b"SECRET", f"{ts}KEY{rw}{captured['data']}".encode(),
                        hashlib.sha256).hexdigest()
    assert h["x-bapi-sign"] == expected


def test_get_order_status_mapping(monkeypatch):
    payload = {"list": [{"orderId": "O1", "symbol": "BTCUSDT", "side": "Buy",
                         "qty": "0.01", "price": "100", "orderStatus": "Filled"}]}
    monkeypatch.setattr("urllib.request.urlopen", _fake_ok(payload))
    a = BybitAdapter("KEY", "SECRET")
    o = a.get_order("O1")
    assert o.status == "filled"
    assert o.qty == 0.01


def test_get_order_missing_returns_none(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _fake_ok({"list": []}))
    a = BybitAdapter("KEY", "SECRET")
    assert a.get_order("NOPE") is None


def test_cancel_not_found_returns_false(monkeypatch):
    def fake(req, **kw):
        class R:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"retCode": 20001, "retMsg": "order not exists"}).encode()

        return R()

    monkeypatch.setattr("urllib.request.urlopen", fake)
    a = BybitAdapter("KEY", "SECRET")
    assert a.cancel_order("NOPE", "BTCUSDT") is False


def test_get_position_zero_size_none(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _fake_ok({"list": [{"size": "0"}]}))
    a = BybitAdapter("KEY", "SECRET")
    assert a.get_position("BTCUSDT") is None


def test_get_position_long_and_short(monkeypatch):
    def fake(payload):
        return _fake_ok(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake({"list": [
        {"symbol": "BTCUSDT", "side": "Buy", "size": "0.1", "avgPrice": "100"}]}))
    a = BybitAdapter("KEY", "SECRET")
    pos = a.get_position("BTCUSDT")
    assert pos.qty == 0.1
    assert pos.entry_price == 100

    monkeypatch.setattr("urllib.request.urlopen", fake({"list": [
        {"symbol": "BTCUSDT", "side": "Sell", "size": "0.2", "avgPrice": "90"}]}))
    assert a.get_position("BTCUSDT").qty == -0.2


def test_get_balance(monkeypatch):
    payload = {"list": [{"totalEquity": "1234.5",
                         "coin": [{"coin": "USDT", "walletBalance": "1000"}]}]}
    monkeypatch.setattr("urllib.request.urlopen", _fake_ok(payload))
    a = BybitAdapter("KEY", "SECRET")
    assert a.get_balance().cash == 1234.5


def test_trading_without_keys_raises():
    a = BybitAdapter("", "")
    with pytest.raises(BybitError) as ei:
        a.place_order("BTCUSDT", "BUY", 0.01)
    assert ei.value.category == "SECURITY"


def test_transient_retry_then_success(monkeypatch):
    calls = []

    def fake(req, **kw):
        calls.append(req)
        class R:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                if len(calls) == 1:
                    return json.dumps({"retCode": 10004, "retMsg": "rate limit"}).encode()
                return json.dumps({"retCode": 0, "result": {"list": [{"size": "0"}]}}).encode()

        return R()

    monkeypatch.setattr("urllib.request.urlopen", fake)
    a = BybitAdapter("KEY", "SECRET")
    a.get_position("BTCUSDT")  # транзиент → retry → успех
    assert len(calls) == 2


def test_transient_exhausted_raises(monkeypatch):
    def fake(req, **kw):
        class R:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"retCode": 10006, "retMsg": "timeout"}).encode()

        return R()

    monkeypatch.setattr("urllib.request.urlopen", fake)
    a = BybitAdapter("KEY", "SECRET")
    with pytest.raises(BybitError) as ei:
        a.get_position("BTCUSDT")
    assert ei.value.category == "TRANSIENT"


def test_instrument_info_rounding():
    raw = {"priceFilter": {"tickSize": "0.5"},
           "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001"}}
    info = InstrumentInfo(raw)
    assert info.round_price(100.3) == 100.5
    assert info.round_qty(0.0101) == 0.011
    assert info.minimal_qty_for_amount(5.0, 100.0) == 0.05