"""Нормализация сырых 1m-данных (ТЗ §7).

raw → normalized без молчаливого исправления: ошибки фиксируются в манифесте
(data/metadata/1m_manifest.json), символ с ошибками в normalized не пишется.

Запуск: .venv/bin/python -m data.normalize [--limit N]
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from config.settings import DATA_1M_DIR, DATA_METADATA, DATA_NORMALIZED

REQUIRED_COLUMNS = ["open_time", "open", "high", "low", "close", "volume"]
RAW_SUFFIX = "_linear_1m.parquet"


def validate_ohlcv(df: pl.DataFrame, symbol: str) -> dict:
    """Проверки целостности. Возвращает найденные ошибки, данные не меняет.

    errors — порча данных (схема, цены, дубли, сортировка): символ отклоняется.
    warnings — отсутствующие данные (gaps): фиксируются в манифесте, символ
    остаётся — пропуски не заполняются (нельзя молча исправлять, ТЗ §7).
    """
    report = {"symbol": symbol, "rows": df.height, "warnings": []}
    if df.height == 0:
        report["errors"] = ["empty"]
        return report
    if not set(REQUIRED_COLUMNS) <= set(df.columns):
        report["errors"] = [f"bad_schema: {df.columns}"]
        return report
    errors = []
    if not df["open_time"].is_sorted():
        errors.append("not_sorted")
    dupes = df.height - df["open_time"].n_unique()
    if dupes:
        errors.append(f"duplicates={dupes}")
    bad = ((pl.col("high") < pl.col("low")) | (pl.col("open") <= 0)
           | (pl.col("high") <= 0) | (pl.col("low") <= 0) | (pl.col("close") <= 0))
    n_bad = df.filter(bad).height
    if n_bad:
        errors.append(f"bad_prices={n_bad}")
    df = df.with_columns(pl.col("open_time").diff().dt.total_seconds().alias("d"))
    gaps = df.filter(pl.col("d") > 60).height
    if gaps:
        report["warnings"].append(f"gaps={gaps}")
    report["errors"] = errors
    report["first"] = df["open_time"][0].isoformat()
    report["last"] = df["open_time"][-1].isoformat()
    return report


def normalize_one(raw_path: Path, out_dir: Path) -> dict:
    """Прочитать raw, проверить, записать normalized. Ошибки → символ не пишется."""
    symbol = raw_path.name.replace(RAW_SUFFIX, "")
    df = pl.read_parquet(raw_path)
    report = validate_ohlcv(df, symbol)
    report["checksum"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()[:16]
    if not report["errors"]:
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{symbol}_1m.parquet"
        df.write_parquet(out)
        report["normalized"] = out.name
    return report


def run_pipeline(raw_dir: Path, out_dir: Path, meta_dir: Path, limit: int | None = None) -> Path:
    files = sorted(raw_dir.glob(f"*{RAW_SUFFIX}"))
    if limit:
        files = files[:limit]
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "bybit_v5_historical",
        "timeframe": "1m",
        "raw_dir": str(raw_dir).replace(str(Path.home()), "~", 1),
        "symbols": {},
        "failed": {},
    }
    for f in files:
        rep = normalize_one(f, out_dir)
        (manifest["symbols"] if not rep["errors"] else manifest["failed"])[rep["symbol"]] = rep
    meta_dir.mkdir(parents=True, exist_ok=True)
    out = meta_dir / "1m_manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    out = run_pipeline(DATA_1M_DIR, DATA_NORMALIZED / "1m", DATA_METADATA, limit=args.limit)
    manifest = json.loads(out.read_text())
    print(f"OK: {len(manifest['symbols'])} | failed: {len(manifest['failed'])} | манифест: {out}")


if __name__ == "__main__":
    main()