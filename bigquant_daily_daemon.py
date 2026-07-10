#!/usr/bin/env python3
"""Run daily BigQuant data update and strategy checks with Feishu notifications."""

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
DEFAULT_STATE_FILE = REPO_ROOT / "run/bigquant_daily_daemon_state.json"
DEFAULT_PID_FILE = REPO_ROOT / "run/bigquant_daily_daemon.pid"
DEFAULT_LOG_DIR = REPO_ROOT / "logs/bigquant_daily"
DEFAULT_DATA_DIR = REPO_ROOT / "data/offline/a_share_12m_bigquant"
DEFAULT_STRATEGY_OUTPUT_ROOT = REPO_ROOT / "data/backtests/daily_bigquant_strategy_signals"
LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def local_now() -> datetime:
    """Return scheduler time in China Standard Time, independent of host TZ."""
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


def webhook_domain(url: str) -> str:
    return url.split("/")[2] if "/" in url else "unknown"


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
                body = response.read().decode("utf-8", "replace")
                try:
                    result = json.loads(body)
                except json.JSONDecodeError:
                    result = {"raw": body}
                code = result.get("code", result.get("StatusCode"))
                msg = result.get("msg", result.get("StatusMessage", ""))
                if code not in {0, "0"}:
                    print(f"{now_text()} ERROR Feishu push rejected domain={webhook_domain(url)} code={code} msg={msg}", flush=True)
                else:
                    print(f"{now_text()} INFO Feishu push ok domain={webhook_domain(url)}", flush=True)
        except Exception as exc:
            print(f"{now_text()} ERROR Feishu push failed domain={webhook_domain(url)}: {type(exc).__name__}: {exc}", flush=True)


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
    started = f"\n[{now_text()}] RUN {' '.join(command)}\n"
    append_log(log_path, started)
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


def parse_positions(value: object) -> list[dict]:
    text = str(value)
    if not text or text == "[]" or text == "nan":
        return []
    try:
        parsed = ast.literal_eval(text)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def parse_transactions(value: object) -> list[dict]:
    return parse_positions(value)


def summarize_strategy_output(output_dir: Path) -> dict:
    summary_path = output_dir / "bigtrader_summary.json"
    raw_perf_path = output_dir / "bigtrader_raw_perf.csv"
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
            result["positions"] = parse_positions(last.get("positions"))
            result["transactions"] = parse_transactions(last.get("transactions"))
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
    if not transactions:
        return "无。维持当前目标持仓，不主动调仓。"
    parts = []
    for item in transactions:
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
        and state.get("latest_bigquant_data_date") != today
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
        "update_offline_a_share_bigquant_daily.py",
        [
            "--end-date",
            today,
            "--output-dir",
            args.data_dir,
            "--env-file",
            args.env_file,
            "--batch-size",
            str(args.batch_size),
            "--min-start-date",
            args.data_min_start_date,
        ],
    )
    code, output = run_command(command, REPO_ROOT, log_path)
    summary_path = Path(args.data_dir) / "daily_update_summary.json"
    summary = load_json(summary_path, {})
    latest = summary.get("latest_local_date") or summary.get("latest_bigquant_date") or read_data_latest(Path(args.data_dir))
    if code == 0 and summary.get("status") in {"updated", "skipped"}:
        latest_bigquant_date = summary.get("latest_bigquant_date")
        upstream_not_ready = summary.get("status") == "skipped" and latest_bigquant_date and latest_bigquant_date < today
        state["data_finished_at"] = now_text()
        state["latest_bigquant_data_date"] = latest
        if upstream_not_ready:
            state["data_last_status"] = "upstream_not_ready"
            state["next_data_retry_after"] = time.time() + args.retry_seconds
            save_json(Path(args.state_file), state)
            message = (
                "BigQuant 数据每日取数：等待上游更新\n"
                f"运行时间：{now_text()}\n"
                f"请求日期：{today}\n"
                f"BigQuant 最新交易日：{latest_bigquant_date}\n"
                f"本地数据截止：{latest}\n"
                f"下次重试：约 {args.retry_seconds // 60} 分钟后\n"
                "说明：BigQuant 日频数据通常在每日 20:00-21:00 完成更新；当前上游尚未更新到今日，暂不运行策略。"
            )
            send_feishu(Path(args.webhook_file), message)
            return summary

        state["data_last_result_date"] = today
        state["data_last_status"] = summary.get("status")
        state.pop("next_data_retry_after", None)
        save_json(Path(args.state_file), state)
        message = (
            "BigQuant 数据每日取数：成功\n"
            f"运行时间：{now_text()}\n"
            f"请求日期：{today}\n"
            f"BigQuant 最新交易日：{summary.get('latest_bigquant_date')}\n"
            f"本地数据截止：{latest}\n"
            f"状态：{summary.get('status')}\n"
            f"说明：{summary.get('reason', '已完成增量更新')}\n"
            f"输出目录：{args.data_dir}"
        )
        send_feishu(Path(args.webhook_file), message)
        return summary

    state["data_last_status"] = "failed"
    retry_at = time.time() + args.retry_seconds
    quota_exhausted = "数据配额不足" in output or "quota" in output.lower()
    if quota_exhausted:
        state["data_last_status"] = "quota_exhausted"
        state["data_last_result_date"] = today
        retry_at = 0.0
    state["next_data_retry_after"] = retry_at
    state["data_finished_at"] = now_text()
    save_json(Path(args.state_file), state)
    tail = "\n".join(output.splitlines()[-20:])
    if quota_exhausted:
        message = (
            "BigQuant 数据每日取数：配额不足\n"
            f"运行时间：{now_text()}\n"
            f"请求日期：{today}\n"
            f"退出码：{code}\n"
            "说明：BigQuant 本周 cell 配额不足，今日不再自动重试，避免重复消耗和刷屏；待配额刷新后会在下个调度日继续执行。\n"
            f"日志：{log_path}\n"
            f"错误摘要：\n{tail[-1500:]}"
        )
        send_feishu(Path(args.webhook_file), message)
        return {"status": "quota_exhausted", "latest_local_date": latest}
    message = (
        "BigQuant 数据每日取数：失败\n"
        f"运行时间：{now_text()}\n"
        f"请求日期：{today}\n"
        f"退出码：{code}\n"
        f"下次重试：约 {args.retry_seconds // 60} 分钟后\n"
        f"日志：{log_path}\n"
        f"错误摘要：\n{tail[-1500:]}"
    )
    send_feishu(Path(args.webhook_file), message)
    return {"status": "failed", "latest_local_date": latest}


