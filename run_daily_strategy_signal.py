#!/usr/bin/env python3
"""Run the latest small-account strategy after daily data update.

This script is intended to run after ``update_offline_a_share_daily.py``.
It only runs the strategy when the offline dataset has been updated to the
target date, then sends a Feishu summary with current holdings and any executed
position changes in the strategy backtest output.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

import pandas as pd

from update_offline_a_share_daily import load_feishu_webhooks, send_to_feishu_all


LOGGER = logging.getLogger("daily_strategy_signal")
REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = REPO_ROOT / "data/offline/a_share_12m_tencent_sina"
DEFAULT_OUTPUT_BASE = REPO_ROOT / "data/backtests/daily_strategy_signals"
DEFAULT_STRATEGY = REPO_ROOT / "strategies/ai_native/small_account_high_conviction_policy.py"
DEFAULT_WEBHOOK_FILE = REPO_ROOT / ".feishu_webhook"
DEFAULT_LIVE_STATE_FILE = REPO_ROOT / "run/daily_strategy_live_state.json"


def yyyymmdd(value: pd.Timestamp) -> str:
    return value.strftime("%Y%m%d")


def setup_logging(level: str, log_file: str | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_path = Path(log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


@contextmanager
def exclusive_lock(lock_file: Path) -> Iterator[bool]:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_file.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid(), "started_at": datetime.now().isoformat(timespec="seconds")}))
        handle.flush()
        yield True
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def latest_dataset_date(data_dir: Path) -> str | None:
    prices_path = data_dir / "prices_long.csv"
    if not prices_path.exists():
        return None
    dates = pd.read_csv(prices_path, usecols=["date"], dtype={"date": str})
    if dates.empty:
        return None
    latest = pd.to_datetime(dates["date"], errors="coerce").max()
    if pd.isna(latest):
        return None
    return yyyymmdd(latest)


def normalize_target_date(value: str | None) -> str:
    if value:
        return yyyymmdd(pd.to_datetime(value))
    return yyyymmdd(pd.Timestamp.today())


def run_strategy(args: argparse.Namespace, target_date: str, output_dir: Path) -> subprocess.CompletedProcess[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(Path(args.strategy_path)),
        "--warmup-start-date",
        args.warmup_start_date,
        "--start-date",
        args.start_date,
        "--end-date",
        target_date,
        "--output-dir",
        str(output_dir),
        "--limit",
        str(args.limit),
    ]
    env = os.environ.copy()
    env["AKSHARE_OFFLINE_DATA_DIR"] = str(Path(args.data_dir))
    env["AKSHARE_INITIAL_CASH"] = str(args.initial_cash)
    LOGGER.info("Run strategy command: %s", " ".join(command))
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=args.timeout,
        check=False,
    )


def read_symbol_names(data_dir: Path) -> dict[str, str]:
    path = data_dir / "universe.csv"
    if not path.exists():
        return {}
    universe = pd.read_csv(path, dtype={"symbol": str})
    if "symbol" not in universe.columns:
        return {}
    name_col = "name" if "name" in universe.columns else None
    if not name_col:
        return {str(s).zfill(6): "" for s in universe["symbol"]}
    return {str(row["symbol"]).zfill(6): str(row.get(name_col, "")) for _, row in universe.iterrows()}


def nonzero_holdings(weights: pd.DataFrame, names: dict[str, str], threshold: float) -> list[dict]:
    if weights.empty:
        return []
    last = weights.iloc[-1]
    date = str(last["date"])
    result: list[dict] = []
    for symbol, value in last.drop(labels=["date"]).items():
        weight = float(value)
        if abs(weight) <= threshold:
            continue
        symbol = str(symbol).zfill(6)
        result.append({"date": date, "symbol": symbol, "name": names.get(symbol, ""), "weight": weight})
    return sorted(result, key=lambda item: abs(item["weight"]), reverse=True)


def latest_trade_actions(trades: pd.DataFrame, target_date: str, names: dict[str, str]) -> list[dict]:
    if trades.empty or "date" not in trades.columns:
        return []
    trades = trades.copy()
    trades["date"] = pd.to_datetime(trades["date"], errors="coerce").dt.strftime("%Y%m%d")
    latest = trades[trades["date"] == target_date].copy()
    if latest.empty:
        return []
    latest["executed_shares"] = pd.to_numeric(latest.get("executed_shares", 0), errors="coerce").fillna(0).astype(int)
    latest["trade_notional"] = pd.to_numeric(latest.get("trade_notional", 0.0), errors="coerce").fillna(0.0)
    latest["requested_weight_change"] = pd.to_numeric(latest.get("requested_weight_change", 0.0), errors="coerce").fillna(0.0)
    latest = latest[(latest["executed_shares"] != 0) | (latest["requested_weight_change"].abs() > 1e-4)]
    actions: list[dict] = []
    for _, row in latest.iterrows():
        symbol = str(row["symbol"]).zfill(6)
        shares = int(row["executed_shares"])
        side = "买入" if shares > 0 else "卖出" if shares < 0 else "未成交"
        actions.append(
            {
                "date": row["date"],
                "symbol": symbol,
                "name": names.get(symbol, ""),
                "side": side,
                "shares": shares,
                "notional": float(row["trade_notional"]),
                "requested_weight_change": float(row["requested_weight_change"]),
            }
        )
    return actions


def analyze_strategy_output(output_dir: Path, data_dir: Path, target_date: str, initial_cash: float) -> dict:
    equity_path = output_dir / "akshare_equity_curve.csv"
    weights_path = output_dir / "akshare_target_weights.csv"
    trades_path = output_dir / "akshare_trades.csv"
    for path in [equity_path, weights_path, trades_path]:
        if not path.exists():
            raise FileNotFoundError(f"Missing strategy output: {path}")

    names = read_symbol_names(data_dir)
    equity = pd.read_csv(equity_path)
    weights = pd.read_csv(weights_path)
    trades = pd.read_csv(trades_path, dtype={"symbol": str}) if trades_path.stat().st_size > 0 else pd.DataFrame()
    holdings = nonzero_holdings(weights, names, threshold=1e-4)
    actions = latest_trade_actions(trades, target_date, names)
    equity_values = pd.to_numeric(equity["equity"], errors="coerce") if not equity.empty else pd.Series(dtype=float)
    final_equity = float(equity_values.iloc[-1]) if not equity_values.empty else 0.0
    previous_equity = float(equity_values.iloc[-2]) if len(equity_values) >= 2 else initial_cash
    daily_return = final_equity / previous_equity - 1.0 if previous_equity else 0.0
    cash = float(pd.to_numeric(equity.get("cash", pd.Series([0.0])), errors="coerce").iloc[-1]) if not equity.empty else 0.0
    total_return = final_equity / initial_cash - 1.0 if final_equity else 0.0
    return {
        "status": "success",
        "target_date": target_date,
        "output_dir": str(output_dir),
        "initial_cash": initial_cash,
        "final_equity": final_equity,
        "cash": cash,
        "daily_return": daily_return,
        "total_return": total_return,
        "backtest_final_equity": final_equity,
        "backtest_cash": cash,
        "backtest_daily_return": daily_return,
        "backtest_total_return": total_return,
        "holdings": holdings,
        "actions": actions,
        "adjustment_required": bool(actions),
    }


def load_live_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Failed to read live state %s: %s", path, exc)
        return {}


def write_live_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_live_account_state(summary: dict, args: argparse.Namespace, latest_data_date: str | None) -> dict:
    """Report PnL from the live tracking start date, not from historical backtest start."""
    state_path = Path(args.live_state_file)
    state = {} if args.reset_live_state else load_live_state(state_path)
    target_date = str(summary["target_date"])
    initial_cash = float(args.initial_cash)

    if not state:
        state = {
            "baseline_date": target_date,
            "initial_cash": initial_cash,
            "current_equity": initial_cash,
            "last_target_date": target_date,
            "last_backtest_equity": summary.get("backtest_final_equity"),
            "latest_data_date": latest_data_date,
        }
        live_daily_return = 0.0
    else:
        current_equity = float(state.get("current_equity", initial_cash))
        last_target_date = str(state.get("last_target_date", ""))
        if target_date > last_target_date:
            live_daily_return = float(summary.get("backtest_daily_return", 0.0))
            current_equity *= 1.0 + live_daily_return
            state["current_equity"] = current_equity
            state["last_target_date"] = target_date
            state["last_backtest_equity"] = summary.get("backtest_final_equity")
            state["latest_data_date"] = latest_data_date
        else:
            live_daily_return = 0.0

    state["initial_cash"] = initial_cash
    state.setdefault("baseline_date", target_date)
    state.setdefault("current_equity", initial_cash)
    write_live_state(state_path, state)

    live_equity = float(state.get("current_equity", initial_cash))
    summary["reporting_mode"] = "live_state"
    summary["live_baseline_date"] = state.get("baseline_date", target_date)
    summary["live_state_file"] = str(state_path)
    summary["final_equity"] = live_equity
    summary["cash"] = live_equity if not summary.get("holdings") else float(summary.get("backtest_cash", live_equity))
    summary["daily_return"] = live_daily_return
    summary["total_return"] = live_equity / initial_cash - 1.0 if initial_cash else 0.0
    return summary


def build_feishu_message(summary: dict) -> str:
    status = summary.get("status", "unknown")
    title_map = {
        "success": "策略每日检查：成功",
        "failed": "策略每日检查：失败",
        "skipped_no_today_data": "策略每日检查：跳过，今日数据未更新",
        "skipped_locked": "策略每日检查：跳过，已有任务运行",
    }
    lines = [title_map.get(status, f"策略每日检查：{status}")]
    lines.append(f"日期：{summary.get('target_date', '-')}")

    if status == "skipped_no_today_data":
        lines.append(f"数据最新日期：{summary.get('latest_data_date', '-')}")
        lines.append("结论：不运行策略，避免基于旧数据生成交易信号。")
        return "\n".join(lines)
    if status == "failed":
        lines.append(f"错误：{summary.get('error', '-')}")
        return "\n".join(lines)
    if status == "skipped_locked":
        lines.append("结论：检测到已有策略任务运行，本次跳过。")
        return "\n".join(lines)

    lines.extend(
        [
            f"策略：small_account_high_conviction_policy v4",
            f"统计口径：从{summary.get('live_baseline_date', summary.get('target_date', '-'))}开始的实盘模拟跟踪",
            f"初始本金：{float(summary.get('initial_cash', 0.0)):.2f}",
            f"最终权益：{float(summary.get('final_equity', 0.0)):.2f}",
            f"当日收益率：{float(summary.get('daily_return', 0.0)):.2%}",
            f"累计收益率：{float(summary.get('total_return', 0.0)):.2%}",
            f"现金：{float(summary.get('cash', 0.0)):.2f}",
        ]
    )
    actions = summary.get("actions") or []
    if actions:
        lines.append("持仓调整：")
        for item in actions[:8]:
            name = f" {item['name']}" if item.get("name") and item.get("name") != "nan" else ""
            lines.append(
                f"- {item['side']} {item['symbol']}{name} {abs(int(item['shares']))}股，约{float(item['notional']):.2f}元"
            )
    else:
        lines.append("持仓调整：无。维持当前目标持仓，不主动调仓。")

    holdings = summary.get("holdings") or []
    if holdings:
        lines.append("当前目标持仓：")
        for item in holdings[:5]:
            name = f" {item['name']}" if item.get("name") and item.get("name") != "nan" else ""
            lines.append(f"- {item['symbol']}{name}：{float(item['weight']):.2%}")
    else:
        lines.append("当前目标持仓：空仓。")
    lines.append(f"输出目录：{summary.get('output_dir', '-')}")
    lines.append("说明：持仓信号来自本地回测撮合，收益从实盘模拟启用日重新计数；实盘下单前需核对账户实际持仓。")
    return "\n".join(lines)


def send_summary(args: argparse.Namespace, summary: dict) -> None:
    if not args.feishu_notify:
        return
    if args.dry_run and not args.feishu_notify_dry_run:
        LOGGER.info("Skip Feishu notification for dry-run.")
        return
    webhooks = load_feishu_webhooks(args.feishu_webhook_file)
    if webhooks:
        send_to_feishu_all(webhooks, build_feishu_message(summary))


def write_summary(output_dir: Path, summary: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "daily_strategy_signal.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "daily_strategy_signal.txt").write_text(build_feishu_message(summary), encoding="utf-8")


def run_once(args: argparse.Namespace) -> dict:
    target_date = normalize_target_date(args.end_date)
    data_dir = Path(args.data_dir)
    latest_data_date = latest_dataset_date(data_dir)
    output_dir = Path(args.output_base_dir) / target_date

    if latest_data_date != target_date and not args.allow_stale_data:
        summary = {
            "status": "skipped_no_today_data",
            "target_date": target_date,
            "latest_data_date": latest_data_date,
            "output_dir": str(output_dir),
        }
        write_summary(output_dir, summary)
        return summary

    if args.dry_run:
        summary = {
            "status": "success",
            "target_date": target_date,
            "latest_data_date": latest_data_date,
            "output_dir": str(output_dir),
            "final_equity": 0.0,
            "cash": 0.0,
            "initial_cash": args.initial_cash,
            "daily_return": 0.0,
            "total_return": 0.0,
            "holdings": [],
            "actions": [],
            "adjustment_required": False,
            "dry_run": True,
        }
        write_summary(output_dir, summary)
        return summary

    completed = run_strategy(args, target_date, output_dir)
    (output_dir / "strategy_stdout.log").write_text(completed.stdout or "", encoding="utf-8")
    (output_dir / "strategy_stderr.log").write_text(completed.stderr or "", encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"Strategy command failed with code {completed.returncode}; see {output_dir}")

    summary = analyze_strategy_output(output_dir, data_dir, target_date, args.initial_cash)
    summary = apply_live_account_state(summary, args, latest_data_date)
    summary["latest_data_date"] = latest_data_date
    write_summary(output_dir, summary)
    return summary


def seconds_until(run_at: str) -> float:
    now = pd.Timestamp.now()
    hour, minute = [int(part) for part in run_at.split(":", 1)]
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += pd.Timedelta(days=1)
    return float((target - now).total_seconds())


def should_run_today(run_at: str, last_run_date: str | None, now: pd.Timestamp | None = None) -> bool:
    now = now or pd.Timestamp.now()
    hour, minute = [int(part) for part in run_at.split(":", 1)]
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    today = yyyymmdd(now.normalize())
    return now >= target and last_run_date != today


def run_daemon(args: argparse.Namespace) -> None:
    LOGGER.info("Daily strategy daemon started; run_at=%s", args.run_at)
    last_run_date: str | None = None
    while True:
        now = pd.Timestamp.now()
        if should_run_today(args.run_at, last_run_date, now):
            args.end_date = yyyymmdd(now.normalize())
            try:
                summary = run_once(args)
                LOGGER.info("Strategy summary: %s", summary)
                send_summary(args, summary)
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Daily strategy run failed: %s", exc)
                target_date = normalize_target_date(args.end_date)
                summary = {"status": "failed", "target_date": target_date, "error": str(exc)}
                send_summary(args, summary)
            finally:
                last_run_date = args.end_date
            time.sleep(60)
            continue

        sleep_seconds = min(seconds_until(args.run_at), 300.0)
        LOGGER.info("Next strategy check in %.0f seconds; run_at=%s", sleep_seconds, args.run_at)
        time.sleep(max(60.0, sleep_seconds))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily small-account strategy signal after data update.")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--output-base-dir", default=str(DEFAULT_OUTPUT_BASE))
    parser.add_argument("--strategy-path", default=str(DEFAULT_STRATEGY))
    parser.add_argument("--warmup-start-date", default="20250106")
    parser.add_argument("--start-date", default="20250705")
    parser.add_argument("--end-date", help="YYYYMMDD. Defaults to today.")
    parser.add_argument("--limit", type=int, default=3046)
    parser.add_argument("--initial-cash", type=float, default=100000.0, help="Assumed live account capital for daily PnL reporting.")
    parser.add_argument("--live-state-file", default=str(DEFAULT_LIVE_STATE_FILE), help="Local state file for live PnL reporting.")
    parser.add_argument("--reset-live-state", action="store_true", help="Reset live PnL baseline to the current target date.")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--allow-stale-data", action="store_true", help="Run even if prices_long.csv is not updated to target date.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--run-at", default="16:50", help="Daemon run time in HH:MM.")
    parser.add_argument("--lock-file", default=str(REPO_ROOT / "run/daily_strategy_signal.lock"))
    parser.add_argument("--log-file", default=str(REPO_ROOT / "logs/daily_strategy_signal.log"))
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--feishu-notify", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--feishu-notify-dry-run", action="store_true")
    parser.add_argument("--feishu-webhook-file", default=str(DEFAULT_WEBHOOK_FILE))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level, args.log_file)
    with exclusive_lock(Path(args.lock_file)) as acquired:
        if not acquired:
            summary = {"status": "skipped_locked", "target_date": normalize_target_date(args.end_date)}
            LOGGER.warning("Another daily strategy task is running; skip.")
            send_summary(args, summary)
            return
        if args.daemon:
            run_daemon(args)
        else:
            try:
                summary = run_once(args)
                LOGGER.info("Strategy summary: %s", summary)
                send_summary(args, summary)
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Daily strategy run failed: %s", exc)
                summary = {"status": "failed", "target_date": normalize_target_date(args.end_date), "error": str(exc)}
                send_summary(args, summary)
                sys.exit(1)


if __name__ == "__main__":
    main()
