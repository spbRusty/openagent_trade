"""Per-trade бэктест (ТЗ §9). Честный: сигнал на close t, вход open t+1,
выход open t+1+H. Защита от lookahead и пробелов в данных.
Издержки: комиссия + слиппедж на сторону (config/research.yaml).
"""
import polars as pl
import numpy as np

from config.research import COST_ROUND_TRIP, MIN_MOVE


def prepare_trades(df: pl.DataFrame, ret_window: int, hold: int, strategy: str,
                   bar_minutes: int = 1, cond: pl.Expr | None = None) -> pl.DataFrame:
    """
    Возвращает DataFrame сделок: symbol, entry_time, exit_time, entry_price, exit_price,
    gross_ret (без издержек), net_ret (с издержками), signal.
    strategy: 'momentum' (продолжение движения) или 'reversion' (откат).
    bar_minutes: длительность бара в минутах (для проверки непрерывности времени).
    cond: дополнительное условие на баре сигнала t (polars-выражение по фичам,
          например (pl.col("rvol_21") > 2.0)). Фильтр применяется ПОСЛЕ расчёта
          сдвигов, поэтому выравнивание по времени не ломается.
    """
    bar = pl.duration(minutes=bar_minutes)
    df = df.sort(["symbol", "open_time"])

    # Сигнал на close бара t: возврат за последние ret_window минут
    df = df.with_columns(
        (pl.col("close") / pl.col("close").shift(ret_window) - 1.0).over("symbol").alias("ret_w")
    )
    df = df.with_columns(
        pl.when(pl.col("ret_w") >= 0).then(1.0).otherwise(-1.0).alias("sig_raw")
    )
    if strategy == "reversion":
        df = df.with_columns((-pl.col("sig_raw")).alias("sig"))
    else:
        df = df.with_columns(pl.col("sig_raw").alias("sig"))

    # Вход: open t+1. Выход: open t+1+hold. Проверка непрерывности времени (нет пробелов).
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

    # Условие на баре сигнала (после сдвигов — выравнивание не ломается)
    if cond is not None:
        df = df.filter(cond)

    # Минимальный |move|: не торгуем суб-шумовые движения
    df = df.filter(pl.col("ret_w").abs() >= MIN_MOVE)

    df = df.with_columns(
        ((pl.col("exit_price") / pl.col("entry_price") - 1.0) * pl.col("sig")).alias("gross_ret")
    )
    df = df.with_columns(
        (pl.col("gross_ret") - COST_ROUND_TRIP).alias("net_ret")
    )
    return df.select([
        "symbol", "open_time", "entry_time", "exit_time",
        "entry_price", "exit_price", "sig", "gross_ret", "net_ret",
    ])


def summarize(trades: pl.DataFrame) -> dict:
    n = trades.height
    if n == 0:
        return {"n": 0}
    net = trades["net_ret"].to_numpy()
    gross = trades["gross_ret"].to_numpy()
    return {
        "n": n,
        "winrate": float((net > 0).mean()),
        "ev_net": float(net.mean()),
        "ev_gross": float(gross.mean()),
        "total_net": float(net.sum()),
        "sharpe": float(net.mean() / net.std()) if n > 1 and net.std() > 0 else 0.0,
    }


def weekly_pnl(trades: pl.DataFrame) -> pl.DataFrame:
    if trades.height == 0:
        return trades
    return (
        trades.with_columns(pl.col("entry_time").dt.strftime("%Y-W%V").alias("week"))
        .group_by("week")
        .agg(
            pl.len().alias("n_trades"),
            pl.col("net_ret").sum().alias("week_net"),
            (pl.col("net_ret") > 0).mean().alias("winrate"),
        )
        .sort("week")
    )