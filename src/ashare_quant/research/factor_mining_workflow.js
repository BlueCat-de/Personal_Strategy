export const meta = {
  name: 'factor-mining-panel',
  description: 'Parallel panel of 7 factor-researcher agents that mine deployable A-share alpha factors',
  phases: [{ title: 'Mine', detail: '7 category agents each return 8-12 factor specs' }],
};

const SHARED_PREAMBLE = `
You are a senior quantitative researcher designing NOVEL equity factors for the China A-share market. Your output drives an IC-screening pipeline. Think deeply — combine economic intuition, behavioral finance, and UNUSUAL statistical methods ("things ordinary analysts wouldn't think of").

# HARD CONSTRAINTS (a factor is rejected if it violates any)

1. **Deployability — every input field must be in the LOCAL cache AND mappable to Ptrade.** We develop locally with Tushare data only; we cannot call Ptrade APIs during research. But the factor must be deployable on Ptrade later. So you may ONLY use these fields:

   PRICE (daily, from prices dict — pivoted wide DataFrames, index=date, columns=symbol):
     close, open, high, low, raw_open, raw_close, volume, amount, turnover,
     up_limit (涨停价), down_limit (跌停价), is_listed, is_st, is_suspended
   (Note: returns = close.pct_change(); overnight_gap = raw_open / close.shift(1) - 1; intraday = close/open - 1; true_range proxies available via high/low.)

   VALUATION (monthly PIT snapshot, from daily_basic cache):
     pe_ttm, pb, ps_ttm, dv_ttm (dividend yield %), total_mv, circ_mv,
     turnover_rate, volume_ratio, total_share, float_share, free_share

   FINANCIAL (quarterly PIT snapshot, fina_indicator cache, available_date = ann_date + 1 day):
     roe, roic, roa, roe_waa, grossprofit_margin, netprofit_margin, debt_to_assets,
     assets_turn, ocf_to_debt, q_ocf_to_sales, q_sales_yoy, dt_netprofit_yoy,
     ocf_yoy, equity_yoy

   **FORBIDDEN inputs:** SW/sector industry membership (Ptrade can't serve it reliably); full statement LEVELS like total_assets / net_profit / OCF / revenue in absolute yuan (NOT in local cache — only the ratios/growth above); macro variables; analyst/fund-holding/news data; any field not listed above.

2. **No future functions (PIT).** A factor at date t may only use data observable by t. Rolling windows on price are naturally PIT if they end at t. Financial snapshots use the latest report announced by t (the cache enforces available_date = ann_date + 1). When you describe a factor, state the lookback and confirm PIT.

3. **Direction convention.** State the final sign so that HIGH factor value = desirable (predicts HIGHER future return). E.g., a "low volatility is good" factor must be expressed as -volatility. Set 'direction' = "high_desirable".

# ALREADY-USED FACTORS (do NOT duplicate — mine must be ORTHOGONAL)
low_turnover, dividend_yield, low_residual_vol60, earnings_yield, ocf_yoy, q_sales_yoy,
reversal20, roe, AMIHUD20 (|ret|/amount, 20d), MAX20 (-max ret, 20d), SKEW60 (-skew, 60d),
mom120_ex20, mom60, low_vol60, low_downside60, low_beta60, trend_quality120, reversal5,
book_yield, sales_yield, low_debt, distance_ma120, drawdown60, volume_contraction.

# OUTPUT — return 8-12 factor specs, each with:
- name: unique snake_case (e.g. "accrual_gap_20d")
- economic_thesis: 1-3 sentences. WHY does it predict returns? Cite the anomaly/literature if known (Sloan accruals, Pontiff issuance, Novy-Marx profitability, Hou-Xue-Zhang q-theory, IVOL puzzle, lottery preference, Amihud, etc.).
- math_formula: PRECISE pseudocode/formula a programmer can implement unambiguously from the allowed fields. Include the exact lookback window(s) and the final sign.
- data_inputs: list of allowed fields used (must all be from the lists above).
- ptrade_mapping: one line confirming each input maps to Ptrade (price→get_history; pe/pb/dv/mv/turnover/share→valuation table; roe/roa/margins/yoy→profit_ability/growth_ability tables).
- direction: "high_desirable"
- expected_ic_sign: "+" or "-" (your prior)
- novelty: what is non-obvious or unusually statistical about this factor?
- pit_note: one line on how PIT safety is guaranteed.

# QUALITY BAR
- Prefer factors with a REAL economic story over data-mined transforms.
- Embrace unusual statistics where they fit: Hurst exponent (R/S rescaled range), Shannon entropy of return signs, Wald-Wolfowitz runs test, Corwin-Schultz high-low spread estimator, Roll effective spread (serial covariance), approximate/sample entropy, PCA residual mispricing, distributional kurtosis, tail ratio / CVaR.
- Vary lookbacks; don't make everything 20d/60d.
- Be concrete and implementable. No hand-waving.
`;

