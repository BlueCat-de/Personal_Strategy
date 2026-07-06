#!/usr/bin/env python3
"""
Generate offline A-share OHLCV data for AkShare-adapted strategy tests.

The generated files match the interface used by strategies/akshare_strategy_runtime.py:

    date,symbol,open,high,low,close,volume,amount,turnover

By default, the script builds a 12-month test dataset for all A-share stocks
except ChiNext codes (300/301) and STAR Market codes (688/689). It also fetches
a warm-up window before the test start date so momentum/volatility indicators
have enough history.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from stock_feature_pipeline import (
    fetch_daily_history,
    get_a_share_universe,
    is_chinext,
    is_bse,
    is_star_market,
    normalize_symbol,
)


LOGGER = logging.getLogger("offline_a_share_data")

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
]


@dataclass(frozen=True)
class OfflineDataConfig:
    output_dir: Path
    test_start_date: str
    test_end_date: str
    fetch_start_date: str
    adjust: str
    sleep_seconds: float
    exclude_chinext: bool
    exclude_star: bool
    exclude_bse: bool
    exclude_st: bool
    limit: int | None
    write_combined: bool
    overwrite: bool
    data_sources: tuple[str, ...]


def yyyymmdd(value: pd.Timestamp) -> str:
    return value.strftime("%Y%m%d")


def compute_date_window(end_date: str | None, months: int, warmup_days: int) -> tuple[str, str, str]:
    end = pd.Timestamp(end_date) if end_date else pd.Timestamp.today().normalize()
    test_start = end - pd.DateOffset(months=months)
    fetch_start = test_start - pd.Timedelta(days=warmup_days)
    return yyyymmdd(test_start), yyyymmdd(end), yyyymmdd(fetch_start)


def to_runtime_ohlcv(history: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame(columns=RUNTIME_COLUMNS)

    df = history.rename(
        columns={
            "trade_date": "date",
            "turnover_rate": "turnover",
        }
    ).copy()
    df["symbol"] = normalize_symbol(symbol)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

    for col in RUNTIME_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    numeric_cols = [col for col in RUNTIME_COLUMNS if col not in {"date", "symbol"}]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df[RUNTIME_COLUMNS].dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)


def load_universe(config: OfflineDataConfig) -> pd.DataFrame:
    universe_source = "jqdata" if "jqdata" in config.data_sources else "akshare"
    universe = get_a_share_universe(
        exclude_chinext=config.exclude_chinext,
        exclude_star=config.exclude_star,
        exclude_bse=config.exclude_bse,
        exclude_st=config.exclude_st,
        source=universe_source,
        date=config.test_end_date,
    ).copy()
    universe["symbol"] = universe["symbol"].map(normalize_symbol)

    if config.exclude_chinext:
        universe = universe[~universe["symbol"].map(is_chinext)]
    if config.exclude_star:
        universe = universe[~universe["symbol"].map(is_star_market)]
    if config.exclude_bse:
        universe = universe[~universe["symbol"].map(is_bse)]

    universe = universe.drop_duplicates("symbol").sort_values("symbol").reset_index(drop=True)
    if config.limit:
        universe = universe.head(config.limit)
    return universe


def save_symbol_data(symbol: str, config: OfflineDataConfig) -> tuple[Path | None, dict]:
    symbol_dir = config.output_dir / "symbols"
    symbol_dir.mkdir(parents=True, exist_ok=True)
    output_path = symbol_dir / f"{symbol}.csv"

    if output_path.exists() and not config.overwrite:
        existing = pd.read_csv(output_path)
        return output_path, {
            "symbol": symbol,
            "rows": len(existing),
            "start_date": str(existing["date"].min()) if "date" in existing else None,
            "end_date": str(existing["date"].max()) if "date" in existing else None,
            "status": "cached",
            "path": str(output_path),
        }

    history = fetch_daily_history(
        symbol=symbol,
        start_date=config.fetch_start_date,
        end_date=config.test_end_date,
        adjust=config.adjust,
        data_sources=config.data_sources,
    )
    runtime_df = to_runtime_ohlcv(history, symbol)
    if runtime_df.empty:
        return None, {
            "symbol": symbol,
            "rows": 0,
            "start_date": None,
            "end_date": None,
            "status": "empty",
            "path": None,
        }

    runtime_df.to_csv(output_path, index=False, encoding="utf-8")
    return output_path, {
        "symbol": symbol,
        "rows": len(runtime_df),
        "start_date": str(runtime_df["date"].min()),
        "end_date": str(runtime_df["date"].max()),
        "status": "saved",
        "path": str(output_path),
    }


def write_manifest(config: OfflineDataConfig, universe: pd.DataFrame, symbol_records: list[dict]) -> None:
    saved = [record for record in symbol_records if record["status"] in {"saved", "cached"} and record["rows"] > 0]
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "schema": "akshare_strategy_runtime_ohlcv_v1",
        "columns": RUNTIME_COLUMNS,
        "config": {
            **asdict(config),
            "output_dir": str(config.output_dir),
        },
        "universe_count": len(universe),
        "saved_count": len(saved),
        "empty_or_failed_count": len(symbol_records) - len(saved),
        "symbol_records": symbol_records,
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
    (config.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_combined_csv(config: OfflineDataConfig, symbol_records: list[dict]) -> None:
    if not config.write_combined:
        return

    frames: list[pd.DataFrame] = []
    for record in symbol_records:
        if record["status"] not in {"saved", "cached"} or not record.get("path"):
            continue
        path = Path(record["path"])
        if path.exists():
            frames.append(pd.read_csv(path, dtype={"symbol": str}))

    if not frames:
        LOGGER.warning("No symbol CSV files available for combined output.")
        return

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(["date", "symbol"]).reset_index(drop=True)
    combined.to_csv(config.output_dir / "prices_long.csv", index=False, encoding="utf-8")


def generate_offline_dataset(config: OfflineDataConfig) -> None:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    universe = load_universe(config)
    universe.to_csv(config.output_dir / "universe.csv", index=False, encoding="utf-8")

    LOGGER.info(
        "Start offline generation: universe=%s fetch_start=%s test_start=%s test_end=%s output=%s",
        len(universe),
        config.fetch_start_date,
        config.test_start_date,
        config.test_end_date,
        config.output_dir,
    )

    symbol_records: list[dict] = []
    for index, row in universe.iterrows():
        symbol = row["symbol"]
        try:
            LOGGER.info("[%s/%s] Fetch %s", index + 1, len(universe), symbol)
            _, record = save_symbol_data(symbol, config)
            symbol_records.append(record)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Failed to fetch %s: %s", symbol, exc)
            symbol_records.append(
                {
                    "symbol": symbol,
                    "rows": 0,
                    "start_date": None,
                    "end_date": None,
                    "status": "failed",
                    "error": str(exc),
                    "path": None,
                }
            )
        time.sleep(config.sleep_seconds)

    write_combined_csv(config, symbol_records)
    write_manifest(config, universe, symbol_records)
    saved_count = sum(1 for record in symbol_records if record["status"] in {"saved", "cached"} and record["rows"] > 0)
    LOGGER.info("Finished. Saved %s/%s symbol files.", saved_count, len(universe))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate offline A-share OHLCV data for AkShare-adapted strategy backtests."
    )
    parser.add_argument(
        "--output-dir",
        default="data/offline/a_share_12m",
        help="Output directory. Runtime can read this via AKSHARE_OFFLINE_DATA_DIR.",
    )
    parser.add_argument("--end-date", help="Test end date in YYYYMMDD. Default: today.")
    parser.add_argument("--months", type=int, default=12, help="Test window length in months. Default: 12.")
    parser.add_argument(
        "--warmup-days",
        type=int,
        default=180,
        help="Extra days fetched before the 12-month test window for indicators. Default: 180.",
    )
    parser.add_argument(
        "--adjust",
        default="qfq",
        choices=["", "qfq", "hfq"],
        help="Price adjustment. qfq maps to JQData pre-adjusted prices. Default: qfq.",
    )
    parser.add_argument("--sleep-seconds", type=float, default=0.3, help="Sleep between AkShare requests.")
    parser.add_argument("--limit", type=int, help="Limit symbols for smoke testing.")
    parser.add_argument("--include-chinext", action="store_true", help="Include ChiNext 300/301 stocks.")
    parser.add_argument("--include-star", action="store_true", help="Include STAR Market 688/689 stocks.")
    parser.add_argument("--include-bse", action="store_true", help="Include Beijing Stock Exchange 4/8/920 stocks.")
    parser.add_argument("--include-st", action="store_true", help="Include ST and *ST stocks. Default excludes them.")
    parser.add_argument("--exclude-st", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-combined", action="store_true", help="Do not write prices_long.csv.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing per-symbol CSV files.")
    parser.add_argument(
        "--data-sources",
        default="jqdata,tencent,eastmoney,sina",
        help="Comma-separated A-share history sources in fallback order: jqdata,tencent,eastmoney,sina.",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    test_start, test_end, fetch_start = compute_date_window(
        end_date=args.end_date,
        months=args.months,
        warmup_days=args.warmup_days,
    )
    config = OfflineDataConfig(
        output_dir=Path(args.output_dir),
        test_start_date=test_start,
        test_end_date=test_end,
        fetch_start_date=fetch_start,
        adjust=args.adjust,
        sleep_seconds=args.sleep_seconds,
        exclude_chinext=not args.include_chinext,
        exclude_star=not args.include_star,
        exclude_bse=not args.include_bse,
        exclude_st=not args.include_st,
        limit=args.limit,
        write_combined=not args.no_combined,
        overwrite=args.overwrite,
        data_sources=tuple(source.strip() for source in args.data_sources.split(",") if source.strip()),
    )
    generate_offline_dataset(config)


if __name__ == "__main__":
    main()