def run_strategy(args: argparse.Namespace, state: dict, data_date: str) -> dict:
    output_dir = Path(args.strategy_output_root) / compact_date(data_date)
    log_path = Path(args.log_dir) / f"{compact_date(data_date)}_strategy.log"
    state["strategy_attempt_data_date"] = data_date
    state["strategy_last_status"] = "in_progress"
    state["strategy_started_at"] = now_text()
    save_json(Path(args.state_file), state)
    command = build_conda_python(
        Path(args.conda),
        args.conda_env,
        "bigquant_strategy.py",
        [
            "--strategy-version",
            args.strategy_version,
            "--warmup-start-date",
            args.warmup_start_date,
            "--start-date",
            args.strategy_start_date,
            "--end-date",
            data_date,
            "--initial-cash",
            str(args.initial_cash),
            "--prices-file",
            str(Path(args.data_dir) / "prices_long.csv"),
            "--output-dir",
            str(output_dir),
        ],
    )
    code, output = run_command(command, REPO_ROOT, log_path)
    if code == 0:
        result = summarize_strategy_output(output_dir)
        state["strategy_success_data_date"] = data_date
        state["strategy_last_status"] = "success"
        state["strategy_finished_at"] = now_text()
        save_json(Path(args.state_file), state)
        positions = format_positions(result.get("positions", []))
        transactions = format_transactions(result.get("transactions", []))
        message = (
            "BigQuant 数据策略每日检查：成功\n"
            f"运行时间：{now_text()}\n"
            f"数据截止：{data_date}\n"
            f"策略版本：{args.strategy_version}\n"
            f"初始本金：{args.initial_cash:.2f}\n"
            f"最终权益：{float(result.get('portfolio_value', 0.0)):.2f}\n"
            f"当日收益率：{float(result.get('daily_return', 0.0)):.2%}\n"
            f"累计收益率：{float(result.get('return_ratio', 0.0)):.2f}%\n"
            f"Sharpe：{float(result.get('sharp_ratio', 0.0)):.2f}\n"
            f"最大回撤：{float(result.get('max_drawdown', 0.0)):.2f}%\n"
            f"现金：{float(result.get('ending_cash', 0.0)):.2f}\n"
            f"持仓调整：{transactions}\n"
            f"当前目标持仓：{positions}\n"
            f"输出目录：{output_dir}\n"
            "说明：该信息基于 BigQuant 数据与 BigTrader 回测口径，实盘下单前需核对账户实际持仓。"
        )
        send_feishu(Path(args.webhook_file), message)
        return result

    state["strategy_last_status"] = "failed"
    state["strategy_finished_at"] = now_text()
    save_json(Path(args.state_file), state)
    tail = "\n".join(output.splitlines()[-20:])
    message = (
        "BigQuant 数据策略每日检查：失败\n"
        f"运行时间：{now_text()}\n"
        f"数据截止：{data_date}\n"
        f"策略版本：{args.strategy_version}\n"
        f"退出码：{code}\n"
        f"日志：{log_path}\n"
        f"错误摘要：\n{tail[-1500:]}"
    )
    send_feishu(Path(args.webhook_file), message)
    return {"status": "failed"}