const CATEGORIES = [
  {
    key: 'quality_accrual',
    title: 'Earnings quality & accruals',
    brief: `Category: EARNINGS QUALITY & ACCRUALS. The Sloan accrual anomaly (high accruals → low future returns) is among the most robust in finance. But we lack statement LEVELS (net_profit, OCF, total_assets in yuan) — only RATIO/YOY fields. So design clever PROXIES:
- Accrual-quality gap = dt_netprofit_yoy (earnings growth) minus ocf_yoy (cash growth): earnings growing much faster than cash → suspect accruals → bearish. (Both fields are deployable: np_parent_company_yoy & net_operate_cash_flow_yoy on Ptrade.)
- Earnings-volatility-of-growth: variability of dt_netprofit_yoy vs q_sales_yoy stability.
- Margin expansion quality = dt_netprofit_yoy - q_sales_yoy (profit growing faster than sales).
- Gross profitability (Novy-Marx) — proxy via grossprofit_margin level.
- ROE stability / ROA level (roa, roe_waa).
- "Too-fast growth": flag implausibly high dt_netprofit_yoy (mean-reversion of extreme growth).
- OCF/debt coverage (ocf_to_debt) as quality.
Deliver 8-12 specs with precise formulas.`,
  },
  {
    key: 'investment_financing',
    title: 'Investment & financing anomaly',
    brief: `Category: INVESTMENT & FINANCING ANOMALY. Asset-growth / net-issuance anomalies (Cooper-Gulen-Schill asset growth; Pontiff-Woodward share issuance) are robust: firms that grow assets fast or issue equity underperform.
- Net issuance proxy = equity_yoy (equity growth; maps to Ptrade net_asset_grow_rate). High issuance → bearish.
- Sustainable growth vs actual growth gap (if sustainable_grow_rate were available — it's not, so reason around equity_yoy vs dt_netprofit_yoy).
- Debt growth / leverage change via debt_to_assets dynamics (we only have the level; design a factor that uses the level cross-sectionally + its PIT change if you can reason about it from consecutive snapshots — note it).
- q-theory: high assets_turn (asset productivity) → desirable.
- "Empire builder" flag: high equity_yoy + low roa (growing without returns).
- Share count dilution via total_share / float_share ratio patterns.
Be precise; respect that we have ratios not levels.`,
  },
  {
    key: 'downside_tail',
    title: 'Downside risk & tail',
    brief: `Category: DOWNSIDE RISK & TAIL. Low-risk anomalies (low-beta, low-IVOL, low-downside-beta, low-coskew) are strong in A-shares.
- Idiosyncratic volatility (IVOL): regress 60d returns on cross-sectional median market return (proxy for market), take residual std. Low IVOL → desirable (IVOL puzzle).
- Downside beta: beta computed only on market-down days vs overall beta (Harvey-Siddique co-skewness idea).
- Realized KURTOSIS60 (fat tails = risk; deployable from price).
- Tail ratio / CVaR: mean of worst-5% daily returns over 60d; or (95th pct - 5th pct) of returns.
- Max-drawdown over 120d and recovery speed.
- "Crash risk": frequency of >7% down days in 120d.
- Down-vol to up-vol ratio (semideviation).
Use high/low where helpful. Direction high_desirable.`,
  },
  {
    key: 'liquidity_micro',
    title: 'Liquidity & microstructure',
    brief: `Category: LIQUIDITY & MICROSTRUCTURE. We have high/low available (deployable via Ptrade get_history) — use them for spread estimators.
- Corwin-Schultz effective spread estimator from daily high/low (the sum of log(High_t/ Low_t) over 2 days vs 1 day isolates bid-ask bounce). Low spread → more liquid → desirable (or premium for illiquid — let IC decide, set direction carefully).
- Roll's effective spread = 2*sqrt(-serial covariance of consecutive returns) (negative autocovariance → spread).
- Amihud ILLIQUIDITY CHANGE (momentum of liquidity): change in |ret|/amount over time.
- Zero(-return) days fraction (Lesmond): high fraction → low liquidity / high transaction cost → bearish (or illiquidity premium — reason it).
- Volume concentration (Herfindahl of daily volume over 60d): concentrated volume = attention spikes.
- Price-impact nonlinear curvature (|ret| vs amount relationship).
- Parkinson volatility from high/low (range-based vol, more efficient than close-to-close).
- Amihud stress: illiquidity conditional on market-wide high-volume days.`,
  },
  {
    key: 'nonlinear_stat',
    title: 'Nonlinear statistical signatures',
    brief: `Category: NONLINEAR / STATISTICAL SIGNATURES. This is where you go beyond standard factors. Use unusual statistics computed from the daily return series (price-only, fully deployable).
- Hurst exponent via rescaled range (R/S) over 120d: H<0.5 = mean-reverting (desirable for reversal), H>0.5 = trending. The Hurst value itself, or distance from 0.5.
- Shannon entropy of the sign of daily returns over 60d ( discretize into up/down buckets): low entropy = more predictable pattern.
- Approximate entropy / sample entropy of the return series (complexity/predictability).
- Wald-Wolfowitz runs test statistic on up/down days (too few runs = trending/clustering; too many = alternating).
- Lag-1 and lag-5 autocorrelation of returns (short-term reversal/trending signature).
- PCA / cross-sectional residual mispricing: hard PIT — instead, cross-sectional rank-dispersion or beta-dispersion. Or the stock's return variance fraction explained by the cross-sectional median (idiosyncratic share).
- Realized skewness & kurtosis jointly; second-order moments.
Deliver genuinely unusual, well-specified factors.`,
  },
  {
    key: 'volumep_flow_calendar',
    title: 'Volume-price flow & A-share calendar',
    brief: `Category: VOLUME-PRICE FLOW & A-SHARE CALENDAR.
- Volume-weighted return concentration / smart-money: correlate large-volume days with positive returns.
- Up-volume vs down-volume ratio over 60d (buying vs selling pressure proxy). Deployable from price+volume.
- OBV-style cumulative (sign(return)*volume) momentum / divergence from price.
- Turnover acceleration (rate of change of turnover_rate).
- Volume-price divergence: rolling rank correlation of cumulative return vs cumulative (log) volume over 60d — divergence (price up, volume down) signals exhaustion.
- Price-limit proximity: how often |close-prevclose|/prevclose is near the 10% limit (speculation intensity). Use preclose = close.shift(1) or up_limit/down_limit.
- A-share "spring agitation" (春季躁动): January/February seasonal dummy (deployable via calendar — but note PIT and that seasonality is weak; include but mark low confidence).
- Limit-up reversal: stocks that recently hit up_limit tend to reverse.
Deliver 8-12 specs.`,
  },
  {
    key: 'composite_blend',
    title: 'Composite interactions & ratio blends',
    brief: `Category: COMPOSITE INTERACTIONS & RATIO BLENDS. Single factors are weak; INTERACTION factors often carry orthogonal alpha.
- Piotroski-style composite: count of {roe>0, ocf_yoy>0, q_sales_yoy>0, debt_to_assets down/low, grossprofit_margin up} (deployable from the ratio fields).
- Magic-formula-like: earnings_yield × roa (cheap AND profitable).
- Quality-at-price: roic × earnings_yield; or grossprofit_margin × book_yield.
- "Safe growth": q_sales_yoy × (-low_vol60 proxy) — growth that isn't volatile.
- Accrual-adjusted profitability: roa combined with the accrual-gap (dt_netprofit_yoy - ocf_yoy).
- DuPont decomposition pieces: netprofit_margin × assets_turn (operating efficiency).
- Issuance-adjusted value: earnings_yield conditional on low equity_yoy (cheap + not diluting).
These interaction factors must still use only allowed fields. Deliver 8-12.`,
  },
];

