#!/usr/bin/env python3
"""Explore point-in-time stock alpha factors outside the v4 framework."""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ashare_quant.backtest import BacktestConfig, run_local_backtest
from ashare_quant.benchmark import benchmark_metrics, fetch_benchmark
from ashare_quant.data.tushare import get_pro_client
from ashare_quant.paths import DEFAULT_ENV_FILE, DEFAULT_PRICES_FILE, PROJECT_ROOT
from ashare_quant.strategies.v4 import StrategyConfig, load_prices


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data/backtests/stock_alpha_factor_exploration"
DEFAULT_BASIC_CACHE = (
    PROJECT_ROOT / "data/offline/a_share_history_tushare/.daily_basic_monthly_cache"
)
MAINBOARD_PREFIXES = ("000", "001", "002", "003", "600", "601", "603", "605")


@dataclass(frozen=True)
class AlphaVariant:
    name: str
    weights: dict[str, float]
    positions: int = 10
    exposure_mode: str = "risk_tier"


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="", dir=path.parent, delete=False
        ) as handle:
            tmp_path = Path(handle.name)
            frame.to_csv(handle, index=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def make_cfg(args: argparse.Namespace) -> StrategyConfig:
    return StrategyConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        warmup_start_date=args.warmup_start_date,
        initial_cash=args.initial_cash,
        max_positions=10,
        min_candidates=20,
        max_position_weight=0.12,
        strong_total_weight=1.0,
        neutral_total_weight=0.5,
        min_price=2.5,
        max_price=85.0,
        min_mom20=-1.0,
        min_mom60=-1.0,
        max_mom20=10.0,
        max_mom5=10.0,
        max_vol20=1.0,
        max_drawdown60=1.0,
        max_signal_gap=0.08,
        min_breadth20=0.0,
        min_breadth60=0.0,
        max_market_vol20=1.0,
        max_weak_drawdown_ratio=1.0,
        stop_loss=1.0,
        trailing_stop=1.0,
        trend_exit_window=20,
        strategy_version="stock_alpha_research",
        min_amount20=30_000_000.0,
        min_turnover20=0.0,
        max_turnover20=1.0,
        min_volume_ratio20=0.0,
        max_volume_ratio20=100.0,
        min_amount_trend60=-10.0,
        buy_cost=0.0003,
        sell_cost=0.0013,
        min_cost=5.0,
        prices_file=Path(args.prices_file),
        output_dir=Path(args.output_dir),
    )


