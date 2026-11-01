"""Unit tests for Avellaneda-Stoikov market maker strategy on Kalshi binary markets."""

import unittest

from kalshi_mm.models import KalshiOrderBook, KalshiPosition, PriceLevel
from kalshi_mm.strategy import KalshiAvellanedaStoikovMM, StrategyConfig


class TestStrategy(unittest.TestCase):
    """Test suite for Avellaneda-Stoikov quoting engine."""

    def setUp(self) -> None:
        self.config = StrategyConfig(
            gamma=0.15,
            base_sigma=0.30,
            tau=1.0,
            kappa=1.5,
            base_quote_size=10,
            max_position=100,
            min_spread_cents=2.0,
            max_spread_cents=10.0,
        )
        self.strategy = KalshiAvellanedaStoikovMM(config=self.config)

    def test_reservation_price_neutral_inventory(self) -> None:
        """When q = 0, reservation price equals reference price."""
        ref_price = 0.50
        sigma = 0.30
        r = self.strategy.calculate_reservation_price(ref_price, inventory_q=0, sigma=sigma, tau=1.0)
        self.assertAlmostEqual(r, ref_price, places=4)

    def test_reservation_price_inventory_skew(self) -> None:
        """Long inventory (q > 0) lowers reservation price; Short inventory (q < 0) raises it."""
        ref_price = 0.50
        sigma = 0.30
        # Long 50 contracts
        r_long = self.strategy.calculate_reservation_price(ref_price, inventory_q=50, sigma=sigma, tau=1.0)
        self.assertLess(r_long, ref_price)

        # Short 50 contracts
        r_short = self.strategy.calculate_reservation_price(ref_price, inventory_q=-50, sigma=sigma, tau=1.0)
        self.assertGreater(r_short, ref_price)

    def test_reservation_price_bounds(self) -> None:
        """Reservation price must be clamped within [0.01, 0.99]."""
        # Huge long inventory would otherwise push r negative
        r_clamped_low = self.strategy.calculate_reservation_price(0.10, inventory_q=10000, sigma=0.50)
        self.assertGreaterEqual(r_clamped_low, 0.01)

        # Huge short inventory would otherwise push r > 1.0
        r_clamped_high = self.strategy.calculate_reservation_price(0.90, inventory_q=-10000, sigma=0.50)
        self.assertLessEqual(r_clamped_high, 0.99)

    def test_optimal_half_spread(self) -> None:
        """Optimal half-spread must respect min_spread_cents and max_spread_cents."""
        sigma = 0.20
        half_spread = self.strategy.calculate_optimal_half_spread(sigma, tau=1.0)
        # min_spread is 2.0 cents => min_half_spread is 0.01
        self.assertGreaterEqual(half_spread, 0.01)
        # max_spread is 10.0 cents => max_half_spread is 0.05
        self.assertLessEqual(half_spread, 0.05)

    def test_quantize_to_cent_tick(self) -> None:
        """Prices must be quantized strictly onto 1-cent ticks."""
        self.assertEqual(self.strategy.quantize_to_tick(0.534), 0.53)
        self.assertEqual(self.strategy.quantize_to_tick(0.536), 0.54)
        self.assertEqual(self.strategy.quantize_to_tick(0.535), 0.54)

    def test_quote_generation_validity(self) -> None:
        """Generated quotes must be valid, discrete, and satisfy bid < ask."""
        bids = [PriceLevel(price=0.50, size=50)]
        asks = [PriceLevel(price=0.54, size=50)]
        book = KalshiOrderBook(ticker="TEST", bids=bids, asks=asks)
        pos = KalshiPosition(ticker="TEST", net_quantity=0)

        quote = self.strategy.generate_quote(book, pos)
        self.assertTrue(quote.is_valid)
        self.assertLess(quote.bid_price, quote.ask_price)
        self.assertGreaterEqual(quote.bid_price, 0.01)
        self.assertLessEqual(quote.ask_price, 0.99)
        self.assertGreater(quote.bid_size, 0)
        self.assertGreater(quote.ask_size, 0)
        # Verify 1-cent tick alignment
        self.assertEqual(round(quote.bid_price, 2), quote.bid_price)
        self.assertEqual(round(quote.ask_price, 2), quote.ask_price)

    def test_position_limits_and_asymmetric_sizes(self) -> None:
        """When at max long position, bid_size must be 0; when max short, ask_size must be 0."""
        bids = [PriceLevel(price=0.50, size=50)]
        asks = [PriceLevel(price=0.54, size=50)]
        book = KalshiOrderBook(ticker="TEST", bids=bids, asks=asks)

        # Max long position (q = 100)
        pos_long = KalshiPosition(ticker="TEST", net_quantity=100)
        quote_long = self.strategy.generate_quote(book, pos_long)
        self.assertEqual(quote_long.bid_size, 0)
        self.assertGreater(quote_long.ask_size, 0)

        # Max short position (q = -100)
        pos_short = KalshiPosition(ticker="TEST", net_quantity=-100)
        quote_short = self.strategy.generate_quote(book, pos_short)
        self.assertGreater(quote_short.bid_size, 0)
        self.assertEqual(quote_short.ask_size, 0)

        # Moderate long (q = 40): bid_size < ask_size
        pos_mod = KalshiPosition(ticker="TEST", net_quantity=40)
        quote_mod = self.strategy.generate_quote(book, pos_mod)
        self.assertLess(quote_mod.bid_size, quote_mod.ask_size)

    def test_order_book_imbalance_drift(self) -> None:
        """Positive OBI (buy pressure) pushes drift-adjusted mid higher."""
        # Heavy bids -> positive OBI
        bids_heavy = [PriceLevel(price=0.50, size=500)]
        asks_light = [PriceLevel(price=0.54, size=50)]
        book_buy = KalshiOrderBook(ticker="TEST", bids=bids_heavy, asks=asks_light)

        # Heavy asks -> negative OBI
        bids_light = [PriceLevel(price=0.50, size=50)]
        asks_heavy = [PriceLevel(price=0.54, size=500)]
        book_sell = KalshiOrderBook(ticker="TEST", bids=bids_light, asks=asks_heavy)

        drift_buy = self.strategy.calculate_drift_adjusted_mid(book_buy)
        drift_sell = self.strategy.calculate_drift_adjusted_mid(book_sell)

        self.assertGreater(drift_buy, drift_sell)


if __name__ == "__main__":
    unittest.main()
