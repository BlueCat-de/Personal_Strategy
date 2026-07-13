#!/usr/bin/env python3
"""Run daily Tushare data updates and local strategy checks with Feishu notifications."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import signal
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_CONDA = Path("/opt/homebrew/Caskroom/miniforge/base/bin/conda")
DEFAULT_WEBHOOK_FILE = REPO_ROOT / ".feishu_webhook"
DEFAULT_STATE_FILE = REPO_ROOT / "run/tushare_daily_daemon_state.json"
DEFAULT_PID_FILE = REPO_ROOT / "run/tushare_daily_daemon.pid"
DEFAULT_LOG_DIR = REPO_ROOT / "logs/tushare_daily"
DEFAULT_DATA_DIR = REPO_ROOT / "data/offline/a_share_12m_tushare"
DEFAULT_STRATEGY_OUTPUT_ROOT = REPO_ROOT / "data/backtests/daily_local_strategy_signals"
LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)


def now_text() -> str:
    return local_now().strftime("%Y-%m-%d %H:%M:%S")


def compact_date(value: str | datetime) -> str:
    return pd.to_datetime(value).strftime("%Y%m%d")


def iso_date(value: str | datetime) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def today_iso() -> str:
    return local_now().strftime("%Y-%m-%d")


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def append_log(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content)
        if not content.endswith("\n"):
            handle.write("\n")


def load_webhooks(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    urls = re.findall(r"https://open\.(?:feishu\.cn|larkoffice\.com)/open-apis/bot/v2/hook/[A-Za-z0-9_-]+", text)
    return sorted(set(urls))


def send_feishu(webhook_file: Path, content: str) -> None:
    urls = load_webhooks(webhook_file)
    if not urls:
        print(f"{now_text()} WARNING no Feishu webhook configured: {webhook_file}", flush=True)
        return
    payload = json.dumps({"msg_type": "text", "content": {"text": content}}, ensure_ascii=False).encode("utf-8")
    for url in urls:
        request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                _ = response.read()
                print(f"{now_text()} INFO Feishu push ok", flush=True)
        except Exception as exc:
            print(f"{now_text()} ERROR Feishu push failed: {type(exc).__name__}: {exc}", flush=True)


def pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def acquire_pid_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            old_pid = int(path.read_text(encoding="utf-8").strip())
            if pid_is_running(old_pid):
                raise SystemExit(f"Daemon already running with PID {old_pid}")
        except ValueError:
            pass
    path.write_text(str(os.getpid()), encoding="utf-8")


def remove_pid_file(path: Path) -> None:
    try:
        if path.exists() and path.read_text(encoding="utf-8").strip() == str(os.getpid()):
            path.unlink()
    except Exception:
        pass


def run_command(command: list[str], cwd: Path, log_path: Path) -> tuple[int, str]:
    append_log(log_path, f"\n[{now_text()}] RUN {' '.join(command)}\n")
    env = os.environ.copy()
    env["HOME"] = str(REPO_ROOT)
    process = subprocess.run(command, cwd=str(cwd), env=env, text=True, capture_output=True)
    output = (process.stdout or "") + (process.stderr or "")
    append_log(log_path, output)
    append_log(log_path, f"[{now_text()}] EXIT {process.returncode}\n")
    return process.returncode, output


def read_data_latest(data_dir: Path) -> str | None:
    prices_file = data_dir / "prices_long.csv"
    if not prices_file.exists():
        return None
    try:
        dates = pd.read_csv(prices_file, usecols=["date"])["date"]
    except Exception:
        return None
    if dates.empty:
        return None
    return iso_date(str(dates.max()))


def parse_list(value: object) -> list[dict]:
    text = str(value)
    if not text or text == "[]" or text == "nan":
        return []
    try:
        parsed = ast.literal_eval(text)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def summarize_strategy_output(output_dir: Path) -> dict:
    summary_path = output_dir / "local_backtest_summary.json"
    raw_perf_path = output_dir / "local_raw_perf.csv"
    result: dict = {}
    if summary_path.exists():
        summary_json = json.loads(summary_path.read_text(encoding="utf-8"))
        result.update(summary_json.get("summary", {}))
        result["strategy"] = summary_json.get("strategy")
        result["signal_rows"] = summary_json.get("signal_rows")
        result["traded_instruments"] = summary_json.get("traded_instruments")
    if raw_perf_path.exists():
        raw = pd.read_csv(raw_perf_path)
        if not raw.empty:
            last = raw.iloc[-1]
            result["final_date"] = str(last.get("date"))
            result["portfolio_value"] = float(last.get("portfolio_value", 0.0))
            result["ending_cash"] = float(last.get("ending_cash", 0.0))
            result["daily_return"] = float(last.get("returns", 0.0))
            result["positions"] = parse_list(last.get("positions"))
            result["transactions"] = parse_list(last.get("transactions"))
    return result


def format_positions(positions: list[dict]) -> str:
    if not positions:
        return "空仓"
    parts = []
    for item in positions:
        instrument = str(item.get("instrument", ""))
        amount = item.get("amount", 0)
        weight = float(item.get("hold_percent", 0.0))
        parts.append(f"{instrument} {amount}股 权重{weight:.2%}")
    return "；".join(parts)


def format_transactions(transactions: list[dict]) -> str:
    actual = [item for item in transactions if int(item.get("amount", 0) or 0) != 0]
    if not actual:
        return "无。维持当前目标持仓，或受涨跌停/停牌约束无法成交。"
    parts = []
    for item in actual:
        instrument = str(item.get("instrument", ""))
        amount = int(item.get("amount", 0))
        side = "买入" if amount > 0 else "卖出"
        price = float(item.get("price", 0.0))
        parts.append(f"{side} {instrument} {abs(amount)}股 @ {price:.2f}")
    return "；".join(parts)


def due(time_text: str) -> bool:
    return local_now().strftime("%H:%M") >= time_text


def should_run_data_update(state: dict, today: str) -> bool:
    retry_after = float(state.get("next_data_retry_after", 0.0) or 0.0)
    if (
        state.get("data_last_result_date") == today
        and state.get("data_last_status") == "skipped"
        and state.get("latest_tushare_data_date") != today
        and time.time() >= retry_after
    ):
        return True
    return state.get("data_last_result_date") != today or (
        state.get("data_last_status") in {"failed", "upstream_not_ready"} and time.time() >= retry_after
    )


def should_run_strategy(state: dict, latest_data_date: str | None, today: str) -> bool:
    data_ready = latest_data_date == today and state.get("data_last_status") != "in_progress"
    return bool(data_ready and state.get("strategy_success_data_date") != latest_data_date)


def build_conda_python(conda_path: Path, env_name: str, script: str, extra_args: list[str]) -> list[str]:
    return [str(conda_path), "run", "-n", env_name, "python", "-u", script, *extra_args]


def run_data_update(args: argparse.Namespace, state: dict) -> dict:
    today = today_iso()
    log_path = Path(args.log_dir) / f"{compact_date(today)}_data_update.log"
    state["data_last_attempt_date"] = today
    state["data_last_status"] = "in_progress"
    state["data_started_at"] = now_text()
    save_json(Path(args.state_file), state)
    command = build_conda_python(
        Path(args.conda),
        args.conda_env,
        "update_offline_a_share_tushare_daily.py",
        ["--end-date", today, "--output-dir", args.data_dir, "--env-file", args.env_file],
    )
    code, _ = run_command(command, REPO_ROOT, log_path)
    summary_path = Path(args.data_dir) / "daily_update_summary.json"
    summary = load_json(summary_path, {})
    latest = summary.get("latest_local_date") or summary.get("latest_tushare_date") or read_data_latest(Path(args.data_dir))
    if code == 0 and summary.get("status") in {"updated", "skipped"}:
        latest_tushare_date = summary.get("latest_tushare_date")
        upstream_not_ready = summary.get("status") == "skipped" and latest_tushare_date and latest_tushare_date < today
        state["data_finished_at"] = now_text()
        state["latest_tushare_data_date"] = latest
        if upstream_not_ready:
            state["data_last_status"] = "upstream_not_ready"
            state["next_data_retry_after"] = time.time() + args.retry_seconds
            save_json(Path(args.state_file), state)
            send_feishu(Path(args.webhook_file), f"Tushare 数据每日取数：等待上游更新\n运行时间：{now_text()}\n请求日期：{today}\nTushare 最新交易日：{latest_tushare_date}\n本地数据截止：{latest}")
            return summary
        state["data_last_result_date"] = today
        state["data_last_status"] = summary.get("status")
        state.pop("next_data_retry_after", None)
        save_json(Path(args.state_file), state)
        send_feishu(Path(args.webhook_file), f"Tushare 数据每日取数：成功\n运行时间：{now_text()}\n请求日期：{today}\nTushare 最新交易日：{summary.get('latest_tushare_date')}\n本地数据截止：{latest}\n状态：{summary.get('status')}")
        return summary
    state["data_last_result_date"] = today
    state["data_last_status"] = "failed"
    state["next_data_retry_after"] = time.time() + args.retry_seconds
    save_json(Path(args.state_file), state)
    send_feishu(Path(args.webhook_file), f"Tushare 数据每日取数：失败\n运行时间：{now_text()}\n请求日期：{today}\n输出目录：{args.data_dir}")
    return summary


def run_strategy(args: argparse.Namespace, state: dict, latest_data_date: str) -> dict:
    today = today_iso()
    run_tag = compact_date(latest_data_date)
    output_dir = Path(args.strategy_output_root) / run_tag
    log_path = Path(args.log_dir) / f"{run_tag}_strategy.log"
    state["strategy_last_attempt_date"] = today
    state["strategy_last_status"] = "in_progress"
    state["strategy_started_at"] = now_text()
    save_json(Path(args.state_file), state)
    command = build_conda_python(
        Path(args.conda),
        args.conda_env,
        "local_strategy.py",
        [
            "--strategy-version",
            args.strategy_version,
            "--warmup-start-date",
            args.warmup_start_date,
            "--start-date",
            args.start_date,
            "--end-date",
            latest_data_date,
            "--initial-cash",
            str(args.initial_cash),
            "--prices-file",
            str(Path(args.data_dir) / "prices_long.csv"),
            "--output-dir",
            str(output_dir),
        ],
    )
    code, _ = run_command(command, REPO_ROOT, log_path)
    result = summarize_strategy_output(output_dir)
    if code == 0 and result:
        state["strategy_last_status"] = "success"
        state["strategy_success_data_date"] = latest_data_date
        state["strategy_finished_at"] = now_text()
        save_json(Path(args.state_file), state)
        send_feishu(
            Path(args.webhook_file),
            "\n".join(
                [
                    "Tushare 本地策略：成功",
                    f"运行时间：{now_text()}",
                    f"数据日期：{latest_data_date}",
                    f"策略版本：{args.strategy_version}",
                    f"累计收益：{float(result.get('total_return', 0.0)):.2%}",
                    f"Sharpe：{float(result.get('sharpe', 0.0)):.2f}",
                    f"期末权益：{float(result.get('portfolio_value', 0.0)):.2f}",
                    f"持仓：{format_positions(result.get('positions', []))}",
                    f"次日计划：{format_transactions(result.get('transactions', []))}",
                    f"输出目录：{output_dir}",
                ]
            ),
        )
        return result
    state["strategy_last_status"] = "failed"
    state["strategy_finished_at"] = now_text()
    save_json(Path(args.state_file), state)
    send_feishu(Path(args.webhook_file), f"Tushare 本地策略：失败\n运行时间：{now_text()}\n数据日期：{latest_data_date}\n输出目录：{output_dir}")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily Tushare update and local strategy notifications.")
    parser.add_argument("--conda", default=str(DEFAULT_CONDA))
    parser.add_argument("--conda-env", default="bigquant")
    parser.add_argument("--env-file", default=str(REPO_ROOT / ".env.local"))
    parser.add_argument("--webhook-file", default=str(DEFAULT_WEBHOOK_FILE))
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--pid-file", default=str(DEFAULT_PID_FILE))
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--strategy-output-root", default=str(DEFAULT_STRATEGY_OUTPUT_ROOT))
    parser.add_argument("--data-time", default="21:10")
    parser.add_argument("--strategy-time", default="21:30")
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--retry-seconds", type=int, default=1800)
    parser.add_argument("--strategy-version", default="v4")
    parser.add_argument("--start-date", default="2025-07-05")
    parser.add_argument("--warmup-start-date", default="2025-01-07")
    parser.add_argument("--initial-cash", type=float, default=100000.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    state_file = Path(args.state_file)
    pid_file = Path(args.pid_file)
    acquire_pid_file(pid_file)
    stop = {"value": False}

    def handle_signal(signum, _frame) -> None:
        stop["value"] = True
        print(f"{now_text()} INFO received signal {signum}, stopping daemon", flush=True)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        while not stop["value"]:
            state = load_json(
                state_file,
                {
                    "data_last_status": "never",
                    "strategy_last_status": "never",
                    "data_last_result_date": None,
                    "strategy_success_data_date": None,
                },
            )
            today = today_iso()
            if due(args.data_time) and should_run_data_update(state, today):
                run_data_update(args, state)
                state = load_json(state_file, state)
            latest_data_date = read_data_latest(Path(args.data_dir))
            if due(args.strategy_time) and latest_data_date and should_run_strategy(state, latest_data_date, today):
                run_strategy(args, state, latest_data_date)
            time.sleep(args.interval_seconds)
    finally:
        remove_pid_file(pid_file)


if __name__ == "__main__":
    main()