"""Bybit V5 adapter (ТЗ §18): контракт исполнения + публичные данные.

Стратегия не знает специфику Bybit — работает через ExecutionInterface.
Подпись запросов HMAC-SHA256 (X-BAPI-*), секреты из .env. stdlib urllib.
Публичные методы (klines/instruments) работают без ключей.
"""
import hashlib
import hmac
import json
import logging
import math
import time
import urllib.error
import urllib.parse
import urllib.request

from config.settings import BYBIT_API_KEY, BYBIT_API_SECRET
from trading.execution.interface import AccountState, ExecutionInterface, Fill, Order, Position

logger = logging.getLogger(__name__)

_BASE = "https://api.bybit.com"
_RECV_WINDOW = "5000"
_TRANSIENT_RETCODES = {10004, 10006}   # rate limit, request timeout
_RETRIES = 2
_TIMEOUT = 10
_SIGN_TYPE = "2"


class BybitError(Exception):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


class BybitAdapter(ExecutionInterface):
    def __init__(self, api_key: str = BYBIT_API_KEY, api_secret: str = BYBIT_API_SECRET,
                 testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.enabled = bool(api_key and api_secret)
        self.base = "https://api-testnet.bybit.com" if testnet else _BASE

    # --- публичные данные (без ключей) ---
    def klines(self, symbol: str, interval: str = "1", limit: int = 500, start: int | None = None) -> list:
        params = {"category": "linear", "symbol": symbol, "interval": interval,
                  "limit": limit}
        if start:
            params["start"] = start
        return self._get("/v5/market/kline", params)["list"]

    def instruments(self, symbol: str | None = None) -> list:
        params = {"category": "linear"}
        if symbol:
            params["symbol"] = symbol
        return self._get("/v5/market/instruments-info", params)["list"]

    # --- контракт исполнения ---
    def place_order(self, symbol: str, side: str, qty: float, price: float | None = None) -> Fill:
        self._require_keys()
        body = {"category": "linear", "symbol": symbol, "side": side,
                "orderType": "Market" if price is None else "Limit",
                "qty": str(qty), "orderLinkId": f"oa-{int(time.time() * 1000)}"}
        if price is not None:
            body["price"] = str(price)
        r = self._post("/v5/order/create", body)
        # фактическая цена исполнения — через get_order (avgPrice)
        return Fill(r.get("orderId", ""), symbol, side, price or 0.0, qty, 0.0, 0)

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        self._require_keys()
        try:
            self._post("/v5/order/cancel", {"category": "linear", "symbol": symbol,
                                            "orderId": order_id})
            return True
        except BybitError as e:
            if e.category == "EXECUTION" and "20001" in str(e):
                return False  # ордера уже нет
            raise

    def get_order(self, order_id: str) -> Order | None:
        self._require_keys()
        r = self._get("/v5/order/realtime", {"category": "linear", "orderId": order_id})
        lst = r.get("list", [])
        if not lst:
            return None
        o = lst[0]
        status = {"Created": "new", "New": "new", "PartiallyFilled": "partial",
                  "Filled": "filled", "Cancelled": "cancelled",
                  "Rejected": "rejected"}.get(o.get("orderStatus"), o.get("orderStatus", ""))
        return Order(o.get("orderId", order_id), o.get("symbol", ""), o.get("side", ""),
                     float(o.get("qty", 0) or 0), float(o.get("price") or 0), "market", status)

    def get_position(self, symbol: str) -> Position | None:
        self._require_keys()
        r = self._get("/v5/position/list", {"category": "linear", "symbol": symbol})
        lst = r.get("list", [])
        if not lst or float(lst[0].get("size", 0) or 0) == 0:
            return None
        p = lst[0]
        qty = float(p["size"]) * (1 if p.get("side") == "Buy" else -1)
        return Position(symbol, qty, float(p.get("avgPrice", 0) or 0))

    def get_balance(self) -> AccountState:
        self._require_keys()
        r = self._get("/v5/wallet/balance", {"accountType": "UNIFIED"})
        acct = r.get("list", [{}])[0]
        equity = float(acct.get("totalEquity", 0) or 0)
        return AccountState(cash=equity, positions={})

    # --- HTTP + подпись ---
    def _get(self, path: str, params: dict) -> dict:
        qs = urllib.parse.urlencode(params)
        url = f"{self.base}{path}?{qs}"
        return self._request(url, qs=qs)

    def _post(self, path: str, body: dict) -> dict:
        return self._request(f"{self.base}{path}", body=body)

    def _request(self, url: str, qs: str = "", body: dict | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        data = None
        if self.enabled:
            ts = str(int(time.time() * 1000))
            sign_str = ts + self.api_key + _RECV_WINDOW + (qs if body is None else json.dumps(body))
            sig = hmac.new(self.api_secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
            headers.update({"X-BAPI-API-KEY": self.api_key, "X-BAPI-TIMESTAMP": ts,
                            "X-BAPI-RECV-WINDOW": _RECV_WINDOW, "X-BAPI-SIGN": sig,
                            "X-BAPI-SIGN-TYPE": _SIGN_TYPE})
            if body is not None:
                data = json.dumps(body).encode()
        for attempt in range(_RETRIES + 1):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers),
                                            timeout=_TIMEOUT) as resp:
                    payload = json.loads(resp.read())
                ret = payload.get("retCode", 0)
                if ret != 0:
                    if ret in _TRANSIENT_RETCODES and attempt < _RETRIES:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    raise BybitError("TRANSIENT" if ret in _TRANSIENT_RETCODES else "EXECUTION",
                                     f"{ret}: {payload.get('retMsg', 'bybit error')}")
                return payload.get("result", {})
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt < _RETRIES:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise BybitError("NETWORK", str(e))

    def _require_keys(self):
        if not self.enabled:
            raise BybitError("SECURITY",
                             "Bybit API ключи не заданы (BYBIT_API_KEY/BYBIT_API_SECRET в .env)")


class InstrumentInfo:
    """Округление цены/количества по фильтрам инструмента (порт из наработок)."""

    def __init__(self, raw: dict):
        lot = raw.get("lotSizeFilter", {})
        price = raw.get("priceFilter", {})
        self.tick = float(price.get("tickSize", 0) or 0)
        self.qty_step = float(lot.get("qtyStep", 0) or 0) or None
        self.min_qty = float(lot.get("minOrderQty", 0) or 0)

    def round_price(self, p: float) -> float:
        return round(p / self.tick) * self.tick if self.tick else p

    def round_qty(self, q: float) -> float:
        if not self.qty_step:
            return q
        return math.ceil(q / self.qty_step) * self.qty_step

    def minimal_qty_for_amount(self, amount: float, price: float) -> float:
        q = max(amount / price, self.min_qty)
        return self.round_qty(q)