#!/usr/bin/env python3
"""Cache PIT financial indicators for the long-horizon large-cap universe."""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import pandas as pd

from ashare_quant.boards import BOARD_SCOPES, symbol_in_board_scope
from ashare_quant.data.tushare import DEFAULT_ENV_FILE, fetch_fina_indicator
from ashare_quant.paths import PROJECT_ROOT
from ashare_quant.research.factors import atomic_write_csv


LOGGER = logging.getLogger("long_horizon_financials")
DEFAULT_BASIC_CACHE = (
    PROJECT_ROOT / "data/offline/a_share_history_tushare/.daily_basic_monthly_cache"
)
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT / "data/offline/a_share_history_tushare/.long_horizon_fina_indicator_cache"
)
FINANCIAL_COLUMNS = [
    "roe",
    "roic",
    "grossprofit_margin",
    "netprofit_margin",
    "debt_to_assets",
    "assets_turn",
    "ocf_to_debt",
    "q_ocf_to_sales",
    "q_sales_yoy",
    "dt_netprofit_yoy",
    "ocf_yoy",
]


def historical_large_cap_universe(
    basic_cache: Path,
    start_date: str,
    end_date: str,
    board_scope: str = "main",
) -> list[str]:
    symbols: set[str] = set()
    for path in sorted(basic_cache.glob("*.csv")):
        if not start_date <= path.stem <= end_date:
            continue
        frame = pd.read_csv(path, dtype={"ts_code": str, "symbol": str})
        frame = frame[
            frame["symbol"].map(lambda symbol: symbol_in_board_scope(symbol, board_scope))
        ].copy()
        frame["total_mv"] = pd.to_numeric(frame["total_mv"], errors="coerce")
        frame = frame.dropna(subset=["ts_code", "total_mv"])
        large = frame["total_mv"].rank(pct=True) >= 2 / 3
        symbols.update(frame.loc[large, "ts_code"])
    return sorted(symbols)


def cache_financials(
    ts_codes: list[str],
    output_dir: Path,
    start_date: str,
    end_date: str,
    env_file: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, ts_code in enumerate(ts_codes, start=1):
        path = output_dir / f"{ts_code.replace('.', '_')}.csv"
        if path.exists():
            cached = pd.read_csv(path, dtype={"ts_code": str})
            cached_end = (
                pd.to_datetime(cached["ann_date"], errors="coerce").max()
                if not cached.empty and "ann_date" in cached
                else pd.NaT
            )
            if pd.notna(cached_end) and cached_end >= pd.Timestamp(end_date):
                LOGGER.info("Financial cache %s/%s %s cached", index, len(ts_codes), ts_code)
                continue
            incremental_start = (
                (cached_end + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                if pd.notna(cached_end)
                else start_date
            )
        else:
            cached = pd.DataFrame()
            incremental_start = start_date
        LOGGER.info("Financial cache %s/%s %s fetch", index, len(ts_codes), ts_code)
        frame = fetch_fina_indicator(ts_code, incremental_start, end_date, env_file)
        frame = pd.concat([cached, frame], ignore_index=True)
        if not frame.empty:
            frame = (
                frame.sort_values(["ann_date", "end_date"])
                .drop_duplicates(["ann_date", "end_date"], keep="last")
                .reset_index(drop=True)
            )
        atomic_write_csv(frame, path)
        time.sleep(0.36)


def load_financial_history(cache_dir: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(cache_dir.glob("*.csv")):
        frame = pd.read_csv(path, dtype={"ts_code": str})
        if frame.empty:
            continue
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    history = pd.concat(frames, ignore_index=True)
    history["ann_date"] = pd.to_datetime(history["ann_date"], errors="coerce")
    history["end_date"] = pd.to_datetime(history["end_date"], errors="coerce")
    history["symbol"] = history["ts_code"].str.split(".").str[0].str.zfill(6)
    for column in FINANCIAL_COLUMNS:
        history[column] = pd.to_numeric(history.get(column), errors="coerce")
    return (
        history.dropna(subset=["symbol", "ann_date", "end_date"])
        .sort_values(["symbol", "ann_date", "end_date"])
        .drop_duplicates(["symbol", "ann_date", "end_date"], keep="last")
        .reset_index(drop=True)
    )


def attach_financial_snapshots(
    panels: dict[pd.Timestamp, pd.DataFrame],
    history: pd.DataFrame,
) -> None:
    if history.empty:
        raise ValueError("Financial history is empty")
    for date, panel in panels.items():
        available = history[history["ann_date"] <= date]
        latest = (
            available.sort_values(["symbol", "end_date", "ann_date"])
            .drop_duplicates("symbol", keep="last")
            .set_index("symbol")
        )
        for column in FINANCIAL_COLUMNS:
            panel[column] = latest[column].reindex(panel.index)
        panel["low_debt"] = -panel["debt_to_assets"]
        panel["has_financials"] = (
            panel[["roe", "q_ocf_to_sales", "debt_to_assets", "dt_netprofit_yoy"]]
            .notna()
            .sum(axis=1)
            >= 3
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cache long-horizon PIT financial indicators.")
    parser.add_argument("--universe-start", default="2011-01-01")
    parser.add_argument("--universe-end", default="2020-12-31")
    parser.add_argument("--financial-start", default="2009-01-01")
    parser.add_argument("--financial-end", default="2020-12-31")
    parser.add_argument("--basic-cache", type=Path, default=DEFAULT_BASIC_CACHE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--board-scope", default="main", choices=BOARD_SCOPES)
    parser.add_argument("--codes-file", type=Path)
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )
    ts_codes = (
        [
            line.strip()
            for line in args.codes_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if args.codes_file
        else historical_large_cap_universe(
            args.basic_cache, args.universe_start, args.universe_end, args.board_scope
        )
    )
    LOGGER.info("Historical large-cap universe %s: %s symbols", args.board_scope, len(ts_codes))
    cache_financials(
        ts_codes,
        args.output_dir,
        args.financial_start,
        args.financial_end,
        args.env_file,
    )
    print(f"Cached {len(ts_codes)} symbols in {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
