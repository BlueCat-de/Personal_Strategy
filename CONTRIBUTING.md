# Contributing

Contributions are welcome through GitHub issues and pull requests.

## Development Setup

```bash
git clone https://github.com/BlueCat-de/Personal_Strategy.git
cd Personal_Strategy
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Before Opening a Pull Request

```bash
ruff check src tests
python -m pytest
```

Keep pull requests focused. Do not include:

- Tushare or other vendor data;
- API tokens or webhook URLs;
- generated backtest outputs;
- runtime logs or PID files;
- unrelated formatting or refactoring.

## Quantitative Research Requirements

Changes to strategies or factors must document:

1. the economic hypothesis;
2. signal and execution timing;
3. point-in-time data availability;
4. transaction costs and slippage;
5. in-sample and out-of-sample boundaries;
6. parameter-neighborhood and cross-section robustness;
7. failed variants, not only the selected result.

Forward returns may be used as research labels but must not enter production
signals. Historical state must not be reconstructed from current security
metadata.

## Commit and Pull Request Style

- Use descriptive, imperative commit subjects.
- Explain behavior changes and migration impact.
- Add or update tests for execution and data-contract changes.
- Update both English and Chinese documentation when user-facing commands change.

By contributing, you agree that your contribution is licensed under GPL-3.0.
