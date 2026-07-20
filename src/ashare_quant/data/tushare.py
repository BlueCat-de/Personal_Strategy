#!/usr/bin/env python3
"""Tushare Pro data adapter for the local A-share strategy framework."""

from __future__ import annotations

import logging
import os
import time
from functools import lru_cache
from pathlib import Path

import pandas as pd
import tushare as ts

from ashare_quant.paths import DEFAULT_ENV_FILE

LOGGER = logging.getLogger("tushare_provider")


def load_env_file(path: Path = DEFAULT_ENV_FILE) -> dict[str, str]:
    """Load simple KEY=VALUE pairs without exporting secrets."""
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


def resolve_tushare_token(env_file: Path = DEFAULT_ENV_FILE) -> str:
    env_values = load_env_file(env_file)
    for key in ["TUSHARE_TOKEN", "TUSHARE_PRO_TOKEN", "tushare_token", "tushare_pro_token"]:
        value = os.environ.get(key) or env_values.get(key)
        if value:
            return value
    raise RuntimeError(f"Tushare token not found in env vars or {env_file}")


@lru_cache(maxsize=4)
def get_pro_client(env_file: Path = DEFAULT_ENV_FILE):
    token = resolve_tushare_token(env_file)
    return ts.pro_api(token)


def normalize_symbol(symbol: str | int | float | None) -> str:
    if symbol is None:
        return ""
    value = str(symbol).strip()
    if not value:
        return ""
    if "." in value:
        value = value.split(".", 1)[0]
    return value.replace(".0", "").zfill(6)


def to_ts_code(symbol: str, market_hint: str | None = None) -> str:
    symbol = normalize_symbol(symbol)
    if not symbol:
        return ""
    if market_hint in {"SH", "SZ", "BJ"}:
        return f"{symbol}.{market_hint}"
    if symbol.startswith(("600", "601", "603", "605", "688", "689")):
        return f"{symbol}.SH"
    if symbol.startswith(("4", "8", "920")):
        return f"{symbol}.BJ"
    return f"{symbol}.SZ"


def from_ts_code(ts_code: str | None) -> str:
    if not ts_code:
        return ""
    return normalize_symbol(str(ts_code).split(".", 1)[0])


def is_chinext(symbol: str) -> bool:
    return normalize_symbol(symbol).startswith(("300", "301"))


def is_star_market(symbol: str) -> bool:
    return normalize_symbol(symbol).startswith(("688", "689"))


def is_bse(symbol: str) -> bool:
    return normalize_symbol(symbol).startswith(("4", "8", "920"))


def _retry_call(func, *, retries: int = 3, sleep_seconds: float = 0.8) -> pd.DataFrame:
    last_error: BaseException | None = None
    for attempt in range(retries):
        try:
            frame = func()
            return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame(frame)
        except Exception as error:
            last_error = error
            if attempt < retries - 1:
                delay = sleep_seconds * (attempt + 1)
                LOGGER.warning(
                    "Tushare request failed (%s/%s): %s; retry in %.1fs",
                    attempt + 1,
                    retries,
                    type(error).__name__,
                    delay,
                )
                time.sleep(delay)
                continue
            raise
    if last_error:
        raise last_error
    return pd.DataFrame()


def fetch_stock_basic(env_file: Path = DEFAULT_ENV_FILE) -> pd.DataFrame:
    pro = get_pro_client(env_file)
    fields = "ts_code,symbol,name,area,industry,market,list_date,exchange"
    frame = _retry_call(lambda: pro.stock_basic(exchange="", list_status="L", fields=fields))
    if frame.empty:
        return frame
    frame["symbol"] = frame["ts_code"].map(from_ts_code)
    return frame.sort_values("ts_code").reset_index(drop=True)


def fetch_stock_basic_all(env_file: Path = DEFAULT_ENV_FILE) -> pd.DataFrame:
    pro = get_pro_client(env_file)
    fields = "ts_code,symbol,name,area,industry,market,list_date,delist_date,exchange"
    frames: list[pd.DataFrame] = []
    for status in ["L", "D", "P"]:
        frame = _retry_call(
            lambda status=status: pro.stock_basic(exchange="", list_status=status, fields=fields)
        )
        if frame.empty:
            continue
        frame["list_status"] = status
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=[*fields.split(","), "list_status"])
    result = pd.concat(frames, ignore_index=True)
    result["symbol"] = result["ts_code"].map(from_ts_code)
    return result.sort_values("ts_code").reset_index(drop=True)


