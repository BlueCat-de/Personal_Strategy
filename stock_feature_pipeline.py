#!/usr/bin/env python3
"""
A-share stock data extraction and feature engineering pipeline.

Data source:
    akshare, which is free and does not require an API token for common A-share data.

Install:
    pip install akshare pandas numpy

Examples:
    python stock_feature_pipeline.py --symbols 000001,600519 --start-date 20240101 --end-date 20240701
    python stock_feature_pipeline.py --all-a --start-date 20230101 --end-date 20240701 --limit 50

Output:
    CSV files under ./data/features by default. One file per stock.

Risk note:
    This script is for data research and feature generation only. It is not
    investment advice and should not be used as a standalone trading system.
"""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


LOGGER = logging.getLogger("stock_feature_pipeline")


@dataclass(frozen=True)
class PipelineConfig:
    start_date: str
    end_date: str
    adjust: str = "qfq"
    output_dir: Path = Path("data/features")
    sleep_seconds: float = 0.3
    exclude_chinext: bool = True
    exclude_st: bool = False
    limit: int | None = None


AKSHARE_COLUMNS = {
    "日期": "trade_date",
    "股票代码": "symbol",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "振幅": "amplitude",
    "涨跌幅": "pct_chg",
    "涨跌额": "change",
    "换手率": "turnover_rate",
}


def import_akshare():
    """Import akshare lazily so syntax checks do not require the dependency."""
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: akshare. Install it with: pip install akshare"
        ) from exc
    return ak


def normalize_symbol(symbol: str) -> str:
    """Keep only the 6-digit A-share code part."""
    symbol = str(symbol).strip()
    if "." in symbol:
        symbol = symbol.split(".")[0]
    return symbol.zfill(6)


def is_chinext(symbol: str) -> bool:
    """创业板股票代码通常以 300 或 301 开头。"""
    symbol = normalize_symbol(symbol)
    return symbol.startswith(("300", "301"))


def get_a_share_universe(exclude_chinext: bool = True, exclude_st: bool = False) -> pd.DataFrame:
    """Fetch A-share stock universe and apply basic tradability filters."""
    ak = import_akshare()
    universe = ak.stock_info_a_code_name()
    universe = universe.rename(columns={"code": "symbol", "name": "name"})
    universe["symbol"] = universe["symbol"].map(normalize_symbol)

    if exclude_chinext:
        universe = universe[~universe["symbol"].map(is_chinext)]

    if exclude_st:
        universe = universe[~universe["name"].astype(str).str.contains("ST", case=False, na=False)]

    return universe.reset_index(drop=True)


