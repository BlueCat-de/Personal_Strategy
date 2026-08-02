"""Drawdown, stress test, and regime analysis (dimension I of the evaluation framework)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

PPY = 252


def _drawdown_series(equity: pd.Series) -> pd.Series:
    peak = equity.cummax()
    return equity / peak - 1.0


def drawdown_analysis(equity: pd.Series) -> dict:
    """Comprehensive drawdown analysis: depth, duration, recovery, top-5 events list."""
    dd = _drawdown_series(equity)
    if dd.empty:
        return {}

    # Identify drawdown episodes (periods where dd < 0)
    underwater = dd < -1e-10
    episode_starts = []
    episode_ends = []
    in_episode = False
    for i in range(len(underwater)):
        if underwater.iloc[i] and not in_episode:
            episode_starts.append(i)
            in_episode = True
        elif not underwater.iloc[i] and in_episode:
            episode_ends.append(i - 1)
            in_episode = False
    if in_episode:
        episode_ends.append(len(underwater) - 1)

    episodes = []
    for s, e in zip(episode_starts, episode_ends):
        trough_local = dd.iloc[s:e + 1].idxmin()
        peak_val = equity.iloc[s - 1] if s > 0 else equity.iloc[0]
        trough_val = equity.loc[trough_local]
        recovery = e - dd.index.get_loc(trough_local)  # days from trough to recovery
        episodes.append({
            "start": str(dd.index[s].date()) if hasattr(dd.index[s], 'date') else str(dd.index[s]),
            "trough": str(trough_local.date()) if hasattr(trough_local, 'date') else str(trough_local),
            "end": str(dd.index[e].date()) if hasattr(dd.index[e], 'date') else str(dd.index[e]),
            "depth": float(dd.loc[trough_local]),
            "duration_days": int(e - s + 1),
            "recovery_days": int(recovery) if recovery > 0 else 0,
        })

    episodes.sort(key=lambda x: x["depth"])
    top5 = episodes[:5]

    durations = [ep["duration_days"] for ep in episodes] if episodes else [0]
    recoveries = [ep["recovery_days"] for ep in episodes if ep["recovery_days"] > 0] or [0]

    return {
        "max_drawdown": float(dd.min()),
        "max_drawdown_date": str(dd.idxmin()),
        "n_episodes": len(episodes),
        "avg_duration_days": float(np.mean(durations)) if durations else 0,
        "max_duration_days": int(max(durations)) if durations else 0,
        "avg_recovery_days": float(np.mean(recoveries)) if recoveries else 0,
        "underwater_ratio": float(underwater.mean()),  # fraction of days in drawdown
        "top_5_drawdowns": top5,
    }


def stress_test(raw_perf: pd.DataFrame, benchmark: pd.DataFrame) -> dict:
    """Historical scenario replay: strategy performance during known crises."""
    from .metrics import sharpe_ratio, annualized_return

    scenarios = {
        "2008 金融危机": ("2007-10-01", "2008-11-30"),
        "2015 股灾": ("2015-06-01", "2015-09-30"),
        "2018 中美贸易战": ("2018-01-01", "2018-12-31"),
        "2020 新冠": ("2020-01-01", "2020-03-31"),
        "2022 熊市": ("2022-01-01", "2022-10-31"),
    }

    raw_perf = raw_perf.copy()
    raw_perf["date"] = pd.to_datetime(raw_perf["date"])
    returns = raw_perf.set_index("date")["returns"]

    results = {}
    for name, (start, end) in scenarios.items():
        seg = returns.loc[start:end]
        if len(seg) < 5:
            continue
        # Max drawdown within the period
        equity_seg = (1 + seg).cumprod()
        peak = equity_seg.cummax()
        mdd = float((equity_seg / peak - 1).min())
        results[name] = {
            "return": float((1 + seg).prod() - 1),
            "sharpe": sharpe_ratio(seg),
            "max_drawdown": mdd,
            "n_days": int(len(seg)),
        }
    return results


def worst_n_periods(returns: pd.Series, n: int = 10, windows: list[int] | None = None) -> dict:
    """Identify the worst rolling-window return periods."""
    if windows is None:
        windows = [5, 20, 60]  # 1 week, 1 month, 3 months

    clean = returns.dropna()
    results = {}
    for w in windows:
        rolling_ret = (1 + clean).rolling(w).apply(np.prod, raw=True) - 1
        worst = rolling_ret.nsmallest(n)
        label = {5: "1周", 20: "1月", 60: "3月"}.get(w, f"{w}天")
        results[f"worst_{label}"] = [
            {"date": str(idx.date()) if hasattr(idx, 'date') else str(idx), "return": float(val)}
            for idx, val in worst.items()
        ]
    return results


def regime_analysis(raw_perf: pd.DataFrame, benchmark: pd.DataFrame) -> dict:
    """Classify trading days into regimes (bull/bear/volatile/calm) and compute per-regime metrics."""
    from .metrics import sharpe_ratio

    raw_perf = raw_perf.copy()
    raw_perf["date"] = pd.to_datetime(raw_perf["date"])
    frame = raw_perf.set_index("date")

    bm = benchmark.copy()
    bm["date"] = pd.to_datetime(bm["date"])
    bm = bm.set_index("date")
    bm_close = bm["benchmark_close"]

    # Regime classification: trend (HS300 vs MA200) × volatility (20d rolling std)
    ma200 = bm_close.rolling(200, min_periods=60).mean()
    trend_up = bm_close > ma200
    bm_ret = bm_close.pct_change()
    vol_20d = bm_ret.rolling(20, min_periods=10).std()
    vol_median = vol_20d.rolling(252, min_periods=60).median()
    high_vol = vol_20d > vol_median

    # 4 regimes: bull (up + calm), bear (down + calm), volatile_bull (up + high vol), volatile_bear (down + high vol)
    returns = frame["returns"].reindex(bm_close.index)

    regimes = {
        "牛市(上行+低波)": trend_up & ~high_vol,
        "熊市(下行+低波)": ~trend_up & ~high_vol,
        "动荡牛(上行+高波)": trend_up & high_vol,
        "动荡熊(下行+高波)": ~trend_up & high_vol,
    }

    results = {}
    for name, mask in regimes.items():
        regime_returns = returns[mask].dropna()
        if len(regime_returns) < 10:
            results[name] = {"n_days": int(mask.sum()), "return": 0, "sharpe": 0}
            continue
        equity = (1 + regime_returns).cumprod()
        peak = equity.cummax()
        mdd = float((equity / peak - 1).min())
        results[name] = {
            "n_days": int(mask.sum()),
            "day_fraction": float(mask.sum() / len(mask)) if len(mask) > 0 else 0,
            "avg_daily_return": float(regime_returns.mean()),
            "sharpe": sharpe_ratio(regime_returns),
            "max_drawdown": mdd,
        }
    return results


def rolling_sharpe(raw_perf: pd.DataFrame, window: int = 252) -> pd.Series:
    """Rolling 1-year Sharpe ratio time series."""
    raw_perf = raw_perf.copy()
    raw_perf["date"] = pd.to_datetime(raw_perf["date"])
    returns = raw_perf.set_index("date")["returns"]
    rolling_mean = returns.rolling(window).mean()
    rolling_std = returns.rolling(window).std(ddof=1)
    sharpe = (rolling_mean / rolling_std.replace(0, np.nan)) * math.sqrt(PPY)
    return sharpe.dropna()


def sharpe_stability_ratio(raw_perf: pd.DataFrame, window_years: int = 2) -> float:
    """Sharpe Stability Ratio = mean(rolling Sharpe) / std(rolling Sharpe).

    High ratio = consistent across sub-periods; low = unstable.
    """
    rs = rolling_sharpe(raw_perf, window_years * PPY)
    if len(rs) < 5:
        return 0.0
    mu = rs.mean()
    sigma = rs.std(ddof=1)
    if sigma == 0:
        return float("inf") if mu > 0 else 0.0
    return float(mu / sigma)


def full_analysis(raw_perf: pd.DataFrame, benchmark: pd.DataFrame) -> dict:
    """Run all risk analysis modules."""
    raw_perf = raw_perf.copy()
    raw_perf["date"] = pd.to_datetime(raw_perf["date"])
    equity = raw_perf.set_index("date")["portfolio_value"]
    returns = raw_perf.set_index("date")["returns"]

    return {
        "drawdown": drawdown_analysis(equity),
        "stress_test": stress_test(raw_perf, benchmark),
        "worst_periods": worst_n_periods(returns),
        "regime": regime_analysis(raw_perf, benchmark),
        "rolling_sharpe_current": float(rolling_sharpe(raw_perf).iloc[-1]) if len(rolling_sharpe(raw_perf)) > 0 else 0.0,
        "sharpe_stability_ratio": sharpe_stability_ratio(raw_perf),
    }
