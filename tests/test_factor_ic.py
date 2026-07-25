import unittest

import pandas as pd

from ashare_quant.research.factors import factor_ic_table


class FactorICTest(unittest.TestCase):
    def test_spearman_ic_does_not_require_scipy(self) -> None:
        index = [f"S{number:02d}" for number in range(30)]
        panel = pd.DataFrame(
            {
                "eligible": True,
                "factor": range(30),
                "forward20": range(30),
                "forward20_available_date": pd.Timestamp("2020-02-03"),
            },
            index=index,
        )

        result = factor_ic_table({pd.Timestamp("2020-01-02"): panel}, ["factor"])

        self.assertAlmostEqual(result.loc[0, "ic"], 1.0)


if __name__ == "__main__":
    unittest.main()
