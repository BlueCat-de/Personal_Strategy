#!/usr/bin/env python3
"""JQData adapter used by the local A-share research pipeline."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

import pandas as pd


_AUTHENTICATED = False


def load_env_file(path: Path | None = None) -> None:
    """Load simple KEY=VALUE pairs from .env.local without adding python-dotenv."""
    env_path = path or Path(__file__).resolve().parent / ".env.local"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def import_jqdatasdk():
    try:
        import jqdatasdk as jq  # type: ignore
    except ImportError as exc:
        raise SystemExit("Missing dependency: install with `pip install jqdatasdk`.") from exc
    return jq


def auth_jqdata():
    """Authenticate once per process using JQDATA_USERNAME/JQDATA_PASSWORD."""
    global _AUTHENTICATED
    jq = import_jqdatasdk()
    if _AUTHENTICATED:
        return jq

    load_env_file()
    username = os.getenv("JQDATA_USERNAME")
    password = os.getenv("JQDATA_PASSWORD")
    if not username or not password:
        raise RuntimeError("Missing JQDATA_USERNAME/JQDATA_PASSWORD. Put them in .env.local or export them.")

    jq.auth(username, password)
    _AUTHENTICATED = True
    return jq


def normalize_symbol(symbol: str) -> str:
    symbol = str(symbol).strip().upper()
    if "." in symbol:
        symbol = symbol.split(".")[0]
    return symbol.zfill(6) if symbol.isdigit() and len(symbol) <= 6 else symbol


def to_jq_symbol(symbol: str) -> str:
    """Convert 6-digit A-share code to JoinQuant security code."""
    symbol = normalize_symbol(symbol)
    if symbol.endswith((".XSHG", ".XSHE", ".XBEI")):
        return symbol
    if symbol.startswith(("6", "9")):
        return f"{symbol}.XSHG"
    if symbol.startswith(("0", "2", "3")):
        return f"{symbol}.XSHE"
    if symbol.startswith(("4", "8")):
        return f"{symbol}.XBEI"
    return symbol


def from_jq_symbol(symbol: str) -> str:
    return normalize_symbol(symbol)


def to_jq_date(value: str) -> str:
    value = str(value).strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def to_jq_fq(adjust: str) -> str:
    if adjust == "qfq":
        return "pre"
    if adjust == "hfq":
        return "post"
    return "none"


def fetch_jq_daily_history(symbol: str, start_date: str, end_date: str, adjust: str = "qfq") -> pd.DataFrame:
    """Fetch one A-share daily OHLCV series from JQData and normalize fields."""
    jq = auth_jqdata()
    plain_symbol = normalize_symbol(symbol)
    jq_symbol = to_jq_symbol(plain_symbol)
    raw = jq.get_price(
        jq_symbol,
        start_date=to_jq_date(start_date),
        end_date=to_jq_date(end_date),
        frequency="daily",
        fields=["open", "close", "low", "high", "volume", "money", "pre_close", "paused"],
        fq=to_jq_fq(adjust),
        skip_paused=False,
        round=True,
        panel=False,
    )
    if raw is None or raw.empty:
        return pd.DataFrame()

    df = raw.reset_index().rename(columns={"index": "trade_date", "money": "amount"})
    first_col = df.columns[0]
    if first_col not in {"trade_date", "date"}:
        df = df.rename(columns={first_col: "trade_date"})
    if "time" in df.columns and "trade_date" not in df.columns:
        df = df.rename(columns={"time": "trade_date"})

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["symbol"] = plain_symbol
    df["data_source"] = "jqdata"

    for col in ["open", "close", "high", "low", "volume", "amount", "pre_close", "paused"]:
        if col not in df.columns:
            df[col] = pd.NA
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["change"] = df["close"] - df["pre_close"]
    df["pct_chg"] = df["change"] / df["pre_close"].replace(0, pd.NA) * 100
    df["amplitude"] = (df["high"] - df["low"]) / df["pre_close"].replace(0, pd.NA) * 100
    df["turnover_rate"] = pd.NA

    columns = [
        "trade_date",
        "symbol",
        "open",
        "close",
        "high",
        "low",
        "volume",
        "amount",
        "amplitude",
        "pct_chg",
        "change",
        "turnover_rate",
        "paused",
        "data_source",
    ]
    return df[columns].dropna(subset=["trade_date", "open", "high", "low", "close"]).sort_values("trade_date").reset_index(drop=True)


def get_jq_a_share_universe(
    date: str | None = None,
    exclude_chinext: bool = True,
    exclude_star: bool = True,
    exclude_st: bool = True,
) -> pd.DataFrame:
    jq = auth_jqdata()
    securities = jq.get_all_securities(types=["stock"], date=to_jq_date(date) if date else None)
    if securities is None or securities.empty:
        return pd.DataFrame(columns=["symbol", "name", "jq_symbol", "start_date", "end_date"])

    df = securities.reset_index().rename(columns={"index": "jq_symbol"})
    if "display_name" in df.columns:
        df["name"] = df["display_name"]
    elif "name" not in df.columns:
        df["name"] = df["jq_symbol"]
    df["symbol"] = df["jq_symbol"].map(from_jq_symbol)
    if exclude_chinext:
        df = df[~df["symbol"].str.startswith(("300", "301"))]
    if exclude_star:
        df = df[~df["symbol"].str.startswith(("688", "689"))]
    if exclude_st and "name" in df.columns:
        df = df[~df["name"].astype(str).str.contains("ST", case=False, na=False)]
    keep = [col for col in ["symbol", "name", "jq_symbol", "start_date", "end_date", "type"] if col in df.columns]
    return df[keep].drop_duplicates("symbol").sort_values("symbol").reset_index(drop=True)


def get_jq_account_status() -> dict:
    jq = auth_jqdata()
    status = {}
    try:
        status["query_count"] = jq.get_query_count()
    except Exception as exc:  # noqa: BLE001
        status["query_count_error"] = str(exc)
    try:
        status["account_info"] = jq.get_account_info()
    except Exception as exc:  # noqa: BLE001
        status["account_info_error"] = str(exc)
    return status


def iter_plain_symbols(symbols: Iterable[str]) -> list[str]:
    return [normalize_symbol(symbol) for symbol in symbols if str(symbol).strip()]
