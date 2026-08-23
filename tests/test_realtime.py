"""Контрактные тесты realtime-контура: парсинг WS, стакан, маячки, дайджест."""
from infrastructure.realtime import beacons, ws
from infrastructure.realtime.monitor import _digest_text


# --- парсинг WS-сообщений ---

def test_parse_orderbook_snapshot():
    msg = {"topic": "orderbook.50.BTCUSDT", "type": "snapshot",
           "data": {"s": "BTCUSDT", "b": [["100.0", "1.5"], ["99.0", "2.0"]],
                    "a": [["101.0", "3.0"]]}}
    u = ws.parse_orderbook_update(msg)
    assert u["symbol"] == "BTCUSDT"
    assert u["type"] == "snapshot"
    assert u["bids"][0] == ["100.0", "1.5"]


def test_parse_ignores_non_orderbook():
    assert ws.parse_orderbook_update({"topic": "kline.1.BTCUSDT", "data": []}) is None


def test_parse_orderbook_delta_has_actions():
    msg = {"topic": "orderbook.50.BTCUSDT", "type": "delta",
           "data": {"s": "BTCUSDT", "b": [["100.0", "0.0", 0]], "a": []}}
    u = ws.parse_orderbook_update(msg)
    assert u["bids"][0][2] == 0


# --- стакан: снапшот и delta ---

def test_book_snapshot_and_delta():
    book = beacons.BookState("BTCUSDT")
    book.apply({"symbol": "BTCUSDT", "type": "snapshot",
                "bids": [["100.0", "1.0"], ["99.0", "2.0"]],
                "asks": [["101.0", "3.0"]]})
    assert book.top("bids", 2) == [(100.0, 1.0), (99.0, 2.0)]
    # delta: удалить лучший бид, обновить второй
    book.apply({"symbol": "BTCUSDT", "type": "delta",
                "bids": [["100.0", "0.0", 0], ["99.0", "5.0", 1]], "asks": []})
    assert book.bids == {99.0: 5.0}


def test_book_tolerates_two_element_delta():
    book = beacons.BookState("BTCUSDT")
    book.apply({"symbol": "BTCUSDT", "type": "snapshot",
                "bids": [["100.0", "1.0"]], "asks": [["101.0", "3.0"]]})
    book.apply({"symbol": "BTCUSDT", "type": "delta",
                "bids": [["100.0", "5.0"]], "asks": []})  # без action — update
    assert book.bids == {100.0: 5.0}


def test_book_prunes_zero_size_levels():
    """Дельта [price, 0] без action = удаление уровня, а не призрак с 0.0:
    иначе словарь стакана растёт бесконечно и душит цикл сортировками."""
    book = beacons.BookState("BTCUSDT")
    book.apply({"symbol": "BTCUSDT", "type": "snapshot",
                "bids": [["100.0", "1.0"], ["99.5", "2.0"]],
                "asks": [["101.0", "3.0"]]})
    book.apply({"symbol": "BTCUSDT", "type": "delta",
                "bids": [["99.5", "0"]], "asks": [["101.0", "0"]]})
    assert book.bids == {100.0: 1.0} and book.asks == {}


# --- маячки ---

def test_imbalance_beacon():
    det = beacons.BeaconDetector(
        {"cooldown_sec": 60, "top_levels": 5, "imbalance_threshold": 0.5,
         "wall_ratio": 5.0, "spread_ratio": 3.0})
    # маячок триггерится уже на первом обновлении (снапшот), повторный вызов — в cooldown
    found = det.update({"symbol": "X", "type": "snapshot",
                        "bids": [["100.0", "10.0"], ["99.0", "20.0"], ["98.0", "30.0"],
                                 ["97.0", "40.0"], ["96.0", "50.0"]],
                        "asks": [["101.0", "1.0"], ["102.0", "1.0"],
                                 ["103.0", "1.0"], ["104.0", "1.0"], ["105.0", "1.0"]]})
    imbalance = [b for b in found if b["type"] == "imbalance"]
    assert imbalance and imbalance[0]["side"] == "buy"


def test_imbalance_skips_thin_book():
    det = beacons.BeaconDetector(
        {"cooldown_sec": 60, "top_levels": 5, "imbalance_threshold": 0.5,
         "wall_ratio": 5.0, "spread_ratio": 3.0})
    found = det.update({"symbol": "X", "type": "snapshot",
                        "bids": [["100.0", "1.0"]],  # 1 уровень — тонкий стакан
                        "asks": [["101.0", "1.0"], ["102.0", "1.0"], ["103.0", "1.0"],
                                 ["104.0", "1.0"], ["105.0", "1.0"]]})
    assert not any(b["type"] == "imbalance" for b in found)


