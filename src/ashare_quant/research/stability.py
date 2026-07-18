#!/usr/bin/env python3
"""Parameter, cross-section, and slippage stability research for stock-only alpha."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ashare_quant.backtest import BacktestConfig, run_local_backtest
from ashare_quant.benchmark import benchmark_metrics, fetch_benchmark
from ashare_quant.paths import DEFAULT_PRICES_FILE, PROJECT_ROOT
from ashare_quant.research.factors import (
    atomic_write_csv,
    atomic_write_text,
    build_factor_panels,
    exposure_for_market,
    factor_score,
    make_cfg,
    period_metrics,
    winsor_zscore,
)
from ashare_quant.strategies.v4 import load_prices


DEFAULT_INDUSTRY_FILE = (
    PROJECT_ROOT / "data/offline/a_share_history_tushare/sw_l1_membership_history.csv"
)
DEFAULT_BASIC_CACHE = (
    PROJECT_ROOT / "data/offline/a_share_history_tushare/.daily_basic_monthly_cache"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data/backtests/stock_alpha_stability_20210701_20260716"
FINANCIAL_INDUSTRIES = {"银行", "非银金融"}
CYCLICAL_INDUSTRIES = {"煤炭", "钢铁", "有色金属", "石油石化", "基础化工", "建筑材料", "房地产"}


@dataclass(frozen=True)
class ExperimentCase:
    name: str
    family: str
    weights: dict[str, float]
    positions: int
    rebalance_months: int
    exposure_mode: str
    universe_mode: str
    min_amount20: float = 30_000_000.0
    rebalance_offset: int = 0
    industry_max_fraction: float = 0.20


FACTOR_FAMILIES = {
    "baseline": {
        "low_vol60": 0.25,
        "low_downside60": 0.15,
        "earnings_yield": 0.18,
        "book_yield": 0.12,
        "dividend_yield": 0.10,
        "volume_contraction": 0.10,
        "low_turnover": 0.10,
    },
    "defensive": {
        "low_vol60": 0.28,
        "low_residual_vol60": 0.14,
        "low_downside60": 0.14,
        "earnings_yield": 0.14,
        "book_yield": 0.10,
        "dividend_yield": 0.08,
        "low_turnover": 0.07,
        "volume_contraction": 0.05,
    },
    "balanced_small": {
        "low_vol60": 0.22,
        "low_downside60": 0.12,
        "earnings_yield": 0.16,
        "book_yield": 0.12,
        "dividend_yield": 0.08,
        "small_float_size": 0.10,
        "low_turnover": 0.10,
        "volume_contraction": 0.10,
    },
    "equal_core": {
        "low_vol60": 1.0,
        "low_downside60": 1.0,
        "earnings_yield": 1.0,
        "book_yield": 1.0,
        "dividend_yield": 1.0,
        "low_turnover": 1.0,
        "volume_contraction": 1.0,
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run stock-only stability experiments.")
    parser.add_argument("--start-date", default="2021-07-01")
    parser.add_argument("--warmup-start-date", default="2020-01-02")
    parser.add_argument("--end-date", default="2026-07-16")
    parser.add_argument("--validation-start", default="2024-01-01")
    parser.add_argument("--initial-cash", type=float, default=45_000.0)
    parser.add_argument("--prices-file", type=Path, default=DEFAULT_PRICES_FILE)
    parser.add_argument("--basic-cache", type=Path, default=DEFAULT_BASIC_CACHE)
    parser.add_argument("--industry-file", type=Path, default=DEFAULT_INDUSTRY_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def load_industry_history(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str)
    frame["in_date"] = pd.to_datetime(frame["in_date"], errors="coerce")
    frame["out_date"] = pd.to_datetime(frame["out_date"], errors="coerce")
    return frame.dropna(subset=["symbol", "in_date", "l1_name"])


def industry_snapshot(history: pd.DataFrame, dt: pd.Timestamp) -> pd.Series:
    available = history[
        (history["in_date"] <= dt) & (history["out_date"].isna() | (history["out_date"] >= dt))
    ]
    if available.empty:
        return pd.Series(dtype=str)
    latest = available.sort_values(["symbol", "in_date"]).drop_duplicates("symbol", keep="last")
    return latest.set_index("symbol")["l1_name"]


def value_composite(panel: pd.DataFrame) -> pd.Series:
    parts = [
        winsor_zscore(panel["earnings_yield"]),
        winsor_zscore(panel["book_yield"]),
        winsor_zscore(panel["dividend_yield"]),
    ]
    return pd.concat(parts, axis=1).mean(axis=1)


def apply_universe(
    panel: pd.DataFrame,
    mode: str,
    industry: pd.Series,
    min_amount20: float,
    positions: int,
    initial_cash: float,
) -> pd.DataFrame:
    result = panel.copy()
    eligible = result["eligible"].fillna(False) & (result["avg_amount20"] >= min_amount20)
    max_lot_price = initial_cash / positions / 100.0 * 0.95
    eligible &= result["raw_close"] <= max_lot_price
    base = result.loc[eligible]
    if base.empty:
        result["eligible"] = False
        result["industry"] = industry.reindex(result.index)
        return result

    cap_rank = base["total_mv"].rank(pct=True)
    low_vol_rank = base["low_vol60"].rank(pct=True)
    turnover_rank = base["low_turnover"].rank(pct=True)
    value_rank = value_composite(base).rank(pct=True)
    keep = pd.Series(True, index=base.index)
    if mode == "cap_ex_small20":
        keep &= cap_rank >= 0.20
    elif mode == "cap_small":
        keep &= cap_rank <= 1 / 3
    elif mode == "cap_mid":
        keep &= cap_rank.between(1 / 3, 2 / 3)
    elif mode == "cap_large":
        keep &= cap_rank >= 2 / 3
    elif mode == "lowvol_half":
        keep &= low_vol_rank >= 0.50
    elif mode == "highvol_half":
        keep &= low_vol_rank < 0.50
    elif mode == "value_half":
        keep &= value_rank >= 0.50
    elif mode == "growth_half":
        keep &= value_rank < 0.50
    elif mode == "lowturn_half":
        keep &= turnover_rank >= 0.50
    elif mode == "highturn_half":
        keep &= turnover_rank < 0.50
    elif mode in {"industry_neutral", "all"}:
        pass
    elif mode == "exclude_financial":
        keep &= ~industry.reindex(base.index).isin(FINANCIAL_INDUSTRIES)
    elif mode == "exclude_cyclical":
        keep &= ~industry.reindex(base.index).isin(CYCLICAL_INDUSTRIES)
    else:
        raise ValueError(f"Unsupported universe mode: {mode}")

    eligible.loc[base.index] &= keep
    result["eligible"] = eligible
    result["industry"] = industry.reindex(result.index)
    return result


def select_industry_neutral(
    ranked: pd.Series,
    industries: pd.Series,
    positions: int,
    maximum_fraction: float,
) -> list[str]:
    maximum_per_industry = max(1, math.ceil(positions * maximum_fraction))
    counts: dict[str, int] = {}
    selected: list[str] = []
    for symbol in ranked.index:
        industry = str(industries.get(symbol, "UNKNOWN"))
        if counts.get(industry, 0) >= maximum_per_industry:
            continue
        selected.append(symbol)
        counts[industry] = counts.get(industry, 0) + 1
        if len(selected) >= positions:
            break
    return selected


def build_case_targets(
    prices: dict[str, pd.DataFrame],
    panels: dict[pd.Timestamp, pd.DataFrame],
    market: pd.DataFrame,
    industry_by_date: dict[pd.Timestamp, pd.Series],
    case: ExperimentCase,
    initial_cash: float,
) -> tuple[dict[pd.Timestamp, pd.Series], pd.DataFrame]:
    columns = prices["close"].columns
    dates = sorted(panels)[case.rebalance_offset :: case.rebalance_months]
    market_by_date = market.set_index(pd.to_datetime(market["date"]))
    targets: dict[pd.Timestamp, pd.Series] = {}
    debug: list[dict] = []
    for dt in dates:
        panel = apply_universe(
            panels[dt],
            case.universe_mode,
            industry_by_date[dt],
            case.min_amount20,
            case.positions,
            initial_cash,
        )
        ranked = factor_score(panel, case.weights).dropna().sort_values(ascending=False)
        if case.universe_mode == "industry_neutral":
            selected = select_industry_neutral(
                ranked,
                panel["industry"],
                case.positions,
                case.industry_max_fraction,
            )
        else:
            selected = ranked.head(case.positions).index.tolist()
        market_state = market_by_date.loc[dt].to_dict()
        if case.exposure_mode == "soft_60":
            gross = 0.60 + 0.40 * exposure_for_market(market_state, "risk_tier")
        elif case.exposure_mode == "soft_80":
            gross = 0.80 + 0.20 * exposure_for_market(market_state, "risk_tier")
        else:
            gross = exposure_for_market(market_state, case.exposure_mode)
        target = pd.Series(0.0, index=columns)
        if selected:
            target.loc[selected] = gross / len(selected)
        targets[dt] = target
        debug.append(
            {
                "date": dt.strftime("%Y-%m-%d"),
                "strategy": case.name,
                "universe_mode": case.universe_mode,
                "candidate_count": len(ranked),
                "gross": gross,
                "selected": ",".join(selected),
            }
        )
    return targets, pd.DataFrame(debug)


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
        strategy_returns = strategy_nav.pct_change().fillna(0.0)
        benchmark_returns = benchmark_nav.pct_change().fillna(0.0)
        active = strategy_returns - benchmark_returns
        rows.append(
            {
                "year": int(year),
                "strategy_return": float(strategy_nav.iloc[-1] - 1.0),
                "benchmark_return": float(benchmark_nav.iloc[-1] - 1.0),
                "excess_return": float(strategy_nav.iloc[-1] - benchmark_nav.iloc[-1]),
                "sharpe": float(strategy_returns.mean() / strategy_returns.std() * np.sqrt(252))
                if strategy_returns.std() > 0
                else 0.0,
                "information_ratio": float(active.mean() / active.std() * np.sqrt(252))
                if active.std() > 0
                else 0.0,
                "max_drawdown": float((strategy_nav / strategy_nav.cummax() - 1.0).min()),
            }
        )
    return pd.DataFrame(rows)


def evaluate_case(
    case: ExperimentCase,
    prices: dict[str, pd.DataFrame],
    panels: dict[pd.Timestamp, pd.DataFrame],
    market: pd.DataFrame,
    industries: dict[pd.Timestamp, pd.Series],
    benchmark: pd.DataFrame,
    args: argparse.Namespace,
    *,
    slippage: float,
    save: bool = False,
) -> tuple[dict, pd.DataFrame]:
    targets, debug = build_case_targets(prices, panels, market, industries, case, args.initial_cash)
    result = run_local_backtest(
        prices,
        targets,
        BacktestConfig(
            initial_cash=args.initial_cash,
            buy_cost=0.0003,
            sell_cost=0.0013,
            slippage=slippage,
            min_cost=5.0,
        ),
        strategy_name=case.name,
    )
    discovery_end = (pd.to_datetime(args.validation_start) - pd.Timedelta(days=1)).strftime(
        "%Y-%m-%d"
    )
    full = benchmark_metrics(result.raw_perf, benchmark, args.start_date, args.initial_cash)
    discovery = period_metrics(
        result.raw_perf, benchmark, args.start_date, discovery_end, args.initial_cash
    )
    validation = period_metrics(
        result.raw_perf, benchmark, args.validation_start, args.end_date, args.initial_cash
    )
    annual = annual_metrics(result.raw_perf, benchmark, args.start_date)
    discovery_annual = annual[annual["year"] <= 2023]
    years = max(full["days"] / 252.0, 1 / 252.0)
    annualized = (1.0 + full["strategy_total_return"]) ** (1.0 / years) - 1.0
    row = {
        "strategy": case.name,
        "family": case.family,
        "positions": case.positions,
        "rebalance_months": case.rebalance_months,
        "exposure_mode": case.exposure_mode,
        "universe_mode": case.universe_mode,
        "min_amount20": case.min_amount20,
        "slippage": slippage,
        "full_return": full["strategy_total_return"],
        "annualized_return": annualized,
        "full_excess": full["excess_total_return"],
        "full_sharpe": full["sharpe"],
        "full_information_ratio": full["information_ratio"],
        "full_mdd": full["max_drawdown"],
        "discovery_return": discovery["strategy_total_return"],
        "discovery_excess": discovery["excess_total_return"],
        "discovery_sharpe": discovery["sharpe"],
        "discovery_information_ratio": discovery["information_ratio"],
        "discovery_min_annual_excess": discovery_annual["excess_return"].min(),
        "validation_return": validation["strategy_total_return"],
        "validation_excess": validation["excess_total_return"],
        "validation_sharpe": validation["sharpe"],
        "validation_information_ratio": validation["information_ratio"],
        "validation_mdd": validation["max_drawdown"],
        "years_beat_hs300": int((annual["excess_return"] > 0).sum()),
        "minimum_annual_excess": annual["excess_return"].min(),
        "turnover": full["turnover_on_initial_cash"],
        "trade_count": result.summary["trade_count"],
        "fees": result.summary["total_fees"],
        "slippage_cost": result.summary["total_slippage_cost"],
    }
    row["discovery_score"] = (
        row["discovery_sharpe"]
        + 0.50 * row["discovery_information_ratio"]
        + 2.0 * row["discovery_min_annual_excess"]
        - 0.002 * row["turnover"]
    )
    if save:
        run_dir = args.output_dir / case.name / f"slippage_{slippage:.4f}"
        atomic_write_csv(result.raw_perf, run_dir / "local_raw_perf.csv")
        atomic_write_csv(result.trades, run_dir / "local_trades.csv")
        atomic_write_csv(debug, run_dir / "signal_debug.csv")
        atomic_write_csv(annual, run_dir / "annual_metrics.csv")
        atomic_write_text(
            run_dir / "performance.json", json.dumps(row, ensure_ascii=False, indent=2)
        )
    return row, annual


def search_cases() -> list[ExperimentCase]:
    cases: list[ExperimentCase] = []
    for family, weights in FACTOR_FAMILIES.items():
        for positions in [8, 10, 12]:
            for interval in [1, 2]:
                for exposure in ["risk_tier", "full"]:
                    for universe in ["all", "cap_ex_small20", "industry_neutral"]:
                        name = f"{family}_n{positions}_m{interval}_{exposure}_{universe}"
                        cases.append(
                            ExperimentCase(
                                name,
                                family,
                                weights,
                                positions,
                                interval,
                                exposure,
                                universe,
                            )
                        )
    return cases


def diagnostic_cases() -> list[ExperimentCase]:
    modes = [
        "cap_small",
        "cap_mid",
        "cap_large",
        "lowvol_half",
        "highvol_half",
        "value_half",
        "growth_half",
        "lowturn_half",
        "highturn_half",
        "exclude_financial",
        "exclude_cyclical",
    ]
    return [
        ExperimentCase(
            name=f"diagnostic_{mode}",
            family="baseline",
            weights=FACTOR_FAMILIES["baseline"],
            positions=10,
            rebalance_months=1,
            exposure_mode="risk_tier",
            universe_mode=mode,
        )
        for mode in modes
    ]


def choose_frozen_case(summary: pd.DataFrame) -> ExperimentCase:
    grouped = (
        summary.groupby(["family", "rebalance_months", "exposure_mode"])
        .agg(
            median_score=("discovery_score", "median"),
            worst_score=("discovery_score", "min"),
            median_min_excess=("discovery_min_annual_excess", "median"),
        )
        .reset_index()
    )
    grouped["robust_score"] = (
        grouped["median_score"] + 0.35 * grouped["worst_score"] + grouped["median_min_excess"]
    )
    winner = grouped.sort_values("robust_score", ascending=False).iloc[0]
    subset = summary[
        (summary["family"] == winner["family"])
        & (summary["rebalance_months"] == winner["rebalance_months"])
        & (summary["exposure_mode"] == winner["exposure_mode"])
        & (summary["positions"] == 10)
    ]
    universe_scores = subset.groupby("universe_mode")["discovery_score"].median()
    universe = str(universe_scores.idxmax())
    family = str(winner["family"])
    return ExperimentCase(
        name="frozen_stable_stock_alpha",
        family=family,
        weights=FACTOR_FAMILIES[family],
        positions=10,
        rebalance_months=int(winner["rebalance_months"]),
        exposure_mode=str(winner["exposure_mode"]),
        universe_mode=universe,
    )


def write_report(
    args: argparse.Namespace,
    frozen: ExperimentCase,
    frozen_row: dict,
    frozen_annual: pd.DataFrame,
    slippage: pd.DataFrame,
    diagnostics: pd.DataFrame,
) -> None:
    passed = bool((frozen_annual["excess_return"] > 0).all())
    annual_table = frozen_annual.to_markdown(index=False, floatfmt=".4f")
    slippage_table = slippage[
        [
            "slippage",
            "full_return",
            "annualized_return",
            "full_sharpe",
            "full_mdd",
            "years_beat_hs300",
            "minimum_annual_excess",
        ]
    ].to_markdown(index=False, floatfmt=".4f")
    diagnostic_table = (
        diagnostics[
            [
                "strategy",
                "full_return",
                "full_sharpe",
                "full_mdd",
                "years_beat_hs300",
                "minimum_annual_excess",
            ]
        ]
        .sort_values("strategy")
        .to_markdown(index=False, floatfmt=".4f")
    )
    content = f"""# 纯股票策略稳定性研究报告

