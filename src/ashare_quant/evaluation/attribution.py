"""Performance attribution and skill decomposition (dimensions D/E)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

PPY = 252


def ols_regression(y: pd.Series, x: pd.Series) -> dict:
    """Simple OLS regression: y = alpha + beta*x + epsilon. Pure numpy."""
    aligned = pd.DataFrame({"y": y, "x": x}).dropna()
    if len(aligned) < 10:
        return {"alpha": 0.0, "beta": 0.0, "r_squared": 0.0, "residuals": pd.Series(dtype=float)}
    x_vals = aligned["x"].values
    y_vals = aligned["y"].values
    x_mean = x_vals.mean()
    y_mean = y_vals.mean()
    ss_xx = ((x_vals - x_mean) ** 2).sum()
    if ss_xx == 0:
        return {"alpha": 0.0, "beta": 0.0, "r_squared": 0.0, "residuals": pd.Series(dtype=float)}
    beta = float(((x_vals - x_mean) * (y_vals - y_mean)).sum() / ss_xx)
    alpha = float(y_mean - beta * x_mean)
    residuals = aligned["y"] - alpha - beta * aligned["x"]
    ss_yy = ((y_vals - y_mean) ** 2).sum()
    ss_res = (residuals ** 2).sum()
    r_squared = float(1.0 - ss_res / ss_yy) if ss_yy > 0 else 0.0
    return {
        "alpha": alpha,
        "beta": beta,
        "r_squared": r_squared,
        "alpha_annual": alpha * PPY,
        "alpha_tstat": float(alpha / (residuals.std(ddof=1) / math.sqrt(len(residuals)))) if len(residuals) > 1 and residuals.std(ddof=1) > 0 else 0.0,
        "residuals": residuals,
        "idio_vol_annual": float(residuals.std(ddof=1) * math.sqrt(PPY)),
    }


def capture_ratios(returns: pd.Series, benchmark_returns: pd.Series) -> dict:
    """Up/Down capture ratios: how much of benchmark's up/down moves the strategy captures."""
    aligned = pd.DataFrame({"s": returns, "b": benchmark_returns}).dropna()
    if len(aligned) < 10:
        return {"up_capture": 0.0, "down_capture": 0.0, "up_down_ratio": 0.0}

    up_days = aligned[aligned["b"] > 0]
    down_days = aligned[aligned["b"] < 0]

    up_capture = float(up_days["s"].mean() / up_days["b"].mean()) if len(up_days) > 0 and up_days["b"].mean() != 0 else 0.0
    down_capture = float(down_days["s"].mean() / down_days["b"].mean()) if len(down_days) > 0 and down_days["b"].mean() != 0 else 0.0

    ratio = float(up_capture / abs(down_capture)) if down_capture != 0 else float("inf")
    return {
        "up_capture": up_capture,
        "down_capture": down_capture,
        "up_down_ratio": ratio,
        "n_up_days": int(len(up_days)),
        "n_down_days": int(len(down_days)),
    }


def yearly_decomposition(raw_perf: pd.DataFrame, benchmark: pd.DataFrame) -> pd.DataFrame:
    """Year-by-year performance breakdown with enhanced metrics."""
    from .metrics import sharpe_ratio, sortino_ratio, max_drawdown

    raw_perf = raw_perf.copy()
    raw_perf["date"] = pd.to_datetime(raw_perf["date"])
    frame = raw_perf.set_index("date")
    returns = frame["returns"]
    equity = frame["portfolio_value"]

    bm = benchmark.copy()
    bm["date"] = pd.to_datetime(bm["date"])
    bm = bm.set_index("date")["benchmark_close"]
    bm_returns = bm.pct_change()

    rows = []
    for year in sorted(returns.index.year.unique()):
        year_mask = returns.index.year == year
        yr_returns = returns[year_mask]
        yr_equity = equity[year_mask]
        yr_bm = bm_returns.reindex(yr_returns.index)

        if len(yr_returns) < 5:
            continue

        yr_excess = yr_returns - yr_bm

        # Turnover
        buy_val = frame.loc[yr_returns.index, "today_sum_buy_value"].sum() if "today_sum_buy_value" in frame.columns else 0
        sell_val = frame.loc[yr_returns.index, "today_sum_sell_value"].sum() if "today_sum_sell_value" in frame.columns else 0
        turnover = float(max(buy_val, sell_val) / equity.iloc[0]) if equity.iloc[0] > 0 else 0

        rows.append({
            "year": int(year),
            "return": float((1 + yr_returns).prod() - 1),
            "benchmark_return": float((1 + yr_bm).prod() - 1) if len(yr_bm.dropna()) > 0 else 0.0,
            "excess": float((1 + yr_returns).prod() - (1 + yr_bm.fillna(0)).prod()),
            "sharpe": sharpe_ratio(yr_returns),
            "sortino": sortino_ratio(yr_returns),
            "max_drawdown": max_drawdown(yr_equity)[0],
            "win_day_rate": float((yr_returns > 0).mean()),
            "volatility": float(yr_returns.std(ddof=1) * math.sqrt(PPY)),
            "turnover": turnover,
            "n_days": int(len(yr_returns)),
        })

    return pd.DataFrame(rows)


def monthly_returns_matrix(raw_perf: pd.DataFrame) -> pd.DataFrame:
    """Year × Month returns matrix for heatmap."""
    raw_perf = raw_perf.copy()
    raw_perf["date"] = pd.to_datetime(raw_perf["date"])
    returns = raw_perf.set_index("date")["returns"]

    # Monthly returns (compound within month)
    monthly = (1 + returns).groupby([returns.index.year, returns.index.month]).apply(
        lambda x: x.prod() - 1, include_groups=False
    )
    monthly.index.names = ["year", "month"]
    matrix = monthly.unstack(level="month")
    matrix.columns = [f"{m}月" for m in matrix.columns]
    # Add annual total
    matrix["全年"] = (1 + returns).groupby(returns.index.year).apply(
        lambda x: x.prod() - 1, include_groups=False
    )
    return matrix


def full_analysis(raw_perf: pd.DataFrame, benchmark: pd.DataFrame) -> dict:
    """Run all attribution modules."""
    raw_perf = raw_perf.copy()
    raw_perf["date"] = pd.to_datetime(raw_perf["date"])
    frame = raw_perf.set_index("date")
    returns = frame["returns"]

    bm = benchmark.copy()
    bm["date"] = pd.to_datetime(bm["date"])
    bm = bm.set_index("date")["benchmark_close"]
    bm_returns = bm.pct_change()

    # Factor regression (CAPM: strategy vs benchmark)
    regression = ols_regression(returns, bm_returns)
    # Remove non-serializable residuals
    reg_summary = {k: v for k, v in regression.items() if k != "residuals"}

    return {
        "capm_regression": reg_summary,
        "capture_ratios": capture_ratios(returns, bm_returns),
        "yearly": yearly_decomposition(raw_perf, benchmark).to_dict(orient="records"),
        "monthly_matrix": monthly_returns_matrix(raw_perf).to_dict(),
    }
