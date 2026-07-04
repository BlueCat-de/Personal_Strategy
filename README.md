# Personal Strategy

A-share stock data extraction and feature engineering toolkit for personal quantitative research.

This project currently provides a Python pipeline based on `akshare` to fetch daily A-share market data, filter stocks that are not tradable for the current account setup, and generate machine-learning-ready features.

> This repository is for data research only. It is not investment advice and should not be used as a standalone trading system.

## Features

- Fetch daily A-share OHLCV data with `akshare`
- Default front-adjusted price data using `qfq`
- Automatically exclude ChiNext stocks by default, filtering stock codes starting with `300` or `301`
- Optional exclusion of ST stocks
- Generate one feature CSV per stock
- Provide future-return labels for supervised learning experiments

## Feature Groups

The pipeline generates the following groups of fields:

- Basic market data: `open`, `close`, `high`, `low`, `volume`, `amount`, `amplitude`, `pct_chg`, `change`, `turnover_rate`
- Moving averages: `ma5`, `ma10`, `ma20`, `ma60`
- Price-to-MA gaps: `ma*_gap`
- Volume moving averages: `volume_ma5`, `volume_ma10`, `volume_ma20`, `volume_ma60`
- Momentum indicators: `rsi6`, `rsi12`, `rsi24`
- Bollinger Bands: `boll_mid`, `boll_upper`, `boll_lower`, `boll_width`, `boll_position`
- MACD: `macd_dif`, `macd_dea`, `macd_hist`
- KDJ: `kdj_k`, `kdj_d`, `kdj_j`
- Williams %R: `wr14`
- Rolling statistics: return, volatility, trend strength, price position, high/low distance, volume change, volume z-score
- Time features: year, month, quarter, day, weekday, day of year, month start/end flags
- Supervised learning labels: `target_return_1d`, `target_return_5d`, `target_return_10d`, `target_up_1d`, `target_up_5d`, `target_up_10d`

## Installation

Use Python 3.10+ if possible.

```bash
pip install -r requirements.txt
```

Dependencies:

- `akshare`
- `pandas`
- `numpy`

## Usage

Fetch features for specific stocks:

```bash
python stock_feature_pipeline.py \
  --symbols 002460,000001,600519 \
  --start-date 20230101 \
  --end-date 20260704
```

Fetch all A-share stocks after default filters:

```bash
python stock_feature_pipeline.py \
  --all-a \
  --start-date 20230101 \
  --end-date 20260704
```

Limit the number of stocks during testing:

```bash
python stock_feature_pipeline.py \
  --all-a \
  --start-date 20230101 \
  --end-date 20260704 \
  --limit 50
```

Exclude ST stocks as well:

```bash
python stock_feature_pipeline.py \
  --all-a \
  --start-date 20230101 \
  --end-date 20260704 \
  --exclude-st
```

Include ChiNext stocks if your account can trade them:

```bash
python stock_feature_pipeline.py \
  --all-a \
  --start-date 20230101 \
  --end-date 20260704 \
  --include-chinext
```

## Output

By default, CSV files are written to:

```bash
data/features/
```

Each stock produces one file:

```text
data/features/002460_features.csv
```

You can change the output path:

```bash
python stock_feature_pipeline.py \
  --symbols 002460 \
  --start-date 20230101 \
  --end-date 20260704 \
  --output-dir data/generation_001
```

## Important Modeling Notes

- Do not train with `target_*` columns as input features. They are labels and contain future information.
- Use time-based train, validation, and test splits. Random splitting can cause leakage in financial time-series tasks.
- Backtest with transaction costs, slippage, suspension days, limit-up/limit-down constraints, and realistic position sizing.
- A high prediction accuracy does not necessarily imply positive returns. Evaluate risk-adjusted return, drawdown, turnover, and stability across market regimes.
- For personal investing, this pipeline is better used for screening and risk monitoring than for direct buy/sell automation.

## Current Files

- `stock_feature_pipeline.py`: main data extraction and feature engineering script
- `requirements.txt`: Python dependencies
- `README.md`: project documentation

