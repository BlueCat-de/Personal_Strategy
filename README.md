# Personal Strategy

A-share stock data extraction and feature engineering toolkit for personal quantitative research.

This project currently provides:

- A Python pipeline based on `akshare` to fetch daily A-share market data, filter stocks that are not tradable for the current account setup, and generate machine-learning-ready features.
- A configurable crawler that searches public quantitative trading strategy source code, applies heuristic quality scoring, and saves accepted strategies under `strategies/`.

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
- `requests`
- `beautifulsoup4`
- `urllib3`

## Stock Feature Pipeline Usage

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
- `strategy_crawler.py`: public quantitative strategy crawler
- `bigquant_strategy_scraper.py`: BigQuant strategy page scraper for known playground URLs
- `crawler_config.json`: unified crawler configuration for GitHub, public platforms, custom URLs, and local fixture tests
- `strategies/`: saved strategy source code directory
- `requirements.txt`: Python dependencies
- `README.md`: project documentation

## Strategy Crawler

`strategy_crawler.py` crawls public quantitative strategy source code from configured sources. All crawler sources are configured in `crawler_config.json`.

The crawler is designed for compliant public data collection:

- Uses public APIs where possible
- Respects `robots.txt` for normal web pages
- Applies request delays and HTTP retries
- Does not bypass login walls, captchas, paywalls, or access controls
- Uses local state for resumable crawling and deduplication

### Configure Sources

Edit `crawler_config.json`:

```json
{
  "crawler": {
    "max_items": 50,
    "max_items_per_source": 20,
    "request_delay_seconds": 3.0,
    "respect_robots_txt": true
  },
  "quality": {
    "min_score": 35,
    "min_lines": 20,
    "allowed_extensions": [".py"]
  }
}
```

For GitHub, add or change search queries:

```json
{
  "name": "github",
  "type": "github_code_search",
  "enabled": true,
  "queries": [
    "language:Python filename:.py backtrader strategy trading",
    "language:Python filename:.py alpha factor backtest"
  ]
}
```

For a public index page:

```json
{
  "name": "my_public_index",
  "type": "html_index",
  "enabled": true,
  "url": "https://example.com/public-strategy-index/",
  "allow_url_patterns": ["\\.py$", "strategy", "backtest"]
}
```

For a known list of public raw strategy URLs:

```json
{
  "name": "custom_public_urls",
  "type": "url_list",
  "enabled": true,
  "urls": [
    {
      "url": "https://example.com/path/to/public_strategy.py",
      "name": "public_strategy.py",
      "platform": "example"
    }
  ]
}
```

For public strategy article pages, use `web_strategy_pages`. This source type opens public index/tutorial/community pages, discovers matching article links, extracts Python-like code blocks from `pre`/`code` HTML nodes, then applies the normal quality filter.

```json
{
  "name": "quantconnect",
  "type": "web_strategy_pages",
  "enabled": true,
  "same_domain": true,
  "start_urls": ["https://www.quantconnect.com/docs/v2/writing-algorithms"],
  "article_url_patterns": ["quantconnect\\.com/docs", "algorithm", "strategy"]
}
```

The unified config includes public page sources for:

- `joinquant`
- `ricequant`
- `bigquant`
- `quantconnect`

These sources respect `robots.txt`. If a site disallows crawling, the crawler logs the skip and continues with other sources.

Current platform status from dry-run checks:

| Source | Status | Notes |
| --- | --- | --- |
| `quantconnect` | Works | Public docs pages expose extractable code blocks. |
| `bigquant` | Works after config narrowing | The config starts from wiki collection pages and discovers `/wiki/doc/` article pages. |
| `joinquant` | Limited | Public community entry pages are client-rendered and static HTML does not expose article links or code blocks. Browser rendering or a documented public API is needed for reliable crawling. |
| `ricequant` | Blocked by robots.txt | The configured public paths are disallowed by robots.txt, so the crawler skips them. |

