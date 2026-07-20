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

    def test_rebalance_sizing_does_not_use_same_day_close(self) -> None:
        dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
        columns = ["A"]
        prices = {
            "raw_open": pd.DataFrame([[10.0], [10.0], [10.0]], index=dates, columns=columns),
            "raw_close": pd.DataFrame([[10.0], [10.0], [100.0]], index=dates, columns=columns),
            "open": pd.DataFrame([[10.0], [10.0], [10.0]], index=dates, columns=columns),
            "close": pd.DataFrame([[10.0], [10.0], [100.0]], index=dates, columns=columns),
        }
        targets = {
            dates[0]: pd.Series([1.0], index=columns),
            dates[1]: pd.Series([0.5], index=columns),
        }

        result = run_local_backtest(
            prices,
            targets,
            BacktestConfig(
                initial_cash=10_000.0,
                buy_cost=0.0,
                sell_cost=0.0,
                min_cost=0.0,
            ),
            strategy_name="no_close_leakage",
        )

        day_three = result.trades[result.trades["date"] == "2026-01-07"]
        self.assertEqual(day_three.iloc[0]["side"], "sell")
        self.assertEqual(day_three.iloc[0]["amount"], -500)

    def test_adjustment_factor_preserves_wealth_across_ex_date(self) -> None:
        dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"])
        columns = ["A"]
        raw = pd.DataFrame([[10.0], [10.0], [5.0], [5.0]], index=dates, columns=columns)
        prices = {
            "raw_open": raw.copy(),
            "raw_close": raw.copy(),
            "open": pd.DataFrame([[10.0], [10.0], [10.0], [10.0]], index=dates, columns=columns),
            "close": pd.DataFrame([[10.0], [10.0], [10.0], [10.0]], index=dates, columns=columns),
            "adj_factor": pd.DataFrame([[1.0], [1.0], [2.0], [2.0]], index=dates, columns=columns),
        }
        targets = {dates[0]: pd.Series([1.0], index=columns)}

        result = run_local_backtest(
            prices,
            targets,
            BacktestConfig(
                initial_cash=10_000.0,
                buy_cost=0.0,
                sell_cost=0.0,
                min_cost=0.0,
            ),
            strategy_name="corporate_action",
        )

        ex_date = result.raw_perf[result.raw_perf["date"] == "2026-01-07"].iloc[0]
        self.assertAlmostEqual(ex_date["portfolio_value"], 10_000.0)
        self.assertAlmostEqual(ex_date["corporate_action_adjustment"], 5_000.0)

    def test_close_marks_are_updated_every_day(self) -> None:
        dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
        columns = ["A"]
        prices = {
            "raw_open": pd.DataFrame([[10.0], [10.0], [11.0]], index=dates, columns=columns),
            "raw_close": pd.DataFrame([[10.0], [11.0], [12.0]], index=dates, columns=columns),
            "open": pd.DataFrame([[10.0], [10.0], [11.0]], index=dates, columns=columns),
            "close": pd.DataFrame([[10.0], [11.0], [12.0]], index=dates, columns=columns),
        }
        targets = {dates[0]: pd.Series([1.0], index=columns)}

        result = run_local_backtest(
            prices,
            targets,
            BacktestConfig(
                initial_cash=10_000.0,
                buy_cost=0.0,
                sell_cost=0.0,
                min_cost=0.0,
            ),
            strategy_name="daily_marks",
        )

        final = result.raw_perf.iloc[-1]
        self.assertAlmostEqual(final["portfolio_value"], 12_000.0)
        self.assertAlmostEqual(final["returns"], 12.0 / 11.0 - 1.0)


if __name__ == "__main__":
    unittest.main()
