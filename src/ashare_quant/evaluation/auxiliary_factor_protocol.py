"""Auxiliary / risk-overlay factor admission protocol — the second channel, parallel to IC.

WHY: the stock-factor layer admits factors by standalone rank IC
(`research/factor_ic_analysis.py:select_stable_factors`, IC >= 0.015 in both dev halves).
That structurally EXCLUDES auxiliary/risk factors — signals with near-zero standalone IC
that improve the PORTFOLIO when layered on top (vol-targeting, drawdown ladders, beta caps,
regime gates). This module is the parallel admission channel for that auxiliary layer: it
judges a candidate overlay on its MARGINAL effect on a frozen alpha composite's risk/return
profile — NOT on IC, and NOT by Sharpe-fishing.

Two layers, fully isolated:
  alpha layer     — "does this factor predict returns?"    (cross-sectional rank IC)
  auxiliary layer — "does this overlay improve the book?"  (portfolio-level delta metric)

Admitted auxiliary signals enter position-sizing / exposure construction — NEVER the stock
ranking score. The alpha layer stays clean.

Convention: the caller supplies `overlaid_returns` (the overlay already constructed and
applied to a frozen baseline). This module JUDGES; overlay construction is per-type and
stays with the caller. Standard applicators (apply_vol_target / apply_dd_ladder /
apply_beta_cap) are provided for the 3 known overlay types, mirroring the validated
reference implementation in `strategies/etf_rotation_vt18.py:apply_risk_overlays` (reimplemented
cleanly here — no import from the private strategies layer).

5 pillars (see docs/SOP_STRATEGY_DEVELOPMENT.md §3.7):
  1. frozen baseline (commit + csv sha256 recorded for audit)
  2. mechanism-specific delta metric (declared by candidate, not Sharpe-fishing)
  3. walk-forward direction-consistency >= 0.75 AND bootstrap p <= 0.05 (magnitude-aware)
  4. separate complexity budget (N_aux; feeds DSR as N_final = N_alpha + N_aux)
  5. layered output (sizing, not ranking)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from ashare_quant.evaluation import metrics as M
from ashare_quant.evaluation.walk_forward import rolling_test_windows

PPY = 252

# ── Mechanism registry ────────────────────────────────────────────────────
# For each declared mechanism: the primary delta-metric keys it is judged on, and whether
# an INCREASE in that metric is an improvement (True) or a decrease is (False).
# The walk-forward direction test + bootstrap p use the FIRST entry (the headline metric).
_MECH_PRIMARY: dict[str, tuple[tuple[str, bool], ...]] = {
    "vol":         (("delta_vol", False), ("delta_maxdd", True)),
    "dd":          (("delta_maxdd", True), ("delta_calmar", True)),
    "beta":        (("delta_beta", False),),
    "combined":    (("delta_calmar", True),),
    "tail_hedge":  (("delta_cvar", True), ("delta_tail", True)),
    "regime_gate": (("delta_maxdd", True),),
}

# Sharpe-preservation rail is reject-only. Positive delta_sharpe MUST NOT admit/rank.
# tail_hedge gets a looser rail (hedges legitimately cost Sharpe).
_SHARPE_RAIL = {  # (abs_floor, rel_frac_of_baseline_sharpe) — delta_sharpe must be >= weaker
    "default":    (-0.10, 0.15),
    "tail_hedge": (-0.15, None),
}

# Hybrid thresholds: (abs, rel_frac). "Weaker bar wins" (the easier-to-clear threshold in the
# improvement direction) so the protocol stays sound across baselines of very different scale.
DEFAULT_THRESHOLDS: dict[str, object] = {
    "delta_vol":    (-0.01, 0.05),   # <= max(-0.01, -0.05*base_vol)
    "delta_maxdd":  (0.03, 0.05),    # >= min(0.03, 0.05*|base_maxdd|)  [weaker for increase-good]
    "delta_calmar": (0.05, None),
    "delta_beta":   (-0.05, None),
    "delta_cvar":   (0.02, None),
    "delta_tail":   (0.10, None),
    # collateral-damage rail (universal, all mechanisms): don't break an undeclared dimension
    "collateral_maxdd_floor": -0.01,   # delta_maxdd >= -0.01
    "collateral_vol_cap":     0.005,   # delta_vol   <= +0.005
    # walk-forward gate
    "wf_consistency_min": 0.75,
    "wf_bootstrap_p_max": 0.05,
    "wf_bootstrap_n": 2000,
    "wf_seed": 20260809,
}


# ── Metrics helpers ───────────────────────────────────────────────────────

def _equity(returns: pd.Series) -> pd.Series:
    return (1.0 + returns.fillna(0.0)).cumprod()


def _portfolio_metrics(returns: pd.Series, benchmark_returns: pd.Series | None = None) -> dict[str, float]:
    """Risk/return metrics on a daily return series (reuses evaluation.metrics)."""
    r = returns.dropna()
    if len(r) < 2:
        return {k: 0.0 for k in ("sharpe", "vol", "ann_ret", "maxdd", "calmar", "cvar", "tail", "beta")}
    eq = _equity(r)
    out = {
        "sharpe": M.sharpe_ratio(r),
        "vol": M.annualized_volatility(r),
        "ann_ret": M.annualized_return(r),
        "maxdd": float(M.max_drawdown(eq)[0]),
        "calmar": M.calmar_ratio(r, eq),
        "cvar": M.cvar(r, 0.95),
        "tail": M.tail_ratio(r),
    }
    if benchmark_returns is not None:
        bmr = benchmark_returns.reindex(r.index).fillna(0.0)
        var_bm = float(np.var(bmr.values))
        out["beta"] = float(np.cov(r.values, bmr.values)[0, 1] / var_bm) if var_bm > 1e-12 else 0.0
    else:
        out["beta"] = float("nan")
    return out


def _delta(base: dict[str, float], cand: dict[str, float]) -> dict[str, float]:
    keys = ("sharpe", "vol", "ann_ret", "maxdd", "calmar", "cvar", "tail", "beta")
    return {f"delta_{k}": float(cand[k] - base[k]) for k in keys}


def _clears(key: str, delta_val: float, base_val: float, thr: dict) -> tuple[bool, float]:
    """Hybrid threshold check. Returns (passed, effective_threshold_used)."""
    abs_t, rel_frac = thr[key]
    if rel_frac is not None and not math.isnan(base_val):
        rel = rel_frac * abs(base_val) * (1.0 if abs_t >= 0 else -1.0)
    else:
        rel = None
    if abs_t >= 0:  # increase is improvement -> weaker bar = the smaller (easier) threshold
        eff = abs_t if rel is None else min(abs_t, rel)
        return delta_val >= eff, eff
    # decrease is improvement -> weaker bar = the larger (closer to 0, easier) threshold
    eff = abs_t if rel is None else max(abs_t, rel)
    return delta_val <= eff, eff


def _bootstrap_p(per_window_deltas: np.ndarray, increase_good: bool, thr: dict) -> float:
    """One-sided bootstrap p-value: P(mean(resample) has NO improvement | the window series).

    increase_good=True  -> improvement = positive mean; p = P(mean <= 0).
    increase_good=False -> improvement = negative mean; p = P(mean >= 0).
    """
    n = thr["wf_bootstrap_n"]
    rng = np.random.default_rng(thr["wf_seed"])
    arr = per_window_deltas[np.isfinite(per_window_deltas)]
    if len(arr) < 3:
        return 1.0
    resamples = rng.choice(arr, size=(n, len(arr)), replace=True)
    means = resamples.mean(axis=1)
    if increase_good:
        return float((means <= 0).mean())
    return float((means >= 0).mean())


# ── Standard overlay applicators (public reimplementation, mirror etf_rotation_vt18) ──

def apply_vol_target(returns: pd.Series, target_vol: float = 0.15, lookback: int = 20) -> pd.Series:
    """Moreira-Muir vol-targeting: scale gross by min(1, target/realized), with the VT
    lever-up to 1.5x before the final 0-1 clip (preserved EXACTLY from the reference impl)."""
    r = returns.copy()
    rv = r.rolling(lookback).std() * math.sqrt(PPY)
    scale = (target_vol / rv.replace(0, np.nan)).clip(0, 1.5).fillna(1.0)
    return r * scale.clip(0.0, 1.0)  # final exposure in [0,1] (1.5 lever-up clipped back)


def apply_dd_ladder(
    returns: pd.Series,
    ladder: list[tuple[float, float]] | None = None,
) -> pd.Series:
    """López de Prado drawdown ladder: scale exposure down as portfolio drawdown deepens."""
    if ladder is None:
        ladder = [(-0.10, 0.8), (-0.15, 0.5), (-0.20, 0.0)]
    r = returns.copy()
    eq = (1 + r).cumprod()
    dd = eq / eq.cummax() - 1.0
    scale = pd.Series(1.0, index=r.index)
    for thr_val, scl in sorted(ladder, reverse=True):
        scale = scale.where(dd > thr_val, scl)
    return r * scale.clip(0, 1.0)


def apply_beta_cap(
    returns: pd.Series,
    benchmark_returns: pd.Series,
    cap: float = 1.3,
    lookback: int = 60,
) -> pd.Series:
    """Scale gross so rolling portfolio beta stays <= cap (closed-form cov/var beta)."""
    r = returns.copy()
    bmr = benchmark_returns.reindex(r.index).fillna(0.0)
    cov = r.rolling(lookback).cov(bmr)
    var = bmr.rolling(lookback).var()
    beta = (cov / var).fillna(1.0)
    scale = (cap / beta).where(beta > 0.1, 1.0).clip(0, 1.0)
    return r * scale


# ── factor -> scaler helper (for cross-sectional edge factors, e.g. xmath) ──────────

def factor_to_scaler(
    factor_panel: pd.DataFrame,
    aggregation: str = "dispersion",   # 'dispersion' (std) or 'mean_vw' (cap-weighted mean)
    transform: str = "zclip",          # 'zclip' (dispersion->de-risk) or 'quantile' (mean->de-risk)
    train_window: tuple[str, str] | None = None,
    cap_weights: pd.DataFrame | None = None,
    clip_max: float = 1.0,
) -> pd.Series:
    """Convert a cross-sectional factor panel to a portfolio-level de-risk scaler.

    Two low-degrees-of-freedom conversions (caller pre-registers which one; the tool never
    takes a stance on which is 'right'):
      dispersion -> regime de-risk (fits vol-regime / anomaly factors): high cross-sectional
                    spread => systemic regime => scale down.
      mean_vw    -> directional crash (fits downside-asymmetry / order-flow factors): elevated
                    cap-weighted mean => scale down.

    Calibration quantiles/z-score are fit on `train_window` only (no look-ahead). Returns a
    daily exposure scaler in (0, clip_max] aligned to the factor_panel index.
    """
    fp = factor_panel.copy()
    if aggregation == "dispersion":
        signal = fp.std(axis=1)
    elif aggregation == "mean_vw":
        if cap_weights is None:
            signal = fp.mean(axis=1)
        else:
            w = cap_weights.reindex(columns=fp.columns).fillna(0.0)
            w = w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
            signal = (fp * w).sum(axis=1)
    else:
        raise ValueError(f"unknown aggregation {aggregation!r}")

    if train_window is not None:
        sig_train = signal.loc[train_window[0]:train_window[1]]
    else:
        sig_train = signal
    sig_train = sig_train.dropna()

    if transform == "zclip":
        mu, sd = sig_train.mean(), (sig_train.std(ddof=1) or 1.0)
        z = ((signal - mu) / sd).clip(lower=0.0)          # only positive z -> de-risk
        scale = 1.0 / (1.0 + z)                           # monotone down; no threshold df
    elif transform == "quantile":
        q_hi = sig_train.quantile(0.90)
        q_lo = sig_train.quantile(0.50)
        excess = ((signal - q_lo) / ((q_hi - q_lo) or 1.0)).clip(0, 1)
        scale = 1.0 - excess                             # 1 at median -> lower at high signal
    else:
        raise ValueError(f"unknown transform {transform!r}")
    return scale.fillna(1.0).clip(0.0, clip_max)


# ── The judge ─────────────────────────────────────────────────────────────

def evaluate_candidate(
    baseline_returns: pd.Series,
    overlaid_returns: pd.Series,
    declared_mechanism: str,
    name: str,
    benchmark_returns: pd.Series | None = None,
    baseline_commit: str = "",
    baseline_csv_sha256: str = "",
    param_train_window: tuple[str, str] | None = None,
    thresholds: dict | None = None,
    trial_log_path: str | Path | None = None,
    wf_window_filter: list[bool] | None = None,   # restrict WF to "active" windows (regime/tail)
    null_matched_scaler: bool = False,
) -> dict:
    """Judge one overlay candidate against a frozen baseline on its declared mechanism.

    Returns a verdict dict with delta metrics, walk-forward stats, ADMIT/REJECT + reason.
    Stateless: no global counter. Append-only trial log if `trial_log_path` given.
    """
    if declared_mechanism not in _MECH_PRIMARY:
        raise ValueError(f"unknown mechanism {declared_mechanism!r}; choose from {list(_MECH_PRIMARY)}")
    thr = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    base_m = _portfolio_metrics(baseline_returns, benchmark_returns)
    cand_m = _portfolio_metrics(overlaid_returns, benchmark_returns)
    delta = _delta(base_m, cand_m)

    # --- walk-forward direction-consistency + bootstrap p on the headline metric ---
    head_key, increase_good = _MECH_PRIMARY[declared_mechanism][0]
    metric_key = head_key.replace("delta_", "")
    windows = rolling_test_windows(baseline_returns.index, test_years=2.0, step_years=1.0)
    per_win_delta: list[float] = []
    for ws, we in windows:
        try:
            mb = _portfolio_metrics(baseline_returns.loc[ws:we], benchmark_returns.loc[ws:we] if benchmark_returns is not None else None)
            mo = _portfolio_metrics(overlaid_returns.loc[ws:we], benchmark_returns.loc[ws:we] if benchmark_returns is not None else None)
        except Exception:
            continue
        per_win_delta.append(mo[metric_key] - mb[metric_key])
    per_win_arr = np.array(per_win_delta, dtype=float)
    if wf_window_filter is not None and len(wf_window_filter) == len(per_win_arr):
        per_win_arr = per_win_arr[np.array(wf_window_filter)]
    n_win = len(per_win_arr)
    wf_consist = float(np.mean(per_win_arr > 0)) if increase_good and n_win else float(np.mean(per_win_arr < 0)) if n_win else 0.0
    wf_p = _bootstrap_p(per_win_arr, increase_good, thr)

    # --- mechanism-specific threshold clears ---
    clears = {k: _clears(k, delta[k], base_m[k.replace("delta_", "")], thr) for k, _ in _MECH_PRIMARY[declared_mechanism]}
    mech_clear = all(passed for passed, _ in clears.values())

    # --- Sharpe reject-only rail ---
    sr_abs, sr_rel = _SHARPE_RAIL.get(declared_mechanism, _SHARPE_RAIL["default"])
    sr_eff = sr_abs if sr_rel is None else max(sr_abs, -sr_rel * base_m["sharpe"])  # weaker bar (decrease=good -> max)
    sharpe_ok = delta["delta_sharpe"] >= sr_eff

    # --- collateral-damage rail (universal) ---
    collateral_ok = (delta["delta_maxdd"] >= thr["collateral_maxdd_floor"]) and (delta["delta_vol"] <= thr["collateral_vol_cap"])

    # --- walk-forward gate ---
    wf_ok = (wf_consist >= thr["wf_consistency_min"]) and (wf_p <= thr["wf_bootstrap_p_max"])

    # --- matched-scale null (optional): candidate must beat a dumb constant-exposure scaler ---
    matched_beat = True
    if null_matched_scaler:
        with np.errstate(divide="ignore", invalid="ignore"):
            implied = (overlaid_returns / baseline_returns).replace([np.inf, -np.inf], np.nan)
        mean_exp = float(implied.dropna().mean())
        if math.isnan(mean_exp) or mean_exp <= 0:
            mean_exp = 1.0
        matched_ret = baseline_returns * mean_exp
        matched_m = _portfolio_metrics(matched_ret, benchmark_returns)
        cand_metric = cand_m[metric_key]
        matched_metric = matched_m[metric_key]
        matched_beat = (cand_metric >= matched_metric) if increase_good else (cand_metric <= matched_metric)

    verdict = "ADMIT" if (mech_clear and sharpe_ok and collateral_ok and wf_ok and matched_beat) else "REJECT"
    reasons = []
    if not mech_clear: reasons.append(f"mechanism thresholds not cleared ({ {k: round(v,4) for k,(p,v) in clears.items() if not p} })")
    if not sharpe_ok:  reasons.append(f"Sharpe rail violated (delta_sharpe {delta['delta_sharpe']:+.3f} < {sr_eff:+.3f})")
    if not collateral_ok: reasons.append(f"collateral damage (delta_maxdd {delta['delta_maxdd']:+.3f}, delta_vol {delta['delta_vol']:+.3f})")
    if not wf_ok:     reasons.append(f"WF gate (consist {wf_consist:.2f}, bootstrap_p {wf_p:.3f})")
    if not matched_beat: reasons.append("does not beat matched-scale null")

    result = {
        "candidate": name, "mechanism": declared_mechanism, "verdict": verdict,
        "reason": "; ".join(reasons) if reasons else "all pillars passed",
        **delta,
        "wf_consistency": wf_consist, "wf_bootstrap_p": wf_p, "n_windows": n_win,
        "collateral_rail_pass": bool(collateral_ok), "sharpe_rail_pass": bool(sharpe_ok),
        "mechanism_thresholds_used": {k: v for k, (_, v) in clears.items()},
        "baseline_commit": baseline_commit, "baseline_csv_sha256": baseline_csv_sha256,
        "param_train_window": f"{param_train_window[0]}..{param_train_window[1]}" if param_train_window else "",
        "n_aux_trials_used": 1,
    }
    if trial_log_path is not None:
        _append_trial_log(trial_log_path, result)
    return result


def evaluate_candidates(specs: list[dict], trial_log_path: str | Path | None = None) -> pd.DataFrame:
    """Run multiple candidates; returns a verdict DataFrame (each row = evaluate_candidate)."""
    rows = [evaluate_candidate(trial_log_path=trial_log_path, **s) for s in specs]
    return pd.DataFrame(rows)


# ── Trial log (append-only; admits AND rejects both count toward N_aux) ──

def _append_trial_log(path: str | Path, row: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    log_cols = [
        "candidate", "mechanism", "verdict", "baseline_commit", "baseline_csv_sha256",
        "param_train_window", "delta_vol", "delta_maxdd", "delta_sharpe", "delta_calmar",
        "delta_beta", "delta_cvar", "delta_tail", "wf_consistency", "wf_bootstrap_p", "n_windows",
    ]
    rec = {k: row.get(k, "") for k in log_cols}
    pd.DataFrame([rec]).to_csv(p, mode="a", header=not p.exists(), index=False)


def count_aux_trials(trial_log_path: str | Path, baseline_commit: str) -> int:
    """N_aux = distinct candidates tried for a given baseline (admits + rejects)."""
    p = Path(trial_log_path)
    if not p.exists():
        return 0
    df = pd.read_csv(p)
    if df.empty or "baseline_commit" not in df.columns:
        return 0
    sub = df[df["baseline_commit"].astype(str) == str(baseline_commit)]
    return int(sub["candidate"].nunique()) if "candidate" in sub.columns else 0


# ── self_test: 6-case discrimination regression ───────────────────────────

def self_test(baseline_csv: str | Path = "data/backtests/composite_alpha_v2_test/local_raw_perf.csv",
              print_table: bool = True) -> bool:
    """Re-run the validated 6-case table on v2 trainval (excludes test lockbox).

    Asserts the protocol discriminates: VT/DD/combined ADMIT; beta-cap/permuted-VT/random REJECT.
    Returns True iff all 6 verdicts match expectations. Run this after any threshold edit.
    """
    from ashare_quant.splits import TEST_START  # trainval = everything before the lockbox
    from ashare_quant.benchmark import fetch_benchmark

    raw = pd.read_csv(baseline_csv)
    raw["date"] = pd.to_datetime(raw["date"])
    base = raw.set_index("date")["returns"]
    base = base[base.index < pd.Timestamp(TEST_START)]   # lockbox-safe: trainval only
    bm_df = fetch_benchmark(str(base.index[0].date()), str(base.index[-1].date()))
    bm_df = bm_df.copy(); bm_df["date"] = pd.to_datetime(bm_df["date"])
    bm = bm_df.set_index("date")["benchmark_close"].pct_change().reindex(base.index).fillna(0.0)

    cases = [
        ("VT 15%",            apply_vol_target(base, 0.15),                            "vol",      "ADMIT"),
        ("DD-ladder",         apply_dd_ladder(base),                                   "dd",       "ADMIT"),
        ("VT+DD combined",    apply_dd_ladder(apply_vol_target(base, 0.15)),           "combined", "ADMIT"),
        ("Beta-cap 1.3",      apply_beta_cap(base, bm, 1.3),                           "beta",     "REJECT"),  # v2 beta ~0.87 < cap -> no-op
        ("NULL permuted-VT",  _permuted_vt(base, 0.15),                                "vol",      "REJECT"),
        ("NULL random",       _random_scaler(base),                                    "combined", "REJECT"),
    ]
    rows = []
    ok = True
    for name, oret, mech, expect in cases:
        r = evaluate_candidate(base, oret, mech, name, benchmark_returns=bm if mech == "beta" else None)
        r["expected"] = expect
        r["match"] = r["verdict"] == expect
        ok = ok and r["match"]
        rows.append(r)
    if print_table:
        df = pd.DataFrame(rows)[["candidate", "mechanism", "delta_sharpe", "delta_vol",
                                 "delta_maxdd", "wf_consistency", "wf_bootstrap_p", "verdict", "expected", "match"]]
        print(df.to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
        print(f"\n{'PASS' if ok else 'FAIL'}: {sum(r['match'] for r in rows)}/6 verdicts match expectation")
    return ok


def _permuted_vt(returns: pd.Series, target: float = 0.15, lookback: int = 20, seed: int = 42) -> pd.Series:
    """VT scale series with the vol-timing destroyed (time-shuffled) — the canonical null."""
    r = returns.copy()
    rv = r.rolling(lookback).std() * math.sqrt(PPY)
    scale = (target / rv.replace(0, np.nan)).clip(0, 1.5).fillna(1.0)
    rng = np.random.default_rng(seed)
    scale_perm = pd.Series(rng.permutation(scale.values), index=scale.index)
    return r * scale_perm


def _random_scaler(returns: pd.Series, seed: int = 7) -> pd.Series:
    rng = np.random.default_rng(seed)
    scale = pd.Series(rng.uniform(0.6, 1.0, len(returns)), index=returns.index)
    return returns * scale


# ── CLI ───────────────────────────────────────────────────────────────────

def _parse_overlay(spec: str, base: pd.Series, bm: pd.Series) -> tuple[pd.Series, str]:
    """Parse 'vt:target=0.15' / 'dd:ladder=default' / 'beta:cap=1.3' / 'combined'."""
    spec = spec.strip()
    if spec.startswith("vt"):
        target = 0.15
        for tok in spec.split(":", 1)[1].split(",") if ":" in spec else []:
            if tok.startswith("target="): target = float(tok.split("=")[1])
        return apply_vol_target(base, target), "vol"
    if spec.startswith("dd"):
        return apply_dd_ladder(base), "dd"
    if spec.startswith("beta"):
        cap = 1.3
        for tok in spec.split(":", 1)[1].split(",") if ":" in spec else []:
            if tok.startswith("cap="): cap = float(tok.split("=")[1])
        return apply_beta_cap(base, bm, cap), "beta"
    if spec.startswith("combined"):
        return apply_dd_ladder(apply_vol_target(base, 0.15)), "combined"
    raise ValueError(f"unknown overlay spec {spec!r}; use vt|dd|beta|combined[:key=val,...]")


def _main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--baseline", type=Path, default=None, help="path to baseline local_raw_perf.csv")
    p.add_argument("--baseline-commit", default="", help="git commit hash of the frozen baseline (audit)")
    p.add_argument("--mechanism", default="", help="declared mechanism (inferred from --overlay if omitted)")
    p.add_argument("--overlay", default="", help="vt|dd|beta|combined[:key=val,...] (omit only with --self-test)")
    p.add_argument("--param-train-window", default="", help="train window the overlay params were selected on, e.g. 2015-01-01:2021-12-31")
    p.add_argument("--trial-log", type=Path, default=Path("data/research/auxiliary_protocol/trial_log.csv"))
    p.add_argument("--null-matched-scaler", action="store_true", help="require candidate to beat a constant-exposure null")
    p.add_argument("--include-test", action="store_true", help="judge on test lockbox too (default: trainval only)")
    p.add_argument("--benchmark", type=Path, default=None, help="optional benchmark raw_perf for beta mechanism")
    p.add_argument("--self-test", action="store_true", help="run the 6-case discrimination self-test and exit")
    args = p.parse_args()

    if args.self_test:
        ok = self_test()
        raise SystemExit(0 if ok else 1)

    if args.baseline is None or not args.overlay:
        p.error("--baseline and --overlay are required (unless --self-test)")

    raw = pd.read_csv(args.baseline)
    raw["date"] = pd.to_datetime(raw["date"])
    base = raw.set_index("date")["returns"]
    if not args.include_test:
        from ashare_quant.splits import TEST_START
        base = base[base.index < pd.Timestamp(TEST_START)]   # lockbox-safe by default

    bm = pd.Series(0.0, index=base.index)
    if args.benchmark:
        bdf = pd.read_csv(args.benchmark); bdf["date"] = pd.to_datetime(bdf["date"])
        bm = bdf.set_index("date")["benchmark_close"].pct_change().reindex(base.index).fillna(0.0)

    overlaid, inferred_mech = _parse_overlay(args.overlay, base, bm)
    mech = args.mechanism or inferred_mech

    sha = hashlib.sha256(args.baseline.read_bytes()).hexdigest()[:16]
    pw = tuple(args.param_train_window.split(":")) if ":" in args.param_train_window else None

    res = evaluate_candidate(
        baseline_returns=base, overlaid_returns=overlaid, declared_mechanism=mech,
        name=args.overlay, benchmark_returns=bm if mech == "beta" else None,
        baseline_commit=args.baseline_commit, baseline_csv_sha256=sha, param_train_window=pw,
        trial_log_path=args.trial_log, null_matched_scaler=args.null_matched_scaler,
    )
    print(f"\n=== auxiliary factor protocol verdict ===")
    print(f"candidate : {res['candidate']}")
    print(f"mechanism : {res['mechanism']}")
    print(f"verdict   : {res['verdict']}   ({res['reason']})")
    print(f"delta     : Sharpe {res['delta_sharpe']:+.3f}  vol {res['delta_vol']:+.3f}  "
          f"MaxDD {res['delta_maxdd']:+.3f}  Calmar {res['delta_calmar']:+.3f}  beta {res['delta_beta']:+.3f}")
    print(f"walk-fwd  : consist {res['wf_consistency']:.2f}  bootstrap_p {res['wf_bootstrap_p']:.3f}  ({res['n_windows']} windows)")
    print(f"rails     : sharpe {'ok' if res['sharpe_rail_pass'] else 'FAIL'}  collateral {'ok' if res['collateral_rail_pass'] else 'FAIL'}")
    print(f"trial log : {args.trial_log}  (baseline_commit={args.baseline_commit or '<unset>'})")


if __name__ == "__main__":
    _main()
