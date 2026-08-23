"""Статистика: полный набор метрик + block bootstrap (ТЗ §9, §11)."""
import numpy as np
import math


def full_metrics(net_returns: np.ndarray, exposure: float = 1.0) -> dict:
    """Полный набор метрик по доходностям сделок/периодов.
    exposure: доля времени в рынке (0..1) — для годовой нормировки.
    """
    n = len(net_returns)
    if n == 0:
        # Полный контракт: все ключи присутствуют (нули), чтобы callers не ловили KeyError
        return {"n": 0, "mean": 0.0, "median": 0.0, "std": 0.0, "winrate": 0.0,
                "profit_factor": 0.0, "sharpe": 0.0, "sharpe_annual": 0.0,
                "total": 0.0, "max_dd": 0.0, "t_stat": 0.0}
    net = np.asarray(net_returns, dtype=float)
    wins = net[net > 0]
    losses = net[net <= 0]
    gross_win = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

    # Максимальная просадка по накопительной кривой
    cum = np.cumsum(net)
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    max_dd = float(dd.min())

    std = float(net.std()) if n > 1 else 0.0
    return {
        "n": n,
        "mean": float(net.mean()),
        "median": float(np.median(net)),
        "std": std,
        "winrate": float((net > 0).mean()),
        "profit_factor": profit_factor,
        "sharpe": float(net.mean() / std) if std > 0 else 0.0,
        "sharpe_annual": (float(net.mean() / std) * math.sqrt(252 * exposure)) if std > 0 else 0.0,
        "total": float(net.sum()),
        "max_dd": max_dd,
        "t_stat": float(net.mean() / (std / math.sqrt(n))) if std > 0 else 0.0,
    }


def block_bootstrap_pvalue(returns: np.ndarray, block: int = 21, n_iter: int = 10000,
                           seed: int = 42) -> tuple:
    """Block bootstrap (движущиеся блоки) для проверки H0: mean <= 0.
    Учитывает временную зависимость доходностей (автокорреляцию).
    Возвращает (p_value, среднее_нулевой_гипотезы, квантили)."""
    rng = np.random.default_rng(seed)
    r = np.asarray(returns, dtype=float)
    n = len(r)
    if n <= block:
        return 1.0, 0.0, None

    # Вычитаем среднее из эмпирического распределения -> центрируем под H0
    centered = r - r.mean()
    n_blocks = n // block
    # Строим пул блоков с перекрытием (движущееся окно)
    block_pool = [centered[i:i + block] for i in range(n - block + 1)]

    stats = np.empty(n_iter)
    for k in range(n_iter):
        # Выбираем n_blocks случайных (независимых) блоков с возвратом
        chosen = rng.integers(0, len(block_pool), size=n_blocks)
        sample = np.concatenate([block_pool[i] for i in chosen])
        stats[k] = sample.mean()

    obs = float(r.mean())
    p = float((stats >= obs).mean())
    return p, obs, (float(np.percentile(stats, 5)), float(np.percentile(stats, 95)))


def weekly_series(net_returns, times) -> dict:
    """Стабильность: разбиение на периоды (недели) — доля периодов в плюсе."""
    import polars as pl
    df = pl.DataFrame({"t": times, "net": net_returns})
    df = df.with_columns(pl.col("t").dt.strftime("%Y-W%V").alias("week"))
    agg = df.group_by("week").agg(pl.col("net").sum().alias("pnl"))
    pnls = agg["pnl"].to_numpy()
    return {
        "n_weeks": len(pnls),
        "weeks_positive": int((pnls > 0).sum()),
        "weeks_positive_pct": float((pnls > 0).mean()),
    }