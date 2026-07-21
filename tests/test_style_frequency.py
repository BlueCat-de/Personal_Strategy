import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from ashare_quant.data.financials import FINANCIAL_COLUMNS
from ashare_quant.research.style_frequency import (
    FinancialSnapshotStore,
    MonthlyBasicStore,
    cadence_dates,
    factor_snapshot,
    select_industry_neutral,
)


class StyleFrequencyTest(unittest.TestCase):
    def test_financial_store_enforces_announcement_plus_one_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rows = []
            for ann_date, end_date, roe in [
                ("20240110", "20230930", 10.0),
                ("20240430", "20231231", 20.0),
            ]:
                row = {
                    "ts_code": "600519.SH",
                    "ann_date": ann_date,
                    "end_date": end_date,
                    "update_flag": "0",
                    **{column: 1.0 for column in FINANCIAL_COLUMNS},
                }
                row["roe"] = roe
                rows.append(row)
            pd.DataFrame(rows).to_csv(Path(directory) / "600519_SH.csv", index=False)
            store = FinancialSnapshotStore(Path(directory))

            self.assertTrue(store.latest(pd.Timestamp("2024-01-10")).empty)
            self.assertEqual(store.latest(pd.Timestamp("2024-01-11")).loc["600519", "roe"], 10.0)
            self.assertEqual(store.latest(pd.Timestamp("2024-04-30")).loc["600519", "roe"], 10.0)
            self.assertEqual(store.latest(pd.Timestamp("2024-05-01")).loc["600519", "roe"], 20.0)

    def test_monthly_basic_store_rejects_mislabeled_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pd.DataFrame([{"ts_code": "600519.SH", "trade_date": "20240103"}]).to_csv(
                Path(directory) / "2024-01-02.csv", index=False
            )
            store = MonthlyBasicStore(Path(directory))

            with self.assertRaisesRegex(ValueError, "date mismatch"):
                store.latest(pd.Timestamp("2024-01-02"))

    def test_style_factors_do_not_read_future_prices(self) -> None:
        dates = pd.bdate_range("2023-01-02", periods=125)
        symbols = [f"{number:06d}" for number in range(1, 12)]
        base = np.arange(len(dates), dtype=float)[:, None]
        offsets = np.arange(len(symbols), dtype=float)[None, :]
        close = pd.DataFrame(10.0 + base * 0.01 + offsets * 0.02, index=dates, columns=symbols)
        changed = close.copy()
        changed.iloc[121:] *= 100.0

        def prices(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
            ones = pd.DataFrame(1.0, index=dates, columns=symbols)
            return {
                "close": frame,
                "raw_close": frame,
                "raw_open": frame,
                "volume": ones * 1_000_000.0,
                "amount": ones * 100_000_000.0,
                "turnover": ones * 0.01,
                "is_suspended": ones * 0.0,
                "is_st": ones * 0.0,
                "is_listed": ones,
            }

        basic = pd.DataFrame(
            {
                "symbol": symbols,
                "pe_ttm": 10.0,
                "pb": 2.0,
                "ps_ttm": 3.0,
                "dv_ttm": 1.0,
                "total_mv": np.arange(len(symbols)) + 1e6,
                "circ_mv": np.arange(len(symbols)) + 8e5,
            }
        )
        financials = pd.DataFrame(
            {column: 1.0 for column in FINANCIAL_COLUMNS},
            index=symbols,
        )

        original = factor_snapshot(
            prices(close), close.pct_change(fill_method=None), 120, basic, financials
        )
        modified = factor_snapshot(
            prices(changed), changed.pct_change(fill_method=None), 120, basic, financials
        )

        pd.testing.assert_frame_equal(original, modified)

    def test_cadence_dates_separate_weekly_and_twice_weekly(self) -> None:
        dates = pd.bdate_range("2026-01-05", "2026-02-27")

        schedules = cadence_dates(dates)

        self.assertEqual(len(schedules["weekly"]), 8)
        self.assertEqual(len(schedules["twice_weekly"]), 16)
        self.assertEqual(len(schedules["biweekly"]), 4)
        self.assertEqual(len(schedules["monthly"]), 2)
        self.assertEqual(len(schedules["bimonthly"]), 1)

    def test_unknown_industries_do_not_share_one_bucket(self) -> None:
        ranked = pd.Series([3.0, 2.0, 1.0], index=["000001", "000002", "000003"])
        industries = pd.Series({"000001": pd.NA, "000002": pd.NA, "000003": "银行"})

        selected = select_industry_neutral(ranked, industries, positions=3, maximum_fraction=0.2)

        self.assertEqual(selected, ["000001", "000002", "000003"])


if __name__ == "__main__":
    unittest.main()
