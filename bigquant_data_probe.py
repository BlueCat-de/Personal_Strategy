#!/usr/bin/env python3
"""Probe BigQuant DAI data and compare it with the local offline dataset."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from bigquant_provider import fetch_bigquant_daily_history_batch, init_bigquant, normalize_symbol


LOGGER = logging.getLogger("bigquant_data_probe")
DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data/offline/a_share_12m_tencent_sina"
DEFAULT_ENV_FILE = Path(__file__).resolve().parent / ".env.local"


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )


def load_local_prices(data_dir: Path, symbols: list[str], start_date: str, end_date: str) -> pd.DataFrame:
    path = data_dir / "prices_long.csv"
    if not path.exists():
        return pd.DataFrame()
    local = pd.read_csv(path, dtype={"symbol": str, "date": str})
    local["symbol"] = local["symbol"].map(normalize_symbol)
    local["trade_date"] = pd.to_datetime(local["date"], errors="coerce").dt.strftime("%Y%m%d")
    return local[
        local["symbol"].isin(symbols)
        & (local["trade_date"] >= start_date)
        & (local["trade_date"] <= end_date)
    ].copy()


def compare_prices(bigquant_df: pd.DataFrame, local_df: pd.DataFrame) -> pd.DataFrame:
    if bigquant_df.empty or local_df.empty:
        return pd.DataFrame()

    local_cols = ["trade_date", "symbol", "open", "high", "low", "close", "volume", "amount", "turnover"]
    local = local_df[[col for col in local_cols if col in local_df.columns]].copy()
    local = local.rename(columns={col: f"{col}_local" for col in local.columns if col not in {"trade_date", "symbol"}})

    bq = bigquant_df.rename(
        columns={
            "open": "open_bigquant",
            "high": "high_bigquant",
            "low": "low_bigquant",
            "close": "close_bigquant",
            "volume": "volume_bigquant",
            "amount": "amount_bigquant",
            "turnover_rate": "turnover_bigquant",
        }
    )
    merged = bq.merge(local, on=["trade_date", "symbol"], how="inner")
    for col in ["open", "high", "low", "close", "volume", "amount", "turnover"]:
        left = f"{col}_bigquant"
        right = f"{col}_local"
        if left in merged.columns and right in merged.columns:
            merged[f"{col}_diff"] = pd.to_numeric(merged[left], errors="coerce") - pd.to_numeric(
                merged[right], errors="coerce"
            )
            denom = pd.to_numeric(merged[right], errors="coerce").replace(0, pd.NA)
            merged[f"{col}_diff_pct"] = merged[f"{col}_diff"] / denom
    return merged


def summarize_comparison(merged: pd.DataFrame) -> dict:
    if merged.empty:
        return {"matched_rows": 0}
    summary: dict[str, float | int] = {"matched_rows": int(len(merged))}
    for col in ["open", "high", "low", "close", "volume", "amount", "turnover"]:
        diff_col = f"{col}_diff_pct"
        if diff_col not in merged.columns:
            continue
        values = pd.to_numeric(merged[diff_col], errors="coerce").dropna().abs()
        if values.empty:
            continue
        summary[f"{col}_median_abs_diff_pct"] = float(values.median())
        summary[f"{col}_max_abs_diff_pct"] = float(values.max())
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe BigQuant DAI OHLCV data.")
    parser.add_argument("--symbols", default="000001,600519", help="Comma-separated 6-digit stock symbols.")
    parser.add_argument("--start-date", default="2026-07-01", help="YYYY-MM-DD or YYYYMMDD.")
    parser.add_argument("--end-date", default="2026-07-06", help="YYYY-MM-DD or YYYYMMDD.")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Local offline dataset for comparison.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Local env file containing BIGQUANT_API_KEY.")
    parser.add_argument("--output-dir", default="data/bigquant_probe", help="Output directory for probe CSV files.")
    parser.add_argument("--adjust", default="qfq", choices=["raw", "qfq", "hfq"], help="Price adjustment exported by the adapter.")
    parser.add_argument("--volume-unit", default="hand", choices=["hand", "share"], help="Volume unit exported by the adapter.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def normalize_date(value: str) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)
    symbols = [normalize_symbol(item) for item in args.symbols.split(",") if item.strip()]
    start_date = normalize_date(args.start_date)
    end_date = normalize_date(args.end_date)
    start_compact = pd.to_datetime(start_date).strftime("%Y%m%d")
    end_compact = pd.to_datetime(end_date).strftime("%Y%m%d")

    init_bigquant(Path(args.env_file))
    try:
        bigquant_df = fetch_bigquant_daily_history_batch(
            symbols,
            start_date=start_date,
            end_date=end_date,
            adjust=args.adjust,
            volume_unit=args.volume_unit,
        )
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        if "请先申请SDK使用权限" in message or "SDK使用权限" in message:
            print("BigQuant probe failed: current account has no SDK data permission.")
            print("Action: apply for BigQuant SDK permission, then rerun this probe.")
            raise SystemExit(2) from exc
        print(f"BigQuant probe failed: {type(exc).__name__}: {message}")
        raise SystemExit(1) from exc
    local_df = load_local_prices(Path(args.data_dir), symbols, start_compact, end_compact)
    merged = compare_prices(bigquant_df, local_df)
    summary = summarize_comparison(merged)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    bigquant_df.to_csv(output_dir / "bigquant_prices.csv", index=False)
    local_df.to_csv(output_dir / "local_prices.csv", index=False)
    merged.to_csv(output_dir / "comparison.csv", index=False)

    print("BigQuant rows:", len(bigquant_df))
    print("Local rows:", len(local_df))
    print("Matched rows:", summary.get("matched_rows", 0))
    for key, value in summary.items():
        if key == "matched_rows":
            continue
        print(f"{key}: {value:.6g}")
    print("Output:", output_dir.resolve())


if __name__ == "__main__":
    main()