def fetch_daily_history(symbol: str, start_date: str, end_date: str, adjust: str = "qfq") -> pd.DataFrame:
    """
    Fetch daily adjusted A-share OHLCV data from akshare.

    adjust:
        ""    : no adjustment
        "qfq" : front-adjusted price, useful for most modeling tasks
        "hfq" : back-adjusted price
    """
    ak = import_akshare()
    symbol = normalize_symbol(symbol)
    raw = ak.stock_zh_a_hist(
        symbol=symbol,
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust=adjust,
    )

    if raw.empty:
        return pd.DataFrame()

    df = raw.rename(columns=AKSHARE_COLUMNS)
    keep_cols = [col for col in AKSHARE_COLUMNS.values() if col in df.columns]
    df = df[keep_cols].copy()
    df["symbol"] = symbol
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    numeric_cols = [col for col in df.columns if col not in {"symbol", "trade_date"}]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.sort_values("trade_date").reset_index(drop=True)


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add calendar features from trade_date."""
    out = df.copy()
    date = out["trade_date"]
    out["year"] = date.dt.year
    out["month"] = date.dt.month
    out["quarter"] = date.dt.quarter
    out["day"] = date.dt.day
    out["day_of_week"] = date.dt.dayofweek
    out["day_of_year"] = date.dt.dayofyear
    out["is_month_start"] = date.dt.is_month_start.astype(int)
    out["is_month_end"] = date.dt.is_month_end.astype(int)
    return out


def add_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add common technical indicators without relying on compiled TA libraries."""
    out = df.copy()
    close = out["close"]
    high = out["high"]
    low = out["low"]
    volume = out["volume"]

    for window in (5, 10, 20, 60):
        out[f"ma{window}"] = close.rolling(window).mean()
        out[f"ma{window}_gap"] = close / out[f"ma{window}"] - 1
        out[f"volume_ma{window}"] = volume.rolling(window).mean()
        out[f"volume_ma{window}_gap"] = volume / out[f"volume_ma{window}"] - 1

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    for window in (6, 12, 24):
        avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
        avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        out[f"rsi{window}"] = 100 - 100 / (1 + rs)

    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    out["boll_mid"] = mid
    out["boll_upper"] = mid + 2 * std
    out["boll_lower"] = mid - 2 * std
    out["boll_width"] = (out["boll_upper"] - out["boll_lower"]) / mid
    out["boll_position"] = (close - out["boll_lower"]) / (
        out["boll_upper"] - out["boll_lower"]
    ).replace(0, np.nan)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    out["macd_dif"] = ema12 - ema26
    out["macd_dea"] = out["macd_dif"].ewm(span=9, adjust=False).mean()
    out["macd_hist"] = 2 * (out["macd_dif"] - out["macd_dea"])

    low_9 = low.rolling(9).min()
    high_9 = high.rolling(9).max()
    rsv = (close - low_9) / (high_9 - low_9).replace(0, np.nan) * 100
    out["kdj_k"] = rsv.ewm(com=2, adjust=False).mean()
    out["kdj_d"] = out["kdj_k"].ewm(com=2, adjust=False).mean()
    out["kdj_j"] = 3 * out["kdj_k"] - 2 * out["kdj_d"]

    high_14 = high.rolling(14).max()
    low_14 = low.rolling(14).min()
    out["wr14"] = (high_14 - close) / (high_14 - low_14).replace(0, np.nan) * -100

    return out


def add_statistical_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add rolling return, volatility, trend, price-position, and volume features."""
    out = df.copy()
    close = out["close"]
    high = out["high"]
    low = out["low"]
    volume = out["volume"]

    out["return_1d"] = close.pct_change()
    out["log_return_1d"] = np.log(close / close.shift(1))
    out["intraday_return"] = out["close"] / out["open"] - 1
    out["high_low_spread"] = out["high"] / out["low"] - 1
    out["close_open_position"] = (out["close"] - out["open"]) / (
        out["high"] - out["low"]
    ).replace(0, np.nan)

    for window in (5, 10, 20, 60):
        rolling_return = close / close.shift(window) - 1
        rolling_high = high.rolling(window).max()
        rolling_low = low.rolling(window).min()
        rolling_range = (rolling_high - rolling_low).replace(0, np.nan)

        out[f"return_{window}d"] = rolling_return
        out[f"volatility_{window}d"] = out["return_1d"].rolling(window).std()
        out[f"trend_strength_{window}d"] = rolling_return / out[f"volatility_{window}d"].replace(
            0, np.nan
        )
        out[f"price_position_{window}d"] = (close - rolling_low) / rolling_range
        out[f"high_distance_{window}d"] = close / rolling_high - 1
        out[f"low_distance_{window}d"] = close / rolling_low - 1
        out[f"volume_change_{window}d"] = volume / volume.shift(window) - 1
        out[f"volume_zscore_{window}d"] = (
            volume - volume.rolling(window).mean()
        ) / volume.rolling(window).std().replace(0, np.nan)

    return out


def add_supervised_targets(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add future-return labels for supervised learning.

    These columns are labels, not input features. When training a model, exclude
    target_* columns from X to avoid future-information leakage.
    """
    out = df.copy()
    close = out["close"]
    for horizon in (1, 5, 10):
        out[f"target_return_{horizon}d"] = close.shift(-horizon) / close - 1
        out[f"target_up_{horizon}d"] = (out[f"target_return_{horizon}d"] > 0).astype(int)
    return out


