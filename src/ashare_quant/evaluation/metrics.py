"""Core risk-adjusted performance metrics (dimension I of the evaluation framework).

All functions take daily-frequency pd.Series as input and return float or dict.
No scipy dependency — statistical functions use math/numpy only.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

PPY = 252  # periods per year (A-share trading days)


# ── Basic annualized metrics ──────────────────────────────────────────────

def annualized_return(returns: pd.Series, ppy: int = PPY) -> float:
    """Geometric annualized return from daily returns."""
    clean = returns.dropna()
    if len(clean) == 0:
        return 0.0
    growth = (1.0 + clean).prod()
    years = len(clean) / ppy
    if growth <= 0 or years <= 0:
        return float(growth - 1.0)  # total loss
    return float(growth ** (1.0 / years) - 1.0)


def annualized_volatility(returns: pd.Series, ppy: int = PPY) -> float:
    """Annualized standard deviation of daily returns."""
    clean = returns.dropna()
    if len(clean) < 2:
        return 0.0
    return float(clean.std(ddof=1) * math.sqrt(ppy))


def sharpe_ratio(returns: pd.Series, rf: float = 0.0, ppy: int = PPY) -> float:
    """Annualized Sharpe ratio (risk-free rate as daily, annualized internally)."""
    clean = returns.dropna()
    if len(clean) < 2:
        return 0.0
    std = clean.std(ddof=1)
    if std == 0:
        return 0.0
    return float((clean.mean() - rf / ppy) / std * math.sqrt(ppy))


def information_ratio(excess_returns: pd.Series, ppy: int = PPY) -> float:
    """Annualized information ratio from daily excess returns."""
    clean = excess_returns.dropna()
    if len(clean) < 2:
        return 0.0
    std = clean.std(ddof=1)
    if std == 0:
        return 0.0
    return float(clean.mean() / std * math.sqrt(ppy))


# ── Downside-risk metrics ─────────────────────────────────────────────────

def downside_deviation(returns: pd.Series, target: float = 0.0, ppy: int = PPY) -> float:
    """Annualized downside deviation (only penalizes returns below target)."""
    clean = returns.dropna()
    if len(clean) == 0:
        return 0.0
    shortfall = (clean - target).clip(upper=0.0)  # only negative deviations
    dd = math.sqrt((shortfall ** 2).mean()) * math.sqrt(ppy)
    return float(dd)


def sortino_ratio(returns: pd.Series, target: float = 0.0, ppy: int = PPY) -> float:
    """Annualized Sortino ratio (downside-deviation-based)."""
    clean = returns.dropna()
    if len(clean) == 0:
        return 0.0
    dd = downside_deviation(clean, target, ppy)
    if dd == 0:
        return 0.0
    excess = (clean.mean() - target) * ppy
    return float(excess / dd)


def upside_potential_ratio(returns: pd.Series, target: float = 0.0) -> float:
    """Upside potential / downside deviation (non-annualized)."""
    clean = returns.dropna()
    if len(clean) == 0:
        return 0.0
    upside = (clean - target).clip(lower=0.0).mean()
    dd = downside_deviation(clean, target, ppy=1)  # raw (non-annualized)
    if dd == 0:
        return 0.0
    return float(upside / dd)


def omega_ratio(returns: pd.Series, threshold: float = 0.0) -> float:
    """Omega ratio: probability-weighted ratio of gains vs losses at threshold."""
    clean = returns.dropna()
    if len(clean) == 0:
        return 0.0
    gains = (clean[clean > threshold] - threshold).sum()
    losses = (threshold - clean[clean < threshold]).sum()
    if losses == 0:
        return float("inf") if gains > 0 else 1.0
    return float(gains / losses)


# ── Drawdown-based metrics ────────────────────────────────────────────────

def _drawdown_series(equity: pd.Series) -> pd.Series:
    """Drawdown as negative fraction from running peak."""
    peak = equity.cummax()
    return equity / peak - 1.0


def max_drawdown(equity: pd.Series) -> tuple[float, str, str, int]:
    """Returns (max_dd_value, peak_date, trough_date, duration_days)."""
    dd = _drawdown_series(equity)
    if dd.empty:
        return 0.0, "", "", 0
    trough_pos = dd.idxmin()
    max_dd = float(dd.loc[trough_pos])
    peak_pos = equity.loc[:trough_pos].idxmax()
    duration = len(equity.loc[peak_pos:trough_pos])
    return max_dd, str(peak_pos), str(trough_pos), duration


def calmar_ratio(returns: pd.Series, equity: pd.Series, ppy: int = PPY) -> float:
    """CAGR / |max drawdown|."""
    cagr = annualized_return(returns, ppy)
    mdd, _, _, _ = max_drawdown(equity)
    if mdd == 0:
        return float("inf") if cagr > 0 else 0.0
    return float(cagr / abs(mdd))


def ulcer_index(equity: pd.Series) -> float:
    """Ulcer Index = sqrt(mean(drawdown_percent^2)) — penalizes depth AND duration."""
    dd = _drawdown_series(equity)
    if dd.empty:
        return 0.0
    return float(math.sqrt((dd ** 2).mean()))


def pain_index(equity: pd.Series) -> float:
    """Pain Index = mean(|drawdown|) — average drawdown depth."""
    dd = _drawdown_series(equity)
    if dd.empty:
        return 0.0
    return float(dd.abs().mean())


def pain_ratio(returns: pd.Series, equity: pd.Series, ppy: int = PPY) -> float:
    """Pain Ratio = annualized return / pain index."""
    ar = annualized_return(returns, ppy)
    pi = pain_index(equity)
    if pi == 0:
        return float("inf") if ar > 0 else 0.0
    return float(ar / pi)


# ── Tail-risk metrics ─────────────────────────────────────────────────────

def var_historical(returns: pd.Series, confidence: float = 0.95) -> float:
    """Historical Value-at-Risk (returns the loss as a positive number)."""
    clean = returns.dropna()
    if len(clean) < 30:
        return 0.0
    return float(-clean.quantile(1.0 - confidence))


def cvar(returns: pd.Series, confidence: float = 0.95) -> float:
    """Conditional VaR / Expected Shortfall (positive number = expected loss)."""
    clean = returns.dropna()
    if len(clean) < 30:
        return 0.0
    var_threshold = clean.quantile(1.0 - confidence)
    tail = clean[clean <= var_threshold]
    if len(tail) == 0:
        return 0.0
    return float(-tail.mean())


def tail_ratio(returns: pd.Series) -> float:
    """Ratio of right-tail to left-tail (abs of 95th pct / abs of 5th pct)."""
    clean = returns.dropna()
    if len(clean) < 30:
        return 0.0
    p95 = clean.quantile(0.95)
    p5 = clean.quantile(0.05)
    if p5 == 0:
        return float("inf") if p95 > 0 else 0.0
    return float(abs(p95) / abs(p5))


def common_sense_ratio(returns: pd.Series, equity: pd.Series) -> float:
    """CSR = tail_ratio * calmar_ratio."""
    tr = tail_ratio(returns)
    cr = calmar_ratio(returns, equity)
    return float(tr * cr)


# ── Distribution moments ──────────────────────────────────────────────────

def skewness(returns: pd.Series) -> float:
    """Sample skewness (Fisher's definition, adjusted for bias)."""
    clean = returns.dropna()
    n = len(clean)
    if n < 3:
        return 0.0
    m = clean.mean()
    s = clean.std(ddof=1)
    if s == 0:
        return 0.0
    g1 = ((clean - m) ** 3).mean() / (s ** 3)
    # Bias correction (adjusted Fisher-Pearson)
    g1 *= math.sqrt(n * (n - 1)) / (n - 2)
    return float(g1)


def excess_kurtosis(returns: pd.Series) -> float:
    """Sample excess kurtosis (adjusted for bias)."""
    clean = returns.dropna()
    n = len(clean)
    if n < 4:
        return 0.0
    m = clean.mean()
    s = clean.std(ddof=1)
    if s == 0:
        return 0.0
    g2 = ((clean - m) ** 4).mean() / (s ** 4) - 3.0
    # Bias correction
    factor = ((n - 1) / ((n - 2) * (n - 3))) * ((n + 1) * g2 + 6)
    return float(factor)


def jarque_bera_statistic(returns: pd.Series) -> dict:
    """Jarque-Bera normality test (returns statistic and approximate p-value).

    No scipy: p-value approximated via chi-square CDF using the Wilson-Hilferty transformation.
    """
    clean = returns.dropna()
    n = len(clean)
    if n < 10:
        return {"jb_stat": 0.0, "p_value": 1.0, "is_normal": True}
    sk = skewness(clean)
    ek = excess_kurtosis(clean)
    jb = n / 6.0 * (sk ** 2 + ek ** 2 / 4.0)
    # Approximate chi-square(2) p-value via Wilson-Hilferty
    # For df=2, chi2 CDF = 1 - exp(-x/2)
    p_value = float(math.exp(-jb / 2.0))
    return {"jb_stat": float(jb), "p_value": p_value, "is_normal": p_value > 0.05}


# ── Aggregate ─────────────────────────────────────────────────────────────

def all_metrics(returns: pd.Series, equity: pd.Series, benchmark_returns: pd.Series | None = None) -> dict:
    """Compute all core metrics in one call.

    Args:
        returns: daily strategy returns (pd.Series indexed by date).
        equity: daily equity/NAV curve (pd.Series, same index).
        benchmark_returns: optional daily benchmark returns for relative metrics.

    Returns:
        Dict with ~30 metrics organized by category.
    """
    clean = returns.dropna()
    eq = equity.dropna()

    result = {
        # Basic
        "annualized_return": annualized_return(clean),
        "annualized_volatility": annualized_volatility(clean),
        "sharpe": sharpe_ratio(clean),
        # Downside
        "downside_deviation": downside_deviation(clean),
        "sortino": sortino_ratio(clean),
        "omega": omega_ratio(clean),
        "upside_potential_ratio": upside_potential_ratio(clean),
        # Drawdown
        "max_drawdown": max_drawdown(eq)[0],
        "calmar": calmar_ratio(clean, eq),
        "ulcer_index": ulcer_index(eq),
        "pain_index": pain_index(eq),
        "pain_ratio": pain_ratio(clean, eq),
        # Tail
        "var_95": var_historical(clean, 0.95),
        "cvar_95": cvar(clean, 0.95),
        "var_99": var_historical(clean, 0.99),
        "cvar_99": cvar(clean, 0.99),
        "tail_ratio": tail_ratio(clean),
        "common_sense_ratio": common_sense_ratio(clean, eq),
        # Distribution
        "skewness": skewness(clean),
        "excess_kurtosis": excess_kurtosis(clean),
        "jarque_bera": jarque_bera_statistic(clean),
        # Extremes
        "best_day": float(clean.max()),
        "worst_day": float(clean.min()),
        "win_day_rate": float((clean > 0).mean()),
        "n_days": int(len(clean)),
    }

    if benchmark_returns is not None:
        br = benchmark_returns.reindex(clean.index).dropna()
        cr = clean.reindex(br.index)
        excess = cr - br
        result["information_ratio"] = information_ratio(excess)
        result["beta"] = float(_beta(cr, br))
        result["alpha_annual"] = float(_alpha(cr, br))
        result["tracking_error"] = float(excess.std(ddof=1) * math.sqrt(PPY))
        result["excess_return_annual"] = annualized_return(excess)

    return result


def _beta(strategy_returns: pd.Series, benchmark_returns: pd.Series) -> float:
    """Beta of strategy vs benchmark."""
    aligned = pd.DataFrame({"s": strategy_returns, "b": benchmark_returns}).dropna()
    if len(aligned) < 10:
        return 0.0
    cov = aligned.cov().iloc[0, 1]
    var_b = aligned["b"].var(ddof=1)
    if var_b == 0:
        return 0.0
    return float(cov / var_b)


def _alpha(strategy_returns: pd.Series, benchmark_returns: pd.Series) -> float:
    """Annualized alpha (CAPM intercept × 252)."""
    aligned = pd.DataFrame({"s": strategy_returns, "b": benchmark_returns}).dropna()
    if len(aligned) < 10:
        return 0.0
    b = _beta(aligned["s"], aligned["b"])
    daily_alpha = aligned["s"].mean() - b * aligned["b"].mean()
    return float(daily_alpha * PPY)
