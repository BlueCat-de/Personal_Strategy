#!/usr/bin/env python3
"""Rebuild local A-share prices with point-in-time trading metadata."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from ashare_quant.data.builder import (
    atomic_write_csv,
    atomic_write_text,
    fetch_trade_date_bundle,
)
from ashare_quant.data.tushare import (
    DEFAULT_ENV_FILE,
    fetch_namechange,
    fetch_stock_basic_all,
    is_bse,
    is_chinext,
    is_star_market,
    normalize_symbol,
    trading_dates,
)
from ashare_quant.paths import DEFAULT_MARKET_DATA_DIR


LOGGER = logging.getLogger("point_in_time_prices")
DEFAULT_OUTPUT_DIR = DEFAULT_MARKET_DATA_DIR
PRICE_COLUMNS = [
    "date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "raw_open",
    "raw_high",
    "raw_low",
    "raw_close",
    "adj_factor",
    "volume",
    "amount",
    "turnover",
    "up_limit",
    "down_limit",
    "is_suspended",
    "is_st",
    "is_listed",
    "list_date",
    "delist_date",
    "ts_code",
    "name",
]


@dataclass(frozen=True)
class RebuildConfig:
    output_dir: Path
    start_date: str
    end_date: str
    env_file: Path
    cache_dir: Path
    backup: bool
    limit: int | None


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level), format="%(asctime)s %(levelname)s %(message)s", force=True
    )


def iso_date(value: str) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def yyyymmdd_or_none(value: object) -> str | None:
    if pd.isna(value) or value in {"", "None", None}:
        return None
    return pd.to_datetime(str(value), errors="coerce").strftime("%Y-%m-%d")


def backup_output(output_dir: Path) -> str | None:
    prices_file = output_dir / "prices_long.csv"
    if not prices_file.exists():
        return None
    backup_dir = (
        output_dir.parent
        / "backups"
        / f"{output_dir.name}_before_point_in_time_rebuild_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name in [
        "prices_long.csv",
        "universe.csv",
        "daily_universe.csv",
        "manifest.json",
        "historical_backfill_2023_summary.json",
        "hybrid_update_summary.json",
    ]:
        src = output_dir / name
        if src.exists():
            shutil.copy2(src, backup_dir / name)
    return str(backup_dir)


def load_universe(config: RebuildConfig) -> pd.DataFrame:
    universe = fetch_stock_basic_all(config.env_file)
    if universe.empty:
        raise RuntimeError("Tushare stock_basic returned no rows")
    universe["symbol"] = universe["symbol"].map(normalize_symbol)
    universe = universe[
        ~universe["symbol"].map(is_chinext)
        & ~universe["symbol"].map(is_star_market)
        & ~universe["symbol"].map(is_bse)
    ].copy()
    universe["list_date"] = universe["list_date"].map(yyyymmdd_or_none)
    universe["delist_date"] = universe["delist_date"].map(yyyymmdd_or_none)
    universe = (
        universe.drop_duplicates("ts_code", keep="last")
        .sort_values("ts_code")
        .reset_index(drop=True)
    )
    if config.limit:
        universe = universe.head(config.limit).copy()
    return universe


def active_names(universe: pd.DataFrame, namechange: pd.DataFrame, date: str) -> pd.Series:
    names = universe.set_index("ts_code")["name"].astype(str).copy()
    if namechange.empty:
        return names
    dt = pd.to_datetime(date)
    changes = namechange.copy()
    changes["start"] = pd.to_datetime(changes["start_date"], errors="coerce")
    changes["end"] = pd.to_datetime(changes["end_date"], errors="coerce")
    active = changes[(changes["start"] <= dt) & (changes["end"].isna() | (changes["end"] >= dt))]
    if active.empty:
        return names
    active = active.sort_values(["ts_code", "start"]).drop_duplicates("ts_code", keep="last")
    names.update(active.set_index("ts_code")["name"].astype(str))
    return names


def build_daily_universe(
    dates: list[str], universe: pd.DataFrame, namechange: pd.DataFrame
) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    base = universe[
        [
            "ts_code",
            "symbol",
            "name",
            "market",
            "exchange",
            "list_date",
            "delist_date",
            "list_status",
        ]
    ].copy()
    for date in dates:
        listed = base[
            (pd.to_datetime(base["list_date"]) <= pd.to_datetime(date))
            & (
                base["delist_date"].isna()
                | (pd.to_datetime(base["delist_date"]) >= pd.to_datetime(date))
            )
        ].copy()
        names = active_names(listed, namechange, date)
        listed["name"] = listed["ts_code"].map(names).fillna(listed["name"])
        listed["date"] = date
        listed["is_listed"] = 1
        listed["is_st"] = (
            listed["name"].astype(str).str.upper().str.contains("ST", na=False).astype(int)
        )
        rows.append(
            listed[
                [
                    "date",
                    "symbol",
                    "ts_code",
                    "name",
                    "market",
                    "exchange",
                    "list_date",
                    "delist_date",
                    "list_status",
                    "is_listed",
                    "is_st",
                ]
            ]
        )
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def read_or_fetch_bundle(date: str, ts_codes: set[str], config: RebuildConfig) -> pd.DataFrame:
    cache_path = config.cache_dir / f"{date}.csv"
    if cache_path.exists():
        frame = pd.read_csv(cache_path, dtype={"symbol": str, "ts_code": str})
        LOGGER.info("Load cached bundle %s rows=%s", date, len(frame))
        return frame
    LOGGER.info("Fetch Tushare bundle %s", date)
    frame = fetch_trade_date_bundle(date, ts_codes, config.env_file)
    atomic_write_csv(frame, cache_path)
    return frame


def normalize_bundle(
    frame: pd.DataFrame, universe: pd.DataFrame, daily_universe: pd.DataFrame, date: str
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    result = frame.copy()
    result["date"] = pd.to_datetime(result.get("trade_date", date), errors="coerce").dt.strftime(
        "%Y-%m-%d"
    )
    result["symbol"] = result["symbol"].map(normalize_symbol)
    for col in ["raw_open", "raw_high", "raw_low", "raw_close"]:
        fallback = col.replace("raw_", "")
        if col not in result.columns and fallback in result.columns:
            result[col] = result[fallback]
    for col in [
        "raw_open",
        "raw_high",
        "raw_low",
        "raw_close",
        "volume",
        "amount",
        "turnover",
        "adj_factor",
        "up_limit",
        "down_limit",
        "is_suspended",
    ]:
        if col not in result.columns:
            result[col] = pd.NA
        result[col] = pd.to_numeric(result[col], errors="coerce")
    result["adj_factor"] = result["adj_factor"].fillna(1.0)
    for src, dst in [
        ("raw_open", "open"),
        ("raw_high", "high"),
        ("raw_low", "low"),
        ("raw_close", "close"),
    ]:
        result[dst] = result[src] * result["adj_factor"]

    meta = universe[["ts_code", "list_date", "delist_date"]].drop_duplicates("ts_code")
    result = result.merge(meta, on="ts_code", how="left")
    day_uni = daily_universe[daily_universe["date"] == date][
        ["ts_code", "name", "is_listed", "is_st"]
    ]
    result = result.merge(day_uni, on="ts_code", how="left", suffixes=("", "_pit"))
    result["is_listed"] = pd.to_numeric(result["is_listed"], errors="coerce").fillna(0).astype(int)
    result["is_st"] = pd.to_numeric(result["is_st"], errors="coerce").fillna(0).astype(int)
    result["is_suspended"] = (
        pd.to_numeric(result["is_suspended"], errors="coerce").fillna(0).astype(int)
    )
    result["name"] = result["name_pit"] if "name_pit" in result.columns else result.get("name")
    listed = result["is_listed"].eq(1)
    result = result[listed].copy()
    return (
        result[PRICE_COLUMNS]
        .dropna(subset=["date", "symbol", "raw_close", "close"])
        .drop_duplicates(["date", "symbol"], keep="last")
        .sort_values(["date", "symbol"])
        .reset_index(drop=True)
    )


def rebuild(config: RebuildConfig) -> dict:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    dates = trading_dates(config.start_date, config.end_date, config.env_file)
    universe = load_universe(config)
    namechange = fetch_namechange(config.start_date, config.end_date, config.env_file)
    daily_universe = build_daily_universe(dates, universe, namechange)
    ts_codes = set(universe["ts_code"])
    frames: list[pd.DataFrame] = []
    for index, date in enumerate(dates, start=1):
        LOGGER.info("Build point-in-time prices %s/%s %s", index, len(dates), date)
        raw = read_or_fetch_bundle(date, ts_codes, config)
        normalized = normalize_bundle(raw, universe, daily_universe, date)
        if not normalized.empty:
            frames.append(normalized)
    prices = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PRICE_COLUMNS)
    prices = (
        prices[PRICE_COLUMNS]
        .drop_duplicates(["date", "symbol"], keep="last")
        .sort_values(["date", "symbol"])
        .reset_index(drop=True)
    )
    backup_dir = backup_output(config.output_dir) if config.backup else None
    atomic_write_csv(prices, config.output_dir / "prices_long.csv")
    atomic_write_csv(daily_universe, config.output_dir / "daily_universe.csv")
    atomic_write_csv(universe, config.output_dir / "universe.csv")
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "tushare_point_in_time_rebuild",
        "status": "updated",
        "start_date": config.start_date,
        "end_date": config.end_date,
        "latest_local_date": str(prices["date"].max()) if not prices.empty else None,
        "latest_tushare_date": dates[-1] if dates else None,
        "rows": len(prices),
        "symbols": int(prices["symbol"].nunique()) if not prices.empty else 0,
        "dates": len(dates),
        "daily_universe_rows": len(daily_universe),
        "namechange_rows": len(namechange),
        "duplicate_date_symbol_rows": int(prices.duplicated(["date", "symbol"]).sum())
        if not prices.empty
        else 0,
        "backup_dir": backup_dir,
        "columns": PRICE_COLUMNS,
        "config": {
            **asdict(config),
            "output_dir": str(config.output_dir),
            "env_file": str(config.env_file),
            "cache_dir": str(config.cache_dir),
        },
    }
    atomic_write_text(
        config.output_dir / "point_in_time_rebuild_summary.json",
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild prices_long.csv with raw OHLC and point-in-time universe fields."
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--start-date", default="2020-01-01")
    parser.add_argument("--end-date", default="2026-07-16")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument(
        "--cache-dir",
        help="Per-date Tushare bundle cache. Defaults to output-dir/.tushare_backfill_cache.",
    )
    parser.add_argument("--limit", type=int, help="Limit universe for smoke tests.")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)
    output_dir = Path(args.output_dir)
    config = RebuildConfig(
        output_dir=output_dir,
        start_date=iso_date(args.start_date),
        end_date=iso_date(args.end_date),
        env_file=Path(args.env_file),
        cache_dir=Path(args.cache_dir or output_dir / ".tushare_backfill_cache"),
        backup=not args.no_backup,
        limit=args.limit,
    )
    print(json.dumps(rebuild(config), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
