import unittest

import pandas as pd

from ashare_quant.research.relative_strength import attach_relative_strength


class RelativeStrengthUniverseTest(unittest.TestCase):
    def test_industry_aggregates_exclude_ineligible_stocks(self) -> None:
        date = pd.Timestamp("2026-01-05")
        panel = pd.DataFrame(
            {
                "eligible": [True, True, False],
                "mom20": [0.10, -0.10, 3.00],
                "mom60": [0.20, -0.20, 3.00],
                "mom120": [0.30, -0.30, 3.00],
                "positive_ratio60": [0.60, 0.40, 1.00],
            },
            index=["000001", "000002", "future_listing"],
        )
        industries = {
            date: pd.Series(
                ["Bank", "Bank", "Bank"],
                index=panel.index,
            )
        }

        attach_relative_strength({date: panel}, industries)

        self.assertAlmostEqual(panel.loc["000001", "industry_mom20"], 0.0)
        self.assertAlmostEqual(panel.loc["000001", "industry_mom60"], 0.0)
        self.assertAlmostEqual(panel.loc["000001", "industry_breadth60"], 0.5)


if __name__ == "__main__":
    unittest.main()
