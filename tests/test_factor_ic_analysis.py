import unittest

import numpy as np
import pandas as pd

from ashare_quant.research.factor_ic_analysis import (
    deduplicate_factors,
    forward_open_return,
    neutralize_factor,
    quantile_diagnostics,
    rank_ic,
    select_stable_factors,
    universe_mask,
)


class FactorICAnalysisTest(unittest.TestCase):
    def test_research_universe_only_accepts_main_board(self) -> None:
        symbols = pd.Index(["600000", "000001", "300001", "688001", "920001"])

        actual = universe_mask(symbols, "main")

        self.assertEqual(actual.tolist(), [True, True, False, False, False])
        with self.assertRaisesRegex(ValueError, "Unsupported universe"):
            universe_mask(symbols, "main_chinext")

    def test_forward_return_starts_on_next_open(self) -> None:
        dates = pd.bdate_range("2026-01-05", periods=5)
        adjusted_open = pd.DataFrame(
            {"A": [10.0, 12.0, 18.0, 24.0, 30.0]},
            index=dates,
        )

        actual = forward_open_return(adjusted_open, signal_loc=0, end_loc=2)

        self.assertAlmostEqual(actual["A"], 1.0)

    def test_rank_ic_uses_cross_sectional_ranks(self) -> None:
        index = [f"S{number:02d}" for number in range(30)]
        factor = pd.Series(np.arange(30), index=index)
        forward = pd.Series(np.arange(30) ** 3, index=index)

        actual, sample = rank_ic(factor, forward)

        self.assertEqual(sample, 30)
        self.assertAlmostEqual(actual, 1.0)

    def test_quantile_diagnostics_reports_top_minus_bottom(self) -> None:
        index = [f"S{number:02d}" for number in range(50)]
        factor = pd.Series(np.arange(50), index=index)
        forward = factor / 100.0

        actual = quantile_diagnostics(factor, forward)

        self.assertGreater(actual["spread_q5_q1"], 0.0)
        self.assertAlmostEqual(actual["monotonicity"], 1.0)

    def test_neutralization_is_finite_with_redundant_industry_effects(self) -> None:
        index = [f"S{number:02d}" for number in range(60)]
        factor = pd.Series(np.arange(60, dtype=float), index=index)
        market_cap = pd.Series(np.geomspace(1e5, 1e9, 60), index=index)
        industries = pd.Series(["A"] * 30 + ["B"] * 30, index=index)

        actual = neutralize_factor(factor, market_cap, industries)

        self.assertEqual(actual.notna().sum(), 60)
        self.assertTrue(np.isfinite(actual.dropna()).all())
        self.assertAlmostEqual(actual.groupby(industries).mean().abs().max(), 0.0, places=10)

    def test_deduplication_keeps_stronger_correlated_factor(self) -> None:
        candidates = pd.DataFrame(
            {
                "factor": ["strong", "duplicate", "independent"],
                "stable": [True, True, True],
                "development_icir": [0.8, 0.6, 0.4],
                "development_ic_mean": [0.08, 0.07, 0.04],
            }
        )
        correlations = pd.DataFrame(
            {
                "factor_a": ["duplicate"],
                "factor_b": ["strong"],
                "correlation": [0.90],
            }
        )

        actual = deduplicate_factors(candidates, correlations)

        self.assertEqual(actual, ("strong", "independent"))

    def test_quarterly_stability_uses_cadence_specific_sample_floor(self) -> None:
        rows = []
        for period in ["development_1", "development_2"]:
            rows.append(
                {
                    "universe": "main",
                    "cadence": "quarterly",
                    "period": period,
                    "factor": "value",
                    "ic_mean": 0.05,
                    "positive_rate": 0.60,
                    "observations": 12,
                    "icir": 0.40,
                }
            )

        actual = select_stable_factors(pd.DataFrame(rows))

        self.assertTrue(actual.loc[0, "stable"])


if __name__ == "__main__":
    unittest.main()
