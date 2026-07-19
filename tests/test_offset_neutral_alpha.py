import unittest

import pandas as pd

from ashare_quant.strategies.offset_neutral_alpha import (
    OffsetNeutralAlphaConfig,
    ResearchStatus,
    equal_weight_target,
    latest_target,
)


class OffsetNeutralAlphaTest(unittest.TestCase):
    def test_config_supports_validated_capital_range(self) -> None:
        config = OffsetNeutralAlphaConfig()

        with self.assertRaises(ValueError):
            config.validate(49_999.0)
        config.validate(100_000.0)

    def test_sleeve_weights_sum_to_one(self) -> None:
        config = OffsetNeutralAlphaConfig(large_defensive_weight=0.34)

        with self.assertRaises(ValueError):
            config.validate(100_000.0)

    def test_capital_profile_distinguishes_supported_and_recommended_cash(self) -> None:
        config = OffsetNeutralAlphaConfig()

        supported = config.capital_profile(100_000.0)
        recommended = config.capital_profile(200_000.0)

        self.assertEqual(supported["capital_tier"], "supported_with_integer_lot_constraints")
        self.assertEqual(recommended["capital_tier"], "recommended")

    def test_latest_target_does_not_use_future_signal(self) -> None:
        columns = pd.Index(["A", "B"])
        targets = {
            pd.Timestamp("2026-01-05"): pd.Series([1.0, 0.0], index=columns),
            pd.Timestamp("2026-03-02"): pd.Series([0.0, 1.0], index=columns),
        }

        actual = latest_target(targets, pd.Timestamp("2026-02-02"))

        pd.testing.assert_series_equal(actual, targets[pd.Timestamp("2026-01-05")])

    def test_equal_weight_target_is_fully_invested(self) -> None:
        target = equal_weight_target(pd.Index(["A", "B", "C"]), ["A", "C"])

        self.assertAlmostEqual(target.sum(), 1.0)
        self.assertEqual(target["A"], 0.5)
        self.assertEqual(target["B"], 0.0)

    def test_frozen_candidate_still_requires_forward_validation(self) -> None:
        status = ResearchStatus()

        self.assertEqual(status.status, "frozen_for_forward_validation")
        self.assertFalse(status.production_eligible)


if __name__ == "__main__":
    unittest.main()
