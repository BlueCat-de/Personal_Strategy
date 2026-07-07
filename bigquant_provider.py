#!/usr/bin/env python3
"""BigQuant SDK data adapter.

This module converts BigQuant DAI output into the local cache schema used by
the project.

BigQuant ``cn_stock_bar1d`` stores post-adjusted prices and share-based volume.
The strategy expects front-adjusted prices and volume in hands, so the adapter
defaults to ``adjust="qfq"`` and ``volume_unit="hand"``.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import pandas as pd


LOGGER = logging.getLogger("bigquant_provider")
DEFAULT_ENV_FILE = Path(__file__).resolve().parent / ".env.local"
DEFAULT_DATASOURCE = "cn_stock_bar1d"


def normalize_symbol(symbol: str) -> str:
    """Keep only the 6-digit A-share code part."""
    value = str(symbol).strip()
    if "." in value:
        value = value.split(".", 1)[0]
    return value.zfill(6)


def to_bigquant_instrument(symbol: str) -> str:
    """Convert a 6-digit A-share code to BigQuant instrument format."""
    symbol = normalize_symbol(symbol)
    if symbol.startswith(("6", "9")):
        return f"{symbol}.SH"
    if symbol.startswith(("0", "2", "3")):
        return f"{symbol}.SZ"
    if symbol.startswith(("4", "8", "920")):
        return f"{symbol}.BJ"
    return symbol


def from_bigquant_instrument(instrument: str) -> str:
    """Convert BigQuant instrument format to a 6-digit A-share code."""
    return normalize_symbol(str(instrument).split(".", 1)[0])


def load_env_file(path: Path = DEFAULT_ENV_FILE) -> dict[str, str]:
    """Load simple KEY=VALUE lines without printing or exporting secrets."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def init_bigquant(env_file: Path = DEFAULT_ENV_FILE) -> None:
    """Initialize BigQuant SDK from BIGQUANT_API_KEY or ~/.bigquant config."""
    import bigquant

    env_values = load_env_file(env_file)
    api_key = os.environ.get("BIGQUANT_API_KEY") or env_values.get("BIGQUANT_API_KEY")
    if api_key:
        if "." in api_key:
            ak, sk = api_key.split(".", 1)
            bigquant.init(ak=ak, sk=sk)
        else:
            bigquant.init_from_token(api_key)
        return

    bigquant.init_from_config()


def _quote_sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


@lru_cache(maxsize=16)
def get_datasource_fields(datasource: str = DEFAULT_DATASOURCE) -> set[str]:
    """Return available field names for a BigQuant datasource."""
    from bigquant import dai

    schema = dai.get_datasource_schema(datasource)
    fields = schema.get("fields", [])
    result: set[str] = set()
    for field in fields:
        if isinstance(field, dict) and field.get("name"):
            result.add(str(field["name"]))
    return result


