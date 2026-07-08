#!/usr/bin/env python3
"""Research price-level volatility asymmetry and a defensive momentum strategy.

This script uses the local BigQuant offline dataset only. It does not call the
BigQuant SDK and does not touch the production daemon.
"""

from __future__ import annotations

import argparse
import json
import math
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_PRICES_FILE = REPO_ROOT / "data/offline/a_share_12m_bigquant/prices_long.csv"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data/backtests/price_volatility_asymmetry/20260706"


@dataclass(frozen=True)
class ResearchConfig:
    prices_file: Path
    output_dir: Path
    warmup_start_date: str
    start_date: str
    end_date: str
    initial_cash: float
    max_positions: int
    max_position_weight: float
    strong_total_weight: float
    neutral_total_weight: float
    min_price: float
    max_price: float
    min_amount20: float
    min_candidates: int
    stop_loss: float
    trailing_stop: float
    trend_exit_window: int
    buy_cost: float
    sell_cost: float
    min_cost: float


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            tmp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.replace(path)
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
        tmp_path.replace(path)
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def pct_rank(values: pd.Series, ascending: bool = True) -> pd.Series:
    return values.rank(pct=True, ascending=ascending)


def spearman_corr(left: pd.Series, right: pd.Series) -> float:
    sample = pd.concat([left, right], axis=1).dropna()
    if len(sample) < 3:
        return float("nan")
    return float(sample.iloc[:, 0].rank().corr(sample.iloc[:, 1].rank()))


def max_drawdown(equity: pd.Series) -> tuple[float, str, str]:
    if equity.empty:
        return 0.0, "", ""
    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    trough_date = str(drawdown.idxmin())
    peak_date = str(equity.loc[:trough_date].idxmax())
    return float(drawdown.min()), peak_date, trough_date


def first_trading_day_each_week(index: pd.Index) -> list[pd.Timestamp]:
    dates = pd.to_datetime(index)
    grouped: dict[tuple[int, int], pd.Timestamp] = {}
    for dt in dates:
        iso = dt.isocalendar()
        grouped.setdefault((int(iso.year), int(iso.week)), dt)
    return sorted(grouped.values())