def build_features(df: pd.DataFrame, drop_warmup_rows: bool = True) -> pd.DataFrame:
    """Build all features for one stock."""
    if df.empty:
        return df

    out = add_time_features(df)
    out = add_technical_indicators(out)
    out = add_statistical_features(out)
    out = add_supervised_targets(out)

    if drop_warmup_rows:
        # MA60 and 60-day statistics need a warm-up period.
        out = out.iloc[60:].reset_index(drop=True)

    return out


def save_features_for_symbol(symbol: str, config: PipelineConfig) -> Path | None:
    """Fetch raw data, build features, and save one CSV file."""
    if config.exclude_chinext and is_chinext(symbol):
        LOGGER.info("Skip %s because it is a ChiNext stock.", symbol)
        return None

    history = fetch_daily_history(
        symbol=symbol,
        start_date=config.start_date,
        end_date=config.end_date,
        adjust=config.adjust,
    )
    if history.empty:
        LOGGER.warning("No data returned for %s.", symbol)
        return None

    features = build_features(history)
    if features.empty:
        LOGGER.warning("Not enough rows to build features for %s.", symbol)
        return None

    config.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = config.output_dir / f"{normalize_symbol(symbol)}_features.csv"
    features.to_csv(output_path, index=False, encoding="utf-8-sig")
    LOGGER.info("Saved %s rows for %s to %s.", len(features), symbol, output_path)
    return output_path


def iter_symbols_from_args(args: argparse.Namespace) -> Iterable[str]:
    if args.symbols:
        for symbol in args.symbols.split(","):
            clean_symbol = normalize_symbol(symbol)
            if clean_symbol:
                yield clean_symbol
        return

    if args.all_a:
        universe = get_a_share_universe(
            exclude_chinext=not args.include_chinext,
            exclude_st=args.exclude_st,
        )
        if args.limit:
            universe = universe.head(args.limit)
        yield from universe["symbol"].tolist()
        return

    raise SystemExit("Please provide --symbols or --all-a.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract A-share OHLCV data and generate ML-ready features."
    )
    parser.add_argument("--symbols", help="Comma-separated 6-digit stock codes, e.g. 000001,600519.")
    parser.add_argument("--all-a", action="store_true", help="Fetch all A-share stocks after filters.")
    parser.add_argument("--start-date", required=True, help="Start date in YYYYMMDD, e.g. 20230101.")
    parser.add_argument("--end-date", required=True, help="End date in YYYYMMDD, e.g. 20240701.")
    parser.add_argument(
        "--adjust",
        default="qfq",
        choices=["", "qfq", "hfq"],
        help="Price adjustment: empty string, qfq, or hfq. Default: qfq.",
    )
    parser.add_argument("--output-dir", default="data/features", help="Output directory for CSV files.")
    parser.add_argument("--sleep-seconds", type=float, default=0.3, help="Sleep between stock requests.")
    parser.add_argument("--limit", type=int, help="Limit number of stocks when --all-a is used.")
    parser.add_argument(
        "--include-chinext",
        action="store_true",
        help="Include ChiNext stocks. Default is to exclude 300/301 codes.",
    )
    parser.add_argument("--exclude-st", action="store_true", help="Exclude ST and *ST stocks.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config = PipelineConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        adjust=args.adjust,
        output_dir=Path(args.output_dir),
        sleep_seconds=args.sleep_seconds,
        exclude_chinext=not args.include_chinext,
        exclude_st=args.exclude_st,
        limit=args.limit,
    )

    symbols = list(iter_symbols_from_args(args))
    if config.limit and args.symbols:
        symbols = symbols[: config.limit]

    LOGGER.info("Start processing %s stocks.", len(symbols))
    saved = 0
    for index, symbol in enumerate(symbols, start=1):
        try:
            LOGGER.info("[%s/%s] Processing %s", index, len(symbols), symbol)
            if save_features_for_symbol(symbol, config):
                saved += 1
        except Exception as exc:  # noqa: BLE001 - keep batch jobs running.
            LOGGER.exception("Failed to process %s: %s", symbol, exc)
        time.sleep(config.sleep_seconds)

    LOGGER.info("Finished. Saved feature files for %s/%s stocks.", saved, len(symbols))


if __name__ == "__main__":
    main()
