import unittest

import pandas as pd

from ashare_quant.backtest import BacktestConfig, run_local_backtest


def sample_prices() -> dict[str, pd.DataFrame]:
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    values = pd.DataFrame({"000001": [10.0, 10.0, 10.0]}, index=dates)
    flags = pd.DataFrame({"000001": [0.0, 0.0, 0.0]}, index=dates)
    limits = pd.DataFrame({"000001": [float("nan")] * 3}, index=dates)
    return {
        "close": values,
        "open": values,
        "raw_close": values,
        "raw_open": values,
        "up_limit": limits,
        "down_limit": limits,
        "is_suspended": flags,
    }


class SlippageBacktestTest(unittest.TestCase):
    def test_zero_slippage_preserves_open_execution(self) -> None:
        prices = sample_prices()
        dates = prices["close"].index
        targets = {
            dates[0]: pd.Series({"000001": 1.0}),
            dates[1]: pd.Series({"000001": 0.0}),
        }
        result = run_local_backtest(
            prices,
            targets,
            BacktestConfig(initial_cash=10_000.0, buy_cost=0.0, sell_cost=0.0, min_cost=0.0),
            strategy_name="zero_slippage",
        )

        self.assertEqual(result.summary["final_equity"], 10_000.0)
        self.assertEqual(result.summary["total_slippage_cost"], 0.0)
        self.assertTrue((result.trades["price"] == result.trades["reference_price"]).all())

    def test_slippage_moves_both_execution_sides(self) -> None:
        prices = sample_prices()
        dates = prices["close"].index
        targets = {
            dates[0]: pd.Series({"000001": 1.0}),
            dates[1]: pd.Series({"000001": 0.0}),
        }
        result = run_local_backtest(
            prices,
            targets,
            BacktestConfig(
                initial_cash=10_000.0,
                buy_cost=0.0,
                sell_cost=0.0,
                slippage=0.01,
                min_cost=0.0,
            ),
            strategy_name="one_percent_slippage",
        )

        buy = result.trades[result.trades["side"] == "buy"].iloc[0]
        sell = result.trades[result.trades["side"] == "sell"].iloc[0]
        self.assertAlmostEqual(buy["price"], 10.1)
        self.assertAlmostEqual(sell["price"], 9.9)
        self.assertAlmostEqual(result.summary["total_slippage_cost"], 180.0)
        self.assertAlmostEqual(result.summary["final_equity"], 9_820.0)

    def test_negative_slippage_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-negative"):
            run_local_backtest(
                sample_prices(),
                {},
                BacktestConfig(slippage=-0.001),
                strategy_name="invalid",
            )


if __name__ == "__main__":
    unittest.main()