## 验收结论

年度超额硬约束：{"通过" if passed else "未通过"}。

该结论不因累计收益较高而放宽。策略选择仅使用2021-07至2023-12发现期，
2024-01至2026-07只用于冻结验证。

## 冻结策略

```json
{json.dumps(frozen.__dict__, ensure_ascii=False, indent=2)}
```

- 初始资金：{args.initial_cash:,.0f}元
- 仅A股主板普通股票和现金
- 不使用ETF、指数权重、股指期货、期权或其他衍生品
- 下一交易日真实开盘价成交
- 100股整数手
- 买入成本3bp，卖出成本13bp，最低5元
- 基准滑点10bp/边

## 核心指标

```json
{json.dumps(frozen_row, ensure_ascii=False, indent=2)}
```

## 年度结果

{annual_table}

## 滑点敏感性

{slippage_table}

## 截面诊断

{diagnostic_table}

## 行业数据口径

行业分层使用Tushare申万一级行业历史成员，按 `in_date/out_date` 在信号日恢复，
没有用当前行业标签回填历史。行业中性组合限制单一行业最多占持仓数量的20%。

## 稳定性选择方法

候选因子族、8/10/12只持仓、月/双月调仓、风险分层/满仓、
全市场/剔除最小20%/行业中性截面组成参数邻域。
选择分数只使用发现期Sharpe、信息比率、最差年度超额和换手率；
先比较参数组的中位数与最差值，再冻结中心参数10只持仓，避免选取孤立最优点。

