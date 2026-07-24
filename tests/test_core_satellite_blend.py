from __future__ import annotations

import unittest

import pandas as pd

from ashare_quant.research.core_satellite_blend import (
    blend_targets,
    development_pass,
)


class CoreSatelliteBlendTest(unittest.TestCase):
    def test_blend_preserves_gross_and_merges_overlap(self) -> None:
        date = pd.Timestamp("2020-01-02")
        defense = {date: pd.Series({"A": 0.5, "B": 0.5, "C": 0.0})}
        offense = {date: pd.Series({"A": 0.0, "B": 0.5, "C": 0.5})}
        targets, debug = blend_targets(defense, offense, 0.2)
        self.assertAlmostEqual(targets[date].sum(), 1.0)
        self.assertAlmostEqual(targets[date]["A"], 0.4)
        self.assertAlmostEqual(targets[date]["B"], 0.5)
        self.assertAlmostEqual(targets[date]["C"], 0.1)
        self.assertEqual(debug.iloc[0]["overlap_names"], 1)
        self.assertEqual(debug.iloc[0]["combined_names"], 3)

    def test_selection_requires_both_offsets(self) -> None:
        rows = []
        for offset in (0, 1):
            rows.append(
                {
                    "family": "pass",
                    "satellite_weight": 0.2,
                    "positions": 8,
                    "offset": offset,
                    "development_1_annualized_return_delta_vs_baseline": 0.01,
                    "development_2_annualized_return_delta_vs_baseline": 0.02,
                    "development_1_max_drawdown_delta_vs_baseline": -0.01,
                    "development_2_max_drawdown_delta_vs_baseline": -0.02,
                    "validation_annualized_return_delta_vs_baseline": -10.0,
                    "historical_diagnostic_not_oos_annualized_return_delta_vs_baseline": -10.0,
                }
            )
            rows.append(
                {
                    "family": "fail",
                    "satellite_weight": 0.2,
                    "positions": 8,
                    "offset": offset,
                    "development_1_annualized_return_delta_vs_baseline": (
                        0.01 if offset == 0 else -0.01
                    ),
                    "development_2_annualized_return_delta_vs_baseline": 0.02,
                    "development_1_max_drawdown_delta_vs_baseline": 0.0,
                    "development_2_max_drawdown_delta_vs_baseline": 0.0,
                    "validation_annualized_return_delta_vs_baseline": 10.0,
                    "historical_diagnostic_not_oos_annualized_return_delta_vs_baseline": 10.0,
                }
            )
        selected = development_pass(pd.DataFrame(rows))
        self.assertEqual(selected["family"].tolist(), ["pass"])

    def test_invalid_weight_is_rejected(self) -> None:
        date = pd.Timestamp("2020-01-02")
        target = {date: pd.Series({"A": 1.0})}
        with self.assertRaises(ValueError):
            blend_targets(target, target, 1.1)


if __name__ == "__main__":
    unittest.main()
