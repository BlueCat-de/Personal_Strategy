"""Benchmark retrieval and relative performance metrics."""

from pathlib import Path

import numpy as np
import pandas as pd

from ashare_quant.data.tushare import get_pro_client
from ashare_quant.paths import DEFAULT_ENV_FILE, DEFAULT_MARKET_DATA_DIR


CSI300 = "000300.SH"
DEFAULT_BENCHMARK_FILE = DEFAULT_MARKET_DATA_DIR / "benchmark_000300.csv"


def load_benchmark_file(path: Path, start_date: str, end_date: str) -> pd.DataFrame:
    """Load a complete local benchmark slice or return an empty frame."""

    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path)
    required = {"date", "benchmark_close"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"benchmark cache missing columns: {missing}")
    frame["date"] = pd.to_datetime(frame["date"])
    frame["benchmark_close"] = pd.to_numeric(frame["benchmark_close"], errors="coerce")
    frame = frame.dropna(subset=["date", "benchmark_close"]).sort_values("date")
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    subset = frame[frame["date"].between(start, end)].copy()
    if subset.empty or subset["date"].min() > start or subset["date"].max() < end:
        return pd.DataFrame()
    return subset


def fetch_benchmark(
    start_date: str,
    end_date: str,
    *,
    env_file: Path = DEFAULT_ENV_FILE,
    ts_code: str = CSI300,
    cache_file: Path | None = DEFAULT_BENCHMARK_FILE,
) -> pd.DataFrame:
    if cache_file is not None:
        cached = load_benchmark_file(cache_file, start_date, end_date)
        if not cached.empty:
            return cached
    pro = get_pro_client(env_file)
    frame = pro.index_daily(
        ts_code=ts_code,
        start_date=pd.to_datetime(start_date).strftime("%Y%m%d"),
        end_date=pd.to_datetime(end_date).strftime("%Y%m%d"),
        fields="trade_date,close",
    )
    if frame.empty:
        raise RuntimeError(f"Tushare index_daily returned no rows for {ts_code}")
    frame["date"] = pd.to_datetime(frame["trade_date"])
    return frame[["date", "close"]].sort_values("date").rename(columns={"close": "benchmark_close"})


def max_drawdown(nav: pd.Series) -> float:
    return float((nav / nav.cummax() - 1.0).min()) if not nav.empty else 0.0


def benchmark_metrics(
    raw_perf: pd.DataFrame,
    benchmark: pd.DataFrame,
    start_date: str,
    initial_cash: float,
) -> dict:
    frame = raw_perf.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.merge(benchmark, on="date", how="inner")
    frame = frame[frame["date"] >= pd.to_datetime(start_date)].copy()
    if frame.empty:
        raise ValueError("Strategy and benchmark have no overlapping observations")
    strategy_nav = frame["portfolio_value"] / frame["portfolio_value"].iloc[0]
    benchmark_nav = frame["benchmark_close"] / frame["benchmark_close"].iloc[0]
    strategy_return = strategy_nav.pct_change().fillna(0.0)
    benchmark_return = benchmark_nav.pct_change().fillna(0.0)
    excess = strategy_return - benchmark_return
    return {
        "days": int(len(frame)),
        "strategy_total_return": float(strategy_nav.iloc[-1] - 1.0),
        "benchmark_total_return": float(benchmark_nav.iloc[-1] - 1.0),
        "excess_total_return": float(strategy_nav.iloc[-1] - benchmark_nav.iloc[-1]),
        "sharpe": float(strategy_return.mean() / strategy_return.std(ddof=1) * np.sqrt(252))
        if strategy_return.std(ddof=1) > 0
        else 0.0,
        "information_ratio": float(excess.mean() / excess.std(ddof=1) * np.sqrt(252))
        if excess.std(ddof=1) > 0
        else 0.0,
        "max_drawdown": max_drawdown(strategy_nav),
        "benchmark_max_drawdown": max_drawdown(benchmark_nav),
        "active_day_ratio": float((frame["gross_leverage"] > 0).mean()),
        "avg_gross_leverage": float(frame["gross_leverage"].mean()),
        "turnover_on_initial_cash": float(
            (frame["today_sum_buy_value"].sum() + frame["today_sum_sell_value"].sum())
            / initial_cash
        ),
    }
