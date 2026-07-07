#!/usr/bin/env python3
"""Compare BigTrader backtest output with the previous local runtime output."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pandas as pd


BIGTRADER_DIR = Path("data/backtests/bigquant_strategy_v4_fixed/20260706")
OLD_RUNTIME_DIR = Path("data/backtests/small_account_bigquant_probe/20260706")
REPORT_PATH = Path("BIGTRADER_RUNTIME_DIFF.md")


def parse_list(value: object) -> list[dict]:
    if value is None or pd.isna(value):
        return []
    text = str(value)
    if text == "[]":
        return []
    parsed = ast.literal_eval(text)
    return parsed if isinstance(parsed, list) else []


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    return float(drawdown.min())


def load_bigtrader_transactions(raw_perf: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for _, row in raw_perf.iterrows():
        for item in parse_list(row["transactions"]):
            rows.append(
                {
                    "date": str(row["date"]),
                    "instrument": item.get("instrument"),
                    "symbol": str(item.get("instrument", "")).split(".")[0].zfill(6),
                    "amount": int(item.get("amount", 0)),
                    "price": float(item.get("price", 0.0)),
                    "notional": abs(float(item.get("transaction_money", 0.0))),
                    "commission": float(item.get("commission", 0.0)),
                    "realized_pnl": float(item.get("realized_pnl", 0.0)),
                    "side": "buy" if int(item.get("amount", 0)) > 0 else "sell",
                }
            )
    return pd.DataFrame(rows)


def load_bigtrader_positions(raw_perf: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for _, row in raw_perf.iterrows():
        for item in parse_list(row["positions"]):
            amount = int(item.get("amount", 0))
            if amount <= 0:
                continue
            rows.append(
                {
                    "date": str(row["date"]),
                    "symbol": str(item.get("instrument", "")).split(".")[0].zfill(6),
                    "weight": float(item.get("hold_percent", 0.0)),
                    "amount": amount,
                    "market_value": float(item.get("market_value", 0.0)),
                }
            )
    return pd.DataFrame(rows)


def old_runtime_trades(path: Path) -> pd.DataFrame:
    trades = pd.read_csv(path)
    trades["date"] = pd.to_datetime(trades["date"]).dt.strftime("%Y-%m-%d")
    trades["symbol"] = trades["symbol"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    trades["side"] = trades["executed_shares"].map(lambda x: "buy" if x > 0 else "sell" if x < 0 else "blocked")
    trades["fee"] = trades[["commission", "stamp_tax", "transfer_fee"]].sum(axis=1)
    return trades


def old_runtime_weights(path: Path) -> pd.DataFrame:
    weights = pd.read_csv(path)
    weights["date"] = pd.to_datetime(weights["date"]).dt.strftime("%Y-%m-%d")
    long = weights.melt(id_vars=["date"], var_name="symbol", value_name="weight")
    long["symbol"] = long["symbol"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    return long[long["weight"].abs() > 1e-8].copy()


def summarize() -> str:
    bt_summary = json.loads((BIGTRADER_DIR / "bigtrader_summary.json").read_text(encoding="utf-8"))
    bt_signals = pd.read_csv(BIGTRADER_DIR / "bigquant_weight_signals.csv")
    bt_raw = pd.read_csv(BIGTRADER_DIR / "bigtrader_raw_perf.csv")
    bt_tx = load_bigtrader_transactions(bt_raw)
    bt_pos = load_bigtrader_positions(bt_raw)

    old_summary = json.loads((OLD_RUNTIME_DIR / "akshare_summary.json").read_text(encoding="utf-8"))
    old_equity = pd.read_csv(OLD_RUNTIME_DIR / "akshare_equity_curve.csv")
    old_trades = old_runtime_trades(OLD_RUNTIME_DIR / "akshare_trades.csv")
    old_weights = old_runtime_weights(OLD_RUNTIME_DIR / "akshare_target_weights.csv")

    bt_final = float(bt_raw["portfolio_value"].iloc[-1])
    bt_return = bt_summary["summary"]["return_ratio"]
    old_final = float(old_summary["final_equity"])
    old_return = float(old_summary["total_return"]) * 100
    old_mdd = abs(max_drawdown(pd.to_numeric(old_equity["equity"], errors="coerce")) * 100)

    bt_signal_dates = set(bt_signals.loc[bt_signals["instrument"].notna(), "date"])
    old_trade_dates = set(old_trades.loc[old_trades["executed_shares"] != 0, "date"])
    common_trade_dates = bt_signal_dates & old_trade_dates

    bt_symbols = set(bt_tx["symbol"]) if not bt_tx.empty else set()
    old_symbols = set(old_trades.loc[old_trades["executed_shares"] != 0, "symbol"])
    common_symbols = bt_symbols & old_symbols
    bt_position_days = int(bt_pos["date"].nunique()) if not bt_pos.empty else 0
    old_position_days = int(old_weights["date"].nunique()) if not old_weights.empty else 0

    first_bt = bt_tx.head(10).to_markdown(index=False) if not bt_tx.empty else "无"
    first_old = old_trades.loc[old_trades["executed_shares"] != 0].head(10)[
        ["date", "symbol", "side", "executed_shares", "trade_notional", "commission", "stamp_tax", "transfer_fee"]
    ].to_markdown(index=False)

    report = f"""# BigTrader 与旧 Runtime 回测 Diff

