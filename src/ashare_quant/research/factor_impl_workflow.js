export const meta = {
  name: 'factor-implementation',
  description: 'Implement mined factor specs into cand_*.py modules (5 clusters, parallel)',
  phases: [{ title: 'Implement', detail: 'one agent per cluster writes its module' }],
};

const GUIDE = `
You implement NOVEL A-share factors as a Python module. Work in repo d:/Chenqm/Personal_Strategy with the project venv at .venv/Scripts/python.exe.

# STEP 1 — Read these files first (do not guess the API)
- src/ashare_quant/research/cand_lib.py  (shared helpers + build_shared: the 'shared' dict contract)
- src/ashare_quant/research/cand_microstructure.py  (REFERENCE IMPLEMENTATION — mirror its style)
- data/research/factor_probe/mined_specs.json  (your factor specs: name, math_formula, data_inputs, direction)

# STEP 2 — Write src/ashare_quant/research/cand_<yourcluster>.py

Module signature (REQUIRED, exactly this):
\`\`\`
def compute(prices, args, shared) -> dict[str, "pd.DataFrame"]:
    ...
    return factors
\`\`\`

The 'shared' dict (precomputed ONCE by build_shared — read from it, do not recompute):
- shared["dates"]: list[pd.Timestamp] — the 84 monthly PIT signal dates (train+val)
- shared["close"], shared["high"], shared["low"], shared["raw_open"], shared["raw_close"],
  shared["volume"], shared["amount"], shared["turnover"], shared["up_limit"], shared["down_limit"]:
  FULL-DAILY wide DataFrames (index=date, columns=symbol). high/low may be None if unavailable.
- shared["returns"], shared["daily_residual"], shared["overnight"], shared["intraday"]: full-daily wide frames.
- shared["basic"][field]: MONTHLY-indexed wide frame for a daily_basic field. Fields: pe_ttm, pb, ps_ttm, dv_ttm, total_mv, circ_mv, turnover_rate, volume_ratio, total_share, float_share, free_share.
- shared["fina"][field]: MONTHLY-indexed wide frame (PIT latest report) for a fina_indicator field. Fields: roe, roic, roa, roe_waa, grossprofit_margin, netprofit_margin, debt_to_assets, assets_turn, ocf_to_debt, q_ocf_to_sales, q_sales_yoy, dt_netprofit_yoy, ocf_yoy, equity_yoy.
- shared["fina_hist"][field]: dict {date -> DataFrame[index=symbol, columns=['q0','q1','q2','q3']]} = last 4 PIT quarterly reports (q0 = most recent). Use for persistence/stability/comovement factors.

# TWO patterns for computing a factor (pick by cost)
A) CHEAP rolling stats (std/mean/max/skew/kurt/cov over a window): full-daily wide rolling.
   e.g. \`factors["x"] = -shared["returns"].rolling(60).std()\`
B) EXPENSIVE per-date stats (Hurst, entropy, runs test, variance ratio, anything not a pandas built-in):
   loop shared["dates"], slice the window, vectorize across symbols with numpy. Return a MONTHLY-indexed frame.
   Use cand_lib.per_date_stat(panel, dates, window, stat_fn) where stat_fn(window_slice_df) -> Series(symbol).
   For multi-panel stats (needs high+low etc.), write the date loop directly like _cs_spread_monthly in cand_microstructure.py.

For FINANCIAL factors: combine shared["fina"]/shared["basic"] monthly frames with vectorized arithmetic
   (they are already aligned date×symbol). e.g. accrual gap = shared["fina"]["ocf_yoy"] - shared["fina"]["dt_netprofit_yoy"].
For MULTI-QUARTER factors: loop dates, use shared["fina_hist"][field][dt] (a symbol×q0..q3 frame), compute per-date, build monthly frame.

# CONVENTIONS
- Each factor = wide DataFrame. Probe reads IC via .loc[dt] on BOTH full-daily and monthly-indexed frames.
- Implement the RAW statistic from math_formula. The probe computes SIGNED IC, which reveals the true direction — so you do NOT need to pre-flip sign perfectly. If confident, express HIGH=desirable; else leave raw and note it.
- Winsorization/standardization happens DOWNSTREAM in the composite — do NOT winsorize in the factor.
- PIT: rolling windows ending at t are fine; financial snapshots are already PIT-latest; NEVER use future data.
- DEPLOYABILITY: use ONLY shared fields (all pre-verified deployable on Ptrade). No new data sources.

# STEP 3 — Verify it imports (cheap, no data load):
\`\`\`
.venv/Scripts/python.exe -c "from ashare_quant.research.cand_<yourcluster> import compute; print('ok')"
\`\`\`
Fix any syntax/import error until it prints ok.

# RETURN (structured)
Return {module: "cand_<yourcluster>", factors: [{name, sign_choice, notes}], skipped: [names you deliberately dropped as duplicates and why]}.
Implement every assigned factor unless it truly duplicates another (then list in 'skipped').
`;

