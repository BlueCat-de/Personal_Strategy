#!/usr/bin/env python3
"""Generate offline A-share OHLCV data from Tushare Pro."""

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

from tushare_provider import (
    DEFAULT_ENV_FILE,
    fetch_adj_factor,
    fetch_daily,
    fetch_daily_basic,
    fetch_stock_basic,
    fetch_stk_limit,
    fetch_suspend,
    is_bse,
    is_chinext,
    is_star_market,
    normalize_symbol,
    trading_dates,
)


LOGGER = logging.getLogger("offline_a_share_tushare")
DEFAULT_OUTPUT_DIR = Path("data/offline/a_share_12m_tushare")

RUNTIME_COLUMNS = [
    "date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "turnover",
    "up_limit",
    "down_limit",
    "is_suspended",
    "ts_code",
    "name",
    "raw_open",
    "raw_high",
    "raw_low",
    "raw_close",
    "adj_factor",
]


@dataclass(frozen=True)
class TushareOfflineConfig:
    output_dir: Path
    test_start_date: str
    test_end_date: str
    fetch_start_date: str
    env_file: Path
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


def iso_date(value: str | pd.Timestamp) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def compute_date_window(end_date: str | None, months: int, warmup_days: int) -> tuple[str, str, str]:
    end = pd.Timestamp(end_date) if end_date else pd.Timestamp.today().normalize()
    test_start = end - pd.DateOffset(months=months)
    fetch_start = test_start - pd.Timedelta(days=warmup_days)
    return yyyymmdd(test_start), yyyymmdd(end), yyyymmdd(fetch_start)


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


def load_tushare_universe(config: TushareOfflineConfig) -> pd.DataFrame:
    universe = fetch_stock_basic(config.env_file).copy()
    if universe.empty:
        raise ValueError("Tushare stock_basic returned no rows")
    universe["symbol"] = universe["symbol"].map(normalize_symbol)
    if config.exclude_chinext:
        universe = universe[~universe["symbol"].map(is_chinext)]
    if config.exclude_star:
        universe = universe[~universe["symbol"].map(is_star_market)]
    if config.exclude_bse:
        universe = universe[~universe["symbol"].map(is_bse)]
    if config.exclude_st:
        universe = universe[~universe["name"].astype(str).str.upper().str.contains("ST", na=False)]
    universe = universe.sort_values("ts_code").reset_index(drop=True)
    if config.limit:
        universe = universe.head(config.limit)
    return universe[["symbol", "ts_code", "name", "market", "list_date", "exchange"]]


