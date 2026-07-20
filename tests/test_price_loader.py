import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from ashare_quant.strategies.v4 import load_prices


class PriceLoaderTest(unittest.TestCase):
    def test_loads_adjustment_factor_for_corporate_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prices.csv"
            pd.DataFrame(
                {
                    "date": ["2020-01-02", "2020-01-03"],
                    "symbol": ["000001", "000001"],
                    "open": [10.0, 10.0],
                    "close": [10.0, 10.0],
                    "raw_open": [10.0, 5.0],
                    "raw_close": [10.0, 5.0],
                    "adj_factor": [1.0, 2.0],
                    "volume": [100.0, 100.0],
                    "amount": [100_000.0, 50_000.0],
                    "turnover": [0.01, 0.01],
                }
            ).to_csv(path, index=False)
            config = SimpleNamespace(
                warmup_start_date="2020-01-01",
                end_date="2020-01-31",
            )

            prices = load_prices(path, config)

            self.assertIn("adj_factor", prices)
            self.assertEqual(prices["adj_factor"].iloc[-1, 0], 2.0)


if __name__ == "__main__":
    unittest.main()