const CLUSTERS = [
  {
    file: 'cand_nonlinear',
    factors: ['hurst_rs_120','approx_entropy_60d','sample_entropy_60d','ret_entropy_60','sign_runs_z_60d','runs_z_60','var_ratio_1_20','realized_kurtosis_60','jump_ratio_60','idio_vol_share60','idio_rsquare_60','hurst_dev_120d','hurst_vol60','skew_kurt_lottery_60','kurtosis_lottery_60'],
    note: 'Nonlinear/statistical signatures (price-only, expensive stats). Dedup the obvious twins (pick ONE Hurst, ONE entropy, ONE runs-z, ONE kurtosis; list dups in skipped). For Hurst use the classic R/S rescaled-range over 120d. Entropy: discretize daily returns into sign buckets (or magnitude quintiles) and compute Shannon entropy. Runs test: Wald-Wolfowitz z on up/down days. Variance ratio: VR(k) = Var(k-period)/[k*Var(1)]. Idiosyncratic vol share: regress stock returns on cross-sectional median (shared["daily_residual"] is already the residual vs median); share = 1 - explained. All vectorized per-date across symbols.',
  },
  {
    file: 'cand_risk_tail',
    factors: ['cvar_tail60','crash_freq120','semidev_ratio60','downside_beta_ratio60','coskew_market60','recovery_speed120'],
    note: 'Downside risk & tail (price-only). CVaR: mean of worst-5% daily returns over 60d (NEGATIVE is bad → express so HIGH=desirable, i.e. factor = -(-CVaR) or just mean of lower-tail; note sign). crash_freq: fraction of days with return < -7% over 120d (low desirable). semidev_ratio: semideviation / total std (low desirable). downside_beta_ratio: beta on market-down days / beta all days (use cross-sectional median as market proxy via shared["daily_residual"]; market_return = returns.median(axis=1)). coskew_market: E[(r-mu)(rm-mum)^2] standardized (Harvey-Siddique). recovery_speed: how fast price recovers from trough (e.g. mean over 120d of (last/close.trough()-1) or days since trough). Use shared["returns"] and the cross-sectional median as market.',
  },
  {
    file: 'cand_flow_calendar',
    factors: ['smart_money_flow_40','up_down_volume_pressure_60','obv_price_confirm_60','turnover_accel_z_20','vp_exhaustion_60','limit_proximity_intensity_20','limit_up_reversal_5','overnight_intraday_gap_20','volpeak_ret_20','spring_seasonality_score'],
    note: 'Volume-price flow & A-share calendar (price+volume). All from shared close/volume/amount/turnover/up_limit/down_limit/raw_open. smart_money: correlation of volume with positive returns (or vol-weighted return). up_down_volume_pressure: vol on up days / vol on down days. OBV confirm: cumulative sign(ret)*volume vs price rank-corr (divergence = exhaustion). turnover_accel: rate of change of turnover (turnover.diff z). vp_exhaustion: rank corr of cumulative return vs cumulative log volume (negative divergence). limit_proximity: freq of days near up_limit/down_limit. limit_up_reversal: recent limit-up → reversal (5d). overnight_intraday: overnight gap (raw_open/close.shift(1)-1) vs intraday. volpeak_ret: return on highest-volume days. spring_seasonality: Jan/Feb dummy (note PIT; weak).',
  },
  {
    file: 'cand_fundamental',
    factors: ['accrual_cash_gap','margin_expansion_quality','gross_profitability_nm','gross_profitability_gp','cash_flow_margin','ocf_debt_coverage','too_fast_growth_penalty','equity_growth_penalty','magic_formula_ey_roa','quality_cheap_roic','dupont_operating_power','safe_growth_lowvol20','accrual_adjusted_roa','issuance_adj_value','gross_profit_book_yield','sustainable_growth_higgins','leverage_adj_quality','liquid_value_roll','net_issuance_12m','sustainable_growth_gap','empire_builder_penalty','sales_equity_growth_spread','overvalued_issuer','float_unlock_pressure_6m','piotroski_accrual_fscore'],
    note: 'Fundamental factors using shared["fina"] and shared["basic"] (MONTHLY frames, latest PIT report). Combine via vectorized arithmetic. Examples: accrual_cash_gap = fina.ocf_yoy - fina.dt_netprofit_yoy; margin_expansion = fina.dt_netprofit_yoy - fina.q_sales_yoy; magic_formula = (1/pe_ttm) * roa (use basic.pe_ttm, fina.roa); dupont = netprofit_margin * assets_turn; net_issuance = -basic.total_share.pct_change over the monthly index (or -float_share ratio change). For safe_growth_lowvol20 = q_sales_yoy * (-returns.rolling(20).std()) aligned at monthly dates. Implement all; note sign choices.',
  },
  {
    file: 'cand_fundamental_history',
    factors: ['accrual_gap_persistence_4q','earnings_growth_vol_8q','roe_stability_8q','cash_earnings_comovement_8q','debt_accumulation_4q','asset_turn_momentum_4q','profitability_persistence_hurst'],
    note: 'Multi-quarter factors using shared["fina_hist"][field] = {date -> DataFrame[symbol, q0..q3]} (last 4 quarters; q0=most recent). Loop shared["dates"]; for each dt use the q0..q3 frame. NOTE: only 4 quarters are prebuilt — for factors needing 8 quarters, use the 4 available (std/mean over q0..q3) and note the reduction, OR compute a proxy from the monthly frames (e.g. earnings_growth_vol = -std of dt_netprofit_yoy requires history; approximate with the available 4 q). accrual_gap_persistence = mean over quarters of (ocf_yoy - dt_netprofit_yoy). roe_stability = -std(roe over quarters). cash_earnings_comovement = corr across quarters of dt_netprofit_yoy vs ocf_yoy (per symbol, over the 4 quarters — small sample, use with care). debt_accumulation = debt_to_assets.q0 - debt_to_assets.q3 (change over 4q). profitability_persistence_hurst: approximate Hurst of roe across quarters is unreliable with 4 points — instead use roe autocorr-like proxy or skip (list in skipped if infeasible). Build monthly-indexed output frames.',
  },
];

phase('Implement');
log(`Implementing ${CLUSTERS.reduce((n, c) => n + c.factors.length, 0)} factor specs across ${CLUSTERS.length} clusters...`);

const results = await parallel(
  CLUSTERS.map((c) => () =>
    agent(
      `${GUIDE}\n\n# YOUR CLUSTER: ${c.file}\nImplement these factors (read each one's math_formula from mined_specs.json): ${JSON.stringify(c.factors)}\n${c.note}\n\nWrite src/ashare_quant/research/${c.file}.py, verify it imports with the venv python, and return the structured summary.`,
      { label: `impl:${c.file}`, phase: 'Implement', agentType: 'general-purpose', schema: {
        type: 'object',
        properties: {
          module: { type: 'string' },
          factors: { type: 'array', items: { type: 'object', properties: { name: { type: 'string' }, sign_choice: { type: 'string' }, notes: { type: 'string' } }, required: ['name'] } },
          skipped: { type: 'array', items: { type: 'string' } },
        },
        required: ['module', 'factors'],
      } }
    )
  )
);

const done = results.filter(Boolean);
let nf = 0;
for (const r of done) nf += (r.factors || []).length;
log(`Implemented ~${nf} factors across ${done.length} modules`);
return done;
