import unittest

from ashare_quant.strategies.financial_quality_alpha import (
    FinancialQualityAlphaConfig,
    ResearchStatus,
)


class FinancialQualityAlphaTest(unittest.TestCase):
    def test_frozen_model_uses_adjacent_position_counts(self) -> None:
        config = FinancialQualityAlphaConfig()

        self.assertEqual(config.model_families, ("quality_value",))
        self.assertEqual(config.positions_per_model, (6, 8))
        self.assertEqual(config.model_weight, 0.50)
        self.assertIn("one stock per industry", config.industry_limit)

    def test_historical_confirmation_does_not_grant_production_status(self) -> None:
        status = ResearchStatus()

        self.assertIn("forward_oos_pending", status.status)
        self.assertFalse(status.production_eligible)
        self.assertEqual(status.forward_oos_start, "2026-07-20")


if __name__ == "__main__":
    unittest.main()
