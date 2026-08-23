"""research.yaml -> константы. Совместимый слой: старые имена сохранены.

Код research использует `from config.research import COST_ROUND_TRIP, ...`,
а источник истины лежит в config/research.yaml.
"""
from pathlib import Path

import yaml

CFG = yaml.safe_load((Path(__file__).parent / "research.yaml").read_text())

_u, _c, _b, _p = CFG["universe"], CFG["costs"], CFG["backtest"], CFG["periods"]

# Юниверс
MIN_MEDIAN_TURNOVER = _u["min_median_turnover"]
MAX_SYMBOLS = _u["max_symbols"]

# Издержки
FEE_TAKER_PER_SIDE = _c["fee_taker_per_side"]
SLIPPAGE_TAKER_PER_SIDE = _c["slippage_taker_per_side"]
BASE_COST_PER_SIDE = FEE_TAKER_PER_SIDE + SLIPPAGE_TAKER_PER_SIDE
BASE_COST_ROUND_TRIP = 2 * BASE_COST_PER_SIDE          # 0.15%
COST_ROUND_TRIP = BASE_COST_ROUND_TRIP                  # обратная совместимость
COST_LEVELS_ROUND_TRIP = _c["cost_levels_round_trip"]
MAKER_COST_ROUND_TRIP = 2 * (_c["maker_fee_per_side"] + SLIPPAGE_TAKER_PER_SIDE)  # 0.08%

# Параметры теста гипотез
RET_WINDOWS = _b["ret_windows"]
HOLD_BARS = _b["hold_bars"]
MIN_MOVE = _b["min_move"]

# Периоды discovery / validation / OOS
PERIOD_DISCOVERY = tuple(_p["discovery"])
PERIOD_VALIDATION = tuple(_p["validation"])
PERIOD_OOS = tuple(_p["oos"])

# Критерий отбора кандидата
CANDIDATE_MIN_T = CFG["candidate"]["min_t"]

# Портфельная симуляция
PORTFOLIO_N_LONG = CFG["portfolio"]["n_long"]
PORTFOLIO_N_SHORT = CFG["portfolio"]["n_short"]
PORTFOLIO_REBALANCE_DAYS = CFG["portfolio"]["rebalance_days"]