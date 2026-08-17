# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses semantic versioning.

## [Unreleased]

### Added

- Evaluation framework: risk/return metrics, statistical significance (PSR,
  DSR, PBO/CSCV, Haircut Sharpe, MinTRL), CAPM attribution, stress and
  regime analysis, cost/turnover, reporting, and a unified pipeline.
- Framework-level sample discipline: train/validation/lockbox splits
  (2015–2021 / 2022–2023 / 2024+ unblinded once) and walk-forward folds
  with an embargo, promoted from private research into `ashare_quant`.
- Auxiliary/risk factor admission protocol — a second admission channel
  parallel to stock-level IC, judging overlays on the marginal portfolio
  delta against a frozen baseline, with standard applicators (vol-target,
  drawdown ladder, beta cap) and a six-case discrimination self-test.
- Dedicated ETF backtest engine for cross-asset rotation strategies
  (close execution, sell-before-buy, suspension-aware marking).
- Raw income / balance-sheet / cash-flow statement cache with PIT
  `available_date = ann_date + 1`, deployable on Ptrade `get_fundamentals`.
- Offset-neutral stock-only strategy asset, including frozen forward-validation
  status, capital-aware configuration, PIT regression tests, research report,
  and matplotlib capital-sensitivity charts.
- Historical industry-relative-strength research and PIT universe regression
  coverage.
- Strategy asset register documenting versioned code, local data boundaries,
  and forward-validation requirements.

### Changed

- README and the Chinese README now open with the framework-level pitch
  ("would you dare to trade this live?") and a five-pillar summary of the
  project's differentiation: PIT data sovereignty, skepticism-quantifying
  evaluation, dual-channel factor admission, dual-implementation validation,
  and agent-followable research governance.
- The offset-neutral strategy now accepts `--initial-cash` from CNY 50,000;
  CNY 200,000 remains a recommendation rather than a runtime restriction.
- Added matplotlib to the runtime dependency lists.

## [0.1.0] - 2026-07-18

### Added

- Installable `ashare_quant` package using the standard `src/` layout.
- Point-in-time Tushare market data builder.
- Next-open A-share backtest engine with bilateral slippage.
- v4 defensive strategy and diversified stock-only strategy.
- Shenwan historical industry membership.
- Parameter, cross-section, and slippage stability research.
- Daily data and signal daemon with optional Feishu/Lark notifications.
- English and Chinese documentation and GitHub community files.

### Changed

- Separated production modules, research modules, data adapters, and automation.
- Removed data snapshots from the Git publication surface.
- Replaced root-level script names with console commands.

### Removed

- Legacy nine-column updater that could overwrite the PIT market table.
- Duplicate and rejected ETF, regime-switch, and one-off research scripts.
