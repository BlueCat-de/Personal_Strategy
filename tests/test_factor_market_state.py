import unittest

import pandas as pd

from ashare_quant.research.factors import market_state_from_panel


class FactorMarketStateTest(unittest.TestCase):
    def test_market_state_uses_only_signal_date_eligible_stocks(self) -> None:
        panel = pd.DataFrame(
            {
                "eligible": [True, True, False],
                "mom120": [0.20, -0.10, 1.50],
                "mom20": [0.04, -0.02, 0.80],
            },
            index=["000001", "000002", "future_listing"],
        )
        returns = pd.DataFrame(
            {
                "000001": [0.01, -0.01, 0.02],
                "000002": [0.02, -0.02, 0.01],
                "future_listing": [0.50, -0.40, 0.60],
            }
        )

        state = market_state_from_panel(panel, returns)

        self.assertEqual(state["breadth120"], 0.5)
        self.assertAlmostEqual(state["median_mom120"], 0.05)
        self.assertAlmostEqual(state["median_mom20"], 0.01)
        self.assertLess(state["market_vol20"], 0.03)


if __name__ == "__main__":
    unittest.main()
