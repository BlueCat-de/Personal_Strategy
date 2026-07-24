from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from ashare_quant.research.dual_regime_meta_strategy import (
    online_choices,
    oracle_choices,
    targets_from_choices,
    trailing_relative_signal,
)


class DualRegimeMetaStrategyTest(unittest.TestCase):
    def test_trailing_signal_uses_only_history_through_signal(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=100)
        defense = pd.Series(np.linspace(100.0, 110.0, len(dates)), index=dates)
        offense = pd.Series(np.linspace(100.0, 130.0, len(dates)), index=dates)
        signal_date = dates[70]
        baseline = trailing_relative_signal(defense, offense, signal_date, 60)
        modified = offense.copy()
        modified.loc[modified.index > signal_date] *= 0.01
        self.assertEqual(
            baseline,
            trailing_relative_signal(defense, modified, signal_date, 60),
        )

    def test_online_choice_prefers_stronger_trailing_sleeve(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=300)
        defense = pd.Series(np.linspace(100.0, 110.0, len(dates)), index=dates)
        offense = pd.Series(np.linspace(100.0, 160.0, len(dates)), index=dates)
        choices = online_choices(defense, offense, [dates[-1]], "relative_consensus")
        self.assertEqual(choices.iloc[0]["sleeve"], "offense")

    def test_oracle_uses_next_interval_for_diagnostic_ceiling(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=10)
        defense = pd.Series([100, 100, 100, 100, 100, 110, 110, 110, 110, 110], index=dates)
        offense = pd.Series([100, 100, 100, 100, 100, 130, 130, 130, 130, 130], index=dates)
        choices = oracle_choices(defense, offense, [dates[0], dates[4], dates[8]])
        self.assertEqual(choices.iloc[0]["sleeve"], "offense")
        self.assertGreater(
            choices.iloc[0]["label_available_date"],
            choices.iloc[0]["date"],
        )

    def test_targets_follow_choice_without_blending(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=2)
        defense = {
            dates[0]: pd.Series({"A": 1.0, "B": 0.0}),
            dates[1]: pd.Series({"A": 1.0, "B": 0.0}),
        }
        offense = {
            dates[0]: pd.Series({"A": 0.0, "B": 1.0}),
            dates[1]: pd.Series({"A": 0.0, "B": 1.0}),
        }
        choices = pd.DataFrame({"date": dates, "sleeve": ["defense", "offense"]})
        targets = targets_from_choices(defense, offense, choices)
        self.assertEqual(targets[dates[0]]["A"], 1.0)
        self.assertEqual(targets[dates[1]]["B"], 1.0)


if __name__ == "__main__":
    unittest.main()
