"""Tests for the auxiliary/risk-overlay admission protocol (public infrastructure).

Guards the protocol's discriminative power: the 6-case self-test must keep discriminating
(VT/DD/combined ADMIT; beta-cap-no-op / permuted-VT / random REJECT) after any threshold
edit. If this regresses, the admission protocol is broken.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from ashare_quant.evaluation.auxiliary_factor_protocol import (
    apply_beta_cap,
    apply_dd_ladder,
    apply_vol_target,
    evaluate_candidate,
    self_test,
)

V2_RAW_PERF = Path("data/backtests/composite_alpha_v2_test/local_raw_perf.csv")


def _baseline() -> pd.Series:
    """v2 daily returns restricted to trainval (excludes the test lockbox)."""
    from ashare_quant.splits import TEST_START
    raw = pd.read_csv(V2_RAW_PERF)
    raw["date"] = pd.to_datetime(raw["date"])
    r = raw.set_index("date")["returns"]
    return r[r.index < pd.Timestamp(TEST_START)]


@unittest.skipUnless(V2_RAW_PERF.exists(), "v2 raw_perf fixture not present")
class AuxiliaryProtocolTest(unittest.TestCase):
    def test_self_test_discriminates(self) -> None:
        self.assertTrue(self_test(print_table=False), "6-case self-test must discriminate")

    def test_vol_target_admits(self) -> None:
        base = _baseline()
        res = evaluate_candidate(base, apply_vol_target(base, 0.15), "vol", "vt")
        self.assertEqual(res["verdict"], "ADMIT")
        self.assertTrue(res["sharpe_rail_pass"])
        self.assertTrue(res["collateral_rail_pass"])

    def test_random_scaler_rejected_on_wf(self) -> None:
        from ashare_quant.evaluation.auxiliary_factor_protocol import _random_scaler
        base = _baseline()
        res = evaluate_candidate(base, _random_scaler(base), "combined", "random")
        self.assertEqual(res["verdict"], "REJECT")
        self.assertLess(res["wf_consistency"], 0.75)  # the WF gate is what kills it

    def test_permuted_vt_rejected_on_sharpe_rail(self) -> None:
        from ashare_quant.evaluation.auxiliary_factor_protocol import _permuted_vt
        base = _baseline()
        res = evaluate_candidate(base, _permuted_vt(base, 0.15), "vol", "permvt")
        self.assertEqual(res["verdict"], "REJECT")
        self.assertFalse(res["sharpe_rail_pass"])  # destroys Sharpe despite reducing vol

    def test_beta_cap_rejected_as_noop(self) -> None:
        """v2's beta (~0.87) is already below the 1.3 cap, so beta-cap never binds."""
        from ashare_quant.benchmark import fetch_benchmark
        base = _baseline()
        bm_df = fetch_benchmark(str(base.index[0].date()), str(base.index[-1].date()))
        bm_df = bm_df.copy(); bm_df["date"] = pd.to_datetime(bm_df["date"])
        bm = bm_df.set_index("date")["benchmark_close"].pct_change().reindex(base.index).fillna(0.0)
        res = evaluate_candidate(
            base, apply_beta_cap(base, bm, 1.3), "beta", "betacap", benchmark_returns=bm
        )
        self.assertEqual(res["verdict"], "REJECT")
        self.assertLess(abs(res["delta_beta"]), 0.01)  # near-zero effect (no-op)


if __name__ == "__main__":
    unittest.main()
