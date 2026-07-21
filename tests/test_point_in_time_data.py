import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from ashare_quant.data.financials import (
    FINANCIAL_COLUMNS,
    attach_financial_snapshots,
    listed_universe,
    load_financial_history,
    normalize_financial_rows,
)
from ashare_quant.data.pit import active_st_flags
from ashare_quant.data.tushare import fetch_namechange
from ashare_quant.research.factors import point_in_time_factor_snapshot, validate_basic_snapshot
from ashare_quant.research.stability import (
    industry_snapshot,
    load_industry_history,
    select_industry_neutral,
)


def financial_row(
    ann_date: str,
    end_date: str,
    roe: float,
    *,
    update_flag: str = "0",
) -> dict:
    row = {
        "ts_code": "600519.SH",
        "ann_date": ann_date,
        "end_date": end_date,
        "update_flag": update_flag,
    }
    row.update({column: 1.0 for column in FINANCIAL_COLUMNS})
    row["roe"] = roe
    return row


class FinancialPointInTimeTest(unittest.TestCase):
    def test_yyyymmdd_integer_dates_are_not_parsed_as_unix_nanoseconds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "600519_SH.csv"
            pd.DataFrame(
                [
                    financial_row("20231021", "20230930", 25.0),
                    financial_row("20240403", "20231231", 30.0),
                    financial_row("20240427", "20240331", 10.0),
                ]
            ).to_csv(path, index=False)

            history = load_financial_history(Path(directory))

        self.assertEqual(history["ann_date"].min(), pd.Timestamp("2023-10-21"))
        self.assertEqual(history["available_date"].min(), pd.Timestamp("2023-10-22"))

    def test_snapshot_uses_only_reports_available_before_signal_date(self) -> None:
        history = normalize_financial_rows(
            pd.DataFrame(
                [
                    financial_row("20231021", "20230930", 25.0),
                    financial_row("20240102", "20231231", 99.0),
                    financial_row("20240427", "20240331", 10.0),
                ]
            )
        )
        history["symbol"] = history["ts_code"].str[:6]
        history["available_date"] = history["ann_date"] + pd.Timedelta(days=1)
        panels = {
            pd.Timestamp("2024-01-02"): pd.DataFrame(index=["600519"]),
            pd.Timestamp("2024-05-06"): pd.DataFrame(index=["600519"]),
        }

        attach_financial_snapshots(panels, history)

        self.assertEqual(panels[pd.Timestamp("2024-01-02")].loc["600519", "roe"], 25.0)
        self.assertEqual(panels[pd.Timestamp("2024-05-06")].loc["600519", "roe"], 10.0)

    def test_original_filing_is_preferred_when_revision_time_is_unknown(self) -> None:
        rows = pd.DataFrame(
            [
                financial_row("20230331", "20221231", 10.0, update_flag="0"),
                financial_row("20230331", "20221231", 90.0, update_flag="1"),
            ]
        )

        normalized = normalize_financial_rows(rows)

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized.iloc[0]["roe"], 10.0)

    def test_delisted_stocks_are_included_when_they_overlap_the_period(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "universe.csv"
            pd.DataFrame(
                [
                    {
                        "ts_code": "600001.SH",
                        "symbol": "600001",
                        "list_date": "19980122",
                        "delist_date": "20091229",
                    },
                    {
                        "ts_code": "600002.SH",
                        "symbol": "600002",
                        "list_date": "20150101",
                        "delist_date": None,
                    },
                ]
            ).to_csv(path, index=False)

            symbols = listed_universe(path, "2007-01-01", "2010-12-31")

        self.assertEqual(symbols, ["600001.SH"])


class UniversePointInTimeTest(unittest.TestCase):
    @patch("ashare_quant.data.tushare.get_pro_client")
    def test_name_changes_are_paginated_and_future_rows_are_removed(
        self, get_pro_client: Mock
    ) -> None:
        client = Mock()
        client.namechange.side_effect = [
            pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "name": "平安银行",
                        "start_date": "20200101",
                        "end_date": None,
                        "change_reason": "其他",
                    }
                ]
            ),
            pd.DataFrame(
                [
                    {
                        "ts_code": "000002.SZ",
                        "name": "万科A",
                        "start_date": "20210101",
                        "end_date": None,
                        "change_reason": "其他",
                    },
                    {
                        "ts_code": "000003.SZ",
                        "name": "未来名称",
                        "start_date": "20220101",
                        "end_date": None,
                        "change_reason": "其他",
                    },
                ]
            ),
        ]
        get_pro_client.return_value = client

        changes = fetch_namechange("2020-01-01", "2021-12-31")

        self.assertEqual(client.namechange.call_count, 2)
        self.assertEqual(set(changes["ts_code"]), {"000001.SZ", "000002.SZ"})

    def test_current_st_name_is_not_backfilled_before_st_start(self) -> None:
        universe = pd.DataFrame([{"ts_code": "002055.SZ", "symbol": "002055", "name": "ST得润"}])
        changes = pd.DataFrame(
            [
                {
                    "ts_code": "002055.SZ",
                    "name": "ST得润",
                    "start_date": "2026-01-06",
                    "end_date": None,
                }
            ]
        )

        historical = active_st_flags(universe, changes, "2024-01-02")
        current = active_st_flags(universe, changes, "2026-01-06")

        self.assertEqual(historical.loc["002055.SZ"], 0)
        self.assertEqual(current.loc["002055.SZ"], 1)

    def test_future_industry_classification_is_unavailable_before_effective_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "industry.csv"
            pd.DataFrame(
                [
                    {
                        "symbol": "600519",
                        "l1_name": "食品饮料",
                        "in_date": "20010101",
                        "out_date": None,
                        "classification_version": "SW2021",
                        "classification_effective_date": "20210730",
                    }
                ]
            ).to_csv(path, index=False)
            history = load_industry_history(path)

        self.assertTrue(industry_snapshot(history, pd.Timestamp("2020-01-02")).empty)
        self.assertEqual(
            industry_snapshot(history, pd.Timestamp("2022-01-04")).loc["600519"],
            "食品饮料",
        )

    def test_unknown_industries_do_not_collapse_into_one_bucket(self) -> None:
        ranked = pd.Series([3.0, 2.0, 1.0], index=["A", "B", "C"])

        selected = select_industry_neutral(ranked, pd.Series(dtype=str), 3, 0.12)

        self.assertEqual(selected, ["A", "B", "C"])