def fetch_trade_date_bundle(trade_date: str, universe_ts_codes: set[str], env_file: Path) -> pd.DataFrame:
    daily = fetch_daily(trade_date, env_file)
    if daily.empty:
        return pd.DataFrame(columns=RUNTIME_COLUMNS)
    daily = daily[daily["ts_code"].isin(universe_ts_codes)].copy()
    if daily.empty:
        return pd.DataFrame(columns=RUNTIME_COLUMNS)
    daily = daily.drop_duplicates(["ts_code", "trade_date"], keep="last")
    adj = fetch_adj_factor(trade_date, env_file)
    basic = fetch_daily_basic(trade_date, env_file)
    limits = fetch_stk_limit(trade_date, env_file)
    suspended = fetch_suspend(trade_date, env_file)

    for frame in [adj, basic, limits, suspended]:
        if not frame.empty:
            frame.drop_duplicates(["ts_code", "trade_date"], keep="last", inplace=True)

    adj_cols = ["ts_code", "trade_date", "adj_factor"]
    basic_cols = ["ts_code", "trade_date", "turnover_rate"]
    limit_cols = ["ts_code", "trade_date", "up_limit", "down_limit"]
    suspend_cols = ["ts_code", "trade_date", "is_suspended"]
    adj = adj.reindex(columns=adj_cols)
    basic = basic.reindex(columns=basic_cols)
    limits = limits.reindex(columns=limit_cols)
    suspended = suspended.reindex(columns=suspend_cols)

    frame = daily.merge(adj, on=["ts_code", "trade_date"], how="left")
    frame = frame.merge(basic, on=["ts_code", "trade_date"], how="left")
    frame = frame.merge(limits, on=["ts_code", "trade_date"], how="left")
    frame = frame.merge(suspended, on=["ts_code", "trade_date"], how="left")
    frame["is_suspended"] = frame["is_suspended"].fillna(0).astype(int)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    frame["symbol"] = frame["ts_code"].map(normalize_symbol)
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce") * 1000.0
    frame["turnover"] = pd.to_numeric(frame["turnover_rate"], errors="coerce") / 100.0
    frame["raw_open"] = pd.to_numeric(frame["open"], errors="coerce")
    frame["raw_high"] = pd.to_numeric(frame["high"], errors="coerce")
    frame["raw_low"] = pd.to_numeric(frame["low"], errors="coerce")
    frame["raw_close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["vol"], errors="coerce")
    frame["adj_factor"] = pd.to_numeric(frame["adj_factor"], errors="coerce").fillna(1.0)
    return frame.drop_duplicates(["trade_date", "symbol"], keep="last").reset_index(drop=True)


def apply_front_adjustment(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=RUNTIME_COLUMNS)
    result = frame.copy()
    if "trade_date" not in result.columns and "date" in result.columns:
        result["trade_date"] = result["date"]
    if "trade_date" in result.columns:
        result["trade_date"] = pd.to_datetime(result["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    if "symbol" in result.columns:
        result["symbol"] = result["symbol"].map(normalize_symbol)
    result = result.drop_duplicates(["trade_date", "symbol"], keep="last")
    result["adj_factor"] = pd.to_numeric(result["adj_factor"], errors="coerce").fillna(1.0)
    latest_adj = result.groupby("symbol", observed=True)["adj_factor"].transform("last").replace(0, 1.0).fillna(1.0)
    ratio = result["adj_factor"] / latest_adj
    for src, dst in [("raw_open", "open"), ("raw_high", "high"), ("raw_low", "low"), ("raw_close", "close")]:
        result[dst] = pd.to_numeric(result[src], errors="coerce") * ratio
    result["date"] = pd.to_datetime(result["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for col in RUNTIME_COLUMNS:
        if col not in result.columns:
            result[col] = pd.NA
    numeric_cols = [col for col in RUNTIME_COLUMNS if col not in {"date", "symbol", "ts_code", "name"}]
    for col in numeric_cols:
        result[col] = pd.to_numeric(result[col], errors="coerce")
    return (
        result[RUNTIME_COLUMNS]
        .dropna(subset=["date", "symbol", "close"])
        .sort_values(["date", "symbol"])
        .reset_index(drop=True)
    )


def save_symbol_frames(runtime_df: pd.DataFrame, output_dir: Path) -> list[dict]:
    symbol_dir = output_dir / "symbols"
    symbol_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    for symbol, frame in runtime_df.groupby("symbol", sort=True):
        path = symbol_dir / f"{symbol}.csv"
        frame = frame.sort_values("date").reset_index(drop=True)
        atomic_write_csv(frame, path)
        records.append(
            {
                "symbol": symbol,
                "rows": len(frame),
                "start_date": str(frame["date"].min()),
                "end_date": str(frame["date"].max()),
                "path": str(path),
            }
        )
    return records


def write_manifest(config: TushareOfflineConfig, universe: pd.DataFrame, runtime_df: pd.DataFrame, symbol_records: list[dict]) -> None:
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "schema": "tushare_ohlcv_cache_v1",
        "source": "tushare",
        "columns": RUNTIME_COLUMNS,
        "config": {
            **asdict(config),
            "output_dir": str(config.output_dir),
            "env_file": str(config.env_file),
        },
        "universe_count": len(universe),
        "saved_count": len(symbol_records),
        "rows": len(runtime_df),
        "symbols": int(runtime_df["symbol"].nunique()) if not runtime_df.empty else 0,
        "start_date": str(runtime_df["date"].min()) if not runtime_df.empty else None,
        "end_date": str(runtime_df["date"].max()) if not runtime_df.empty else None,
        "symbol_records": symbol_records,
        "notes": [
            "Prices are rebuilt from Tushare daily + adj_factor and stored as front-adjusted prices.",
            "volume uses Tushare daily.vol in hands.",
            "amount is converted from thousand CNY to CNY.",
            "turnover stores decimal form converted from daily_basic.turnover_rate.",
        ],
    }
    atomic_write_text(config.output_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))


def write_combined_csv(config: TushareOfflineConfig, runtime_df: pd.DataFrame) -> None:
    if not config.write_combined:
        return
    atomic_write_csv(runtime_df.sort_values(["date", "symbol"]).reset_index(drop=True), config.output_dir / "prices_long.csv")


def generate_offline_dataset(config: TushareOfflineConfig) -> None:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    universe = load_tushare_universe(config)
    atomic_write_csv(universe, config.output_dir / "universe.csv")
    ts_codes = set(universe["ts_code"])
    dates = trading_dates(iso_date(config.fetch_start_date), iso_date(config.test_end_date), config.env_file)
    LOGGER.info("Start Tushare offline generation: trade_dates=%s universe=%s output=%s", len(dates), len(universe), config.output_dir)
    frames: list[pd.DataFrame] = []
    for index, trade_date in enumerate(dates, start=1):
        LOGGER.info("Fetch Tushare trade date %s/%s %s", index, len(dates), trade_date)
        frame = fetch_trade_date_bundle(trade_date, ts_codes, config.env_file)
        if not frame.empty:
            frames.append(frame)
    merged = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if merged.empty:
        raise ValueError("No Tushare rows fetched for the requested date window")
    merged = merged.merge(universe[["ts_code", "name"]], on="ts_code", how="left", suffixes=("", "_universe"))
    merged["name"] = merged["name"].fillna(merged.pop("name_universe")) if "name_universe" in merged.columns else merged["name"]
    merged = merged.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    runtime_df = apply_front_adjustment(merged)
    write_combined_csv(config, runtime_df)
    symbol_records = save_symbol_frames(runtime_df, config.output_dir)
    write_manifest(config, universe, runtime_df, symbol_records)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate offline A-share OHLCV data from Tushare Pro.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--end-date", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--warmup-days", type=int, default=180)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--exclude-chinext", action="store_true", default=True)
    parser.add_argument("--exclude-star", action="store_true", default=True)
    parser.add_argument("--exclude-bse", action="store_true", default=True)
    parser.add_argument("--exclude-st", action="store_true", default=True)
    parser.add_argument("--no-write-combined", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)
    test_start, test_end, fetch_start = compute_date_window(args.end_date, args.months, args.warmup_days)
    config = TushareOfflineConfig(
        output_dir=Path(args.output_dir),
        test_start_date=test_start,
        test_end_date=test_end,
        fetch_start_date=fetch_start,
        env_file=Path(args.env_file),
        limit=args.limit,
        exclude_chinext=args.exclude_chinext,
        exclude_star=args.exclude_star,
        exclude_bse=args.exclude_bse,
        exclude_st=args.exclude_st,
        write_combined=not args.no_write_combined,
        overwrite=args.overwrite,
    )
    generate_offline_dataset(config)
    print(f"Output: {config.output_dir.resolve()}")


if __name__ == "__main__":
    main()
