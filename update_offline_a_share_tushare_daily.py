#!/usr/bin/env python3
"""Incrementally update the local Tushare A-share offline dataset."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from generate_offline_a_share_tushare import (
    RUNTIME_COLUMNS,
    TushareOfflineConfig,
    apply_front_adjustment,
    atomic_write_csv,
    atomic_write_text,
    compute_date_window,
    fetch_trade_date_bundle,
    load_tushare_universe,
)
from tushare_provider import DEFAULT_ENV_FILE, latest_open_trade_date, normalize_symbol, trading_dates


LOGGER = logging.getLogger("tushare_daily_update")
REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data/offline/a_share_12m_tushare"


@dataclass(frozen=True)
class DailyUpdateConfig:
    output_dir: Path
    end_date: str
    months: int
    warmup_days: int
    min_start_date: str | None
    env_file: Path
    limit: int | None


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )


def iso_date(value: str) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def latest_local_date(prices_file: Path) -> str | None:
    if not prices_file.exists():
        return None
    try:
        dates = pd.read_csv(prices_file, usecols=["date"])["date"]
    except Exception:
        return None
    if dates.empty:
        return None
    return iso_date(str(dates.max()))


def retention_start(fetch_start: str, min_start_date: str | None) -> str:
    start = iso_date(fetch_start)
    if min_start_date:
        start = min(start, iso_date(min_start_date))
    return start


def load_existing_prices(prices_file: Path) -> pd.DataFrame:
    if not prices_file.exists():
        return pd.DataFrame(columns=RUNTIME_COLUMNS)
    existing = pd.read_csv(prices_file, dtype={"symbol": str, "ts_code": str})
    for col in RUNTIME_COLUMNS:
        if col not in existing.columns:
            if col in {"raw_open", "raw_high", "raw_low", "raw_close"}:
                fallback = col.replace("raw_", "")
                existing[col] = existing.get(fallback)
            elif col == "adj_factor":
                existing[col] = 1.0
            elif col == "ts_code":
                existing[col] = existing["symbol"].map(lambda s: f"{normalize_symbol(s)}.SZ")
            elif col == "name":
                existing[col] = pd.NA
            else:
                existing[col] = pd.NA
    existing["symbol"] = existing["symbol"].map(normalize_symbol)
    existing["date"] = pd.to_datetime(existing["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return existing


def merge_prices(output_dir: Path, runtime: pd.DataFrame, keep_start: str) -> pd.DataFrame:
    prices_file = output_dir / "prices_long.csv"
    existing = load_existing_prices(prices_file)
    update_dates = set(runtime["date"].dropna().unique())
    if update_dates:
        existing = existing[~existing["date"].isin(update_dates)]
    combined = pd.concat([existing, runtime], ignore_index=True)
    combined = combined[combined["date"] >= iso_date(keep_start)].copy()
    combined = combined.drop_duplicates(["date", "symbol"], keep="last")
    combined = combined.sort_values(["symbol", "date"]).reset_index(drop=True)
    combined["trade_date"] = combined["date"]
    adjusted = apply_front_adjustment(combined.rename(columns={"date": "trade_date"})).rename(columns={"trade_date": "date"})
    adjusted = adjusted.sort_values(["date", "symbol"]).reset_index(drop=True)
    atomic_write_csv(adjusted, prices_file)
    return adjusted


def update_symbol_files(output_dir: Path, runtime: pd.DataFrame) -> None:
    symbol_dir = output_dir / "symbols"
    symbol_dir.mkdir(parents=True, exist_ok=True)
    for symbol, frame in runtime.groupby("symbol", sort=True):
        path = symbol_dir / f"{symbol}.csv"
        atomic_write_csv(frame.sort_values("date").reset_index(drop=True), path)


def write_summary(output_dir: Path, summary: dict) -> None:
    atomic_write_text(output_dir / "daily_update_summary.json", json.dumps(summary, ensure_ascii=False, indent=2, default=str))


def run_update(config: DailyUpdateConfig) -> dict:
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    prices_file = output_dir / "prices_long.csv"
    local_latest = latest_local_date(prices_file)
    latest_tushare = latest_open_trade_date(config.end_date, config.env_file)

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "tushare",
        "requested_end_date": iso_date(config.end_date),
        "latest_tushare_date": latest_tushare,
        "previous_local_date": local_latest,
        "output_dir": str(output_dir),
    }
    if latest_tushare is None:
        summary.update({"status": "skipped", "reason": "no_tushare_trading_date"})
        write_summary(output_dir, summary)
        return summary
    if local_latest and pd.to_datetime(local_latest) >= pd.to_datetime(latest_tushare):
        summary.update({"status": "skipped", "reason": "local_data_already_current", "latest_local_date": local_latest})
        write_summary(output_dir, summary)
        return summary

    test_start, test_end, fetch_start = compute_date_window(latest_tushare, config.months, config.warmup_days)
    keep_start = retention_start(fetch_start, config.min_start_date)
    offline_config = TushareOfflineConfig(
        output_dir=output_dir,
        test_start_date=test_start,
        test_end_date=test_end,
        fetch_start_date=fetch_start,
        env_file=config.env_file,
        limit=config.limit,
        exclude_chinext=True,
        exclude_star=True,
        exclude_bse=True,
        exclude_st=True,
        write_combined=True,
        overwrite=True,
    )
    universe = load_tushare_universe(offline_config)
    atomic_write_csv(universe, output_dir / "universe.csv")
    ts_codes = set(universe["ts_code"])

    fetch_start_date = iso_date(pd.to_datetime(local_latest).strftime("%Y-%m-%d")) if local_latest else iso_date(fetch_start)
    if local_latest:
        missing_dates = trading_dates((pd.to_datetime(local_latest) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"), latest_tushare, config.env_file)
    else:
        missing_dates = trading_dates(iso_date(fetch_start), latest_tushare, config.env_file)
    frames: list[pd.DataFrame] = []
    for index, trade_date in enumerate(missing_dates, start=1):
        LOGGER.info("Fetch Tushare missing trade date %s/%s %s", index, len(missing_dates), trade_date)
        frame = fetch_trade_date_bundle(trade_date, ts_codes, config.env_file)
        if not frame.empty:
            frame = frame.merge(universe[["ts_code", "name"]], on="ts_code", how="left", suffixes=("", "_universe"))
            frame["name"] = frame["name"].fillna(frame.pop("name_universe")) if "name_universe" in frame.columns else frame["name"]
            frame = apply_front_adjustment(frame.sort_values(["symbol", "trade_date"]).reset_index(drop=True))
            frames.append(frame)
    runtime_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=RUNTIME_COLUMNS)
    if runtime_df.empty:
        summary.update({"status": "skipped", "reason": "empty_latest_bars", "latest_local_date": local_latest})
        write_summary(output_dir, summary)
        return summary

    merged = merge_prices(output_dir, runtime_df, keep_start)
    update_symbol_files(output_dir, merged)
    summary.update(
        {
            "status": "updated",
            "latest_local_date": str(merged["date"].max()) if not merged.empty else local_latest,
            "updated_rows": len(runtime_df),
            "updated_symbols": int(runtime_df["symbol"].nunique()) if not runtime_df.empty else 0,
            "updated_dates": sorted(runtime_df["date"].dropna().unique().tolist()),
            "backfill_start_date": fetch_start_date,
            "backfill_end_date": latest_tushare,
            "merged_rows": len(merged),
            "merged_symbols": int(merged["symbol"].nunique()) if not merged.empty else 0,
            "test_start_date": test_start,
            "fetch_start_date": fetch_start,
            "retention_start_date": keep_start,
            "config": {
                **asdict(config),
                "output_dir": str(output_dir),
                "env_file": str(config.env_file),
            },
        }
    )
    write_summary(output_dir, summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incrementally update the local Tushare offline dataset.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--end-date", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--warmup-days", type=int, default=180)
    parser.add_argument("--min-start-date", help="Keep rows from at least this date when merging updates.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)
    config = DailyUpdateConfig(
        output_dir=Path(args.output_dir),
        end_date=iso_date(args.end_date),
        months=args.months,
        warmup_days=args.warmup_days,
        min_start_date=iso_date(args.min_start_date) if args.min_start_date else None,
        env_file=Path(args.env_file),
        limit=args.limit,
    )
    summary = run_update(config)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()