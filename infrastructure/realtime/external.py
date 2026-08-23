"""Внешний контекст: Fear & Greed Index + RSS-новости.
Только контекст для панели/анализа — торговых решений не принимает,
в trader не попадает (как и киты). HTTP stdlib-urllib, без зависимостей."""
import asyncio
import gzip
import hashlib
import json
import logging
import re
import sqlite3
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

logger = logging.getLogger(__name__)

FNG_URL = "https://api.alternative.me/fng/?limit=2"


def _http_get(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
    return data.decode("utf-8", "replace")


# ---------- Fear & Greed ----------

def parse_fng(payload: str) -> dict:
    """{value, classification, timestamp, change_24h} из ответа alternative.me."""
    d = json.loads(payload)["data"]
    cur = int(d[0]["value"])
    prev_day = int(d[1]["value"]) if len(d) > 1 else None
    return {
        "value": cur,
        "classification": d[0]["value_classification"],
        "timestamp": datetime.fromtimestamp(int(d[0]["timestamp"]), timezone.utc)
                     .isoformat(timespec="seconds"),
        "change_24h": (cur - prev_day) if prev_day is not None else None,
    }


def should_emit_fng(prev: dict | None, new: dict, significant: int) -> tuple[bool, str]:
    """Эмитим только при: первое наблюдение / смена режима / сдвиг >= significant.
    severity: warning при входе в Extreme или смене режима с Extreme."""
    if prev is None:
        return True, "info"
    regime_changed = prev.get("classification") != new["classification"]
    sharp = abs(new["value"] - prev["value"]) >= significant
    if not (regime_changed or sharp):
        return False, ""
    extreme = "Extreme" in new["classification"]
    return True, "warning" if (extreme or regime_changed and
                               "Extreme" in str(prev.get("classification"))) else "info"


async def fng_loop(cfg: dict, recent: list, publish) -> None:
    state_path = Path(cfg["fng_state_path"])
    interval = cfg["fng_poll_min"] * 60
    sig = cfg["fng_significant_change"]
    while True:
        try:
            prev = json.loads(state_path.read_text()) if state_path.exists() else None
            st = parse_fng(_http_get(FNG_URL))
            st["previous_value"] = prev["value"] if prev else None
            st["change"] = (st["value"] - prev["value"]) if prev else None
            emit, sev = should_emit_fng(prev, st, sig)
            if emit:
                chg = f"{st['change']:+d}" if st["change"] is not None else "—"
                d24 = f"{st['change_24h']:+d}" if st["change_24h"] is not None else "—"
                ev = {"ts": st["timestamp"], "type": "market_sentiment",
                      "subtype": "fear_greed", "symbol": "MARKET", "severity": sev,
                      "detail": f"Fear&Greed {st['value']} ({st['classification']}), "
                                f"Δ{chg}, за 24ч {d24}"}
                recent.append(ev)
                publish(ev)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(st))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("fng_loop error")
        await asyncio.sleep(interval)


# ---------- RSS ----------

_TAG = re.compile(r"<[^>]+>")


def _clean(html: str | None, limit: int = 220) -> str | None:
    if not html:
        return None
    txt = re.sub(r"\s+", " ", _TAG.sub("", html)).strip()
    return txt[:limit] or None


