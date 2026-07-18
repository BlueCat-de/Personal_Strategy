# Architecture

## Data Flow

```text
Tushare Pro
    |
    v
PIT market builder
    |-- raw OHLC for execution and valuation
    |-- adjusted OHLC for factors
    |-- listing/ST/suspension/limit state
    v
Local CSV data store
    |
    +--> strategy target weights
    |
    +--> next-open backtest engine
    |
    +--> reports / daily notifications
```

The system is local-first. Tushare is used as a data source, not as an
execution or backtesting service.

## Package Boundaries

### `ashare_quant.data`

- `tushare`: API authentication, retry logic, symbol normalization, and raw endpoints.
- `builder`: daily bundle normalization and atomic file writes.
- `pit`: historical point-in-time universe and market table construction.
- `industries`: historical Shenwan level-one membership.

### `ashare_quant.strategies`

- `v4`: weekly high-conviction defensive strategy with daily exits.
- `stable`: diversified stock-only low-volatility/value strategy.

Strategies produce target weights. They do not submit broker orders.

### `ashare_quant.backtest`

The execution engine applies:

- one-session signal delay;
- next-day raw open execution;
- separate buy and sell costs;
- minimum transaction fees;
- configurable bilateral slippage;
- 100-share board lots;
- no negative cash;
- suspension and price-limit blocking.

### `ashare_quant.research`

- `factors`: monthly PIT factor panels and IC utilities.
- `stability`: parameter, cross-section, and slippage experiments.

Research modules may be slower and generate large local outputs under `data/`.

### `ashare_quant.automation`

The daemon coordinates the PIT data builder, v4 strategy, local state, logs,
and optional Feishu/Lark notifications.

## Point-in-Time Contract

The main market file is:

```text
data/offline/a_share_history_tushare/prices_long.csv
```

Required columns:

```text
date,symbol,
open,high,low,close,
raw_open,raw_high,raw_low,raw_close,
adj_factor,volume,amount,turnover,
up_limit,down_limit,is_suspended,is_st,is_listed,
list_date,delist_date,ts_code,name
```

Rules:

1. `open/high/low/close` are point-in-time adjusted prices used by factors.
2. `raw_open/raw_close` are unadjusted prices used by execution and valuation.
3. Historical eligibility uses state effective on the signal date.
4. Financial or membership data may only enter after its publication/effective date.
5. Forward returns are research labels and must never enter production signals.

## Storage Policy

The source repository contains code and documentation only.

Ignored local paths:

```text
data/       market data, caches, snapshots, and backtests
logs/       runtime logs
run/        PID and scheduler state
.env.local  Tushare token
.feishu_webhook
```

## Extension Points

New strategies should:

1. accept a normalized `prices: dict[str, DataFrame]`;
2. produce `dict[pd.Timestamp, pd.Series]` target weights;
3. avoid broker- or provider-specific execution logic;
4. include PIT and slippage tests;
5. document whether parameters were selected in-sample or out-of-sample.
