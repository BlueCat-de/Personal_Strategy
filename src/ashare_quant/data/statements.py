"""Cache PIT raw financial statements (income / balancesheet / cashflow).

Mirrors `data/financials.py` (the fina_indicator ratio cache) for the three raw
statement LEVEL tables, with the SAME point-in-time discipline:

  - `normalize_financial_rows` (imported from financials.py): parse ann_date /
    end_date, require end_date <= ann_date, dedup on (ts_code, ann_date, end_date)
    preferring the ORIGINAL filing (update_flag=0) over later revisions — revision
    timestamps aren't exposed, so preferring the original avoids look-ahead leakage.
  - `available_date = ann_date + 1 day` — conservative T+1 signal availability,
    matching the fina_indicator cache and the Ptrade `get_fundamentals` publ_date mode.

Why: the fina_indicator RATIO cache cannot express cross-statement level factors
like true accruals (Sloan) = (net_income - operating_cash_flow) / total_assets.
These raw levels unlock that family.

Deployability: every cached field maps 1:1 to a Ptrade `get_fundamentals` statement
field (PIT by publ_date — see docs/ptradeapi `hub-data-finance/001`). The mapping is
`DEPLOYABLE_FIELD_MAP`. Factor code MUST use only mapped fields; verify the Ptrade
name at factor-build time.

Usage:
    python -m ashare_quant.data.statements --statement income \
        --financial-start 2009-01-01 --financial-end 2026-07-17
    python -m ashare_quant.data.statements --statement balancesheet ...
    python -m ashare_quant.data.statements --statement cashflow ...
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import pandas as pd

from ashare_quant.boards import BOARD_SCOPES
from ashare_quant.data.financials import (
    DEFAULT_BASIC_CACHE,
    historical_large_cap_universe,
    normalize_financial_rows,
)
from ashare_quant.data.tushare import (
    DEFAULT_ENV_FILE,
    fetch_balancesheet,
    fetch_cashflow,
    fetch_income,
)
from ashare_quant.paths import PROJECT_ROOT
from ashare_quant.research.factors import atomic_write_csv

LOGGER = logging.getLogger("raw_statements")

DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data/offline/a_share_history_tushare"

# Curated fields that are BOTH in Tushare's statement API AND Ptrade-deployable.
# Mapping: {statement: {tushare_field: ptrade_get_fundamentals_field}}.
DEPLOYABLE_FIELD_MAP: dict[str, dict[str, str]] = {
    "income": {
        "n_income": "net_profit",
        "n_income_attr_p": "np_parent_company_owners",
        "total_revenue": "total_operating_revenue",
        "revenue": "operating_revenue",
        "operate_profit": "operating_profit",
        "total_profit": "total_profit",
        "oper_cost": "operating_cost",
    },
    "balancesheet": {
        "total_assets": "total_assets",
        "total_cur_assets": "total_current_assets",
        "total_cur_liab": "total_current_liability",
        "total_hldr_eqy_exc_min_int": "se_without_mi",  # 归母权益
    },
    "cashflow": {
        "n_cashflow_act": "net_operate_cash_flow",
        "n_cashflow_inv_act": "net_invest_cash_flow",
        "n_cash_flows_fnc_act": "net_finance_cash_flow",
        "net_profit": "net_profit",
    },
}

_FETCHERS = {
    "income": fetch_income,
    "balancesheet": fetch_balancesheet,
    "cashflow": fetch_cashflow,
}

META_COLS = ["ts_code", "ann_date", "end_date", "update_flag"]


def _curated_fields(statement: str) -> list[str]:
    return [c for c in DEPLOYABLE_FIELD_MAP[statement] if c not in META_COLS]


# Joined fields across the 3 statements (net_profit dropped — collides with income's n_income).
JOINED_FIELDS: list[str] = (
    _curated_fields("income")
    + _curated_fields("balancesheet")
    + [f for f in _curated_fields("cashflow") if f != "net_profit"]
)


def load_joined_statements(cache_root: Path) -> pd.DataFrame:
    """Join income/balancesheet/cashflow on (symbol, end_date) into a per-report wide frame.

    A report is treatable as known when ALL its statements are announced → available_date
    = max(ann_date across the 3 statements) + 1 day. This keeps cross-statement factors
    (e.g. accruals = (n_income − n_cashflow_act)/total_assets) on a SINGLE reporting
    period (no Q4-income / Q3-assets mixing), which is the correct Sloan construction.
    """
    cache_root = Path(cache_root)
    merged = None
    for stmt in ("income", "balancesheet", "cashflow"):
        h = load_statement_history(cache_root / f".{stmt}_statement_cache", stmt)
        if h.empty:
            continue
        ann_col = f"ann_date_{stmt}"
        h = h.rename(columns={"ann_date": ann_col})
        keep = ["symbol", "end_date", ann_col] + [f for f in _curated_fields(stmt) if f != "net_profit" and f in h.columns]
        h = h[keep]
        # one row per (symbol, end_date): keep the latest ann for that statement
        h = h.sort_values(["symbol", "end_date", ann_col]).drop_duplicates(["symbol", "end_date"], keep="last")
        merged = h if merged is None else merged.merge(h, on=["symbol", "end_date"], how="outer")
    if merged is None or merged.empty:
        return pd.DataFrame()
    ann_cols = [c for c in merged.columns if c.startswith("ann_date_")]
    merged["ann_date"] = merged[ann_cols].max(axis=1)  # available when ALL parts announced
    merged["available_date"] = merged["ann_date"] + pd.Timedelta(days=1)
    for c in JOINED_FIELDS:
        if c in merged.columns:
            merged[c] = pd.to_numeric(merged[c], errors="coerce")
    return (
        merged.dropna(subset=["symbol", "ann_date"])
        .sort_values(["symbol", "available_date", "end_date"])
        .reset_index(drop=True)
    )


class StatementSnapshotStore:
    """PIT snapshot of joined statements — mirrors FinancialSnapshotStore (chronological,
    cursor-based). `.latest(date)` returns the most-recent fully-announced report per
    symbol as-of `date`, as a DataFrame indexed by symbol with JOINED_FIELDS columns.
    """

    def __init__(self, cache_root: Path):
        history = load_joined_statements(cache_root)
        if history.empty:
            raise FileNotFoundError(f"No joined statement history under {cache_root}")
        self._history = history.sort_values(
            ["available_date", "symbol", "end_date"]
        ).reset_index(drop=True)
        self._cursor = 0
        self._last_date: pd.Timestamp | None = None
        self._latest: dict[str, pd.Series] = {}
        self._latest_key: dict[str, tuple] = {}

    def latest(self, date: pd.Timestamp) -> pd.DataFrame:
        date = pd.Timestamp(date)
        if self._last_date is not None and date < self._last_date:
            raise ValueError("Statement snapshots must be requested in chronological order")
        while self._cursor < len(self._history):
            row = self._history.iloc[self._cursor]
            if row["available_date"] > date:
                break
            symbol = str(row["symbol"])
            key = (row["end_date"], row["available_date"])
            if key >= self._latest_key.get(symbol, (pd.Timestamp.min,) * 2):
                self._latest[symbol] = row
                self._latest_key[symbol] = key
            self._cursor += 1
        self._last_date = date
        if not self._latest:
            return pd.DataFrame(columns=JOINED_FIELDS)
        df = pd.DataFrame.from_dict(self._latest, orient="index")
        cols = [c for c in JOINED_FIELDS if c in df.columns]
        return df[cols]


def cache_statements(
    ts_codes: list[str],
    output_dir: Path,
    start_date: str,
    end_date: str,
    env_file: Path,
    statement: str,
) -> None:
    """Incremental per-ts_code cache for one statement type. Mirrors cache_financials."""
    fetcher = _FETCHERS[statement]
    keep_cols = META_COLS + _curated_fields(statement)
    output_dir.mkdir(parents=True, exist_ok=True)

    for index, ts_code in enumerate(ts_codes, start=1):
        path = output_dir / f"{ts_code.replace('.', '_')}.csv"
        requested_start = pd.Timestamp(start_date)
        requested_end = pd.Timestamp(end_date)
        fetch_windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
        if path.exists():
            cached = pd.read_csv(
                path, dtype={"ts_code": str, "ann_date": str, "end_date": str, "update_flag": str}
            )
            normalized = normalize_financial_rows(cached) if not cached.empty else cached
            cached_start = normalized["end_date"].min() if not normalized.empty else pd.NaT
            cached_end = normalized["end_date"].max() if not normalized.empty else pd.NaT
            if pd.isna(cached_start) or pd.isna(cached_end):
                fetch_windows.append((requested_start, requested_end))
            else:
                if requested_start < cached_start:
                    fetch_windows.append((requested_start, min(requested_end, cached_start - pd.Timedelta(days=1))))
                if requested_end > cached_end:
                    fetch_windows.append((max(requested_start, cached_end + pd.Timedelta(days=1)), requested_end))
            fetch_windows = [(s, e) for s, e in fetch_windows if s <= e]
            if not fetch_windows:
                LOGGER.info("%s/%s %s cached [statement=%s]", index, len(ts_codes), ts_code, statement)
                continue
        else:
            cached = pd.DataFrame()
            fetch_windows.append((requested_start, requested_end))

        fetched: list[pd.DataFrame] = []
        for window_start, window_end in fetch_windows:
            LOGGER.info("%s/%s %s fetch %s [%s] %s..%s", index, len(ts_codes), ts_code, statement,
                        window_start.date(), window_start.date(), window_end.date())
            frame = fetcher(ts_code, window_start.strftime("%Y-%m-%d"), window_end.strftime("%Y-%m-%d"), env_file)
            fetched.append(frame)
            time.sleep(0.36)
        frame = pd.concat([cached, *fetched], ignore_index=True)
        if not frame.empty:
            # keep only curated deployable fields (+ meta) before normalizing/writing
            present_keep = [c for c in keep_cols if c in frame.columns]
            frame = frame[present_keep]
            frame = normalize_financial_rows(frame)
            frame["ann_date"] = frame["ann_date"].dt.strftime("%Y%m%d")
            frame["end_date"] = frame["end_date"].dt.strftime("%Y%m%d")
        atomic_write_csv(frame, path)


def load_statement_history(cache_dir: Path, statement: str) -> pd.DataFrame:
    """Load all cached CSVs for one statement type, normalize, and attach available_date (PIT)."""
    frames: list[pd.DataFrame] = []
    for path in sorted(cache_dir.glob("*.csv")):
        frame = pd.read_csv(
            path, dtype={"ts_code": str, "ann_date": str, "end_date": str, "update_flag": str}
        )
        if frame.empty:
            continue
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    history = normalize_financial_rows(pd.concat(frames, ignore_index=True))
    history["symbol"] = history["ts_code"].str.split(".").str[0].str.zfill(6)
    history["available_date"] = history["ann_date"] + pd.Timedelta(days=1)  # PIT T+1
    for col in _curated_fields(statement):
        if col in history.columns:
            history[col] = pd.to_numeric(history[col], errors="coerce")
    return (
        history.dropna(subset=["symbol", "ann_date", "end_date", "available_date"])
        .sort_values(["symbol", "available_date", "end_date", "ann_date"])
        .drop_duplicates(["symbol", "ann_date", "end_date"], keep="last")
        .reset_index(drop=True)
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--statement", required=True, choices=list(_FETCHERS),
                        help="which statement table to cache")
    parser.add_argument("--universe-start", default="2011-01-01")
    parser.add_argument("--universe-end", default="2026-07-17")
    parser.add_argument("--financial-start", default="2009-01-01")
    parser.add_argument("--financial-end", default="2026-07-17")
    parser.add_argument("--basic-cache", type=Path, default=DEFAULT_BASIC_CACHE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--board-scope", default="main", choices=BOARD_SCOPES)
    parser.add_argument("--codes-file", type=Path)
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(message)s", force=True)
    output_dir = args.output_root / f".{args.statement}_statement_cache"

    if args.codes_file:
        ts_codes = [ln.strip() for ln in args.codes_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    else:
        ts_codes = historical_large_cap_universe(args.basic_cache, args.universe_start, args.universe_end, args.board_scope)
    LOGGER.info("Universe [%s]: %s symbols  →  %s", args.board_scope, len(ts_codes), output_dir)

    cache_statements(ts_codes, output_dir, args.financial_start, args.financial_end, args.env_file, args.statement)
    print(f"Cached {len(ts_codes)} symbols [{args.statement}] in {output_dir.resolve()}")


if __name__ == "__main__":
    main()
