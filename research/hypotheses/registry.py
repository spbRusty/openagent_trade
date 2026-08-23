"""Предрегистрированные гипотезы (ТЗ §8): формализованный реестр + фичи.

Гипотезы фиксируются ДО просмотра данных. Каждая имеет уникальный ID и
экономическое обоснование (защита от data mining). Схема: сигнал × условие —
условие фильтрует сделки базового сигнала.
"""
from dataclasses import dataclass, field

import polars as pl

from config.settings import DATA_4H


def load_daily_full() -> pl.DataFrame:
    """Дневные бары OHLCV из 4h-истории: open/high/low/close/volume."""
    frames = []
    for f in sorted(DATA_4H.glob("*_4h.parquet")):
        df = pl.read_parquet(f)
        sym = f.name.replace("_4h.parquet", "")
        frames.append(df.with_columns(pl.lit(sym).alias("symbol")))
    df = pl.concat(frames).sort(["symbol", "open_time"])
    return (df.group_by_dynamic("open_time", every="1d", group_by="symbol")
            .agg([pl.col("open").first().alias("open"),
                  pl.col("high").max().alias("high"),
                  pl.col("low").min().alias("low"),
                  pl.col("close").last().alias("close"),
                  pl.col("volume").sum().alias("volume")])
            .drop_nulls())


def add_features(df: pl.DataFrame) -> pl.DataFrame:
    """RVOL(5/21) = volume/rolling_median(volume,N); ATR_rel; realized vol;
    vol regime (atr_rel vs собственный медианный уровень); rel_vol (кросс-секция)."""
    df = df.sort(["symbol", "open_time"])
    df = df.with_columns(
        (pl.col("volume") / pl.col("volume").rolling_median(window_size=5).over("symbol")).alias("rvol_5"),
        (pl.col("volume") / pl.col("volume").rolling_median(window_size=21).over("symbol")).alias("rvol_21"),
    )
    df = df.with_columns(
        ((pl.col("high") - pl.col("low")) / pl.col("close")).alias("hlr")
    )
    df = df.with_columns(
        pl.col("hlr").rolling_mean(window_size=14).over("symbol").alias("atr_rel")
    )
    df = df.with_columns(
        pl.col("close").log().diff().over("symbol").alias("log_ret")
    )
    df = df.with_columns(
        pl.col("log_ret").rolling_std(window_size=21).over("symbol").alias("realized_vol")
    )
    df = df.with_columns(
        pl.col("atr_rel").rolling_median(window_size=60).over("symbol").alias("atr_med")
    )
    df = df.with_columns(
        pl.when(pl.col("atr_rel") > pl.col("atr_med")).then(True).otherwise(False).alias("vol_high"),
        pl.when(pl.col("atr_rel") < pl.col("atr_med")).then(True).otherwise(False).alias("vol_low"),
    )
    df = df.with_columns(
        pl.col("volume").median().over("open_time").alias("mkt_vol_med")
    )
    df = df.with_columns(
        (pl.col("volume") / pl.col("mkt_vol_med")).alias("rel_vol")
    )
    return df.drop(["hlr", "log_ret", "atr_med", "mkt_vol_med"])


@dataclass(frozen=True)
class Hypothesis:
    """Формализованная гипотеза по ТЗ §8. Поля обязательны до прогона."""
    id: str
    name: str
    strategy: str                    # momentum / reversion
    description: str
    economic_rationale: str
    expected_signal: str
    universe: str = "линейные фьючерсы Bybit, топ по обороту"
    timeframe: str = "1d (агрегация из 4h)"
    entry: str = "open t+1"
    exit: str = "open t+1+H"
    holding_period: str = "H баров"
    features: tuple = ()
    parameters: dict = field(default_factory=dict)
    cost_assumptions: str = "taker 0.15% round-trip; стресс 0.10-0.30%"
    expected_failure_modes: str = ""
    cond: object = None              # polars-выражение условия (сигнал × условие)

    def to_record(self) -> dict:
        return {
            "id": self.id, "name": self.name, "strategy": self.strategy,
            "description": self.description, "economic_rationale": self.economic_rationale,
            "expected_failure_modes": self.expected_failure_modes,
        }


HYPOTHESES = [
    Hypothesis("H1", "RVOL_экстрем→momentum", "momentum",
               "экстремальный объём → продолжение",
               "аномальный объём означает вовлечённость крупных участников, "
               "движение чаще продолжается, чем разворачивается",
               "положительный net EV после издержек",
               cond=pl.col("rvol_21") > 2.0,
               expected_failure_modes="пересечение: объём на разворотах, шум"),
    Hypothesis("H2", "RVOL_экстрем→reversal", "reversion",
               "экстремальный объём → откат",
               "аномальный объём = перекупленность/перепроданность, "
               "ожидание возврата к среднему",
               "отрицательная корреляция с движением после объёмных баров",
               cond=pl.col("rvol_21") > 2.0,
               expected_failure_modes="тренд перебивает откат"),
    Hypothesis("H3", "движение+объём→momentum", "momentum",
               "сильное движение с объёмом → продолжение",
               "согласие цены и объёма = подтверждение движения крупными деньгами",
               "momentum с фильтром объёма лучше сырого momentum",
               cond=(pl.col("rvol_21") > 1.5) & (pl.col("ret_w").abs() > 0.02),
               expected_failure_modes="двухдневные откаты после импульса"),
    Hypothesis("H4", "движение_без_объёма→reversal", "reversion",
               "сильное движение без объёма → откат",
               "движение без объёма — тонкий рынок, движение не подтверждено",
               "откат после сильного, но не подтверждённого объёмом движения",
               cond=(pl.col("rvol_21") < 0.8) & (pl.col("ret_w").abs() > 0.02),
               expected_failure_modes="движение на малом объёме = начало тренда"),
    Hypothesis("H5", "высокий_vol→momentum", "momentum",
               "высокая волатильность → momentum",
               "волатильные режимы склонны к продолжению (тренды живут в волатильности)",
               "momentum в режиме высокой волатильности",
               cond=pl.col("vol_high"),
               expected_failure_modes="скачки волатильности = развороты"),
    Hypothesis("H6", "низкий_vol→reversal", "reversion",
               "низкая волатильность → mean reversion",
               "в спокойном рынке цены возвращаются к среднему",
               "reversion в режиме низкой волатильности",
               cond=pl.col("vol_low"),
               expected_failure_modes="прорыв из консолидации"),
    Hypothesis("H7", "отн_объём→momentum", "momentum",
               "объём выше рынка → продолжение",
               "символ торгуется активнее рынка — фокус участников, тренд усиливается",
               "momentum у символов с объёмом выше рыночного",
               cond=pl.col("rel_vol") > 2.0,
               expected_failure_modes="кросс-секционная ротация"),
]

W_GRID = [3, 5, 10, 20]
H_GRID = [5, 10, 20]
MIN_TRADES_DISCOVERY = 300