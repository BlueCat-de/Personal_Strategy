import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from ashare_quant.backtest import BacktestConfig, ExecutionMode, run_local_backtest
from ashare_quant.strategies.main_board_bimonthly_ic import (
    FROZEN_WEIGHTS,
    MainBoardBimonthlyICConfig,
    ResearchStatus,
    bimonthly_rebalance_dates,
    composite_score,
    plot_return_curves,
    rebase_curve,
)


class MainBoardBimonthlyICTest(unittest.TestCase):
    def test_execution_modes_are_a_public_interface(self) -> None:
        self.assertEqual(ExecutionMode.coerce("same_open"), ExecutionMode.SAME_OPEN)
        self.assertEqual(
            ExecutionMode.coerce(ExecutionMode.SELL_OPEN_BUY_CLOSE),
            ExecutionMode.SELL_OPEN_BUY_CLOSE,
        )
        with self.assertRaisesRegex(ValueError, "unsupported execution mode"):
            ExecutionMode.coerce("future_open")

    def test_conservative_execution_delays_buys_to_next_open(self) -> None:
        dates = pd.bdate_range("2026-01-05", periods=5)
        columns = ["600000", "000001"]
        close = pd.DataFrame(
            [[10.0, 20.0], [10.0, 20.0], [10.0, 20.0], [10.0, 20.0], [10.0, 20.0]],
            index=dates,
            columns=columns,
        )
        prices = {
            "close": close,
            "raw_close": close,
            "open": close,
            "raw_open": close,
            "adj_factor": pd.DataFrame(1.0, index=dates, columns=columns),
            "up_limit": pd.DataFrame(np.nan, index=dates, columns=columns),
            "down_limit": pd.DataFrame(np.nan, index=dates, columns=columns),
            "is_suspended": pd.DataFrame(0, index=dates, columns=columns),
        }
        first_target = pd.Series([1.0, 0.0], index=columns)
        second_target = pd.Series([0.0, 1.0], index=columns)
        targets = {dates[0]: first_target, dates[2]: second_target}

        same_open = run_local_backtest(
            prices,
            targets,
            BacktestConfig(initial_cash=10_000.0, execution_mode="same_open"),
            strategy_name="same_open",
        )
        conservative = run_local_backtest(
            prices,
            targets,
            BacktestConfig(
                initial_cash=10_000.0,
                execution_mode="sell_open_buy_next_open",
            ),
            strategy_name="conservative",
        )

        same_buys = same_open.trades[same_open.trades.side == "buy"]
        conservative_trades = conservative.trades
        self.assertEqual(same_buys.date.iloc[0], dates[1].strftime("%Y-%m-%d"))
        self.assertEqual(
            conservative_trades[
                (conservative_trades.symbol == "600000") & (conservative_trades.side == "buy")
            ].date.iloc[0],
            dates[2].strftime("%Y-%m-%d"),
        )
        self.assertEqual(
            conservative_trades[
                (conservative_trades.symbol == "000001") & (conservative_trades.side == "buy")
            ].date.iloc[0],
            dates[4].strftime("%Y-%m-%d"),
        )
        self.assertEqual(
            conservative_trades[
                (conservative_trades.symbol == "600000") & (conservative_trades.side == "sell")
            ].date.iloc[0],
            dates[3].strftime("%Y-%m-%d"),
        )

    def test_sell_open_buy_close_uses_same_day_close_for_new_position(self) -> None:
        dates = pd.bdate_range("2026-01-05", periods=4)
        columns = ["600000", "000001"]
        open_prices = pd.DataFrame(
            [[10.0, 20.0], [10.0, 20.0], [10.0, 20.0], [10.0, 20.0]],
            index=dates,
            columns=columns,
        )
        close_prices = pd.DataFrame(
            [[10.0, 20.0], [11.0, 20.0], [10.0, 20.0], [10.0, 22.0]],
            index=dates,
            columns=columns,
        )
        prices = {
            "open": open_prices,
            "raw_open": open_prices,
            "close": close_prices,
            "raw_close": close_prices,
            "adj_factor": pd.DataFrame(1.0, index=dates, columns=columns),
            "up_limit": pd.DataFrame(np.nan, index=dates, columns=columns),
            "down_limit": pd.DataFrame(np.nan, index=dates, columns=columns),
            "is_suspended": pd.DataFrame(0, index=dates, columns=columns),
        }
        targets = {
            dates[0]: pd.Series([1.0, 0.0], index=columns),
            dates[2]: pd.Series([0.0, 1.0], index=columns),
        }

        result = run_local_backtest(
            prices,
            targets,
            BacktestConfig(
                initial_cash=10_000.0,
                buy_cost=0.0,
                sell_cost=0.0,
                min_cost=0.0,
                execution_mode="sell_open_buy_close",
            ),
            strategy_name="sell_open_buy_close",
        )
        trades = result.trades
        buy_new = trades[(trades.symbol == "000001") & (trades.side == "buy")].iloc[0]
        sell_old = trades[(trades.symbol == "600000") & (trades.side == "sell")].iloc[0]

        self.assertEqual(buy_new.date, dates[3].strftime("%Y-%m-%d"))
        self.assertEqual(sell_old.date, dates[3].strftime("%Y-%m-%d"))
        self.assertAlmostEqual(buy_new.reference_price, 22.0)

    def test_frozen_config_is_main_board_bimonthly_and_eight_positions(self) -> None:
        config = MainBoardBimonthlyICConfig()

        self.assertEqual(config.board_scope, "main")
        self.assertEqual(config.rebalance, "bimonthly_first_month")
        self.assertEqual(config.positions, 8)
        self.assertEqual(config.rebalance_offset, 0)
        self.assertAlmostEqual(sum(config.weights().values()), 1.0)
        self.assertEqual(config.weights(), FROZEN_WEIGHTS)

    def test_research_status_blocks_production_for_offset_sensitivity(self) -> None:
        status = ResearchStatus()

        self.assertIn("offset_sensitive", status.status)
        self.assertFalse(status.production_eligible)
        self.assertTrue(any("alternate bimonthly start" in reason for reason in status.reasons))

    def test_bimonthly_dates_support_two_fixed_starts(self) -> None:
        dates = pd.bdate_range("2024-01-01", "2024-06-28")

        first_start = bimonthly_rebalance_dates(dates, rebalance_offset=0)
        second_start = bimonthly_rebalance_dates(dates, rebalance_offset=1)

        self.assertEqual([date.month for date in first_start], [1, 3, 5])
        self.assertEqual([date.month for date in second_start], [2, 4, 6])
        with self.assertRaisesRegex(ValueError, "0 or 1"):
            bimonthly_rebalance_dates(dates, rebalance_offset=2)

    def test_composite_score_requires_all_frozen_factors(self) -> None:
        symbols = pd.Index([f"600{number:03d}" for number in range(60)])
        panel = pd.DataFrame(index=symbols)
        for index, factor in enumerate(FROZEN_WEIGHTS):
            panel[factor] = np.arange(60, dtype=float) * (index + 1)
        panel["total_mv"] = np.geomspace(1e8, 1e12, len(symbols))
        panel["industry"] = ["银行"] * 30 + ["家电"] * 30

        actual = composite_score(panel, MainBoardBimonthlyICConfig())

        self.assertEqual(actual.notna().sum(), len(symbols))
        self.assertTrue(np.isfinite(actual).all())

    def test_rebase_curve_and_plot_cover_requested_start_dates(self) -> None:
        dates = pd.bdate_range("2007-01-04", "2026-07-17")
        values = pd.Series(np.linspace(100_000.0, 300_000.0, len(dates)), index=dates)
        rebased = rebase_curve(values, "2021-01-01")
        raw_perf = pd.DataFrame({"date": dates, "portfolio_value": values})
        benchmark = pd.DataFrame({"date": dates, "benchmark_close": values * 0.8})

        self.assertAlmostEqual(rebased.iloc[0], 0.0)
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            plot_return_curves(raw_perf, benchmark, output_dir)

            self.assertTrue((output_dir / "return_curves_2007_2021_2026.png").exists())
            self.assertTrue((output_dir / "return_curves_2007_2021_2026.pdf").exists())


if __name__ == "__main__":
    unittest.main()
