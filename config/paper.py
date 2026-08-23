"""Бумажная торговля (ТЗ §15-16): параметры из config/paper.yaml.

Код импортирует константы отсюда, а не читает yaml напрямую (конвенция проекта).
"""
import yaml

from config.settings import ROOT

_CFG = yaml.safe_load((ROOT / "config" / "paper.yaml").read_text())

INITIAL_BALANCE = _CFG["paper"]["initial_balance"]
MAX_DRAWDOWN = _CFG["paper"]["max_drawdown"]
MAX_POSITION_USD = _CFG["paper"]["max_position_usd"]

MAX_EXPOSURE_USD = _CFG["risk"]["max_exposure_usd"]
MAX_OPEN_POSITIONS = _CFG["risk"]["max_open_positions"]
MAX_DAILY_LOSS = _CFG["risk"]["max_daily_loss"]

FEE_RATE = _CFG["execution"]["fee_rate"]
SLIPPAGE_PCT = _CFG["execution"]["slippage_pct"]
LATENCY_MS = _CFG["execution"]["latency_ms"]

ACCOUNT_PATH = ROOT / "data" / "paper" / "account.json"