def load_bars(path: Path, start: str, end: str) -> pd.DataFrame:
    bars = pd.read_csv(path)
    bars["date"] = pd.to_datetime(bars["date"])
    bars["symbol"] = bars["symbol"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    bars = bars[(bars["date"] >= pd.to_datetime(start)) & (bars["date"] <= pd.to_datetime(end))].copy()
    bars = bars.rename(columns={"date": "trade_date", "turnover": "turnover_rate"})
    for col in ["open", "high", "low", "close", "volume", "amount", "turnover_rate"]:
        if col not in bars.columns:
            bars[col] = np.nan
        bars[col] = pd.to_numeric(bars[col], errors="coerce")
    return bars.dropna(subset=["trade_date", "symbol", "open", "high", "low", "close"])


def pivot_bars(bars: pd.DataFrame) -> dict[str, pd.DataFrame]:
    close = bars.pivot(index="trade_date", columns="symbol", values="close").sort_index()
    out = {"close": close}
    for col in ["open", "high", "low", "volume", "amount", "turnover_rate"]:
        out[col] = bars.pivot(index="trade_date", columns="symbol", values=col).reindex(index=close.index, columns=close.columns)
    out["open"] = out["open"].fillna(close)
    return out


def analyze_price_volatility(p: dict[str, pd.DataFrame], start_date: str) -> tuple[pd.DataFrame, dict]:
    close = p["close"]
    high = p["high"]
    low = p["low"]
    returns = close.pct_change(fill_method=None)
    abs_range = high - low
    abs_change = close.diff().abs()
    rel_range = abs_range / close
    formal_mask = close.index >= pd.to_datetime(start_date)

    rows: list[dict] = []
    corrs: list[dict] = []
    for dt in close.index[formal_mask]:
        price = close.loc[dt]
        sample = pd.DataFrame(
            {
                "price": price,
                "abs_range": abs_range.loc[dt],
                "abs_change": abs_change.loc[dt],
                "rel_range": rel_range.loc[dt],
                "abs_return": returns.loc[dt].abs(),
            }
        ).replace([np.inf, -np.inf], np.nan).dropna()
        sample = sample[(sample["price"] > 0) & (sample["abs_range"] > 0)]
        if len(sample) < 200:
            continue
        corrs.append(
            {
                "date": dt.strftime("%Y-%m-%d"),
                "pearson_price_abs_range": sample["price"].corr(sample["abs_range"], method="pearson"),
                "spearman_price_abs_range": spearman_corr(sample["price"], sample["abs_range"]),
                "pearson_price_abs_change": sample["price"].corr(sample["abs_change"], method="pearson"),
                "spearman_price_abs_change": spearman_corr(sample["price"], sample["abs_change"]),
                "spearman_price_rel_range": spearman_corr(sample["price"], sample["rel_range"]),
            }
        )
        decile = pd.qcut(sample["price"], 10, labels=False, duplicates="drop")
        grouped = sample.assign(price_decile=decile).groupby("price_decile", observed=True)
        for bucket, item in grouped:
            rows.append(
                {
                    "date": dt.strftime("%Y-%m-%d"),
                    "price_decile": int(bucket) + 1,
                    "median_price": item["price"].median(),
                    "mean_abs_range": item["abs_range"].mean(),
                    "mean_abs_change": item["abs_change"].mean(),
                    "mean_rel_range": item["rel_range"].mean(),
                    "mean_abs_return": item["abs_return"].mean(),
                    "count": len(item),
                }
            )
    corr_df = pd.DataFrame(corrs)
    decile_df = pd.DataFrame(rows)
    summary = {
        "trading_days": int(corr_df["date"].nunique()) if not corr_df.empty else 0,
        "median_pearson_price_abs_range": float(corr_df["pearson_price_abs_range"].median()),
        "median_spearman_price_abs_range": float(corr_df["spearman_price_abs_range"].median()),
        "median_pearson_price_abs_change": float(corr_df["pearson_price_abs_change"].median()),
        "median_spearman_price_abs_change": float(corr_df["spearman_price_abs_change"].median()),
        "median_spearman_price_rel_range": float(corr_df["spearman_price_rel_range"].median()),
    }
    return decile_df, {"daily_correlations": corr_df, "summary": summary}


def build_factor_table(p: dict[str, pd.DataFrame], config: ResearchConfig) -> pd.DataFrame:
    close = p["close"]
    high = p["high"]
    low = p["low"]
    amount = p["amount"].where(p["amount"].notna() & (p["amount"] > 0), p["open"] * p["volume"] * 100.0)
    returns = close.pct_change(fill_method=None)
    abs_range_pct = (high - low) / close
    mom20 = close / close.shift(20) - 1
    mom60 = close / close.shift(60) - 1
    mom120 = close / close.shift(120) - 1
    vol20 = returns.rolling(20).std()
    downside60 = returns.clip(upper=0).rolling(60).std()
    abs_vol20_yuan = (high - low).rolling(20).mean()
    rel_range20 = abs_range_pct.rolling(20).mean()
    downside_capture = downside60 / returns.rolling(60).std()
    drawdown60 = close / close.rolling(60).max() - 1
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ma120 = close.rolling(120).mean()
    amount20 = amount.rolling(20).mean()
    future5 = close.shift(-5) / close - 1
    future20 = close.shift(-20) / close - 1

    rows: list[pd.DataFrame] = []
    weekly_dates = first_trading_day_each_week(close.index)
    for dt in weekly_dates:
        if dt < pd.to_datetime(config.start_date):
            continue
        frame = pd.DataFrame(
            {
                "date": dt.strftime("%Y-%m-%d"),
                "symbol": close.columns,
                "close": close.loc[dt],
                "mom20": mom20.loc[dt],
                "mom60": mom60.loc[dt],
                "mom120": mom120.loc[dt],
                "vol20": vol20.loc[dt],
                "downside60": downside60.loc[dt],
                "abs_vol20_yuan": abs_vol20_yuan.loc[dt],
                "rel_range20": rel_range20.loc[dt],
                "downside_capture": downside_capture.loc[dt],
                "drawdown60": drawdown60.loc[dt],
                "ma20_gap": close.loc[dt] / ma20.loc[dt] - 1,
                "ma60_gap": close.loc[dt] / ma60.loc[dt] - 1,
                "ma120_gap": close.loc[dt] / ma120.loc[dt] - 1,
                "amount20": amount20.loc[dt],
                "future5": future5.loc[dt],
                "future20": future20.loc[dt],
            }
        ).replace([np.inf, -np.inf], np.nan)
        frame = frame.dropna(subset=["close", "mom20", "mom60", "vol20", "downside60", "rel_range20", "future5", "future20"])
        if frame.empty:
            continue
        recovery_penalty = (-frame["drawdown60"]).clip(lower=0) / (1 + frame["drawdown60"].clip(lower=-0.95))
        low_relative_vol = pct_rank(frame["rel_range20"], ascending=False)
        low_downside = pct_rank(frame["downside60"], ascending=False)
        low_recovery_penalty = pct_rank(recovery_penalty, ascending=False)
        trend_strength = 0.45 * pct_rank(frame["mom60"]) + 0.25 * pct_rank(frame["mom120"]) + 0.30 * pct_rank(frame["ma60_gap"])
        frame["recovery_penalty"] = recovery_penalty
        frame["price_vol_asymmetry_factor"] = (
            0.36 * trend_strength
            + 0.24 * low_relative_vol
            + 0.18 * low_downside
            + 0.14 * low_recovery_penalty
            + 0.08 * pct_rank(frame["amount20"])
        )
        rows.append(frame)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def factor_metrics(factors: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    rows: list[dict] = []
    for horizon in ["future5", "future20"]:
        for date, item in factors.groupby("date"):
            sample = item[["price_vol_asymmetry_factor", horizon]].dropna()
            if len(sample) < 100:
                continue
            rows.append(
                {
                    "date": date,
                    "horizon": horizon,
                    "spearman_ic": spearman_corr(sample["price_vol_asymmetry_factor"], sample[horizon]),
                    "pearson_ic": sample["price_vol_asymmetry_factor"].corr(sample[horizon], method="pearson"),
                    "top_decile_mean_return": sample.loc[sample["price_vol_asymmetry_factor"] >= sample["price_vol_asymmetry_factor"].quantile(0.9), horizon].mean(),
                    "bottom_decile_mean_return": sample.loc[sample["price_vol_asymmetry_factor"] <= sample["price_vol_asymmetry_factor"].quantile(0.1), horizon].mean(),
                    "top_decile_win_rate": (sample.loc[sample["price_vol_asymmetry_factor"] >= sample["price_vol_asymmetry_factor"].quantile(0.9), horizon] > 0).mean(),
                }
            )
    ic = pd.DataFrame(rows)
    summary: dict[str, dict] = {}
    for horizon, item in ic.groupby("horizon"):
        summary[horizon] = {
            "mean_spearman_ic": float(item["spearman_ic"].mean()),
            "median_spearman_ic": float(item["spearman_ic"].median()),
            "ic_positive_rate": float((item["spearman_ic"] > 0).mean()),
            "mean_top_bottom_spread": float((item["top_decile_mean_return"] - item["bottom_decile_mean_return"]).mean()),
            "mean_top_decile_win_rate": float(item["top_decile_win_rate"].mean()),
            "observations": int(len(item)),
        }
    return ic, summary


def feature_ic_metrics(factors: pd.DataFrame) -> pd.DataFrame:
    features = [
        "close",
        "mom20",
        "mom60",
        "mom120",
        "vol20",
        "downside60",
        "abs_vol20_yuan",
        "rel_range20",
        "downside_capture",
        "drawdown60",
        "ma20_gap",
        "ma60_gap",
        "ma120_gap",
        "amount20",
        "recovery_penalty",
        "price_vol_asymmetry_factor",
    ]
    rows: list[dict] = []
    for horizon in ["future5", "future20"]:
        for feature in features:
            ics: list[float] = []
            spreads: list[float] = []
            wins: list[float] = []
            for _, item in factors.groupby("date"):
                sample = item[[feature, horizon]].dropna()
                if len(sample) < 100:
                    continue
                ic = spearman_corr(sample[feature], sample[horizon])
                top = sample.loc[sample[feature] >= sample[feature].quantile(0.9), horizon]
                bottom = sample.loc[sample[feature] <= sample[feature].quantile(0.1), horizon]
                ics.append(ic)
                spreads.append(float(top.mean() - bottom.mean()))
                wins.append(float((top > 0).mean()))
            if not ics:
                continue
            rows.append(
                {
                    "horizon": horizon,
                    "feature": feature,
                    "mean_ic": float(np.nanmean(ics)),
                    "median_ic": float(np.nanmedian(ics)),
                    "ic_positive_rate": float(np.mean(np.array(ics) > 0)),
                    "mean_top_bottom_spread": float(np.nanmean(spreads)),
                    "mean_top_decile_win_rate": float(np.nanmean(wins)),
                    "observations": len(ics),
                }
            )
    return pd.DataFrame(rows)


def market_exposure(close: pd.DataFrame, loc: int, neutral: float, strong: float) -> tuple[float, str]:
    history = close.iloc[: loc + 1]
    last = history.iloc[-1]
    ma20 = history.rolling(20).mean().iloc[-1]
    ma60 = history.rolling(60).mean().iloc[-1]
    ma120 = history.rolling(120).mean().iloc[-1]
    returns = close.pct_change(fill_method=None)
    median_ret20 = (last / history.iloc[-21] - 1).median(skipna=True)
    median_ret60 = (last / history.iloc[-61] - 1).median(skipna=True)
    breadth20 = (last > ma20).mean(skipna=True)
    breadth60 = (last > ma60).mean(skipna=True)
    breadth120 = (last > ma120).mean(skipna=True)
    market_vol20 = returns.iloc[max(0, loc - 20) : loc + 1].median(axis=1).std()
    if breadth20 < 0.48 or breadth60 < 0.42 or breadth120 < 0.35 or median_ret20 < -0.025 or market_vol20 > 0.03:
        return 0.0, "bear"
    if breadth20 >= 0.62 and breadth60 >= 0.56 and median_ret20 > 0.01 and median_ret60 > -0.005 and market_vol20 < 0.022:
        return strong, "bull"
    return neutral, "range"


def allocate(selected: list[str], vol20: pd.Series, gross: float, cap: float) -> pd.Series:
    weights = pd.Series(0.0, index=vol20.index)
    if not selected or gross <= 0:
        return weights
    risk = vol20.reindex(selected).replace(0, np.nan)
    raw = (1 / risk).replace([np.inf, -np.inf], np.nan).fillna(0)
    if raw.sum() <= 0:
        raw = pd.Series(1.0, index=selected)
    alloc = (raw / raw.sum() * gross).clip(upper=cap)
    if alloc.sum() > gross:
        alloc = alloc / alloc.sum() * gross
    weights.loc[alloc.index] = alloc
    return weights


def build_signals(p: dict[str, pd.DataFrame], factors: pd.DataFrame, config: ResearchConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    close = p["close"]
    factor_by_date = {date: item.set_index("symbol") for date, item in factors.groupby("date")}
    weekly_dates = set(first_trading_day_each_week(close.index))
    current = pd.Series(0.0, index=close.columns)
    entry: dict[str, float] = {}
    peak: dict[str, float] = {}
    signal_rows: list[dict] = []
    debug_rows: list[dict] = []
    formal_start = pd.to_datetime(config.start_date)

    for loc, dt in enumerate(close.index):
        if dt < formal_start:
            continue
        dt_iso = pd.to_datetime(dt).strftime("%Y-%m-%d")
        last = close.iloc[loc]
        if dt in weekly_dates and loc >= 120:
            frame = factor_by_date.get(dt_iso)
            gross, regime = market_exposure(close, loc, config.neutral_total_weight, config.strong_total_weight)
            if frame is None or len(frame) < config.min_candidates:
                ranked = pd.DataFrame()
                gross = 0.0
            else:
                tradable = (
                    frame["close"].between(config.min_price, config.max_price)
                    & (frame["amount20"] >= config.min_amount20)
                    & (frame["mom20"] > -0.02)
                    & (frame["mom60"] > 0.00)
                    & (frame["mom120"] > -0.03)
                    & (frame["vol20"] < 0.055)
                    & (frame["rel_range20"] < 0.075)
                    & (frame["drawdown60"] > -0.18)
                    & (frame["recovery_penalty"] < 0.22)
                    & (frame["ma20_gap"] > -0.02)
                    & (frame["ma60_gap"] > -0.01)
                )
                ranked = frame.loc[tradable].sort_values("price_vol_asymmetry_factor", ascending=False)
                if len(ranked) < config.min_candidates:
                    gross = 0.0
            previous = current.copy()
            if gross > 0 and not ranked.empty:
                selected = ranked.head(config.max_positions).index.tolist()
                current = allocate(selected, frame["vol20"], gross, config.max_position_weight)
                entry = {s: float(last.loc[s]) for s in selected if current.get(s, 0.0) > 0 and pd.notna(last.loc[s])}
                peak = dict(entry)
            else:
                selected = []
                current = pd.Series(0.0, index=close.columns)
                entry = {}
                peak = {}
            if not current.equals(previous):
                for symbol in sorted(set(previous[previous > 0].index) | set(current[current > 0].index)):
                    signal_rows.append({"date": dt_iso, "symbol": symbol, "target_weight": float(current.get(symbol, 0.0)), "signal_close": float(last.get(symbol, np.nan))})
            debug_rows.append({"date": dt_iso, "regime": regime, "gross": gross, "candidate_count": 0 if ranked.empty else len(ranked), "selected": ",".join(selected)})

        if loc >= config.trend_exit_window:
            previous = current.copy()
            trend_ma = close.iloc[: loc + 1].rolling(config.trend_exit_window).mean().iloc[-1]
            for symbol in list(current[current > 0].index):
                price = float(last.loc[symbol]) if pd.notna(last.loc[symbol]) else np.nan
                if not math.isfinite(price):
                    current.loc[symbol] = 0.0
                    entry.pop(symbol, None)
                    peak.pop(symbol, None)
                    continue
                peak[symbol] = max(peak.get(symbol, price), price)
                hit_stop = price <= entry.get(symbol, price) * (1 - config.stop_loss)
                hit_trailing = price <= peak.get(symbol, price) * (1 - config.trailing_stop)
                broken_trend = price < float(trend_ma.loc[symbol]) if pd.notna(trend_ma.loc[symbol]) else False
                if hit_stop or hit_trailing or broken_trend:
                    current.loc[symbol] = 0.0
                    entry.pop(symbol, None)
                    peak.pop(symbol, None)
            if not current.equals(previous):
                for symbol in sorted(set(previous[previous > 0].index) | set(current[current > 0].index)):
                    signal_rows.append({"date": dt_iso, "symbol": symbol, "target_weight": float(current.get(symbol, 0.0)), "signal_close": float(last.get(symbol, np.nan))})
    return pd.DataFrame(signal_rows), pd.DataFrame(debug_rows)


def backtest(p: dict[str, pd.DataFrame], signals: pd.DataFrame, config: ResearchConfig) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    close = p["close"]
    open_ = p["open"].fillna(close)
    dates = list(close.index[close.index >= pd.to_datetime(config.start_date)])
    signal_map = {date: item.set_index("symbol")["target_weight"] for date, item in signals.groupby("date")} if not signals.empty else {}
    cash = config.initial_cash
    shares = pd.Series(0, index=close.columns, dtype=int)
    pending: pd.Series | None = None
    equity_rows: list[dict] = []
    trade_rows: list[dict] = []

    for dt in dates:
        dt_iso = pd.to_datetime(dt).strftime("%Y-%m-%d")
        prices_open = open_.loc[dt]
        if pending is not None:
            equity_before = cash + float((shares * prices_open.fillna(0)).sum())
            target_values = pending.reindex(close.columns).fillna(0.0) * equity_before
            # Sell first, then buy with available cash. This approximates T+1 with next-open execution.
            for symbol in target_values.index:
                price = float(prices_open.loc[symbol]) if pd.notna(prices_open.loc[symbol]) else np.nan
                if not math.isfinite(price) or price <= 0:
                    continue
                target_shares = int((target_values.loc[symbol] // (price * 100)) * 100)
                delta = target_shares - int(shares.loc[symbol])
                if delta >= 0:
                    continue
                fee = max(abs(delta) * price * config.sell_cost, config.min_cost)
                cash += abs(delta) * price - fee
                shares.loc[symbol] += delta
                trade_rows.append({"date": dt_iso, "symbol": symbol, "side": "sell", "shares": delta, "price": price, "fee": fee, "cash_after": cash})
            for symbol in target_values.index:
                price = float(prices_open.loc[symbol]) if pd.notna(prices_open.loc[symbol]) else np.nan
                if not math.isfinite(price) or price <= 0:
                    continue
                target_shares = int((target_values.loc[symbol] // (price * 100)) * 100)
                delta = target_shares - int(shares.loc[symbol])
                if delta <= 0:
                    continue
                fee = max(delta * price * config.buy_cost, config.min_cost)
                cost = delta * price + fee
                if cost > cash:
                    affordable = int(((cash - config.min_cost) // (price * 100)) * 100)
                    delta = max(0, min(delta, affordable))
                    fee = max(delta * price * config.buy_cost, config.min_cost) if delta > 0 else 0
                    cost = delta * price + fee
                if delta <= 0 or cost > cash:
                    continue
                cash -= cost
                shares.loc[symbol] += delta
                trade_rows.append({"date": dt_iso, "symbol": symbol, "side": "buy", "shares": delta, "price": price, "fee": fee, "cash_after": cash})
            pending = None

        prices_close = close.loc[dt].fillna(prices_open).fillna(0)
        equity = cash + float((shares * prices_close).sum())
        held = shares[shares > 0]
        equity_rows.append({"date": dt_iso, "equity": equity, "cash": cash, "positions": int(len(held)), "position_symbols": ",".join(held.index.tolist())})
        if dt_iso in signal_map:
            pending = signal_map[dt_iso]

    equity_df = pd.DataFrame(equity_rows)
    trades_df = pd.DataFrame(trade_rows)
    eq = equity_df.set_index("date")["equity"]
    daily = eq.pct_change().fillna(0.0)
    mdd, peak_date, trough_date = max_drawdown(eq)
    total_return = float(eq.iloc[-1] / config.initial_cash - 1) if not eq.empty else 0.0
    years = max(len(eq) / 252, 1 / 252)
    annual_return = (1 + total_return) ** (1 / years) - 1
    sharpe = float(daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0.0
    sell_trades = trades_df[trades_df["side"] == "sell"] if not trades_df.empty else pd.DataFrame()
    summary = {
        "initial_cash": config.initial_cash,
        "final_equity": float(eq.iloc[-1]) if not eq.empty else config.initial_cash,
        "total_return": total_return,
        "annual_return": float(annual_return),
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "max_drawdown_peak": peak_date,
        "max_drawdown_trough": trough_date,
        "trade_count": int(len(trades_df)),
        "buy_count": int((trades_df["side"] == "buy").sum()) if not trades_df.empty else 0,
        "sell_count": int((trades_df["side"] == "sell").sum()) if not trades_df.empty else 0,
        "win_day_rate": float((daily > 0).mean()),
        "avg_daily_return": float(daily.mean()),
        "daily_volatility": float(daily.std() * np.sqrt(252)),
        "total_fees": float(trades_df["fee"].sum()) if not trades_df.empty else 0.0,
        "sell_trade_count": int(len(sell_trades)),
    }
    return equity_df, trades_df, summary


def regime_performance(equity: pd.DataFrame, debug: pd.DataFrame) -> pd.DataFrame:
    if equity.empty or debug.empty:
        return pd.DataFrame()
    eq = equity.copy()
    eq["date"] = pd.to_datetime(eq["date"])
    dbg = debug[["date", "regime"]].copy()
    dbg["date"] = pd.to_datetime(dbg["date"])
    merged = pd.merge_asof(eq.sort_values("date"), dbg.sort_values("date"), on="date", direction="backward")
    merged["daily_return"] = merged["equity"].pct_change().fillna(0.0)
    rows = []
    for regime, item in merged.groupby("regime"):
        if not regime or item.empty:
            continue
        ret = item["equity"].iloc[-1] / item["equity"].iloc[0] - 1 if item["equity"].iloc[0] else 0.0
        mdd, _, _ = max_drawdown(item.set_index(item["date"].dt.strftime("%Y-%m-%d"))["equity"])
        rows.append({"regime": regime, "days": len(item), "return": ret, "max_drawdown": mdd, "win_day_rate": (item["daily_return"] > 0).mean()})
    return pd.DataFrame(rows)


def sensitivity_test(p: dict[str, pd.DataFrame], factors: pd.DataFrame, config: ResearchConfig) -> pd.DataFrame:
    rows = []
    for stop_loss in [0.05, 0.06, 0.08]:
        for max_weight in [0.28, 0.34, 0.40]:
            cfg = ResearchConfig(**{**asdict(config), "stop_loss": stop_loss, "max_position_weight": max_weight})
            signals, _ = build_signals(p, factors, cfg)
            _, _, summary = backtest(p, signals, cfg)
            rows.append({"stop_loss": stop_loss, "max_position_weight": max_weight, **summary})
    return pd.DataFrame(rows)


def make_report(
    config: ResearchConfig,
    vol_summary: dict,
    factor_summary: dict,
    strategy_summary: dict,
    regime: pd.DataFrame,
    sensitivity: pd.DataFrame,
    feature_ic: pd.DataFrame,
) -> str:
    best_sens = sensitivity.sort_values(["sharpe", "total_return"], ascending=False).head(5)
    top_features = (
        feature_ic[feature_ic["horizon"] == "future20"]
        .sort_values("mean_ic", ascending=False)
        .head(8)[["feature", "mean_ic", "median_ic", "ic_positive_rate", "mean_top_bottom_spread", "mean_top_decile_win_rate"]]
    )
    v4 = {
        "total_return": 0.2115,
        "annual_return": 0.2212,
        "sharpe": 1.55,
        "max_drawdown": -0.0690,
        "win_rate": 0.5455,
    }
    comparison = pd.DataFrame(
        [
            {
                "strategy": "v4_bigquant_baseline",
                "total_return": v4["total_return"],
                "annual_return": v4["annual_return"],
                "sharpe": v4["sharpe"],
                "max_drawdown": v4["max_drawdown"],
                "win_rate": v4["win_rate"],
            },
            {
                "strategy": "price_volatility_asymmetry",
                "total_return": strategy_summary["total_return"],
                "annual_return": strategy_summary["annual_return"],
                "sharpe": strategy_summary["sharpe"],
                "max_drawdown": strategy_summary["max_drawdown"],
                "win_rate": strategy_summary["win_day_rate"],
            },
        ]
    )
    lines = [
        "# 价格波动非对称策略研究报告",
        "",
        "## 结论摘要",
        "- `股票绝对值波动金额与当前股价正相关` 在样本内显著存在。",
        "- 但这个现象主要是价格尺度效应，并不天然等价于可交易 alpha。",
        "- 基于相对波动、下行波动和回撤修复压力构造的防御型因子，样本内 IC 接近 0，预测能力不足。",
        "- 独立策略回测显著弱于当前 v4，不建议替换线上生产策略。",
        "- 该研究更适合作为 v4 的风险过滤参考，而不是单独作为主策略。",
        "",
        "## 数据范围",
        f"- 数据文件：`{config.prices_file}`",
        f"- 预热区间：{config.warmup_start_date} ~ {config.start_date}",
        f"- 正式回测：{config.start_date} ~ {config.end_date}",
        "- 数据源：本地 BigQuant 离线日线数据。本次研究未调用 BigQuant SDK，不消耗 cell 配额。",
        "",
        "## 市场特性验证",
        "- 假设：股票的绝对值波动金额与当前股价存在正相关。",
        "- 注意：这个关系很大程度来自价格尺度效应，因为绝对波动金额 = 股价 × 相对波动率。",
        f"- 日度样本数：{vol_summary['trading_days']}",
        f"- 价格 vs 日内绝对波动金额 Pearson 中位数：{vol_summary['median_pearson_price_abs_range']:.4f}",
        f"- 价格 vs 日内绝对波动金额 Spearman 中位数：{vol_summary['median_spearman_price_abs_range']:.4f}",
        f"- 价格 vs 收盘绝对变动金额 Spearman 中位数：{vol_summary['median_spearman_price_abs_change']:.4f}",
        f"- 价格 vs 相对日内波动 Spearman 中位数：{vol_summary['median_spearman_price_rel_range']:.4f}",
        "",
        "结论：绝对金额波动与股价正相关显著存在，但直接用绝对波动做 alpha 容易混入价格尺度。更合理的做法是使用相对波动、下行波动和回撤修复难度来控制风险。",
        "",
        "## 因子验证",
    ]
    for horizon, item in factor_summary.items():
        lines.extend(
            [
                f"- `{horizon}` mean IC：{item['mean_spearman_ic']:.4f}",
                f"- `{horizon}` median IC：{item['median_spearman_ic']:.4f}",
                f"- `{horizon}` IC 为正比例：{item['ic_positive_rate']:.2%}",
                f"- `{horizon}` Top-Bottom 平均收益差：{item['mean_top_bottom_spread']:.2%}",
                f"- `{horizon}` Top decile 胜率：{item['mean_top_decile_win_rate']:.2%}",
            ]
        )
    lines.extend(
        [
            "",
            "### 单因子挖掘 Top 8（20 日未来收益）",
            top_features.to_markdown(index=False),
            "",
            "## 策略逻辑",
            "- 周频调仓，信号日收盘后生成，下一交易日开盘执行。",
            "- 最多持仓 2 只，单票权重上限 34%，根据 20 日波动率做风险反比分配。",
            "- 选股核心：中期趋势向上、相对波动低、下行波动低、60 日回撤修复压力小、成交额充足。",
            "- 市场状态过滤：全市场宽度较弱或波动过高时空仓；强市场 68% 总仓位，中性市场 34% 总仓位。",
            "- 风控：趋势跌破、固定止损和跟踪止损三类退出。",
            "",
            "## 回测绩效",
            f"- 初始资金：{strategy_summary['initial_cash']:.2f}",
            f"- 最终权益：{strategy_summary['final_equity']:.2f}",
            f"- 累计收益率：{strategy_summary['total_return']:.2%}",
            f"- 年化收益率：{strategy_summary['annual_return']:.2%}",
            f"- Sharpe：{strategy_summary['sharpe']:.2f}",
            f"- 最大回撤：{strategy_summary['max_drawdown']:.2%}",
            f"- 年化波动率：{strategy_summary['daily_volatility']:.2%}",
            f"- 日胜率：{strategy_summary['win_day_rate']:.2%}",
            f"- 交易次数：{strategy_summary['trade_count']}",
            f"- 总费用：{strategy_summary['total_fees']:.2f}",
            "",
            "## 与当前 v4 基准对比",
            comparison.to_markdown(index=False),
            "",
            "## 市场环境表现",
            regime.to_markdown(index=False) if not regime.empty else "样本内没有足够市场环境分组。",
            "",
            "## 参数敏感性 Top 5",
            best_sens[["stop_loss", "max_position_weight", "total_return", "annual_return", "sharpe", "max_drawdown", "trade_count"]].to_markdown(index=False),
            "",
            "## 实盘建议与风险",
            "- 当前研究版策略不建议上线替换 v4，因为收益、Sharpe 和回撤均不占优。",
            "- 样本当前只有本地 BigQuant 约 18 个月数据，不能证明长期稳定性；后续应在 2023 起更长样本上复验。",
            "- 绝对波动金额本身不是稳定 alpha，必须归一化为相对波动并结合趋势、成交额和市场状态。",
            "- 更实际的用法：把 `rel_range20`、`downside60`、`recovery_penalty` 作为 v4 候选股风险过滤条件，避免深回撤后回本难度过高的标的。",
            "- A 股实盘仍需考虑涨跌停无法成交、停牌、真实滑点、账户已有持仓和人工执行偏差。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prices-file", type=Path, default=DEFAULT_PRICES_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--warmup-start-date", default="2025-01-07")
    parser.add_argument("--start-date", default="2025-07-05")
    parser.add_argument("--end-date", default="2026-07-06")
    args = parser.parse_args()
    config = ResearchConfig(
        prices_file=args.prices_file,
        output_dir=args.output_dir,
        warmup_start_date=args.warmup_start_date,
        start_date=args.start_date,
        end_date=args.end_date,
        initial_cash=100000.0,
        max_positions=2,
        max_position_weight=0.34,
        strong_total_weight=0.68,
        neutral_total_weight=0.34,
        min_price=3.0,
        max_price=80.0,
        min_amount20=30_000_000.0,
        min_candidates=20,
        stop_loss=0.06,
        trailing_stop=0.10,
        trend_exit_window=20,
        buy_cost=0.0003,
        sell_cost=0.0013,
        min_cost=5.0,
    )
    bars = load_bars(config.prices_file, config.warmup_start_date, config.end_date)
    p = pivot_bars(bars)
    deciles, vol_result = analyze_price_volatility(p, config.start_date)
    factors = build_factor_table(p, config)
    ic, factor_summary = factor_metrics(factors)
    feature_ic = feature_ic_metrics(factors)
    signals, debug = build_signals(p, factors, config)
    equity, trades, strategy_summary = backtest(p, signals, config)
    regime = regime_performance(equity, debug)
    sensitivity = sensitivity_test(p, factors, config)

    output = config.output_dir
    atomic_write_csv(deciles, output / "price_volatility_deciles.csv")
    atomic_write_csv(vol_result["daily_correlations"], output / "price_volatility_daily_correlations.csv")
    atomic_write_csv(factors, output / "factor_table.csv")
    atomic_write_csv(ic, output / "factor_ic.csv")
    atomic_write_csv(feature_ic, output / "feature_ic.csv")
    atomic_write_csv(signals, output / "target_weight_signals.csv")
    atomic_write_csv(debug, output / "signal_debug.csv")
    atomic_write_csv(equity, output / "equity_curve.csv")
    atomic_write_csv(trades, output / "trades.csv")
    atomic_write_csv(regime, output / "regime_performance.csv")
    atomic_write_csv(sensitivity, output / "sensitivity.csv")
    summary = {
        "config": {k: str(v) if isinstance(v, Path) else v for k, v in asdict(config).items()},
        "price_volatility": vol_result["summary"],
        "factor": factor_summary,
        "strategy": strategy_summary,
    }
    atomic_write_text(output / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
    atomic_write_text(
        REPO_ROOT / "PRICE_VOLATILITY_ASYMMETRY_STRATEGY_REPORT.md",
        make_report(config, vol_result["summary"], factor_summary, strategy_summary, regime, sensitivity, feature_ic),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