## 结论

本次差异的最大来源已经定位：BigTrader 的 `HandleDataLib.handle_data_weight_based` 需要每日有明确语义。

- 事件日必须提供完整目标持仓快照，而不是只提供变化量。
- 非调仓日必须提供 `instrument=None` 的哨兵行，否则 helper 会把当天空信号解释为空目标仓位，并卖出所有持仓。
- 修复后，BigTrader 回测从旧的 `+2.93%` 回到 `+21.15%`，与旧 runtime 在同一 BigQuant 数据上的 `+20.02%` 已经接近。

## 结果对比

| 项目 | BigTrader 修复后 | 旧本地 runtime |
| --- | ---: | ---: |
| 最终权益 | {bt_final:,.2f} | {old_final:,.2f} |
| 累计收益率 | {bt_return:.2f}% | {old_return:.2f}% |
| 最大回撤 | {bt_summary['summary']['max_drawdown']:.2f}% | {old_mdd:.2f}% |
| 交易次数 | {len(bt_tx)} | {int((old_trades['executed_shares'] != 0).sum())} |
| 交易标的数 | {len(bt_symbols)} | {len(old_symbols)} |
| 有持仓交易日数 | {bt_position_days} | {old_position_days} |
| 总费用 | {bt_tx['commission'].sum() if not bt_tx.empty else 0.0:,.2f} | {old_trades['fee'].sum():,.2f} |

## 信号差异

| 项目 | 数值 |
| --- | ---: |
| BigTrader 输入总行数 | {len(bt_signals)} |
| BigTrader 非调仓哨兵行 | {int(bt_signals['instrument'].isna().sum())} |
| BigTrader 事件目标行 | {int(bt_signals['instrument'].notna().sum())} |
| BigTrader 事件日期数 | {len(bt_signal_dates)} |
| 旧 runtime 实际成交日期数 | {len(old_trade_dates)} |
| 共同交易日期数 | {len(common_trade_dates)} |
| 共同交易标的数 | {len(common_symbols)} |

旧 runtime 输出的 `akshare_target_weights.csv` 是执行后的实际权重矩阵，不是原始目标信号；因此这里主要比较 BigTrader 事件信号、BigTrader 实际成交和旧 runtime 实际成交。

## BigTrader 成交样例

{first_bt}

## 旧 Runtime 成交样例

{first_old}

## 源码层差异

### BigTrader

BigTrader helper 的逻辑是：

1. 取当天信号：`df_today = context.data[context.data["date"] == data.current_dt.strftime("%Y-%m-%d")]`
2. 如果当天恰好一行且 `instrument is None`，认为是非调仓日，直接 return。
3. 否则先卖出所有不在 `df_today["instrument"]` 里的现有持仓。
4. 再对 `df_today` 里的每个标的调用 `context.order_target_percent(instrument, weight)`。

所以 BigTrader 输入必须是“事件日完整目标快照 + 非事件日哨兵”，不能是旧 runtime 那种稀疏变化量。

### 旧 Runtime

旧 runtime 的核心逻辑是：

1. `weights.reindex(close.index).ffill().fillna(0.0)`，非调仓日自动延续上一个目标权重。
2. `executable_weights = weights.shift(trade_delay_days)`，默认延迟 1 个交易日执行。
3. 自己按开盘价、T+1、100 股整数手、手续费、印花税、过户费、滑点和涨跌停限制逐日撮合。

## 差异来源拆解

- 信号：迁移版信号目前已经接近 v4，但不完全等价；例如旧 runtime 首批成交出现 `600777`，迁移版首批 BigTrader 目标是 `600057` 和 `603137`。这说明当前 BigQuant-only 版仍存在细节口径差异。
- 执行日：两者都是信号后下一交易日开盘附近执行。BigTrader 订单行会出现在信号日，成交行出现在下一交易日。
- 成交价：BigTrader 使用 `order_price_field_buy/sell="open"`，旧 runtime 使用执行价字段 `open` 并额外加入滑点。
- 手续费：BigTrader 当前配置 `buy_cost=0.0003, sell_cost=0.0013, min_cost=5.0`；旧 runtime 拆分佣金、印花税、过户费，并且买卖都有 `transfer_fee`。
- T+1：BigTrader 通过 `context.set_stock_t1(1)`；旧 runtime 显式维护 `sellable_shares`。
- 分红：BigTrader 自动使用 dividend data；旧 runtime 没有同等的分红现金/送股处理。
- 持仓延续：这是已修复的主差异。修复前 BigTrader 在无信号日清仓；修复后非调仓日用哨兵维持持仓。

## 下一步

剩余差异已经从“回测引擎误用”缩小到“策略信号细节和撮合口径差异”。若要继续追平，需要逐条对齐：

1. v4 原始候选池和当前 BigQuant-only 候选池。
2. 每个周频调仓日的 top score 排名。
3. 旧 runtime 的滑点、过户费、涨跌停限制在 BigTrader 中的等价实现。
4. 是否关闭或显式控制 BigTrader 分红处理，以便和旧 runtime 做纯撮合对比。
"""
    return report


def main() -> None:
    REPORT_PATH.write_text(summarize(), encoding="utf-8")
    print(f"Wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
