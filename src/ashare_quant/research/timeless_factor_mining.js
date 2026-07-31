export const meta = {
  name: 'timeless-factor-mining',
  description: 'Mine TIMELESS A-share regularities (low-risk/behavioral) at multiple horizons, judged by per-year IC stability',
  phases: [{ title: 'Mine', detail: '5 factor-researcher agents return specs for timeless behavioral primitives' }],
};

const SHARED = `
You are a senior A-share quant. Goal: design factors capturing TIMELESS, STRUCTURALLY PERSISTENT A-share regularities — anomalies rooted in the market's persistent retail-dominated, T+1, limit-up/down structure that have held across 2007–2026 (multiple bull/bear/policy regimes).

# HARD-FOUNDED DIAGNOSIS (from per-year IC stability 2007-2026)
The TIMELESS factors (train+val IR holds in test, <0.06 decay): low_residual_vol60 (0.72→0.68), MAX20 (0.69→0.64), SKEW60 (0.38→0.34), low_turnover (0.45→0.45), AMIHUD20 (0.38→0.34), reversal20 (0.49→0.37).
The REGIME-SPECIFIC factors (high decay, AVOID designing more like these): fundamental value/quality/growth (sales_equity_growth_spread 0.41→0.25, margin_expansion 0.23→0.07, gross_profit_book_yield 0.46→0.32).
LESSON: timeless alpha lives in LOW-RISK / BEHAVIORAL / MICROSTRUCTURE factors driven by persistent retail behavior. Fundamentals are cyclical.

# ALLOWED DATA (local Tushare cache; each MUST map to Ptrade — no SW industry, no statement LEVELS)
PRICE (prices dict, daily wide): close, open, high, low, raw_open, raw_close, volume, amount, turnover, up_limit, down_limit, is_listed/is_st/is_suspended
VALUATION (monthly PIT): pe_ttm, pb, ps_ttm, dv_ttm, total_mv, circ_mv, turnover_rate, volume_ratio, total_share, float_share, free_share
FINANCIAL (quarterly PIT, ratios only): roe, roic, roa, grossprofit_margin, netprofit_margin, debt_to_assets, assets_turn, ocf_to_debt, q_ocf_to_sales, q_sales_yoy, dt_netprofit_yoy, ocf_yoy, equity_yoy

# HORIZONS — design each factor at MULTIPLE lookbacks so we can find its natural frequency
The pipeline tests IC at 5/10/20/40-day forward returns (weekly/biweekly/monthly/bimonthly). For each factor, specify the lookback window(s); where the anomaly is horizon-sensitive, give 2-3 variants (e.g. reversal_5d, reversal_20d, reversal_60d).

# OUTPUT — 8-12 specs, each: {name, economic_thesis (WHY timeless in A-share retail structure), math_formula (precise, implementable from allowed fields, with lookback), data_inputs, ptrade_mapping, direction (high_desirable), expected_best_horizon (5/10/20/40), timelessness_rationale (why this survives regime shifts), pit_note}.

# QUALITY BAR
- Prefer factors tied to PERSISTENT microstructure/behavior (overreaction, lottery preference, herding, liquidity, limit-moves, attention).
- Vary lookbacks; behavioral anomalies often peak at short horizons (5-20d).
- Be concrete & implementable. Cite the anomaly literature (Jegadeesh-Titman reversal, Bali-Cakici-Whitby MAX, Amihud, Baker-Haugen low-vol, George-Hwang 52-week high, Hou-Moskowitz price delay, Kumar lottery).
`;

