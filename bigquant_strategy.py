#!/usr/bin/env python3
"""BigQuant-only small-account strategy.

Data source: BigQuant DAI.
Backtest engine: BigQuant BigTrader.

This is the minimal strategy entry point for this branch.
"""

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

import pandas as pd
import numpy as np

from bigquant_provider import (
    DEFAULT_DATASOURCE,
    dai_query,
    fetch_bigquant_daily_history_batch,
    from_bigquant_instrument,
    init_bigquant,
    normalize_symbol,
    to_bigquant_instrument,
)


LOGGER = logging.getLogger("bigquant_strategy")
REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = REPO_ROOT / ".env.local"
DEFAULT_CACHE_DIR = REPO_ROOT / "data/bigquant_cache"
DEFAULT_PRICES_FILE = REPO_ROOT / "data/offline/a_share_12m_bigquant/prices_long.csv"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data/backtests/bigquant_strategy"


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
    batch_size: int
    limit: int | None
    env_file: Path
    cache_dir: Path
    prices_file: Path | None
    output_dir: Path
    datasource: str
    benchmark: str
    use_cache: bool


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


def is_chinext(symbol: str) -> bool:
    return normalize_symbol(symbol).startswith(("300", "301"))


def is_star_market(symbol: str) -> bool:
    return normalize_symbol(symbol).startswith(("688", "689"))


def is_bse(symbol: str) -> bool:
    symbol = normalize_symbol(symbol)
    return symbol.startswith(("4", "8", "920"))


def load_universe(config: StrategyConfig) -> pd.DataFrame:
    end_date = iso_date(config.end_date)
    sql = f"""
        SELECT instrument, name
        FROM {config.datasource}
        WHERE date = '{end_date}'
        ORDER BY instrument
    """
    raw = dai_query(sql, filters={"date": [end_date, end_date]}, env_file=config.env_file).df()
    if raw.empty:
        raise ValueError(f"No BigQuant universe rows on {end_date}")

    universe = raw.copy()
    universe["symbol"] = universe["instrument"].map(from_bigquant_instrument)
    universe["name"] = universe["name"].astype(str)
    universe = universe.drop_duplicates("symbol")
    universe = universe[~universe["symbol"].map(is_chinext)]
    universe = universe[~universe["symbol"].map(is_star_market)]
    universe = universe[~universe["symbol"].map(is_bse)]
    universe = universe[~universe["name"].str.upper().str.contains("ST", na=False)]
    universe = universe.sort_values("symbol").reset_index(drop=True)
    if config.limit:
        universe = universe.head(config.limit)
    return universe[["symbol", "instrument", "name"]]


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def cache_path(config: StrategyConfig, universe_count: int) -> Path:
    return (
        config.cache_dir
        / f"bars_{compact_date(config.warmup_start_date)}_{compact_date(config.end_date)}_{universe_count}_{config.datasource}.csv"
    )