### Run the Crawler

Run the built-in local fixture test without network access:

```bash
python strategy_crawler.py \
  --config crawler_config.json \
  --sources local_fixture \
  --state-file .crawler_state/local_test_state.json \
  --log-file logs/local_test_crawler.log \
  --max-items 5
```

This reads `tests/fixtures/mock_strategies.json`, which contains 5 simulated strategy source files. Expected output:

```text
strategies/local_fixture/
  factor/
  machine_learning/
  mean_reversion/
  momentum/
  pairs_trading/
```

Dry run without saving strategy files:

```bash
python strategy_crawler.py --dry-run --max-items 10
```

Dry run only QuantConnect public pages:

```bash
python strategy_crawler.py \
  --config crawler_config.json \
  --sources quantconnect \
  --state-file .crawler_state/quantconnect_dry_run_state.json \
  --log-file logs/quantconnect_dry_run.log \
  --dry-run \
  --max-items 1
```

Run public platform page crawling and save accepted strategies:

```bash
python strategy_crawler.py \
  --config crawler_config.json \
  --sources bigquant,quantconnect \
  --output-dir strategies \
  --state-file .crawler_state/platforms_state.json \
  --log-file logs/platforms_crawler.log \
  --max-items 10
```

Run and save accepted strategies:

```bash
python strategy_crawler.py --max-items 20
```

Use a GitHub token to increase API rate limits:

```bash
export GITHUB_TOKEN=your_github_token
python strategy_crawler.py --max-items 50
```

Or pass it directly:

```bash
python strategy_crawler.py --github-token your_github_token --max-items 50
```

### Crawler Output

Accepted strategy source files are saved by source and detected strategy type:

```text
strategies/
  github/
    momentum/
      example_strategy.py
    mean_reversion/
      example_strategy.py
```

Each saved strategy also has a sidecar metadata file:

```text
example_strategy.py.meta.json
```

Metadata includes source URL, repository information, quality score, detected strategy type, content hash, and scoring reasons.

### Logs and Resume State

Crawler logs:

```text
logs/crawler.log
```

Resume and deduplication state:

```text
.crawler_state/state.json
```

If the crawler is interrupted, rerun the same command and it will skip URLs and content hashes already recorded in the state file.

### Quality Scoring

The crawler does not guarantee that a strategy is profitable. It scores code using explainable heuristics:

- Non-empty code length
- Strategy, signal, backtest, portfolio, risk, and performance keywords
- Common framework names such as `backtrader`, `zipline`, `vnpy`, `rqalpha`, `jqdata`, `quantconnect`
- Strategy lifecycle functions such as `initialize`, `handle_data`, `next`, `on_bar`, `rebalance`
- GitHub stars and forks
- Duplicate content hash filtering
- Penalties for placeholder or low-quality patterns

You can tune `quality.min_score` and `quality.min_lines` in `crawler_config.json`.

## BigQuant Strategy Page Scraper

If you already have a list of BigQuant strategy playground pages, put one URL per line in `strategies/BigQuant/webs`, then run:

```bash
python3 bigquant_strategy_scraper.py \
  --input-file strategies/BigQuant/webs \
  --output-dir strategies/BigQuant \
  --log-file logs/bigquant_strategy_scraper.log \
  --delay 1
```

The scraper saves each strategy under:

```text
strategies/BigQuant/{trading_style}/{strategy_title}_{uuid}/
```

Each strategy folder contains:

- `strategy.py`: extracted BigQuant strategy code
- `performance.json`: performance summary and raw daily return rows
- `performance.csv`: tabular performance series
- `summary.md`: readable summary with style and key return metrics
- `metadata.json`: source URL, strategy ID, save status, and errors if any
- `article.md`: public article text from the BigQuant strategy page

The current style labels include `multi_factor`, `machine_learning`, `etf_allocation`, `trend_momentum`, `convertible_bond`, `intraday`, `value_quality`, and `general`.
