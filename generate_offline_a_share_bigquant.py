#!/usr/bin/env python3
"""Generate offline A-share OHLCV data from BigQuant SDK.

The output schema matches the current local strategy runtime:

    date,symbol,open,high,low,close,volume,amount,turnover

This script is isolated from the production Tencent/Sina pipeline. It is meant
for side-by-side data validation before any production migration.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from bigquant_provider import (
    DEFAULT_DATASOURCE,
    fetch_bigquant_daily_history_batch,
    from_bigquant_instrument,
    init_bigquant,
    normalize_symbol,
)
from generate_offline_a_share_data import RUNTIME_COLUMNS
from stock_feature_pipeline import is_bse, is_chinext, is_star_market


LOGGER = logging.getLogger("offline_a_share_bigquant")
DEFAULT_OUTPUT_DIR = Path("data/offline/a_share_12m_bigquant")
DEFAULT_ENV_FILE = Path(__file__).resolve().parent / ".env.local"


@dataclass(frozen=True)
class BigQuantOfflineConfig:
    output_dir: Path
    test_start_date: str
    test_end_date: str
    fetch_start_date: str
    env_file: Path
    datasource: str
    adjust: str
    batch_size: int
    limit: int | None
    exclude_chinext: bool
    exclude_star: bool
    exclude_bse: bool
    exclude_st: bool
    write_combined: bool
    overwrite: bool


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )


def yyyymmdd(value: pd.Timestamp) -> str:
    return value.strftime("%Y%m%d")


def compute_date_window(end_date: str | None, months: int, warmup_days: int) -> tuple[str, str, str]:
    end = pd.Timestamp(end_date) if end_date else pd.Timestamp.today().normalize()
    test_start = end - pd.DateOffset(months=months)
    fetch_start = test_start - pd.Timedelta(days=warmup_days)
    return yyyymmdd(test_start), yyyymmdd(end), yyyymmdd(fetch_start)


def iso_date(value: str) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            tmp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def atomic_write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
            tmp_path = Path(handle.name)
            df.to_csv(handle, index=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def runtime_from_bigquant(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=RUNTIME_COLUMNS)
    result = df.rename(columns={"trade_date": "date", "turnover_rate": "turnover"}).copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    result["symbol"] = result["symbol"].map(normalize_symbol)
    for col in RUNTIME_COLUMNS:
        if col not in result.columns:
            result[col] = pd.NA
    numeric_cols = [col for col in RUNTIME_COLUMNS if col not in {"date", "symbol"}]
    for col in numeric_cols:
        result[col] = pd.to_numeric(result[col], errors="coerce")
    return (
        result[RUNTIME_COLUMNS]
        .dropna(subset=["date", "symbol", "close"])
        .sort_values(["date", "symbol"])
        .reset_index(drop=True)
    )


def load_bigquant_universe(config: BigQuantOfflineConfig) -> pd.DataFrame:
    from bigquant import dai

    end_date = iso_date(config.test_end_date)
    sql = f"""
        SELECT instrument, name
        FROM {config.datasource}
        WHERE date = '{end_date}'
        ORDER BY instrument
    """
    raw = dai.query(sql, filters={"date": [end_date, end_date]}).df()
    if raw.empty:
        raise ValueError(f"BigQuant universe query returned no rows for {end_date}")

    universe = raw.copy()
    universe["symbol"] = universe["instrument"].map(from_bigquant_instrument)
    universe["name"] = universe.get("name", "").astype(str)
    universe = universe.drop_duplicates("symbol").sort_values("symbol").reset_index(drop=True)

    if config.exclude_chinext:
        universe = universe[~universe["symbol"].map(is_chinext)]
    if config.exclude_star:
        universe = universe[~universe["symbol"].map(is_star_market)]
    if config.exclude_bse:
        universe = universe[~universe["symbol"].map(is_bse)]
    if config.exclude_st:
        universe = universe[~universe["name"].str.upper().str.contains("ST", na=False)]
    if config.limit:
        universe = universe.head(config.limit)
    return universe[["symbol", "name", "instrument"]].reset_index(drop=True)


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def save_symbol_frames(runtime_df: pd.DataFrame, output_dir: Path, overwrite: bool) -> list[dict]:
    symbol_dir = output_dir / "symbols"
    symbol_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    for symbol, frame in runtime_df.groupby("symbol", sort=True):
        symbol = normalize_symbol(symbol)
        path = symbol_dir / f"{symbol}.csv"
        if path.exists() and not overwrite:
            existing = pd.read_csv(path, dtype={"symbol": str})
            records.append(
                {
                    "symbol": symbol,
                    "rows": len(existing),
                    "start_date": str(existing["date"].min()) if "date" in existing else None,
                    "end_date": str(existing["date"].max()) if "date" in existing else None,
                    "status": "cached",
                    "path": str(path),
                }
            )
            continue

        frame = frame.sort_values("date").reset_index(drop=True)
        atomic_write_csv(frame, path)
        records.append(
            {
                "symbol": symbol,
                "rows": len(frame),
                "start_date": str(frame["date"].min()),
                "end_date": str(frame["date"].max()),
                "status": "saved",
                "path": str(path),
            }
        )
    return records


def write_manifest(config: BigQuantOfflineConfig, universe: pd.DataFrame, symbol_records: list[dict]) -> None:
    saved = [record for record in symbol_records if record["status"] in {"saved", "cached"} and record["rows"] > 0]
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "schema": "akshare_strategy_runtime_ohlcv_v1",
        "source": "bigquant",
        "datasource": config.datasource,
        "columns": RUNTIME_COLUMNS,
        "config": {
            **asdict(config),
            "output_dir": str(config.output_dir),
            "env_file": str(config.env_file),
        },
        "universe_count": len(universe),
        "saved_count": len(saved),
        "empty_or_failed_count": len(symbol_records) - len(saved),
        "symbol_records": symbol_records,
        "notes": [
            "cn_stock_bar1d stores post-adjusted prices.",
            "The adapter exports qfq by dividing post-adjusted prices by the latest adjust_factor in the query window.",
            "BigQuant volume is shares; this dataset stores volume in hands to match the existing project schema.",
        ],
        "usage": {
            "env": f"export AKSHARE_OFFLINE_DATA_DIR={config.output_dir}",
            "example": (
                "AKSHARE_OFFLINE_DATA_DIR="
                f"{config.output_dir} "
                "python3 strategies/ai_native/small_account_high_conviction_policy.py "
                "--warmup-start-date "
                f"{config.fetch_start_date} "
                "--start-date "
                f"{config.test_start_date} "
                "--end-date "
                f"{config.test_end_date}"
            ),
        },
    }
    atomic_write_text(config.output_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))


def write_combined_csv(config: BigQuantOfflineConfig, runtime_df: pd.DataFrame) -> None:
    if not config.write_combined:
        return
    if runtime_df.empty:
        LOGGER.warning("No runtime rows available for combined output.")
        return
    combined = runtime_df.sort_values(["date", "symbol"]).reset_index(drop=True)
    atomic_write_csv(combined, config.output_dir / "prices_long.csv")


def generate_offline_dataset(config: BigQuantOfflineConfig) -> None:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    init_bigquant(config.env_file)
    universe = load_bigquant_universe(config)
    atomic_write_csv(universe, config.output_dir / "universe.csv")

    symbols = universe["symbol"].tolist()
    columns_per_row = 10
    estimated_trading_days = max(1, int((pd.to_datetime(config.test_end_date) - pd.to_datetime(config.fetch_start_date)).days * 0.7))
    estimated_cells = len(symbols) * estimated_trading_days * columns_per_row
    LOGGER.info(
        "Start BigQuant offline generation: symbols=%s fetch_start=%s test_end=%s estimated_cells≈%s output=%s",
        len(symbols),
        config.fetch_start_date,
        config.test_end_date,
        f"{estimated_cells:,}",
        config.output_dir,
    )

    frames: list[pd.DataFrame] = []
    symbol_records: list[dict] = []
    fetched_symbols: set[str] = set()
    for index, batch in enumerate(chunked(symbols, config.batch_size), start=1):
        LOGGER.info("Fetch batch %s: symbols=%s", index, len(batch))
        raw = fetch_bigquant_daily_history_batch(
            batch,
            start_date=iso_date(config.fetch_start_date),
            end_date=iso_date(config.test_end_date),
            datasource=config.datasource,
            adjust=config.adjust,
            volume_unit="hand",
        )
        runtime = runtime_from_bigquant(raw)
        if not runtime.empty:
            symbol_records.extend(save_symbol_frames(runtime, config.output_dir, config.overwrite))
            fetched_symbols.update(runtime["symbol"].unique())
            frames.append(runtime)

    runtime_df = (
        pd.concat(frames, ignore_index=True).sort_values(["date", "symbol"]).reset_index(drop=True)
        if frames
        else pd.DataFrame(columns=RUNTIME_COLUMNS)
    )
    for symbol in symbols:
        if symbol not in fetched_symbols:
            symbol_records.append(
                {
                    "symbol": symbol,
                    "rows": 0,
                    "start_date": None,
                    "end_date": None,
                    "status": "empty",
                    "path": None,
                }
            )

    write_combined_csv(config, runtime_df)
    write_manifest(config, universe, symbol_records)
    LOGGER.info("Done: rows=%s symbols=%s", len(runtime_df), len(fetched_symbols))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate offline A-share data from BigQuant SDK.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output offline dataset directory.")
    parser.add_argument("--end-date", help="Dataset end date, default today. Accepts YYYY-MM-DD or YYYYMMDD.")
    parser.add_argument("--months", type=int, default=12, help="Test window length in months.")
    parser.add_argument("--warmup-days", type=int, default=180, help="Extra warm-up days before the test window.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Local env file containing BIGQUANT_API_KEY.")
    parser.add_argument("--datasource", default=DEFAULT_DATASOURCE, help="BigQuant daily bar datasource.")
    parser.add_argument("--adjust", default="qfq", choices=["raw", "qfq", "hfq"], help="Price adjustment to export.")
    parser.add_argument("--batch-size", type=int, default=100, help="Symbols per BigQuant DAI query.")
    parser.add_argument("--limit", type=int, help="Limit universe size for smoke tests.")
    parser.add_argument("--include-chinext", action="store_true", help="Include ChiNext 300/301 stocks.")
    parser.add_argument("--include-star", action="store_true", help="Include STAR Market 688/689 stocks.")
    parser.add_argument("--include-bse", action="store_true", help="Include Beijing Stock Exchange stocks.")
    parser.add_argument("--include-st", action="store_true", help="Include ST/*ST stocks.")
    parser.add_argument("--no-combined", action="store_true", help="Do not write prices_long.csv.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing symbol CSV files.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)
    test_start, test_end, fetch_start = compute_date_window(args.end_date, args.months, args.warmup_days)
    config = BigQuantOfflineConfig(
        output_dir=Path(args.output_dir),
        test_start_date=test_start,
        test_end_date=test_end,
        fetch_start_date=fetch_start,
        env_file=Path(args.env_file),
        datasource=args.datasource,
        adjust=args.adjust,
        batch_size=args.batch_size,
        limit=args.limit,
        exclude_chinext=not args.include_chinext,
        exclude_star=not args.include_star,
        exclude_bse=not args.include_bse,
        exclude_st=not args.include_st,
        write_combined=not args.no_combined,
        overwrite=args.overwrite,
    )
    generate_offline_dataset(config)


if __name__ == "__main__":
    main()