def test_wall_beacon():
    det = beacons.BeaconDetector(
        {"cooldown_sec": 60, "top_levels": 5, "imbalance_threshold": 0.9,
         "wall_ratio": 5.0, "spread_ratio": 3.0})
    found = det.update({"symbol": "X", "type": "snapshot",
                        "bids": [["100.0", "1.0"], ["99.0", "1.0"], ["98.0", "1.0"],
                                 ["97.0", "1.0"], ["96.0", "50.0"]],  # стена на 96
                        "asks": [["101.0", "1.0"], ["102.0", "1.0"]]})
    walls = [b for b in found if b["type"] == "wall"]
    assert walls and walls[0]["side"] == "buy" and walls[0]["strength"] >= 5.0


def test_wall_min_notional_gate():
    """Карманная стена (notional < min_wall_usd) не проходит, крупная — проходит."""
    cfg = {"cooldown_sec": 60, "top_levels": 5, "imbalance_threshold": 0.9,
           "wall_ratio": 5.0, "spread_ratio": 3.0, "min_wall_usd": 5000}
    det = beacons.BeaconDetector(dict(cfg))
    found = det.update({"symbol": "X", "type": "snapshot",
                        "bids": [["0.056", "3544"], ["0.056", "191"], ["0.056", "191"],
                                 ["0.056", "191"], ["0.056", "191"]],  # $200 как TUTUSDT
                        "asks": [["0.06", "191"]] * 5})
    assert not any(b["type"] == "wall" for b in found)

    det2 = beacons.BeaconDetector(dict(cfg))
    found2 = det2.update({"symbol": "Y", "type": "snapshot",
                          "bids": [["100.0", "1.0"], ["99.0", "1.0"], ["98.0", "1.0"],
                                   ["97.0", "1.0"], ["96.0", "500.0"]],   # $48k
                          "asks": [["101.0", "1.0"]] * 5})
    walls = [b for b in found2 if b["type"] == "wall"]
    assert walls and "usd=" in walls[0]["detail"]


def test_wall_without_key_keeps_old_behavior():
    det = beacons.BeaconDetector(
        {"cooldown_sec": 60, "top_levels": 5, "imbalance_threshold": 0.9,
         "wall_ratio": 5.0, "spread_ratio": 3.0})   # без min_wall_usd
    found = det.update({"symbol": "X", "type": "snapshot",
                        "bids": [["100.0", "1.0"], ["99.0", "1.0"], ["98.0", "1.0"],
                                 ["97.0", "1.0"], ["96.0", "6.0"]],  # $576 < 5000
                        "asks": [["101.0", "1.0"]] * 5})
    assert any(b["type"] == "wall" for b in found)


def test_cooldown_suppresses_duplicates():
    det = beacons.BeaconDetector(
        {"cooldown_sec": 3600, "top_levels": 5, "imbalance_threshold": 0.5,
         "wall_ratio": 5.0, "spread_ratio": 3.0})
    bids = [["100.0", "50.0"], ["99.0", "50.0"], ["98.0", "50.0"],
            ["97.0", "50.0"], ["96.0", "50.0"]]
    asks = [["101.0", "1.0"], ["102.0", "1.0"], ["103.0", "1.0"],
            ["104.0", "1.0"], ["105.0", "1.0"]]
    for _ in range(3):
        det.update({"symbol": "X", "type": "snapshot", "bids": bids, "asks": asks})
        det.update({"symbol": "X", "type": "delta", "bids": [], "asks": []})
    # только один imbalance за cooldown
    assert sum(1 for b in det.update({"symbol": "X", "type": "delta", "bids": [], "asks": []})
               if b["type"] == "imbalance") == 0


def test_humanize_plain_russian():
    title, body = beacons.humanize(
        {"symbol": "BTCUSDT", "type": "wall", "side": "buy",
         "strength": 237.0, "price": 72544.5})
    assert title == "Стена на покупку: BTCUSDT"
    assert "72544.5" in body and "237" in body

    title, body = beacons.humanize(
        {"symbol": "ETHUSDT", "type": "spread_expansion", "side": "both",
         "strength": 4.0, "spread_pct": 0.12, "median_pct": 0.03})
    assert "Спред" in title and "0.12%" in body

# --- дайджест маячков ---

def test_digest_text_groups_by_symbol():
    rows = [{"symbol": "BTCUSDT", "type": "wall", "strength": 53.8},
            {"symbol": "BTCUSDT", "type": "imbalance", "strength": 0.9},
            {"symbol": "ETHUSDT", "type": "wall", "strength": 12.0}]
    txt = _digest_text(rows, 30)
    assert "Маячков за 30 мин: 3" in txt
    assert "• BTCUSDT: дисбаланс+стена, макс ×53.8" in txt
    assert "• ETHUSDT: стена, макс ×12" in txt


def test_digest_text_caps_at_ten_symbols():
    rows = [{"symbol": f"S{i:02d}USDT", "type": "wall", "strength": 1.0} for i in range(13)]
    txt = _digest_text(rows, 30)
    assert "ещё символов: 3" in txt
    assert txt.count("•") == 11  # топ-10 символов + строка «ещё»
