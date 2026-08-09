"""Walk-forward / rolling-OOS evaluation — the multi-window anti-overfit lens.

WHY: a single train/val/test split has one OOS window — "test Sharpe 0.57 vs val
1.11" cannot tell systematic decay from one unlucky regime. Walk-forward evaluates
the strategy across MANY rolling OOS windows so decay/regime-dependence is visible.

Two layers:
  1. `walk_forward_stability` (primary, fast): slice an ALREADY-PRODUCED raw_perf
     equity curve into rolling test windows, compute metrics per window, aggregate.
     No re-backtest — just `segment_metrics` over rolling windows. Use this to
     diagnose any strategy's cross-regime stability.
  2. `walk_forward_folds` + `walk_forward_evaluate` (nested-ready): generate
     (train, test) folds with an EMBARGO gap (>= longest forward-return horizon) to
     kill label-overlap leakage, and re-run the strategy per fold with parameters
     fit on each train window. This is the path for strategies with train-derived
     params / future nested factor re-selection.

Convention: all dates are trading-day Timestamps from the strategy's own index.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ashare_quant.benchmark import fetch_benchmark
# segment_metrics is imported lazily inside the functions that need it, so this module
# imports cleanly even when the (private) research/ layer is absent from a public clone.


def rolling_test_windows(
    index: pd.DatetimeIndex,
    test_years: float = 2.0,
    step_years: float = 1.0,
    min_days: int = 252,
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Forward-rolling test windows of `test_years` length, stepping `step_years`."""
    windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    start = index.min()
    end_max = index.max()
    step = pd.DateOffset(years=step_years)
    span = pd.DateOffset(years=test_years)
    cursor = start
    while cursor + span <= end_max + pd.Timedelta(days=1):
        w_end = cursor + span
        n_days = ((index >= cursor) & (index < w_end)).sum()
        if n_days >= min_days:
            windows.append((cursor, w_end - pd.Timedelta(days=1)))
        cursor = cursor + step
    return windows


def walk_forward_folds(
    index: pd.DatetimeIndex,
    train_years: float = 7.0,
    test_years: float = 2.0,
    step_years: float = 1.0,
    embargo_days: int = 21,
) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    """Train/test folds with an embargo gap (kills forward-return label leakage).

    Each fold = (train_start, train_end, test_start, test_end) where
    test_start = train_end + embargo_days. Expanding train (sliding can be emulated
    by also advancing train_start — left to the caller). embargo_days should be
    >= the strategy's longest forward-return horizon (e.g. 21 for a 20-day horizon).
    """
    folds: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]] = []
    start = index.min()
    end_max = index.max()
    train_span = pd.DateOffset(years=train_years)
    test_span = pd.DateOffset(years=test_years)
    step = pd.DateOffset(years=step_years)
    train_start = start
    while True:
        train_end = train_start + train_span
        test_start = train_end + pd.Timedelta(days=embargo_days)
        test_end = test_start + test_span
        if test_start >= end_max:
            break
        test_end = min(test_end, end_max)
        folds.append((train_start, train_end, test_start, test_end))
        train_start = train_start + step
    return folds


