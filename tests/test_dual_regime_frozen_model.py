from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from ashare_quant.research.dual_regime_frozen_model import (
    FrozenLinearModel,
    choices_from_model,
    fit_fixed_ridge_classifier,
)


class DualRegimeFrozenModelTest(unittest.TestCase):
    def test_fit_is_deterministic(self) -> None:
        generator = np.random.default_rng(11)
        frame = pd.DataFrame(
            {
                "x1": generator.normal(size=60),
                "x2": generator.normal(size=60),
            }
        )
        frame["label"] = np.where(frame["x1"] + frame["x2"] > 0.0, 1.0, -1.0)
        first = fit_fixed_ridge_classifier(frame, ("x1", "x2"))
        second = fit_fixed_ridge_classifier(frame, ("x1", "x2"))
        self.assertEqual(first, second)

    def test_training_scaler_does_not_read_later_rows(self) -> None:
        frame = pd.DataFrame(
            {
                "x1": np.arange(40, dtype=float),
                "x2": np.arange(40, dtype=float) ** 2,
                "label": np.where(np.arange(40) % 2, 1.0, -1.0),
            }
        )
        baseline = fit_fixed_ridge_classifier(frame.iloc[:30], ("x1", "x2"))
        changed = frame.copy()
        changed.loc[30:, ["x1", "x2"]] = 1e12
        modified = fit_fixed_ridge_classifier(changed.iloc[:30], ("x1", "x2"))
        self.assertEqual(baseline, modified)

    def test_frozen_choices_ignore_future_feature_rows(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=3)
        model = FrozenLinearModel(
            feature_names=("x",),
            means=(0.0,),
            scales=(1.0,),
            coefficients=(1.0,),
            intercept=0.0,
        )
        frame = pd.DataFrame({"x": [1.0, -1.0, 1.0]}, index=dates)
        baseline = choices_from_model(model, frame, list(dates), dates[0])
        changed = frame.copy()
        changed.loc[dates[-1], "x"] = -1000.0
        modified = choices_from_model(model, changed, list(dates), dates[0])
        self.assertEqual(
            baseline.loc[baseline["date"] == dates[1], "sleeve"].iloc[0],
            modified.loc[modified["date"] == dates[1], "sleeve"].iloc[0],
        )


if __name__ == "__main__":
    unittest.main()
