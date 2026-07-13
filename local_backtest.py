#!/usr/bin/env python3
"""Local A-share backtest engine with next-open execution constraints."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 100_000.0
    buy_cost: float = 0.0003
    sell_cost: float = 0.0013
    min_cost: float = 5.0
    lot_size: int = 100
    benchmark_symbol: str = "000300.SH"


@dataclass(frozen=True)
class BacktestArtifacts:
    raw_perf: pd.DataFrame
    trades: pd.DataFrame
    summary: dict


def max_drawdown(equity: pd.Series) -> tuple[float, str, str]:
    if equity.empty:
        return 0.0, "", ""
    peak = equity.cummax()
    dd = equity / peak - 1.0
    trough = str(dd.idxmin())
    peak_date = str(equity.loc[:trough].idxmax())
    return float(dd.min()), peak_date, trough


def perf_summary(equity: pd.Series, initial_cash: float = 100_000.0) -> dict:
    daily = equity.pct_change().fillna(0.0)
    total = float(equity.iloc[-1] / initial_cash - 1.0) if not equity.empty else 0.0
    years = max(len(equity) / 252.0, 1 / 252.0)
    mdd, peak, trough = max_drawdown(equity)
    return {
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
        "best_day": float(daily.max()) if not daily.empty else 0.0,
        "worst_day": float(daily.min()) if not daily.empty else 0.0,
    }


def _serialize_positions(shares: pd.Series, close_prices: pd.Series, equity: float) -> list[dict]:
    held = shares[shares > 0]
    rows: list[dict] = []
    for symbol in held.index:
        price = float(close_prices.get(symbol, np.nan))
        amount = int(held.loc[symbol])
        market_value = amount * price if math.isfinite(price) else 0.0
        rows.append(
            {
                "instrument": symbol,
                "symbol": symbol,
                "amount": amount,
                "last_price": None if not math.isfinite(price) else price,
                "market_value": market_value,
                "hold_percent": market_value / equity if equity > 0 else 0.0,
            }
        )
    return rows


def _can_buy(open_price: float, up_limit: float, suspended: bool) -> tuple[bool, str]:
    if suspended or not math.isfinite(open_price) or open_price <= 0:
        return False, "suspended_or_no_open"
    if math.isfinite(up_limit) and open_price >= up_limit * 0.9999:
        return False, "limit_up"
    return True, ""


def _can_sell(open_price: float, down_limit: float, suspended: bool) -> tuple[bool, str]:
    if suspended or not math.isfinite(open_price) or open_price <= 0:
        return False, "suspended_or_no_open"
    if math.isfinite(down_limit) and open_price <= down_limit * 1.0001:
        return False, "limit_down"
    return True, ""


def run_local_backtest(
    prices: dict[str, pd.DataFrame],
    targets: dict[pd.Timestamp, pd.Series],
    config: BacktestConfig,
    *,
    strategy_name: str,
) -> BacktestArtifacts:
    close = prices["close"]
    open_ = prices["open"].reindex_like(close).ffill().fillna(close)
    up_limit = prices.get("up_limit", pd.DataFrame(index=close.index, columns=close.columns, dtype=float)).reindex_like(close)
    down_limit = prices.get("down_limit", pd.DataFrame(index=close.index, columns=close.columns, dtype=float)).reindex_like(close)
    suspended = prices.get("is_suspended", pd.DataFrame(0, index=close.index, columns=close.columns))
    dates = list(close.index)
    symbols = list(close.columns)
    cash = config.initial_cash
    shares = pd.Series(0, index=symbols, dtype=int)
    pending: pd.Series | None = None
    raw_rows: list[dict] = []
    trade_rows: list[dict] = []
    prev_equity = config.initial_cash

    for dt in dates:
        day = dt.strftime("%Y-%m-%d")
        day_trades: list[dict] = []
        op = open_.loc[dt]
        cl = close.loc[dt].fillna(op).fillna(0.0)
        equity_open = cash + float((shares * op.fillna(0.0)).sum())
        buy_value = 0.0
        sell_value = 0.0
        commission = 0.0

        if pending is not None:
            target_value = pending.reindex(symbols).fillna(0.0) * equity_open
            trade_symbols = sorted(set(target_value[target_value > 0].index) | set(shares[shares > 0].index))
            for side in ["sell", "buy"]:
                for symbol in trade_symbols:
                    price = float(op.get(symbol, np.nan))
                    suspended_flag = bool(pd.to_numeric(suspended.loc[dt, symbol], errors="coerce")) if symbol in suspended.columns else False
                    if side == "sell":
                        can_trade, reason = _can_sell(price, float(down_limit.loc[dt, symbol]) if symbol in down_limit.columns else np.nan, suspended_flag)
                    else:
                        can_trade, reason = _can_buy(price, float(up_limit.loc[dt, symbol]) if symbol in up_limit.columns else np.nan, suspended_flag)
                    current_shares = int(shares.loc[symbol])
                    target_shares = int((target_value.loc[symbol] // (price * config.lot_size)) * config.lot_size) if math.isfinite(price) and price > 0 else current_shares
                    delta = target_shares - current_shares
                    if side == "sell" and delta < 0:
                        if not can_trade:
                            day_trades.append({"instrument": symbol, "symbol": symbol, "amount": 0, "price": price, "commission": 0.0, "reason": reason})
                            continue
                        fee = max(abs(delta) * price * config.sell_cost, config.min_cost)
                        notional = abs(delta) * price
                        cash += notional - fee
                        shares.loc[symbol] += delta
                        sell_value += notional
                        commission += fee
                        row = {
                            "date": day,
                            "strategy": strategy_name,
                            "instrument": symbol,
                            "symbol": symbol,
                            "side": "sell",
                            "amount": int(delta),
                            "shares": int(delta),
                            "price": price,
                            "commission": fee,
                            "transaction_money": notional,
                            "realized_pnl": np.nan,
                        }
                        trade_rows.append(row)
                        day_trades.append(row)
                    elif side == "buy" and delta > 0:
                        if not can_trade:
                            day_trades.append({"instrument": symbol, "symbol": symbol, "amount": 0, "price": price, "commission": 0.0, "reason": reason})
                            continue
                        fee = max(delta * price * config.buy_cost, config.min_cost)
                        cost = delta * price + fee
                        if cost > cash:
                            affordable = int(max(0.0, (cash - config.min_cost)) // (price * config.lot_size)) * config.lot_size
                            delta = max(0, min(delta, affordable))
                            fee = max(delta * price * config.buy_cost, config.min_cost) if delta > 0 else 0.0
                            cost = delta * price + fee
                        if delta > 0 and cost <= cash:
                            notional = delta * price
                            cash -= cost
                            shares.loc[symbol] += delta
                            buy_value += notional
                            commission += fee
                            row = {
                                "date": day,
                                "strategy": strategy_name,
                                "instrument": symbol,
                                "symbol": symbol,
                                "side": "buy",
                                "amount": int(delta),
                                "shares": int(delta),
                                "price": price,
                                "commission": fee,
                                "transaction_money": notional,
                                "realized_pnl": np.nan,
                            }
                            trade_rows.append(row)
                            day_trades.append(row)
            pending = None

        equity_close = cash + float((shares * cl).sum())
        gross = float((shares * cl).sum() / equity_close) if equity_close > 0 else 0.0
        raw_rows.append(
            {
                "date": day,
                "portfolio_value": equity_close,
                "ending_cash": cash,
                "long_value": float((shares * cl).sum()),
                "gross_leverage": gross,
                "returns": equity_close / prev_equity - 1.0 if prev_equity > 0 else 0.0,
                "benchmark_returns": 0.0,
                "benchmark_period_return": 0.0,
                "algorithm_period_return": equity_close / config.initial_cash - 1.0,
                "commission": commission,
                "today_sum_buy_value": buy_value,
                "today_sum_sell_value": sell_value,
                "positions": _serialize_positions(shares, cl, equity_close),
                "transactions": day_trades,
            }
        )
        prev_equity = equity_close
        if dt in targets:
            pending = targets[dt]

    raw_perf = pd.DataFrame(raw_rows)
    trades = pd.DataFrame(trade_rows)
    equity = raw_perf.set_index("date")["portfolio_value"] if not raw_perf.empty else pd.Series(dtype=float)
    summary = perf_summary(equity, config.initial_cash)
    summary.update(
        {
            "engine": "local_backtest",
            "strategy": strategy_name,
            "trade_count": int(len(trades)),
            "total_fees": float(trades["commission"].sum()) if not trades.empty else 0.0,
            "active_day_ratio": float((raw_perf["gross_leverage"] > 0).mean()) if not raw_perf.empty else 0.0,
            "avg_gross_leverage": float(raw_perf["gross_leverage"].mean()) if not raw_perf.empty else 0.0,
            "turnover_on_initial_cash": float((raw_perf["today_sum_buy_value"].sum() + raw_perf["today_sum_sell_value"].sum()) / config.initial_cash) if not raw_perf.empty else 0.0,
            "config": asdict(config),
        }
    )
    return BacktestArtifacts(raw_perf=raw_perf, trades=trades, summary=summary)