def recover_interrupted_state(args: argparse.Namespace) -> dict:
    state_path = Path(args.state_file)
    state = load_json(state_path, {})
    notices: list[str] = []
    if state.get("data_last_status") == "in_progress":
        notices.append(
            "检测到上次 BigQuant 取数任务处于 in_progress，可能因睡眠、关机或进程中断未写入完成状态；本次启动后会按调度重新补跑。"
        )
        state["data_last_status"] = "interrupted"
        state["data_recovered_at"] = now_text()
    if state.get("strategy_last_status") == "in_progress":
        notices.append(
            "检测到上次 BigQuant 策略任务处于 in_progress，可能因睡眠、关机或进程中断未写入完成状态；本次启动后会在数据就绪后重新补跑。"
        )
        state["strategy_last_status"] = "interrupted"
        state["strategy_recovered_at"] = now_text()
    if notices:
        save_json(state_path, state)
        send_feishu(
            Path(args.webhook_file),
            "BigQuant 自动任务故障恢复\n" + "\n".join(f"- {item}" for item in notices),
        )
    return state


def daemon_loop(args: argparse.Namespace) -> None:
    acquire_pid_file(Path(args.pid_file))
    running = True

    def stop(signum, frame) -> None:
        _ = (signum, frame)
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    recover_interrupted_state(args)
    if not args.no_startup_notify:
        send_feishu(
            Path(args.webhook_file),
            "BigQuant 自动任务已启动\n"
            f"启动时间：{now_text()}\n"
            f"取数时间：每日 {args.data_time}\n"
            f"策略时间：每日 {args.strategy_time}\n"
            f"策略版本：{args.strategy_version}\n"
            f"数据目录：{args.data_dir}",
        )
    print(f"{now_text()} BigQuant daily daemon started pid={os.getpid()}", flush=True)
    try:
        while running:
            state = load_json(Path(args.state_file), {})
            today = today_iso()
            if due(args.data_time):
                if should_run_data_update(state, today):
                    print(f"{now_text()} data update due", flush=True)
                    run_data_update(args, state)
                    state = load_json(Path(args.state_file), {})

            latest_data_date = state.get("latest_bigquant_data_date") or read_data_latest(Path(args.data_dir))
            if due(args.strategy_time) and should_run_strategy(state, latest_data_date, today):
                print(f"{now_text()} strategy run due for {latest_data_date}", flush=True)
                run_strategy(args, state, latest_data_date)
            elif due(args.strategy_time) and state.get("data_last_status") == "in_progress":
                print(f"{now_text()} strategy waiting: BigQuant data update still in progress", flush=True)

            time.sleep(args.interval_seconds)
    finally:
        remove_pid_file(Path(args.pid_file))
        print(f"{now_text()} BigQuant daily daemon stopped", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Schedule BigQuant daily data update and strategy runs.")
    parser.add_argument("--conda", default=str(DEFAULT_CONDA))
    parser.add_argument("--conda-env", default="bigquant")
    parser.add_argument("--data-time", default="21:10")
    parser.add_argument("--strategy-time", default="21:30")
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--retry-seconds", type=int, default=1800)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--data-min-start-date", default=os.environ.get("BIGQUANT_DATA_MIN_START_DATE", "2024-01-01"))
    parser.add_argument("--strategy-output-root", default=str(DEFAULT_STRATEGY_OUTPUT_ROOT))
    parser.add_argument("--strategy-version", default=os.environ.get("BIGQUANT_STRATEGY_VERSION", "v4"))
    parser.add_argument("--strategy-start-date", default=os.environ.get("BIGQUANT_STRATEGY_START_DATE", "2025-07-05"))
    parser.add_argument("--warmup-start-date", default=os.environ.get("BIGQUANT_WARMUP_START_DATE", "2025-01-07"))
    parser.add_argument("--initial-cash", type=float, default=float(os.environ.get("BIGQUANT_INITIAL_CASH", "100000")))
    parser.add_argument("--env-file", default=str(REPO_ROOT / ".env.local"))
    parser.add_argument("--webhook-file", default=str(DEFAULT_WEBHOOK_FILE))
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--pid-file", default=str(DEFAULT_PID_FILE))
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    parser.add_argument("--no-startup-notify", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    daemon_loop(args)


if __name__ == "__main__":
    main()