## 风险提示

2021和2026分别是半年度区间，不是完整自然年。任何未通过年度超额硬约束的版本，
不得表述为“每年跑赢沪深300”。即使通过，五年样本仍不足以证明未来必然跑赢。
"""
    atomic_write_text(args.output_dir / "STABILITY_REPORT.md", content)


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cfg = make_cfg(args)
    prices = load_prices(cfg.prices_file, cfg)
    benchmark = fetch_benchmark(args.warmup_start_date, args.end_date)
    panels, market = build_factor_panels(prices, args.start_date, args.basic_cache)
    history = load_industry_history(args.industry_file)
    industries = {dt: industry_snapshot(history, dt) for dt in panels}

    rows: list[dict] = []
    for number, case in enumerate(search_cases(), start=1):
        row, _ = evaluate_case(
            case,
            prices,
            panels,
            market,
            industries,
            benchmark,
            args,
            slippage=0.001,
        )
        rows.append(row)
        print(
            f"search {number}/{len(search_cases())} {case.name} score={row['discovery_score']:.4f}",
            flush=True,
        )
    summary = pd.DataFrame(rows)
    atomic_write_csv(
        summary.sort_values("discovery_score", ascending=False),
        args.output_dir / "parameter_search.csv",
    )

    diagnostic_rows: list[dict] = []
    for case in diagnostic_cases():
        row, _ = evaluate_case(
            case,
            prices,
            panels,
            market,
            industries,
            benchmark,
            args,
            slippage=0.001,
        )
        diagnostic_rows.append(row)
    diagnostics = pd.DataFrame(diagnostic_rows)
    atomic_write_csv(diagnostics, args.output_dir / "cross_section_diagnostics.csv")

    frozen = choose_frozen_case(summary)
    slippage_rows: list[dict] = []
    frozen_annual = pd.DataFrame()
    frozen_row: dict = {}
    for slippage in [0.0005, 0.001, 0.002, 0.005]:
        row, annual = evaluate_case(
            frozen,
            prices,
            panels,
            market,
            industries,
            benchmark,
            args,
            slippage=slippage,
            save=True,
        )
        slippage_rows.append(row)
        if slippage == 0.001:
            frozen_row = row
            frozen_annual = annual
    slippage_frame = pd.DataFrame(slippage_rows)
    atomic_write_csv(slippage_frame, args.output_dir / "slippage_sensitivity.csv")
    atomic_write_csv(frozen_annual, args.output_dir / "final_annual_metrics.csv")
    atomic_write_text(
        args.output_dir / "frozen_strategy.json",
        json.dumps(frozen.__dict__, ensure_ascii=False, indent=2),
    )
    write_report(args, frozen, frozen_row, frozen_annual, slippage_frame, diagnostics)
    print(
        json.dumps({"frozen": frozen.__dict__, "metrics": frozen_row}, ensure_ascii=False, indent=2)
    )
    print(f"Output: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
