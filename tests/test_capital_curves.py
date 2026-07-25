import unittest

import pandas as pd

from ashare_quant.visualization.capital_curves import Curve, merge_curves, summary


class CapitalCurvesTest(unittest.TestCase):
    def test_merge_curves_keeps_common_dates(self) -> None:
        dates = pd.to_datetime(["2026-01-05", "2026-01-06"])
        first = Curve("50k", "#f59f00", pd.Series([100.0, 110.0], index=dates))
        second = Curve("hs300", "#495057", pd.Series([100.0, 105.0], index=dates))

        actual = merge_curves([first, second])

        self.assertEqual(actual.columns.tolist(), ["50k", "hs300"])
        self.assertEqual(len(actual), 2)

    def test_summary_reports_total_return_and_drawdown(self) -> None:
        dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
        frame = pd.DataFrame({"100k": [100.0, 120.0, 108.0]}, index=dates)

        actual = summary(frame).set_index("series").loc["100k"]

        self.assertEqual(actual["total_return"], 8.0)
        self.assertEqual(actual["max_drawdown"], -10.0)


if __name__ == "__main__":
    unittest.main()