const SCHEMA = {
  type: 'object',
  properties: {
    factors: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string' },
          economic_thesis: { type: 'string' },
          math_formula: { type: 'string' },
          data_inputs: { type: 'array', items: { type: 'string' } },
          ptrade_mapping: { type: 'string' },
          direction: { type: 'string', enum: ['high_desirable', 'low_desirable'] },
          expected_ic_sign: { type: 'string', enum: ['+', '-'] },
          novelty: { type: 'string' },
          pit_note: { type: 'string' },
        },
        required: ['name', 'economic_thesis', 'math_formula', 'data_inputs', 'ptrade_mapping', 'direction', 'expected_ic_sign', 'pit_note'],
      },
    },
  },
  required: ['factors'],
};

phase('Mine');
log(`Mining deployable A-share factors across ${CATEGORIES.length} categories...`);

const results = await parallel(
  CATEGORIES.map((c) => () =>
    agent(
      `${SHARED_PREAMBLE}\n\n# YOUR ASSIGNMENT\n\n${c.brief}\n\nReturn 8-12 factor specs via the structured-output tool. Make every formula implementable from ONLY the allowed fields. Be creative but rigorous.`,
      { label: `mine:${c.key}`, phase: 'Mine', schema: SCHEMA }
    ).then((r) => ({ category: c.key, title: c.title, factors: (r && r.factors) || [] }))
  )
);

const all = results.filter(Boolean);
let total = 0;
for (const r of all) total += r.factors.length;
log(`Collected ${total} factor specs across ${all.length} categories`);

return all;
