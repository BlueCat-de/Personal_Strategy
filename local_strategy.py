#!/usr/bin/env python3
"""Local-only A-share strategy runner using Tushare offline data."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from local_backtest import BacktestConfig, run_local_backtest


LOGGER = logging.getLogger("local_strategy")
REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_PRICES_FILE = REPO_ROOT / "data/offline/a_share_history_tushare/prices_long.csv"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data/backtests/local_strategy"


@dataclass(frozen=True)
class StrategyConfig:
    start_date: str
    end_date: str
    warmup_start_date: str
    initial_cash: float
    max_positions: int
    min_candidates: int
    max_position_weight: float
    strong_total_weight: float
    neutral_total_weight: float
    min_price: float
    max_price: float
    min_mom20: float
    min_mom60: float
    max_mom20: float
    max_mom5: float
    max_vol20: float
    max_drawdown60: float
    max_signal_gap: float
    min_breadth20: float
    min_breadth60: float
    max_market_vol20: float
    max_weak_drawdown_ratio: float
    stop_loss: float
    trailing_stop: float
    trend_exit_window: int
    strategy_version: str
    min_amount20: float
    min_turnover20: float
    max_turnover20: float
    min_volume_ratio20: float
    max_volume_ratio20: float
    min_amount_trend60: float
    buy_cost: float
    sell_cost: float
    min_cost: float
    prices_file: Path
    output_dir: Path


def setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level), format="%(asctime)s %(levelname)s %(message)s", force=True)


def iso_date(value: str) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


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


def load_prices(path: Path, cfg: StrategyConfig) -> dict[str, pd.DataFrame]:
    if not path.exists():
        raise FileNotFoundError(f"prices file not found: {path}")
    bars = pd.read_csv(path, dtype={"symbol": str, "ts_code": str})
    required = {"date", "symbol", "open", "close", "volume", "amount", "turnover"}
    missing = sorted(required - set(bars.columns))
    if missing:
        raise ValueError(f"prices file missing required columns: {missing}")
    bars["date"] = pd.to_datetime(bars["date"])
    bars["symbol"] = bars["symbol"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    bars = bars[(bars["date"] >= pd.to_datetime(cfg.warmup_start_date)) & (bars["date"] <= pd.to_datetime(cfg.end_date))].copy()
    bars = bars.sort_values(["date", "symbol"]).drop_duplicates(["date", "symbol"], keep="last")
    numeric_cols = [col for col in ["open", "high", "low", "close", "volume", "amount", "turnover", "up_limit", "down_limit", "is_suspended"] if col in bars.columns]
    for col in numeric_cols:
        bars[col] = pd.to_numeric(bars[col], errors="coerce")
    pivots: dict[str, pd.DataFrame] = {}
    for col in ["open", "close", "volume", "amount", "turnover", "up_limit", "down_limit", "is_suspended"]:
        if col in bars.columns:
            pivots[col] = bars.pivot(index="date", columns="symbol", values=col).sort_index()
    if "close" not in pivots or pivots["close"].empty:
        raise ValueError(f"prices file has no usable close data in {cfg.warmup_start_date} ~ {cfg.end_date}")
    pivots["open"] = pivots["open"].fillna(pivots["close"])
    if "volume" not in pivots:
        pivots["volume"] = pd.DataFrame(0.0, index=pivots["close"].index, columns=pivots["close"].columns)
    if "amount" not in pivots:
        pivots["amount"] = pivots["open"] * pivots["volume"] * 100.0
    if "turnover" not in pivots:
        pivots["turnover"] = pd.DataFrame(np.nan, index=pivots["close"].index, columns=pivots["close"].columns)
    if "up_limit" not in pivots:
        pivots["up_limit"] = pd.DataFrame(np.nan, index=pivots["close"].index, columns=pivots["close"].columns)
    if "down_limit" not in pivots:
        pivots["down_limit"] = pd.DataFrame(np.nan, index=pivots["close"].index, columns=pivots["close"].columns)
    if "is_suspended" in pivots:
        pivots["is_suspended"] = pivots["is_suspended"].fillna(0.0)
    else:
        pivots["is_suspended"] = pd.DataFrame(0.0, index=pivots["close"].index, columns=pivots["close"].columns)
    return pivots


def pct_rank(series: pd.Series, ascending: bool = True) -> pd.Series:
    clean = series.replace([np.inf, -np.inf], np.nan).dropna()
    if clean.empty:
        return pd.Series(dtype=float)
    return clean.rank(pct=True, ascending=ascending)


def first_trading_day_each_week(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if len(index) == 0:
        return index
    grouped = pd.Series(index, index=index).groupby(index.to_period("W-FRI")).first()
    return pd.DatetimeIndex(grouped.values)


def high_conviction_market_exposure(close: pd.DataFrame, loc: int, config: StrategyConfig) -> float:
    if loc < 120:
        return 0.0
    history = close.iloc[: loc + 1]
    last = history.iloc[-1]
    ma20 = history.rolling(20).mean().iloc[-1]
    ma60 = history.rolling(60).mean().iloc[-1]
    ma120 = history.rolling(120).mean().iloc[-1]
    breadth20 = float((last > ma20).mean())
    breadth60 = float((last > ma60).mean())
    breadth120 = float((last > ma120).mean())
    median_ret10 = float((last / history.iloc[-11] - 1).median())
    median_ret20 = float((last / history.iloc[-21] - 1).median())
    median_ret60 = float((last / history.iloc[-61] - 1).median())
    market_vol20 = float(close.pct_change(fill_method=None).iloc[max(0, loc - 20) : loc + 1].std().median())
    recent = history.iloc[max(0, loc - 20) : loc + 1]
    weak_drawdown_ratio = float(((recent.iloc[-1] / recent.cummax().iloc[-1] - 1) < -0.10).mean())
    if (
        breadth20 < config.min_breadth20
        or breadth60 < config.min_breadth60
        or breadth120 < 0.48
        or median_ret20 < -0.04
        or median_ret60 < -0.08
        or market_vol20 > config.max_market_vol20
        or weak_drawdown_ratio > config.max_weak_drawdown_ratio
    ):
        return 0.0
    if breadth20 < 0.52 or breadth60 < 0.56 or median_ret10 < -0.01 or median_ret20 < 0.0:
        return config.neutral_total_weight
    return config.strong_total_weight


def regime_adaptive_market_exposure(close: pd.DataFrame, loc: int, config: StrategyConfig) -> float:
    if loc < 120:
        return 0.0
    history = close.iloc[: loc + 1]
    last = history.iloc[-1]
    ma20 = history.rolling(20).mean().iloc[-1]
    ma60 = history.rolling(60).mean().iloc[-1]
    ma120 = history.rolling(120).mean().iloc[-1]
    breadth20 = float((last > ma20).mean())
    breadth60 = float((last > ma60).mean())
    breadth120 = float((last > ma120).mean())
    median_ret10 = float((last / history.iloc[-11] - 1).median())
    median_ret20 = float((last / history.iloc[-21] - 1).median())
    median_ret60 = float((last / history.iloc[-61] - 1).median())
    returns = close.pct_change(fill_method=None)
    market_vol20 = float(returns.iloc[max(0, loc - 20) : loc + 1].std().median())
    recent = history.iloc[max(0, loc - 20) : loc + 1]
    weak_drawdown_ratio = float(((recent.iloc[-1] / recent.cummax().iloc[-1] - 1) < -0.08).mean())
    if (
        breadth20 < 0.56
        or breadth60 < 0.52
        or breadth120 < 0.48
        or median_ret20 < -0.02
        or median_ret60 < -0.05
        or market_vol20 > 0.020
        or weak_drawdown_ratio > 0.16
    ):
        return 0.0
    if breadth20 >= 0.62 and breadth60 >= 0.58 and median_ret20 > 0.015 and median_ret60 > 0.0 and market_vol20 < 0.017:
        return min(config.strong_total_weight, 0.68)
    if breadth20 >= 0.58 and breadth60 >= 0.54 and median_ret10 > -0.005:
        return min(config.neutral_total_weight, 0.34)
    return 0.0


def allocate_two_names(selected: list[str], vol20: pd.Series, gross: float, max_position_weight: float) -> pd.Series:
    weights = pd.Series(0.0, index=vol20.index)
    if not selected or gross <= 0:
        return weights
    risk = vol20.reindex(selected).replace(0, np.nan)
    raw = (1.0 / risk).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if raw.sum() <= 0:
        raw = pd.Series(1.0, index=selected)
    alloc = raw / raw.sum() * gross
    alloc = alloc.clip(upper=max_position_weight)
    if alloc.sum() > gross:
        alloc = alloc / alloc.sum() * gross
    weights.loc[alloc.index] = alloc
    return weights


def coerce_turnover_rate(turnover: pd.DataFrame) -> pd.DataFrame:
    median = turnover.stack().median(skipna=True)
    if pd.notna(median) and median > 1.0:
        return turnover / 100.0
    return turnover


def append_signal_snapshot(rows: list[dict], dt_iso: str, current: pd.Series) -> None:
    active = current[current > 0]
    if active.empty:
        rows.append({"date": dt_iso, "symbol": None, "target_weight": 0.0})
        return
    for symbol, weight in active.sort_index().items():
        rows.append({"date": dt_iso, "symbol": symbol, "target_weight": float(weight)})


def build_targets(prices: dict[str, pd.DataFrame], config: StrategyConfig) -> tuple[dict[pd.Timestamp, pd.Series], pd.DataFrame, pd.DataFrame]:
    close = prices["close"]
    open_ = prices["open"].reindex_like(close).ffill().fillna(close)
    volume = prices["volume"].reindex_like(close).ffill()
    amount = prices["amount"].reindex_like(close)
    turnover_rate = coerce_turnover_rate(prices["turnover"].reindex_like(close))
    actual_traded_value = amount.where(amount.notna() & (amount > 0), open_ * volume * 100.0).replace([np.inf, -np.inf], np.nan)
    returns = close.pct_change(fill_method=None)
    weekly_dates = set(first_trading_day_each_week(pd.to_datetime(close.index)))
    formal_start = pd.to_datetime(config.start_date)
    signal_rows: list[dict] = []
    debug_rows: list[dict] = []
    current = pd.Series(0.0, index=close.columns)
    entry_price: dict[str, float] = {}
    peak_price: dict[str, float] = {}
    targets: dict[pd.Timestamp, pd.Series] = {}

    for loc, dt_value in enumerate(close.index):
        dt = pd.to_datetime(dt_value)
        dt_iso = dt.strftime("%Y-%m-%d")
        if dt < formal_start:
            continue
        if dt in weekly_dates and loc >= 120:
            history = close.iloc[: loc + 1]
            last = history.iloc[-1]
            mom5 = last / history.iloc[-6] - 1
            mom20 = last / history.iloc[-21] - 1
            mom60 = last / history.iloc[-61] - 1
            mom120 = last / history.iloc[-121] - 1
            ma20 = history.rolling(20).mean().iloc[-1]
            ma60 = history.rolling(60).mean().iloc[-1]
            ma120 = history.rolling(120).mean().iloc[-1]
            vol20 = returns.iloc[max(0, loc - 20) : loc + 1].std()
            downside60 = returns.iloc[max(0, loc - 60) : loc + 1].clip(upper=0).std()
            positive_ratio20 = (returns.iloc[max(0, loc - 20) : loc + 1] > 0).mean()
            positive_ratio60 = (returns.iloc[max(0, loc - 60) : loc + 1] > 0).mean()
            trailing60 = history.iloc[max(0, loc - 60) : loc + 1]
            drawdown60 = trailing60.iloc[-1] / trailing60.cummax().iloc[-1] - 1
            avg_value20 = actual_traded_value.iloc[max(0, loc - 20) : loc + 1].mean()
            avg_value60 = actual_traded_value.iloc[max(0, loc - 60) : loc + 1].mean()
            avg_turnover20 = turnover_rate.iloc[max(0, loc - 20) : loc + 1].mean()
            avg_volume20 = volume.iloc[max(0, loc - 20) : loc + 1].mean()
            volume_ratio20 = volume.iloc[loc] / avg_volume20
            amount_trend60 = avg_value20 / avg_value60 - 1.0
            prev_close = history.iloc[-2]
            signal_gap = open_.iloc[loc] / prev_close - 1

            tradable = (
                last.between(config.min_price, config.max_price)
                & (last > ma20)
                & (last > ma60)
                & (last > ma120)
                & (mom20 > config.min_mom20)
                & (mom60 > config.min_mom60)
                & (mom120 > -0.03)
                & (mom20 < config.max_mom20)
                & (mom5 < config.max_mom5)
                & (vol20 < config.max_vol20)
                & (drawdown60 > -config.max_drawdown60)
                & (signal_gap < config.max_signal_gap)
                & avg_value20.notna()
                & (avg_value20 > 0)
                & vol20.notna()
            )
            if config.strategy_version == "v4_volume_enhanced":
                tradable = tradable & (
                    (avg_value20 >= config.min_amount20)
                    & (avg_turnover20 >= config.min_turnover20)
                    & (avg_turnover20 <= config.max_turnover20)
                    & (volume_ratio20 >= config.min_volume_ratio20)
                    & (volume_ratio20 <= config.max_volume_ratio20)
                    & (amount_trend60 >= config.min_amount_trend60)
                )
            elif config.strategy_version == "v4_volume_risk_filter":
                tradable = tradable & (
                    (avg_value20 >= config.min_amount20)
                    & (avg_turnover20 <= config.max_turnover20)
                    & (volume_ratio20 <= config.max_volume_ratio20)
                    & (amount_trend60 >= config.min_amount_trend60)
                )
            elif config.strategy_version == "v5_regime_adaptive":
                smooth_trend = (
                    (positive_ratio20 >= 0.48)
                    & (positive_ratio60 >= 0.45)
                    & (last > ma20)
                    & (ma20 > ma60)
                    & (ma60 > ma120)
                )
                liquidity_quality = (
                    (avg_value20 >= config.min_amount20)
                    & (avg_turnover20 <= min(config.max_turnover20, 0.16))
                    & (volume_ratio20 <= min(config.max_volume_ratio20, 2.8))
                    & (amount_trend60 >= max(config.min_amount_trend60, -0.12))
                )
                tradable = tradable & smooth_trend & liquidity_quality & (mom60 > max(config.min_mom60, 0.02)) & (mom120 > 0.0)

            if config.strategy_version == "v4_volume_enhanced":
                score = (
                    0.20 * pct_rank(mom60)
                    + 0.15 * pct_rank(mom120)
                    + 0.10 * pct_rank(mom20)
                    + 0.15 * pct_rank(last / ma60 - 1)
                    + 0.13 * pct_rank(vol20, ascending=False)
                    + 0.07 * pct_rank(downside60, ascending=False)
                    + 0.04 * pct_rank(drawdown60)
                    + 0.06 * pct_rank(avg_value20)
                    + 0.05 * pct_rank(avg_turnover20)
                    + 0.03 * pct_rank(amount_trend60)
                    + 0.02 * pct_rank(volume_ratio20, ascending=False)
                )
            elif config.strategy_version == "v4_volume_light":
                turnover_balance = -((avg_turnover20 - 0.035).abs())
                score = (
                    0.23 * pct_rank(mom60)
                    + 0.17 * pct_rank(mom120)
                    + 0.10 * pct_rank(mom20)
                    + 0.17 * pct_rank(last / ma60 - 1)
                    + 0.15 * pct_rank(vol20, ascending=False)
                    + 0.08 * pct_rank(downside60, ascending=False)
                    + 0.04 * pct_rank(drawdown60)
                    + 0.03 * pct_rank(avg_value20)
                    + 0.02 * pct_rank(amount_trend60)
                    + 0.01 * pct_rank(turnover_balance)
                )
            elif config.strategy_version == "v5_regime_adaptive":
                turnover_balance = -((avg_turnover20 - 0.030).abs())
                score = (
                    0.20 * pct_rank(mom60)
                    + 0.16 * pct_rank(mom120)
                    + 0.10 * pct_rank(mom20)
                    + 0.14 * pct_rank(last / ma60 - 1)
                    + 0.12 * pct_rank(vol20, ascending=False)
                    + 0.08 * pct_rank(downside60, ascending=False)
                    + 0.06 * pct_rank(drawdown60)
                    + 0.05 * pct_rank(positive_ratio60)
                    + 0.04 * pct_rank(amount_trend60)
                    + 0.03 * pct_rank(avg_value20)
                    + 0.02 * pct_rank(turnover_balance)
                )
            else:
                score = (
                    0.24 * pct_rank(mom60)
                    + 0.18 * pct_rank(mom120)
                    + 0.10 * pct_rank(mom20)
                    + 0.18 * pct_rank(last / ma60 - 1)
                    + 0.16 * pct_rank(vol20, ascending=False)
                    + 0.08 * pct_rank(downside60, ascending=False)
                    + 0.04 * pct_rank(drawdown60)
                    + 0.02 * pct_rank(avg_value20)
                )
            ranked = score[tradable.reindex(score.index).fillna(False)].dropna().sort_values(ascending=False)
            selected = ranked.head(config.max_positions).index.tolist()
            gross = regime_adaptive_market_exposure(close, loc, config) if config.strategy_version == "v5_regime_adaptive" else high_conviction_market_exposure(close, loc, config)
            if len(ranked) < config.min_candidates:
                gross = 0.0

            previous = current.copy()
            if gross > 0 and selected:
                current = allocate_two_names(selected, vol20, gross, config.max_position_weight)
                entry_price = {s: float(last.loc[s]) for s in selected if current.loc[s] > 0}
                peak_price = {s: float(last.loc[s]) for s in selected if current.loc[s] > 0}
            else:
                current = pd.Series(0.0, index=close.columns)
                entry_price = {}
                peak_price = {}
            if not current.equals(previous):
                targets[dt] = current.copy()
                append_signal_snapshot(signal_rows, dt_iso, current)
            debug_rows.append(
                {
                    "date": dt_iso,
                    "gross": gross,
                    "candidate_count": int(len(ranked)),
                    "selected": ",".join(selected),
                }
            )

        if loc >= max(1, config.trend_exit_window):
            last = close.iloc[loc]
            trend_ma = close.iloc[: loc + 1].rolling(config.trend_exit_window).mean().iloc[-1]
            previous = current.copy()
            for symbol in list(current[current > 0].index):
                price = float(last.loc[symbol])
                if not math.isfinite(price):
                    current.loc[symbol] = 0.0
                    entry_price.pop(symbol, None)
                    peak_price.pop(symbol, None)
                    continue
                peak_price[symbol] = max(peak_price.get(symbol, price), price)
                broken_trend = price < float(trend_ma.loc[symbol])
                hit_stop = price <= entry_price.get(symbol, price) * (1.0 - config.stop_loss)
                hit_trailing = price <= peak_price.get(symbol, price) * (1.0 - config.trailing_stop)
                if broken_trend or hit_stop or hit_trailing:
                    current.loc[symbol] = 0.0
                    entry_price.pop(symbol, None)
                    peak_price.pop(symbol, None)
            if not current.equals(previous):
                targets[dt] = current.copy()
                append_signal_snapshot(signal_rows, dt_iso, current)

    signals = pd.DataFrame(signal_rows, columns=["date", "symbol", "target_weight"])
    debug = pd.DataFrame(debug_rows)
    return targets, signals, debug


def save_outputs(
    config: StrategyConfig,
    prices: dict[str, pd.DataFrame],
    signals: pd.DataFrame,
    debug: pd.DataFrame,
    backtest,
) -> None:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_csv(signals, config.output_dir / "local_weight_signals.csv")
    atomic_write_csv(debug, config.output_dir / "local_signal_debug.csv")
    atomic_write_csv(backtest.raw_perf, config.output_dir / "local_raw_perf.csv")
    atomic_write_csv(backtest.trades, config.output_dir / "local_trades.csv")
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "engine": "local_backtest",
        "data_source": "tushare_offline",
        "strategy": f"small_account_high_conviction_policy_{config.strategy_version}_local",
        "config": {**asdict(config), "prices_file": str(config.prices_file), "output_dir": str(config.output_dir)},
        "signal_rows": len(signals),
        "traded_instruments": int(signals["symbol"].dropna().nunique()) if not signals.empty else 0,
        "summary": backtest.summary,
    }
    atomic_write_text(config.output_dir / "local_backtest_summary.json", json.dumps(summary, ensure_ascii=False, indent=2, default=str))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local v4/v5 strategy on Tushare offline data.")
    parser.add_argument("--start-date", default="2025-07-05")
    parser.add_argument("--end-date", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    parser.add_argument("--warmup-start-date", default="2025-01-07")
    parser.add_argument("--initial-cash", type=float, default=100000.0)
    parser.add_argument("--max-positions", type=int, default=2)
    parser.add_argument("--min-candidates", type=int, default=2)
    parser.add_argument("--max-position-weight", type=float, default=0.34)
    parser.add_argument("--strong-total-weight", type=float, default=0.68)
    parser.add_argument("--neutral-total-weight", type=float, default=0.34)
    parser.add_argument("--min-price", type=float, default=2.5)
    parser.add_argument("--max-price", type=float, default=85.0)
    parser.add_argument("--min-mom20", type=float, default=0.0)
    parser.add_argument("--min-mom60", type=float, default=0.0)
    parser.add_argument("--max-mom20", type=float, default=0.45)
    parser.add_argument("--max-mom5", type=float, default=0.45)
    parser.add_argument("--max-vol20", type=float, default=0.055)
    parser.add_argument("--max-drawdown60", type=float, default=0.22)
    parser.add_argument("--max-signal-gap", type=float, default=0.06)
    parser.add_argument("--min-breadth20", type=float, default=0.55)
    parser.add_argument("--min-breadth60", type=float, default=0.50)
    parser.add_argument("--max-market-vol20", type=float, default=0.021)
    parser.add_argument("--max-weak-drawdown-ratio", type=float, default=0.18)
    parser.add_argument("--stop-loss", type=float, default=0.06)
    parser.add_argument("--trailing-stop", type=float, default=0.10)
    parser.add_argument("--trend-exit-window", type=int, default=20)
    parser.add_argument("--strategy-version", choices=["v4", "v4_volume_enhanced", "v4_volume_light", "v4_volume_risk_filter", "v5_regime_adaptive"], default="v4")
    parser.add_argument("--min-amount20", type=float, default=30_000_000.0)
    parser.add_argument("--min-turnover20", type=float, default=0.005)
    parser.add_argument("--max-turnover20", type=float, default=0.18)
    parser.add_argument("--min-volume-ratio20", type=float, default=0.55)
    parser.add_argument("--max-volume-ratio20", type=float, default=3.2)
    parser.add_argument("--min-amount-trend60", type=float, default=-0.35)
    parser.add_argument("--buy-cost", type=float, default=0.0003)
    parser.add_argument("--sell-cost", type=float, default=0.0013)
    parser.add_argument("--min-cost", type=float, default=5.0)
    parser.add_argument("--prices-file", default=str(DEFAULT_PRICES_FILE))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)
    config = StrategyConfig(
        start_date=iso_date(args.start_date),
        end_date=iso_date(args.end_date),
        warmup_start_date=iso_date(args.warmup_start_date),
        initial_cash=args.initial_cash,
        max_positions=args.max_positions,
        min_candidates=args.min_candidates,
        max_position_weight=args.max_position_weight,
        strong_total_weight=args.strong_total_weight,
        neutral_total_weight=args.neutral_total_weight,
        min_price=args.min_price,
        max_price=args.max_price,
        min_mom20=args.min_mom20,
        min_mom60=args.min_mom60,
        max_mom20=args.max_mom20,
        max_mom5=args.max_mom5,
        max_vol20=args.max_vol20,
        max_drawdown60=args.max_drawdown60,
        max_signal_gap=args.max_signal_gap,
        min_breadth20=args.min_breadth20,
        min_breadth60=args.min_breadth60,
        max_market_vol20=args.max_market_vol20,
        max_weak_drawdown_ratio=args.max_weak_drawdown_ratio,
        stop_loss=args.stop_loss,
        trailing_stop=args.trailing_stop,
        trend_exit_window=args.trend_exit_window,
        strategy_version=args.strategy_version,
        min_amount20=args.min_amount20,
        min_turnover20=args.min_turnover20,
        max_turnover20=args.max_turnover20,
        min_volume_ratio20=args.min_volume_ratio20,
        max_volume_ratio20=args.max_volume_ratio20,
        min_amount_trend60=args.min_amount_trend60,
        buy_cost=args.buy_cost,
        sell_cost=args.sell_cost,
        min_cost=args.min_cost,
        prices_file=Path(args.prices_file),
        output_dir=Path(args.output_dir),
    )
    prices = load_prices(config.prices_file, config)
    targets, signals, debug = build_targets(prices, config)
    backtest = run_local_backtest(
        prices,
        targets,
        BacktestConfig(initial_cash=config.initial_cash, buy_cost=config.buy_cost, sell_cost=config.sell_cost, min_cost=config.min_cost),
        strategy_name=f"local_{config.strategy_version}",
    )
    save_outputs(config, prices, signals, debug, backtest)
    print(json.dumps(backtest.summary, ensure_ascii=False, indent=2, default=str))
    print(f"Output: {config.output_dir.resolve()}")


if __name__ == "__main__":
    main()
