"""Портфельная симуляция кросс-секционального momentum/mr (ТЗ §9, portfolio-level).

Отличие от per-trade бэктеста: моделируется РЕАЛЬНЫЙ портфель.
Каждый ребаланс: ранжируем символы по ret_W, лонг топ-N, шорт боттом-N,
вход на open следующего бара, выход на open через H дней, равные веса,
издержки на каждую позицию каждый ребаланс (консервативно: полный раунд-трип).
"""
import numpy as np
import polars as pl

from config.research import (BASE_COST_ROUND_TRIP, PORTFOLIO_N_LONG,
                             PORTFOLIO_N_SHORT, PORTFOLIO_REBALANCE_DAYS)


def run_portfolio(df: pl.DataFrame, ret_window: int, hold_days: int,
                  strategy: str = "momentum",
                  cost_round_trip: float = BASE_COST_ROUND_TRIP,
                  n_long: int = PORTFOLIO_N_LONG, n_short: int = PORTFOLIO_N_SHORT,
                  rebalance_days: int = PORTFOLIO_REBALANCE_DAYS) -> dict:
    """Кросс-секциональный портфель. df: symbol, open_time (дн), open, close.
    Возвращает метрики портфеля + посделочный DataFrame + кривую капитала."""
    day = pl.duration(days=1)
    df = df.sort(["symbol", "open_time"])

    df = df.with_columns(
        (pl.col("close") / pl.col("close").shift(ret_window) - 1.0).over("symbol").alias("ret_w")
    )
    # Цены входа/выхода относительно каждого бара t: вход open t+1, выход open t+1+H
    df = df.with_columns(
        pl.col("open").shift(-1).over("symbol").alias("entry_price"),
        pl.col("open").shift(-1 - hold_days).over("symbol").alias("exit_price"),
        pl.col("open_time").shift(-1).over("symbol").alias("entry_time"),
        pl.col("open_time").shift(-1 - hold_days).over("symbol").alias("exit_time"),
    )
    # Проверка непрерывности: t+1 ровно через 1 день, t+1+H ровно через 1+H дней
    df = df.with_columns(
        ((pl.col("entry_time") - pl.col("open_time")) == day).alias("entry_ok"),
        ((pl.col("exit_time") - pl.col("open_time")) == pl.duration(days=1 + hold_days)).alias("exit_ok"),
    )
    df = df.filter(
        pl.col("entry_ok") & pl.col("exit_ok")
        & pl.col("entry_price").is_not_null() & pl.col("exit_price").is_not_null()
    )

    # Ребалансы: каждые rebalance_days дней от начала данных
    times = df["open_time"].unique().sort()
    rebal_dates = times[::rebalance_days].to_list()

    trades = []
    equity = 1.0
    curve = []
    for t in rebal_dates:
        bar = df.filter(pl.col("open_time") == t)
        if bar.height < 5:
            continue
        bar = bar.drop_nulls("ret_w").filter(pl.col("ret_w").abs() >= 0.0001)
        if bar.height < 5:
            continue

        if strategy == "reversion":
            bar = bar.with_columns((-pl.col("ret_w")).alias("score"))
        else:
            bar = bar.with_columns(pl.col("ret_w").alias("score"))

        bar = bar.sort("score", descending=True)
        longs = bar.head(n_long)
        shorts = bar.tail(n_short)

        positions = []
        for pos, side in [(longs, 1.0), (shorts, -1.0)]:
            for row in pos.iter_rows(named=True):
                gross = side * (row["exit_price"] / row["entry_price"] - 1.0)
                net = gross - cost_round_trip  # издержки на раунд-трип каждой позиции
                trades.append({
                    "rebal_date": t, "symbol": row["symbol"], "side": side,
                    "entry": row["entry_price"], "exit": row["exit_price"],
                    "gross": gross, "net": net,
                })

        # считаем за период: только сделки этого ребаланса
        period_trades = trades[- (len(longs) + len(shorts)):]
        if period_trades:
            period_net = np.mean([x["net"] for x in period_trades])
            equity *= (1 + period_net)
        curve.append({"date": t, "equity": equity, "period_ret": period_net if period_trades else 0.0,
                      "n_pos": len(longs) + len(shorts)})

    return {"trades": pl.DataFrame(trades), "curve": pl.DataFrame(curve)}


def summarize_portfolio(res: dict) -> dict:
    tr = res["trades"]
    curve = res["curve"]
    if tr.height == 0:
        return {"n": 0}
    period_rets = curve["period_ret"].to_numpy()
    n = len(period_rets)
    mean = period_rets.mean()
    std = period_rets.std() if n > 1 else 0.0
    cum = curve["equity"].to_numpy()
    peak = np.maximum.accumulate(cum)
    max_dd = float(((cum - peak) / peak).min()) if n else 0.0
    return {
        "n_periods": n,
        "n_trades": tr.height,
        "period_mean": float(mean),
        "period_std": float(std),
        "period_sharpe": float(mean / std) if std > 0 else 0.0,
        "final_equity": float(cum[-1]),
        "total_return": float(cum[-1] - 1.0),
        "max_dd": max_dd,
        "win_rate_periods": float((period_rets > 0).mean()),
    }