def load_or_fetch_bars(config: StrategyConfig, universe: pd.DataFrame) -> pd.DataFrame:
    if config.prices_file and config.prices_file.exists():
        LOGGER.info("Load BigQuant local prices file: %s", config.prices_file)
        local = pd.read_csv(config.prices_file, dtype={"symbol": str})
        local["symbol"] = local["symbol"].map(normalize_symbol)
        local["trade_date"] = pd.to_datetime(local["date"], errors="coerce").dt.strftime("%Y%m%d")
        local = local[
            local["symbol"].isin(set(universe["symbol"]))
            & (local["trade_date"] >= compact_date(config.warmup_start_date))
            & (local["trade_date"] <= compact_date(config.end_date))
        ].copy()
        local = local.rename(columns={"turnover": "turnover_rate"})
        return local[["trade_date", "symbol", "open", "high", "low", "close", "volume", "amount", "turnover_rate"]]

    path = cache_path(config, len(universe))
    if config.use_cache and path.exists():
        LOGGER.info("Load cached BigQuant bars: %s", path)
        return pd.read_csv(path, dtype={"symbol": str})

    symbols = universe["symbol"].tolist()
    frames: list[pd.DataFrame] = []
    for index, batch in enumerate(chunked(symbols, config.batch_size), start=1):
        LOGGER.info("Fetch BigQuant batch %s/%s size=%s", index, (len(symbols) + config.batch_size - 1) // config.batch_size, len(batch))
        frame = fetch_bigquant_daily_history_batch(
            batch,
            start_date=iso_date(config.warmup_start_date),
            end_date=iso_date(config.end_date),
            datasource=config.datasource,
            adjust="qfq",
            volume_unit="hand",
        )
        if not frame.empty:
            frames.append(frame)

    bars = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    bars = bars.sort_values(["trade_date", "symbol"]).reset_index(drop=True)
    atomic_write_csv(bars, path)
    return bars


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
    """Classify market regime instead of forecasting the next price point."""
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
    """Normalize turnover to decimal form when a source stores percent values."""
    median = turnover.stack().median(skipna=True)
    if pd.notna(median) and median > 1.0:
        return turnover / 100.0
    return turnover


def append_target_snapshot(rows: list[dict], date: str, previous: pd.Series, current: pd.Series) -> None:
    """Append a full BigTrader target snapshot for an event date."""
    active_symbols = set(previous[previous > 0].index) | set(current[current > 0].index)
    if not active_symbols:
        rows.append({"date": date, "instrument": None, "weight": 0.0})
        return
    for symbol in sorted(active_symbols):
        rows.append({"date": date, "instrument": to_bigquant_instrument(symbol), "weight": float(current.get(symbol, 0.0))})


def build_weight_signals(bars: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
    if bars.empty:
        return pd.DataFrame(columns=["date", "instrument", "weight"])

    close = bars.pivot(index="trade_date", columns="symbol", values="close").sort_index()
    open_ = bars.pivot(index="trade_date", columns="symbol", values="open").reindex(index=close.index, columns=close.columns).ffill().fillna(close)
    volume = bars.pivot(index="trade_date", columns="symbol", values="volume").reindex(index=close.index, columns=close.columns).ffill()
    amount = bars.pivot(index="trade_date", columns="symbol", values="amount").reindex(index=close.index, columns=close.columns)
    turnover_rate = bars.pivot(index="trade_date", columns="symbol", values="turnover_rate").reindex(index=close.index, columns=close.columns)
    turnover_rate = coerce_turnover_rate(turnover_rate)
    legacy_traded_value = (open_ * volume).replace([np.inf, -np.inf], np.nan)
    fallback_amount = open_ * volume * 100.0
    actual_traded_value = amount.where(amount.notna() & (amount > 0), fallback_amount).replace([np.inf, -np.inf], np.nan)
    uses_bigquant_amount = config.strategy_version in {
        "v4_volume_enhanced",
        "v4_volume_light",
        "v4_volume_risk_filter",
        "v5_regime_adaptive",
    }
    traded_value = actual_traded_value if uses_bigquant_amount else legacy_traded_value
    returns = close.pct_change(fill_method=None)
    weekly_dates = set(first_trading_day_each_week(pd.to_datetime(close.index)))
    formal_start = pd.to_datetime(config.start_date)
    rows: list[dict] = []
    event_dates: set[str] = set()
    current = pd.Series(0.0, index=close.columns)
    entry_price: dict[str, float] = {}
    peak_price: dict[str, float] = {}

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
            avg_value20 = traded_value.iloc[max(0, loc - 20) : loc + 1].mean()
            avg_value60 = traded_value.iloc[max(0, loc - 60) : loc + 1].mean()
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
                volume_confirmed = (
                    (avg_value20 >= config.min_amount20)
                    & (avg_turnover20 >= config.min_turnover20)
                    & (avg_turnover20 <= config.max_turnover20)
                    & (volume_ratio20 >= config.min_volume_ratio20)
                    & (volume_ratio20 <= config.max_volume_ratio20)
                    & (amount_trend60 >= config.min_amount_trend60)
                    & avg_turnover20.notna()
                    & volume_ratio20.notna()
                    & amount_trend60.notna()
                )
                tradable = tradable & volume_confirmed
            elif config.strategy_version == "v4_volume_risk_filter":
                volume_risk_guard = (
                    (avg_value20 >= config.min_amount20)
                    & (avg_turnover20 <= config.max_turnover20)
                    & (volume_ratio20 <= config.max_volume_ratio20)
                    & (amount_trend60 >= config.min_amount_trend60)
                    & avg_turnover20.notna()
                    & volume_ratio20.notna()
                    & amount_trend60.notna()
                )
                tradable = tradable & volume_risk_guard
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
                    & avg_turnover20.notna()
                    & volume_ratio20.notna()
                    & amount_trend60.notna()
                )
                tradable = (
                    tradable
                    & smooth_trend
                    & liquidity_quality
                    & (mom60 > max(config.min_mom60, 0.02))
                    & (mom120 > 0.0)
                    & (mom20 < min(config.max_mom20, 0.35))
                    & (mom5 < min(config.max_mom5, 0.25))
                    & (vol20 < min(config.max_vol20, 0.048))
                    & (drawdown60 > -min(config.max_drawdown60, 0.18))
                )

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
            score = score[tradable.reindex(score.index).fillna(False)]
            ranked = score.dropna().sort_values(ascending=False)
            selected = ranked.head(config.max_positions).index.tolist()

            if config.strategy_version == "v5_regime_adaptive":
                gross = regime_adaptive_market_exposure(close, loc, config)
            else:
                gross = high_conviction_market_exposure(close, loc, config)
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
                append_target_snapshot(rows, dt_iso, previous, current)
                event_dates.add(dt_iso)

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
                entry = entry_price.get(symbol, price)
                peak = peak_price.get(symbol, price)
                broken_trend = price < float(trend_ma.loc[symbol])
                hit_stop = price <= entry * (1.0 - config.stop_loss)
                hit_trailing = price <= peak * (1.0 - config.trailing_stop)
                if broken_trend or hit_stop or hit_trailing:
                    current.loc[symbol] = 0.0
                    entry_price.pop(symbol, None)
                    peak_price.pop(symbol, None)
            for symbol in sorted(previous[previous > 0].index):
                if previous.loc[symbol] > 0 and current.loc[symbol] == 0.0:
                    append_target_snapshot(rows, dt_iso, previous, current)
                    event_dates.add(dt_iso)
                    break

    formal_dates = pd.to_datetime(close.index[pd.to_datetime(close.index) >= formal_start]).strftime("%Y-%m-%d")
    for dt_iso in formal_dates:
        if dt_iso not in event_dates:
            rows.append({"date": dt_iso, "instrument": None, "weight": 0.0})

    signals = pd.DataFrame(rows, columns=["date", "instrument", "weight"])
    if signals.empty:
        return signals
    signals = signals.drop_duplicates(["date", "instrument"], keep="last").sort_values(["date", "instrument"])
    return signals.reset_index(drop=True)


def run_bigtrader(signals: pd.DataFrame, config: StrategyConfig):
    from bigquant import bigtrader

    if signals.empty:
        raise ValueError("No BigQuant weight signals generated; skip BigTrader run to avoid loading the full market.")

    # Trigger BigTrader lazy exports before accessing names.
    _ = bigtrader.run

    instruments = sorted(signals["instrument"].dropna().unique().tolist()) if not signals.empty else []

    def initialize(context):
        context.set_commission(bigtrader.PerOrder(buy_cost=0.0003, sell_cost=0.0013, min_cost=5.0))
        context.set_stock_t1(1)

    def handle_data(context, data):
        bigtrader.HandleDataLib.handle_data_weight_based(context, data)

    return bigtrader.run(
        instruments=instruments,
        start_date=iso_date(config.start_date),
        end_date=iso_date(config.end_date),
        data=signals,
        capital_base=config.initial_cash,
        initialize=initialize,
        handle_data=handle_data,
        benchmark=config.benchmark,
        order_price_field_buy="open",
        order_price_field_sell="open",
        volume_limit=1,
        render=None,
        report_output_path=False,
    )


def save_outputs(performance, signals: pd.DataFrame, universe: pd.DataFrame, config: StrategyConfig) -> None:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_csv(universe, config.output_dir / "universe.csv")
    atomic_write_csv(signals, config.output_dir / "bigquant_weight_signals.csv")

    raw_perf = performance.raw_perf
    if raw_perf is not None:
        atomic_write_csv(raw_perf.reset_index(drop=True), config.output_dir / "bigtrader_raw_perf.csv")

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "engine": "bigquant.bigtrader",
        "data_source": "bigquant.dai",
        "strategy": f"small_account_high_conviction_policy_{config.strategy_version}_bigquant",
        "config": {
            **asdict(config),
            "env_file": str(config.env_file),
            "cache_dir": str(config.cache_dir),
            "prices_file": str(config.prices_file) if config.prices_file else None,
            "output_dir": str(config.output_dir),
        },
        "universe_count": len(universe),
        "signal_rows": len(signals),
        "traded_instruments": int(signals["instrument"].nunique()) if not signals.empty else 0,
        "summary": performance.summary,
    }
    atomic_write_text(config.output_dir / "bigtrader_summary.json", json.dumps(summary, ensure_ascii=False, indent=2, default=str))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the BigQuant-only v4 high-conviction small-account strategy.")
    parser.add_argument("--start-date", default="2025-07-05", help="Formal backtest start date.")
    parser.add_argument("--end-date", default="2026-07-06", help="Backtest end date.")
    parser.add_argument("--warmup-start-date", default="2025-01-07", help="Warmup start date for indicators.")
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
    parser.add_argument(
        "--strategy-version",
        choices=["v4", "v4_volume_enhanced", "v4_volume_light", "v4_volume_risk_filter", "v5_regime_adaptive"],
        default="v4",
    )
    parser.add_argument("--min-amount20", type=float, default=30_000_000.0, help="Minimum 20-day average turnover amount for volume-enhanced selection.")
    parser.add_argument("--min-turnover20", type=float, default=0.005, help="Minimum 20-day average turnover rate in decimal form.")
    parser.add_argument("--max-turnover20", type=float, default=0.18, help="Maximum 20-day average turnover rate in decimal form.")
    parser.add_argument("--min-volume-ratio20", type=float, default=0.55, help="Minimum current volume divided by 20-day average volume.")
    parser.add_argument("--max-volume-ratio20", type=float, default=3.2, help="Maximum current volume divided by 20-day average volume.")
    parser.add_argument("--min-amount-trend60", type=float, default=-0.35, help="Minimum 20-day amount trend versus 60-day amount.")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--prices-file", default=str(DEFAULT_PRICES_FILE), help="Existing BigQuant local prices_long.csv to reuse before querying DAI.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--datasource", default=DEFAULT_DATASOURCE)
    parser.add_argument("--benchmark", default="000300.SH")
    parser.add_argument("--no-cache", action="store_true")
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
        batch_size=args.batch_size,
        limit=args.limit,
        env_file=Path(args.env_file),
        cache_dir=Path(args.cache_dir),
        prices_file=Path(args.prices_file) if args.prices_file else None,
        output_dir=Path(args.output_dir),
        datasource=args.datasource,
        benchmark=args.benchmark,
        use_cache=not args.no_cache,
    )
    init_bigquant(config.env_file)
    universe = load_universe(config)
    bars = load_or_fetch_bars(config, universe)
    signals = build_weight_signals(bars, config)
    LOGGER.info("Prepared BigQuant signals: rows=%s instruments=%s", len(signals), signals["instrument"].nunique() if not signals.empty else 0)
    performance = run_bigtrader(signals, config)
    save_outputs(performance, signals, universe, config)
    print(json.dumps(performance.summary, ensure_ascii=False, indent=2, default=str))
    print(f"Output: {config.output_dir.resolve()}")


if __name__ == "__main__":
    main()
