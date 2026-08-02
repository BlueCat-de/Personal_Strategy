# AShare Quant

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: GPL v3](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)

Point-in-time A-share data engineering, factor research, realistic backtesting,
and daily signal automation.

[中文文档](docs/README_zh-CN.md) | [Architecture](docs/ARCHITECTURE.md) |
[Python API](docs/API.md) | [策略资产登记册](docs/STRATEGY_ASSETS_zh-CN.md) |
[Contributing](CONTRIBUTING.md)

> This project is for research and education. It does not provide investment
> advice, guarantee returns, or place live orders.

## Features

- Builds a point-in-time A-share dataset from Tushare Pro.
- Separates adjusted factor prices from raw execution prices.
- Restores historical listing, delisting, ST, suspension, and price-limit state.
- Caches raw income / balance-sheet / cash-flow statements with PIT `available_date
  = ann_date + 1` (joined same-period, deployable on Ptrade `get_fundamentals`).
- Executes signals at the next trading day's raw open.
- Models board lots, commissions, stamp duty, minimum fees, and bilateral slippage.
- Includes a defensive v4 strategy and a diversified stock-only factor strategy.
- Ships an evaluation framework: Sharpe/Sortino/Calmar/Omega/Ulcer/VaR/CVaR,
  statistical significance (PSR, DSR, PBO/CSCV, Haircut Sharpe, MinTRL), CAPM
  attribution, stress/regime analysis, cost & turnover, and a walk-forward
  rolling-OOS stability diagnostic with embargo.
- Supports market-cap, industry, style, rebalance-date, and slippage robustness tests.
- Runs scheduled data updates, strategy checks, and optional Feishu/Lark notifications.

The repository intentionally excludes market data, API tokens, webhook URLs,
runtime logs, and backtest outputs.

## Tech Stack

- Python 3.11+
- pandas and NumPy
- Tushare Pro
- setuptools with a `src/` package layout
- unittest/pytest-compatible tests

## Project Layout

```text
.
├── src/ashare_quant/
│   ├── automation/       # Daily scheduler and Feishu/Lark notifications
│   ├── data/             # Tushare adapter, PIT builder, industry history, raw statements
│   ├── evaluation/       # Backtest metrics, significance (PSR/DSR/PBO), walk-forward OOS
│   ├── research/         # Factor panels and stability experiments
│   ├── strategies/       # v4, experimental, and forward-validation strategies
│   ├── visualization/    # matplotlib research charts
│   ├── backtest.py       # Next-open execution engine
│   ├── benchmark.py      # CSI 300 retrieval and relative metrics
│   └── paths.py          # Repository-local paths
├── tests/
├── docs/
├── pyproject.toml
└── requirements.txt
```

Local data remains under `data/` and is ignored by Git.

## Installation

```bash
git clone https://github.com/BlueCat-de/Personal_Strategy.git
cd Personal_Strategy

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

For runtime dependencies only:

```bash
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
```

## Configuration

```bash
cp .env.example .env.local
cp .feishu_webhook.example .feishu_webhook
```

Set your Tushare token in `.env.local`:

```dotenv
TUSHARE_TOKEN=replace_with_your_token
```

Webhook configuration is optional. Never commit `.env.local` or
`.feishu_webhook`.

## Quick Start

### 1. Build point-in-time market data

Use a short smoke test first:

```bash
ashare-rebuild-data \
  --start-date 2024-01-01 \
  --end-date 2024-03-31 \
  --limit 30 \
  --output-dir data/offline/smoke
```

Build the full local dataset:

```bash
ashare-rebuild-data \
  --start-date 2020-01-01 \
  --end-date "$(date +%F)" \
  --board-scope main \
  --output-dir data/offline/a_share_history_tushare
```

Expected core files:

```text
data/offline/a_share_history_tushare/
├── prices_long.csv
├── daily_universe.csv
└── universe.csv
```

The default `main` scope keeps the existing main-board research dataset
unchanged. To build the missing ChiNext, STAR Market, and Beijing Stock
Exchange data, use a separate output directory:

```bash
ashare-rebuild-data \
  --start-date 2010-01-01 \
  --end-date 2026-07-17 \
  --board-scope growth \
  --output-dir data/offline/a_share_growth_boards_tushare
```

This produces an isolated dataset:

```text
data/offline/a_share_growth_boards_tushare/
├── prices_long.csv
├── daily_universe.csv
└── universe.csv
```

Backtests keep `--board-scope main` by default. To include the growth-board
file at runtime without merging files on disk:

> **Audit status:** `financial_quality_alpha` is under full revalidation after
> point-in-time data defects were found and corrected. Historical performance
> published before 2026-07-21 is invalid and must not be used for investment
> decisions. See [Future-function audit](docs/FUTURE_FUNCTION_AUDIT_zh-CN.md).

```bash
ashare-financial-quality \
  --prices-file data/offline/a_share_history_tushare/prices_long.csv \
  --extra-prices-file data/offline/a_share_growth_boards_tushare/prices_long.csv \
  --board-scope all \
  --start-date 2011-01-01 \
  --warmup-start-date 2010-01-04 \
  --end-date 2026-07-17 \
  --initial-cash 100000 \
  --slippage 0.001 \
  --output-dir data/backtests/financial_quality_all_boards
