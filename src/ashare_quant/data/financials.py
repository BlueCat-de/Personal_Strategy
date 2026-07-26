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
    # Extended (deployable on Ptrade: net_asset_grow_rate / roa / roe_weighted)
    "equity_yoy",
    "roa",
    "roe_waa",
]


def parse_tushare_date(series: pd.Series, column: str) -> pd.Series:
    """Parse Tushare YYYYMMDD values without treating integers as Unix nanoseconds."""

    raw = series.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
    parsed = pd.to_datetime(raw, format="%Y%m%d", errors="coerce")
    invalid = raw.notna() & raw.ne("") & parsed.isna()
    if invalid.any():
        examples = sorted(raw[invalid].unique().tolist())[:5]
        raise ValueError(f"Invalid {column} YYYYMMDD values: {examples}")
    return parsed


def normalize_financial_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize dates and prefer the original filing when revision timing is unavailable."""

    if frame.empty:
        return frame.copy()
    required = {"ts_code", "ann_date", "end_date"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Financial data missing required columns: {missing}")

    result = frame.copy()
    result["ann_date"] = parse_tushare_date(result["ann_date"], "ann_date")
    result["end_date"] = parse_tushare_date(result["end_date"], "end_date")
    result = result.dropna(subset=["ts_code", "ann_date", "end_date"])
    result = result[result["end_date"] <= result["ann_date"]].copy()

    # Tushare can return update_flag=0 and update_flag=1 for the same announcement.
    # The revised row has no separate revision timestamp, so using it historically can
    # leak a correction published later. Prefer the original row when both are present.
    if "update_flag" in result:
        flag = result["update_flag"].astype("string")
        result["_revision_priority"] = flag.map({"0": 0, "1": 1}).fillna(2)
    else:
        result["_revision_priority"] = 2
    result = (
        result.sort_values(["ts_code", "ann_date", "end_date", "_revision_priority"])
        .drop_duplicates(["ts_code", "ann_date", "end_date"], keep="first")
        .drop(columns="_revision_priority")
        .reset_index(drop=True)
    )
    return result


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


def listed_universe(
    universe_file: Path,
    start_date: str,
    end_date: str,
    board_scope: str = "main",
) -> list[str]:
    """Return all stocks listed at any point in the requested period."""

    universe = pd.read_csv(
        universe_file,
        dtype={"ts_code": str, "symbol": str, "list_date": str, "delist_date": str},
    )
    list_date = pd.to_datetime(universe["list_date"], errors="coerce")
    delist_date = pd.to_datetime(universe["delist_date"], errors="coerce")
    active = (list_date <= pd.Timestamp(end_date)) & (
        delist_date.isna() | (delist_date >= pd.Timestamp(start_date))
    )
    in_scope = universe["symbol"].map(lambda symbol: symbol_in_board_scope(symbol, board_scope))
    return sorted(universe.loc[active & in_scope, "ts_code"].dropna().unique().tolist())


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
        requested_start = pd.Timestamp(start_date)
        requested_end = pd.Timestamp(end_date)
        fetch_windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
        if path.exists():
            cached = pd.read_csv(
                path,
                dtype={
                    "ts_code": str,
                    "ann_date": str,
                    "end_date": str,
                    "update_flag": str,
                },
            )
            normalized_cache = normalize_financial_rows(cached)
            cached_period_start = (
                normalized_cache["end_date"].min() if not normalized_cache.empty else pd.NaT
            )
            cached_period_end = (
                normalized_cache["end_date"].max() if not normalized_cache.empty else pd.NaT
            )
            if pd.isna(cached_period_start) or pd.isna(cached_period_end):
                fetch_windows.append((requested_start, requested_end))
            else:
                if requested_start < cached_period_start:
                    fetch_windows.append(
                        (
                            requested_start,
                            min(requested_end, cached_period_start - pd.Timedelta(days=1)),
                        )
                    )
                if requested_end > cached_period_end:
                    fetch_windows.append(
                        (
                            max(requested_start, cached_period_end + pd.Timedelta(days=1)),
                            requested_end,
                        )
                    )
            fetch_windows = [(start, end) for start, end in fetch_windows if start <= end]
            if not fetch_windows:
                LOGGER.info("Financial cache %s/%s %s cached", index, len(ts_codes), ts_code)
                continue
        else:
            cached = pd.DataFrame()
            fetch_windows.append((requested_start, requested_end))
        fetched: list[pd.DataFrame] = []
        for window_start, window_end in fetch_windows:
            LOGGER.info(
                "Financial cache %s/%s %s fetch %s/%s",
                index,
                len(ts_codes),
                ts_code,
                window_start.date(),
                window_end.date(),
            )
            fetched.append(
                fetch_fina_indicator(
                    ts_code,
                    window_start.strftime("%Y-%m-%d"),
                    window_end.strftime("%Y-%m-%d"),
                    env_file,
                )
            )
            time.sleep(0.36)
        frame = pd.concat([cached, *fetched], ignore_index=True)
        if not frame.empty:
            frame = normalize_financial_rows(frame)
            frame["ann_date"] = frame["ann_date"].dt.strftime("%Y%m%d")
            frame["end_date"] = frame["end_date"].dt.strftime("%Y%m%d")
        atomic_write_csv(frame, path)


def load_financial_history(cache_dir: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(cache_dir.glob("*.csv")):
        frame = pd.read_csv(
            path,
            dtype={
                "ts_code": str,
                "ann_date": str,
                "end_date": str,
                "update_flag": str,
            },
        )
        if frame.empty:
            continue
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    history = normalize_financial_rows(pd.concat(frames, ignore_index=True))
    history["symbol"] = history["ts_code"].str.split(".").str[0].str.zfill(6)
    # Tushare documents fina_indicator as generally entering the service on T+1.
    # A calendar-day lag is conservative for a signal generated after market close.
    history["available_date"] = history["ann_date"] + pd.Timedelta(days=1)
    for column in FINANCIAL_COLUMNS:
        history[column] = pd.to_numeric(history.get(column), errors="coerce")
    return (
        history.dropna(subset=["symbol", "ann_date", "end_date", "available_date"])
        .sort_values(["symbol", "available_date", "end_date", "ann_date"])
        .drop_duplicates(["symbol", "ann_date", "end_date"], keep="last")
        .reset_index(drop=True)
    )


def attach_financial_snapshots(
    panels: dict[pd.Timestamp, pd.DataFrame],
    history: pd.DataFrame,
) -> None:
    if history.empty:
        raise ValueError("Financial history is empty")
    if "available_date" not in history:
        raise ValueError("Financial history is missing available_date")
    for date, panel in panels.items():
        available = history[history["available_date"] <= date]
        latest = (
            available.sort_values(["symbol", "end_date", "available_date", "ann_date"])
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
    parser.add_argument("--universe-file", type=Path)
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
        else (
            listed_universe(
                args.universe_file,
                args.universe_start,
                args.universe_end,
                args.board_scope,
            )
            if args.universe_file
            else historical_large_cap_universe(
                args.basic_cache, args.universe_start, args.universe_end, args.board_scope
            )
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
