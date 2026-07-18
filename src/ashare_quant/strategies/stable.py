#!/usr/bin/env python3
"""Run the discovery-frozen, stock-only stable alpha strategy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ashare_quant.benchmark import fetch_benchmark
from ashare_quant.paths import DEFAULT_PRICES_FILE, PROJECT_ROOT
from ashare_quant.research.factors import (
    atomic_write_csv,
    atomic_write_text,
    build_factor_panels,
    make_cfg,
)
from ashare_quant.research.stability import (
    DEFAULT_BASIC_CACHE,
    DEFAULT_INDUSTRY_FILE,
    ExperimentCase,
    FACTOR_FAMILIES,
    evaluate_case,
    industry_snapshot,
    load_industry_history,
)
from ashare_quant.strategies.v4 import load_prices


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data/backtests/stable_stock_alpha"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run frozen stock-only stable alpha.")
    parser.add_argument("--start-date", default="2021-07-01")
    parser.add_argument("--warmup-start-date", default="2020-01-02")
    parser.add_argument("--end-date", default="2026-07-16")
    parser.add_argument("--validation-start", default="2024-01-01")
    parser.add_argument("--initial-cash", type=float, default=45_000.0)
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--prices-file", type=Path, default=DEFAULT_PRICES_FILE)
    parser.add_argument("--basic-cache", type=Path, default=DEFAULT_BASIC_CACHE)
    parser.add_argument("--industry-file", type=Path, default=DEFAULT_INDUSTRY_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cfg = make_cfg(args)
    prices = load_prices(cfg.prices_file, cfg)
    benchmark = fetch_benchmark(args.warmup_start_date, args.end_date)
    panels, market = build_factor_panels(prices, args.start_date, args.basic_cache)
    history = load_industry_history(args.industry_file)
    industries = {dt: industry_snapshot(history, dt) for dt in panels}
    strategy = ExperimentCase(
        name="stable_stock_alpha",
        family="balanced_small",
        weights=FACTOR_FAMILIES["balanced_small"],
        positions=10,
        rebalance_months=2,
        exposure_mode="soft_80",
        universe_mode="industry_neutral",
        rebalance_offset=1,
        industry_max_fraction=0.20,
    )
    row, annual = evaluate_case(
        strategy,
        prices,
        panels,
        market,
        industries,
        benchmark,
        args,
        slippage=args.slippage,
        save=True,
    )
    atomic_write_csv(annual, args.output_dir / "annual_metrics.csv")
    atomic_write_text(
        args.output_dir / "strategy_config.json",
        json.dumps(strategy.__dict__, ensure_ascii=False, indent=2),
    )
    print(json.dumps(row, ensure_ascii=False, indent=2))
    print(annual.to_string(index=False))
    print(f"Output: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
