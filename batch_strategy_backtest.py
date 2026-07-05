#!/usr/bin/env python3
"""Batch-run all generated strategy skills on an offline market dataset."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "offline" / "a_share_12m_jqdata"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "backtests" / "jqdata_12m"


@dataclass(frozen=True)
class StrategyJob:
    skill: str
    strategy_path: Path
    adapter: str
    source: str
    style: str


def is_single_symbol_job(job: StrategyJob) -> bool:
    """Return True for adapters that backtest one fixed/default symbol only."""
    adapter = job.adapter.replace("\\", "/").lower()
    if "strategies/github/general/" in adapter or "strategies/github/mean_reversion/" in adapter:
        return True

    path = REPO_ROOT / job.adapter
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    return "DEFAULT_SYMBOL =" in text and "symbols[0]" in text and "technical_signal_strategy" in text


def parse_strategy_index(index_path: Path) -> list[StrategyJob]:
    rows: list[StrategyJob] = []
    if not index_path.exists():
        raise FileNotFoundError(f"Strategy index not found: {index_path}")

    for line in index_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("| `strategy-"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 4:
            continue
        skill = cells[0].strip("`")
        source = cells[1]
        style = cells[2]
        adapter = cells[3].strip("`")
        strategy_path = REPO_ROOT / ".trae" / "skills" / skill / "strategy.py"
        rows.append(StrategyJob(skill=skill, strategy_path=strategy_path, adapter=adapter, source=source, style=style))
    return rows


def load_dataset_window(data_dir: Path) -> tuple[str, str]:
    manifest_path = data_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        config = manifest.get("config", {})
        start = str(config.get("test_start_date") or config.get("fetch_start_date") or "")
        end = str(config.get("test_end_date") or "")
        if start and end:
            return start.replace("-", ""), end.replace("-", "")

    symbols_dir = data_dir / "symbols"
    first = next(symbols_dir.glob("*.csv"))
    df = pd.read_csv(first, usecols=["date"])
    return pd.to_datetime(df["date"].min()).strftime("%Y%m%d"), pd.to_datetime(df["date"].max()).strftime("%Y%m%d")


def is_chinext(symbol: str) -> bool:
    symbol = str(symbol).zfill(6)
    return symbol.startswith(("300", "301"))


def is_star_market(symbol: str) -> bool:
    symbol = str(symbol).zfill(6)
    return symbol.startswith(("688", "689"))


def is_st_name(name: object) -> bool:
    return "ST" in str(name).upper()


def bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(float(value))
    except ValueError:
        return default


def str_env(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower()


def realistic_backtest_settings() -> dict[str, float | bool]:
    return {
        "realistic_execution": bool_env("AKSHARE_REALISTIC_BACKTEST", True),
        "long_only": bool_env("AKSHARE_LONG_ONLY", True),
        "initial_cash": float_env("AKSHARE_INITIAL_CASH", 45000.0),
        "max_gross_exposure": float_env("AKSHARE_MAX_GROSS_EXPOSURE", 1.0),
        "limit_threshold": float_env("AKSHARE_LIMIT_THRESHOLD", 0.095),
        "slippage_rate": float_env("AKSHARE_SLIPPAGE_RATE", 0.001),
        "max_participation": float_env("AKSHARE_MAX_PARTICIPATION", 0.005),
        "trade_delay_days": float_env("AKSHARE_TRADE_DELAY_DAYS", 1.0),
        "t_plus_one": bool_env("AKSHARE_T_PLUS_ONE", True),
        "lot_size": int_env("AKSHARE_LOT_SIZE", 100),
        "commission_rate": float_env("AKSHARE_COMMISSION_RATE", 0.0003),
        "min_commission": float_env("AKSHARE_MIN_COMMISSION", 5.0),
        "stamp_tax_rate": float_env("AKSHARE_STAMP_TAX_RATE", 0.001),
        "transfer_fee_rate": float_env("AKSHARE_TRANSFER_FEE_RATE", 0.00001),
        "execution_price_field": str_env("AKSHARE_EXECUTION_PRICE_FIELD", "open"),
    }


def load_universe(
    data_dir: Path,
    limit: int | None,
    exclude_chinext: bool = True,
    exclude_star: bool = True,
    exclude_st: bool = True,
) -> list[str]:
    universe_path = data_dir / "universe.csv"
    if universe_path.exists():
        universe = pd.read_csv(universe_path, dtype={"symbol": str})
        universe["symbol"] = universe["symbol"].astype(str).str.zfill(6)
        if exclude_st and "name" in universe.columns:
            universe = universe[~universe["name"].map(is_st_name)]
        symbols = universe["symbol"]
    else:
        symbols = pd.Series([path.stem for path in sorted((data_dir / "symbols").glob("*.csv"))])

    symbols = symbols.dropna().drop_duplicates()
    if exclude_chinext:
        symbols = symbols[~symbols.map(is_chinext)]
    if exclude_star:
        symbols = symbols[~symbols.map(is_star_market)]
    if limit:
        symbols = symbols.head(limit)
    return symbols.tolist()


def compute_metrics(output_dir: Path, elapsed_seconds: float) -> dict[str, Any]:
    summary_path = output_dir / "akshare_summary.json"
    equity_path = output_dir / "akshare_equity_curve.csv"
    trades_path = output_dir / "akshare_trades.csv"
    weights_path = output_dir / "akshare_target_weights.csv"

    metrics: dict[str, Any] = {
        "status": "ok",
        "elapsed_seconds": round(elapsed_seconds, 3),
        "rows": 0,
        "final_equity": np.nan,
        "total_return": np.nan,
        "annual_return": np.nan,
        "annual_volatility": np.nan,
        "sharpe": np.nan,
        "max_drawdown": np.nan,
        "win_rate": np.nan,
        "trading_days": 0,
        "trade_count": 0,
        "blocked_trade_count": 0,
        "tplus1_blocked_trade_count": 0,
        "lot_blocked_trade_count": 0,
        "total_commission": 0.0,
        "total_stamp_tax": 0.0,
        "total_transfer_fee": 0.0,
        "avg_positions": np.nan,
        "max_positions": np.nan,
    }

    if summary_path.exists():
        try:
            metrics.update(json.loads(summary_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass

    if equity_path.exists():
        equity = pd.read_csv(equity_path)
        if not equity.empty and {"equity", "return"}.issubset(equity.columns):
            returns = pd.to_numeric(equity["return"], errors="coerce").fillna(0.0)
            curve = pd.to_numeric(equity["equity"], errors="coerce")
            total_return = curve.iloc[-1] / curve.iloc[0] - 1 if len(curve) > 1 and curve.iloc[0] else np.nan
            annual_return = (1 + total_return) ** (252 / max(1, len(curve) - 1)) - 1 if pd.notna(total_return) else np.nan
            annual_vol = returns.std(ddof=0) * np.sqrt(252)
            running_max = curve.cummax()
            drawdown = curve / running_max - 1
            metrics.update(
                {
                    "rows": int(len(equity)),
                    "trading_days": int(len(equity)),
                    "final_equity": float(curve.iloc[-1]),
                    "total_return": float(total_return),
                    "annual_return": float(annual_return),
                    "annual_volatility": float(annual_vol),
                    "sharpe": float(returns.mean() / returns.std(ddof=0) * np.sqrt(252)) if returns.std(ddof=0) > 0 else np.nan,
                    "max_drawdown": float(drawdown.min()),
                    "win_rate": float((returns > 0).mean()),
                }
            )

    if trades_path.exists():
        trades = pd.read_csv(trades_path)
        blocked_threshold = 1e-4
        if "target_weight_change" in trades.columns:
            executed = pd.to_numeric(trades["target_weight_change"], errors="coerce").fillna(0.0)
            metrics["trade_count"] = int((executed.abs() > 1e-10).sum())
        else:
            metrics["trade_count"] = int(len(trades))
        if "blocked_weight_change" in trades.columns:
            blocked = pd.to_numeric(trades["blocked_weight_change"], errors="coerce").fillna(0.0)
            metrics["blocked_trade_count"] = int((blocked.abs() > blocked_threshold).sum())
        if "tplus1_blocked_weight_change" in trades.columns:
            tplus1_blocked = pd.to_numeric(trades["tplus1_blocked_weight_change"], errors="coerce").fillna(0.0)
            metrics["tplus1_blocked_trade_count"] = int((tplus1_blocked.abs() > blocked_threshold).sum())
        if "lot_blocked_weight_change" in trades.columns:
            lot_blocked = pd.to_numeric(trades["lot_blocked_weight_change"], errors="coerce").fillna(0.0)
            metrics["lot_blocked_trade_count"] = int((lot_blocked.abs() > blocked_threshold).sum())
        for column, metric in [
            ("commission", "total_commission"),
            ("stamp_tax", "total_stamp_tax"),
            ("transfer_fee", "total_transfer_fee"),
        ]:
            if column in trades.columns:
                metrics[metric] = float(pd.to_numeric(trades[column], errors="coerce").fillna(0.0).sum())

    if weights_path.exists():
        weights = pd.read_csv(weights_path)
        symbol_cols = [col for col in weights.columns if col != "date"]
        if symbol_cols:
            positions = weights[symbol_cols].abs().gt(1e-10).sum(axis=1)
            metrics["avg_positions"] = float(positions.mean())
            metrics["max_positions"] = int(positions.max())
    return metrics


def run_job(
    job: StrategyJob,
    data_dir: Path,
    output_root: Path,
    symbols_arg: str,
    start_date: str,
    end_date: str,
    timeout: int,
) -> dict[str, Any]:
    output_dir = output_root / job.skill
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = output_dir / "stdout.log"
    stderr_path = output_dir / "stderr.log"

    env = os.environ.copy()
    env["AKSHARE_OFFLINE_DATA_DIR"] = str(data_dir)
    cmd = [
        sys.executable,
        str(job.strategy_path),
        "--start-date",
        start_date,
        "--end-date",
        end_date,
        "--output-dir",
        str(output_dir),
        "--limit",
        str(len(symbols_arg.split(",")) if symbols_arg else 20),
    ]
    if symbols_arg:
        cmd[2:2] = ["--symbols", symbols_arg]

    started = time.time()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        try:
            completed = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                stdout=stdout,
                stderr=stderr,
                text=True,
                timeout=timeout,
                check=False,
            )
            elapsed = time.time() - started
            metrics = compute_metrics(output_dir, elapsed)
            if completed.returncode != 0:
                metrics["status"] = "failed"
                metrics["returncode"] = completed.returncode
            else:
                metrics["returncode"] = 0
        except subprocess.TimeoutExpired:
            elapsed = time.time() - started
            metrics = compute_metrics(output_dir, elapsed)
            metrics["status"] = "timeout"
            metrics["returncode"] = 124

    metrics.update(
        {
            "skill": job.skill,
            "source": job.source,
            "style": job.style,
            "adapter": job.adapter,
            "output_dir": str(output_dir),
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
        }
    )
    return metrics


def write_report(results: list[dict[str, Any]], output_root: Path, data_dir: Path, start_date: str, end_date: str, universe_size: int) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    results_df = pd.DataFrame(results)
    sort_cols = ["status", "total_return", "sharpe"]
    ascending = [True, False, False]
    results_df = results_df.sort_values(sort_cols, ascending=ascending, na_position="last")
    results_df.to_csv(output_root / "summary.csv", index=False, quoting=csv.QUOTE_MINIMAL)
    (output_root / "summary.json").write_text(results_df.to_json(orient="records", force_ascii=False, indent=2), encoding="utf-8")

    ok = results_df[results_df["status"] == "ok"].copy()
    failed = results_df[results_df["status"] != "ok"].copy()
    top = ok.sort_values("total_return", ascending=False).head(15)
    columns = [
        "skill",
        "source",
        "style",
        "total_return",
        "annual_return",
        "sharpe",
        "max_drawdown",
        "trade_count",
        "blocked_trade_count",
        "tplus1_blocked_trade_count",
        "avg_positions",
    ]
    lines = [
        "# Batch Strategy Backtest Report",
        "",
        f"- Offline data: `{data_dir}`",
        f"- Test window: `{start_date}` to `{end_date}`",
        f"- Universe size: `{universe_size}`",
        f"- Strategies: `{len(results_df)}`",
        f"- Succeeded: `{len(ok)}`",
        f"- Failed/timeouts: `{len(failed)}`",
        "",
        "## Top Strategies By Total Return",
        "",
    ]
    if top.empty:
        lines.append("No successful strategy runs.")
    else:
        lines.append(top[columns].to_markdown(index=False, floatfmt=".4f"))
    if not failed.empty:
        lines.extend(["", "## Failed Or Timed Out", ""])
        lines.append(failed[["skill", "status", "returncode", "stderr_log"]].to_markdown(index=False))
    (output_root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch-test all generated strategy skills on offline OHLCV data.")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Offline dataset directory.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Batch result directory.")
    parser.add_argument("--skills-index", default=str(REPO_ROOT / ".trae" / "skills" / "STRATEGY_SKILLS_INDEX.md"))
    parser.add_argument("--start-date", default="", help="YYYYMMDD. Empty uses dataset manifest test_start_date.")
    parser.add_argument("--end-date", default="", help="YYYYMMDD. Empty uses dataset manifest test_end_date.")
    parser.add_argument("--limit", type=int, default=None, help="Limit universe size. Empty uses all offline universe symbols.")
    parser.add_argument(
        "--universe-mode",
        default="adaptive",
        choices=["adaptive", "full", "defaults"],
        help=(
            "full passes the offline universe to every strategy; defaults passes no symbols; "
            "adaptive passes full universe only to cross-sectional strategies and smaller symbol sets to single/pair strategies."
        ),
    )
    parser.add_argument(
        "--include-chinext",
        action="store_true",
        help="Include ChiNext 300/301 symbols. Default excludes them.",
    )
    parser.add_argument(
        "--include-star",
        action="store_true",
        help="Include STAR Market 688/689 symbols. Default excludes them.",
    )
    parser.add_argument("--include-st", action="store_true", help="Include ST and *ST symbols. Default excludes them.")
    parser.add_argument(
        "--include-single-symbol",
        action="store_true",
        help="Include single-symbol technical timing adapters. Default excludes them from cross-strategy rankings.",
    )
    parser.add_argument("--exclude-star", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--exclude-st", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--workers", type=int, default=1, help="Parallel strategy processes.")
    parser.add_argument("--timeout", type=int, default=900, help="Timeout per strategy in seconds.")
    parser.add_argument("--strategy-filter", default="", help="Only run skills whose name contains this substring.")
    return parser.parse_args()


def choose_symbols_for_job(job: StrategyJob, symbols: list[str], universe_mode: str) -> list[str]:
    if universe_mode == "full":
        return symbols
    if universe_mode == "defaults":
        return []

    adapter = job.adapter.lower()
    skill = job.skill.lower()
    if "pairs_trading" in adapter:
        return symbols[:2]
    if "kelly" in skill:
        return symbols[:2]
    if "github/general" in adapter or "github/mean_reversion" in adapter:
        return []
    return symbols


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir).resolve()
    output_root = Path(args.output_dir).resolve()
    if not data_dir.exists():
        raise SystemExit(f"Offline data dir not found: {data_dir}")

    manifest_start, manifest_end = load_dataset_window(data_dir)
    start_date = args.start_date or manifest_start
    end_date = args.end_date or manifest_end
    symbols = load_universe(
        data_dir,
        limit=args.limit,
        exclude_chinext=not args.include_chinext,
        exclude_star=not args.include_star,
        exclude_st=not args.include_st,
    )
    if not symbols:
        raise SystemExit(f"No symbols found under {data_dir}")

    jobs = parse_strategy_index(Path(args.skills_index))
    if args.strategy_filter:
        jobs = [job for job in jobs if args.strategy_filter in job.skill]
    excluded_single_symbol_jobs: list[StrategyJob] = []
    if not args.include_single_symbol:
        excluded_single_symbol_jobs = [job for job in jobs if is_single_symbol_job(job)]
        jobs = [job for job in jobs if not is_single_symbol_job(job)]
    if not jobs:
        raise SystemExit("No strategy jobs matched.")

    print(f"Running {len(jobs)} strategies on {len(symbols)} symbols from {start_date} to {end_date}")
    output_root.mkdir(parents=True, exist_ok=True)
    if excluded_single_symbol_jobs:
        excluded_df = pd.DataFrame(
            [
                {
                    "skill": job.skill,
                    "source": job.source,
                    "style": job.style,
                    "adapter": job.adapter,
                    "reason": "single-symbol technical timing adapter",
                }
                for job in excluded_single_symbol_jobs
            ]
        )
        excluded_df.to_csv(output_root / "excluded_single_symbol_strategies.csv", index=False)
    run_config = {
        "data_dir": str(data_dir),
        "output_dir": str(output_root),
        "start_date": start_date,
        "end_date": end_date,
        "universe_size": len(symbols),
        "universe_mode": args.universe_mode,
        "exclude_chinext": not args.include_chinext,
        "exclude_star": not args.include_star,
        "exclude_st": not args.include_st,
        "exclude_single_symbol": not args.include_single_symbol,
        "excluded_single_symbol_count": len(excluded_single_symbol_jobs),
        "excluded_single_symbol_skills": [job.skill for job in excluded_single_symbol_jobs],
        "backtest_settings": realistic_backtest_settings(),
        "workers": args.workers,
        "timeout": args.timeout,
        "strategy_count": len(jobs),
    }
    (output_root / "run_config.json").write_text(json.dumps(run_config, ensure_ascii=False, indent=2), encoding="utf-8")

    results: list[dict[str, Any]] = []
    if args.workers <= 1:
        for idx, job in enumerate(jobs, 1):
            print(f"[{idx}/{len(jobs)}] {job.skill}")
            job_symbols = choose_symbols_for_job(job, symbols, args.universe_mode)
            results.append(run_job(job, data_dir, output_root, ",".join(job_symbols), start_date, end_date, args.timeout))
            write_report(results, output_root, data_dir, start_date, end_date, len(symbols))
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_job = {
                executor.submit(
                    run_job,
                    job,
                    data_dir,
                    output_root,
                    ",".join(choose_symbols_for_job(job, symbols, args.universe_mode)),
                    start_date,
                    end_date,
                    args.timeout,
                ): job
                for job in jobs
            }
            completed_count = 0
            for future in as_completed(future_to_job):
                completed_count += 1
                job = future_to_job[future]
                print(f"[{completed_count}/{len(jobs)}] {job.skill}")
                results.append(future.result())
                write_report(results, output_root, data_dir, start_date, end_date, len(symbols))

    write_report(results, output_root, data_dir, start_date, end_date, len(symbols))
    print(f"Saved batch report to {output_root / 'REPORT.md'}")
    print(f"Saved summary CSV to {output_root / 'summary.csv'}")


if __name__ == "__main__":
    main()
