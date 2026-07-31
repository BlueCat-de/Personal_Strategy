export const meta = {
  name: 'v2-adversarial-review',
  description: 'Independent skeptics audit composite_alpha_v2 for leakage/overfit/deployability before test unblind',
  phases: [{ title: 'Audit', detail: '4 lenses, each returns findings' }],
};

const CONTEXT = `
Strategy under review: composite_alpha_v2 — a 10-factor / 6-dimension monthly A-share composite.
Repo: d:/Chenqm/Personal_Strategy (venv: .venv/Scripts/python.exe).

KEY FILES TO READ:
- src/ashare_quant/strategies/composite_alpha_v2.py  (the strategy: build_targets, _composite_score_v2, weights)
- src/ashare_quant/research/cand_lib.py  (build_shared, snapshot_panels, fina_history_quarters, existing_factors_from_shared, per_date_stat)
- src/ashare_quant/research/cand_*.py  (the candidate factor implementations)
- src/ashare_quant/research/composite_v2_robustness.py  (robustness harness)
- data/research/factor_probe/ic_combined.csv  (train+val IC of all factors)
- data/backtests/composite_alpha_v2_trainval/performance.json  (train/val perf)
- C:/Users/Qimao Chen/.claude/projects/d--Chenqm-Personal-Strategy/memory/ptrade-deployable-data-universe.md  (deployability contract)

DISCIPLINE: train(2007-2013) / val(2014-2020) / test(2021-2026 BLIND). Test must NOT be peeked.
The 10 v2 factors: earnings_yield, dividend_yield, low_residual_vol60, low_turnover, AMIHUD20,
ocf_yoy, sales_equity_growth_spread, gross_profit_book_yield, margin_expansion_quality, debt_accumulation_4q.
`;

const SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['blocker', 'major', 'minor', 'nit'] },
          location: { type: 'string' },
          issue: { type: 'string' },
          fix: { type: 'string' },
        },
        required: ['severity', 'location', 'issue', 'fix'],
      },
    },
    verdict: { type: 'string', enum: ['ship', 'fix-then-ship', 'rework'] },
    summary: { type: 'string' },
  },
  required: ['findings', 'verdict', 'summary'],
};

const LENSES = [
  {
    key: 'pit_lookahead',
    prompt: `LENS: PIT / LOOKAHEAD LEAKAGE. You are a skeptic hunting for ANY future-function. For every factor in the v2 set AND every helper (build_shared, snapshot_panels, fina_history_quarters, per_date_stat, the cand_*.py modules):
- Does any rolling/window use data AFTER the signal date? (check .shift, .rolling end-alignment, iloc slicing)
- Are financial snapshots truly PIT (available_date = ann_date+1; latest report by signal date, not by end_date)?
- Is fina_history_quarters using reports available BY the date (not future quarters)?
- Are forward returns used ONLY in IC eval, never inside a factor or the strategy build_targets?
- Is the eligibility mask computed from data up to t (not t+1)?
- Monthly cadence: signal generated at month-start t, executed T+1 — any peek?
Cite exact file:line for each finding. Run small python checks if needed (you may import modules, but do NOT load the 1.9GB prices). Return findings + verdict.`,
  },
  {
    key: 'overfit_selection',
    prompt: `LENS: OVERFITTING & SELECTION BIAS. You are a skeptic asking "is the val performance real or an artifact of selection?"
- 78 factors were mined, 10 selected on train+val IC. Assess multiple-comparison risk: with 78 candidates, some will look good in val by chance. Is the selected set's val edge defensible? Check data/research/factor_probe/ic_combined.csv — how many of the 10 are robust in BOTH train and val (consistent sign, IR>0.3 both)?
- Was the test set (2021-2026) EVER touched during selection/weighting? (Read the workflow; confirm no --include-test was run during mining.)
- Equal-weighting was chosen to avoid AMIHUD20-style over-weighting fragility — is this sound, or does it hide something?
- Are the 4 NEW factors (sales_equity_growth_spread, gross_profit_book_yield, margin_expansion_quality, debt_accumulation_4q) economically distinct or did IC-mining pick noise? Check their train IR specifically (weak train + strong val = red flag).
- Note: sales_equity_growth_spread correlates 0.77 with q_sales_yoy — was dropping q_sales_yoy and adding this just swapping labels?
Return findings + verdict on whether val performance is trustworthy.`,
  },
  {
    key: 'deployability',
    prompt: `LENS: DEPLOYABILITY ON PTRADE. You are a skeptic verifying EVERY data input is fetchable on Ptrade at runtime (see the memory contract). For each of the 10 v2 factors, trace its data_inputs through cand_lib/cand_*.py to the raw fields:
- price fields (close/open/high/low/volume/amount/turnover/up_limit/down_limit) → Ptrade get_history ✓
- valuation (pe_ttm/pb/ps_ttm/dv_ttm/total_mv/circ_mv/total_share/float_share) → Ptrade valuation table ✓
- fina ratios (roe/roa/grossprofit_margin/netprofit_margin/debt_to_assets/assets_turn/ocf_to_debt/q_ocf_to_sales/q_sales_yoy/dt_netprofit_yoy/ocf_yoy/equity_yoy) → Ptrade profit_ability/growth_ability ✓
- FLAG any input NOT in the above lists (e.g. SW industry, statement LEVELS like total_assets/net_profit in yuan, macro/analyst data).
- debt_accumulation_4q and the fina_history multi-quarter factors: can Ptrade fetch prior quarterly reports? (get_fundamentals with report_types / start_year-end_year supports period history — confirm.)
- Check the v2 strategy uses market-cap-only neutralization (no industry) — confirm NO industry dependency anywhere.
Return findings + verdict on full deployability.`,
  },
  {
    key: 'stat_correctness',
    prompt: `LENS: STATISTICAL / IMPLEMENTATION CORRECTNESS. You are a skeptic checking the math.
- _neutralize_market_cap_only and _composite_score_v2: is the rank→winsorize→demean→regress-on-log(mv)→residual→standardize→weight pipeline correct? Any divide-by-zero, mis-aligned indexes, or sign errors?
- build_targets: does it correctly slice factors by .loc[dt], handle missing dates, apply eligibility, coverage filter, top-N?
- The cand_*.py factor formulas — spot-check 4-5 of the NEW factors (sales_equity_growth_spread, gross_profit_book_yield, margin_expansion_quality, debt_accumulation_4q, plus one per-date stat like cvar/coskew) for correctness vs their stated math formula in data/research/factor_probe/mined_specs.json.
- Are the RuntimeWarnings (Mean of empty slice / All-NaN slice in cand_risk_tail) handled (return NaN, not crash)?
- Coverage threshold = 1.0 (require ALL 10 factors non-null) — is this too strict (shrinks universe) or appropriate?
Return findings + verdict.`,
  },
];

phase('Audit');
log(`Adversarial review of composite_alpha_v2 across ${LENSES.length} lenses...`);

const results = await parallel(
  LENSES.map((l) => () =>
    agent(`${CONTEXT}\n\n# ${l.prompt}\n\nReturn structured findings via the tool. Be concrete: cite file:line. Default to skepticism.`, {
      label: `audit:${l.key}`, phase: 'Audit', agentType: 'general-purpose', schema: SCHEMA,
    })
  )
);

const all = results.filter(Boolean);
let blockers = 0, majors = 0;
for (const r of all) for (const f of (r.findings || [])) {
  if (f.severity === 'blocker') blockers++;
  if (f.severity === 'major') majors++;
}
log(`Review done: ${all.length} lenses, ${blockers} blockers, ${majors} majors`);
return all;
