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
        info=lambda *_: None, warn=lambda *_: None, error=lambda *_: None
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

    def test_signal_day_is_first_trading_day_of_odd_month(self) -> None:
        first = context_at("2026-03-02 15:30", "2026-02-27")
        later = context_at("2026-03-03 15:30", "2026-03-02")
        even = context_at("2026-04-01 15:30", "2026-03-31")
        self.assertTrue(self.strategy._is_signal_day(first))
        self.assertFalse(self.strategy._is_signal_day(later))
        self.assertFalse(self.strategy._is_signal_day(even))

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