def monthly_rebalance_dates(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    grouped = pd.Series(index, index=index).groupby(index.to_period("M")).first()
    return [pd.Timestamp(value) for value in grouped.values]


def winsor_zscore(series: pd.Series) -> pd.Series:
    clean = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    valid = clean.dropna()
    if len(valid) < 30:
        return pd.Series(np.nan, index=series.index, dtype=float)
    lo, hi = valid.quantile([0.05, 0.95])
    clipped = clean.clip(lo, hi)
    std = clipped.std(ddof=0)
    if not math.isfinite(std) or std <= 1e-12:
        return pd.Series(0.0, index=series.index).where(clipped.notna())
    return (clipped - clipped.mean()) / std


def fetch_monthly_basic(trade_date: pd.Timestamp, cache_dir: Path) -> pd.DataFrame:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{trade_date.strftime('%Y-%m-%d')}.csv"
    if cache_path.exists():
        return pd.read_csv(cache_path, dtype={"ts_code": str, "symbol": str})
    pro = get_pro_client(DEFAULT_ENV_FILE)
    compact = trade_date.strftime("%Y%m%d")
    fields = (
        "ts_code,trade_date,turnover_rate,volume_ratio,pe_ttm,pb,ps_ttm,"
        "dv_ttm,total_share,float_share,free_share,total_mv,circ_mv"
    )
    last_error: BaseException | None = None
    for attempt in range(3):
        try:
            frame = pro.daily_basic(trade_date=compact, fields=fields)
            if frame is None:
                frame = pd.DataFrame()
            frame["symbol"] = frame["ts_code"].astype(str).str.split(".").str[0].str.zfill(6)
            atomic_write_csv(frame, cache_path)
            return frame
        except Exception as error:
            last_error = error
            if attempt < 2:
                time.sleep(1.0 + attempt)
    raise RuntimeError(f"daily_basic failed for {compact}: {last_error}")


def point_in_time_factor_snapshot(
    prices: dict[str, pd.DataFrame],
    loc: int,
    basic: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, float]]:
    close = prices["close"]
    raw_close = prices["raw_close"].reindex_like(close)
    raw_open = prices["raw_open"].reindex_like(close)
    volume = prices["volume"].reindex_like(close)
    amount = prices["amount"].reindex_like(close)
    turnover = prices["turnover"].reindex_like(close)
    is_suspended = prices["is_suspended"].reindex_like(close).fillna(0.0)
    is_st = prices["is_st"].reindex_like(close).fillna(0.0)
    is_listed = prices["is_listed"].reindex_like(close).fillna(0.0)
    returns = close.pct_change(fill_method=None)
    history = close.iloc[: loc + 1]
    last = history.iloc[-1]
    ret20_window = returns.iloc[max(0, loc - 19) : loc + 1]
    ret60_window = returns.iloc[max(0, loc - 59) : loc + 1]
    close60 = history.iloc[max(0, loc - 59) : loc + 1]
    value20 = amount.iloc[max(0, loc - 19) : loc + 1].mean()
    volume5 = volume.iloc[max(0, loc - 4) : loc + 1].mean()
    volume20 = volume.iloc[max(0, loc - 19) : loc + 1].mean()
    turnover20 = turnover.iloc[max(0, loc - 19) : loc + 1].mean()
    market_ret = ret60_window.median(axis=1)
    residual = ret60_window.sub(market_ret, axis=0)

    panel = pd.DataFrame(index=close.columns)
    panel["raw_close"] = raw_close.iloc[loc]
    panel["mom5"] = last / history.iloc[-6] - 1.0
    panel["mom20"] = last / history.iloc[-21] - 1.0
    panel["mom60"] = last / history.iloc[-61] - 1.0
    panel["mom120"] = last / history.iloc[-121] - 1.0
    panel["reversal5"] = -panel["mom5"]
    panel["reversal20"] = -panel["mom20"]
    panel["low_vol20"] = -ret20_window.std()
    panel["low_vol60"] = -ret60_window.std()
    panel["low_downside60"] = -ret60_window.clip(upper=0.0).std()
    panel["low_residual_vol60"] = -residual.std()
    panel["positive_ratio60"] = (ret60_window > 0).mean()
    panel["drawdown60"] = last / close60.max() - 1.0
    panel["trend_quality120"] = panel["mom120"] / ret60_window.std().replace(0.0, np.nan)
    panel["volume_contraction"] = -(volume5 / volume20)
    panel["low_turnover"] = -turnover20
    panel["avg_amount20"] = value20
    panel["low_liquidity"] = -np.log(value20.where(value20 > 0))
    panel["amihud20"] = -(ret20_window.abs().mean() / value20.replace(0.0, np.nan))

    if not basic.empty:
        basic = basic.copy().set_index("symbol")
        for column in [
            "pe_ttm",
            "pb",
            "ps_ttm",
            "dv_ttm",
            "total_mv",
            "circ_mv",
            "free_share",
            "turnover_rate",
            "volume_ratio",
        ]:
            panel[column] = pd.to_numeric(basic.get(column), errors="coerce").reindex(panel.index)
    panel["small_size"] = -np.log(panel["total_mv"].where(panel["total_mv"] > 0))
    panel["small_float_size"] = -np.log(panel["circ_mv"].where(panel["circ_mv"] > 0))
    panel["earnings_yield"] = (1.0 / panel["pe_ttm"]).where(panel["pe_ttm"] > 0)
    panel["book_yield"] = (1.0 / panel["pb"]).where(panel["pb"] > 0)
    panel["sales_yield"] = (1.0 / panel["ps_ttm"]).where(panel["ps_ttm"] > 0)
    panel["dividend_yield"] = panel["dv_ttm"].fillna(0.0)

    trade_open = raw_open.iloc[loc]
    trade_close = raw_close.iloc[loc]
    mainboard = pd.Series(panel.index.str.startswith(MAINBOARD_PREFIXES), index=panel.index)
    panel["eligible"] = (
        mainboard
        & is_listed.iloc[loc].eq(1)
        & is_st.iloc[loc].eq(0)
        & is_suspended.iloc[loc].eq(0)
        & trade_open.notna()
        & trade_close.notna()
        & trade_close.between(2.5, 85.0)
        & (value20 >= 30_000_000.0)
        & panel["low_vol60"].notna()
        & panel["total_mv"].notna()
    )

    market = {
        "breadth120": float((panel["mom120"] > 0).mean()),
        "median_mom120": float(panel["mom120"].median()),
        "median_mom20": float(panel["mom20"].median()),
        "market_vol20": float(ret20_window.std().median()),
    }
    return panel, market


