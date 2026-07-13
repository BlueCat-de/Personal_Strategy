#!/usr/bin/env python3
"""Append Tushare daily bars to a historical offline snapshot.

The output keeps the historical price schema:

date,symbol,open,high,low,close,volume,amount,turnover

Tushare prices are stitched to the historical adjusted-price scale by fetching
one overlap date, using the existing close on that date as the anchor,
and scaling Tushare raw_price * adj_factor for subsequent dates.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from generate_offline_a_share_tushare import atomic_write_csv, atomic_write_text, fetch_trade_date_bundle
from tushare_provider import DEFAULT_ENV_FILE, latest_open_trade_date, normalize_symbol, to_ts_code, trading_dates


LOGGER = logging.getLogger("hybrid_tushare_update")
REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data/offline/a_share_history_tushare"
DEFAULT_SOURCE_DIR = REPO_ROOT / "data/offline/a_share_history_seed"
DEFAULT_SNAPSHOT = REPO_ROOT / "packages/a_share_history_snapshot_20260709.tar.gz"
PRICE_COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume", "amount", "turnover"]


@dataclass(frozen=True)
class HybridUpdateConfig:
    output_dir: Path
    source_dir: Path
    snapshot: Path
    end_date: str
    env_file: Path
    limit: int | None
    backup: bool


def setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level), format="%(asctime)s %(levelname)s %(message)s", force=True)


def iso_date(value: str) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def load_prices(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=PRICE_COLUMNS)
    prices = pd.read_csv(path, dtype={"symbol": str})
    missing = sorted(set(PRICE_COLUMNS) - set(prices.columns))
    if missing:
        raise ValueError(f"{path} missing required columns: {missing}")
    prices = prices[PRICE_COLUMNS].copy()
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    prices["symbol"] = prices["symbol"].map(normalize_symbol)
    for col in ["open", "high", "low", "close", "volume", "amount", "turnover"]:
        prices[col] = pd.to_numeric(prices[col], errors="coerce")
    return (
        prices.dropna(subset=["date", "symbol", "close"])
        .drop_duplicates(["date", "symbol"], keep="last")
        .sort_values(["date", "symbol"])
        .reset_index(drop=True)
    )


def latest_local_date(prices: pd.DataFrame) -> str | None:
    if prices.empty:
        return None
    return iso_date(str(prices["date"].max()))


def copy_seed_dir(source_dir: Path, output_dir: Path) -> str | None:
    prices_file = source_dir / "prices_long.csv"
    if not prices_file.exists():
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ["prices_long.csv", "universe.csv", "manifest.json", "daily_update_summary.json", "historical_backfill_summary.json"]:
        src = source_dir / name
        if src.exists():
            shutil.copy2(src, output_dir / name)
    return str(source_dir)


def extract_seed_snapshot(snapshot: Path, output_dir: Path) -> str | None:
    if not snapshot.exists():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        with tarfile.open(snapshot, "r:gz") as tar:
            tar.extractall(tmp_dir)
        matches = list(tmp_dir.rglob("prices_long.csv"))
        if not matches:
            return None
        extracted = matches[0].parent
        seeded = copy_seed_dir(extracted, output_dir)
        return str(snapshot) if seeded else None


def ensure_seed_data(config: HybridUpdateConfig) -> str | None:
    if (config.output_dir / "prices_long.csv").exists():
        return "existing_output"
    seeded = extract_seed_snapshot(config.snapshot, config.output_dir)
    if seeded:
        return seeded
    return copy_seed_dir(config.source_dir, config.output_dir)


def backup_prices(output_dir: Path) -> str | None:
    prices_file = output_dir / "prices_long.csv"
    if not prices_file.exists():
        return None
    backup_dir = output_dir.parent / "backups" / f"{output_dir.name}_before_tushare_append_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name in ["prices_long.csv", "universe.csv", "manifest.json", "daily_update_summary.json", "historical_backfill_summary.json"]:
        src = output_dir / name
        if src.exists():
            shutil.copy2(src, backup_dir / name)
    return str(backup_dir)


def fetch_tushare_overlap_and_missing(dates: list[str], symbols: list[str], env_file: Path) -> pd.DataFrame:
    ts_codes = {to_ts_code(symbol) for symbol in symbols}
    ts_codes = {item for item in ts_codes if item}
    frames: list[pd.DataFrame] = []
    for index, trade_date in enumerate(dates, start=1):
        LOGGER.info("Fetch Tushare stitch date %s/%s %s", index, len(dates), trade_date)
        frame = fetch_trade_date_bundle(trade_date, ts_codes, env_file)
        if not frame.empty:
            frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def stitch_tushare_to_history_scale(existing: pd.DataFrame, raw: pd.DataFrame, overlap_date: str) -> tuple[pd.DataFrame, dict]:
    if raw.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS), {"skipped_no_anchor_symbols": 0, "anchored_symbols": 0}

    frame = raw.copy()
    frame["date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    frame["symbol"] = frame["symbol"].map(normalize_symbol)
    frame = frame.drop_duplicates(["date", "symbol"], keep="last")
    for col in ["raw_open", "raw_high", "raw_low", "raw_close", "volume", "amount", "turnover", "adj_factor"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    anchor_existing = existing[existing["date"] == overlap_date].set_index("symbol")["close"]
    overlap = frame[frame["date"] == overlap_date].copy()
    overlap["raw_adj_close"] = overlap["raw_close"] * overlap["adj_factor"]
    overlap = overlap[(overlap["raw_adj_close"] > 0) & overlap["symbol"].isin(anchor_existing.index)]
    scale = anchor_existing.reindex(overlap["symbol"]).astype(float) / overlap.set_index("symbol")["raw_adj_close"]
    scale = scale.replace([float("inf"), float("-inf")], pd.NA).dropna()

    future = frame[frame["date"] > overlap_date].copy()
    future["scale"] = future["symbol"].map(scale)
    skipped_symbols = int(future.loc[future["scale"].isna(), "symbol"].nunique())
    future = future.dropna(subset=["scale"])
    if future.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS), {"skipped_no_anchor_symbols": skipped_symbols, "anchored_symbols": int(scale.size)}

    for src, dst in [("raw_open", "open"), ("raw_high", "high"), ("raw_low", "low"), ("raw_close", "close")]:
        future[dst] = future[src] * future["adj_factor"] * future["scale"]
    result = future[PRICE_COLUMNS].copy()
    result = result.dropna(subset=["date", "symbol", "close"])
    result = result.drop_duplicates(["date", "symbol"], keep="last").sort_values(["date", "symbol"]).reset_index(drop=True)
    return result, {"skipped_no_anchor_symbols": skipped_symbols, "anchored_symbols": int(scale.size)}


def write_hybrid_summary(output_dir: Path, summary: dict) -> None:
    atomic_write_text(output_dir / "hybrid_update_summary.json", json.dumps(summary, ensure_ascii=False, indent=2, default=str))


def run_update(config: HybridUpdateConfig) -> dict:
    seed_source = ensure_seed_data(config)
    if not seed_source:
        raise FileNotFoundError(f"No seed prices found in {config.source_dir} or {config.snapshot}")

    output_dir = config.output_dir
    prices_file = output_dir / "prices_long.csv"
    existing = load_prices(prices_file)
    local_latest = latest_local_date(existing)
    latest_tushare = latest_open_trade_date(config.end_date, config.env_file)
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "history_snapshot_plus_tushare_increment",
        "seed_source": seed_source,
        "requested_end_date": iso_date(config.end_date),
        "previous_local_date": local_latest,
        "latest_tushare_date": latest_tushare,
        "output_dir": str(output_dir),
        "price_columns": PRICE_COLUMNS,
    }
    if not local_latest:
        summary.update({"status": "failed", "reason": "empty_seed_prices"})
        write_hybrid_summary(output_dir, summary)
        return summary
    if not latest_tushare:
        summary.update({"status": "skipped", "reason": "no_tushare_trading_date", "latest_local_date": local_latest})
        write_hybrid_summary(output_dir, summary)
        return summary
    if pd.to_datetime(local_latest) >= pd.to_datetime(latest_tushare):
        summary.update({"status": "skipped", "reason": "local_data_already_current", "latest_local_date": local_latest})
        write_hybrid_summary(output_dir, summary)
        return summary

    fetch_dates = trading_dates(local_latest, latest_tushare, config.env_file)
    if local_latest not in fetch_dates:
        fetch_dates = [local_latest, *fetch_dates]
    symbols = sorted(existing["symbol"].dropna().unique().tolist())
    if config.limit:
        symbols = symbols[: config.limit]
    raw = fetch_tushare_overlap_and_missing(fetch_dates, symbols, config.env_file)
    runtime, stitch_info = stitch_tushare_to_history_scale(existing, raw, local_latest)
    if runtime.empty:
        summary.update({"status": "skipped", "reason": "empty_tushare_increment_after_stitch", "latest_local_date": local_latest, **stitch_info})
        write_hybrid_summary(output_dir, summary)
        return summary

    backup_dir = backup_prices(output_dir) if config.backup else None
    update_dates = set(runtime["date"].dropna().unique())
    merged = existing[~existing["date"].isin(update_dates)].copy()
    merged = pd.concat([merged, runtime], ignore_index=True)
    merged = merged[PRICE_COLUMNS].drop_duplicates(["date", "symbol"], keep="last").sort_values(["date", "symbol"]).reset_index(drop=True)
    atomic_write_csv(merged, prices_file)
    summary.update(
        {
            "status": "updated",
            "latest_local_date": str(merged["date"].max()),
            "updated_rows": len(runtime),
            "updated_symbols": int(runtime["symbol"].nunique()),
            "updated_dates": sorted(runtime["date"].dropna().unique().tolist()),
            "backfill_start_date": sorted(runtime["date"].dropna().unique().tolist())[0],
            "backfill_end_date": latest_tushare,
            "merged_rows": len(merged),
            "merged_symbols": int(merged["symbol"].nunique()),
            "duplicate_date_symbol_rows": int(merged.duplicated(["date", "symbol"]).sum()),
            "backup_dir": backup_dir,
            **stitch_info,
            "config": {
                **asdict(config),
                "output_dir": str(config.output_dir),
                "source_dir": str(config.source_dir),
                "snapshot": str(config.snapshot),
                "env_file": str(config.env_file),
            },
        }
    )
    write_hybrid_summary(output_dir, summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Use a historical snapshot and append missing Tushare daily bars.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--source-dir", default=str(DEFAULT_SOURCE_DIR))
    parser.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT))
    parser.add_argument("--end-date", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--limit", type=int, help="Limit symbols for smoke tests.")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)
    config = HybridUpdateConfig(
        output_dir=Path(args.output_dir),
        source_dir=Path(args.source_dir),
        snapshot=Path(args.snapshot),
        end_date=iso_date(args.end_date),
        env_file=Path(args.env_file),
        limit=args.limit,
        backup=not args.no_backup,
    )
    summary = run_update(config)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
