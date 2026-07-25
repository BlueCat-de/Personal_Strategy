from __future__ import annotations

import unittest

import pandas as pd

from ashare_quant.research.dual_regime_dual_offset_model import combine_raw_perf


class DualRegimeDualOffsetModelTest(unittest.TestCase):
    def test_combined_account_averages_two_compounded_subaccounts(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=3)
        first = pd.DataFrame({"date": dates, "portfolio_value": [100.0, 110.0, 121.0]})
        second = pd.DataFrame({"date": dates, "portfolio_value": [100.0, 90.0, 81.0]})
        combined = combine_raw_perf(first, second, 100.0)
        self.assertEqual(combined["portfolio_value"].tolist(), [100.0, 100.0, 101.0])
        self.assertAlmostEqual(combined.iloc[-1]["returns"], 0.01)


if __name__ == "__main__":
    unittest.main()
