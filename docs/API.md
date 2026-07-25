# Python API

The public API is intentionally small. Command-line interfaces are preferred
for full data builds and research runs.

## Backtesting

```python
from ashare_quant.backtest import (
    BacktestArtifacts,
    BacktestConfig,
    run_local_backtest,
)
```

### `BacktestConfig`

Key fields:

| Field | Default | Meaning |
|---|---:|---|
| `initial_cash` | `100000` | Starting cash |
| `buy_cost` | `0.0003` | Proportional buy fee |
| `sell_cost` | `0.0013` | Proportional sell fee |
| `slippage` | `0.0` | Per-side adverse execution adjustment |
| `min_cost` | `5.0` | Minimum fee per transaction |
| `lot_size` | `100` | A-share board lot |

### `run_local_backtest`

```python
result = run_local_backtest(
    prices,
    targets,
    BacktestConfig(slippage=0.001),
    strategy_name="my_strategy",
)
```

Inputs:

- `prices`: mapping of field name to date-by-symbol DataFrames.
- `targets`: mapping of signal date to symbol target-weight Series.
- `config`: execution assumptions.

The target generated on date `t` becomes pending and executes at the next
available session's raw open.

Returns `BacktestArtifacts` with:

- `raw_perf`: daily equity, cash, leverage, transactions, and fees;
- `trades`: normalized transaction records;
- `summary`: return, volatility, Sharpe, drawdown, turnover, fees, and slippage.

## v4 Strategy

```python
from ashare_quant.strategies.v4 import (
    StrategyConfig,
    build_targets,
    load_prices,
)
```

`load_prices(path, config)` validates and pivots the PIT long table.

`build_targets(prices, config)` returns:

1. target weights;
2. signal records;
3. diagnostic market and candidate information.

## Stable Stock Strategy

The supported executable entry is:

```bash
ashare-stable --help
```

The implementation composes PIT factor panels, historical industry membership,
stock-only target construction, and the local backtest engine.

## Adaptive Stock Strategy

The breadth-adaptive executable entry is:

```bash
ashare-adaptive --help
```

`ashare_quant.strategies.adaptive_alpha` exposes the experimental baseline,
machine-readable research status, breadth style selection, rebalance schedule,
and PIT target builder. It is not currently production eligible.

## Offset-Neutral Stock Strategy

The frozen forward-validation candidate is exposed through:

```bash
ashare-offset-neutral --initial-cash 100000 --slippage 0.001
```

`ashare_quant.strategies.offset_neutral_alpha` provides:

- `OffsetNeutralAlphaConfig`: immutable sleeve, factor, liquidity, and capital settings;
- `build_offset_neutral_targets`: PIT target-weight generation;
- `ResearchStatus`: machine-readable historical cutoff and forward-validation state.

`--initial-cash` supports CNY 50,000 and above. CNY 200,000 is the recommended
starting capital because board-lot and minimum-fee constraints are lower at
that size. See [策略资产登记册](STRATEGY_ASSETS_zh-CN.md) for the asset boundary
and [策略说明](OFFSET_NEUTRAL_STRATEGY_zh-CN.md) for research limitations.

## Data

```python
from ashare_quant.data.tushare import get_pro_client, normalize_symbol
from ashare_quant.data.pit import RebuildConfig, rebuild
```

`rebuild(config)` writes the normalized market table, daily universe, static
security metadata, and a machine-readable build summary.

## Benchmark

```python
from ashare_quant.benchmark import benchmark_metrics, fetch_benchmark
```

`fetch_benchmark` retrieves CSI 300 daily closes by default.

`benchmark_metrics` calculates total and excess return, Sharpe, information
ratio, maximum drawdown, exposure, and turnover.

## Stability Research

```python
from ashare_quant.research.stability import (
    ExperimentCase,
    evaluate_case,
)
```

Research APIs are less stable than execution and data APIs. Changes to factor
definitions or experiment grids may occur between minor versions.
