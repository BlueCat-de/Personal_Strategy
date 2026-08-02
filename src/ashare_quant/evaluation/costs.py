"""Cost, turnover, and capacity analysis (dimension F of the evaluation framework)."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

PPY = 252


def turnover_analysis(raw_perf: pd.DataFrame) -> dict:
    """Single-side annualized turnover, average holding period, rebalance frequency.

    Turnover is computed relative to AVERAGE AUM (not initial capital) to avoid
    inflation from compounding growth over long backtests.
    """
    raw_perf = raw_perf.copy()
    raw_perf["date"] = pd.to_datetime(raw_perf["date"])
    frame = raw_perf.set_index("date")

    buy_val = frame["today_sum_buy_value"].sum() if "today_sum_buy_value" in frame.columns else 0
    sell_val = frame["today_sum_sell_value"].sum() if "today_sum_sell_value" in frame.columns else 0
    initial = frame["portfolio_value"].iloc[0]
    avg_aum = frame["portfolio_value"].mean()  # average AUM (correct denominator)
    years = len(frame) / PPY

    single_side_turnover = float(max(buy_val, sell_val) / avg_aum / years) if avg_aum > 0 and years > 0 else 0
    two_side_turnover = float((buy_val + sell_val) / avg_aum / years) if avg_aum > 0 and years > 0 else 0

    # Also report relative to initial capital (legacy, for comparison)
    single_side_vs_initial = float(max(buy_val, sell_val) / initial / years) if initial > 0 and years > 0 else 0

    # Rebalance frequency (days with nonzero buy or sell)
    trade_days = ((frame["today_sum_buy_value"] > 0) | (frame["today_sum_sell_value"] > 0)).sum() if "today_sum_buy_value" in frame.columns else 0
    avg_holding_days = float(len(frame) / trade_days) if trade_days > 0 else 0

    return {
        "single_side_annual_turnover": single_side_turnover,
        "two_side_annual_turnover": two_side_turnover,
        "single_side_vs_initial": single_side_vs_initial,
        "avg_aum": float(avg_aum),
        "initial_capital": float(initial),
        "avg_holding_days": avg_holding_days,
        "avg_holding_months": avg_holding_days / 21 if avg_holding_days > 0 else 0,
        "n_trade_days": int(trade_days),
        "trade_frequency_per_year": float(trade_days / years) if years > 0 else 0,
    }


def cost_drag(raw_perf: pd.DataFrame) -> dict:
    """Cost drag analysis: commission, slippage, and total cost as % of returns.

    All drags are relative to AVERAGE AUM (not initial capital).
    """
    raw_perf = raw_perf.copy()
    raw_perf["date"] = pd.to_datetime(raw_perf["date"])
    frame = raw_perf.set_index("date")

    initial = frame["portfolio_value"].iloc[0]
    avg_aum = frame["portfolio_value"].mean()
    years = len(frame) / PPY

    total_commission = frame["commission"].sum() if "commission" in frame.columns else 0
    total_slippage = frame["slippage_cost"].sum() if "slippage_cost" in frame.columns else 0
    total_cost = total_commission + total_slippage

    # Strategy total return
    final = frame["portfolio_value"].iloc[-1]

    # Drag relative to average AUM (correct denominator)
    commission_drag_annual = float(total_commission / avg_aum / years) if years > 0 else 0
    slippage_drag_annual = float(total_slippage / avg_aum / years) if years > 0 else 0
    total_drag_annual = commission_drag_annual + slippage_drag_annual

    # Cost as fraction of gross profit
    cost_to_return_ratio = float(total_cost / abs(final - initial)) if abs(final - initial) > 0 else float("inf")

    return {
        "total_commission": float(total_commission),
        "total_slippage": float(total_slippage),
        "total_cost": float(total_cost),
        "commission_drag_annual_pct": commission_drag_annual * 100,
        "slippage_drag_annual_pct": slippage_drag_annual * 100,
        "total_drag_annual_pct": total_drag_annual * 100,
        "cost_to_gross_return_ratio": cost_to_return_ratio,
    }


def holding_period_distribution(raw_perf: pd.DataFrame) -> dict:
    """Estimate holding period distribution from position changes in raw_perf."""
    raw_perf = raw_perf.copy()
    raw_perf["date"] = pd.to_datetime(raw_perf["date"])

    # Parse positions column (list of dicts per day)
    holding_periods = []
    # Track when each symbol first appears
    symbol_first_seen = {}

    for _, row in raw_perf.iterrows():
        positions = row.get("positions", [])
        if isinstance(positions, str):
            try:
                positions = json.loads(positions)
            except (json.JSONDecodeError, TypeError):
                positions = []
        if not isinstance(positions, list):
            continue

        current_symbols = set()
        for pos in positions:
            if isinstance(pos, dict):
                sym = pos.get("symbol", pos.get("instrument", ""))
                if sym:
                    current_symbols.add(sym)

        date = row["date"]
        # Symbols that disappeared (were sold)
        disappeared = set(symbol_first_seen.keys()) - current_symbols
        for sym in disappeared:
            first_date = symbol_first_seen.pop(sym)
            holding_periods.append((date - first_date).days)

        # New symbols
        for sym in current_symbols:
            if sym not in symbol_first_seen:
                symbol_first_seen[sym] = date

    if not holding_periods:
        return {"n_records": 0, "avg_holding_days": 0, "median_holding_days": 0}

    arr = np.array(holding_periods)
    return {
        "n_records": int(len(arr)),
        "avg_holding_days": float(arr.mean()),
        "median_holding_days": float(np.median(arr)),
        "min_holding_days": int(arr.min()),
        "max_holding_days": int(arr.max()),
        "p25_holding_days": float(np.percentile(arr, 25)),
        "p75_holding_days": float(np.percentile(arr, 75)),
    }


def capacity_estimate(raw_perf: pd.DataFrame, initial_cash: float = 100_000.0, max_participation: float = 0.01) -> dict:
    """Rough capacity estimate based on average position size and participation rate.

    For a long-only equal-weight strategy, capacity ≈ (smallest stock daily amount × participation) × positions / exposure.
    This is a simplified estimate — real capacity needs order-book depth data.
    """
    raw_perf = raw_perf.copy()
    raw_perf["date"] = pd.to_datetime(raw_perf["date"])
    frame = raw_perf.set_index("date")

    # Average buy value per trade day
    trade_days = frame[(frame["today_sum_buy_value"] > 0)].index if "today_sum_buy_value" in frame.columns else []
    if len(trade_days) == 0:
        return {"capacity_estimate": 0, "note": "no trade data"}

    avg_daily_buy = frame.loc[trade_days, "today_sum_buy_value"].mean()
    # If we assume 8 stocks equal weight, each stock gets initial_cash * 0.95 / 8
    # Participation rate 1% means we need: stock_daily_amount >= position_value / max_participation
    # Capacity = min_stock_amount * max_participation * 8 / 0.95
    # Rough: assume our positions trade ~30M/day, capacity ≈ 30M * 0.01 * 8 / 0.95 ≈ 2.5M per rebalance round
    estimated_stock_daily_amount = 50_000_000  # 5000万, conservative for main-board mid-caps
    capacity = estimated_stock_daily_amount * max_participation * 8 / 0.95

    return {
        "capacity_estimate_cny": float(capacity),
        "capacity_estimate_wan": float(capacity / 1e4),
        "method": "simplified: stock_daily_amount(5000万) × participation(1%) × 8 positions / 0.95 exposure",
        "note": "粗略估算。实际容量需要逐股的盘口深度数据。对 10万 账户完全不构成约束。",
    }


def full_analysis(raw_perf: pd.DataFrame) -> dict:
    """Run all cost/turnover modules."""
    return {
        "turnover": turnover_analysis(raw_perf),
        "cost_drag": cost_drag(raw_perf),
        "holding_period": holding_period_distribution(raw_perf),
        "capacity": capacity_estimate(raw_perf),
    }