def _first_available(fields: set[str], candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        if candidate in fields:
            return candidate
    return None


def _select_columns(datasource: str) -> tuple[list[str], dict[str, str | None]]:
    fields = get_datasource_fields(datasource)
    required = ["date", "instrument", "open", "high", "low", "close", "volume"]
    missing = [field for field in required if field not in fields]
    if missing:
        raise ValueError(f"BigQuant datasource {datasource} missing required fields: {missing}")

    amount_col = _first_available(fields, ["amount", "turnover_value", "transaction_amount"])
    turnover_col = _first_available(fields, ["turnover_rate", "turnover", "turn"])

    columns = required.copy()
    if "adjust_factor" in fields:
        columns.append("adjust_factor")
    if amount_col:
        columns.append(amount_col)
    if turnover_col and turnover_col not in columns:
        columns.append(turnover_col)
    return columns, {"amount": amount_col, "turnover_rate": turnover_col}


def _standardize_daily_frame(
    df: pd.DataFrame,
    aliases: dict[str, str | None],
    *,
    adjust: str,
    volume_unit: str,
) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "trade_date",
                "symbol",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "turnover_rate",
            ]
        )

    result = df.copy()
    result["trade_date"] = pd.to_datetime(result["date"], errors="coerce").dt.strftime("%Y%m%d")
    result["symbol"] = result["instrument"].map(from_bigquant_instrument)
    result["amount"] = result[aliases["amount"]] if aliases.get("amount") else pd.NA
    result["turnover_rate"] = result[aliases["turnover_rate"]] if aliases.get("turnover_rate") else pd.NA

    price_cols = ["open", "high", "low", "close"]
    for col in price_cols + ["volume", "amount", "turnover_rate"]:
        result[col] = pd.to_numeric(result[col], errors="coerce")

    if "adjust_factor" in result.columns:
        result["adjust_factor"] = pd.to_numeric(result["adjust_factor"], errors="coerce")
    elif adjust in {"raw", "qfq"}:
        raise ValueError("BigQuant adjust_factor is required for raw/qfq conversion.")

    if adjust == "raw":
        for col in price_cols:
            result[col] = result[col] / result["adjust_factor"]
    elif adjust == "qfq":
        latest_factor = (
            result.sort_values(["symbol", "trade_date"])
            .groupby("symbol")["adjust_factor"]
            .transform("last")
        )
        for col in price_cols:
            result[col] = result[col] / latest_factor
    elif adjust == "hfq":
        pass
    else:
        raise ValueError(f"Unsupported adjust: {adjust}")

    if volume_unit == "hand":
        result["volume"] = result["volume"] / 100.0
    elif volume_unit == "share":
        pass
    else:
        raise ValueError(f"Unsupported volume_unit: {volume_unit}")

    output_cols = [
        "trade_date",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "turnover_rate",
    ]
    return (
        result[output_cols]
        .dropna(subset=["trade_date", "symbol", "close"])
        .sort_values(["trade_date", "symbol"])
        .reset_index(drop=True)
    )


def fetch_bigquant_daily_history(
    symbol: str,
    start_date: str,
    end_date: str,
    *,
    datasource: str = DEFAULT_DATASOURCE,
    adjust: str = "qfq",
    volume_unit: str = "hand",
) -> pd.DataFrame:
    """Fetch one symbol's daily OHLCV data from BigQuant DAI."""
    return fetch_bigquant_daily_history_batch(
        [symbol],
        start_date=start_date,
        end_date=end_date,
        datasource=datasource,
        adjust=adjust,
        volume_unit=volume_unit,
    )


def fetch_bigquant_daily_history_batch(
    symbols: Iterable[str],
    start_date: str,
    end_date: str,
    *,
    datasource: str = DEFAULT_DATASOURCE,
    adjust: str = "qfq",
    volume_unit: str = "hand",
) -> pd.DataFrame:
    """Fetch multiple symbols' daily OHLCV data from BigQuant DAI."""
    from bigquant import dai

    instruments = [to_bigquant_instrument(symbol) for symbol in symbols]
    if not instruments:
        return _standardize_daily_frame(
            pd.DataFrame(),
            {"amount": None, "turnover_rate": None},
            adjust=adjust,
            volume_unit=volume_unit,
        )

    columns, aliases = _select_columns(datasource)
    instruments_sql = ", ".join(_quote_sql_string(item) for item in instruments)
    sql = f"""
        SELECT {", ".join(columns)}
        FROM {datasource}
        WHERE date >= {_quote_sql_string(start_date)}
          AND date <= {_quote_sql_string(end_date)}
          AND instrument IN ({instruments_sql})
        ORDER BY date, instrument
    """
    LOGGER.info("Query BigQuant %s for %s instruments from %s to %s", datasource, len(instruments), start_date, end_date)
    raw = dai.query(
        sql,
        filters={
            "date": [start_date, end_date],
            "instrument": instruments,
        },
    ).df()
    return _standardize_daily_frame(raw, aliases, adjust=adjust, volume_unit=volume_unit)
