#!/usr/bin/env python3
"""Cache point-in-time Shenwan level-one industry membership from Tushare."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

from ashare_quant.data.tushare import get_pro_client
from ashare_quant.paths import DEFAULT_ENV_FILE, DEFAULT_MARKET_DATA_DIR
from ashare_quant.research.factors import atomic_write_csv


DEFAULT_CACHE_DIR = DEFAULT_MARKET_DATA_DIR / ".sw_l1_member_cache"
DEFAULT_OUTPUT = DEFAULT_MARKET_DATA_DIR / "sw_l1_membership_history.csv"
FIELDS = "l1_code,l1_name,ts_code,name,in_date,out_date,is_new"


def fetch_with_retry(call, retries: int = 3) -> pd.DataFrame:
    last_error: BaseException | None = None
    for attempt in range(retries):
        try:
            result = call()
            return result if isinstance(result, pd.DataFrame) else pd.DataFrame()
        except Exception as error:
            last_error = error
            if attempt + 1 < retries:
                time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"Tushare industry request failed: {last_error}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch PIT Shenwan L1 membership.")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    pro = get_pro_client(DEFAULT_ENV_FILE)
    classify_path = args.cache_dir / "classify.csv"
    if classify_path.exists():
        industries = pd.read_csv(classify_path, dtype=str)
    else:
        industries = fetch_with_retry(
            lambda: pro.index_classify(
                level="L1",
                src="SW2021",
                fields="index_code,industry_name,level,industry_code,src",
            )
        )
        atomic_write_csv(industries, classify_path)

    frames: list[pd.DataFrame] = []
    historical_path = args.cache_dir / "historical.csv"
    if historical_path.exists():
        historical = pd.read_csv(historical_path, dtype=str)
    else:
        historical = fetch_with_retry(lambda: pro.index_member_all(is_new="N", fields=FIELDS))
        atomic_write_csv(historical, historical_path)
    frames.append(historical)

    for number, row in enumerate(industries.itertuples(index=False), start=1):
        code = str(row.index_code)
        path = args.cache_dir / f"{code.replace('.', '_')}_current.csv"
        if path.exists():
            current = pd.read_csv(path, dtype=str)
        else:
            current = fetch_with_retry(
                lambda code=code: pro.index_member_all(
                    l1_code=code,
                    is_new="Y",
                    fields=FIELDS,
                )
            )
            atomic_write_csv(current, path)
        frames.append(current)
        print(f"{number}/{len(industries)} {code} current={len(current)}", flush=True)

    result = pd.concat(frames, ignore_index=True)
    result = result.drop_duplicates(["l1_code", "ts_code", "in_date", "out_date", "is_new"])
    result["symbol"] = result["ts_code"].str.split(".").str[0].str.zfill(6)
    result = result.sort_values(["symbol", "in_date", "out_date"], na_position="last")
    atomic_write_csv(result, args.output)
    print(
        {
            "rows": len(result),
            "symbols": int(result["symbol"].nunique()),
            "industries": int(result["l1_code"].nunique()),
            "output": str(args.output.resolve()),
        },
        flush=True,
    )


if __name__ == "__main__":
    main()
