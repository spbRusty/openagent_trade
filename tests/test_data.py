"""Контрактные тесты нормализации (ТЗ §7): валидация не молчит, ошибки в манифест,
invalid-символы не пишутся в normalized."""
import json

import polars as pl
import pytest

from data import normalize


def make_frame() -> pl.DataFrame:
    times = pl.datetime_range(pl.datetime(2025, 1, 1), pl.datetime(2025, 1, 1, 0, 4),
                              interval="1m", eager=True)
    return pl.DataFrame({"open_time": times, "open": [100.0] * 5, "high": [101.0] * 5,
                         "low": [99.0] * 5, "close": [100.5] * 5, "volume": [1.0] * 5})


def write_raw(path, df):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)
    return path


def test_valid_frame_passes_and_writes(tmp_path):
    raw = write_raw(tmp_path / "raw" / "BTCUSDT_linear_1m.parquet", make_frame())
    rep = normalize.normalize_one(raw, tmp_path / "out")
    assert rep["errors"] == []
    assert rep["rows"] == 5
    assert (tmp_path / "out" / "BTCUSDT_1m.parquet").exists()
    assert len(rep["checksum"]) == 16


def test_duplicates_flagged_and_skipped(tmp_path):
    df = pl.concat([make_frame(), make_frame()])
    raw = write_raw(tmp_path / "raw" / "DUP_linear_1m.parquet", df)
    rep = normalize.normalize_one(raw, tmp_path / "out")
    assert any("duplicates=5" in e for e in rep["errors"])
    assert not (tmp_path / "out" / "DUP_1m.parquet").exists()


def test_unsorted_flagged(tmp_path):
    df = make_frame().sort("open_time", descending=True)
    raw = write_raw(tmp_path / "raw" / "UNS_linear_1m.parquet", df)
    rep = normalize.normalize_one(raw, tmp_path / "out")
    assert "not_sorted" in rep["errors"]


def test_gaps_recorded_as_warning_but_written(tmp_path):
    df = make_frame().filter(pl.col("open_time") != pl.datetime(2025, 1, 1, 0, 2))
    raw = write_raw(tmp_path / "raw" / "GAP_linear_1m.parquet", df)
    rep = normalize.normalize_one(raw, tmp_path / "out")
    assert rep["errors"] == []
    assert any("gaps=1" in w for w in rep["warnings"])
    assert (tmp_path / "out" / "GAP_1m.parquet").exists()  # пропуски не порча


def test_bad_prices_flagged(tmp_path):
    df = make_frame().with_columns(pl.when(pl.col("open_time") == pl.datetime(2025, 1, 1, 0, 2))
                                   .then(pl.lit(50.0)).otherwise(pl.col("high")).alias("high"))
    raw = write_raw(tmp_path / "raw" / "BAD_linear_1m.parquet", df)
    rep = normalize.normalize_one(raw, tmp_path / "out")
    assert any("bad_prices=1" in e for e in rep["errors"])


def test_empty_flagged(tmp_path):
    df = make_frame().head(0)
    raw = write_raw(tmp_path / "raw" / "EMPTY_linear_1m.parquet", df)
    rep = normalize.normalize_one(raw, tmp_path / "out")
    assert "empty" in rep["errors"]


def test_manifest_separates_ok_and_failed(tmp_path):
    ok = write_raw(tmp_path / "raw" / "BTCUSDT_linear_1m.parquet", make_frame())
    bad = write_raw(tmp_path / "raw" / "DUP_linear_1m.parquet",
                    pl.concat([make_frame(), make_frame()]))
    out = normalize.run_pipeline(tmp_path / "raw", tmp_path / "out", tmp_path / "meta")
    manifest = json.loads(out.read_text())
    assert manifest["symbols"]["BTCUSDT"]["errors"] == []
    assert "DUP" in manifest["failed"]
    assert (tmp_path / "out" / "BTCUSDT_1m.parquet").exists()
    assert not (tmp_path / "out" / "DUP_1m.parquet").exists()