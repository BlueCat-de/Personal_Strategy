#!/usr/bin/env python3
"""Local A-share backtest engine with next-open execution constraints."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import StrEnum

import numpy as np
import pandas as pd


class ExecutionMode(StrEnum):
    """Public execution interface for supported transaction schedules."""

    SAME_OPEN = "same_open"
    SELL_OPEN_BUY_NEXT_OPEN = "sell_open_buy_next_open"
    SELL_OPEN_BUY_CLOSE = "sell_open_buy_close"

    @classmethod
    def coerce(cls, value: "ExecutionMode | str") -> "ExecutionMode":
        try:
            return value if isinstance(value, cls) else cls(value)
        except ValueError as exc:
            choices = ", ".join(mode.value for mode in cls)
            raise ValueError(
                f"unsupported execution mode {value!r}; choose from {choices}"
            ) from exc


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 100_000.0
    buy_cost: float = 0.0003
    sell_cost: float = 0.0013
    slippage: float = 0.0
    min_cost: float = 5.0
    lot_size: int = 100
    benchmark_symbol: str = "000300.SH"
    account_for_corporate_actions: bool = True
    execution_mode: ExecutionMode | str = ExecutionMode.SAME_OPEN
    max_participation_rate: float | None = None
    liquidity_lookback_days: int = 20


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
    if config.max_participation_rate is not None:
        return _run_local_backtest_with_liquidity(
            prices,
            targets,
            config,
            strategy_name=strategy_name,
        )
    if config.slippage < 0:
        raise ValueError("slippage must be non-negative")
    execution_mode = ExecutionMode.coerce(config.execution_mode)
    close = prices.get("raw_close", prices["close"])
    open_ = prices.get("raw_open", prices["open"]).reindex_like(close)
    adj_factor = prices.get(
        "adj_factor", pd.DataFrame(1.0, index=close.index, columns=close.columns)
    ).reindex_like(close)
    up_limit = prices.get(
        "up_limit", pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    ).reindex_like(close)
    down_limit = prices.get(
        "down_limit", pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    ).reindex_like(close)
    suspended = prices.get(
        "is_suspended", pd.DataFrame(0, index=close.index, columns=close.columns)
    )
    dates = list(close.index)
    symbols = list(close.columns)
    cash = config.initial_cash
    shares = pd.Series(0, index=symbols, dtype=int)
    pending: pd.Series | None = None
    pending_buy: pd.Series | None = None
    raw_rows: list[dict] = []
    trade_rows: list[dict] = []
    prev_equity = config.initial_cash
    mark_prices = close.ffill().iloc[0].reindex(symbols).fillna(0.0)
    previous_adj_factor = adj_factor.ffill().iloc[0].reindex(symbols).fillna(1.0)

    for dt in dates:
        day = dt.strftime("%Y-%m-%d")
        day_trades: list[dict] = []
        op = open_.loc[dt]
        cl = close.loc[dt]
        opening_shares = shares.copy()
        opening_prices = op.where(op.notna() & (op > 0), mark_prices).fillna(0.0)
        current_adj_factor = adj_factor.loc[dt].fillna(previous_adj_factor)
        adjustment_ratio = (
            (current_adj_factor / previous_adj_factor.replace(0.0, np.nan))
            .replace([np.inf, -np.inf], np.nan)
            .fillna(1.0)
        )
        corporate_action_adjustment = 0.0
        if config.account_for_corporate_actions:
            corporate_action_adjustment = float(
                (opening_shares * opening_prices * (adjustment_ratio - 1.0)).sum()
            )
            cash += corporate_action_adjustment
        previous_adj_factor = current_adj_factor
        equity_open = cash + float((shares * opening_prices).sum())
        buy_value = 0.0
        sell_value = 0.0
        commission = 0.0
        slippage_cost = 0.0
        close_order: pd.Series | None = None

        orders: list[tuple[pd.Series, tuple[str, ...]]] = []
        if execution_mode == ExecutionMode.SAME_OPEN:
            if pending is not None:
                orders.append((pending, ("sell", "buy")))
                pending = None
        elif execution_mode == ExecutionMode.SELL_OPEN_BUY_NEXT_OPEN:
            if pending_buy is not None:
                orders.append((pending_buy, ("buy",)))
                pending_buy = None
            if pending is not None:
                orders.append((pending, ("sell",)))
                pending_buy = pending
                pending = None
        else:
            if pending is not None:
                orders.append((pending, ("sell",)))
                close_order = pending
                pending = None

        for order, sides in orders:
            target_value = order.reindex(symbols).fillna(0.0) * equity_open
            trade_symbols = sorted(
                set(target_value[target_value > 0].index) | set(shares[shares > 0].index)
            )
            for side in sides:
                for symbol in trade_symbols:
                    reference_price = float(op.get(symbol, np.nan))
                    suspended_flag = (
                        bool(pd.to_numeric(suspended.loc[dt, symbol], errors="coerce"))
                        if symbol in suspended.columns
                        else False
                    )
                    if side == "sell":
                        price = reference_price * (1.0 - config.slippage)
                        can_trade, reason = _can_sell(
                            price,
                            float(down_limit.loc[dt, symbol])
                            if symbol in down_limit.columns
                            else np.nan,
                            suspended_flag,
                        )
                    else:
                        price = reference_price * (1.0 + config.slippage)
                        can_trade, reason = _can_buy(
                            price,
                            float(up_limit.loc[dt, symbol])
                            if symbol in up_limit.columns
                            else np.nan,
                            suspended_flag,
                        )
                    current_shares = int(shares.loc[symbol])
                    target_shares = (
                        int(
                            (target_value.loc[symbol] // (price * config.lot_size))
                            * config.lot_size
                        )
                        if math.isfinite(price) and price > 0
                        else current_shares
                    )
                    delta = target_shares - current_shares
                    if side == "sell" and delta < 0:
                        if not can_trade:
                            day_trades.append(
                                {
                                    "instrument": symbol,
                                    "symbol": symbol,
                                    "amount": 0,
                                    "price": price,
                                    "commission": 0.0,
                                    "reason": reason,
                                }
                            )
                            continue
                        fee = max(abs(delta) * price * config.sell_cost, config.min_cost)
                        notional = abs(delta) * price
                        cash += notional - fee
                        shares.loc[symbol] += delta
                        sell_value += notional
                        commission += fee
                        trade_slippage = abs(delta) * (reference_price - price)
                        slippage_cost += trade_slippage
                        row = {
                            "date": day,
                            "strategy": strategy_name,
                            "instrument": symbol,
                            "symbol": symbol,
                            "side": "sell",
                            "amount": int(delta),
                            "shares": int(delta),
                            "price": price,
                            "reference_price": reference_price,
                            "slippage_cost": trade_slippage,
                            "commission": fee,
                            "transaction_money": notional,
                            "realized_pnl": np.nan,
                        }
                        trade_rows.append(row)
                        day_trades.append(row)
                    elif side == "buy" and delta > 0:
                        if not can_trade:
                            day_trades.append(
                                {
                                    "instrument": symbol,
                                    "symbol": symbol,
                                    "amount": 0,
                                    "price": price,
                                    "commission": 0.0,
                                    "reason": reason,
                                }
                            )
                            continue
                        fee = max(delta * price * config.buy_cost, config.min_cost)
                        cost = delta * price + fee
                        if cost > cash:
                            affordable = (
                                int(max(0.0, (cash - config.min_cost)) // (price * config.lot_size))
                                * config.lot_size
                            )
                            delta = max(0, min(delta, affordable))
                            fee = (
                                max(delta * price * config.buy_cost, config.min_cost)
                                if delta > 0
                                else 0.0
                            )
                            cost = delta * price + fee
                        if delta > 0 and cost <= cash:
                            notional = delta * price
                            cash -= cost
                            shares.loc[symbol] += delta
                            buy_value += notional
                            commission += fee
                            trade_slippage = delta * (price - reference_price)
                            slippage_cost += trade_slippage
                            row = {
                                "date": day,
                                "strategy": strategy_name,
                                "instrument": symbol,
                                "symbol": symbol,
                                "side": "buy",
                                "amount": int(delta),
                                "shares": int(delta),
                                "price": price,
                                "reference_price": reference_price,
                                "slippage_cost": trade_slippage,
                                "commission": fee,
                                "transaction_money": notional,
                                "realized_pnl": np.nan,
                            }
                            trade_rows.append(row)
                            day_trades.append(row)
        mark_prices = mark_prices.where(cl.isna(), cl).fillna(0.0)
        equity_close = cash + float((shares * mark_prices).sum())
        if execution_mode == ExecutionMode.SELL_OPEN_BUY_CLOSE and close_order is not None:
            # Size the closing-auction order at the open. The closing price is
            # unknown when the order quantity is submitted and only determines
            # the eventual fill and any cash-limited partial execution.
            target_value = close_order.reindex(symbols).fillna(0.0) * equity_open
            trade_symbols = sorted(
                set(target_value[target_value > 0].index) | set(shares[shares > 0].index)
            )
            for symbol in trade_symbols:
                reference_price = float(cl.get(symbol, np.nan))
                suspended_flag = (
                    bool(pd.to_numeric(suspended.loc[dt, symbol], errors="coerce"))
                    if symbol in suspended.columns
                    else False
                )
                price = reference_price * (1.0 + config.slippage)
                can_trade, reason = _can_buy(
                    price,
                    float(up_limit.loc[dt, symbol]) if symbol in up_limit.columns else np.nan,
                    suspended_flag,
                )
                sizing_price = float(op.get(symbol, np.nan))
                current_shares = int(shares.loc[symbol])
                target_shares = (
                    int(
                        (target_value.loc[symbol] // (sizing_price * config.lot_size))
                        * config.lot_size
                    )
                    if math.isfinite(sizing_price) and sizing_price > 0
                    else current_shares
                )
                delta = target_shares - current_shares
                if delta <= 0:
                    continue
                if not can_trade:
                    day_trades.append(
                        {
                            "instrument": symbol,
                            "symbol": symbol,
                            "amount": 0,
                            "price": price,
                            "commission": 0.0,
                            "reason": reason,
                        }
                    )
                    continue
                fee = max(delta * price * config.buy_cost, config.min_cost)
                cost = delta * price + fee
                if cost > cash:
                    affordable = (
                        int(max(0.0, (cash - config.min_cost)) // (price * config.lot_size))
                        * config.lot_size
                    )
                    delta = max(0, min(delta, affordable))
                    fee = (
                        max(delta * price * config.buy_cost, config.min_cost) if delta > 0 else 0.0
                    )
                    cost = delta * price + fee
                if delta > 0 and cost <= cash:
                    notional = delta * price
                    cash -= cost
                    shares.loc[symbol] += delta
                    buy_value += notional
                    commission += fee
                    trade_slippage = delta * (price - reference_price)
                    slippage_cost += trade_slippage
                    row = {
                        "date": day,
                        "strategy": strategy_name,
                        "instrument": symbol,
                        "symbol": symbol,
                        "side": "buy",
                        "amount": int(delta),
                        "shares": int(delta),
                        "price": price,
                        "reference_price": reference_price,
                        "slippage_cost": trade_slippage,
                        "commission": fee,
                        "transaction_money": notional,
                        "realized_pnl": np.nan,
                    }
                    trade_rows.append(row)
                    day_trades.append(row)
            equity_close = cash + float((shares * mark_prices).sum())
        gross = float((shares * mark_prices).sum() / equity_close) if equity_close > 0 else 0.0
        raw_rows.append(
            {
                "date": day,
                "portfolio_value": equity_close,
                "ending_cash": cash,
                "long_value": float((shares * mark_prices).sum()),
                "gross_leverage": gross,
                "returns": equity_close / prev_equity - 1.0 if prev_equity > 0 else 0.0,
                "benchmark_returns": 0.0,
                "benchmark_period_return": 0.0,
                "algorithm_period_return": equity_close / config.initial_cash - 1.0,
                "commission": commission,
                "slippage_cost": slippage_cost,
                "corporate_action_adjustment": corporate_action_adjustment,
                "today_sum_buy_value": buy_value,
                "today_sum_sell_value": sell_value,
                "positions": _serialize_positions(shares, mark_prices, equity_close),
                "transactions": day_trades,
            }
        )
        prev_equity = equity_close
        if dt in targets:
            pending = targets[dt]

    raw_perf = pd.DataFrame(raw_rows)
    trades = pd.DataFrame(trade_rows)
    equity = (
        raw_perf.set_index("date")["portfolio_value"]
        if not raw_perf.empty
        else pd.Series(dtype=float)
    )
    summary = perf_summary(equity, config.initial_cash)
    summary.update(
        {
            "engine": "local_backtest",
            "strategy": strategy_name,
            "trade_count": int(len(trades)),
            "total_fees": float(trades["commission"].sum()) if not trades.empty else 0.0,
            "total_slippage_cost": float(trades["slippage_cost"].sum())
            if not trades.empty
            else 0.0,
            "active_day_ratio": float((raw_perf["gross_leverage"] > 0).mean())
            if not raw_perf.empty
            else 0.0,
            "avg_gross_leverage": float(raw_perf["gross_leverage"].mean())
            if not raw_perf.empty
            else 0.0,
            "turnover_on_initial_cash": float(
                (raw_perf["today_sum_buy_value"].sum() + raw_perf["today_sum_sell_value"].sum())
                / config.initial_cash
            )
            if not raw_perf.empty
            else 0.0,
            "config": asdict(config),
        }
    )
    return BacktestArtifacts(raw_perf=raw_perf, trades=trades, summary=summary)


def _run_local_backtest_with_liquidity(
    prices: dict[str, pd.DataFrame],
    targets: dict[pd.Timestamp, pd.Series],
    config: BacktestConfig,
    *,
    strategy_name: str,
) -> BacktestArtifacts:
    """Run a constrained local backtest with persistent, volume-capped orders."""

    if config.slippage < 0:
        raise ValueError("slippage must be non-negative")
    if config.max_participation_rate is not None and not (0 < config.max_participation_rate <= 1):
        raise ValueError("max_participation_rate must be in (0, 1]")
    if config.liquidity_lookback_days < 1:
        raise ValueError("liquidity_lookback_days must be positive")

    execution_mode = ExecutionMode.coerce(config.execution_mode)
    close = prices.get("raw_close", prices["close"])
    open_ = prices.get("raw_open", prices["open"]).reindex_like(close)
    volume = prices.get(
        "volume", pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    ).reindex_like(close)
    estimated_volume_hands = (
        volume.shift(1)
        .rolling(
            config.liquidity_lookback_days,
            min_periods=1,
        )
        .median()
    )
    adj_factor = prices.get(
        "adj_factor", pd.DataFrame(1.0, index=close.index, columns=close.columns)
    ).reindex_like(close)
    up_limit = prices.get(
        "up_limit", pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    ).reindex_like(close)
    down_limit = prices.get(
        "down_limit", pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    ).reindex_like(close)
    suspended = prices.get(
        "is_suspended", pd.DataFrame(0, index=close.index, columns=close.columns)
    ).reindex_like(close)
    dates = list(close.index)
    symbols = list(close.columns)
    cash = config.initial_cash
    shares = pd.Series(0, index=symbols, dtype=int)
    pending_target: pd.Series | None = None
    pending_buy_target: pd.Series | None = None
    open_orders: dict[tuple[str, str], int] = {}
    close_orders: dict[tuple[str, str], int] = {}
    raw_rows: list[dict] = []
    trade_rows: list[dict] = []
    prev_equity = config.initial_cash
    mark_prices = close.ffill().iloc[0].reindex(symbols).fillna(0.0)
    previous_adj_factor = adj_factor.ffill().iloc[0].reindex(symbols).fillna(1.0)
    observed_participation: list[float] = []

    for dt in dates:
        day = dt.strftime("%Y-%m-%d")
        op = open_.loc[dt]
        cl = close.loc[dt]
        day_trades: list[dict] = []
        opening_shares = shares.copy()
        opening_prices = op.where(op.notna() & (op > 0), mark_prices).fillna(0.0)
        current_adj_factor = adj_factor.loc[dt].fillna(previous_adj_factor)
        adjustment_ratio = (
            (current_adj_factor / previous_adj_factor.replace(0.0, np.nan))
            .replace([np.inf, -np.inf], np.nan)
            .fillna(1.0)
        )
        corporate_action_adjustment = 0.0
        if config.account_for_corporate_actions:
            corporate_action_adjustment = float(
                (opening_shares * opening_prices * (adjustment_ratio - 1.0)).sum()
            )
            cash += corporate_action_adjustment
        previous_adj_factor = current_adj_factor
        equity_open = cash + float((shares * opening_prices).sum())
        buy_value = 0.0
        sell_value = 0.0
        commission = 0.0
        slippage_cost = 0.0
        participation_used: dict[str, int] = {}

        def queue_target(
            queue: dict[tuple[str, str], int],
            target: pd.Series,
            sides: tuple[str, ...],
            *,
            sizing_prices: pd.Series,
        ) -> None:
            target_value = target.reindex(symbols).fillna(0.0) * equity_open
            trade_symbols = sorted(
                set(target_value[target_value > 0].index) | set(shares[shares > 0].index)
            )
            for symbol in trade_symbols:
                sizing_price = float(sizing_prices.get(symbol, np.nan))
                current_shares = int(shares.loc[symbol])
                target_shares = (
                    int(
                        (target_value.loc[symbol] // (sizing_price * config.lot_size))
                        * config.lot_size
                    )
                    if math.isfinite(sizing_price) and sizing_price > 0
                    else current_shares
                )
                delta = target_shares - current_shares
                if delta < 0 and "sell" in sides:
                    queue[("sell", symbol)] = abs(delta)
                elif delta > 0 and "buy" in sides:
                    queue[("buy", symbol)] = delta

        def execute_queue(
            queue: dict[tuple[str, str], int],
            *,
            reference_prices: pd.Series,
            phase: str,
        ) -> None:
            nonlocal buy_value, sell_value, commission, slippage_cost, cash

            for side in ("sell", "buy"):
                for order_side, symbol in sorted(list(queue)):
                    if order_side != side:
                        continue
                    requested = queue[(order_side, symbol)]
                    reference_price = float(reference_prices.get(symbol, np.nan))
                    suspended_flag = bool(pd.to_numeric(suspended.loc[dt, symbol], errors="coerce"))
                    if side == "sell":
                        price = reference_price * (1.0 - config.slippage)
                        can_trade, reason = _can_sell(
                            price, float(down_limit.loc[dt, symbol]), suspended_flag
                        )
                    else:
                        price = reference_price * (1.0 + config.slippage)
                        can_trade, reason = _can_buy(
                            price, float(up_limit.loc[dt, symbol]), suspended_flag
                        )
                    if not can_trade:
                        day_trades.append(
                            {
                                "instrument": symbol,
                                "symbol": symbol,
                                "amount": 0,
                                "price": price,
                                "commission": 0.0,
                                "reason": reason,
                            }
                        )
                        continue

                    liquidity_cap = requested
                    liquidity_estimate_hands = float(estimated_volume_hands.loc[dt, symbol])
                    if config.max_participation_rate is not None:
                        liquidity_cap = (
                            int(
                                (
                                    max(0.0, liquidity_estimate_hands)
                                    * 100
                                    * config.max_participation_rate
                                )
                                // config.lot_size
                            )
                            * config.lot_size
                            if math.isfinite(liquidity_estimate_hands)
                            else 0
                        )
                    available = max(0, liquidity_cap - participation_used.get(symbol, 0))
                    amount = min(requested, available)
                    if amount <= 0:
                        day_trades.append(
                            {
                                "instrument": symbol,
                                "symbol": symbol,
                                "amount": 0,
                                "price": price,
                                "commission": 0.0,
                                "reason": "participation_cap",
                            }
                        )
                        continue
                    if side == "buy":
                        affordable = (
                            int(max(0.0, cash - config.min_cost) // (price * config.lot_size))
                            * config.lot_size
                        )
                        amount = min(amount, affordable)
                        if amount <= 0:
                            day_trades.append(
                                {
                                    "instrument": symbol,
                                    "symbol": symbol,
                                    "amount": 0,
                                    "price": price,
                                    "commission": 0.0,
                                    "reason": "insufficient_cash",
                                }
                            )
                            continue
                    fee_rate = config.sell_cost if side == "sell" else config.buy_cost
                    fee = max(amount * price * fee_rate, config.min_cost)
                    if side == "buy" and amount * price + fee > cash:
                        continue
                    notional = amount * price
                    signed_amount = -amount if side == "sell" else amount
                    if side == "sell":
                        cash += notional - fee
                        shares.loc[symbol] -= amount
                        sell_value += notional
                        trade_slippage = amount * (reference_price - price)
                    else:
                        cash -= notional + fee
                        shares.loc[symbol] += amount
                        buy_value += notional
                        trade_slippage = amount * (price - reference_price)
                    commission += fee
                    slippage_cost += trade_slippage
                    participation_used[symbol] = participation_used.get(symbol, 0) + amount
                    daily_hands = float(volume.loc[dt, symbol])
                    if math.isfinite(daily_hands) and daily_hands > 0:
                        observed_participation.append(amount / (daily_hands * 100))
                    remaining = requested - amount
                    if remaining:
                        queue[(order_side, symbol)] = remaining
                    else:
                        del queue[(order_side, symbol)]
                    row = {
                        "date": day,
                        "strategy": strategy_name,
                        "instrument": symbol,
                        "symbol": symbol,
                        "side": side,
                        "amount": signed_amount,
                        "shares": signed_amount,
                        "price": price,
                        "reference_price": reference_price,
                        "slippage_cost": trade_slippage,
                        "commission": fee,
                        "transaction_money": notional,
                        "realized_pnl": np.nan,
                        "requested_amount": requested,
                        "liquidity_cap_shares": liquidity_cap,
                        "liquidity_estimate_hands": liquidity_estimate_hands,
                        "liquidity_estimate_as_of": (
                            dates[dates.index(dt) - 1].strftime("%Y-%m-%d")
                            if dates.index(dt) > 0
                            else None
                        ),
                        "remaining_amount": remaining,
                        "execution_phase": phase,
                    }
                    trade_rows.append(row)
                    day_trades.append(row)

        if execution_mode == ExecutionMode.SAME_OPEN and pending_target is not None:
            open_orders.clear()
            close_orders.clear()
            queue_target(open_orders, pending_target, ("sell", "buy"), sizing_prices=op)
            pending_target = None
        elif execution_mode == ExecutionMode.SELL_OPEN_BUY_NEXT_OPEN:
            if pending_target is not None:
                open_orders.clear()
                close_orders.clear()
                queue_target(open_orders, pending_target, ("sell",), sizing_prices=op)
                pending_buy_target = pending_target
                pending_target = None
            elif pending_buy_target is not None:
                queue_target(open_orders, pending_buy_target, ("buy",), sizing_prices=op)
                pending_buy_target = None
        elif execution_mode == ExecutionMode.SELL_OPEN_BUY_CLOSE and pending_target is not None:
            open_orders.clear()
            close_orders.clear()
            queue_target(open_orders, pending_target, ("sell",), sizing_prices=op)

        execute_queue(open_orders, reference_prices=op, phase="open")
        mark_prices = mark_prices.where(cl.isna(), cl).fillna(0.0)

        if execution_mode == ExecutionMode.SELL_OPEN_BUY_CLOSE and pending_target is not None:
            queue_target(close_orders, pending_target, ("buy",), sizing_prices=op)
            pending_target = None
        execute_queue(close_orders, reference_prices=cl, phase="close")

        equity_close = cash + float((shares * mark_prices).sum())
        gross = float((shares * mark_prices).sum() / equity_close) if equity_close > 0 else 0.0
        raw_rows.append(
            {
                "date": day,
                "portfolio_value": equity_close,
                "ending_cash": cash,
                "long_value": float((shares * mark_prices).sum()),
                "gross_leverage": gross,
                "returns": equity_close / prev_equity - 1.0 if prev_equity > 0 else 0.0,
                "benchmark_returns": 0.0,
                "benchmark_period_return": 0.0,
                "algorithm_period_return": equity_close / config.initial_cash - 1.0,
                "commission": commission,
                "slippage_cost": slippage_cost,
                "corporate_action_adjustment": corporate_action_adjustment,
                "today_sum_buy_value": buy_value,
                "today_sum_sell_value": sell_value,
                "pending_order_shares": sum(open_orders.values()) + sum(close_orders.values()),
                "positions": _serialize_positions(shares, mark_prices, equity_close),
                "transactions": day_trades,
            }
        )
        prev_equity = equity_close
        if dt in targets:
            pending_target = targets[dt]

    raw_perf = pd.DataFrame(raw_rows)
    trades = pd.DataFrame(trade_rows)
    equity = (
        raw_perf.set_index("date")["portfolio_value"]
        if not raw_perf.empty
        else pd.Series(dtype=float)
    )
    summary = perf_summary(equity, config.initial_cash)
    summary.update(
        {
            "engine": "local_backtest",
            "strategy": strategy_name,
            "trade_count": int(len(trades)),
            "total_fees": float(trades["commission"].sum()) if not trades.empty else 0.0,
            "total_slippage_cost": float(trades["slippage_cost"].sum())
            if not trades.empty
            else 0.0,
            "active_day_ratio": float((raw_perf["gross_leverage"] > 0).mean())
            if not raw_perf.empty
            else 0.0,
            "avg_gross_leverage": float(raw_perf["gross_leverage"].mean())
            if not raw_perf.empty
            else 0.0,
            "turnover_on_initial_cash": float(
                (raw_perf["today_sum_buy_value"].sum() + raw_perf["today_sum_sell_value"].sum())
                / config.initial_cash
            )
            if not raw_perf.empty
            else 0.0,
            "max_observed_participation_rate": max(observed_participation, default=0.0),
            "unfilled_order_shares": int(sum(open_orders.values()) + sum(close_orders.values())),
            "config": asdict(config),
        }
    )
    return BacktestArtifacts(raw_perf=raw_perf, trades=trades, summary=summary)