def exposure_for_market(market: dict[str, float], mode: str) -> float:
    if mode == "full":
        return 1.0
    if mode != "risk_tier":
        raise ValueError(f"Unsupported exposure mode: {mode}")
    if market["median_mom120"] < -0.18 and market["breadth120"] < 0.30:
        return 0.20
    if market["median_mom120"] < -0.08 and market["breadth120"] < 0.40:
        return 0.40
    if market["median_mom120"] < -0.03 or market["breadth120"] < 0.45:
        return 0.65
    return 1.0


def factor_score(panel: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    eligible = panel["eligible"].fillna(False)
    score = pd.Series(0.0, index=panel.index)
    available_weight = pd.Series(0.0, index=panel.index)
    for factor, weight in weights.items():
        normalized = winsor_zscore(panel[factor].where(eligible))
        valid = normalized.notna()
        score = score.add(normalized.fillna(0.0) * weight, fill_value=0.0)
        available_weight = available_weight.add(valid.astype(float) * abs(weight), fill_value=0.0)
    score = score / available_weight.replace(0.0, np.nan)
    return score.where(eligible)


def equal_weight_targets(
    columns: pd.Index,
    ranked: pd.Series,
    positions: int,
    gross: float,
) -> pd.Series:
    target = pd.Series(0.0, index=columns)
    selected = ranked.head(positions).index
    if len(selected):
        target.loc[selected] = gross / len(selected)
    return target


def variants() -> list[AlphaVariant]:
    return [
        AlphaVariant(
            "lowvol_value",
            {
                "low_vol60": 0.25,
                "low_downside60": 0.15,
                "earnings_yield": 0.18,
                "book_yield": 0.12,
                "dividend_yield": 0.10,
                "volume_contraction": 0.10,
                "low_turnover": 0.10,
            },
        ),
        AlphaVariant(
            "small_value_lowvol",
            {
                "small_size": 0.22,
                "earnings_yield": 0.18,
                "book_yield": 0.12,
                "low_vol60": 0.18,
                "low_downside60": 0.10,
                "reversal20": 0.10,
                "volume_contraction": 0.10,
            },
        ),
        AlphaVariant(
            "lowvol_reversal",
            {
                "low_vol60": 0.30,
                "low_downside60": 0.15,
                "reversal20": 0.20,
                "reversal5": 0.05,
                "volume_contraction": 0.15,
                "earnings_yield": 0.10,
                "dividend_yield": 0.05,
            },
        ),
        AlphaVariant(
            "defensive_quality_proxy",
            {
                "low_vol60": 0.25,
                "low_residual_vol60": 0.15,
                "positive_ratio60": 0.10,
                "drawdown60": 0.10,
                "earnings_yield": 0.15,
                "book_yield": 0.10,
                "dividend_yield": 0.10,
                "low_turnover": 0.05,
            },
            positions=15,
        ),
        AlphaVariant(
            "trend_quality_value",
            {
                "trend_quality120": 0.22,
                "positive_ratio60": 0.18,
                "low_residual_vol60": 0.15,
                "earnings_yield": 0.15,
                "book_yield": 0.10,
                "volume_contraction": 0.10,
                "low_turnover": 0.10,
            },
        ),
        AlphaVariant(
            "small_value_full",
            {
                "small_float_size": 0.28,
                "earnings_yield": 0.20,
                "book_yield": 0.15,
                "sales_yield": 0.10,
                "low_vol60": 0.12,
                "reversal20": 0.08,
                "volume_contraction": 0.07,
            },
            exposure_mode="full",
        ),
    ]


def build_factor_panels(
    prices: dict[str, pd.DataFrame],
    start_date: str,
    cache_dir: Path,
) -> tuple[dict[pd.Timestamp, pd.DataFrame], pd.DataFrame]:
    close = prices["close"]
    dates = monthly_rebalance_dates(pd.DatetimeIndex(close.index))
    panels: dict[pd.Timestamp, pd.DataFrame] = {}
    market_rows: list[dict] = []
    for dt in dates:
        if dt < pd.to_datetime(start_date):
            continue
        loc = int(close.index.get_loc(dt))
        if loc < 120:
            continue
        basic = fetch_monthly_basic(dt, cache_dir)
        panel, market = point_in_time_factor_snapshot(prices, loc, basic)
        panels[dt] = panel
        market_rows.append(
            {
                "date": dt.strftime("%Y-%m-%d"),
                **market,
                "eligible_count": int(panel["eligible"].sum()),
            }
        )
        print(
            f"factor_panel {dt.strftime('%Y-%m-%d')} eligible={int(panel['eligible'].sum())}",
            flush=True,
        )
    return panels, pd.DataFrame(market_rows)


def attach_forward_returns(
    panels: dict[pd.Timestamp, pd.DataFrame],
    close: pd.DataFrame,
    horizon: int = 20,
) -> None:
    for dt, panel in panels.items():
        loc = int(close.index.get_loc(dt))
        if loc + horizon >= len(close.index):
            panel["forward20"] = np.nan
            panel["forward20_available_date"] = pd.NaT
        else:
            panel["forward20"] = close.iloc[loc + horizon] / close.iloc[loc] - 1.0
            panel["forward20_available_date"] = close.index[loc + horizon]


def factor_ic_table(
    panels: dict[pd.Timestamp, pd.DataFrame],
    factor_names: list[str],
) -> pd.DataFrame:
    rows: list[dict] = []
    for dt, panel in panels.items():
        sample = panel[panel["eligible"]].copy()
        for factor in factor_names:
            values = sample[[factor, "forward20"]].dropna()
            if len(values) < 30:
                continue
            rows.append(
                {
                    "date": dt.strftime("%Y-%m-%d"),
                    "available_date": pd.to_datetime(
                        panel["forward20_available_date"].iloc[0]
                    ).strftime("%Y-%m-%d"),
                    "factor": factor,
                    "ic": float(values[factor].corr(values["forward20"], method="spearman")),
                    "n": int(len(values)),
                }
            )
    return pd.DataFrame(rows)


def adaptive_weights(ic: pd.DataFrame, dt: pd.Timestamp, lookback: int = 24) -> dict[str, float]:
    history = ic[pd.to_datetime(ic["available_date"]) < dt]
    if history.empty:
        return {}
    recent_dates = sorted(history["date"].unique())[-lookback:]
    means = history[history["date"].isin(recent_dates)].groupby("factor")["ic"].mean()
    stable = means[means.abs() >= 0.015]
    if stable.empty:
        return {}
    return (stable / stable.abs().sum()).to_dict()


def build_targets(
    prices: dict[str, pd.DataFrame],
    panels: dict[pd.Timestamp, pd.DataFrame],
    market: pd.DataFrame,
    variant: AlphaVariant,
    ic: pd.DataFrame,
) -> tuple[dict[pd.Timestamp, pd.Series], pd.DataFrame]:
    columns = prices["close"].columns
    market_by_date = market.set_index(pd.to_datetime(market["date"]))
    targets: dict[pd.Timestamp, pd.Series] = {}
    debug_rows: list[dict] = []
    formal_start = pd.to_datetime(min(panel_date for panel_date in panels))
    for dt, panel in panels.items():
        if dt < formal_start:
            continue
        weights = adaptive_weights(ic, dt) if variant.name == "adaptive_ic" else variant.weights
        score = factor_score(panel, weights)
        ranked = score.dropna().sort_values(ascending=False)
        market_state = market_by_date.loc[dt].to_dict()
        gross = exposure_for_market(market_state, variant.exposure_mode)
        target = equal_weight_targets(columns, ranked, variant.positions, gross)
        targets[dt] = target
        debug_rows.append(
            {
                "date": dt.strftime("%Y-%m-%d"),
                "strategy": variant.name,
                "gross": gross,
                "candidate_count": int(len(ranked)),
                "selected": ",".join(ranked.head(variant.positions).index),
                "factor_weights": json.dumps(weights, ensure_ascii=False, sort_keys=True),
            }
        )
    return targets, pd.DataFrame(debug_rows)


def annual_metrics(
    raw_perf: pd.DataFrame, benchmark: pd.DataFrame, start_date: str
) -> pd.DataFrame:
    perf = raw_perf.copy()
    perf["date"] = pd.to_datetime(perf["date"])
    frame = perf.merge(benchmark, on="date", how="inner")
    frame = frame[frame["date"] >= pd.to_datetime(start_date)]
    rows: list[dict] = []
    for year, group in frame.groupby(frame["date"].dt.year):
        strategy_nav = group["portfolio_value"] / group["portfolio_value"].iloc[0]
        benchmark_nav = group["benchmark_close"] / group["benchmark_close"].iloc[0]
        rows.append(
            {
                "year": int(year),
                "strategy_return": float(strategy_nav.iloc[-1] - 1.0),
                "benchmark_return": float(benchmark_nav.iloc[-1] - 1.0),
                "excess_return": float(strategy_nav.iloc[-1] - benchmark_nav.iloc[-1]),
                "strategy_mdd": float((strategy_nav / strategy_nav.cummax() - 1.0).min()),
            }
        )
    return pd.DataFrame(rows)


def period_metrics(
    raw_perf: pd.DataFrame, benchmark: pd.DataFrame, start: str, end: str, initial_cash: float
) -> dict:
    perf = raw_perf.copy()
    perf["date"] = pd.to_datetime(perf["date"])
    subset = perf[
        (perf["date"] >= pd.to_datetime(start)) & (perf["date"] <= pd.to_datetime(end))
    ].copy()
    if subset.empty:
        return {}
    rebased = subset.copy()
    rebased["portfolio_value"] = (
        rebased["portfolio_value"] / rebased["portfolio_value"].iloc[0] * initial_cash
    )
    benchmark_subset = benchmark[
        (benchmark["date"] >= pd.to_datetime(start)) & (benchmark["date"] <= pd.to_datetime(end))
    ]
    return benchmark_metrics(rebased, benchmark_subset, start, initial_cash)


def save_strategy(
    output_dir: Path,
    name: str,
    raw_perf: pd.DataFrame,
    trades: pd.DataFrame,
    debug: pd.DataFrame,
    annual: pd.DataFrame,
    summary: dict,
) -> None:
    run_dir = output_dir / name
    atomic_write_csv(raw_perf, run_dir / "local_raw_perf.csv")
    atomic_write_csv(trades, run_dir / "local_trades.csv")
    atomic_write_csv(debug, run_dir / "signal_debug.csv")
    atomic_write_csv(annual, run_dir / "annual_metrics.csv")
    atomic_write_text(
        run_dir / "performance.json", json.dumps(summary, ensure_ascii=False, indent=2, default=str)
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Explore stock-only alpha factors with PIT data.")
    parser.add_argument("--start-date", default="2021-07-01")
    parser.add_argument("--warmup-start-date", default="2020-01-02")
    parser.add_argument("--end-date", default="2026-07-16")
    parser.add_argument("--validation-start", default="2024-01-01")
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument("--prices-file", type=Path, default=DEFAULT_PRICES_FILE)
    parser.add_argument("--basic-cache", type=Path, default=DEFAULT_BASIC_CACHE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = make_cfg(args)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    prices = load_prices(cfg.prices_file, cfg)
    required = {
        "raw_open",
        "raw_close",
        "is_st",
        "is_listed",
        "is_suspended",
        "up_limit",
        "down_limit",
    }
    missing = sorted(required - set(prices))
    if missing:
        raise RuntimeError(f"Strict PIT fields are missing: {missing}")

    benchmark = fetch_benchmark(args.warmup_start_date, args.end_date)
    panels, market = build_factor_panels(prices, args.start_date, args.basic_cache)
    attach_forward_returns(panels, prices["close"])
    factor_names = sorted({factor for variant in variants() for factor in variant.weights})
    factor_names.extend(
        ["mom20", "mom60", "mom120", "small_size", "small_float_size", "sales_yield"]
    )
    factor_names = sorted(set(factor_names))
    ic = factor_ic_table(panels, factor_names)
    atomic_write_csv(ic, args.output_dir / "factor_ic_monthly.csv")
    atomic_write_csv(market, args.output_dir / "market_state_monthly.csv")
    ic_summary = (
        ic.assign(
            period=np.where(
                pd.to_datetime(ic["date"]) < pd.to_datetime(args.validation_start),
                "discovery",
                "validation",
            )
        )
        .groupby(["period", "factor"])
        .agg(
            ic_mean=("ic", "mean"),
            ic_median=("ic", "median"),
            ic_std=("ic", "std"),
            positive_rate=("ic", lambda x: float((x > 0).mean())),
            months=("ic", "count"),
        )
        .reset_index()
    )
    ic_summary["ic_ir"] = ic_summary["ic_mean"] / ic_summary["ic_std"].replace(0.0, np.nan)
    atomic_write_csv(ic_summary, args.output_dir / "factor_ic_summary.csv")

    all_variants = variants() + [
        AlphaVariant("adaptive_ic", {}, positions=10, exposure_mode="risk_tier")
    ]
    rows: list[dict] = []
    for variant in all_variants:
        targets, debug = build_targets(prices, panels, market, variant, ic)
        targets = {
            dt: weights for dt, weights in targets.items() if dt >= pd.to_datetime(args.start_date)
        }
        debug = debug[pd.to_datetime(debug["date"]) >= pd.to_datetime(args.start_date)].copy()
        result = run_local_backtest(
            prices,
            targets,
            BacktestConfig(
                initial_cash=args.initial_cash, buy_cost=0.0003, sell_cost=0.0013, min_cost=5.0
            ),
            strategy_name=variant.name,
        )
        full = benchmark_metrics(result.raw_perf, benchmark, args.start_date, args.initial_cash)
        discovery_end = (pd.to_datetime(args.validation_start) - pd.Timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
        discovery = period_metrics(
            result.raw_perf, benchmark, args.start_date, discovery_end, args.initial_cash
        )
        validation = period_metrics(
            result.raw_perf, benchmark, args.validation_start, args.end_date, args.initial_cash
        )
        annual = annual_metrics(result.raw_perf, benchmark, args.start_date)
        row = {
            "strategy": variant.name,
            "full_return": full.get("strategy_total_return"),
            "full_excess": full.get("excess_total_return"),
            "full_sharpe": full.get("sharpe"),
            "full_mdd": full.get("max_drawdown"),
            "discovery_return": discovery.get("strategy_total_return"),
            "discovery_excess": discovery.get("excess_total_return"),
            "validation_return": validation.get("strategy_total_return"),
            "validation_excess": validation.get("excess_total_return"),
            "validation_sharpe": validation.get("sharpe"),
            "validation_mdd": validation.get("max_drawdown"),
            "avg_gross": full.get("avg_gross_leverage"),
            "turnover": full.get("turnover_on_initial_cash"),
            "trade_count": result.summary["trade_count"],
            "fees": result.summary["total_fees"],
            "years_positive": int((annual["strategy_return"] > 0).sum()),
            "years_beat_hs300": int((annual["excess_return"] > 0).sum()),
        }
        rows.append(row)
        save_strategy(
            args.output_dir,
            variant.name,
            result.raw_perf,
            result.trades,
            debug,
            annual,
            {"variant": asdict(variant), "metrics": row},
        )
        print(json.dumps(row, ensure_ascii=False), flush=True)

    summary = pd.DataFrame(rows).sort_values(["validation_return", "full_return"], ascending=False)
    atomic_write_csv(summary, args.output_dir / "strategy_summary.csv")
    report = {
        "config": {
            **vars(args),
            "prices_file": str(args.prices_file),
            "basic_cache": str(args.basic_cache),
            "output_dir": str(args.output_dir),
        },
        "data_policy": "PIT adjusted prices for factors, raw open for execution, monthly point-in-time daily_basic snapshots, main-board non-ST stocks only.",
        "selection_policy": "All predefined variants are reported. Discovery is before 2024; validation begins 2024-01-01. Adaptive IC only uses factor IC available at least 30 days earlier.",
        "results": summary.to_dict("records"),
    }
    atomic_write_text(
        args.output_dir / "exploration_report.json",
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
    )
    print(summary.to_csv(index=False))
    print(f"Output: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
