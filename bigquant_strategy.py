#!/usr/bin/env python3
"""BigQuant-only small-account strategy.

Data source: BigQuant DAI.
Backtest engine: BigQuant BigTrader.

This is the minimal strategy entry point for this branch.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from bigquant_provider import (
    DEFAULT_DATASOURCE,
    fetch_bigquant_daily_history_batch,
    from_bigquant_instrument,
    init_bigquant,
    normalize_symbol,
    to_bigquant_instrument,
)


LOGGER = logging.getLogger("bigquant_strategy")
REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = REPO_ROOT / ".env.local"
DEFAULT_CACHE_DIR = REPO_ROOT / "data/bigquant_cache"
DEFAULT_PRICES_FILE = REPO_ROOT / "data/offline/a_share_12m_bigquant/prices_long.csv"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data/backtests/bigquant_strategy"


@dataclass(frozen=True)
class StrategyConfig:
    start_date: str
    end_date: str
    warmup_start_date: str
    initial_cash: float
    max_positions: int
    max_position_weight: float
    strong_total_weight: float
    neutral_total_weight: float
    batch_size: int
    limit: int | None
    env_file: Path
    cache_dir: Path
    prices_file: Path | None
    output_dir: Path
    datasource: str
    benchmark: str
    use_cache: bool


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )


def iso_date(value: str) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def compact_date(value: str) -> str:
    return pd.to_datetime(value).strftime("%Y%m%d")


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            tmp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def atomic_write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
            tmp_path = Path(handle.name)
            df.to_csv(handle, index=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def is_chinext(symbol: str) -> bool:
    return normalize_symbol(symbol).startswith(("300", "301"))


def is_star_market(symbol: str) -> bool:
    return normalize_symbol(symbol).startswith(("688", "689"))


def is_bse(symbol: str) -> bool:
    symbol = normalize_symbol(symbol)
    return symbol.startswith(("4", "8", "920"))


def load_universe(config: StrategyConfig) -> pd.DataFrame:
    from bigquant import dai

    end_date = iso_date(config.end_date)
    sql = f"""
        SELECT instrument, name
        FROM {config.datasource}
        WHERE date = '{end_date}'
        ORDER BY instrument
    """
    raw = dai.query(sql, filters={"date": [end_date, end_date]}).df()
    if raw.empty:
        raise ValueError(f"No BigQuant universe rows on {end_date}")

    universe = raw.copy()
    universe["symbol"] = universe["instrument"].map(from_bigquant_instrument)
    universe["name"] = universe["name"].astype(str)
    universe = universe.drop_duplicates("symbol")
    universe = universe[~universe["symbol"].map(is_chinext)]
    universe = universe[~universe["symbol"].map(is_star_market)]
    universe = universe[~universe["symbol"].map(is_bse)]
    universe = universe[~universe["name"].str.upper().str.contains("ST", na=False)]
    universe = universe.sort_values("symbol").reset_index(drop=True)
    if config.limit:
        universe = universe.head(config.limit)
    return universe[["symbol", "instrument", "name"]]


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def cache_path(config: StrategyConfig, universe_count: int) -> Path:
    return (
        config.cache_dir
        / f"bars_{compact_date(config.warmup_start_date)}_{compact_date(config.end_date)}_{universe_count}_{config.datasource}.csv"
    )


def load_or_fetch_bars(config: StrategyConfig, universe: pd.DataFrame) -> pd.DataFrame:
    if config.prices_file and config.prices_file.exists():
        LOGGER.info("Load BigQuant local prices file: %s", config.prices_file)
        local = pd.read_csv(config.prices_file, dtype={"symbol": str})
        local["symbol"] = local["symbol"].map(normalize_symbol)
        local["trade_date"] = pd.to_datetime(local["date"], errors="coerce").dt.strftime("%Y%m%d")
        local = local[
            local["symbol"].isin(set(universe["symbol"]))
            & (local["trade_date"] >= compact_date(config.warmup_start_date))
            & (local["trade_date"] <= compact_date(config.end_date))
        ].copy()
        local = local.rename(columns={"turnover": "turnover_rate"})
        return local[["trade_date", "symbol", "open", "high", "low", "close", "volume", "amount", "turnover_rate"]]

    path = cache_path(config, len(universe))
    if config.use_cache and path.exists():
        LOGGER.info("Load cached BigQuant bars: %s", path)
        return pd.read_csv(path, dtype={"symbol": str})

    symbols = universe["symbol"].tolist()
    frames: list[pd.DataFrame] = []
    for index, batch in enumerate(chunked(symbols, config.batch_size), start=1):
        LOGGER.info("Fetch BigQuant batch %s/%s size=%s", index, (len(symbols) + config.batch_size - 1) // config.batch_size, len(batch))
        frame = fetch_bigquant_daily_history_batch(
            batch,
            start_date=iso_date(config.warmup_start_date),
            end_date=iso_date(config.end_date),
            datasource=config.datasource,
            adjust="qfq",
            volume_unit="hand",
        )
        if not frame.empty:
            frames.append(frame)

    bars = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    bars = bars.sort_values(["trade_date", "symbol"]).reset_index(drop=True)
    atomic_write_csv(bars, path)
    return bars


def compute_market_regime(close: pd.DataFrame) -> pd.Series:
    ma20 = close.rolling(20, min_periods=20).mean()
    ma60 = close.rolling(60, min_periods=60).mean()
    ma120 = close.rolling(120, min_periods=120).mean()
    breadth20 = (close > ma20).mean(axis=1)
    breadth60 = (close > ma60).mean(axis=1)
    breadth120 = (close > ma120).mean(axis=1)
    score = 0.5 * breadth20 + 0.3 * breadth60 + 0.2 * breadth120
    regime = pd.Series("weak", index=close.index)
    regime[score >= 0.55] = "strong"
    regime[(score >= 0.45) & (score < 0.55)] = "neutral"
    return regime


def weekly_rebalance_dates(dates: pd.Index, start_date: str) -> set[str]:
    active = pd.Series(pd.to_datetime(dates), index=dates)
    active = active[active >= pd.to_datetime(start_date)]
    if active.empty:
        return set()
    grouped = active.groupby(active.dt.to_period("W-MON"))
    return {group.iloc[0].strftime("%Y%m%d") for _, group in grouped}


def build_weight_signals(bars: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
    if bars.empty:
        return pd.DataFrame(columns=["date", "instrument", "weight"])

    prices = bars.pivot(index="trade_date", columns="symbol", values="close").sort_index()
    volume = bars.pivot(index="trade_date", columns="symbol", values="volume").reindex_like(prices)
    returns_20 = prices.pct_change(20, fill_method=None)
    returns_60 = prices.pct_change(60, fill_method=None)
    vol_20 = prices.pct_change(fill_method=None).rolling(20, min_periods=20).std()
    liquidity = (prices * volume).rolling(20, min_periods=20).mean()

    momentum_score = returns_20.rank(axis=1, pct=True) * 0.45 + returns_60.rank(axis=1, pct=True) * 0.35
    low_vol_score = (1.0 - vol_20.rank(axis=1, pct=True)) * 0.20
    score = momentum_score + low_vol_score

    high_risk = (vol_20.rank(axis=1, pct=True) > 0.90) | (returns_20 < -0.08) | (liquidity.rank(axis=1, pct=True) < 0.20)
    score = score.mask(high_risk)
    regime = compute_market_regime(prices)
    rebalance_dates = weekly_rebalance_dates(prices.index, config.start_date)

    rows: list[dict] = []
    current_holdings: set[str] = set()
    for dt in prices.index:
        dt_iso = pd.to_datetime(dt).strftime("%Y-%m-%d")
        dt_compact = str(dt)
        today_prices = prices.loc[dt]

        # Daily risk exits: MA20 break, -6% 20-day loss proxy, or high volatility.
        if current_holdings:
            ma20 = prices.rolling(20, min_periods=20).mean().loc[dt]
            vol_today = vol_20.loc[dt]
            ret20_today = returns_20.loc[dt]
            exits = {
                symbol
                for symbol in current_holdings
                if pd.notna(today_prices.get(symbol))
                and (
                    today_prices.get(symbol) < ma20.get(symbol)
                    or ret20_today.get(symbol, 0.0) < -0.06
                    or vol_today.get(symbol, 0.0) > vol_20.loc[dt].quantile(0.90)
                )
            }
            for symbol in exits:
                rows.append({"date": dt_iso, "instrument": to_bigquant_instrument(symbol), "weight": 0.0})
            current_holdings -= exits

        if dt_compact not in rebalance_dates:
            continue

        if regime.loc[dt] == "weak":
            for symbol in list(current_holdings):
                rows.append({"date": dt_iso, "instrument": to_bigquant_instrument(symbol), "weight": 0.0})
            current_holdings.clear()
            continue

        total_weight = config.strong_total_weight if regime.loc[dt] == "strong" else config.neutral_total_weight
        candidates = score.loc[dt].dropna().sort_values(ascending=False).head(config.max_positions).index.tolist()
        if not candidates:
            continue
        per_position = min(config.max_position_weight, total_weight / len(candidates))
        target = set(candidates)
        for symbol in current_holdings - target:
            rows.append({"date": dt_iso, "instrument": to_bigquant_instrument(symbol), "weight": 0.0})
        for symbol in candidates:
            rows.append({"date": dt_iso, "instrument": to_bigquant_instrument(symbol), "weight": per_position})
        current_holdings = target

    signals = pd.DataFrame(rows, columns=["date", "instrument", "weight"])
    if signals.empty:
        return signals
    signals = signals.drop_duplicates(["date", "instrument"], keep="last").sort_values(["date", "instrument"])
    return signals.reset_index(drop=True)


def run_bigtrader(signals: pd.DataFrame, config: StrategyConfig):
    from bigquant import bigtrader

    if signals.empty:
        raise ValueError("No BigQuant weight signals generated; skip BigTrader run to avoid loading the full market.")

    # Trigger BigTrader lazy exports before accessing names.
    _ = bigtrader.run

    instruments = sorted(signals["instrument"].dropna().unique().tolist()) if not signals.empty else []

    def initialize(context):
        context.set_commission(bigtrader.PerOrder(buy_cost=0.0003, sell_cost=0.0013, min_cost=5.0))
        context.set_stock_t1(1)

    def handle_data(context, data):
        bigtrader.HandleDataLib.handle_data_weight_based(context, data)

    return bigtrader.run(
        instruments=instruments,
        start_date=iso_date(config.start_date),
        end_date=iso_date(config.end_date),
        data=signals,
        capital_base=config.initial_cash,
        initialize=initialize,
        handle_data=handle_data,
        benchmark=config.benchmark,
        order_price_field_buy="open",
        order_price_field_sell="open",
        volume_limit=1,
        render=None,
        report_output_path=False,
    )


def save_outputs(performance, signals: pd.DataFrame, universe: pd.DataFrame, config: StrategyConfig) -> None:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_csv(universe, config.output_dir / "universe.csv")
    atomic_write_csv(signals, config.output_dir / "bigquant_weight_signals.csv")

    raw_perf = performance.raw_perf
    if raw_perf is not None:
        atomic_write_csv(raw_perf.reset_index(drop=True), config.output_dir / "bigtrader_raw_perf.csv")

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "engine": "bigquant.bigtrader",
        "data_source": "bigquant.dai",
        "config": {
            **asdict(config),
            "env_file": str(config.env_file),
            "cache_dir": str(config.cache_dir),
            "prices_file": str(config.prices_file) if config.prices_file else None,
            "output_dir": str(config.output_dir),
        },
        "universe_count": len(universe),
        "signal_rows": len(signals),
        "traded_instruments": int(signals["instrument"].nunique()) if not signals.empty else 0,
        "summary": performance.summary,
    }
    atomic_write_text(config.output_dir / "bigtrader_summary.json", json.dumps(summary, ensure_ascii=False, indent=2, default=str))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the BigQuant-only small-account strategy.")
    parser.add_argument("--start-date", default="2025-07-05", help="Formal backtest start date.")
    parser.add_argument("--end-date", default="2026-07-06", help="Backtest end date.")
    parser.add_argument("--warmup-start-date", default="2025-01-07", help="Warmup start date for indicators.")
    parser.add_argument("--initial-cash", type=float, default=100000.0)
    parser.add_argument("--max-positions", type=int, default=2)
    parser.add_argument("--max-position-weight", type=float, default=0.34)
    parser.add_argument("--strong-total-weight", type=float, default=0.68)
    parser.add_argument("--neutral-total-weight", type=float, default=0.34)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--prices-file", default=str(DEFAULT_PRICES_FILE), help="Existing BigQuant local prices_long.csv to reuse before querying DAI.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--datasource", default=DEFAULT_DATASOURCE)
    parser.add_argument("--benchmark", default="000300.SH")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)
    config = StrategyConfig(
        start_date=iso_date(args.start_date),
        end_date=iso_date(args.end_date),
        warmup_start_date=iso_date(args.warmup_start_date),
        initial_cash=args.initial_cash,
        max_positions=args.max_positions,
        max_position_weight=args.max_position_weight,
        strong_total_weight=args.strong_total_weight,
        neutral_total_weight=args.neutral_total_weight,
        batch_size=args.batch_size,
        limit=args.limit,
        env_file=Path(args.env_file),
        cache_dir=Path(args.cache_dir),
        prices_file=Path(args.prices_file) if args.prices_file else None,
        output_dir=Path(args.output_dir),
        datasource=args.datasource,
        benchmark=args.benchmark,
        use_cache=not args.no_cache,
    )
    init_bigquant(config.env_file)
    universe = load_universe(config)
    bars = load_or_fetch_bars(config, universe)
    signals = build_weight_signals(bars, config)
    LOGGER.info("Prepared BigQuant signals: rows=%s instruments=%s", len(signals), signals["instrument"].nunique() if not signals.empty else 0)
    performance = run_bigtrader(signals, config)
    save_outputs(performance, signals, universe, config)
    print(json.dumps(performance.summary, ensure_ascii=False, indent=2, default=str))
    print(f"Output: {config.output_dir.resolve()}")


if __name__ == "__main__":
    main()