def walk_forward_stability(
    raw_perf: pd.DataFrame,
    benchmark: pd.DataFrame,
    test_years: float = 2.0,
    step_years: float = 1.0,
    min_days: int = 252,
) -> dict:
    """Rolling-window OOS stability of a strategy's realized equity curve.

    Slices `raw_perf` into rolling `test_years` windows (step `step_years`) and runs
    `segment_metrics` per window. Aggregate answers: is Sharpe stable across regimes,
    or does it decay? `sharpe_decay_slope` < 0 and later-half < earlier-half ⇒ systematic.
    """
    from ashare_quant.research.long_horizon import segment_metrics  # lazy: private dep

    raw_perf = raw_perf.copy()
    raw_perf["date"] = pd.to_datetime(raw_perf["date"])
    frame = raw_perf.set_index("date")
    windows = rolling_test_windows(frame.index, test_years, step_years, min_days)

    rows: list[dict] = []
    for ws, we in windows:
        try:
            m = segment_metrics(raw_perf, benchmark, ws.strftime("%Y-%m-%d"), we.strftime("%Y-%m-%d"))
        except ValueError:
            continue
        rows.append({
            "window": f"{ws.strftime('%Y-%m')}..{we.strftime('%Y-%m')}",
            "sharpe": m.get("sharpe"),
            "ir": m.get("information_ratio"),
            "annualized_return": m.get("annualized_return"),
            "excess": m.get("excess"),
            "max_drawdown": m.get("max_drawdown"),
            "annual_win_rate": m.get("annual_win_rate"),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return {"per_window": df, "aggregate": {"n_windows": 0}}

    n = len(df)
    half = n // 2
    early = df["sharpe"].iloc[:half].mean() if half > 0 else float("nan")
    late = df["sharpe"].iloc[half:].mean() if half > 0 else float("nan")
    slope = float(np.polyfit(range(n), df["sharpe"].values, 1)[0]) if n >= 2 else 0.0
    agg = {
        "n_windows": n,
        "sharpe_mean": float(df["sharpe"].mean()),
        "sharpe_std": float(df["sharpe"].std(ddof=1)) if n > 1 else 0.0,
        "sharpe_min": float(df["sharpe"].min()),
        "sharpe_max": float(df["sharpe"].max()),
        "sharpe_median": float(df["sharpe"].median()),
        "ir_positive_frac": float((df["ir"] > 0).mean()),
        "beat_benchmark_frac": float((df["excess"] > 0).mean()),
        "sharpe_decay_slope_per_window": slope,
        "early_half_mean_sharpe": float(early),
        "late_half_mean_sharpe": float(late),
        "systematic_decay": bool(slope < 0 and late < early),
    }
    return {"per_window": df, "aggregate": agg}


def walk_forward_evaluate(make_targets_fn, prices, backtest_config, fold_config: dict, strategy_name: str) -> list[dict]:
    """Nested-ready: re-run the strategy per fold with params fit on each train window.

    `make_targets_fn(train_start, train_end, test_start, test_end) -> targets_dict`
    must produce targets for the [test_start, test_end] window using parameters
    estimated ONLY from [train_start, train_end]. Each fold runs a FRESH backtest
    (reset initial_cash, zero positions) for isolation.
    """
    from ashare_quant.backtest import BacktestConfig, run_local_backtest  # local import (cycle-safe)
    from ashare_quant.research.long_horizon import segment_metrics  # lazy: private dep

    close = prices["close"]
    folds = walk_forward_folds(close.index, **fold_config)
    out: list[dict] = []
    for i, (ts, te, xs, xe) in enumerate(folds):
        targets = make_targets_fn(ts, te, xs, xe)
        result = run_local_backtest(prices, targets, backtest_config, strategy_name=f"{strategy_name}_fold{i}")
        try:
            m = segment_metrics(result.raw_perf, None, xs.strftime("%Y-%m-%d"), xe.strftime("%Y-%m-%d"))
        except ValueError:
            m = {}
        out.append({"fold": i, "train": f"{ts.date()}..{te.date()}", "test": f"{xs.date()}..{xe.date()}", **m})
    return out


def _main():
    p = argparse.ArgumentParser(description="Walk-forward / rolling-OOS stability on a raw_perf CSV")
    p.add_argument("--raw-perf", type=Path, required=True, help="path to local_raw_perf.csv")
    p.add_argument("--test-years", type=float, default=2.0)
    p.add_argument("--step-years", type=float, default=1.0)
    p.add_argument("--min-days", type=int, default=252)
    p.add_argument("--warmup-start-date", default="2006-01-04")
    p.add_argument("--end-date", default="2026-07-17")
    p.add_argument("--output-dir", type=Path, default=Path("data/research/walk_forward"))
    args = p.parse_args()

    raw_perf = pd.read_csv(args.raw_perf)
    benchmark = fetch_benchmark(args.warmup_start_date, args.end_date)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    res = walk_forward_stability(raw_perf, benchmark, args.test_years, args.step_years, args.min_days)
    df = res["per_window"]
    agg = res["aggregate"]

    print(f"\n=== Walk-forward 滚动 OOS 稳定性 ({args.raw_perf.name}, 窗口 {args.test_years}年/步 {args.step_years}年) ===")
    if df.empty:
        print("无有效窗口。"); return
    print(df.to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
    print(f"\n--- 聚合 ({agg['n_windows']} 窗口) ---")
    print(f"  Sharpe: mean {agg['sharpe_mean']:.3f}  std {agg['sharpe_std']:.3f}  "
          f"min {agg['sharpe_min']:.3f}  max {agg['sharpe_max']:.3f}  median {agg['sharpe_median']:.3f}")
    print(f"  IR>0 窗口占比: {agg['ir_positive_frac']:.0%}   跑赢基准窗口占比: {agg['beat_benchmark_frac']:.0%}")
    print(f"  衰减斜率(每窗口): {agg['sharpe_decay_slope_per_window']:+.4f}   "
          f"前半段均值 {agg['early_half_mean_sharpe']:.3f} -> 后半段 {agg['late_half_mean_sharpe']:.3f}")
    verdict = "[!] 系统性衰减" if agg["systematic_decay"] else ("系统性提升" if agg["sharpe_decay_slope_per_window"] > 0 else "无趋势")
    print(f"  >>> 判定: {verdict}")

    df.assign(window_start=df["window"]).to_csv(args.output_dir / "walk_forward_per_window.csv", index=False)
    (args.output_dir / "walk_forward_aggregate.json").write_text(
        json.dumps(agg, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nOutput: {args.output_dir.resolve()}")


if __name__ == "__main__":
    _main()
