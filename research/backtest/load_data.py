"""Загрузка 1m klines, фильтр ликвидности, сборка в один DataFrame (ТЗ §6-7).

Данные (~1.3 ГБ) лежат вне проекта — путь в config/settings.py (DATA_1M_DIR).
"""
import polars as pl

from config.settings import DATA_1M_DIR
from config.research import MIN_MEDIAN_TURNOVER, MAX_SYMBOLS


def load_symbol_turnovers() -> list[tuple[str, float]]:
    """Медианный оборот (USD/мин) по каждому символу."""
    out = []
    for f in sorted(DATA_1M_DIR.glob("*_linear_1m.parquet")):
        df = pl.read_parquet(f, columns=["turnover"])
        sym = f.name.replace("_linear_1m.parquet", "")
        out.append((sym, float(df["turnover"].median())))
    out.sort(key=lambda x: -x[1])
    return out


def load_universe() -> pl.DataFrame:
    """Топ символов по обороту -> единый DataFrame, отсортированный по времени."""
    turnovers = load_symbol_turnovers()
    keep = [s for s, t in turnovers if t >= MIN_MEDIAN_TURNOVER][:MAX_SYMBOLS]
    print(f"Юниверс: {len(keep)} символов (порог оборот >= {MIN_MEDIAN_TURNOVER:,.0f} USD/мин)")

    frames = []
    for sym in keep:
        df = pl.read_parquet(DATA_1M_DIR / f"{sym}_linear_1m.parquet")
        df = df.select(["open_time", "open", "high", "low", "close", "volume", "turnover"])
        df = df.with_columns(pl.lit(sym).alias("symbol"))
        frames.append(df)

    all_df = pl.concat(frames)
    all_df = all_df.sort(["symbol", "open_time"]).unique(
        subset=["symbol", "open_time"], keep="first"
    )
    print(f"Всего строк: {all_df.height:,}")
    return all_df


if __name__ == "__main__":
    df = load_universe()
    print(df.group_by("symbol").len().sort("len", descending=True).head(8))