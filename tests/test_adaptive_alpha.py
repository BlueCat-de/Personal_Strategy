import unittest

import pandas as pd

from ashare_quant.research.stability import select_industry_neutral
from ashare_quant.strategies.adaptive_alpha import (
    AdaptiveAlphaConfig,
    ResearchStatus,
    rebalance_dates,
    style_for_breadth,
)


class AdaptiveAlphaTest(unittest.TestCase):
    def test_style_for_breadth_uses_frozen_threshold(self) -> None:
        self.assertEqual(style_for_breadth(0.7599, 0.76), "relative_strength")
        self.assertEqual(style_for_breadth(0.76, 0.76), "large_cap")

    def test_rebalance_dates_use_second_month_of_each_pair(self) -> None:
        dates = list(pd.date_range("2021-07-01", periods=6, freq="MS"))

        self.assertEqual(
            rebalance_dates(dates, AdaptiveAlphaConfig()),
            [
                pd.Timestamp("2021-08-01"),
                pd.Timestamp("2021-10-01"),
                pd.Timestamp("2021-12-01"),
            ],
        )

    def test_relative_sleeve_limits_industry_concentration(self) -> None:
        ranked = pd.Series(
            range(10, 0, -1),
            index=[f"{index:06d}" for index in range(10)],
            dtype=float,
        )
        industries = pd.Series(
            ["A", "A", "A", "A", "B", "B", "C", "C", "D", "D"],
            index=ranked.index,
        )

        selected = select_industry_neutral(ranked, industries, 8, 0.20)
        selected_industries = industries.loc[selected]

        self.assertEqual(len(selected), 8)
        self.assertEqual(selected_industries.value_counts().max(), 2)

    def test_research_status_blocks_production_freeze(self) -> None:
        status = ResearchStatus()

        self.assertFalse(status.production_eligible)
        self.assertEqual(status.forward_oos_start, "2026-07-20")


if __name__ == "__main__":
    unittest.main()
