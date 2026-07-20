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
- Executes signals at the next trading day's raw open.
- Models board lots, commissions, stamp duty, minimum fees, and bilateral slippage.
- Includes a defensive v4 strategy and a diversified stock-only factor strategy.
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
│   ├── data/             # Tushare adapter, PIT builder, industry history
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

This produces historical level-one membership with effective entry and exit
dates. It is required by the industry-neutral stock strategy.

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

Tushare data is subject to its own license and account permissions. This
repository does not redistribute Tushare market data. Backtests are only
reproducible when the same point-in-time inputs and configuration are used.

Historical snapshots stored under `data/snapshots/` are local assets and are
not part of the Git repository.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening an issue or pull request.
Security reports should follow [SECURITY.md](SECURITY.md). Community
participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

Copyright (C) BlueCat-de.

Licensed under the [GNU General Public License v3.0](LICENSE).

## Maintainer

[BlueCat-de](https://github.com/BlueCat-de)