```

See [Architecture](docs/ARCHITECTURE.md) for the schema and PIT rules.

### 2. Fetch historical Shenwan industries

```bash
ashare-fetch-industries
```

This produces versioned historical level-one membership with effective entry,
exit, and classification-version dates. SW2021 classifications are not used
before their effective date.

### 3. Run v4

```bash
ashare-v4 \
  --strategy-version v4 \
  --warmup-start-date 2024-01-01 \
  --start-date 2024-07-01 \
  --end-date 2026-07-16 \
  --initial-cash 100000 \
  --prices-file data/offline/a_share_history_tushare/prices_long.csv \
  --output-dir data/backtests/v4
```

### 4. Run the stock-only stable strategy

```bash
ashare-stable \
  --start-date 2021-07-01 \
  --warmup-start-date 2020-01-02 \
  --end-date 2026-07-16 \
  --initial-cash 45000 \
  --slippage 0.001 \
  --output-dir data/backtests/stable_stock_alpha
```

This strategy uses only individual A-share stocks and cash. It does not use
ETFs, index weights, futures, options, or other derivatives.

### 5. Run the experimental breadth-adaptive strategy

```bash
ashare-adaptive \
  --start-date 2021-07-01 \
  --warmup-start-date 2020-01-02 \
  --end-date 2026-07-16 \
  --initial-cash 45000 \
  --slippage 0.001 \
  --output-dir data/backtests/adaptive_stock_alpha
```

The research baseline rebalances every second month. When the
point-in-time 120-day market breadth is at least 76%, it selects 12 large-cap
low-volatility value stocks. Otherwise, it selects eight industry-neutral
relative-strength stocks, with at most 20% of holdings from one industry.

After the full point-in-time audit, this baseline beats the CSI 300 in five of
six reported periods and is not production eligible. The earlier 6/6 result
has been invalidated because its breadth denominator was not strictly PIT.

### 6. Run the offset-neutral stock strategy

```bash
ashare-offset-neutral \
  --initial-cash 100000 \
  --slippage 0.001 \
  --output-dir data/backtests/offset_neutral_stock_alpha
```

This stock-only candidate combines monthly large-cap defensive selection,
low-turnover small caps, and two staggered adaptive sleeves. It passes all six
historical annual excess-return periods after the PIT audit, but remains frozen
for forward validation rather than production approved. `--initial-cash` supports
capital from CNY 50,000; CNY 200,000 remains the recommended starting capital
because integer-lot constraints are lower.

### 7. Plot capital-sensitive backtest curves

```bash
ashare-plot-capital-curves \
  --input-dir data/backtests/offset_neutral_capital_sensitivity_20210701_20260716 \
  --output-dir data/backtests/offset_neutral_capital_sensitivity_20210701_20260716/charts
```

This uses `matplotlib` to generate PNG and PDF normalized-NAV and cumulative-return
charts for 50k, 100k, 500k, and 1m strategy capital alongside the CSI 300 benchmark.

### 8. Run robustness research

```bash
ashare-stability \
  --start-date 2021-07-01 \
  --validation-start 2024-01-01 \
  --end-date 2026-07-16 \
  --initial-cash 45000
```

The experiment covers factor-weight neighborhoods, 8/10/12 holdings,
monthly/bimonthly rebalancing, market-cap and industry cross-sections, and
5/10/20/50 bp slippage. Historical results are research evidence rather than
a guarantee of future excess returns.

## Daily Deployment

Run with an explicit Python executable:

```bash
ashare-daemon \
  --python "$PWD/.venv/bin/python" \
  --data-time 21:10 \
  --strategy-time 21:30
```

For production, use a process supervisor such as systemd, launchd, or
Supervisor. Keep runtime state in `run/` and logs in `logs/`; both are ignored.

The daemon generates signals and notifications only. It does not connect to a
broker or submit orders.

## Python API

```python
from ashare_quant.backtest import BacktestConfig, run_local_backtest
from ashare_quant.strategies.v4 import StrategyConfig, build_targets, load_prices

