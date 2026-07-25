import tempfile
import unittest
from pathlib import Path

import pandas as pd

from ashare_quant.strategies.financial_quality_alpha import (
    FinancialQualityAlphaConfig,
    ResearchStatus,
    validate_strict_pit_price_files,
)


class FinancialQualityAlphaTest(unittest.TestCase):
    def test_frozen_model_uses_adjacent_position_counts(self) -> None:
        config = FinancialQualityAlphaConfig()

        self.assertEqual(config.model_families, ("quality_value",))
        self.assertEqual(config.positions_per_model, (6, 8))
        self.assertEqual(config.model_weight, 0.50)
        self.assertIn("one stock per industry", config.industry_limit)

    def test_pit_audit_requires_full_revalidation(self) -> None:
        status = ResearchStatus()

        self.assertIn("research_reset_required", status.status)
        self.assertIn("diagnostic_only", status.historical_confirmation_period)
        self.assertFalse(status.production_eligible)
        self.assertEqual(status.forward_oos_start, "2026-07-20")

    def test_incomplete_price_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prices.csv"
            pd.DataFrame(columns=["date", "symbol", "raw_open", "raw_close"]).to_csv(
                path, index=False
            )

            with self.assertRaisesRegex(ValueError, "Strict PIT price fields missing"):
                validate_strict_pit_price_files([path])


if __name__ == "__main__":
    unittest.main()
