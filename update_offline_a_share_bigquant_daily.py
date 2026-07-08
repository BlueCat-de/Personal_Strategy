#!/usr/bin/env python3
"""Incrementally update the local BigQuant A-share offline dataset."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from bigquant_provider import (
    DEFAULT_DATASOURCE,
    dai_query,
    fetch_bigquant_daily_history_batch,
    init_bigquant,
    normalize_symbol,
)
from generate_offline_a_share_bigquant import (
    RUNTIME_COLUMNS,
    BigQuantOfflineConfig,
    atomic_write_csv,
    atomic_write_text,
    compute_date_window,
    load_bigquant_universe,
    runtime_from_bigquant,
)


LOGGER = logging.getLogger("bigquant_daily_update")
REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = REPO_ROOT / ".env.local"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data/offline/a_share_12m_bigquant"


@dataclass(frozen=True)
class DailyUpdateConfig:
    output_dir: Path
    end_date: str
    months: int
    warmup_days: int
    env_file: Path
    datasource: str
    batch_size: int


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )


def iso_date(value: str) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def compact_date(value: str) -> str:
    return pd.to_datetime(value).strftime("%Y%m%d")


def latest_local_date(prices_file: Path) -> str | None:
    if not prices_file.exists():
        return None
    dates = pd.read_csv(prices_file, usecols=["date"])["date"]
    if dates.empty:
        return None
    return iso_date(str(dates.max()))


def missing_fetch_start(local_latest: str | None, latest_bigquant: str) -> str:
    if not local_latest:
        return latest_bigquant
    start = pd.to_datetime(local_latest) + pd.Timedelta(days=1)
    return iso_date(str(start))


def resolve_latest_bigquant_date(config: DailyUpdateConfig) -> str | None:
    end = pd.to_datetime(config.end_date)
    start = end - pd.Timedelta(days=14)
    start_iso = start.strftime("%Y-%m-%d")
    end_iso = end.strftime("%Y-%m-%d")
    sql = f"""
        SELECT date
        FROM {config.datasource}
        WHERE date >= '{start_iso}' AND date <= '{end_iso}'
        GROUP BY date
        ORDER BY date DESC
        LIMIT 1
    """
    raw = dai_query(sql, filters={"date": [start_iso, end_iso]}, env_file=config.env_file).df()
    if raw.empty:
        return None
    return iso_date(str(raw["date"].max()))


def merge_prices(output_dir: Path, runtime: pd.DataFrame, fetch_start: str) -> pd.DataFrame:
    prices_file = output_dir / "prices_long.csv"
    if prices_file.exists():
        existing = pd.read_csv(prices_file, dtype={"symbol": str})
    else:
        existing = pd.DataFrame(columns=RUNTIME_COLUMNS)

    existing["date"] = pd.to_datetime(existing["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    existing["symbol"] = existing["symbol"].map(normalize_symbol)
    runtime["date"] = pd.to_datetime(runtime["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    runtime["symbol"] = runtime["symbol"].map(normalize_symbol)

    update_dates = set(runtime["date"].dropna().unique())
    if update_dates:
        existing = existing[~existing["date"].isin(update_dates)]

    merged = pd.concat([existing, runtime], ignore_index=True)
    merged = merged[merged["date"] >= iso_date(fetch_start)]
    merged = merged.drop_duplicates(["date", "symbol"], keep="last")
    merged = merged.sort_values(["date", "symbol"]).reset_index(drop=True)
    for col in RUNTIME_COLUMNS:
        if col not in merged.columns:
            merged[col] = pd.NA
    atomic_write_csv(merged[RUNTIME_COLUMNS], prices_file)
    return merged[RUNTIME_COLUMNS]


def update_symbol_files(output_dir: Path, runtime: pd.DataFrame, fetch_start: str) -> None:
    symbol_dir = output_dir / "symbols"
    symbol_dir.mkdir(parents=True, exist_ok=True)
    for symbol, frame in runtime.groupby("symbol", sort=True):
        symbol = normalize_symbol(symbol)
        path = symbol_dir / f"{symbol}.csv"
        if path.exists():
            existing = pd.read_csv(path, dtype={"symbol": str})
        else:
            existing = pd.DataFrame(columns=RUNTIME_COLUMNS)
        existing["date"] = pd.to_datetime(existing["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        existing["symbol"] = existing["symbol"].map(normalize_symbol)
        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        dates = set(frame["date"].dropna().unique())
        existing = existing[~existing["date"].isin(dates)]
        merged = pd.concat([existing, frame], ignore_index=True)
        merged = merged[merged["date"] >= iso_date(fetch_start)]
        merged = merged.drop_duplicates(["date", "symbol"], keep="last")
        merged = merged.sort_values("date").reset_index(drop=True)
        atomic_write_csv(merged[RUNTIME_COLUMNS], path)


def write_summary(output_dir: Path, summary: dict) -> None:
    atomic_write_text(output_dir / "daily_update_summary.json", json.dumps(summary, ensure_ascii=False, indent=2, default=str))


def run_update(config: DailyUpdateConfig) -> dict:
    init_bigquant(config.env_file)
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    prices_file = output_dir / "prices_long.csv"
    local_latest = latest_local_date(prices_file)
    latest_bigquant = resolve_latest_bigquant_date(config)

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "bigquant",
        "requested_end_date": iso_date(config.end_date),
        "latest_bigquant_date": latest_bigquant,
        "previous_local_date": local_latest,
        "output_dir": str(output_dir),
    }
    if latest_bigquant is None:
        summary.update({"status": "skipped", "reason": "no_bigquant_trading_date"})
        write_summary(output_dir, summary)
        return summary
    if local_latest and pd.to_datetime(local_latest) >= pd.to_datetime(latest_bigquant):
        summary.update({"status": "skipped", "reason": "local_data_already_current", "latest_local_date": local_latest})
        write_summary(output_dir, summary)
        return summary

    backfill_start = missing_fetch_start(local_latest, latest_bigquant)
    test_start, test_end, fetch_start = compute_date_window(latest_bigquant, config.months, config.warmup_days)
    offline_config = BigQuantOfflineConfig(
        output_dir=output_dir,
        test_start_date=test_start,
        test_end_date=test_end,
        fetch_start_date=fetch_start,
        env_file=config.env_file,
        datasource=config.datasource,
        adjust="qfq",
        batch_size=config.batch_size,
        limit=None,
        exclude_chinext=True,
        exclude_star=True,
        exclude_bse=True,
        exclude_st=True,
        write_combined=True,
        overwrite=True,
        resume=False,
    )
    universe = load_bigquant_universe(offline_config)
    atomic_write_csv(universe, output_dir / "universe.csv")

    frames: list[pd.DataFrame] = []
    symbols = universe["symbol"].tolist()
    for index in range(0, len(symbols), config.batch_size):
        batch = symbols[index : index + config.batch_size]
        LOGGER.info("Fetch BigQuant daily batch %s/%s size=%s", index // config.batch_size + 1, (len(symbols) + config.batch_size - 1) // config.batch_size, len(batch))
        raw = fetch_bigquant_daily_history_batch(
            batch,
            start_date=backfill_start,
            end_date=latest_bigquant,
            datasource=config.datasource,
            adjust="qfq",
            volume_unit="hand",
        )
        runtime = runtime_from_bigquant(raw)
        if not runtime.empty:
            frames.append(runtime)

    runtime_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=RUNTIME_COLUMNS)
    if runtime_df.empty:
        summary.update({"status": "skipped", "reason": "empty_latest_bars", "latest_local_date": local_latest})
        write_summary(output_dir, summary)
        return summary

    merged = merge_prices(output_dir, runtime_df, fetch_start)
    update_symbol_files(output_dir, runtime_df, fetch_start)
    summary.update(
        {
            "status": "updated",
            "latest_local_date": latest_bigquant,
            "updated_rows": len(runtime_df),
            "updated_symbols": int(runtime_df["symbol"].nunique()),
            "updated_dates": sorted(runtime_df["date"].dropna().unique().tolist()),
            "backfill_start_date": backfill_start,
            "backfill_end_date": latest_bigquant,
            "merged_rows": len(merged),
            "merged_symbols": int(merged["symbol"].nunique()),
            "test_start_date": test_start,
            "fetch_start_date": fetch_start,
            "config": {**asdict(config), "output_dir": str(output_dir), "env_file": str(config.env_file)},
        }
    )
    write_summary(output_dir, summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incrementally update the local BigQuant offline dataset.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--end-date", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--warmup-days", type=int, default=180)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--datasource", default=DEFAULT_DATASOURCE)
    parser.add_argument("--batch-size", type=int, default=100)
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
        env_file=Path(args.env_file),
        datasource=args.datasource,
        batch_size=args.batch_size,
    )
    summary = run_update(config)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