prices = load_prices("data/offline/a_share_history_tushare/prices_long.csv", config)
targets, signals, debug = build_targets(prices, config)
result = run_local_backtest(
    prices,
    targets,
    BacktestConfig(initial_cash=100_000, slippage=0.001),
    strategy_name="example",
)
```

See [docs/API.md](docs/API.md) for supported public modules and data contracts.

## Testing

```bash
python -m pytest
ruff check src tests
```

The execution tests cover zero slippage, bilateral slippage, and invalid
slippage configuration.

## Data and Reproducibility

Git contains code and documentation only. After cloning, assemble these local
items yourself:

1. `.env.local`, containing your own `TUSHARE_TOKEN`.
2. `deploy/ptrade/tushare_token.csv`, only when running the Ptrade adapter.
3. The strict-PIT data tree under `data/offline/a_share_history_tushare/`.

Never commit either token file. The Ptrade transaction exports and all
backtest outputs are also intentionally ignored.

### Restore the frozen local data bundle

The reproducibility bundle is distributed separately because Tushare data is
subject to its own license and GitHub is not suitable for multi-gigabyte market
data. Obtain `ashare_quant_main_reproduction_YYYYMMDD.tar.gz` and its
`.sha256` file through the project's private artifact channel, place both in
the repository root, then run:

```bash
shasum -a 256 -c ashare_quant_main_reproduction_YYYYMMDD.tar.gz.sha256
tar -xzf ashare_quant_main_reproduction_YYYYMMDD.tar.gz
```

The archive restores paths directly under:

```text
data/offline/a_share_history_tushare/
├── prices_long.csv
├── daily_universe.csv
├── universe.csv
├── manifest.json
├── benchmark_000300.csv
├── sw_l1_membership_history.csv
├── .daily_basic_monthly_cache/
├── .long_horizon_daily_basic_cache/
├── .long_horizon_fina_indicator_cache/
├── .income_statement_cache/
├── .balancesheet_statement_cache/
└── .cashflow_statement_cache/
```

These inputs reproduce the frozen main-board strategy and the committed
strict-PIT research through 2026-07-17. The bundle excludes API credentials,
backtest outputs, backup directories, Ptrade transaction exports, and the
separate growth-board dataset.

### Rebuild from Tushare instead

To reconstruct the inputs independently, first configure `.env.local`, then
build the main-board PIT bars:

```bash
ashare-rebuild-data \
  --start-date 2006-01-04 \
  --end-date 2026-07-17 \
  --board-scope main \
  --output-dir data/offline/a_share_history_tushare

ashare-fetch-industries
```

Next populate monthly `daily_basic` snapshots for every first trading day,
and cache `fina_indicator` records for every symbol in `universe.csv`.
Financial rows must retain `ann_date`, `end_date`, and `update_flag`; research
uses `available_date = ann_date + 1 calendar day` and prefers original filings
when revision timing is unavailable. Do not replace these caches with a
current cross-section.

The separate cache names are intentional:

- `.daily_basic_monthly_cache`: short-horizon strategy inputs.
- `.long_horizon_daily_basic_cache`: 2007-2026 style and factor research.
- `.long_horizon_fina_indicator_cache`: announcement-dated financial ratio history.
- `.income_statement_cache` / `.balancesheet_statement_cache` /
  `.cashflow_statement_cache`: raw statement LEVELS (PIT, joined same-period).
  Build with `python -m ashare_quant.data.statements --statement {income,balancesheet,cashflow}`.
  Used for cross-statement factors (e.g. Sloan accruals, asset-growth) that the ratio
  table cannot express. Fields are restricted to `DEPLOYABLE_FIELD_MAP` (local Tushare
  name ↔ Ptrade `get_fundamentals` name).
- `sw_l1_membership_history.csv`: versioned PIT Shenwan membership.
- `benchmark_000300.csv`: offline CSI 300 price-index comparison.

Rebuilding can produce different historical values if Tushare revises its
database. Exact historical reproduction therefore requires the frozen bundle,
not merely the same API commands.

### Reproduce the current frozen strategy

```bash
ashare-main-board-bimonthly-ic \
  --warmup-start-date 2006-01-04 \
  --start-date 2007-01-01 \
  --end-date 2026-07-17 \
  --initial-cash 100000 \
  --positions 8 \
  --rebalance-offset 0 \
  --slippage 0.001 \
  --output-dir data/backtests/main_board_bimonthly_ic
```

The current research modules can be rerun with:

```bash
python -m ashare_quant.research.incremental_behavioral_factors
python -m ashare_quant.research.incremental_behavioral_robustness
python -m ashare_quant.research.core_satellite_blend
python -m ashare_quant.research.dual_regime_strategy
python -m ashare_quant.research.dual_regime_meta_strategy
python -m ashare_quant.research.dual_regime_frozen_model
python -m ashare_quant.research.dual_regime_dual_offset_model
python -m ashare_quant.research.score_layer_blend --positions 6 8 10 12
```

Generate a fresh bundle from an already assembled workspace with:

```bash
python scripts/package_reproduction_data.py
```

The command writes the archive, a per-file manifest, and an archive SHA-256
file under `data/packages/`.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening an issue or pull request.
Security reports should follow [SECURITY.md](SECURITY.md). Community
participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

Copyright (C) BlueCat-de.

Licensed under the [GNU General Public License v3.0](LICENSE).

## Maintainer

[BlueCat-de](https://github.com/BlueCat-de)
