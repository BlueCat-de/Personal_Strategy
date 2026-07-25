from __future__ import annotations

import unittest

import pandas as pd

from ashare_quant.research.score_layer_blend import (
    development_selection,
    mixed_score,
    select_target,
)


class ScoreLayerBlendTest(unittest.TestCase):
    def test_zero_offensive_weight_keeps_defensive_coverage(self) -> None:
        defensive = pd.Series({"A": 2.0, "B": 1.0})
        offensive = pd.Series({"B": 3.0})

        result = mixed_score(defensive, offensive, 0.0)

        pd.testing.assert_series_equal(result, defensive)

    def test_selection_has_exact_requested_holdings(self) -> None:
        score = pd.Series({"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0})
        industries = pd.Series({"A": "I1", "B": "I2", "C": "I3", "D": "I4"})
        target, selected = select_target(score, industries, score.index, 3)

        self.assertEqual(selected, ["A", "B", "C"])
        self.assertEqual(int(target.gt(0.0).sum()), 3)
        self.assertAlmostEqual(target.sum(), 1.0)

    def test_development_selection_requires_both_offsets(self) -> None:
        rows = []
        for offset in (0, 1):
            rows.append(
                {
                    "family": "pass",
                    "offensive_weight": 0.2,
                    "positions": 8,
                    "offset": offset,
                    "development_1_annualized_return_delta_vs_baseline": 0.01,
                    "development_2_annualized_return_delta_vs_baseline": 0.02,
                    "development_1_max_drawdown_delta_vs_baseline": -0.01,
                    "development_2_max_drawdown_delta_vs_baseline": -0.02,
                    "validation_annualized_return_delta_vs_baseline": -10.0,
                }
            )
            rows.append(
                {
                    "family": "fail",
                    "offensive_weight": 0.2,
                    "positions": 8,
                    "offset": offset,
                    "development_1_annualized_return_delta_vs_baseline": (
                        0.01 if offset == 0 else -0.01
                    ),
                    "development_2_annualized_return_delta_vs_baseline": 0.02,
                    "development_1_max_drawdown_delta_vs_baseline": 0.0,
                    "development_2_max_drawdown_delta_vs_baseline": 0.0,
                    "validation_annualized_return_delta_vs_baseline": 10.0,
                }
            )

        selected = development_selection(pd.DataFrame(rows))

        self.assertEqual(selected["family"].tolist(), ["pass"])


if __name__ == "__main__":
    unittest.main()