def fetch_namechange(
    start_date: str, end_date: str, env_file: Path = DEFAULT_ENV_FILE
) -> pd.DataFrame:
    pro = get_pro_client(env_file)
    fields = "ts_code,name,start_date,end_date,change_reason"
    frame = _retry_call(
        lambda: pro.namechange(
            start_date=pd.to_datetime(start_date).strftime("%Y%m%d"),
            end_date=pd.to_datetime(end_date).strftime("%Y%m%d"),
            fields=fields,
        )
    )
    if frame.empty:
        return pd.DataFrame(columns=fields.split(","))
    frame["start_date"] = pd.to_datetime(frame["start_date"], errors="coerce").dt.strftime(
        "%Y-%m-%d"
    )
    frame["end_date"] = pd.to_datetime(frame["end_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return frame.sort_values(["ts_code", "start_date"]).reset_index(drop=True)


def fetch_trade_calendar(
    start_date: str, end_date: str, env_file: Path = DEFAULT_ENV_FILE
) -> pd.DataFrame:
    pro = get_pro_client(env_file)
    frame = _retry_call(
        lambda: pro.trade_cal(
            exchange="",
            start_date=pd.to_datetime(start_date).strftime("%Y%m%d"),
            end_date=pd.to_datetime(end_date).strftime("%Y%m%d"),
            fields="exchange,cal_date,is_open,pretrade_date",
        )
    )
    if frame.empty:
        return frame
    frame["cal_date"] = pd.to_datetime(frame["cal_date"]).dt.strftime("%Y-%m-%d")
    return frame.sort_values("cal_date").reset_index(drop=True)


def latest_open_trade_date(
    end_date: str, env_file: Path = DEFAULT_ENV_FILE, lookback_days: int = 20
) -> str | None:
    start = pd.to_datetime(end_date) - pd.Timedelta(days=lookback_days)
    cal = fetch_trade_calendar(start.strftime("%Y-%m-%d"), end_date, env_file)
    if cal.empty:
        return None
    opened = cal[cal["is_open"] == 1]
    if opened.empty:
        return None
    return str(opened["cal_date"].max())


def trading_dates(start_date: str, end_date: str, env_file: Path = DEFAULT_ENV_FILE) -> list[str]:
    cal = fetch_trade_calendar(start_date, end_date, env_file)
    if cal.empty:
        return []
    return cal.loc[cal["is_open"] == 1, "cal_date"].astype(str).tolist()


def fetch_daily(trade_date: str, env_file: Path = DEFAULT_ENV_FILE) -> pd.DataFrame:
    pro = get_pro_client(env_file)
    compact = pd.to_datetime(trade_date).strftime("%Y%m%d")
    frame = _retry_call(lambda: pro.daily(trade_date=compact))
    if frame.empty:
        return frame
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    return frame


def fetch_adj_factor(trade_date: str, env_file: Path = DEFAULT_ENV_FILE) -> pd.DataFrame:
    pro = get_pro_client(env_file)
    compact = pd.to_datetime(trade_date).strftime("%Y%m%d")
    frame = _retry_call(lambda: pro.adj_factor(trade_date=compact))
    if frame.empty:
        return frame
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    return frame


def fetch_daily_basic(trade_date: str, env_file: Path = DEFAULT_ENV_FILE) -> pd.DataFrame:
    pro = get_pro_client(env_file)
    compact = pd.to_datetime(trade_date).strftime("%Y%m%d")
    fields = "ts_code,trade_date,turnover_rate,volume_ratio,total_mv,circ_mv,pe,pb"
    frame = _retry_call(lambda: pro.daily_basic(trade_date=compact, fields=fields))
    if frame.empty:
        return frame
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    return frame


def fetch_fina_indicator(
    ts_code: str,
    start_date: str,
    end_date: str,
    env_file: Path = DEFAULT_ENV_FILE,
) -> pd.DataFrame:
    pro = get_pro_client(env_file)
    fields = (
        "ts_code,ann_date,end_date,roe,roe_waa,roa,roic,grossprofit_margin,"
        "netprofit_margin,debt_to_assets,assets_turn,ocf_to_debt,q_ocf_to_sales,"
        "q_sales_yoy,q_op_qoq,dt_netprofit_yoy,ocf_yoy,equity_yoy"
    )
    return _retry_call(
        lambda: pro.fina_indicator(
            ts_code=ts_code,
            start_date=pd.to_datetime(start_date).strftime("%Y%m%d"),
            end_date=pd.to_datetime(end_date).strftime("%Y%m%d"),
            fields=fields,
        ),
        retries=5,
        sleep_seconds=15.0,
    )


def fetch_suspend(trade_date: str, env_file: Path = DEFAULT_ENV_FILE) -> pd.DataFrame:
    pro = get_pro_client(env_file)
    compact = pd.to_datetime(trade_date).strftime("%Y%m%d")
    frame = _retry_call(lambda: pro.suspend_d(trade_date=compact))
    if frame.empty:
        return pd.DataFrame(columns=["ts_code", "trade_date", "is_suspended"])
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    frame["is_suspended"] = 1
    return frame[["ts_code", "trade_date", "is_suspended"]]


def fetch_stk_limit(trade_date: str, env_file: Path = DEFAULT_ENV_FILE) -> pd.DataFrame:
    pro = get_pro_client(env_file)
    compact = pd.to_datetime(trade_date).strftime("%Y%m%d")
    frame = _retry_call(lambda: pro.stk_limit(trade_date=compact))
    if frame.empty:
        return pd.DataFrame(columns=["ts_code", "trade_date", "up_limit", "down_limit"])
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    return frame[["ts_code", "trade_date", "up_limit", "down_limit"]]
