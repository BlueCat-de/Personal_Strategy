from __future__ import annotations

import importlib.util
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STRATEGY_PATH = PROJECT_ROOT / "deploy/ptrade/main_board_bimonthly_ic.py"


def load_strategy():
    spec = importlib.util.spec_from_file_location("ptrade_main_board_bimonthly_ic", STRATEGY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.g = types.SimpleNamespace()
    module.log = types.SimpleNamespace(
        info=lambda *_: None, error=lambda *_: None
    )
    module.get_research_path = lambda: "/tmp/"
    return module


def context_at(date: str, previous_date: str, positions=None):
    current_dt = pd.Timestamp(date).to_pydatetime()
    previous = pd.Timestamp(previous_date).date()
    portfolio = types.SimpleNamespace(
        positions=positions or {},
        cash=100_000.0,
        portfolio_value=100_000.0,
    )
    return types.SimpleNamespace(
        blotter=types.SimpleNamespace(current_dt=current_dt),
        previous_date=previous,
        portfolio=portfolio,
    )


class PtradeAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = load_strategy()

    def test_main_board_filter(self) -> None:
        self.assertTrue(self.strategy._is_main_board("600000.SS"))
        self.assertTrue(self.strategy._is_main_board("002001.SZ"))
        self.assertFalse(self.strategy._is_main_board("300001.SZ"))
        self.assertFalse(self.strategy._is_main_board("688001.SS"))

    def test_research_file_path_does_not_require_os_module(self) -> None:
        self.assertEqual(
            self.strategy._research_file_path("signals.csv"),
            "/tmp/signals.csv",
        )

    def test_signal_day_is_first_trading_day_of_odd_month(self) -> None:
        first = context_at("2026-03-02 15:30", "2026-02-27")
        later = context_at("2026-03-03 15:30", "2026-03-02")
        even = context_at("2026-04-01 15:30", "2026-03-31")
        self.assertTrue(self.strategy._is_signal_day(first))
        self.assertFalse(self.strategy._is_signal_day(later))
        self.assertFalse(self.strategy._is_signal_day(even))

    def test_main_board_universe_uses_current_backtest_date(self) -> None:
        self.strategy.get_Ashares = lambda: [
            "600000.SS",
            "000001.SZ",
            "300001.SZ",
        ]

        stocks = self.strategy._main_board_universe()

        self.assertEqual(stocks, ["000001.SZ", "600000.SS"])

    def test_chunks_returns_materialized_batches(self) -> None:
        chunks = self.strategy._chunks(["A", "B", "C", "D", "E"], 2)

        self.assertIsInstance(chunks, list)
        self.assertEqual(chunks, [["A", "B"], ["C", "D"], ["E"]])

    def test_tushare_basic_rejects_wrong_trade_date(self) -> None:
        frame = pd.DataFrame(
            {
                "ts_code": ["600000.SH"],
                "trade_date": ["20240101"],
                "turnover_rate": [1.0],
                "pe_ttm": [5.0],
                "dv_ttm": [3.0],
                "total_mv": [100.0],
                "free_share": [10.0],
            }
        )

        with self.assertRaisesRegex(RuntimeError, "date mismatch"):
            self.strategy._normalize_tushare_basic(frame, "20240102", ["600000.SS"])

    def test_tushare_basics_build_exact_twenty_day_turnover_mean(self) -> None:
        trade_days = pd.date_range("2024-01-01", periods=20, freq="D")

        class FakeClient:
            def daily_basic(self, ts_code, trade_date, fields):
                position = [day.strftime("%Y%m%d") for day in trade_days].index(trade_date)
                return pd.DataFrame(
                    {
                        "ts_code": ["600000.SH"],
                        "trade_date": [trade_date],
                        "turnover_rate": [position + 1.0],
                        "pe_ttm": [5.0],
                        "dv_ttm": [3.0],
                        "total_mv": [100.0],
                        "free_share": [10.0],
                    }
                )

        context = context_at("2024-01-20 15:30", "2024-01-19")
        self.strategy.get_trade_days = lambda **_: trade_days
        with patch.object(self.strategy, "_tushare_client", return_value=FakeClient()):
            latest, turnover = self.strategy._fetch_tushare_basics(context, ["600000.SS"])

        self.assertEqual(float(latest.loc["600000.SS", "pe_ttm"]), 5.0)
        self.assertAlmostEqual(float(turnover.loc["600000.SS"]), 0.105)

    def test_liquidity_cap_uses_prior_history(self) -> None:
        self.strategy.g.pending_target = ["600000.SS"]
        self.strategy.g.liquidity_date = ""
        self.strategy.g.liquidity_caps = {}
        self.strategy.g.day_used_shares = {}
        context = context_at("2026-03-03 09:31", "2026-03-02")
        volume = pd.DataFrame({"600000.SS": [100_000.0] * 20})
        with patch.object(
            self.strategy, "_history_panels", return_value={"volume": volume}
        ) as call:
            self.strategy._refresh_liquidity_caps(context)
        self.assertEqual(self.strategy.g.liquidity_caps["600000.SS"], 1_000)
        self.assertFalse(call.call_args.args[4])

    def test_desired_shares_reserve_cash_and_keep_one_lot(self) -> None:
        self.strategy.g.desired_shares = {}
        self.strategy.g.pending_target = ["600000.SS"]
        context = context_at("2026-03-03 09:31", "2026-03-02")
        context.portfolio.portfolio_value = 5_000.0
        data = {"600000.SS": types.SimpleNamespace(price=80.0)}

        self.strategy._initialize_desired_shares(context, data)

        self.assertEqual(self.strategy.g.desired_shares, {"600000.SS": 100})

    def test_execution_times_are_separated(self) -> None:
        calls = []
        self.strategy.g.rebalance_active = True
        self.strategy.g.signal_date = "2026-03-02"
        with (
            patch.object(self.strategy, "_ensure_state"),
            patch.object(
                self.strategy, "_execute_sells", side_effect=lambda *_: calls.append("sell")
            ),
            patch.object(
                self.strategy, "_execute_buys", side_effect=lambda *_: calls.append("buy")
            ),
        ):
            self.strategy.handle_data(context_at("2026-03-03 09:31", "2026-03-02"), {})
            self.strategy.handle_data(context_at("2026-03-03 14:50", "2026-03-02"), {})
            self.strategy.handle_data(context_at("2026-03-03 14:51", "2026-03-02"), {})
        self.assertEqual(calls, ["sell", "buy"])

    def test_initial_signal_builds_on_a_non_signal_day(self) -> None:
        self.strategy.g.rebalance_active = False
        self.strategy.g.initial_signal_pending = True
        context = context_at("2026-07-23 15:30", "2026-07-22")
        with patch.object(self.strategy, "_build_signal") as build:
            self.strategy.after_trading_end(context, {})
        build.assert_called_once_with(context)

    def test_target_requires_no_extra_positions(self) -> None:
        self.strategy.g.desired_shares = {"600000.SS": 100}
        target = types.SimpleNamespace(amount=100)
        extra = types.SimpleNamespace(amount=100)
        context = context_at(
            "2026-03-03 15:30",
            "2026-03-02",
            positions={"600000.SS": target, "000001.SZ": extra},
        )
        self.assertFalse(self.strategy._target_reached(context))


if __name__ == "__main__":
    unittest.main()
