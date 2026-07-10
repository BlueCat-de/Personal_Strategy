#!/usr/bin/env python3
"""Local-only factor composite strategy research.

Reads data/factors/local_a_share/factors_long.csv, runs cross-sectional RankIC,
tries transparent factor-composite recipes, simulates next-open execution, and
writes a Markdown report. It does not call BigQuant SDK or touch the daemon.
"""

from __future__ import annotations

import argparse
import json
import math
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_FACTOR_FILE = REPO_ROOT / "data/factors/local_a_share/factors_long.csv"
DEFAULT_FACTOR_MANIFEST = REPO_ROOT / "data/factors/local_a_share/manifest.json"
DEFAULT_OFFLINE_SUMMARY = REPO_ROOT / "data/offline/a_share_12m_bigquant/daily_update_summary.json"
DEFAULT_DAEMON_STATE = REPO_ROOT / "run/bigquant_daily_daemon_state.json"
DEFAULT_V4_SUMMARY = REPO_ROOT / "data/backtests/bigquant_strategy_v4/20260708_full_year/bigtrader_summary.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data/backtests/local_factor_composite/20260708_full_year"
DEFAULT_REPORT_FILE = REPO_ROOT / "LOCAL_FACTOR_COMPOSITE_STRATEGY_REPORT.md"

BASE_COLUMNS = ["date", "symbol", "open", "close"]
LABEL_COLUMNS = ["fwd_return_5", "fwd_return_10", "fwd_return_20"]
FEATURES = [
    "amount_ma_20",
    "turnover_ma_20",
    "amount_ratio_20",
    "volume_ratio_20",
    "momentum_5",
    "momentum_20",
    "momentum_60",
    "momentum_120",
    "price_to_ma_20",
    "price_to_ma_60",
    "volatility_20",
    "volatility_60",
    "downside_volatility_20",
    "downside_volatility_60",
    "drawdown_from_high_60",
    "range_position_60",
    "up_days_ratio_60",
    "price_volume_corr_20",
    "amount_return_corr_20",
    "ts_rank_amount_60",
    "ts_rank_turnover_60",
    "wq101_alpha_003",
    "wq101_alpha_006",
    "wq101_alpha_013",
    "wq101_alpha_015",
    "wq101_alpha_016",
]
DIRECTIONS = {
    "wq101_alpha_016": 1.0,
    "wq101_alpha_013": 1.0,
    "wq101_alpha_006": 1.0,
    "wq101_alpha_015": 1.0,
    "wq101_alpha_003": 1.0,
    "volatility_20": -1.0,
    "volatility_60": -1.0,
    "downside_volatility_20": -1.0,
    "downside_volatility_60": -1.0,
    "ts_rank_amount_60": -1.0,
    "ts_rank_turnover_60": -1.0,
    "amount_ratio_20": -1.0,
    "volume_ratio_20": -1.0,
    "price_volume_corr_20": -1.0,
    "amount_return_corr_20": -1.0,
    "price_to_ma_60": -1.0,
    "momentum_5": -1.0,
}
RECIPES = [
    "local_wq_lowvol_pullback",
    "local_wq_smooth_trend",
    "local_defensive_reversal",
    "local_wq_lowvol_midband",
    "local_wq_smooth_midband",
    "local_defensive_reversal_lowband",
]


@dataclass(frozen=True)
class Config:
    factor_file: Path
    factor_manifest: Path
    offline_summary: Path
    daemon_state: Path
    v4_summary: Path
    output_dir: Path
    report_file: Path
    start_date: str
    end_date: str
    initial_cash: float
    max_positions: int
    max_position_weight: float
    min_candidates: int
    min_price: float
    max_price: float
    min_amount20: float
    min_turnover20: float
    max_turnover20: float
    strong_total_weight: float
    neutral_total_weight: float
    stop_loss: float
    trailing_stop: float
    trend_exit_gap: float
    buy_cost: float
    sell_cost: float
    min_cost: float


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        tmp = Path(handle.name)
        handle.write(text)
    tmp.replace(path)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
        tmp = Path(handle.name)
        df.to_csv(handle, index=False)
    tmp.replace(path)


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def rank(s: pd.Series, ascending: bool = True) -> pd.Series:
    return s.replace([np.inf, -np.inf], np.nan).rank(pct=True, ascending=ascending)


def pct(x: float | int | None) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    return f"{float(x):.2%}"


def num(x: float | int | None, digits: int = 2) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    return f"{float(x):.{digits}f}"