class MarketSnapshotPointInTimeTest(unittest.TestCase):
    def test_daily_basic_cache_must_match_signal_date(self) -> None:
        frame = pd.DataFrame([{"ts_code": "600519.SH", "trade_date": "20240103", "total_mv": 1.0}])

        with self.assertRaisesRegex(ValueError, "date mismatch"):
            validate_basic_snapshot(frame, pd.Timestamp("2024-01-02"))

    def test_future_prices_do_not_change_signal_date_factors(self) -> None:
        dates = pd.bdate_range("2023-01-02", periods=125)
        symbols = [f"{number:06d}" for number in range(1, 32)]
        base = np.arange(len(dates), dtype=float)[:, None]
        offsets = np.arange(len(symbols), dtype=float)[None, :]
        close = pd.DataFrame(10.0 + base * 0.01 + offsets * 0.02, index=dates, columns=symbols)
        future_changed = close.copy()
        future_changed.iloc[121:] *= 50.0

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
                "pe_ttm": np.linspace(8.0, 20.0, len(symbols)),
                "pb": np.linspace(1.0, 3.0, len(symbols)),
                "ps_ttm": np.linspace(1.0, 4.0, len(symbols)),
                "dv_ttm": np.linspace(0.0, 2.0, len(symbols)),
                "total_mv": np.linspace(1e6, 5e6, len(symbols)),
                "circ_mv": np.linspace(8e5, 4e6, len(symbols)),
                "free_share": 1e6,
                "turnover_rate": 1.0,
                "volume_ratio": 1.0,
            }
        )

        original, _ = point_in_time_factor_snapshot(prices(close), 120, basic)
        changed, _ = point_in_time_factor_snapshot(prices(future_changed), 120, basic)

        pd.testing.assert_frame_equal(original, changed)


if __name__ == "__main__":
    unittest.main()
