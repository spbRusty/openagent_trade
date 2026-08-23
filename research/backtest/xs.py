"""Кросс-секциональный (рыночно-нейтральный) бэктест (ТЗ §9).

Сигнал = относительный возврат символа vs медиана рынка на том же баре.
Momentum: long лучшие / short худшие. Так бета рынка вычитается, остаётся альфа.
"""
import polars as pl

from config.research import COST_ROUND_TRIP, MIN_MOVE


def prepare_trades_xs(df: pl.DataFrame, ret_window: int, hold: int, strategy: str,
                      bar_minutes: int, cond: pl.Expr | None = None) -> pl.DataFrame:
    """
    df: бары с колонками symbol, open_time, open, close.
    Сигнал: ret_w_rel = ret_w(symbol) - median(ret_w по всем символам на баре t).
    momentum: long если ret_w_rel > 0, short если < 0. reversion: наоборот.
    cond: условие на баре сигнала (после сдвигов, выравнивание не ломается).
    """
    bar = pl.duration(minutes=bar_minutes)
    df = df.sort(["symbol", "open_time"])

    df = df.with_columns(
        (pl.col("close") / pl.col("close").shift(ret_window) - 1.0).over("symbol").alias("ret_w")
    )
    # Медиана рынка по каждому бару времени
    df = df.with_columns(
        pl.col("ret_w").median().over("open_time").alias("mkt_ret")
    )
    df = df.with_columns(
        (pl.col("ret_w") - pl.col("mkt_ret")).alias("ret_w_rel")
    )
    df = df.with_columns(
        pl.when(pl.col("ret_w_rel") >= 0).then(1.0).otherwise(-1.0).alias("sig_raw")
    )
    if strategy == "reversion":
        df = df.with_columns((-pl.col("sig_raw")).alias("sig"))
    else:
        df = df.with_columns(pl.col("sig_raw").alias("sig"))

    df = df.with_columns(
        pl.col("open").shift(-1).over("symbol").alias("entry_price"),
        pl.col("open").shift(-1 - hold).over("symbol").alias("exit_price"),
        pl.col("open_time").shift(-1).over("symbol").alias("entry_time"),
        pl.col("open_time").shift(-1 - hold).over("symbol").alias("exit_time"),
    )
    time_ok = (
        (pl.col("entry_time") - pl.col("open_time")) == bar
    ) & (
        (pl.col("exit_time") - pl.col("open_time")) == pl.duration(minutes=bar_minutes * (1 + hold))
    )
    df = df.filter(time_ok & pl.col("entry_price").is_not_null() & pl.col("exit_price").is_not_null())
    if cond is not None:
        df = df.filter(cond)
    # отсекаем нулевой относительный сигнал (символ = рынок)
    df = df.filter(pl.col("ret_w_rel").abs() >= MIN_MOVE)

    df = df.with_columns(
        ((pl.col("exit_price") / pl.col("entry_price") - 1.0) * pl.col("sig")).alias("gross_ret")
    )
    df = df.with_columns(
        (pl.col("gross_ret") - COST_ROUND_TRIP).alias("net_ret")
    )
    return df.select([
        "symbol", "open_time", "entry_time", "exit_time",
        "entry_price", "exit_price", "sig", "gross_ret", "net_ret", "ret_w_rel",
    ])