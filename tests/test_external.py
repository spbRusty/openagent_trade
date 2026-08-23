"""Тесты внешнего контекста: Fear & Greed + RSS (без сети)."""
import json

from infrastructure.realtime.external import (
    SeenDB, classify, normalize, parse_feed, parse_fng, relevant_and_classified,
    should_emit_fng)

FNG_JSON = json.dumps({"data": [
    {"value": "23", "value_classification": "Extreme Fear", "timestamp": "1787356800"},
    {"value": "31", "value_classification": "Fear", "timestamp": "1787270400"}]})

RSS_XML = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>SEC sues Binance: new regulation may hit Bitcoin ETF market</title>
<link>https://ex.com/a</link><guid>g1</guid>
<pubDate>Fri, 21 Aug 2026 10:00:00 +0000</pubDate>
<description>&lt;p&gt;Regulators act&lt;/p&gt;</description></item>
<item><title>Local bakery opens new branch</title>
<link>https://ex.com/b</link><guid>g2</guid></item>
</channel></rss>"""

RULES = {
    "assets": ["BTC", "Bitcoin", "ETH"], "entities": ["Binance", "SEC", "ETF"],
    "categories": ["regulation", "hack"],
    "importance_critical": ["hacked", "exploit"],
    "importance_high": ["sec ", "etf"],
    "importance_medium": ["coinbase"]}


# ---------- FNG ----------

def test_fng_parsing():
    st = parse_fng(FNG_JSON)
    assert st["value"] == 23 and st["classification"] == "Extreme Fear"
    assert st["change_24h"] == -8 and st["timestamp"].startswith("2026")


def test_fng_regime_change_emits():
    prev = {"value": 45, "classification": "Neutral"}
    new = {"value": 23, "classification": "Extreme Fear"}
    emit, sev = should_emit_fng(prev, new, significant=7)
    assert emit and sev == "warning"      # вход в Extreme


def test_fng_small_drift_silent():
    prev = {"value": 70, "classification": "Greed"}
    new = {"value": 72, "classification": "Greed"}
    emit, _ = should_emit_fng(prev, new, significant=7)
    assert not emit                        # +2 — шум


def test_fng_sharp_move_within_regime_emits():
    prev = {"value": 50, "classification": "Neutral"}
    new = {"value": 41, "classification": "Neutral"}
    emit, sev = should_emit_fng(prev, new, significant=7)
    assert emit and sev == "info"          # резкий сдвиг без Extreme


def test_fng_first_observation_is_baseline_once():
    new = {"value": 71, "classification": "Greed"}
    emit, sev = should_emit_fng(None, new, significant=7)
    assert emit and sev == "info"
    # повтор с тем же состоянием — тишина
    emit2, _ = should_emit_fng(new, new, significant=7)
    assert not emit2


# ---------- RSS ----------

def test_rss_parse_and_normalize():
    items = parse_feed(RSS_XML, "testsrc")
    assert len(items) == 2
    n = classify(normalize(items[0], "2026-08-22T00:00:00+00:00"), RULES)
    assert "Bitcoin" in n["symbols"]      # слово найдено; тикера BTC в тексте нет
    assert "Binance" in n["entities"] and "SEC" in n["entities"]
    assert "regulation" in n["categories"]
    assert n["importance"] == "high" and n["needs_analysis"]
    assert n["summary"] == "Regulators act"          # HTML снят
    assert n["published_at"].startswith("2026-08-21")


def test_rss_irrelevant_filtered():
    items = parse_feed(RSS_XML, "testsrc")
    out = relevant_and_classified(items[1], RULES, "now")
    assert out is None                    # пекарня не проходит relevance filter


def test_rss_malformed_feed_returns_empty():
    assert parse_feed("<rss><channel><item><title>x", "s") == []


def test_rss_critical_importance():
    xml = RSS_XML.replace("ETF market", "ETF market hacked by exploit")
    items = parse_feed(xml, "s")
    n = classify(normalize(items[0], "now"), RULES)
    assert n["importance"] == "critical" and n["needs_analysis"]


def test_dedup_url_and_guid(tmp_path):
    db = SeenDB(str(tmp_path / "seen.sqlite"))
    n = classify(normalize(parse_feed(RSS_XML, "s")[0], "now"), RULES)
    assert db.is_new(n)
    assert not db.is_new(n)               # тот же id/url — дубль
    other = dict(n, id="otherid", url="https://other.com/x")
    assert db.is_new(other)               # другой url+id — новая


def test_dedup_survives_restart(tmp_path):
    n = classify(normalize(parse_feed(RSS_XML, "s")[0], "now"), RULES)
    db1 = SeenDB(str(tmp_path / "seen.sqlite"))
    assert db1.is_new(n)
    db2 = SeenDB(str(tmp_path / "seen.sqlite"))   # «перезапуск»
    assert not db2.is_new(n)