def parse_feed(xml_text: str, source: str) -> list[dict]:
    """RSS/Atom → сырые items; битый XML отдаёт пустой список (лента молчит)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.warning("rss %s: malformed feed", source)
        return []
    out = []
    for it in root.findall(".//item"):
        get = lambda t: (it.findtext(t) or "").strip()
        out.append({"source": source, "title": get("title"),
                    "url": get("link"), "guid": get("guid"),
                    "published": get("pubDate"), "summary": _clean(get("description"))})
    return out


def normalize(raw: dict, fetched_at: str) -> dict:
    try:
        pub = parsedate_to_datetime(raw["published"]).astimezone(timezone.utc) \
                 .isoformat(timespec="seconds") if raw["published"] else None
    except (TypeError, ValueError):
        pub = None
    nid = raw.get("url") or raw.get("guid") or raw["title"]
    return {"id": hashlib.sha1(nid.encode()).hexdigest()[:16],
            "type": "external_context", "subtype": "news",
            "source": raw["source"], "title": raw["title"], "url": raw["url"],
            "published_at": pub, "fetched_at": fetched_at,
            "summary": raw["summary"], "symbols": [], "entities": [],
            "categories": [], "importance": "low",
            "needs_analysis": False, "analysis_reason": "", "confidence": 0.6}


def classify(news: dict, rules: dict) -> dict:
    """Rule-based: keywords из конфига → symbols/entities/categories/importance."""
    text = f"{news['title']} {news['summary'] or ''}".lower()
    for group, field in (("assets", "symbols"), ("entities", "entities"),
                        ("categories", "categories")):
        for kw in rules.get(group, []):
            if kw.lower() in text:
                news[field].append(kw)
    for imp in ("critical", "high", "medium"):
        for kw in rules.get(f"importance_{imp}", []):
            if kw.lower() in text:
                news["importance"] = imp
                news["analysis_reason"] = kw
                break
        if news["importance"] != "low":
            break
    news["needs_analysis"] = news["importance"] in ("high", "critical")
    return news


_SEV = {"critical": "critical", "high": "warning", "medium": "info", "low": "info"}


class SeenDB:
    """Персистентный дедуп по url и guid (TTL-чистка на старте)."""

    def __init__(self, path: str, ttl_hours: int = 72):
        self.ttl = ttl_hours * 3600
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS seen"
                        "(k TEXT PRIMARY KEY, ts REAL)")
        self.db.execute("DELETE FROM seen WHERE ts < ?", (time.time() - self.ttl,))
        self.db.commit()

    def is_new(self, news: dict) -> bool:
        now = time.time()
        keys = [f"url:{news['url']}", f"id:{news['id']}"] if news["url"] \
            else [f"id:{news['id']}"]
        if any(self.db.execute("SELECT 1 FROM seen WHERE k=?", (k,)).fetchone()
               for k in keys):
            return False
        self.db.executemany("INSERT OR REPLACE INTO seen VALUES (?,?)",
                            [(k, now) for k in keys])
        self.db.commit()
        return True


def relevant_and_classified(raw: dict, rules: dict, fetched_at: str) -> dict | None:
    """Relevance filter: ни одного keyword → None (игнорируем по ТЗ)."""
    news = classify(normalize(raw, fetched_at), rules)
    if not (news["symbols"] or news["entities"] or news["categories"]):
        return None
    return news


def news_event(news: dict) -> dict:
    return {"ts": news["published_at"] or news["fetched_at"],
            "type": "external_context", "subtype": "news",
            "symbol": ",".join(news["symbols"]) or news["source"].upper(),
            "severity": _SEV[news["importance"]],
            "source_name": news["source"], "url": news["url"],
            "needs_analysis": news["needs_analysis"],
            "analysis_reason": news["analysis_reason"],
            "confidence": news["confidence"],
            "importance": news["importance"],
            "detail": f"[{news['source']}] {news['title'][:120]}"}


def _age_hours(news: dict) -> float:
    if not news["published_at"]:
        return 0.0                            # нет даты — считаем свежей
    pub = datetime.fromisoformat(news["published_at"])
    return max(0.0, (datetime.now(timezone.utc) - pub).total_seconds() / 3600)


async def rss_loop(cfg: dict, recent: list, publish) -> None:
    interval = cfg["rss_poll_min"] * 60
    rules = cfg["rss_rules"]
    max_age_h = cfg.get("rss_max_age_hours", 12)
    fails: dict[str, int] = {}
    db = None
    while True:
        try:
            if db is None:
                db = SeenDB(cfg["rss_seen_db"], cfg["rss_dedup_ttl_hours"])
            fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for name, url in cfg["rss_sources"].items():
                backoff = min(60 * 2 ** fails.get(name, 0), 1800)
                if backoff > interval:
                    continue                       # лента временно в бане
                try:
                    items = parse_feed(_http_get(url), name)
                    fails[name] = 0
                except Exception:
                    fails[name] = fails.get(name, 0) + 1
                    logger.warning("rss %s недоступна (%d раз)", name, fails[name])
                    continue
                fresh = 0
                for raw in items:
                    news = relevant_and_classified(raw, rules, fetched_at)
                    if news and db.is_new(news):
                        if _age_hours(news) <= max_age_h:   # бэклог молча в дедуп
                            ev = news_event(news)
                            recent.append(ev)
                            publish(ev)
                            fresh += 1
                if fresh:
                    logger.info("rss %s: %d новых релевантных", name, fresh)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("rss_loop error")
        await asyncio.sleep(interval)
