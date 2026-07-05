#!/usr/bin/env python3
"""JQData command-line helper for A-share strategy datasets."""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import pandas as pd

from generate_offline_a_share_data import OfflineDataConfig, compute_date_window, generate_offline_dataset
from jqdata_provider import fetch_jq_daily_history, get_jq_a_share_universe, get_jq_account_status, normalize_symbol


LOGGER = logging.getLogger("jqdata_a_share_data")


def cmd_status(_: argparse.Namespace) -> None:
    status = get_jq_account_status()
    print(json.dumps(status, ensure_ascii=False, indent=2, default=str))


def cmd_universe(args: argparse.Namespace) -> None:
    universe = get_jq_a_share_universe(
        date=args.date,
        exclude_chinext=not args.include_chinext,
        exclude_star=not args.include_star,
        exclude_st=not args.include_st,
    )
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        universe.to_csv(output, index=False, encoding="utf-8")
        LOGGER.info("Saved %s symbols to %s", len(universe), output)
    else:
        print(universe.head(args.head).to_string(index=False))
        print(f"rows={len(universe)}")


def cmd_history(args: argparse.Namespace) -> None:
    df = fetch_jq_daily_history(
        symbol=args.symbol,
        start_date=args.start_date,
        end_date=args.end_date,
        adjust=args.adjust,
    )
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output, index=False, encoding="utf-8")
        LOGGER.info("Saved %s rows for %s to %s", len(df), normalize_symbol(args.symbol), output)
    else:
        print(df.head(args.head).to_string(index=False))
        print(f"rows={len(df)}")


def cmd_offline(args: argparse.Namespace) -> None:
    account_info = (get_jq_account_status().get("account_info") or {})
    account_start = account_info.get("date_range_start")
    account_end = account_info.get("date_range_end")
    end_date = args.end_date
    if not end_date and account_end and account_end != "*":
        end_date = pd.Timestamp(account_end).strftime("%Y%m%d")

    test_start, test_end, fetch_start = compute_date_window(
        end_date=end_date,
        months=args.months,
        warmup_days=args.warmup_days,
    )
    if account_start and account_start != "*":
        min_fetch_start = pd.Timestamp(account_start).strftime("%Y%m%d")
        if fetch_start < min_fetch_start:
            LOGGER.warning(
                "Clamp fetch_start_date from %s to account date_range_start %s.",
                fetch_start,
                min_fetch_start,
            )
            fetch_start = min_fetch_start

    config = OfflineDataConfig(
        output_dir=Path(args.output_dir),
        test_start_date=test_start,
        test_end_date=test_end,
        fetch_start_date=fetch_start,
        adjust=args.adjust,
        sleep_seconds=args.sleep_seconds,
        exclude_chinext=not args.include_chinext,
        exclude_star=not args.include_star,
        exclude_st=not args.include_st,
        limit=args.limit,
        write_combined=not args.no_combined,
        overwrite=args.overwrite,
        data_sources=("jqdata",),
    )
    generate_offline_dataset(config)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Use JoinQuant JQData SDK for A-share data.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Authenticate and print account/query status.")
    status.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    status.set_defaults(func=cmd_status)

    universe = subparsers.add_parser("universe", help="Fetch JQData A-share universe.")
    universe.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    universe.add_argument("--date", help="Universe date, e.g. 20260705.")
    universe.add_argument("--include-chinext", action="store_true", help="Include 300/301 ChiNext stocks.")
    universe.add_argument("--include-star", action="store_true", help="Include 688/689 STAR Market stocks.")
    universe.add_argument("--include-st", action="store_true", help="Include ST and *ST stocks. Default excludes them.")
    universe.add_argument("--exclude-st", action="store_true", help=argparse.SUPPRESS)
    universe.add_argument("--output", help="Optional CSV output path.")
    universe.add_argument("--head", type=int, default=20)
    universe.set_defaults(func=cmd_universe)

    history = subparsers.add_parser("history", help="Fetch one stock daily history from JQData.")
    history.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    history.add_argument("--symbol", required=True, help="6-digit A-share code, e.g. 002460.")
    history.add_argument("--start-date", required=True, help="Start date in YYYYMMDD or YYYY-MM-DD.")
    history.add_argument("--end-date", required=True, help="End date in YYYYMMDD or YYYY-MM-DD.")
    history.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"])
    history.add_argument("--output", help="Optional CSV output path.")
    history.add_argument("--head", type=int, default=20)
    history.set_defaults(func=cmd_history)

    offline = subparsers.add_parser("offline", help="Generate offline strategy dataset using only JQData.")
    offline.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    offline.add_argument("--output-dir", default="data/offline/a_share_12m_jqdata")
    offline.add_argument("--end-date", help="Test end date in YYYYMMDD. Default: today.")
    offline.add_argument("--months", type=int, default=12)
    offline.add_argument("--warmup-days", type=int, default=180)
    offline.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"])
    offline.add_argument("--sleep-seconds", type=float, default=0.05)
    offline.add_argument("--limit", type=int, help="Limit symbols for smoke testing.")
    offline.add_argument("--include-chinext", action="store_true")
    offline.add_argument("--include-star", action="store_true")
    offline.add_argument("--include-st", action="store_true")
    offline.add_argument("--exclude-st", action="store_true", help=argparse.SUPPRESS)
    offline.add_argument("--no-combined", action="store_true")
    offline.add_argument("--overwrite", action="store_true")
    offline.set_defaults(func=cmd_offline)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(message)s")
    args.func(args)
    time.sleep(0)


if __name__ == "__main__":
    main()
