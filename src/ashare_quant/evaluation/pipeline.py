"""Main evaluation pipeline — orchestrates all modules into a single run.

Usage:
    python -m ashare_quant.evaluation.pipeline \\
        --strategy-name composite_alpha_v2 \\
        --raw-perf data/backtests/composite_alpha_v2_test/local_raw_perf.csv \\
        --output-dir data/evaluation/composite_alpha_v2 \\
        --n-trials 50
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

from ashare_quant.benchmark import fetch_benchmark
from ashare_quant.evaluation import attribution, costs, metrics, report, risk, significance
from ashare_quant.paths import PROJECT_ROOT

# Default train/val/test boundaries — canonical source is research/splits.py (the
# modern 2015-2021 / 2022-2023 / 2024+ split). The strategy layer already reports on
# these boundaries; the eval pipeline MUST match, or its dev/val/test segmentation
# diverges from the strategy's own (the old inline 2007-2013 values were the legacy
# split and caused exactly that mismatch). Fall back to inline legacy values ONLY if
# the research layer is unavailable, so this package still imports standalone.
try:
    from ashare_quant.research.splits import (
        DEVELOPMENT_START as _DEV_START,
        DEVELOPMENT_END as _DEV_END,
        VALIDATION_START as _VAL_START,
        VALIDATION_END as _VAL_END,
        TEST_START as _TEST_START,
    )
except ImportError:  # standalone import without the research layer
    _DEV_START, _DEV_END = "2007-01-01", "2013-12-31"
    _VAL_START, _VAL_END = "2014-01-01", "2020-12-31"
    _TEST_START = "2021-01-01"


def evaluate_strategy(
    strategy_name: str,
    raw_perf_path: Path,
    benchmark: pd.DataFrame,
    output_dir: Path,
    n_trials: int = 1,
    dev_start: str = _DEV_START,
    dev_end: str = _DEV_END,
    val_start: str = _VAL_START,
    val_end: str = _VAL_END,
    test_start: str = _TEST_START,
    test_end: str = "2026-07-17",
    warmup_start: str = "2006-01-04",
    walk_forward: bool = False,
    wf_test_years: float = 2.0,
    wf_step_years: float = 1.0,
) -> dict:
    """Complete evaluation pipeline. Returns all results + writes report to output_dir."""

    # Lazy imports from the (private) research layer: the pipeline orchestrator needs
    # segment_metrics for dev/val/test segmentation. The module imports cleanly without
    # research/ installed; calling evaluate_strategy requires it.
    from ashare_quant.research.long_horizon import segment_metrics
    if walk_forward:
        from ashare_quant.research.walk_forward import walk_forward_stability

    print("=== Strategy Evaluation Pipeline ===", flush=True)
    print(f"Strategy: {strategy_name}", flush=True)
    print(f"Data: {raw_perf_path}", flush=True)
    print(f"Output: {output_dir}", flush=True)
    print(f"N trials (for DSR): {n_trials}", flush=True)

    # 1. Load data
    raw_perf = pd.read_csv(raw_perf_path)
    raw_perf["date"] = pd.to_datetime(raw_perf["date"])
    returns = raw_perf.set_index("date")["returns"]
    equity = raw_perf.set_index("date")["portfolio_value"]

    # Benchmark returns (merge with raw_perf dates)
    bm = benchmark.copy()
    bm["date"] = pd.to_datetime(bm["date"])
    bm = bm.set_index("date")["benchmark_close"]
    bm_returns = bm.pct_change().reindex(returns.index)

    # 2. Core metrics
    print("\n[1/6] Computing core metrics...", flush=True)
    t0 = time.time()
    core = metrics.all_metrics(returns, equity, bm_returns)
    print(f"  done in {time.time()-t0:.1f}s — Sharpe={core['sharpe']:.3f} Sortino={core['sortino']:.3f} Calmar={core['calmar']:.3f}", flush=True)

    # 3. Statistical significance
    print("[2/6] Computing statistical significance...", flush=True)
    t0 = time.time()
    sig = significance.evaluate(returns, n_trials=n_trials)
    dsr = sig.get("dsr", 0)
    pbo_val = sig.get("pbo", {}).get("pbo", 0)
    print(f"  done in {time.time()-t0:.1f}s — DSR={dsr:.3f} PBO={pbo_val:.3f}", flush=True)

    # 4. Risk analysis
    print("[3/6] Computing risk analysis (drawdown/stress/regime)...", flush=True)
    t0 = time.time()
    risk_results = risk.full_analysis(raw_perf, benchmark)
    ssr = risk_results.get("sharpe_stability_ratio", 0)
    print(f"  done in {time.time()-t0:.1f}s — SharpeStability={ssr:.3f}", flush=True)

    # 5. Attribution
    print("[4/6] Computing attribution...", flush=True)
    t0 = time.time()
    attrib = attribution.full_analysis(raw_perf, benchmark)
    beta = attrib.get("capm_regression", {}).get("beta", 0)
    alpha_t = attrib.get("capm_regression", {}).get("alpha_tstat", 0)
    print(f"  done in {time.time()-t0:.1f}s — Beta={beta:.3f} Alpha-t={alpha_t:.2f}", flush=True)

    # 6. Cost analysis
    print("[5/6] Computing cost analysis...", flush=True)
    t0 = time.time()
    cost_results = costs.full_analysis(raw_perf)
    turnover = cost_results.get("turnover", {}).get("single_side_annual_turnover", 0)
    drag = cost_results.get("cost_drag", {}).get("total_drag_annual_pct", 0)
    print(f"  done in {time.time()-t0:.1f}s — Turnover={turnover:.1f}x Drag={drag:.3f}%", flush=True)

    # 7. Three-segment breakdown (reuse existing segment_metrics)
    print("[6/6] Computing segment metrics + generating report...", flush=True)
    t0 = time.time()
    segments = {}
    seg_labels = [
        ("development", dev_start, dev_end),
        ("validation", val_start, val_end),
    ]
    # test segment only if data extends past val_end
    if returns.index.max() >= pd.Timestamp(test_start):
        seg_labels.append(("test", test_start, test_end))

    for label, s, e in seg_labels:
        try:
            segments[label] = segment_metrics(raw_perf, benchmark, s, e)
        except (ValueError, KeyError):
            segments[label] = {}

    # 8. Assemble all results
    all_results = {
        "strategy_name": strategy_name,
        "n_trials": n_trials,
        "core": core,
        "significance": sig,
        "risk": risk_results,
        "attribution": attrib,
        "costs": cost_results,
        "segments": segments,
    }

    # 8b. Optional walk-forward / rolling-OOS stability (multi-window anti-overfit lens)
    if walk_forward:
        print("[7] Computing walk-forward rolling-OOS stability...", flush=True)
        t0 = time.time()
        wf = walk_forward_stability(raw_perf, benchmark, wf_test_years, wf_step_years)
        agg = wf.get("aggregate", {})
        all_results["walk_forward"] = agg
        if agg.get("n_windows", 0) > 0:
            verdict = "[!] systematic decay" if agg.get("systematic_decay") else "no systematic decay"
            print(f"  done in {time.time()-t0:.1f}s — {agg['n_windows']} windows, "
                  f"Sharpe mean={agg['sharpe_mean']:.3f} min={agg['sharpe_min']:.3f}, "
                  f"early {agg['early_half_mean_sharpe']:.3f} -> late {agg['late_half_mean_sharpe']:.3f} ({verdict})", flush=True)

    # 9. Generate report
    report.generate_report(strategy_name, raw_perf, benchmark, all_results, output_dir)
    print(f"  done in {time.time()-t0:.1f}s", flush=True)

    print(f"\n=== Evaluation complete. Output: {output_dir.resolve()} ===")
    return all_results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy-name", required=True, help="Strategy name for the report title")
    parser.add_argument("--raw-perf", type=Path, required=True, help="Path to local_raw_perf.csv")
    parser.add_argument("--output-dir", type=Path, required=True, help="Output directory for report + charts")
    parser.add_argument("--n-trials", type=int, default=1, help="Number of strategy variations tried (for DSR)")
    parser.add_argument("--warmup-start-date", default="2006-01-04")
    parser.add_argument("--end-date", default="2026-07-17")
    parser.add_argument("--prices-file", type=Path, default=PROJECT_ROOT / "data/offline/a_share_history_tushare/prices_long.csv")
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument("--output-dir-override", type=Path, default=None)
    parser.add_argument("--walk-forward", action="store_true", help="add rolling-OOS walk-forward stability (multi-window anti-overfit lens)")
    parser.add_argument("--test-years", type=float, default=2.0, help="walk-forward test window length (years)")
    parser.add_argument("--step-years", type=float, default=1.0, help="walk-forward step (years)")
    args = parser.parse_args()

    if args.output_dir_override:
        args.output_dir = args.output_dir_override

    # Fetch benchmark
    benchmark = fetch_benchmark(args.warmup_start_date, args.end_date)

    evaluate_strategy(
        strategy_name=args.strategy_name,
        raw_perf_path=args.raw_perf,
        benchmark=benchmark,
        output_dir=args.output_dir,
        n_trials=args.n_trials,
        walk_forward=args.walk_forward,
        wf_test_years=args.test_years,
        wf_step_years=args.step_years,
    )


if __name__ == "__main__":
    main()
