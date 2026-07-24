from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from ashare_quant.research.incremental_behavioral_robustness import (
    VARIANTS,
    development_selected_factors,
)
from ashare_quant.research.factor_ic_analysis import neutralize_factor
from ashare_quant.research.incremental_behavioral_factors import (
    FACTOR_SPECS,
    FinancialChangeStore,
    add_incremental_factors,
    incremental_residual,
    market_state,
)
from ashare_quant.strategies.main_board_bimonthly_ic import FROZEN_WEIGHTS


class IncrementalBehavioralFactorsTest(unittest.TestCase):
    def test_price_activity_factors_only_use_history_through_signal(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=140)
        symbols = pd.Index(["000001", "600000"])
        close = pd.DataFrame(
            {
                "000001": np.linspace(10.0, 20.0, len(dates)),
                "600000": np.linspace(10.0, 12.0, len(dates)),
            },
            index=dates,
        )
        amount = pd.DataFrame(
            {
                "000001": np.r_[np.ones(120) * 100.0, np.ones(20) * 200.0],
                "600000": np.ones(140) * 100.0,
            },
            index=dates,
        )
        prices = {
            "close": close,
            "raw_close": close,
            "amount": amount,
            "turnover": amount / 1000.0,
            "volume": amount,
            "up_limit": close * 1.1,
        }
        returns = close.pct_change(fill_method=None)
        panel = pd.DataFrame(index=symbols)
        industries = pd.Series({"000001": "银行", "600000": "银行"})
        result = add_incremental_factors(
            panel,
            prices,
            returns,
            len(dates) - 1,
            industries,
            pd.DataFrame(index=symbols),
        )
        self.assertAlmostEqual(result.loc["000001", "amount_acceleration20"], 1.0)
        self.assertAlmostEqual(result.loc["600000", "amount_acceleration20"], 0.0)
        self.assertGreater(
            result.loc["000001", "relative_strength60"],
            result.loc["600000", "relative_strength60"],
        )

    def test_financial_changes_advance_on_available_date(self) -> None:
        history = pd.DataFrame(
            [
                {
                    "symbol": "000001",
                    "ann_date": pd.Timestamp("2020-04-01"),
                    "available_date": pd.Timestamp("2020-04-02"),
                    "end_date": pd.Timestamp("2019-12-31"),
                    "q_sales_yoy": 10.0,
                    "ocf_yoy": 20.0,
                    "dt_netprofit_yoy": 30.0,
                },
                {
                    "symbol": "000001",
                    "ann_date": pd.Timestamp("2020-08-01"),
                    "available_date": pd.Timestamp("2020-08-02"),
                    "end_date": pd.Timestamp("2020-06-30"),
                    "q_sales_yoy": 15.0,
                    "ocf_yoy": 18.0,
                    "dt_netprofit_yoy": 40.0,
                },
            ]
        )
        store = FinancialChangeStore(history)
        before = store.latest(pd.Timestamp("2020-08-01"), pd.Index(["000001"]))
        after = store.latest(pd.Timestamp("2020-08-02"), pd.Index(["000001"]))
        self.assertTrue(pd.isna(before.loc["000001", "sales_growth_acceleration"]))
        self.assertEqual(after.loc["000001", "sales_growth_acceleration"], 5.0)
        self.assertEqual(after.loc["000001", "ocf_growth_acceleration"], -2.0)
        self.assertEqual(after.loc["000001", "profit_growth_acceleration"], 10.0)

    def test_industry_factors_start_at_sw2021_effective_date(self) -> None:
        industry_factors = [
            name
            for name, spec in FACTOR_SPECS.items()
            if spec.family in {"industry_rotation"}
            or name in {"industry_relative_strength60", "industry_limit_diffusion20"}
        ]
        self.assertTrue(industry_factors)
        self.assertTrue(
            all(FACTOR_SPECS[name].earliest_date == "2021-07-30" for name in industry_factors)
        )

    def test_development_selection_does_not_read_validation(self) -> None:
        rows = []
        for factor, first, second, validation in [
            ("stable", 0.3, 0.4, -10.0),
            ("weak", 0.1, 0.4, 10.0),
        ]:
            for period, icir in [
                ("development_1", first),
                ("development_2", second),
                ("validation", validation),
            ]:
                rows.append(
                    {
                        "universe": "all",
                        "cadence": "bimonthly",
                        "period": period,
                        "factor": factor,
                        "incremental_ic_mean": 0.01,
                        "incremental_icir": icir,
                    }
                )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "summary.csv"
            pd.DataFrame(rows).to_csv(path, index=False)
            self.assertEqual(development_selected_factors(path), ("stable",))

    def test_variant_weights_sum_to_one(self) -> None:
        for variant in VARIANTS:
            self.assertAlmostEqual(sum(variant.weights().values()), 1.0)

    def test_market_breadth_ignores_unavailable_stocks(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=121)
        close = pd.DataFrame(
            {
                "rising": np.linspace(10.0, 20.0, len(dates)),
                "falling": np.linspace(20.0, 19.0, len(dates)),
                **{f"future_{index}": np.nan for index in range(10)},
            },
            index=dates,
        )
        self.assertEqual(market_state({"close": close}, 120), "bull_broad")

    def test_exact_baseline_duplicate_has_no_incremental_residual(self) -> None:
        generator = np.random.default_rng(7)
        index = pd.Index([f"{value:06d}" for value in range(60)])
        sample = pd.DataFrame(index=index)
        sample["total_mv"] = np.exp(generator.normal(10.0, 1.0, len(index)))
        sample["industry"] = np.where(np.arange(len(index)) % 2, "A", "B")
        for factor in FROZEN_WEIGHTS:
            sample[factor] = generator.normal(size=len(index))
        candidate = -sample["reversal20"]
        neutral_candidate = neutralize_factor(candidate, sample["total_mv"], sample["industry"])
        residual = incremental_residual(neutral_candidate, sample)
        self.assertTrue(residual.isna().all())


if __name__ == "__main__":
    unittest.main()
