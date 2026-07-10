#!/usr/bin/env python3
"""Build a local A-share factor library from offline BigQuant OHLCV data.

The script intentionally depends only on the existing local runtime schema:

    date,symbol,open,high,low,close,volume,amount,turnover

It creates point-in-time factors using per-symbol rolling windows and optional
cross-sectional percentile ranks per trading date.  The first production target
is a robust, transparent quantity/price factor set that can be used for IC tests
and later strategy experiments without requiring paid BigQuant factor tables.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)
warnings.filterwarnings("ignore", category=FutureWarning, message="DataFrameGroupBy.apply operated on the grouping columns.*")

LOGGER = logging.getLogger("local_factor_library")
REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_PRICES_FILE = REPO_ROOT / "data/offline/a_share_12m_bigquant/prices_long.csv"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data/factors/local_a_share"
BASE_COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume", "amount", "turnover"]


@dataclass(frozen=True)
class FactorConfig:
    prices_file: Path
    output_dir: Path
    start_date: str | None
    end_date: str | None
    min_history_days: int
    include_rank: bool
    write_symbol_files: bool
    limit_symbols: int | None


def setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level), format="%(asctime)s %(levelname)s %(message)s", force=True)


def local_now_text() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


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


def normalize_symbol(symbol: object) -> str:
    text = str(symbol).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6)


def safe_div(numer: pd.Series, denom: pd.Series) -> pd.Series:
    denom = denom.replace(0, np.nan)
    return numer / denom


def rolling_rank_latest(window_values: np.ndarray) -> float:
    """Percentile rank of the latest value within a trailing window."""
    if len(window_values) == 0:
        return np.nan
    latest = window_values[-1]
    if not np.isfinite(latest):
        return np.nan
    valid = window_values[np.isfinite(window_values)]
    if len(valid) == 0:
        return np.nan
    return float((valid <= latest).sum() / len(valid))


def load_prices(path: Path, limit_symbols: int | None) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    LOGGER.info("Load prices: %s", path)
    df = pd.read_csv(path, dtype={"symbol": str}, parse_dates=["date"])
    missing = [col for col in BASE_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"prices file missing columns: {missing}")
    df = df[BASE_COLUMNS].copy()
    df["symbol"] = df["symbol"].map(normalize_symbol)
    numeric_cols = [col for col in BASE_COLUMNS if col not in {"date", "symbol"}]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["date", "symbol", "close"]).sort_values(["symbol", "date"]).reset_index(drop=True)
    if limit_symbols:
        symbols = sorted(df["symbol"].dropna().unique())[:limit_symbols]
        df = df[df["symbol"].isin(symbols)].reset_index(drop=True)
    return df


def add_group_factors(group: pd.DataFrame) -> pd.DataFrame:
    g = group.sort_values("date").copy()
    close = g["close"].astype(float)
    open_ = g["open"].astype(float)
    high = g["high"].astype(float)
    low = g["low"].astype(float)
    volume = g["volume"].astype(float)
    amount = g["amount"].astype(float)
    turnover = g["turnover"].astype(float)

    prev_close = close.shift(1)
    daily_return = close.pct_change()
    g["daily_return_1"] = daily_return
    g["intraday_return"] = safe_div(close, open_) - 1
    g["overnight_gap"] = safe_div(open_, prev_close) - 1
    g["high_low_range"] = safe_div(high, low) - 1
    g["close_position_in_day_range"] = safe_div(close - low, high - low)
    g["amount_per_volume"] = safe_div(amount, volume)

    for window in [5, 10, 20, 60, 120]:
        g[f"momentum_{window}"] = close / close.shift(window) - 1
        g[f"reversal_{window}"] = -g[f"momentum_{window}"]
        g[f"ma_{window}"] = close.rolling(window, min_periods=max(2, window // 2)).mean()
        g[f"price_to_ma_{window}"] = safe_div(close, g[f"ma_{window}"]) - 1
        g[f"volatility_{window}"] = daily_return.rolling(window, min_periods=max(2, window // 2)).std()
        downside = daily_return.where(daily_return < 0, 0.0)
        g[f"downside_volatility_{window}"] = downside.rolling(window, min_periods=max(2, window // 2)).std()
        roll_max = close.rolling(window, min_periods=max(2, window // 2)).max()
        roll_min = close.rolling(window, min_periods=max(2, window // 2)).min()
        g[f"drawdown_from_high_{window}"] = safe_div(close, roll_max) - 1
        g[f"range_position_{window}"] = safe_div(close - roll_min, roll_max - roll_min)
        g[f"amount_ma_{window}"] = amount.rolling(window, min_periods=max(2, window // 2)).mean()
        g[f"volume_ma_{window}"] = volume.rolling(window, min_periods=max(2, window // 2)).mean()
        g[f"turnover_ma_{window}"] = turnover.rolling(window, min_periods=max(2, window // 2)).mean()
        g[f"amount_ratio_{window}"] = safe_div(amount, g[f"amount_ma_{window}"])
        g[f"volume_ratio_{window}"] = safe_div(volume, g[f"volume_ma_{window}"])
        g[f"turnover_ratio_{window}"] = safe_div(turnover, g[f"turnover_ma_{window}"])

    for window in [5, 20, 60]:
        g[f"return_skew_{window}"] = daily_return.rolling(window, min_periods=max(3, window // 2)).skew()
        g[f"return_kurt_{window}"] = daily_return.rolling(window, min_periods=max(4, window // 2)).kurt()
        g[f"up_days_ratio_{window}"] = (daily_return > 0).astype(float).rolling(window, min_periods=max(2, window // 2)).mean()
        g[f"price_volume_corr_{window}"] = close.pct_change().rolling(window, min_periods=max(3, window // 2)).corr(volume.pct_change())
        g[f"amount_return_corr_{window}"] = daily_return.rolling(window, min_periods=max(3, window // 2)).corr(amount.pct_change())

    # Rolling percentile ranks within each stock's own time series.
    for col, window in [
        ("turnover", 60),
        ("amount", 60),
        ("volume", 60),
        ("daily_return_1", 60),
        ("high_low_range", 60),
    ]:
        g[f"ts_rank_{col}_{window}"] = g[col].rolling(window, min_periods=max(10, window // 3)).apply(rolling_rank_latest, raw=True)

    # Forward returns for research/IC analysis only.  Do not use as model features.
    for horizon in [1, 5, 10, 20]:
        g[f"fwd_return_{horizon}"] = close.shift(-horizon) / close - 1

    return g


def add_cross_sectional_ranks(df: pd.DataFrame) -> pd.DataFrame:
    rank_source_cols = [
        "momentum_5",
        "momentum_20",
        "momentum_60",
        "momentum_120",
        "reversal_5",
        "volatility_20",
        "volatility_60",
        "downside_volatility_20",
        "drawdown_from_high_20",
        "drawdown_from_high_60",
        "price_to_ma_20",
        "price_to_ma_60",
        "amount_ratio_20",
        "volume_ratio_20",
        "turnover_ma_20",
        "intraday_return",
        "overnight_gap",
        "high_low_range",
        "up_days_ratio_20",
        "price_volume_corr_20",
    ]
    available = [col for col in rank_source_cols if col in df.columns]
    if not available:
        return df
    LOGGER.info("Add cross-sectional ranks for %s columns", len(available))
    ranked = df.groupby("date", observed=True)[available].rank(pct=True, method="average")
    ranked = ranked.add_prefix("cs_rank_")
    return pd.concat([df, ranked], axis=1)


def grouped_rolling_corr(df: pd.DataFrame, left: str, right: str, window: int, min_periods: int | None = None) -> pd.Series:
    min_periods = min_periods or max(3, window // 2)
    pieces = []
    for _, group in df.groupby("symbol", sort=False, observed=True):
        pieces.append(group[left].rolling(window, min_periods=min_periods).corr(group[right]))
    return pd.concat(pieces).sort_index()


def grouped_rolling_cov(df: pd.DataFrame, left: str, right: str, window: int, min_periods: int | None = None) -> pd.Series:
    min_periods = min_periods or max(3, window // 2)
    pieces = []
    for _, group in df.groupby("symbol", sort=False, observed=True):
        pieces.append(group[left].rolling(window, min_periods=min_periods).cov(group[right]))
    return pd.concat(pieces).sort_index()


def grouped_rolling_sum(df: pd.DataFrame, col: str, window: int, min_periods: int | None = None) -> pd.Series:
    min_periods = min_periods or max(2, window // 2)
    return df.groupby("symbol", observed=True)[col].transform(lambda x: x.rolling(window, min_periods=min_periods).sum())


def grouped_rolling_std(df: pd.DataFrame, col: str, window: int, min_periods: int | None = None) -> pd.Series:
    min_periods = min_periods or max(2, window // 2)
    return df.groupby("symbol", observed=True)[col].transform(lambda x: x.rolling(window, min_periods=min_periods).std())


def grouped_shift(df: pd.DataFrame, col: str, periods: int) -> pd.Series:
    return df.groupby("symbol", observed=True)[col].shift(periods)


def grouped_ts_rank(df: pd.DataFrame, col: str, window: int, min_periods: int | None = None) -> pd.Series:
    min_periods = min_periods or max(3, window // 2)
    return df.groupby("symbol", observed=True)[col].transform(
        lambda x: x.rolling(window, min_periods=min_periods).apply(rolling_rank_latest, raw=True)
    )


def grouped_ts_argmax(df: pd.DataFrame, col: str, window: int, min_periods: int | None = None) -> pd.Series:
    min_periods = min_periods or window

    def _argmax(values: np.ndarray) -> float:
        valid = values[np.isfinite(values)]
        if len(valid) == 0:
            return np.nan
        # WorldQuant ts_argmax is 1-based within the trailing window.
        return float(np.nanargmax(values) + 1)

    return df.groupby("symbol", observed=True)[col].transform(
        lambda x: x.rolling(window, min_periods=min_periods).apply(_argmax, raw=True)
    )


def add_wq101_subset(df: pd.DataFrame) -> pd.DataFrame:
    """Add a practical WorldQuant 101-style alpha subset.

    The formulas follow common public definitions of 101 Formulaic Alphas where
    they can be computed from daily OHLCV.  True VWAP is unavailable in the
    local runtime schema, so formulas that require VWAP use a qfq-compatible
    typical-price proxy: (open + high + low + close) / 4.
    """
    LOGGER.info("Add WorldQuant 101-style alpha subset")
    result = df.sort_values(["date", "symbol"]).copy()
    close = result["close"].astype(float)
    open_ = result["open"].astype(float)
    high = result["high"].astype(float)
    low = result["low"].astype(float)
    volume = result["volume"].astype(float)
    returns = result["daily_return_1"].astype(float)
    vwap_proxy = (open_ + high + low + close) / 4.0

    result["_wq_rank_open"] = result.groupby("date", observed=True)["open"].rank(pct=True)
    result["_wq_rank_high"] = result.groupby("date", observed=True)["high"].rank(pct=True)
    result["_wq_rank_low"] = result.groupby("date", observed=True)["low"].rank(pct=True)
    result["_wq_rank_close"] = result.groupby("date", observed=True)["close"].rank(pct=True)
    result["_wq_rank_volume"] = result.groupby("date", observed=True)["volume"].rank(pct=True)
    result["_wq_intraday"] = safe_div(close - open_, open_)
    log_volume = np.log(volume.where(volume > 0))
    result["_wq_delta_log_volume_2"] = log_volume.groupby(result["symbol"]).diff(2)
    result["_wq_rank_delta_log_volume_2"] = result.groupby("date", observed=True)["_wq_delta_log_volume_2"].rank(pct=True)
    result["_wq_rank_intraday"] = result.groupby("date", observed=True)["_wq_intraday"].rank(pct=True)
    result["_wq_vwap_proxy"] = vwap_proxy

    alpha001_base = pd.Series(np.where(returns < 0, result["volatility_20"], close), index=result.index, dtype=float)
    result["_wq_alpha001_base"] = np.sign(alpha001_base) * np.abs(alpha001_base) ** 2
    result["wq101_alpha_001"] = grouped_ts_argmax(result, "_wq_alpha001_base", 5, 5).groupby(result["date"]).rank(pct=True) - 0.5

    result["wq101_alpha_002"] = -grouped_rolling_corr(result, "_wq_rank_delta_log_volume_2", "_wq_rank_intraday", 6, 3)
    result["wq101_alpha_003"] = -grouped_rolling_corr(result, "_wq_rank_open", "_wq_rank_volume", 10, 5)
    result["wq101_alpha_004"] = -grouped_ts_rank(result, "_wq_rank_low", 9, 5)
    result["wq101_alpha_006"] = -grouped_rolling_corr(result, "open", "volume", 10, 5)

    adv20 = result["volume_ma_20"]
    delta_close_7 = close - grouped_shift(result, "close", 7)
    result["_wq_abs_delta_close_7"] = delta_close_7.abs()
    result["wq101_alpha_007"] = np.where(
        adv20 < volume,
        -grouped_ts_rank(result, "_wq_abs_delta_close_7", 60, 20) * np.sign(delta_close_7),
        -1.0,
    )

    sum_open_5 = grouped_rolling_sum(result.assign(_wq_open=open_), "_wq_open", 5, 3)
    result["_wq_sum_returns_5"] = returns.groupby(result["symbol"]).transform(lambda x: x.rolling(5, min_periods=3).sum())
    alpha008_raw = sum_open_5 * result["_wq_sum_returns_5"]
    result["_wq_alpha008_raw"] = alpha008_raw - alpha008_raw.groupby(result["symbol"]).shift(10)
    result["wq101_alpha_008"] = -result.groupby("date", observed=True)["_wq_alpha008_raw"].rank(pct=True)

    result["wq101_alpha_012"] = np.sign(volume - grouped_shift(result, "volume", 1)) * -(close - grouped_shift(result, "close", 1))
    result["wq101_alpha_013"] = -grouped_rolling_cov(result, "_wq_rank_close", "_wq_rank_volume", 5, 3).groupby(result["date"]).rank(pct=True)
    result["_wq_delta_returns_3"] = returns - returns.groupby(result["symbol"]).shift(3)
    result["wq101_alpha_014"] = -result.groupby("date", observed=True)["_wq_delta_returns_3"].rank(pct=True) * grouped_rolling_corr(result, "open", "volume", 10, 5)

    result["_wq_corr_high_volume_3"] = grouped_rolling_corr(result, "_wq_rank_high", "_wq_rank_volume", 3, 3)
    result["_wq_rank_corr_high_volume_3"] = result.groupby("date", observed=True)["_wq_corr_high_volume_3"].rank(pct=True)
    result["wq101_alpha_015"] = -grouped_rolling_sum(result, "_wq_rank_corr_high_volume_3", 3, 2)
    result["wq101_alpha_016"] = -grouped_rolling_cov(result, "_wq_rank_high", "_wq_rank_volume", 5, 3).groupby(result["date"]).rank(pct=True)

    result["_wq_abs_close_open"] = (close - open_).abs()
    alpha018_raw = grouped_rolling_std(result, "_wq_abs_close_open", 5, 3) + (close - open_) + grouped_rolling_corr(result, "close", "open", 10, 5)
    result["_wq_alpha018_raw"] = alpha018_raw
    result["wq101_alpha_018"] = -result.groupby("date", observed=True)["_wq_alpha018_raw"].rank(pct=True)

    result["_wq_mean_vwap_10"] = vwap_proxy.groupby(result["symbol"]).transform(lambda x: x.rolling(10, min_periods=5).mean())
    alpha005_left = (open_ - result["_wq_mean_vwap_10"]).groupby(result["date"]).rank(pct=True)
    alpha005_right = (close - vwap_proxy).groupby(result["date"]).rank(pct=True).abs()
    result["wq101_alpha_005"] = alpha005_left * (-alpha005_right)

    wq_cols = [col for col in result.columns if col.startswith("wq101_alpha_")]
    result[wq_cols] = result[wq_cols].replace([np.inf, -np.inf], np.nan)
    temp_cols = [col for col in result.columns if col.startswith("_wq_")]
    return result.drop(columns=temp_cols)


def build_factors(config: FactorConfig) -> tuple[pd.DataFrame, dict]:
    prices = load_prices(config.prices_file, config.limit_symbols)
    input_start = str(prices["date"].min().date()) if not prices.empty else None
    input_end = str(prices["date"].max().date()) if not prices.empty else None
    LOGGER.info("Compute factors rows=%s symbols=%s dates=%s..%s", len(prices), prices["symbol"].nunique(), input_start, input_end)

    factors = prices.groupby("symbol", group_keys=False, observed=True).apply(add_group_factors)
    factors = factors.sort_values(["date", "symbol"]).reset_index(drop=True).copy()
    if config.include_rank:
        factors = add_cross_sectional_ranks(factors)
    factors = add_wq101_subset(factors)

    # Drop early rows that cannot satisfy the desired warmup horizon, then apply explicit output window.
    if config.min_history_days > 0:
        first_dates = factors.groupby("symbol", observed=True)["date"].transform("min")
        factors = factors[factors["date"] >= first_dates + pd.to_timedelta(config.min_history_days, unit="D")]
    if config.start_date:
        factors = factors[factors["date"] >= pd.to_datetime(config.start_date)]
    if config.end_date:
        factors = factors[factors["date"] <= pd.to_datetime(config.end_date)]

    factors = factors.sort_values(["date", "symbol"]).reset_index(drop=True)
    factors["date"] = factors["date"].dt.strftime("%Y-%m-%d")

    feature_cols = [col for col in factors.columns if col not in BASE_COLUMNS and not col.startswith("fwd_return_")]
    label_cols = [col for col in factors.columns if col.startswith("fwd_return_")]
    summary = {
        "generated_at": local_now_text(),
        "source": "local_bigquant_offline_ohlcv",
        "prices_file": str(config.prices_file),
        "output_dir": str(config.output_dir),
        "input_start_date": input_start,
        "input_end_date": input_end,
        "start_date": str(factors["date"].min()) if not factors.empty else None,
        "end_date": str(factors["date"].max()) if not factors.empty else None,
        "rows": int(len(factors)),
        "symbols": int(factors["symbol"].nunique()) if not factors.empty else 0,
        "feature_count": len(feature_cols),
        "label_count": len(label_cols),
        "base_columns": BASE_COLUMNS,
        "feature_columns": feature_cols,
        "label_columns_for_research_only": label_cols,
        "config": asdict(config),
    }
    summary["config"] = {k: str(v) if isinstance(v, Path) else v for k, v in summary["config"].items()}
    return factors, summary


def write_outputs(factors: pd.DataFrame, summary: dict, config: FactorConfig) -> None:
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    full_path = output_dir / "factors_long.csv"
    LOGGER.info("Write %s", full_path)
    atomic_write_csv(factors, full_path)
    atomic_write_text(output_dir / "manifest.json", json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    feature_rows = []
    for name in summary["feature_columns"]:
        if name.startswith("wq101_alpha_"):
            category = "worldquant_101"
        elif name.startswith("cs_rank_"):
            category = "cross_sectional_rank"
        elif name.startswith(("momentum", "reversal", "price_to_ma", "daily_return", "intraday", "overnight", "range_position", "drawdown")):
            category = "price_momentum_reversal"
        elif name.startswith(("volatility", "downside", "return_skew", "return_kurt", "high_low")):
            category = "risk_volatility"
        elif name.startswith(("amount", "volume", "turnover", "price_volume", "amount_return")):
            category = "liquidity_volume"
        elif name.startswith("ts_rank_"):
            category = "time_series_rank"
        else:
            category = "other"
        feature_rows.append({"feature": name, "category": category})
    atomic_write_csv(pd.DataFrame(feature_rows), output_dir / "feature_catalog.csv")

    if config.write_symbol_files:
        symbol_dir = output_dir / "symbols"
        symbol_dir.mkdir(parents=True, exist_ok=True)
        for symbol, frame in factors.groupby("symbol", sort=True, observed=True):
            atomic_write_csv(frame, symbol_dir / f"{symbol}.csv")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build local A-share OHLCV factor library.")
    parser.add_argument("--prices-file", default=str(DEFAULT_PRICES_FILE))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--start-date", default=None, help="Output start date, e.g. 2025-07-01")
    parser.add_argument("--end-date", default=None, help="Output end date, e.g. 2026-07-08")
    parser.add_argument("--min-history-days", type=int, default=120, help="Drop each symbol's earliest rows before this many calendar days of warmup.")
    parser.add_argument("--no-rank", action="store_true", help="Do not compute cross-sectional percentile ranks.")
    parser.add_argument("--write-symbol-files", action="store_true")
    parser.add_argument("--limit-symbols", type=int, default=None, help="Limit symbols for smoke tests.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)
    config = FactorConfig(
        prices_file=Path(args.prices_file),
        output_dir=Path(args.output_dir),
        start_date=args.start_date,
        end_date=args.end_date,
        min_history_days=args.min_history_days,
        include_rank=not args.no_rank,
        write_symbol_files=args.write_symbol_files,
        limit_symbols=args.limit_symbols,
    )
    factors, summary = build_factors(config)
    write_outputs(factors, summary, config)
    print(json.dumps({k: summary[k] for k in ["rows", "symbols", "feature_count", "label_count", "start_date", "end_date"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
