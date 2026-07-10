#!/usr/bin/env python3
"""Validate v4 with currently available local data only.

This script deliberately avoids new data downloads. It audits the existing
BigTrader result and runs local approximate ablations/sensitivity tests from the
same offline OHLCV file used by v4.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_PRICES = REPO_ROOT / "data/offline/a_share_12m_bigquant/prices_long.csv"
DEFAULT_BIGTRADER_DIR = REPO_ROOT / "data/backtests/bigquant_strategy_v4/20260708_full_year"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data/backtests/v4_validation/20260708_full_year"
DEFAULT_REPORT = REPO_ROOT / "V4_STRATEGY_VALIDATION_REPORT.md"


@dataclass(frozen=True)
class V4Config:
    start_date: str = "2025-07-08"
    end_date: str = "2026-07-08"
    warmup_start_date: str = "2025-01-07"
    initial_cash: float = 100_000.0
    max_positions: int = 2
    min_candidates: int = 2
    max_position_weight: float = 0.34
    strong_total_weight: float = 0.68
    neutral_total_weight: float = 0.34
    min_price: float = 2.5
    max_price: float = 85.0
    min_mom20: float = 0.0
    min_mom60: float = 0.0
    max_mom20: float = 0.45
    max_mom5: float = 0.45
    max_vol20: float = 0.055
    max_drawdown60: float = 0.22
    max_signal_gap: float = 0.06
    min_breadth20: float = 0.55
    min_breadth60: float = 0.50
    max_market_vol20: float = 0.021
    max_weak_drawdown_ratio: float = 0.18
    stop_loss: float = 0.06
    trailing_stop: float = 0.10
    trend_exit_window: int = 20
    buy_cost: float = 0.0003
    sell_cost: float = 0.0013
    min_cost: float = 5.0


@dataclass(frozen=True)
class RunOptions:
    name: str
    disable_market_timing: bool = False
    disable_stops: bool = False
    equal_weight: bool = False
    disable_low_vol_score: bool = False
    disable_overheat_filter: bool = False


@dataclass(frozen=True)
class ScriptConfig:
    prices_file: Path
    bigtrader_dir: Path
    output_dir: Path
    report_file: Path
    v4: V4Config


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as h:
        tmp = Path(h.name)
        h.write(text)
    tmp.replace(path)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, delete=False) as h:
        tmp = Path(h.name)
        df.to_csv(h, index=False)
    tmp.replace(path)


def pct(x: float | int | None) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    return f"{float(x):.2%}"


def num(x: float | int | None, digits: int = 2) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    return f"{float(x):.{digits}f}"


def md_table(df: pd.DataFrame, cols: list[str], fmts: dict[str, str] | None = None, max_rows: int | None = None) -> str:
    fmts = fmts or {}
    item = df.loc[:, cols].head(max_rows) if max_rows else df.loc[:, cols]
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in item.iterrows():
        cells: list[str] = []
        for col in cols:
            val = row[col]
            if fmts.get(col) == "pct":
                cells.append(pct(val))
            elif fmts.get(col) == "num":
                cells.append(num(val))
            else:
                cells.append(str(val))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def max_drawdown(equity: pd.Series) -> tuple[float, str, str]:
    if equity.empty:
        return 0.0, "", ""
    peak = equity.cummax()
    dd = equity / peak - 1.0
    trough = str(dd.idxmin())
    peak_date = str(equity.loc[:trough].idxmax())
    return float(dd.min()), peak_date, trough


def perf_summary(equity: pd.Series, benchmark_returns: pd.Series | None = None, initial_cash: float = 100_000.0) -> dict:
    daily = equity.pct_change().fillna(0.0)
    total = float(equity.iloc[-1] / initial_cash - 1.0) if not equity.empty else 0.0
    years = max(len(equity) / 252.0, 1 / 252.0)
    mdd, peak, trough = max_drawdown(equity)
    out = {
        "days": int(len(equity)),
        "final_equity": float(equity.iloc[-1]) if not equity.empty else initial_cash,
        "total_return": total,
        "annual_return": float((1 + total) ** (1 / years) - 1) if total > -1 else -1.0,
        "daily_volatility": float(daily.std() * np.sqrt(252)) if len(daily) > 1 else 0.0,
        "sharpe": float(daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0.0,
        "max_drawdown": mdd,
        "max_drawdown_peak": peak,
        "max_drawdown_trough": trough,
        "win_day_rate": float((daily > 0).mean()),
        "best_day": float(daily.max()),
        "worst_day": float(daily.min()),
    }
    if benchmark_returns is not None and benchmark_returns.std() > 0 and daily.std() > 0:
        aligned = pd.concat([daily.rename("strategy"), benchmark_returns.rename("benchmark")], axis=1).dropna()
        if len(aligned) > 5:
            beta = float(aligned["strategy"].cov(aligned["benchmark"]) / aligned["benchmark"].var())
            corr = float(aligned["strategy"].corr(aligned["benchmark"]))
            out["beta_to_benchmark"] = beta
            out["corr_to_benchmark"] = corr
    return out


def compound_returns(series: pd.Series) -> float:
    return float((1.0 + series.fillna(0.0)).prod() - 1.0)


def parse_list(value: object) -> list[dict]:
    if not isinstance(value, str) or value in {"", "nan"}:
        return []
    try:
        parsed = ast.literal_eval(value)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def audit_bigtrader(bigtrader_dir: Path, initial_cash: float) -> dict[str, pd.DataFrame | dict]:
    raw = pd.read_csv(bigtrader_dir / "bigtrader_raw_perf.csv")
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw.sort_values("date").reset_index(drop=True)
    raw["month"] = raw["date"].dt.to_period("M").astype(str)
    raw["quarter"] = raw["date"].dt.to_period("Q").astype(str)
    eq = raw.set_index(raw["date"].dt.strftime("%Y-%m-%d"))["portfolio_value"]
    benchmark_returns = raw.set_index(raw["date"].dt.strftime("%Y-%m-%d"))["benchmark_returns"]
    summary = perf_summary(eq, benchmark_returns, initial_cash)
    summary.update(
        {
            "active_days": int((raw["long_value"] > 0).sum()),
            "active_day_ratio": float((raw["long_value"] > 0).mean()),
            "avg_gross_leverage": float(raw["gross_leverage"].mean()),
            "avg_gross_when_active": float(raw.loc[raw["gross_leverage"] > 0, "gross_leverage"].mean()) if (raw["gross_leverage"] > 0).any() else 0.0,
            "max_gross_leverage": float(raw["gross_leverage"].max()),
            "total_commission": float(raw["commission"].sum()),
            "total_turnover_value": float(raw["today_sum_buy_value"].sum() + raw["today_sum_sell_value"].sum()),
            "turnover_on_initial_cash": float((raw["today_sum_buy_value"].sum() + raw["today_sum_sell_value"].sum()) / initial_cash),
            "benchmark_total_return": float(raw["benchmark_period_return"].iloc[-1]),
+            "relative_total_return": float(raw["algorithm_period_return"].iloc[-1] - raw["benchmark_period_return"].iloc[-1]),
        }
    )
    monthly = raw.groupby("month", observed=True)["returns"].apply(compound_returns).reset_index(name="strategy_return")
    monthly_bm = raw.groupby("month", observed=True)["benchmark_returns"].apply(compound_returns).reset_index(name="benchmark_return")
    monthly = monthly.merge(monthly_bm, on="month", how="left")
    monthly["excess_return"] = monthly["strategy_return"] - monthly["benchmark_return"]
    quarterly = raw.groupby("quarter", observed=True)["returns"].apply(compound_returns).reset_index(name="strategy_return")
    quarterly_bm = raw.groupby("quarter", observed=True)["benchmark_returns"].apply(compound_returns).reset_index(name="benchmark_return")
    quarterly = quarterly.merge(quarterly_bm, on="quarter", how="left")
    quarterly["excess_return"] = quarterly["strategy_return"] - quarterly["benchmark_return"]
    day_contrib = raw[["date", "returns", "portfolio_value", "long_value", "gross_leverage"]].copy()
    day_contrib["date"] = day_contrib["date"].dt.strftime("%Y-%m-%d")
    best_days = day_contrib.sort_values("returns", ascending=False).head(10)
    worst_days = day_contrib.sort_values("returns", ascending=True).head(10)

    tx_rows: list[dict] = []
    pos_rows: list[dict] = []
    for _, row in raw.iterrows():
        date = row["date"].strftime("%Y-%m-%d")
        for tx in parse_list(row.get("transactions")):
            tx_rows.append({"date": date, **tx})
        for pos in parse_list(row.get("positions")):
            pos_rows.append({"date": date, **pos})
    tx = pd.DataFrame(tx_rows)
    if not tx.empty:
        for col in ["amount", "price", "transaction_money", "commission", "realized_pnl"]:
            tx[col] = pd.to_numeric(tx[col], errors="coerce")
        tx["side"] = np.where(tx["amount"] > 0, "buy", "sell")
        tx["notional"] = tx["amount"].abs() * tx["price"]
    pos = pd.DataFrame(pos_rows)
    if not pos.empty:
        for col in ["amount", "last_price", "market_value", "holding_pnl", "profit_ratio", "hold_percent", "hold_days"]:
            if col in pos.columns:
                pos[col] = pd.to_numeric(pos[col], errors="coerce")

    if tx.empty:
        instrument_pnl = pd.DataFrame(columns=["instrument", "name", "realized_pnl", "commission", "buy_notional", "sell_notional", "trade_count"])
    else:
        grouped = tx.groupby("instrument", observed=True)
        instrument_pnl = grouped.agg(
            name=("name", "last"),
            realized_pnl=("realized_pnl", "sum"),
            commission=("commission", "sum"),
            notional=("notional", "sum"),
            trade_count=("instrument", "size"),
            first_trade=("date", "min"),
            last_trade=("date", "max"),
        ).reset_index()
        buys = tx[tx["side"] == "buy"].groupby("instrument", observed=True)["notional"].sum().rename("buy_notional")
        sells = tx[tx["side"] == "sell"].groupby("instrument", observed=True)["notional"].sum().rename("sell_notional")
        instrument_pnl = instrument_pnl.merge(buys, on="instrument", how="left").merge(sells, on="instrument", how="left")
        instrument_pnl[["buy_notional", "sell_notional"]] = instrument_pnl[["buy_notional", "sell_notional"]].fillna(0.0)
        instrument_pnl = instrument_pnl.sort_values("realized_pnl", ascending=False).reset_index(drop=True)
        total_profit = float(instrument_pnl.loc[instrument_pnl["realized_pnl"] > 0, "realized_pnl"].sum())
        instrument_pnl["positive_pnl_share"] = np.where(total_profit > 0, instrument_pnl["realized_pnl"].clip(lower=0) / total_profit, 0.0)
    final_equity = float(raw["portfolio_value"].iloc[-1])
    removal_rows = []
    winners = instrument_pnl[instrument_pnl["realized_pnl"] > 0].sort_values("realized_pnl", ascending=False) if not instrument_pnl.empty else pd.DataFrame()
    for k in [1, 2, 3, 5, 10]:
        removed = float(winners.head(k)["realized_pnl"].sum()) if not winners.empty else 0.0
        removal_rows.append({"remove_top_winners": k, "removed_realized_pnl": removed, "approx_final_equity": final_equity - removed, "approx_total_return": (final_equity - removed) / initial_cash - 1.0})
    winner_removal = pd.DataFrame(removal_rows)
    return {
        "raw": raw,
        "summary": summary,
        "monthly": monthly,
        "quarterly": quarterly,
        "transactions": tx,
        "positions": pos,
        "instrument_pnl": instrument_pnl,
        "winner_removal": winner_removal,
        "best_days": best_days,
        "worst_days": worst_days,
    }


def load_prices(path: Path, cfg: V4Config) -> dict[str, pd.DataFrame]:
    bars = pd.read_csv(path, dtype={"symbol": str})
    bars["date"] = pd.to_datetime(bars["date"])
    bars["symbol"] = bars["symbol"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    bars = bars[(bars["date"] >= pd.to_datetime(cfg.warmup_start_date)) & (bars["date"] <= pd.to_datetime(cfg.end_date))].copy()
    for col in ["open", "high", "low", "close", "volume", "amount", "turnover"]:
        bars[col] = pd.to_numeric(bars[col], errors="coerce")
    pivots = {}
    for col in ["open", "close", "volume", "amount", "turnover"]:
        pivots[col] = bars.pivot(index="date", columns="symbol", values=col).sort_index()
    pivots["open"] = pivots["open"].fillna(pivots["close"])
    return pivots


def pct_rank(series: pd.Series, ascending: bool = True) -> pd.Series:
    return series.replace([np.inf, -np.inf], np.nan).rank(pct=True, ascending=ascending)


def first_week_days(index: pd.DatetimeIndex) -> set[pd.Timestamp]:
    grouped = pd.Series(index, index=index).groupby(index.to_period("W-FRI")).first()
    return set(pd.DatetimeIndex(grouped.values))


def market_exposure(close: pd.DataFrame, loc: int, cfg: V4Config) -> float:
    if loc < 120:
        return 0.0
    hist = close.iloc[: loc + 1]
    last = hist.iloc[-1]
    ma20 = hist.rolling(20).mean().iloc[-1]
    ma60 = hist.rolling(60).mean().iloc[-1]
    ma120 = hist.rolling(120).mean().iloc[-1]
    breadth20 = float((last > ma20).mean())
    breadth60 = float((last > ma60).mean())
    breadth120 = float((last > ma120).mean())
    median_ret10 = float((last / hist.iloc[-11] - 1).median())
    median_ret20 = float((last / hist.iloc[-21] - 1).median())
    median_ret60 = float((last / hist.iloc[-61] - 1).median())
    market_vol20 = float(close.pct_change(fill_method=None).iloc[max(0, loc - 20) : loc + 1].std().median())
    recent = hist.iloc[max(0, loc - 20) : loc + 1]
    weak_dd = float(((recent.iloc[-1] / recent.cummax().iloc[-1] - 1) < -0.10).mean())
    if (
        breadth20 < cfg.min_breadth20
        or breadth60 < cfg.min_breadth60
        or breadth120 < 0.48
        or median_ret20 < -0.04
        or median_ret60 < -0.08
        or market_vol20 > cfg.max_market_vol20
        or weak_dd > cfg.max_weak_drawdown_ratio
    ):
        return 0.0
    if breadth20 < 0.52 or breadth60 < 0.56 or median_ret10 < -0.01 or median_ret20 < 0.0:
        return cfg.neutral_total_weight
    return cfg.strong_total_weight


def allocate(selected: list[str], vol20: pd.Series, gross: float, cfg: V4Config, equal_weight: bool) -> pd.Series:
    weights = pd.Series(0.0, index=vol20.index)
    if not selected or gross <= 0:
        return weights
    if equal_weight:
        alloc = pd.Series(gross / len(selected), index=selected)
    else:
        raw = (1.0 / vol20.reindex(selected).replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        if raw.sum() <= 0:
            raw = pd.Series(1.0, index=selected)
        alloc = raw / raw.sum() * gross
    alloc = alloc.clip(upper=cfg.max_position_weight)
    if alloc.sum() > gross:
        alloc = alloc / alloc.sum() * gross
    weights.loc[alloc.index] = alloc
    return weights


def build_local_targets(p: dict[str, pd.DataFrame], cfg: V4Config, opt: RunOptions) -> tuple[dict[pd.Timestamp, pd.Series], pd.DataFrame]:
    close = p["close"]
    open_ = p["open"].reindex_like(close).ffill().fillna(close)
    volume = p["volume"].reindex_like(close).ffill()
    traded_value = (open_ * volume).replace([np.inf, -np.inf], np.nan)
    returns = close.pct_change(fill_method=None)
    weeks = first_week_days(pd.DatetimeIndex(close.index))
    formal_start = pd.to_datetime(cfg.start_date)
    current = pd.Series(0.0, index=close.columns)
    targets: dict[pd.Timestamp, pd.Series] = {}
    entry_price: dict[str, float] = {}
    peak_price: dict[str, float] = {}
    debug_rows: list[dict] = []
    for loc, dt in enumerate(close.index):
        if dt < formal_start:
            continue
        last = close.iloc[loc]
        selected: list[str] = []
        candidate_count = 0
        gross = np.nan
        if dt in weeks and loc >= 120:
            hist = close.iloc[: loc + 1]
            mom5 = last / hist.iloc[-6] - 1
            mom20 = last / hist.iloc[-21] - 1
            mom60 = last / hist.iloc[-61] - 1
            mom120 = last / hist.iloc[-121] - 1
            ma20 = hist.rolling(20).mean().iloc[-1]
            ma60 = hist.rolling(60).mean().iloc[-1]
            ma120 = hist.rolling(120).mean().iloc[-1]
            vol20 = returns.iloc[max(0, loc - 20) : loc + 1].std()
            downside60 = returns.iloc[max(0, loc - 60) : loc + 1].clip(upper=0).std()
            trailing60 = hist.iloc[max(0, loc - 60) : loc + 1]
            drawdown60 = trailing60.iloc[-1] / trailing60.cummax().iloc[-1] - 1
            avg_value20 = traded_value.iloc[max(0, loc - 20) : loc + 1].mean()
            prev_close = hist.iloc[-2]
            signal_gap = open_.iloc[loc] / prev_close - 1
            tradable = (
                last.between(cfg.min_price, cfg.max_price)
                & (last > ma20)
                & (last > ma60)
                & (last > ma120)
                & (mom20 > cfg.min_mom20)
                & (mom60 > cfg.min_mom60)
                & (mom120 > -0.03)
                & (vol20 < cfg.max_vol20)
                & (drawdown60 > -cfg.max_drawdown60)
                & (signal_gap < cfg.max_signal_gap)
                & avg_value20.notna()
                & (avg_value20 > 0)
                & vol20.notna()
            )
            if not opt.disable_overheat_filter:
                tradable = tradable & (mom20 < cfg.max_mom20) & (mom5 < cfg.max_mom5)
            if opt.disable_low_vol_score:
                score = (
                    0.31 * pct_rank(mom60)
                    + 0.24 * pct_rank(mom120)
                    + 0.13 * pct_rank(mom20)
                    + 0.24 * pct_rank(last / ma60 - 1)
                    + 0.05 * pct_rank(drawdown60)
                    + 0.03 * pct_rank(avg_value20)
                )
            else:
                score = (
                    0.24 * pct_rank(mom60)
                    + 0.18 * pct_rank(mom120)
                    + 0.10 * pct_rank(mom20)
                    + 0.18 * pct_rank(last / ma60 - 1)
                    + 0.16 * pct_rank(vol20, ascending=False)
                    + 0.08 * pct_rank(downside60, ascending=False)
                    + 0.04 * pct_rank(drawdown60)
                    + 0.02 * pct_rank(avg_value20)
                )
            ranked = score[tradable.reindex(score.index).fillna(False)].dropna().sort_values(ascending=False)
            selected = ranked.head(cfg.max_positions).index.tolist()
            candidate_count = int(len(ranked))
            gross = cfg.strong_total_weight if opt.disable_market_timing else market_exposure(close, loc, cfg)
            if len(ranked) < cfg.min_candidates:
                gross = 0.0
            previous = current.copy()
            if gross > 0 and selected:
                current = allocate(selected, vol20, gross, cfg, opt.equal_weight)
                entry_price = {s: float(last.loc[s]) for s in selected if current.loc[s] > 0 and pd.notna(last.loc[s])}
                peak_price = dict(entry_price)
            else:
                current = pd.Series(0.0, index=close.columns)
                entry_price = {}
                peak_price = {}
            if not current.equals(previous):
                targets[dt] = current.copy()
        if not opt.disable_stops and loc >= max(1, cfg.trend_exit_window):
            trend_ma = close.iloc[: loc + 1].rolling(cfg.trend_exit_window).mean().iloc[-1]
            previous = current.copy()
            for sym in list(current[current > 0].index):
                price = float(last.loc[sym]) if pd.notna(last.loc[sym]) else np.nan
                if not math.isfinite(price):
                    current.loc[sym] = 0.0
                    entry_price.pop(sym, None)
                    peak_price.pop(sym, None)
                    continue
                peak_price[sym] = max(peak_price.get(sym, price), price)
                broken = price < float(trend_ma.loc[sym]) if pd.notna(trend_ma.loc[sym]) else False
                stop = price <= entry_price.get(sym, price) * (1.0 - cfg.stop_loss)
                trail = price <= peak_price.get(sym, price) * (1.0 - cfg.trailing_stop)
                if broken or stop or trail:
                    current.loc[sym] = 0.0
                    entry_price.pop(sym, None)
                    peak_price.pop(sym, None)
            if not current.equals(previous):
                targets[dt] = current.copy()
        if dt in weeks:
            debug_rows.append({"date": dt.strftime("%Y-%m-%d"), "run": opt.name, "gross": 0.0 if pd.isna(gross) else gross, "candidate_count": candidate_count, "selected": ",".join(selected)})
    return targets, pd.DataFrame(debug_rows)


def local_backtest(p: dict[str, pd.DataFrame], targets: dict[pd.Timestamp, pd.Series], cfg: V4Config, run_name: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    close = p["close"]
    open_ = p["open"].reindex_like(close).ffill().fillna(close)
    dates = [dt for dt in close.index if dt >= pd.to_datetime(cfg.start_date)]
    symbols = list(close.columns)
    cash = cfg.initial_cash
    shares = pd.Series(0, index=symbols, dtype=int)
    pending: pd.Series | None = None
    eq_rows: list[dict] = []
    trade_rows: list[dict] = []
    for dt in dates:
        d = dt.strftime("%Y-%m-%d")
        op = open_.loc[dt].fillna(close.loc[dt])
        if pending is not None:
            equity_open = cash + float((shares * op.fillna(0.0)).sum())
            target_value = pending.reindex(symbols).fillna(0.0) * equity_open
            trade_symbols = sorted(set(target_value[target_value > 0].index) | set(shares[shares > 0].index))
            for side in ["sell", "buy"]:
                for sym in trade_symbols:
                    price = float(op.loc[sym]) if pd.notna(op.loc[sym]) else np.nan
                    if not math.isfinite(price) or price <= 0:
                        continue
                    target_shares = int((target_value.loc[sym] // (price * 100)) * 100)
                    delta = target_shares - int(shares.loc[sym])
                    if side == "sell" and delta < 0:
                        fee = max(abs(delta) * price * cfg.sell_cost, cfg.min_cost)
                        cash += abs(delta) * price - fee
                        shares.loc[sym] += delta
                        trade_rows.append({"date": d, "run": run_name, "symbol": sym, "side": "sell", "shares": delta, "price": price, "fee": fee})
                    elif side == "buy" and delta > 0:
                        fee = max(delta * price * cfg.buy_cost, cfg.min_cost)
                        cost = delta * price + fee
                        if cost > cash:
                            affordable = int(((cash - cfg.min_cost) // (price * 100)) * 100)
                            delta = max(0, min(delta, affordable))
                            fee = max(delta * price * cfg.buy_cost, cfg.min_cost) if delta > 0 else 0.0
                            cost = delta * price + fee
                        if delta > 0 and cost <= cash:
                            cash -= cost
                            shares.loc[sym] += delta
                            trade_rows.append({"date": d, "run": run_name, "symbol": sym, "side": "buy", "shares": delta, "price": price, "fee": fee})
            pending = None
        cl = close.loc[dt].fillna(op).fillna(0.0)
        equity = cash + float((shares * cl).sum())
        held = shares[shares > 0]
        eq_rows.append({"date": d, "run": run_name, "equity": equity, "cash": cash, "positions": int(len(held)), "gross_exposure": float((shares * cl).sum() / equity) if equity else 0.0})
        if dt in targets:
            pending = targets[dt]
    equity = pd.DataFrame(eq_rows)
    trades = pd.DataFrame(trade_rows)
    eq = equity.set_index("date")["equity"]
    summary = {"run": run_name, **perf_summary(eq, initial_cash=cfg.initial_cash)}
    summary.update(
        {
            "trade_count": int(len(trades)),
            "total_fees": float(trades["fee"].sum()) if not trades.empty else 0.0,
            "active_day_ratio": float((equity["gross_exposure"] > 0).mean()) if not equity.empty else 0.0,
            "avg_gross_exposure": float(equity["gross_exposure"].mean()) if not equity.empty else 0.0,
            "turnover_on_initial_cash": float((trades["shares"].abs() * trades["price"]).sum() / cfg.initial_cash) if not trades.empty else 0.0,
        }
    )
    return equity, trades, summary


def run_local_suite(prices: dict[str, pd.DataFrame], base_cfg: V4Config) -> dict[str, pd.DataFrame]:
    runs: list[tuple[V4Config, RunOptions, str]] = [
        (base_cfg, RunOptions("base_v4_local"), "ablation"),
        (base_cfg, RunOptions("no_market_timing", disable_market_timing=True), "ablation"),
        (base_cfg, RunOptions("no_daily_stops", disable_stops=True), "ablation"),
        (base_cfg, RunOptions("equal_weight_selected", equal_weight=True), "ablation"),
        (base_cfg, RunOptions("no_low_vol_score", disable_low_vol_score=True), "ablation"),
        (base_cfg, RunOptions("no_overheat_filter", disable_overheat_filter=True), "ablation"),
    ]
    sensitivity_specs = [
        ("max_positions", [1, 2, 3, 5]),
        ("strong_total_weight", [0.50, 0.68, 0.85]),
        ("min_breadth20", [0.50, 0.55, 0.60]),
        ("max_market_vol20", [0.018, 0.021, 0.026]),
        ("max_vol20", [0.045, 0.055, 0.070]),
        ("max_drawdown60", [0.16, 0.22, 0.30]),
        ("stop_loss", [0.04, 0.06, 0.08]),
        ("trailing_stop", [0.08, 0.10, 0.14]),
    ]
    for field, values in sensitivity_specs:
        for value in values:
            kwargs = {field: value}
            if field == "strong_total_weight":
                kwargs["neutral_total_weight"] = value / 2
            cfg = replace(base_cfg, **kwargs)
            runs.append((cfg, RunOptions(f"sens_{field}_{value}"), "sensitivity"))
    summary_rows: list[dict] = []
    equity_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    debug_frames: list[pd.DataFrame] = []
    for cfg, opt, group in runs:
        targets, debug = build_local_targets(prices, cfg, opt)
        equity, trades, summary = local_backtest(prices, targets, cfg, opt.name)
        summary["group"] = group
        summary["changed_config"] = json.dumps({k: v for k, v in asdict(cfg).items() if getattr(base_cfg, k) != v}, ensure_ascii=False)
        summary_rows.append(summary)
        equity_frames.append(equity)
        trade_frames.append(trades)
        debug_frames.append(debug)
    return {
        "local_summaries": pd.DataFrame(summary_rows).sort_values(["group", "sharpe"], ascending=[True, False]),
        "local_equity": pd.concat(equity_frames, ignore_index=True),
        "local_trades": pd.concat(trade_frames, ignore_index=True) if any(not x.empty for x in trade_frames) else pd.DataFrame(),
        "local_debug": pd.concat(debug_frames, ignore_index=True),
    }


def sensitivity_stability(summaries: pd.DataFrame) -> pd.DataFrame:
    sens = summaries[summaries["group"] == "sensitivity"].copy()
    rows = []
    for run, row in sens.iterrows():
        name = row["run"]
        parts = str(name).split("_")
        field = "_".join(parts[1:-1])
        rows.append({"parameter": field, **row.to_dict()})
    detail = pd.DataFrame(rows)
    if detail.empty:
        return pd.DataFrame()
    return detail.groupby("parameter", observed=True).agg(
        runs=("run", "count"),
        min_total_return=("total_return", "min"),
        median_total_return=("total_return", "median"),
        max_total_return=("total_return", "max"),
        min_sharpe=("sharpe", "min"),
        median_sharpe=("sharpe", "median"),
        max_drawdown_worst=("max_drawdown", "min"),
        positive_return_rate=("total_return", lambda x: float((x > 0).mean())),
    ).reset_index()


def build_report(cfg: ScriptConfig, audit: dict, local: dict, stability: pd.DataFrame) -> str:
    bt = audit["summary"]
    monthly = audit["monthly"]
    quarterly = audit["quarterly"]
    pnl = audit["instrument_pnl"]
    removal = audit["winner_removal"]
    best_days = audit["best_days"]
    worst_days = audit["worst_days"]
    local_summaries = local["local_summaries"]
    ablations = local_summaries[local_summaries["group"] == "ablation"].sort_values("sharpe", ascending=False)
    sensitivity = local_summaries[local_summaries["group"] == "sensitivity"].sort_values("sharpe", ascending=False)
    base_local = local_summaries[local_summaries["run"] == "base_v4_local"].iloc[0].to_dict()
    top_pnl = pnl.head(10).copy() if not pnl.empty else pd.DataFrame()
    if not top_pnl.empty:
        top_pnl["realized_pnl_on_initial_cash"] = top_pnl["realized_pnl"] / cfg.v4.initial_cash
    report = [
        "# v4 策略严谨验证报告（仅使用现有本地数据）",
        "",
        "## 结论摘要",
        f"- BigTrader 原始结果：累计收益 {pct(bt['total_return'])}，年化 {pct(bt['annual_return'])}，Sharpe {num(bt['sharpe'])}，最大回撤 {pct(bt['max_drawdown'])}，相对 benchmark 超额 {pct(bt['relative_total_return'])}。",
        f"- 风险暴露很克制：有持仓交易日占比 {pct(bt['active_day_ratio'])}，全样本平均 gross exposure {pct(bt['avg_gross_leverage'])}，活跃日平均 gross exposure {pct(bt['avg_gross_when_active'])}。",
        f"- 交易集中度高：BigTrader 交易涉及 {len(pnl)} 只股票；Top 1 盈利股票约贡献 {pct(top_pnl.iloc[0]['positive_pnl_share']) if not top_pnl.empty else 'NA'} 的正 realized PnL。去掉 Top 1/3 盈利股票后，近似累计收益分别变为 {pct(removal.iloc[0]['approx_total_return'])} / {pct(removal.iloc[2]['approx_total_return'])}。",
        f"- 本地近似复现 base_v4_local：累计收益 {pct(base_local['total_return'])}，Sharpe {num(base_local['sharpe'])}，最大回撤 {pct(base_local['max_drawdown'])}。它与 BigTrader 不完全一致，只用于相对消融/敏感性，不替代 BigTrader。",
        "- 严格结论：v4 不是 toy，但现有一年数据无法证明它是成熟 alpha；当前证据支持“有潜力、强依赖择时/少数交易、需要继续样本外跟踪”。",
        "",
        "## 数据边界与不可消除偏差",
        f"- 使用行情：`{cfg.prices_file}`；使用 BigTrader 输出：`{cfg.bigtrader_dir}`。没有下载或调用任何新数据。",
        "- 当前只有约一年有效回测，不能覆盖完整牛熊震荡周期。",
        "- 股票池仍沿用项目现有口径；由于缺少 point-in-time 成分、历史 ST/退市/停牌状态，幸存者偏差和可交易性偏差无法彻底消除。",
        "- 本地消融回测是近似撮合；正式绩效仍以 BigTrader 原始结果为准。",
        "",
        "## BigTrader 原始绩效审计",
        md_table(
            pd.DataFrame([bt]),
            ["total_return", "annual_return", "sharpe", "daily_volatility", "max_drawdown", "win_day_rate", "beta_to_benchmark", "corr_to_benchmark", "benchmark_total_return"],
            {"total_return": "pct", "annual_return": "pct", "daily_volatility": "pct", "max_drawdown": "pct", "win_day_rate": "pct", "beta_to_benchmark": "num", "corr_to_benchmark": "num", "benchmark_total_return": "pct", "sharpe": "num"},
        ),
        "",
        "## 月度收益",
        md_table(monthly, ["month", "strategy_return", "benchmark_return", "excess_return"], {"strategy_return": "pct", "benchmark_return": "pct", "excess_return": "pct"}),
        "",
        "## 季度收益",
        md_table(quarterly, ["quarter", "strategy_return", "benchmark_return", "excess_return"], {"strategy_return": "pct", "benchmark_return": "pct", "excess_return": "pct"}),
        "",
        "## 交易和 PnL 集中度",
        md_table(top_pnl, ["instrument", "name", "realized_pnl", "realized_pnl_on_initial_cash", "positive_pnl_share", "trade_count", "first_trade", "last_trade"], {"realized_pnl": "num", "realized_pnl_on_initial_cash": "pct", "positive_pnl_share": "pct"}, max_rows=10) if not top_pnl.empty else "无交易 PnL。",
        "",
        "### 去掉最大赢家压力测试（路径近似）",
        md_table(removal, ["remove_top_winners", "removed_realized_pnl", "approx_final_equity", "approx_total_return"], {"removed_realized_pnl": "num", "approx_final_equity": "num", "approx_total_return": "pct"}),
        "",
        "解释：这是把最大盈利股票 realized PnL 从最终权益中扣除的静态压力测试，不重算路径和仓位；它衡量收益对少数赢家的依赖程度。",
        "",
        "## 单日收益贡献",
        "### 最好 10 天",
        md_table(best_days, ["date", "returns", "portfolio_value", "gross_leverage"], {"returns": "pct", "portfolio_value": "num", "gross_leverage": "pct"}),
        "",
        "### 最差 10 天",
        md_table(worst_days, ["date", "returns", "portfolio_value", "gross_leverage"], {"returns": "pct", "portfolio_value": "num", "gross_leverage": "pct"}),
        "",
        "## 本地近似消融测试",
        md_table(ablations, ["run", "total_return", "annual_return", "sharpe", "max_drawdown", "active_day_ratio", "trade_count", "turnover_on_initial_cash"], {"total_return": "pct", "annual_return": "pct", "sharpe": "num", "max_drawdown": "pct", "active_day_ratio": "pct", "turnover_on_initial_cash": "num"}),
        "",
        "消融解释：`no_market_timing` 用于衡量市场择时保护；`no_daily_stops` 衡量日内风控退出；`no_low_vol_score` 衡量低波/下行波动打分；`equal_weight_selected` 衡量波动率倒数分配。",
        "",
        "## 参数敏感性汇总（本地近似）",
        md_table(stability, ["parameter", "runs", "min_total_return", "median_total_return", "max_total_return", "min_sharpe", "median_sharpe", "max_drawdown_worst", "positive_return_rate"], {"min_total_return": "pct", "median_total_return": "pct", "max_total_return": "pct", "min_sharpe": "num", "median_sharpe": "num", "max_drawdown_worst": "pct", "positive_return_rate": "pct"}) if not stability.empty else "无参数敏感性结果。",
        "",
        "### 参数敏感性 Top/Bottom 明细",
        "Top 10：",
        md_table(sensitivity.head(10), ["run", "total_return", "sharpe", "max_drawdown", "changed_config"], {"total_return": "pct", "sharpe": "num", "max_drawdown": "pct"}),
        "",
        "Bottom 10：",
        md_table(sensitivity.tail(10), ["run", "total_return", "sharpe", "max_drawdown", "changed_config"], {"total_return": "pct", "sharpe": "num", "max_drawdown": "pct"}),
        "",
        "## 研究判断",
        "- v4 的强表现主要来自低暴露择时 + 集中捕捉少数强势交易，而不是每天稳定产生收益。",
        "- 一年样本里，收益对少数赢家有明显依赖；这降低了统计置信度，也意味着后续必须持续跟踪 out-of-sample。",
        "- 如果本地消融显示禁用市场择时后回撤或收益恶化，则说明 v4 的核心不是单纯选股，而是风险开关。",
        "- 参数敏感性若大部分邻近参数仍为正，说明规则有一定稳健性；若只有精确默认参数表现好，则要警惕过拟合。",
        "- 在没有新数据之前，最严谨的下一步是每日滚动记录 forward OOS：冻结 v4 参数，不再改规则，积累未来 3~6 个月真实样本。",
        "",
        "## 输出文件",
        f"- 验证目录：`{cfg.output_dir}`",
        "- `bigtrader_monthly_returns.csv`、`bigtrader_instrument_pnl.csv`、`winner_removal_stress.csv`。",
        "- `local_ablation_summaries.csv`、`local_equity_curves.csv`、`local_trades.csv`、`parameter_stability.csv`。",
    ]
    return "\n".join(report) + "\n"


def run(cfg: ScriptConfig) -> dict:
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    audit = audit_bigtrader(cfg.bigtrader_dir, cfg.v4.initial_cash)
    prices = load_prices(cfg.prices_file, cfg.v4)
    local = run_local_suite(prices, cfg.v4)
    stability = sensitivity_stability(local["local_summaries"])
    write_csv(audit["monthly"], cfg.output_dir / "bigtrader_monthly_returns.csv")
    write_csv(audit["quarterly"], cfg.output_dir / "bigtrader_quarterly_returns.csv")
    write_csv(audit["instrument_pnl"], cfg.output_dir / "bigtrader_instrument_pnl.csv")
    write_csv(audit["transactions"], cfg.output_dir / "bigtrader_transactions.csv")
    write_csv(audit["winner_removal"], cfg.output_dir / "winner_removal_stress.csv")
    write_csv(audit["best_days"], cfg.output_dir / "best_days.csv")
    write_csv(audit["worst_days"], cfg.output_dir / "worst_days.csv")
    write_csv(local["local_summaries"], cfg.output_dir / "local_ablation_summaries.csv")
    write_csv(local["local_equity"], cfg.output_dir / "local_equity_curves.csv")
    write_csv(local["local_trades"], cfg.output_dir / "local_trades.csv")
    write_csv(local["local_debug"], cfg.output_dir / "local_signal_debug.csv")
    write_csv(stability, cfg.output_dir / "parameter_stability.csv")
    summary = {
        "config": {k: str(v) if isinstance(v, Path) else v for k, v in asdict(cfg).items()},
        "bigtrader_summary": audit["summary"],
        "base_local_summary": local["local_summaries"].loc[local["local_summaries"]["run"] == "base_v4_local"].iloc[0].to_dict(),
        "output_dir": str(cfg.output_dir),
        "report_file": str(cfg.report_file),
    }
    write_text(cfg.output_dir / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    report = build_report(cfg, audit, local, stability)
    write_text(cfg.output_dir / "report.md", report)
    write_text(cfg.report_file, report)
    return summary


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate v4 strategy with existing local data only.")
    p.add_argument("--prices-file", default=str(DEFAULT_PRICES))
    p.add_argument("--bigtrader-dir", default=str(DEFAULT_BIGTRADER_DIR))
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    p.add_argument("--report-file", default=str(DEFAULT_REPORT))
    return p.parse_args()


def main() -> None:
    a = parse_args()
    cfg = ScriptConfig(
        prices_file=Path(a.prices_file),
        bigtrader_dir=Path(a.bigtrader_dir),
        output_dir=Path(a.output_dir),
        report_file=Path(a.report_file),
        v4=V4Config(),
    )
    print(json.dumps(run(cfg), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
