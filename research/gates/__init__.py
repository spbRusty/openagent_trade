"""Gates — формальные детерминированные проверки результата (ТЗ §12).

Порядок строго: market-neutral → cost stress → overlap-adjusted stats →
long/short → концентрация → временная стабильность → block bootstrap → OOS.
Если какой-то гейт не пройден — кандидат отбрасывается с причиной.

Gates НЕ зависят от LLM. Каждый возвращает PASS/FAIL + структурированный reason.
"""
import numpy as np
import polars as pl

from research.backtest import prepare_trades
from research.backtest.xs import prepare_trades_xs
from research.statistics import full_metrics, block_bootstrap_pvalue
from config.research import COST_LEVELS_ROUND_TRIP, CANDIDATE_MIN_T


class GateResult:
    def __init__(self):
        self.results = []
        self.passed = True
        self.fail_reason = None

    def add(self, name: str, ok: bool, detail: str = ""):
        self.results.append((name, ok, detail))
        if not ok and self.passed:
            self.passed = False
            self.fail_reason = name

    def print(self):
        for name, ok, detail in self.results:
            mark = "PASS" if ok else "FAIL"
            print(f"  [{mark}] {name}: {detail}")


def gate_all(df: pl.DataFrame, ret_window: int, hold: int, strategy: str,
             bar_minutes: int, oos_start=None, cond: pl.Expr | None = None) -> GateResult:
    """
    Полный шлюз. df: бары (symbol, open_time, open, close).
    oos_start: если задан — OOS-гейт проверяется только на данных >= oos_start.
    cond: условие на баре сигнала — применяется и к raw, и к XS (сигнал × условие).
    Возвращает GateResult. Параметры фиксируются ДО вызова (после discovery).
    """
    g = GateResult()

    # --- 1. Market-neutral: переживает ли сигнал вычитание медианы рынка? ---
    tr_xs = prepare_trades_xs(df, ret_window, hold, strategy, bar_minutes, cond=cond)
    s_xs = full_metrics(tr_xs["net_ret"].to_numpy())
    g.add("market_neutral", s_xs["n"] > 100 and s_xs["mean"] > 0,
          f"EV_net={s_xs['mean']:+.5f} t={s_xs['t_stat']:.2f} n={s_xs['n']}")
    if not g.passed:
        return g  # альфы нет — глубокий анализ не нужен

    # --- 2. Cost stress: выживает ли при 0.20%+ ? ---
    tr_raw = prepare_trades(df, ret_window, hold, strategy, bar_minutes, cond=cond)
    for cost in COST_LEVELS_ROUND_TRIP:
        net = (tr_raw["gross_ret"].to_numpy() - cost)
        s = full_metrics(net)
        ok = s["mean"] > 0
        g.add(f"cost_{cost:.2%}", ok, f"EV_net={s['mean']:+.5f} t={s['t_stat']:.2f}")
    if not g.passed:
        return g

    # --- 3. Overlap-adjusted: месячная агрегация, независимые периоды ---
    monthly = (
        tr_raw.with_columns(pl.col("entry_time").dt.strftime("%Y-%m").alias("m"))
        .group_by("m").agg(pl.col("net_ret").sum().alias("pnl"))
    )
    mpnl = monthly["pnl"].to_numpy()
    m = full_metrics(mpnl)
    ok_overlap = m["n"] >= 12 and m["t_stat"] >= CANDIDATE_MIN_T
    g.add("overlap_adjusted", ok_overlap,
          f"месяцев={m['n']} EV={m['mean']:+.5f} t={m['t_stat']:.2f} плюс-месяцев={int((mpnl>0).sum())}")
    if not g.passed:
        return g

    # --- 4. Long/Short раздельно ---
    longs = tr_raw.filter(pl.col("sig") > 0)["net_ret"].to_numpy()
    shorts = tr_raw.filter(pl.col("sig") < 0)["net_ret"].to_numpy()
    s_l, s_s = full_metrics(longs), full_metrics(shorts)
    # лонг и шорт должны оба быть неотрицательными (или хотя бы один значительно в плюс, другой не в глубокий минус)
    ok_ls = s_l["mean"] >= 0 and s_s["mean"] >= -0.0005
    g.add("long_short", ok_ls,
          f"long EV={s_l['mean']:+.5f} t={s_l['t_stat']:.2f} | short EV={s_s['mean']:+.5f} t={s_s['t_stat']:.2f}")
    if not g.passed:
        return g

    # --- 5. Концентрация по символам ---
    by_sym = (tr_raw.group_by("symbol").agg(pl.col("net_ret").sum().alias("pnl"))
              .sort("pnl", descending=True))
    tot = tr_raw["net_ret"].sum()
    top5 = by_sym.head(5)["pnl"].sum()
    ok_conc = tot > 0 and (top5 / tot) <= 0.60
    g.add("concentration", ok_conc, f"топ-5={top5/tot:.0%} результата (порог 60%)")
    if not g.passed:
        return g

    # --- 6. Временная стабильность: недели + две половины истории ---
    weekly = (tr_raw.with_columns(pl.col("entry_time").dt.strftime("%Y-W%V").alias("w"))
              .group_by("w").agg(pl.col("net_ret").sum().alias("pnl")))
    wpnl = weekly["pnl"].to_numpy()
    pos_weeks = (wpnl > 0).sum() / len(wpnl)
    lo, hi = tr_raw["entry_time"].min(), tr_raw["entry_time"].max()
    mid = lo + (hi - lo) / 2
    first = tr_raw.filter(pl.col("entry_time") < mid)["net_ret"].to_numpy()
    second = tr_raw.filter(pl.col("entry_time") >= mid)["net_ret"].to_numpy()
    s_f, s_s2 = full_metrics(first), full_metrics(second)
    ok_stab = pos_weeks >= 0.5 and s_f["mean"] > 0 and s_s2["mean"] > 0
    g.add("time_stability", ok_stab,
          f"недель_плюс={pos_weeks:.0%} | 1я половина EV={s_f['mean']:+.5f} 2я EV={s_s2['mean']:+.5f}")
    if not g.passed:
        return g

    # --- 7. Block bootstrap (на месячных агрегатах) ---
    p_val, obs, ci = block_bootstrap_pvalue(mpnl, block=3, n_iter=5000)
    ok_bs = p_val < 0.05
    g.add("bootstrap", ok_bs, f"p={p_val:.3f} (H0: mean<=0), obs={obs:+.5f}")
    if not g.passed:
        return g

    # --- 8. OOS: результат на нетронутом периоде ---
    if oos_start is not None:
        tr_oos = tr_raw.filter(pl.col("entry_time") >= pl.lit(oos_start))
        if tr_oos.height > 50:
            s_oos = full_metrics(tr_oos["net_ret"].to_numpy())
            ok_oos = s_oos["mean"] > 0
            g.add("oos", ok_oos, f"EV_net={s_oos['mean']:+.5f} t={s_oos['t_stat']:.2f} n={s_oos['n']}")
        else:
            g.add("oos", False, f"недостаточно сделок в OOS ({tr_oos.height})")

    return g