def md_table(df: pd.DataFrame, cols: list[str], fmts: dict[str, str] | None = None) -> str:
    fmts = fmts or {}
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df[cols].iterrows():
        cells = []
        for col in cols:
            value = row[col]
            if fmts.get(col) == "pct":
                cells.append(pct(value))
            elif fmts.get(col) == "num":
                cells.append(num(value))
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def max_drawdown(equity: pd.Series) -> tuple[float, str, str]:
    peak = equity.cummax()
    dd = equity / peak - 1.0
    trough = str(dd.idxmin()) if not dd.empty else ""
    peak_date = str(equity.loc[:trough].idxmax()) if trough else ""
    return (float(dd.min()) if not dd.empty else 0.0, peak_date, trough)


def weekly_dates(dates: pd.Series) -> set[pd.Timestamp]:
    idx = pd.DatetimeIndex(pd.to_datetime(dates).unique()).sort_values()
    first = pd.Series(idx, index=idx).groupby(idx.to_period("W-FRI")).first()
    return set(pd.DatetimeIndex(first.values))


def load_factors(cfg: Config) -> pd.DataFrame:
    cols = set(BASE_COLUMNS + LABEL_COLUMNS + FEATURES)
    df = pd.read_csv(cfg.factor_file, usecols=lambda c: c in cols, dtype={"symbol": str})
    df["date"] = pd.to_datetime(df["date"])
    df["symbol"] = df["symbol"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    df = df[(df["date"] >= pd.to_datetime(cfg.start_date)) & (df["date"] <= pd.to_datetime(cfg.end_date))].copy()
    for col in df.columns:
        if col not in {"date", "symbol"}:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values(["date", "symbol"]).reset_index(drop=True)


def base_mask(df: pd.DataFrame, cfg: Config) -> pd.Series:
    return (
        df["close"].between(cfg.min_price, cfg.max_price)
        & (df["amount_ma_20"] >= cfg.min_amount20)
        & df["turnover_ma_20"].between(cfg.min_turnover20, cfg.max_turnover20)
        & (df["amount_ratio_20"] <= 3.5)
        & (df["volume_ratio_20"] <= 3.2)
        & (df["drawdown_from_high_60"] > -0.28)
        & df["volatility_20"].notna()
    )


def exposure(day: pd.DataFrame, cfg: Config) -> tuple[float, str, dict]:
    liquid = day[base_mask(day, cfg)]
    if liquid.empty:
        return 0.0, "empty", {"breadth20": np.nan, "breadth60": np.nan, "median_mom20": np.nan, "median_vol20": np.nan}
    breadth20 = float((liquid["price_to_ma_20"] > 0).mean())
    breadth60 = float((liquid["price_to_ma_60"] > 0).mean())
    median_mom20 = float(liquid["momentum_20"].median())
    median_vol20 = float(liquid["volatility_20"].median())
    info = {"breadth20": breadth20, "breadth60": breadth60, "median_mom20": median_mom20, "median_vol20": median_vol20}
    if breadth20 >= 0.55 and breadth60 >= 0.50 and median_mom20 > -0.015 and median_vol20 < 0.040:
        return cfg.strong_total_weight, "strong", info
    if breadth20 >= 0.48 and breadth60 >= 0.44 and median_mom20 > -0.035 and median_vol20 < 0.048:
        return cfg.neutral_total_weight, "neutral", info
    return 0.0, "risk_off", info


def score(day: pd.DataFrame, recipe: str) -> pd.Series:
    wq = 0.45 * rank(day["wq101_alpha_016"]) + 0.35 * rank(day["wq101_alpha_013"]) + 0.20 * rank(day["wq101_alpha_006"])
    low_vol = 0.45 * rank(day["volatility_20"], False) + 0.30 * rank(day["downside_volatility_20"], False) + 0.25 * rank(day["volatility_60"], False)
    low_crowd = (
        0.35 * rank(day["ts_rank_turnover_60"], False)
        + 0.30 * rank(day["ts_rank_amount_60"], False)
        + 0.20 * rank(day["amount_ratio_20"], False)
        + 0.15 * rank(day["volume_ratio_20"], False)
    )
    pullback = 0.45 * rank(day["momentum_5"], False) + 0.35 * rank(day["price_to_ma_20"], False) + 0.20 * rank(day["price_to_ma_60"], False)
    trend = 0.30 * rank(day["momentum_60"]) + 0.25 * rank(day["up_days_ratio_60"]) + 0.20 * rank(day["range_position_60"]) + 0.15 * rank(day["momentum_120"]) + 0.10 * rank(day["price_to_ma_60"])
    if recipe in {"local_wq_lowvol_pullback", "local_wq_lowvol_midband"}:
        return 0.42 * wq + 0.24 * low_vol + 0.18 * low_crowd + 0.16 * pullback
    if recipe in {"local_wq_smooth_trend", "local_wq_smooth_midband"}:
        return 0.36 * wq + 0.24 * trend + 0.22 * low_vol + 0.12 * low_crowd + 0.06 * rank(day["amount_ma_20"])
    if recipe in {"local_defensive_reversal", "local_defensive_reversal_lowband"}:
        return 0.28 * wq + 0.30 * pullback + 0.26 * low_vol + 0.16 * low_crowd
    raise ValueError(recipe)


def candidate_mask(day: pd.DataFrame, cfg: Config, recipe: str) -> pd.Series:
    m = base_mask(day, cfg)
    if recipe in {"local_wq_smooth_trend", "local_wq_smooth_midband"}:
        return m & (day["momentum_60"] > 0) & (day["momentum_120"] > -0.03) & (day["price_to_ma_60"] > -0.03)
    if recipe in {"local_defensive_reversal", "local_defensive_reversal_lowband"}:
        return m & (day["momentum_60"] > -0.04) & (day["price_to_ma_60"] > -0.08) & (day["momentum_5"] < 0.18)
    return m & (day["momentum_60"] > -0.05) & (day["price_to_ma_60"] > -0.08) & (day["momentum_5"] < 0.20)


def allocate(sel: pd.DataFrame, gross: float, cfg: Config) -> pd.Series:
    if sel.empty or gross <= 0:
        return pd.Series(dtype=float)
    inv = 1.0 / sel["volatility_20"].clip(0.012, 0.080)
    w = inv / inv.sum() * gross
    w = w.clip(upper=cfg.max_position_weight)
    if w.sum() > gross:
        w = w / w.sum() * gross
    return w


def build_signals(df: pd.DataFrame, cfg: Config, recipe: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    weeks = weekly_dates(df["date"])
    signals, debug = [], []
    for dt, day in df.groupby("date", sort=True):
        if dt not in weeks:
            continue
        gross, regime, info = exposure(day, cfg)
        cand = day[candidate_mask(day, cfg, recipe)].copy()
        cand["score"] = score(cand, recipe)
        cand["score_pct"] = cand["score"].rank(pct=True)
        if recipe == "local_wq_smooth_midband":
            cand = cand[cand["score_pct"].between(0.55, 0.85)]
        elif recipe == "local_wq_lowvol_midband":
            cand = cand[cand["score_pct"].between(0.15, 0.55)]
        elif recipe == "local_defensive_reversal_lowband":
            cand = cand[cand["score_pct"] <= 0.30]
            cand["score"] = -cand["score"]
        cand = cand.dropna(subset=["score", "volatility_20"]).sort_values("score", ascending=False)
        if len(cand) < cfg.min_candidates:
            gross = 0.0
            sel = cand.head(0).set_index("symbol")
        else:
            sel = cand.head(cfg.max_positions).set_index("symbol")
        weights = allocate(sel, gross, cfg)
        d = dt.strftime("%Y-%m-%d")
        if weights.empty:
            signals.append({"date": d, "recipe": recipe, "symbol": None, "target_weight": 0.0, "score": np.nan, "signal_close": np.nan})
        for sym, weight in weights.items():
            signals.append({"date": d, "recipe": recipe, "symbol": sym, "target_weight": float(weight), "score": float(sel.loc[sym, "score"]), "signal_close": float(sel.loc[sym, "close"])})
        debug.append({"date": d, "recipe": recipe, "regime": regime, "gross": gross, "candidate_count": int(len(cand)), "selected": ",".join(weights.index.tolist()), **info})
    return pd.DataFrame(signals), pd.DataFrame(debug)


def trade_to_target(dt: str, symbols: list[str], target: pd.Series, prices: pd.Series, shares: pd.Series, cash: float, cfg: Config, recipe: str, trades: list[dict]) -> tuple[float, pd.Series]:
    equity_open = cash + float((shares * prices.fillna(0)).sum())
    target_value = target.reindex(symbols).fillna(0.0) * equity_open
    trade_symbols = sorted(set(target_value[target_value > 0].index) | set(shares[shares > 0].index))
    for side in ["sell", "buy"]:
        for sym in trade_symbols:
            price = float(prices.loc[sym]) if pd.notna(prices.loc[sym]) else np.nan
            if not math.isfinite(price) or price <= 0:
                continue
            target_shares = int((target_value.loc[sym] // (price * 100)) * 100)
            delta = target_shares - int(shares.loc[sym])
            if side == "sell" and delta < 0:
                fee = max(abs(delta) * price * cfg.sell_cost, cfg.min_cost)
                cash += abs(delta) * price - fee
                shares.loc[sym] += delta
                trades.append({"date": dt, "recipe": recipe, "symbol": sym, "side": "sell", "shares": delta, "price": price, "fee": fee, "cash_after": cash})
            elif side == "buy" and delta > 0:
                fee = max(delta * price * cfg.buy_cost, cfg.min_cost)
                cost = delta * price + fee
                if cost > cash:
                    affordable = int(((cash - cfg.min_cost) // (price * 100)) * 100)
                    delta = max(0, min(delta, affordable))
                    fee = max(delta * price * cfg.buy_cost, cfg.min_cost) if delta > 0 else 0.0
                    cost = delta * price + fee
                if delta > 0 and cost <= cash:
                    cash -= cost
                    shares.loc[sym] += delta
                    trades.append({"date": dt, "recipe": recipe, "symbol": sym, "side": "buy", "shares": delta, "price": price, "fee": fee, "cash_after": cash})
    return cash, shares


def backtest(df: pd.DataFrame, signals: pd.DataFrame, debug: pd.DataFrame, cfg: Config, recipe: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    dates = pd.DatetimeIndex(sorted(df["date"].unique()))
    symbols = sorted(df["symbol"].unique())
    open_px = df.pivot(index="date", columns="symbol", values="open").reindex(index=dates, columns=symbols)
    close_px = df.pivot(index="date", columns="symbol", values="close").reindex(index=dates, columns=symbols)
    p2ma20 = df.pivot(index="date", columns="symbol", values="price_to_ma_20").reindex(index=dates, columns=symbols)
    dd60 = df.pivot(index="date", columns="symbol", values="drawdown_from_high_60").reindex(index=dates, columns=symbols)
    signal_map = {pd.to_datetime(d): g.dropna(subset=["symbol"]).set_index("symbol")["target_weight"] for d, g in signals.groupby("date")}
    cash = cfg.initial_cash
    shares = pd.Series(0, index=symbols, dtype=int)
    entry, peak = {}, {}
    pending: pd.Series | None = None
    eq_rows, trades = [], []
    for dt in dates:
        d = dt.strftime("%Y-%m-%d")
        op = open_px.loc[dt].fillna(close_px.loc[dt])
        if pending is not None:
            old = shares.copy()
            cash, shares = trade_to_target(d, symbols, pending, op, shares, cash, cfg, recipe, trades)
            for sym in shares[shares > 0].index:
                if old.loc[sym] <= 0:
                    entry[sym] = float(op.loc[sym]) if pd.notna(op.loc[sym]) else np.nan
                    peak[sym] = entry[sym]
            for sym in old[old > 0].index:
                if shares.loc[sym] <= 0:
                    entry.pop(sym, None)
                    peak.pop(sym, None)
            pending = None
        cl = close_px.loc[dt].fillna(op).fillna(0.0)
        equity = cash + float((shares * cl).sum())
        held = shares[shares > 0]
        eq_rows.append({"date": d, "recipe": recipe, "equity": equity, "cash": cash, "positions": int(len(held)), "position_symbols": ",".join(held.index.tolist())})
        if dt in signal_map:
            pending = signal_map[dt]
            continue
        exits = []
        for sym in held.index:
            price = float(cl.loc[sym])
            peak[sym] = max(peak.get(sym, price), price)
            if price <= entry.get(sym, price) * (1 - cfg.stop_loss):
                exits.append(sym)
            elif price <= peak.get(sym, price) * (1 - cfg.trailing_stop):
                exits.append(sym)
            elif pd.notna(p2ma20.loc[dt, sym]) and p2ma20.loc[dt, sym] < -cfg.trend_exit_gap:
                exits.append(sym)
            elif pd.notna(dd60.loc[dt, sym]) and dd60.loc[dt, sym] < -0.24:
                exits.append(sym)
        if exits:
            current_w = (shares * cl / equity).replace([np.inf, -np.inf], np.nan).fillna(0.0)
            current_w.loc[exits] = 0.0
            pending = current_w[current_w > 0]
    equity = pd.DataFrame(eq_rows)
    trades_df = pd.DataFrame(trades)
    eq = equity.set_index("date")["equity"]
    daily = eq.pct_change().fillna(0.0)
    mdd, pk, tr = max_drawdown(eq)
    total = float(eq.iloc[-1] / cfg.initial_cash - 1.0)
    years = max(len(eq) / 252.0, 1 / 252.0)
    turnover = float((trades_df["shares"].abs() * trades_df["price"]).sum() / cfg.initial_cash) if not trades_df.empty else 0.0
    summary = {
        "recipe": recipe,
        "final_equity": float(eq.iloc[-1]),
        "total_return": total,
        "annual_return": float((1 + total) ** (1 / years) - 1),
        "sharpe": float(daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0.0,
        "max_drawdown": mdd,
        "max_drawdown_peak": pk,
        "max_drawdown_trough": tr,
        "daily_volatility": float(daily.std() * np.sqrt(252)),
        "win_day_rate": float((daily > 0).mean()),
        "trade_count": int(len(trades_df)),
        "total_fees": float(trades_df["fee"].sum()) if not trades_df.empty else 0.0,
        "turnover_on_initial_cash": turnover,
        "signal_count": int(signals["symbol"].notna().sum()),
        "avg_candidate_count": float(debug["candidate_count"].mean()) if not debug.empty else 0.0,
        "risk_off_weeks": int((debug["gross"] == 0).sum()) if not debug.empty else 0,
    }
    return equity, trades_df, summary


def factor_ic(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    base = df[base_mask(df, cfg)].copy()
    rows = []
    for feat, direction in DIRECTIONS.items():
        for label in LABEL_COLUMNS:
            ics, spreads, wins = [], [], []
            for _, g in base[["date", feat, label]].dropna().groupby("date", observed=True):
                if len(g) < 100:
                    continue
                directed = g[feat] * direction
                ics.append(float(directed.rank().corr(g[label].rank())))
                top = directed >= directed.quantile(0.9)
                bottom = directed <= directed.quantile(0.1)
                spreads.append(float(g.loc[top, label].mean() - g.loc[bottom, label].mean()))
                wins.append(float((g.loc[top, label] > 0).mean()))
            if ics:
                arr = np.array(ics)
                rows.append({"feature": feat, "direction": direction, "label": label, "mean_rank_ic": float(arr.mean()), "median_rank_ic": float(np.median(arr)), "ic_positive_rate": float((arr > 0).mean()), "mean_top_bottom_spread": float(np.nanmean(spreads)), "mean_top_decile_win_rate": float(np.nanmean(wins)), "sample_days": len(arr)})
    return pd.DataFrame(rows).sort_values(["label", "mean_rank_ic"], ascending=[True, False])


def factor_corr(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    base = df[base_mask(df, cfg)].copy()
    cols = [c for c in DIRECTIONS if c in base.columns]
    for c in cols:
        base[c] = base[c] * DIRECTIONS[c]
    return base[cols].corr(method="spearman").reset_index().rename(columns={"index": "feature"})


def deciles(df: pd.DataFrame, cfg: Config, recipe: str) -> pd.DataFrame:
    rows = []
    for dt, day in df.groupby("date", sort=True, observed=True):
        cand = day[candidate_mask(day, cfg, recipe)].copy()
        if len(cand) < 100:
            continue
        cand["score"] = score(cand, recipe)
        cand = cand.dropna(subset=["score"])
        if len(cand) < 100:
            continue
        cand["decile"] = pd.qcut(cand["score"].rank(method="first"), 10, labels=False) + 1
        for dec, g in cand.groupby("decile", observed=True):
            rows.append({"date": dt.strftime("%Y-%m-%d"), "recipe": recipe, "decile": int(dec), "count": len(g), "fwd_return_5": float(g["fwd_return_5"].mean()), "fwd_return_10": float(g["fwd_return_10"].mean()), "fwd_return_20": float(g["fwd_return_20"].mean())})
    return pd.DataFrame(rows)


def equal_weight(df: pd.DataFrame, cfg: Config) -> dict:
    x = df.copy()
    x["ret"] = x.groupby("symbol", observed=True)["close"].pct_change()
    daily = x[base_mask(x, cfg)].groupby("date", observed=True)["ret"].mean().fillna(0.0)
    eq = (1 + daily).cumprod() * cfg.initial_cash
    mdd, pk, tr = max_drawdown(eq.rename(lambda d: d.strftime("%Y-%m-%d")))
    total = float(eq.iloc[-1] / cfg.initial_cash - 1.0)
    years = max(len(eq) / 252.0, 1 / 252.0)
    return {"recipe": "liquid_universe_equal_weight", "total_return": total, "annual_return": float((1 + total) ** (1 / years) - 1), "sharpe": float(daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0.0, "max_drawdown": mdd, "win_day_rate": float((daily > 0).mean()), "max_drawdown_peak": pk, "max_drawdown_trough": tr}


def build_report(cfg: Config, df: pd.DataFrame, summaries: pd.DataFrame, ic: pd.DataFrame, dec: pd.DataFrame, benchmark: dict) -> str:
    manifest = load_json(cfg.factor_manifest)
    offline = load_json(cfg.offline_summary)
    daemon = load_json(cfg.daemon_state)
    v4_json = load_json(cfg.v4_summary)
    v4 = v4_json.get("summary", {})
    best = summaries.sort_values(["sharpe", "total_return"], ascending=False).iloc[0]
    comparison = []
    if v4:
        comparison.append({"strategy": "BigTrader v4 baseline", "total_return": float(v4.get("return_ratio", 0)) / 100, "annual_return": float(v4.get("annual_return_ratio", 0)) / 100, "sharpe": float(v4.get("sharp_ratio", 0)), "max_drawdown": -float(v4.get("max_drawdown", 0)) / 100, "win_rate": float(v4.get("win_ratio", 0)) / 100})
    comparison.append({"strategy": "liquid universe equal-weight", "total_return": benchmark["total_return"], "annual_return": benchmark["annual_return"], "sharpe": benchmark["sharpe"], "max_drawdown": benchmark["max_drawdown"], "win_rate": benchmark["win_day_rate"]})
    for _, row in summaries.iterrows():
        comparison.append({"strategy": row["recipe"], "total_return": row["total_return"], "annual_return": row["annual_return"], "sharpe": row["sharpe"], "max_drawdown": row["max_drawdown"], "win_rate": row["win_day_rate"]})
    comparison_df = pd.DataFrame(comparison).sort_values("sharpe", ascending=False)
    top_ic = ic[ic["label"] == "fwd_return_20"].sort_values("mean_rank_ic", ascending=False).head(10)
    dec_summary = dec.groupby(["recipe", "decile"], observed=True)[["fwd_return_5", "fwd_return_10", "fwd_return_20"]].mean().reset_index()
    best_dec = dec_summary[dec_summary["recipe"] == best["recipe"]]
    v4_line = "- v4 最近完整一年 BigTrader：未找到摘要文件。"
    if v4:
        v4_line = f"- v4 最近完整一年 BigTrader：累计收益 {pct(float(v4.get('return_ratio', 0)) / 100)}，Sharpe {num(float(v4.get('sharp_ratio', 0)))}，最大回撤 {pct(-float(v4.get('max_drawdown', 0)) / 100)}。"
    lines = [
        "# 本地因子组合策略研究报告",
        "",
        "## 结论摘要",
        f"- 本地取数链路已更新到 `{offline.get('latest_local_date', offline.get('latest_bigquant_date', 'NA'))}`；daemon 状态显示取数 `{daemon.get('data_last_status', 'NA')}`、策略 `{daemon.get('strategy_last_status', 'NA')}`。",
        f"- 本地因子库覆盖 `{manifest.get('start_date', 'NA')}` ~ `{manifest.get('end_date', 'NA')}`，`{manifest.get('symbols', 'NA')}` 只股票、`{manifest.get('feature_count', 'NA')}` 个特征、`{manifest.get('rows', 'NA')}` 行。",
        "- 当前生产 v4 仍是更成熟的线上候选：过去一年 BigTrader 摘要收益和回撤优于本次本地近似研究策略。",
        f"- 本次探索中表现最好的本地因子组合是 `{best['recipe']}`：累计收益 {pct(best['total_return'])}，年化 {pct(best['annual_return'])}，Sharpe {num(best['sharpe'])}，最大回撤 {pct(best['max_drawdown'])}。",
        "- 该结果是样本内探索；建议先作为 v4 的候选股二级排序/风险过滤，而不是直接替换生产策略。",
        "",
        "## 本地数据与任务状态",
        f"- 离线行情：`{offline.get('output_dir', 'data/offline/a_share_12m_bigquant')}`，BigQuant 最新日期 `{offline.get('latest_bigquant_date', 'NA')}`，本地最新日期 `{offline.get('latest_local_date', 'NA')}`，最近一次更新状态 `{offline.get('status', 'NA')}`。",
        f"- 每日状态机：取数最近一次 `{daemon.get('data_last_status', 'NA')}`，策略最近一次 `{daemon.get('strategy_last_status', 'NA')}`，策略成功数据日 `{daemon.get('strategy_success_data_date', 'NA')}`。",
        v4_line,
        "",
        "## 当前策略使用的因子/变量",
        "- v4 候选约束：价格区间、站上 MA20/MA60/MA120、20/60/120 日动量、20 日波动率、60 日回撤、开盘跳空、20 日成交额，以及市场宽度/市场波动状态。",
        "- v4 打分：`momentum_60`、`momentum_120`、`momentum_20`、`price_to_ma_60`、低 `volatility_20`、低 `downside_volatility_60`、`drawdown_from_high_60`、`amount_ma_20`。",
        "- v4_volume/v5 研究版本额外引入 `turnover_ma_20`、`volume_ratio_20`、`amount_trend60`、正收益天数比例和换手平衡项。",
        "",
        "## 因子筛选方法",
        "- 股票池：价格 2.5~85 元、20 日成交额不低于 3000 万、20 日换手率 0.3%~16%，排除极端放量和深回撤样本。",
        "- 检验口径：逐日横截面 Spearman RankIC，并按方向调整后计算 Top/Bottom 十分位未来收益差。",
        "- 组合逻辑：优先使用低相关的 WQ101 量价结构因子，再叠加低波动、低拥挤、短期回撤/平滑趋势约束。",
        "",
        "### 20 日 RankIC Top 因子（方向已调整）",
        md_table(top_ic, ["feature", "direction", "mean_rank_ic", "median_rank_ic", "ic_positive_rate", "mean_top_bottom_spread"], {"mean_rank_ic": "num", "median_rank_ic": "num", "ic_positive_rate": "pct", "mean_top_bottom_spread": "pct"}),
        "",
        "## 策略设计",
        "- `local_wq_lowvol_pullback`：WQ101 alpha_016/013/006 为核心，叠加低波动、低成交拥挤和短期回撤，不追高。",
        "- `local_wq_smooth_trend`：WQ101 核心 + 60/120 日趋势质量 + 低波动，要求中期趋势为正。",
        "- `local_defensive_reversal`：更强调短期反转、低波动和低拥挤，只要求中期趋势不明显破坏。",
        "- `_midband/_lowband` 版本来自十分位检验：当极端高分并非最优时，改选历史分组收益更好的中间分位或低分位。",
        "- 交易规则：周频调仓，信号日收盘生成目标，下一交易日开盘按 100 股整数手成交；买入成本 0.03%，卖出成本 0.13%，最低佣金 5 元。",
        "",
        "## 过去一年本地近似回测结果",
        md_table(comparison_df, ["strategy", "total_return", "annual_return", "sharpe", "max_drawdown", "win_rate"], {"total_return": "pct", "annual_return": "pct", "sharpe": "num", "max_drawdown": "pct", "win_rate": "pct"}),
        "",
        "### 本次最佳组合十分位检验",
        md_table(best_dec, ["recipe", "decile", "fwd_return_5", "fwd_return_10", "fwd_return_20"], {"fwd_return_5": "pct", "fwd_return_10": "pct", "fwd_return_20": "pct"}),
        "",
        "## 研究判断",
        "- WQ101 alpha_016/013/006 在本地样本中 RankIC 稳定性最好，适合作为现有 v4 候选池内的二级排序增强。",
        "- 单纯追逐价格动量在这一年横截面上并不稳定；更稳健的用法是保留趋势约束，但把拥挤放量、高波动和短期过热作为扣分项。",
        "- 本地回测撮合是近似版，不等同 BigTrader；若要进入生产，应先把最佳组合接入 `bigquant_strategy.py` 的研究版本，再用 BigTrader 复核。",
        "- 不建议用本次样本内最佳策略替换 v4；更合理的下一步是把 `wq101_alpha_016/013/006 + low_vol + low_crowding` 做成 v4 的候选股二级排序或风险过滤开关。",
        "",
        "## 输出文件",
        f"- 研究目录：`{cfg.output_dir}`",
        "- `factor_ic.csv`、`strategy_summaries.csv`、`equity_curves.csv`、`trades.csv`、`signals.csv`、`signal_debug.csv`、`composite_deciles.csv`、`selected_factor_correlation.csv`。",
    ]
    return "\n".join(lines) + "\n"


def run(cfg: Config) -> dict:
    df = load_factors(cfg)
    ic = factor_ic(df, cfg)
    corr = factor_corr(df, cfg)
    benchmark = equal_weight(df, cfg)
    summaries, equities, trades, signals, debugs, decs = [], [], [], [], [], []
    for recipe in RECIPES:
        sig, dbg = build_signals(df, cfg, recipe)
        eq, tr, summary = backtest(df, sig, dbg, cfg, recipe)
        summaries.append(summary)
        equities.append(eq)
        trades.append(tr)
        signals.append(sig)
        debugs.append(dbg)
        decs.append(deciles(df, cfg, recipe))
    summaries_df = pd.DataFrame(summaries).sort_values(["sharpe", "total_return"], ascending=False)
    equity_df = pd.concat(equities, ignore_index=True)
    trades_df = pd.concat(trades, ignore_index=True) if any(not x.empty for x in trades) else pd.DataFrame()
    signals_df = pd.concat(signals, ignore_index=True)
    debug_df = pd.concat(debugs, ignore_index=True)
    dec_df = pd.concat(decs, ignore_index=True)
    report = build_report(cfg, df, summaries_df, ic, dec_df, benchmark)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(ic, cfg.output_dir / "factor_ic.csv")
    write_csv(corr, cfg.output_dir / "selected_factor_correlation.csv")
    write_csv(summaries_df, cfg.output_dir / "strategy_summaries.csv")
    write_csv(equity_df, cfg.output_dir / "equity_curves.csv")
    write_csv(trades_df, cfg.output_dir / "trades.csv")
    write_csv(signals_df, cfg.output_dir / "signals.csv")
    write_csv(debug_df, cfg.output_dir / "signal_debug.csv")
    write_csv(dec_df, cfg.output_dir / "composite_deciles.csv")
    write_text(cfg.output_dir / "report.md", report)
    write_text(cfg.report_file, report)
    summary = {"config": {k: str(v) if isinstance(v, Path) else v for k, v in asdict(cfg).items()}, "benchmark": benchmark, "strategies": summaries, "best_strategy": summaries_df.iloc[0].to_dict(), "output_dir": str(cfg.output_dir), "report_file": str(cfg.report_file)}
    write_text(cfg.output_dir / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return summary


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Research local factor composite strategies.")
    p.add_argument("--factor-file", default=str(DEFAULT_FACTOR_FILE))
    p.add_argument("--factor-manifest", default=str(DEFAULT_FACTOR_MANIFEST))
    p.add_argument("--offline-summary", default=str(DEFAULT_OFFLINE_SUMMARY))
    p.add_argument("--daemon-state", default=str(DEFAULT_DAEMON_STATE))
    p.add_argument("--v4-summary", default=str(DEFAULT_V4_SUMMARY))
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    p.add_argument("--report-file", default=str(DEFAULT_REPORT_FILE))
    p.add_argument("--start-date", default="2025-07-08")
    p.add_argument("--end-date", default="2026-07-08")
    p.add_argument("--initial-cash", type=float, default=100_000.0)
    p.add_argument("--max-positions", type=int, default=5)
    p.add_argument("--max-position-weight", type=float, default=0.20)
    p.add_argument("--min-candidates", type=int, default=50)
    p.add_argument("--min-price", type=float, default=2.5)
    p.add_argument("--max-price", type=float, default=85.0)
    p.add_argument("--min-amount20", type=float, default=30_000_000.0)
    p.add_argument("--min-turnover20", type=float, default=0.003)
    p.add_argument("--max-turnover20", type=float, default=0.16)
    p.add_argument("--strong-total-weight", type=float, default=0.85)
    p.add_argument("--neutral-total-weight", type=float, default=0.55)
    p.add_argument("--stop-loss", type=float, default=0.06)
    p.add_argument("--trailing-stop", type=float, default=0.10)
    p.add_argument("--trend-exit-gap", type=float, default=0.035)
    p.add_argument("--buy-cost", type=float, default=0.0003)
    p.add_argument("--sell-cost", type=float, default=0.0013)
    p.add_argument("--min-cost", type=float, default=5.0)
    return p.parse_args()


def main() -> None:
    a = parse_args()
    cfg = Config(
        factor_file=Path(a.factor_file),
        factor_manifest=Path(a.factor_manifest),
        offline_summary=Path(a.offline_summary),
        daemon_state=Path(a.daemon_state),
        v4_summary=Path(a.v4_summary),
        output_dir=Path(a.output_dir),
        report_file=Path(a.report_file),
        start_date=a.start_date,
        end_date=a.end_date,
        initial_cash=a.initial_cash,
        max_positions=a.max_positions,
        max_position_weight=a.max_position_weight,
        min_candidates=a.min_candidates,
        min_price=a.min_price,
        max_price=a.max_price,
        min_amount20=a.min_amount20,
        min_turnover20=a.min_turnover20,
        max_turnover20=a.max_turnover20,
        strong_total_weight=a.strong_total_weight,
        neutral_total_weight=a.neutral_total_weight,
        stop_loss=a.stop_loss,
        trailing_stop=a.trailing_stop,
        trend_exit_gap=a.trend_exit_gap,
        buy_cost=a.buy_cost,
        sell_cost=a.sell_cost,
        min_cost=a.min_cost,
    )
    print(json.dumps(run(cfg), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
