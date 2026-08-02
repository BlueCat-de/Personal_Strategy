"""Statistical significance testing (dimension B of the evaluation framework).

Implements PSR, DSR, Haircut Sharpe, PBO (CSCV), MinTRL — all without scipy.
Uses math.erf for normal CDF and numpy for bootstrap.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

PPY = 252


def _norm_cdf(x: float) -> float:
    """Standard normal CDF without scipy."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam approximation, accuracy ~1e-9)."""
    if p <= 0:
        return -float("inf")
    if p >= 1:
        return float("inf")
    # Coefficients
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= phigh:
        q = p - 0.5
        r = q*q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = math.sqrt(-2 * math.log(1-p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


# ── PSR / DSR / Haircut ───────────────────────────────────────────────────

def probabilistic_sharpe_ratio(
    observed_sr: float,
    benchmark_sr: float,
    n_observations: int,
    skew: float,
    kurtosis: float,  # NOT excess (use excess + 3)
) -> float:
    """PSR: probability that true Sharpe exceeds benchmark_sr.

    Bailey & López de Prado (2012). Returns a probability in [0, 1].
    Higher = more confident the Sharpe is real.

    Args:
        observed_sr: observed annualized Sharpe ratio.
        benchmark_sr: benchmark Sharpe to beat (often 0).
        n_observations: number of daily return observations.
        skew: return skewness.
        kurtosis: return kurtosis (NOT excess — use excess_kurtosis + 3).
    """
    if n_observations < 2:
        return 0.0
    sr_diff = (observed_sr - benchmark_sr) * math.sqrt(n_observations - 1)
    denom = math.sqrt(
        1.0 - skew * observed_sr / math.sqrt(PPY)
        + (kurtosis - 1) / 4.0 * (observed_sr / PPY) ** 2 * PPY
    )
    if denom <= 0:
        return 0.0
    z = sr_diff / denom / math.sqrt(PPY)
    return _norm_cdf(z)


def deflated_sharpe_ratio(
    observed_sr: float,
    n_trials: int,
    n_observations: int,
    skew: float,
    excess_kurt: float,
) -> float:
    """DSR: probability that true Sharpe exceeds the expected max of N trials.

    Bailey & López de Prado (2014). Corrects PSR for multiple testing.
    Returns a probability in [0, 1]. Should be >= 0.95 to trust the Sharpe.

    Args:
        observed_sr: observed annualized Sharpe ratio.
        n_trials: number of independent strategy variations tried.
        n_observations: number of daily return observations.
        skew: return skewness.
        excess_kurt: EXCESS kurtosis.
    """
    sr0 = _expected_max_sharpe(n_trials, n_observations)
    kurtosis = excess_kurt + 3.0  # convert excess to raw
    return probabilistic_sharpe_ratio(observed_sr, sr0, n_observations, skew, kurtosis)


def _expected_max_sharpe(n_trials: int, n_observations: int) -> float:
    """Expected maximum Sharpe ratio under the null (true SR = 0) for N trials.

    Bailey & López de Prado: SR0 ≈ sqrt(2 ln(N)) * sigma_sharpe
    where sigma_sharpe = sqrt(1/T) and T = n_observations / PPY (in years).
    """
    if n_trials <= 1:
        return 0.0
    t_years = max(n_observations / PPY, 1.0 / PPY)
    sigma_sharpe = 1.0 / math.sqrt(t_years)
    euler_mascheroni = 0.5772156649
    z = math.sqrt(2.0 * math.log(n_trials))
    sr0 = sigma_sharpe * (z - (math.log(math.pi) + euler_mascheroni) / (2.0 * z))
    return float(sr0)


def haircut_sharpe_ratio(
    observed_sr: float,
    n_trials: int,
    n_observations: int,
    skew: float,
    excess_kurt: float,
) -> dict:
    """Haircut Sharpe: how much of the observed Sharpe survives after multiple-testing correction.

    Returns the haircut fraction (0 = no haircut, 1 = fully deflated).
    """
    # PSR with benchmark=0 gives baseline confidence
    psr0 = probabilistic_sharpe_ratio(
        observed_sr, 0.0, n_observations, skew, excess_kurt + 3.0
    )
    # DSR
    dsr = deflated_sharpe_ratio(observed_sr, n_trials, n_observations, skew, excess_kurt)
    # Find the SR that gives DSR = 0.95 (binary search)
    lo, hi = 0.0, observed_sr
    for _ in range(50):
        mid = (lo + hi) / 2.0
        d = deflated_sharpe_ratio(mid, n_trials, n_observations, skew, excess_kurt)
        if d < 0.95:
            lo = mid
        else:
            hi = mid
    sr_cutoff = hi
    haircut_pct = float((observed_sr - sr_cutoff) / observed_sr) if observed_sr > 0 else 0.0
    return {
        "observed_sr": float(observed_sr),
        "psr": float(psr0),
        "dsr": float(dsr),
        "sr_at_dsr95": float(sr_cutoff),
        "haircut_pct": haircut_pct,
        "n_trials": int(n_trials),
        "verdict": "PASS" if dsr >= 0.95 else "FAIL",
    }


# ── PBO ───────────────────────────────────────────────────────────────────
# Two flavors:
#  - pbo_block_bootstrap (single strategy): a PROXY. Splits ONE return series into
#    2*n_blocks contiguous blocks, bootstrap-assigns half as IS / half as OOS, and
#    asks whether the IS-best block underperforms the OOS median. This is NOT the
#    textbook Bailey CSCV (which needs N strategies). Kept for backward-compat as
#    `probability_of_backtest_overfitting`.
#  - pbo_cscv (true CSCV): accepts an (N_strategies × T) returns matrix and ranks
#    the IS-optimal strategy across the OOS panel — the actual combinatorial CV.


def pbo_block_bootstrap(
    returns: pd.Series,
    n_blocks: int = 8,
    n_bootstrap: int = 1000,
    seed: int | None = None,
) -> dict:
    """Single-strategy PBO PROXY via block-bootstrap (NOT true CSCV).

    For a single strategy path (not multiple strategies), we use a block-bootstrap
    approach: split the return series into 2*n_blocks blocks, randomly assign half as
    IS and half as OOS, compute IS-Sharpe-rank vs OOS-performance.

    PBO = fraction of bootstrap iterations where IS-best block underperforms in OOS.

    Caveat: this measures one strategy's internal IS/OOS consistency, not the
    combinatorial overfitting across many trial strategies. For the latter use
    `pbo_cscv` with an N-strategy returns matrix.
    """
    if seed is not None:
        rng = np.random.default_rng(seed)
    else:
        rng = np.random.default_rng()

    clean = returns.dropna().values
    n = len(clean)
    total_blocks = 2 * n_blocks
    block_size = n // total_blocks
    if block_size < 20:
        return {"pbo": float("nan"), "n_bootstrap": 0, "verdict": "INSUFFICIENT_DATA"}

    # Pre-split into blocks
    usable = clean[:block_size * total_blocks]
    blocks = usable.reshape(total_blocks, block_size)

    pbo_count = 0
    for _ in range(n_bootstrap):
        # Random partition: pick n_blocks blocks as IS, rest as OOS
        perm = rng.permutation(total_blocks)
        is_idx = perm[:n_blocks]
        oos_idx = perm[n_blocks:]

        # Compute Sharpe for each IS block
        is_sharpes = np.array([
            blocks[i].mean() / blocks[i].std(ddof=1) * math.sqrt(PPY)
            if blocks[i].std(ddof=1) > 0 else 0.0
            for i in is_idx
        ])
        # Best IS block
        best_is_pos = np.argmax(is_sharpes)
        best_block = is_idx[best_is_pos]

        # Compute Sharpe for each OOS block
        oos_sharpes = np.array([
            blocks[i].mean() / blocks[i].std(ddof=1) * math.sqrt(PPY)
            if blocks[i].std(ddof=1) > 0 else 0.0
            for i in oos_idx
        ])

        # Where does the IS-best block's return rank in OOS?
        # Use the IS-best block's actual OOS counterpart: compute its mean return
        # in the OOS blocks that follow it temporally
        # Simpler: rank the IS-best block's performance among OOS blocks
        best_is_return = blocks[best_block].mean()
        oos_median = np.median([blocks[i].mean() for i in oos_idx])

        if best_is_return < oos_median:
            pbo_count += 1

    pbo = pbo_count / n_bootstrap
    return {
        "pbo": float(pbo),
        "n_bootstrap": int(n_bootstrap),
        "n_blocks": int(total_blocks),
        "block_size": int(block_size),
        "verdict": "OK" if pbo < 0.5 else "OVERFIT_SUSPECTED",
    }


# Backward-compatible alias (external callers may still use this name).
probability_of_backtest_overfitting = pbo_block_bootstrap


def pbo_cscv(
    returns_matrix: pd.DataFrame,
    n_blocks: int = 8,
    n_bootstrap: int = 1000,
    seed: int | None = None,
) -> dict:
    """True Combinatorial Symmetric Cross-Validation PBO (Bailey & López de Prado).

    `returns_matrix`: DataFrame shape (T × N_strategies) — each column is one trial
    strategy's daily returns. The series is split into 2*n_blocks contiguous blocks;
    each bootstrap iteration randomly assigns half the blocks to IS and half to OOS
    (symmetric). The IS-optimal strategy (highest IS Sharpe) is located, then ranked
    by its OOS Sharpe across all N strategies. PBO = fraction of iterations where the
    IS-optimal strategy ranks BELOW the OOS median (i.e. IS winner does not generalize).

    Unlike `pbo_block_bootstrap`, this captures overfitting from selecting among MANY
    trial strategies — the actual question "did trying N strategies fool us?"
    """
    rng = np.random.default_rng(seed) if seed is not None else np.random.default_rng()
    mat = returns_matrix.dropna(how="all").values
    n_strategies = mat.shape[1]
    if n_strategies < 2:
        raise ValueError("pbo_cscv needs >= 2 strategy columns; for a single strategy use pbo_block_bootstrap")
    n_obs = mat.shape[0]
    total_blocks = 2 * n_blocks
    block_size = n_obs // total_blocks
    if block_size < 20:
        return {"pbo": float("nan"), "n_bootstrap": 0, "verdict": "INSUFFICIENT_DATA"}

    usable = mat[: block_size * total_blocks]
    # reshape to (total_blocks, block_size, N_strategies)
    blocks = usable.reshape(total_blocks, block_size, n_strategies)

    def _sharpe(arr):
        s = arr.std(ddof=1)
        return arr.mean() / s * math.sqrt(PPY) if s > 0 else 0.0

    pbo_count = 0
    for _ in range(n_bootstrap):
        perm = rng.permutation(total_blocks)
        is_idx, oos_idx = perm[:n_blocks], perm[n_blocks:]
        # IS-optimal strategy = highest aggregate IS Sharpe across its IS blocks
        is_agg = np.array([_sharpe(np.concatenate([blocks[i][:, j] for i in is_idx])) for j in range(n_strategies)])
        best_j = int(np.argmax(is_agg))
        # OOS Sharpe per strategy, rank the IS-winner
        oos_per = np.array([_sharpe(np.concatenate([blocks[i][:, j] for i in oos_idx])) for j in range(n_strategies)])
        rank = np.argsort(np.argsort(oos_per))[best_j]  # 0 = worst
        if rank < n_strategies / 2:  # below median
            pbo_count += 1

    pbo = pbo_count / n_bootstrap
    return {
        "pbo": float(pbo),
        "n_bootstrap": int(n_bootstrap),
        "n_strategies": int(n_strategies),
        "n_blocks": int(total_blocks),
        "verdict": "OK" if pbo < 0.5 else "OVERFIT_SUSPECTED",
    }


# ── MinTRL ────────────────────────────────────────────────────────────────

def min_trustworthy_return_length(
    observed_sr: float,
    threshold_sr: float = 0.0,
    confidence: float = 0.95,
) -> dict:
    """Minimum Track Record Length: how many days needed to trust observed_sr > threshold_sr.

    Bailey & López de Prado (2015). Returns the minimum number of daily observations.
    """
    z = _norm_ppf(confidence)
    sr_diff = observed_sr - threshold_sr
    if sr_diff <= 0:
        return {"min_trl_days": float("inf"), "min_trl_years": float("inf"),
                "current_days": 0, "verdict": "SR_BELOW_THRESHOLD"}
    # Approximate: MinTRL ≈ 1 + (z / SR_diff)^2 * PPY  (simplified, ignoring skew/kurt)
    min_trl = 1.0 + (z * math.sqrt(PPY) / sr_diff) ** 2
    return {
        "min_trl_days": float(min_trl),
        "min_trl_years": float(min_trl / PPY),
        "z_score": float(z),
        "verdict": "PASS" if min_trl <= PPY else "NEEDS_LONGER_TRACK",
    }


# ── Aggregate ─────────────────────────────────────────────────────────────

def evaluate(returns: pd.Series, n_trials: int = 1) -> dict:
    """Run all significance tests on a daily returns series.

    Args:
        returns: daily strategy returns.
        n_trials: estimated number of independent strategy variations tried.
    """
    from .metrics import sharpe_ratio, skewness, excess_kurtosis

    clean = returns.dropna()
    n = len(clean)
    sr = sharpe_ratio(clean)
    sk = skewness(clean)
    ek = excess_kurtosis(clean)

    return {
        "sharpe": float(sr),
        "skewness": float(sk),
        "excess_kurtosis": float(ek),
        "n_observations": int(n),
        "n_trials": int(n_trials),
        "psr": probabilistic_sharpe_ratio(sr, 0.0, n, sk, ek + 3.0),
        "dsr": deflated_sharpe_ratio(sr, n_trials, n, sk, ek),
        "haircut": haircut_sharpe_ratio(sr, n_trials, n, sk, ek),
        "pbo": pbo_block_bootstrap(clean),
        "min_trl": min_trustworthy_return_length(sr),
    }
