"""Конфиг realtime-монитора: загрузка из config/realtime.yaml.

Параметры подстраиваются с опытом — правки применяются без рестарта
(монитор перечитывает YAML по SIGHUP и вызывает reload()).
"""
import yaml
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _ROOT / "config" / "realtime.yaml"

_DEFAULTS = {
    "symbols": {"top_k": 50, "min_turnover_usd": 20000, "refresh_hours": 6},
    "ws": {"base_url": "wss://stream.bybit.com/v5/public/linear", "depth": 50,
           "ping_interval": 20, "reconnect_backoff": [5, 60]},
    "beacons": {"cooldown_sec": 60, "top_levels": 10, "imbalance_threshold": 0.7,
                "wall_ratio": 5.0, "spread_ratio": 3.0},
    "opencode": {"interval_min": 60, "bin": "/home/vlad/.opencode/bin/opencode",
                 "timeout_sec": 300, "prompt": ""},
    "notify": {"min_level": "warning", "digest_min": 30},
    "dashboard": {"port": 8000},
    "journal": {"dir": "logs/realtime"},
    "correlation": {"sample_sec": 5, "window_min": 60, "anchor": "BTCUSDT"},
    "whale": {"events_path": "/home/vlad/Документы/whale_monitor/events/whale_events.jsonl"},
    "external": {
        "fng_enabled": True, "fng_poll_min": 10, "fng_significant_change": 7,
        "fng_state_path": "/home/vlad/Документы/openagent_trade/data/external/fng_state.json",
        "rss_enabled": True, "rss_poll_min": 5,
        "rss_seen_db": "/home/vlad/Документы/openagent_trade/data/external/news_seen.sqlite",
        "rss_dedup_ttl_hours": 72,
        "rss_sources": {
            "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "cointelegraph": "https://cointelegraph.com/rss",
            "decrypt": "https://decrypt.co/feed"},
        "rss_rules": {
            "assets": ["BTC", "Bitcoin", "ETH", "Ethereum", "SOL", "Solana",
                       "USDT", "USDC"],
            "entities": ["Binance", "Bybit", "Coinbase", "ETF", "SEC", "CFTC",
                         "Tether", "Circle"],
            "categories": ["regulation", "hack", "exploit", "liquidation",
                           "stablecoin", "Fed", "interest rate", "whale",
                           "outage", "ban", "approval", "rejection"],
            "importance_critical": ["hacked", "exploit", "ban", "outage",
                                    "depeg", "insolven"],
            "importance_high": ["fed", "interest rate", "etf approval",
                                "etf rejection", "sec ", "cftc", "billion"],
            "importance_medium": ["coinbase", "tether", "circle", "institution"]},
    },
    "autotune": {
        "enabled": True, "interval_min": 720,
        "state_path": "/home/vlad/Документы/openagent_trade/data/paper/autotune.json",
        "window_hours": 24,
        "rules": {"min_sample": 15, "winrate_floor": 0.35,
                  "winrate_ceiling": 0.60, "zombie_share_max": 0.50,
                  "starve_hours": 6},
    },
}

_cfg = {}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def reload() -> dict:
    """Перечитать YAML и вернуть актуальный конфиг (с дефолтами по умолчанию)."""
    global _cfg
    if _CONFIG.exists():
        with open(_CONFIG) as f:
            user = yaml.safe_load(f) or {}
    else:
        user = {}
    _cfg = _deep_merge(_DEFAULTS, user)
    return _cfg


def get() -> dict:
    if not _cfg:
        reload()
    return _cfg


reload()