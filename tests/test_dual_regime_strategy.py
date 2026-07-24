from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from ashare_quant.research.dual_regime_strategy import (
    build_market_features,
    classify_raw_regimes,
    confirm_regime,
    select_development_candidates,
)


class DualRegimeStrategyTest(unittest.TestCase):
    def test_confirmation_requires_consecutive_evidence(self) -> None:
        raw = pd.Series(
            [
                "transition",
                "risk_on",
                "risk_on",
                "transition",
                "risk_on",
                "risk_on",
                "risk_on",
                "risk_off",
                "risk_off",
                "risk_off",
            ]
        )
        result = confirm_regime(raw, confirmation_days=3)
        self.assertEqual(result.iloc[2], "transition")
        self.assertEqual(result.iloc[6], "risk_on")
        self.assertEqual(result.iloc[9], "risk_off")

    def test_future_prices_do_not_change_past_features(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=180)
        close = pd.DataFrame(
            {
                "000001": np.linspace(10.0, 20.0, len(dates)),
                "600000": np.linspace(20.0, 15.0, len(dates)),
            },
            index=dates,
        )
        flags = pd.DataFrame(1, index=dates, columns=close.columns)
        zeros = pd.DataFrame(0, index=dates, columns=close.columns)
        amount = pd.DataFrame(1_000_000.0, index=dates, columns=close.columns)
        limits = close * 1.1
        prices = {
            "close": close,
            "raw_close": close,
            "is_listed": flags,
            "is_st": zeros,
            "amount": amount,
            "up_limit": limits,
        }
        baseline = build_market_features(prices)
        changed = {key: value.copy() for key, value in prices.items()}
        changed["close"].iloc[-20:] *= 5.0
        changed["raw_close"].iloc[-20:] *= 5.0
        changed["up_limit"].iloc[-20:] *= 5.0
        modified = build_market_features(changed)
        pd.testing.assert_series_equal(
            baseline.loc[dates[-21]],
            modified.loc[dates[-21]],
        )

    def test_unlisted_stocks_do_not_dilute_breadth(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=140)
        close = pd.DataFrame(
            {
                "active": np.linspace(10.0, 20.0, len(dates)),
                "future": np.nan,
            },
            index=dates,
        )
        prices = {
            "close": close,
            "raw_close": close,
            "is_listed": pd.DataFrame({"active": 1, "future": 0}, index=dates),
            "is_st": pd.DataFrame(0, index=dates, columns=close.columns),
            "amount": pd.DataFrame({"active": 1_000_000.0, "future": np.nan}, index=dates),
            "up_limit": close * 1.1,
        }
        features = build_market_features(prices)
        self.assertEqual(features.iloc[-1]["breadth120"], 1.0)
        regimes = classify_raw_regimes(features)
        self.assertEqual(regimes.iloc[-1]["slow_trend"], "risk_on")

    def test_development_selection_ignores_validation(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "case": "pass",
                    "development_1_annualized_return_delta_vs_baseline": 0.01,
                    "development_2_annualized_return_delta_vs_baseline": 0.02,
                    "development_1_max_drawdown_delta_vs_baseline": -0.01,
                    "development_2_max_drawdown_delta_vs_baseline": -0.01,
                    "development_1_excess_delta_vs_baseline": 0.01,
                    "validation_annualized_return_delta_vs_baseline": -10.0,
                },
                {
                    "case": "fail",
                    "development_1_annualized_return_delta_vs_baseline": -0.01,
                    "development_2_annualized_return_delta_vs_baseline": 0.02,
                    "development_1_max_drawdown_delta_vs_baseline": 0.00,
                    "development_2_max_drawdown_delta_vs_baseline": 0.00,
                    "development_1_excess_delta_vs_baseline": 0.01,
                    "validation_annualized_return_delta_vs_baseline": 10.0,
                },
            ]
        )
        selected = select_development_candidates(frame)
        self.assertEqual(selected["case"].tolist(), ["pass"])


if __name__ == "__main__":
    unittest.main()
