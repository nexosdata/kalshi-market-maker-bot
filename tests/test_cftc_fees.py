"""Unit tests for CFTC Rule 40.2 quadratic fee schedules and maker fee models."""

import math
import unittest

from kalshi_mm.cftc_fees import (
    MakerFeeSchedule,
    calculate_cftc_maker_fee,
    calculate_cftc_taker_fee,
    calculate_effective_cost,
    calculate_net_spread_after_cftc_fees,
    calculate_taker_breakeven_spread,
    get_cftc_fee_curve,
)


class TestCFTCFees(unittest.TestCase):
    """Test suite for Kalshi CFTC Rule 40.2 fee computations."""

    def test_cftc_taker_fee_exact_formula(self) -> None:
        """Verify exact formula: ceil(0.07 * C * P * (1 - P) * 100) / 100."""
        # Test C = 100, P = 0.50
        # raw cents = 0.07 * 100 * 0.50 * 0.50 * 100 = 175.0 cents -> $1.75
        fee = calculate_cftc_taker_fee(contracts=100, price=0.50)
        self.assertAlmostEqual(fee, 1.75, places=4)

        # Test single contract C = 1, P = 0.50
        # raw cents = 0.07 * 1 * 0.25 * 100 = 1.75 cents -> ceil is 2 cents = $0.02
        fee_single = calculate_cftc_taker_fee(contracts=1, price=0.50)
        self.assertAlmostEqual(fee_single, 0.02, places=4)

        # Test C = 100, P = 0.10
        # raw cents = 0.07 * 100 * 0.10 * 0.90 * 100 = 63.0 cents -> $0.63
        fee_p10 = calculate_cftc_taker_fee(contracts=100, price=0.10)
        self.assertAlmostEqual(fee_p10, 0.63, places=4)

        # Test symmetry: P = 0.90 should equal P = 0.10
        fee_p90 = calculate_cftc_taker_fee(contracts=100, price=0.90)
        self.assertAlmostEqual(fee_p90, 0.63, places=4)

    def test_cftc_taker_fee_edge_cases(self) -> None:
        """Test zero and boundary edge cases."""
        self.assertEqual(calculate_cftc_taker_fee(contracts=0, price=0.50), 0.0)
        self.assertEqual(calculate_cftc_taker_fee(contracts=-10, price=0.50), 0.0)
        self.assertEqual(calculate_cftc_taker_fee(contracts=100, price=0.0), 0.0)
        self.assertEqual(calculate_cftc_taker_fee(contracts=100, price=1.0), 0.0)
        self.assertEqual(calculate_cftc_taker_fee(contracts=100, price=-0.1), 0.0)
        self.assertEqual(calculate_cftc_taker_fee(contracts=100, price=1.2), 0.0)

    def test_maker_fee_models(self) -> None:
        """Test standard zero maker fee and rebate tiers."""
        # Standard tier: 0% maker fee
        fee_standard = calculate_cftc_maker_fee(contracts=100, price=0.50, maker_fee_rate=0.0)
        self.assertEqual(fee_standard, 0.0)

        # Active MM tier rebate: -0.0010 ($0.10 cent rebate)
        rebate_schedule = MakerFeeSchedule.active_mm()
        fee_active = calculate_cftc_maker_fee(
            contracts=100, price=0.50, maker_fee_rate=rebate_schedule.maker_fee_per_contract
        )
        self.assertAlmostEqual(fee_active, -0.10, places=4)

        # Institutional rebate: -0.0025 ($0.25 cent rebate)
        dmm_schedule = MakerFeeSchedule.institutional()
        fee_dmm = calculate_cftc_maker_fee(
            contracts=100, price=0.50, maker_fee_rate=dmm_schedule.maker_fee_per_contract
        )
        self.assertAlmostEqual(fee_dmm, -0.25, places=4)

    def test_net_spread_calculation(self) -> None:
        """Test net captured spread calculation after fees."""
        # Maker captures full 2 cents on 100 contracts with 0% maker fee
        net_maker = calculate_net_spread_after_cftc_fees(
            bid_price=0.50, ask_price=0.52, contracts=100, is_maker=True
        )
        # Gross = (0.52 - 0.50) * 100 = $2.00. Fee = 0. Net = $2.00
        self.assertAlmostEqual(net_maker, 2.00, places=4)

        # Taker round-trip: pays quadratic taker fee on both legs
        net_taker = calculate_net_spread_after_cftc_fees(
            bid_price=0.50, ask_price=0.52, contracts=100, is_maker=False
        )
        # Taker fee at 0.50 is $1.75; at 0.52: 0.07 * 100 * 0.52 * 0.48 * 100 = 174.72 -> $1.75
        # Total fee approx $3.50. Net = 2.00 - 3.50 = -1.50
        self.assertLess(net_taker, 0.0)

        # Inverted or zero spread returns 0.0
        self.assertEqual(calculate_net_spread_after_cftc_fees(0.52, 0.50, 100), 0.0)
        self.assertEqual(calculate_net_spread_after_cftc_fees(0.50, 0.50, 100), 0.0)

    def test_effective_cost(self) -> None:
        """Test effective capital outlay."""
        # Maker: 100 contracts at $0.50 + $0 maker fee = $50.00
        cost_maker = calculate_effective_cost(100, 0.50, is_maker=True)
        self.assertAlmostEqual(cost_maker, 50.00, places=4)

        # Taker: 100 contracts at $0.50 + $1.75 taker fee = $51.75
        cost_taker = calculate_effective_cost(100, 0.50, is_maker=False)
        self.assertAlmostEqual(cost_taker, 51.75, places=4)

    def test_taker_breakeven_spread(self) -> None:
        """Test minimum round-trip spread required for takers."""
        breakeven = calculate_taker_breakeven_spread(p_mid=0.50, contracts=100)
        # Total fees = $1.75 * 2 = $3.50 for 100 contracts => 3.50 cents / contract = 0.035
        self.assertAlmostEqual(breakeven, 0.035, places=3)

    def test_fee_curve_generation(self) -> None:
        """Test fee curve generation across price spectrum."""
        curve = get_cftc_fee_curve(contracts=100, step_cents=10)
        self.assertGreater(len(curve), 5)
        # The maximum fee should occur at price = 0.50
        p50_row = next(r for r in curve if r["price"] == 0.50)
        self.assertAlmostEqual(float(p50_row["fee_usd"]), 1.75, places=3)

        for row in curve:
            self.assertGreater(float(row["fee_usd"]), 0.0)
            self.assertGreater(float(row["fee_per_contract_cents"]), 0.0)


if __name__ == "__main__":
    unittest.main()
