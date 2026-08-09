"""Canonical train / validation / test split boundaries — single source of truth.

**2026-08 REPARTITION (modern-market split):**
The original 2007-2013 training period was retired. Evidence: v2's walk-forward showed
systematic decay (early Sharpe 1.38 → late 0.49), and the A-share market structure
changed fundamentally post-2014 (Stock Connect, MSCI inclusion 2018, registration-based
IPO 2019, quant/institutional participation growth). Training on 2007-2013 teaches
patterns from a structurally different (retail-dominated, pre-reform) market.

New partition (all post-Stock-Connect + post-MSCI + registration-based era):
  development (train) : 2015-01-01 .. 2021-12-31  (7y — modern institutional market)
  validation (val)    : 2022-01-01 .. 2023-12-31  (2y — recent, pre-lockbox)
  test (lockbox)      : 2024-01-01 ..              (unveiled exactly once)

Legacy aliases are kept for backward compatibility (existing strategies/reports that
reference the old 2007-2013 split still run, just with explicit LEGACY_ prefix).
New development MUST use the modern split.

NOTE: `long_horizon.py` uses its own 2011-2020 scope (unchanged, different study).
"""

from __future__ import annotations

import pandas as pd

# ── MODERN-MARKET partition (canonical, 2026-08+) ──────────────────────────
DEVELOPMENT_START = pd.Timestamp("2015-01-01")
DEVELOPMENT_END = pd.Timestamp("2021-12-31")
VALIDATION_START = pd.Timestamp("2022-01-01")
VALIDATION_END = pd.Timestamp("2023-12-31")
TEST_START = pd.Timestamp("2024-01-01")

# ── Backward-compatible aliases (point to MODERN split) ────────────────────
DIAGNOSTIC_START = TEST_START          # legacy name (factor_ic_analysis / strategies)
CONFIRMATION_START = TEST_START        # legacy name (style_frequency)

# ── LEGACY split (2007-2013 train, kept for reference/re-comparison) ──────
LEGACY_DEVELOPMENT_START = pd.Timestamp("2007-01-01")
LEGACY_DEVELOPMENT_END = pd.Timestamp("2013-12-31")
LEGACY_VALIDATION_START = pd.Timestamp("2014-01-01")
LEGACY_VALIDATION_END = pd.Timestamp("2020-12-31")
LEGACY_TEST_START = pd.Timestamp("2021-01-01")

# Convenience tuples
TRAIN_RANGE = (DEVELOPMENT_START, DEVELOPMENT_END)
VAL_RANGE = (VALIDATION_START, VALIDATION_END)
TEST_RANGE = (TEST_START, pd.Timestamp("2026-12-31"))  # data availability cap

# Legacy convenience
LEGACY_TRAIN_RANGE = (LEGACY_DEVELOPMENT_START, LEGACY_DEVELOPMENT_END)
LEGACY_VAL_RANGE = (LEGACY_VALIDATION_START, LEGACY_VALIDATION_END)