const CATEGORIES = [
  { key: 'reversal_family', brief: `REVERSAL FAMILY at multiple horizons. Short-term reversal is THE most robust A-share anomaly (retail overreaction + T+1). Design reversal factors at 5d, 10d, 20d, 60d, 120d lookbacks. ALSO design NON-PRICE reversal variants: reversal weighted by turnover (high-turnover reverses more), reversal among recent limit-up names, reversal conditional on volatility, industry-adjusted reversal (use cross-section median as market proxy — NO sw industry), and "long-horizon reversal" (12-month reversal, since A-share momentum REVERSES). Include a skip-month reversal (avoid the last few days' continuation).` },
  { key: 'lowrisk_family', brief: `LOW-RISK / VOL FAMILY. The low-vol anomaly is the strongest timeless A-share regularity. Beyond low_residual_vol60, design: Parkinson range-vol, Garman-Klass vol, IVOL (residual from cross-sectional median market model), downside semideviation, beta (vs cross-sectional median), downside-beta, vol-of-vol (volatility stability), and multi-horizon vol (20d/60d/120d). Also "vol mean-reversion": stocks whose vol SPIKED tend to keep underperforming (clustering of retail attention). low-vol at MULTIPLE horizons.` },
  { key: 'lottery_moments', brief: `LOTTERY / HIGHER MOMENTS FAMILY. Retail lottery preference is structural in A-shares. Design: MAX at 5/10/20/60d, realized skew at 20/60d, realized kurtosis, "near 52-week high" (George-Hwang: distance from rolling 250d max — near-high overhang), "number of limit-up days in 20/60d" (speculation intensity using up_limit vs close), tail ratio / CVaR, and the MAX-MIN asymmetry. All these proxy "lottery-like" stocks that retail overpays for → underperform.` },
  { key: 'liquidity_micro', brief: `LIQUIDITY / TURNOVER / MICROSTRUCTURE FAMILY. Design: Amihud at 5/10/20/60d, turnover level + turnover trend (declining attention), zero-return-day fraction (Lesmond), Corwin-Schultz spread (high/low), Roll spread, volume HHI (attention clustering), volume-price divergence (price up + volume down = exhaustion), Amihud momentum (change in liquidity), and "turnover × reversal" interaction. Turnover effect (low-turnover outperforms) is one of the most timeless A-share anomalies.` },
  { key: 'behavioral_interactions', brief: 'BEHAVIORAL INTERACTION PRIMITIVES. Single factors are weak; timeless interactions often carry the alpha. Design composite behavioral factors: (a) turnover-weighted reversal (high-turnover losers bounce most), (b) low-vol × low-MAX (doubly non-lottery), (c) Amihud × reversal (illiquid + recently dropped), (d) near-52w-high × high-turnover (overhang + distribution), (e) limit-up-frequency × reversal (speculation then reversal), (f) MAX × SKEW (lottery phenotype). All from price/volume/amount/turnover/up_limit only — fully deployable. Each interaction should target a SPECIFIC retail-behavior mechanism.',
  },
];

const SCHEMA = {
  type: 'object',
  properties: { factors: { type: 'array', items: {
    type: 'object',
    properties: {
      name: { type: 'string' }, economic_thesis: { type: 'string' }, math_formula: { type: 'string' },
      data_inputs: { type: 'array', items: { type: 'string' } }, ptrade_mapping: { type: 'string' },
      direction: { type: 'string', enum: ['high_desirable', 'low_desirable'] },
      expected_best_horizon: { type: 'string', enum: ['5', '10', '20', '40'] },
      timelessness_rationale: { type: 'string' }, pit_note: { type: 'string' },
    },
    required: ['name', 'economic_thesis', 'math_formula', 'data_inputs', 'ptrade_mapping', 'direction', 'expected_best_horizon', 'timelessness_rationale', 'pit_note'],
  } } },
  required: ['factors'],
};

phase('Mine');
log(`Mining TIMELESS A-share factors across ${CATEGORIES.length} behavioral/low-risk families...`);
const results = await parallel(CATEGORIES.map((c) => () =>
  agent(`${SHARED}\n\n# YOUR FAMILY: ${c.key}\n${c.brief}\n\nReturn 8-12 specs via the structured-output tool.`,
    { label: `mine:${c.key}`, phase: 'Mine', schema: SCHEMA }).then((r) => ({ family: c.key, factors: (r && r.factors) || [] }))
));
const all = results.filter(Boolean);
let n = 0; for (const r of all) n += r.factors.length;
log(`Collected ${n} timeless-factor specs across ${all.length} families`);
return all;
