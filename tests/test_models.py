"""Unit tests for Kalshi market models, orderbook metrics, quotes, and position tracking."""

import unittest

from kalshi_mm.models import (
    KalshiMarket,
    KalshiOrderBook,
    KalshiPosition,
    KalshiQuote,
    MarketOrder,
    PriceLevel,
)


class TestModels(unittest.TestCase):
    """Test suite for core data models."""

    def test_price_level_serialization(self) -> None:
        """Test PriceLevel to_dict and from_dict."""
        pl = PriceLevel(price=0.54, size=120)
        d = pl.to_dict()
        self.assertEqual(d["price"], 0.54)
        self.assertEqual(d["size"], 120.0)
        reconstructed = PriceLevel.from_dict(d)
        self.assertEqual(reconstructed, pl)

    def test_kalshi_market_model(self) -> None:
        """Test KalshiMarket fields and dict serialization."""
        market = KalshiMarket(
            ticker="KXFED-24DEC-T5.00",
            title="Fed Funds Target Rate > 5.00%",
            volume_24h=50000.0,
            open_interest=25000.0,
        )
        d = market.to_dict()
        self.assertEqual(d["ticker"], "KXFED-24DEC-T5.00")
        self.assertEqual(d["tick_size"], 0.01)
        self.assertEqual(d["min_price"], 0.01)
        self.assertEqual(d["max_price"], 0.99)

        from_dict_m = KalshiMarket.from_dict(d)
        self.assertEqual(from_dict_m.ticker, market.ticker)
        self.assertEqual(from_dict_m.title, market.title)

    def test_kalshi_orderbook_metrics(self) -> None:
        """Test Level 2 orderbook properties: best bid/ask, mid, micro-price, and OBI."""
        bids = [
            PriceLevel(price=0.52, size=100),
            PriceLevel(price=0.51, size=200),
        ]
        asks = [
            PriceLevel(price=0.55, size=100),
            PriceLevel(price=0.56, size=300),
        ]
        book = KalshiOrderBook(ticker="KXFED-24DEC-T5.00", bids=bids, asks=asks)

        self.assertEqual(book.best_bid, 0.52)
        self.assertEqual(book.best_ask, 0.55)
        self.assertEqual(book.best_bid_size, 100.0)
        self.assertEqual(book.best_ask_size, 100.0)
        self.assertAlmostEqual(book.mid_price or 0.0, 0.535, places=3)
        self.assertAlmostEqual(book.spread or 0.0, 0.03, places=3)
        self.assertAlmostEqual(book.spread_cents or 0.0, 3.0, places=2)

        # Equal queue sizes -> micro price equals mid price
        self.assertAlmostEqual(book.micro_price, 0.535, places=3)

        # OBI top level (100 - 100) / 200 = 0.0
        self.assertAlmostEqual(book.order_book_imbalance(levels=1), 0.0, places=3)

    def test_orderbook_asymmetric_micro_price(self) -> None:
        """Test micro-price weighting when top of book is asymmetric."""
        # Bid has 300 contracts at 0.50, Ask has 100 contracts at 0.54
        # Micro-price = (0.50 * 100 + 0.54 * 300) / 400 = (50 + 162) / 400 = 212 / 400 = 0.53
        bids = [PriceLevel(price=0.50, size=300)]
        asks = [PriceLevel(price=0.54, size=100)]
        book = KalshiOrderBook(ticker="TEST", bids=bids, asks=asks)

        self.assertEqual(book.mid_price, 0.52)
        self.assertAlmostEqual(book.micro_price, 0.53, places=3)

        # OBI = (300 - 100) / 400 = +0.50 (heavy buying pressure)
        self.assertAlmostEqual(book.order_book_imbalance(levels=1), 0.50, places=3)

    def test_kalshi_quote_validation(self) -> None:
        """Test KalshiQuote validity logic."""
        # Valid quote
        q_valid = KalshiQuote(
            ticker="TEST",
            bid_price=0.52,
            ask_price=0.55,
            bid_size=10,
            ask_size=10,
            reservation_price=0.535,
            half_spread=0.015,
            inventory_skew=0.0,
        )
        self.assertTrue(q_valid.is_valid)
        self.assertAlmostEqual(q_valid.spread, 0.03, places=3)
        self.assertAlmostEqual(q_valid.spread_cents, 3.0, places=2)

        # Crossed quote (bid >= ask)
        q_crossed = KalshiQuote(
            ticker="TEST",
            bid_price=0.55,
            ask_price=0.52,
            bid_size=10,
            ask_size=10,
            reservation_price=0.535,
            half_spread=0.015,
            inventory_skew=0.0,
        )
        self.assertFalse(q_crossed.is_valid)

        # Out of bounds quote
        q_oob = KalshiQuote(
            ticker="TEST",
            bid_price=0.0,
            ask_price=0.52,
            bid_size=10,
            ask_size=10,
            reservation_price=0.50,
            half_spread=0.02,
            inventory_skew=0.0,
        )
        self.assertFalse(q_oob.is_valid)

    def test_kalshi_position_and_pnl_tracking(self) -> None:
        """Test inventory accumulation, average entry price, and PnL."""
        pos = KalshiPosition(ticker="TEST", cash=1000.0)

        # Fill 1: Buy 10 contracts at $0.50, fee = $0.00
        pos.update_fill(side="buy", price=0.50, size=10, fee=0.0)
        self.assertEqual(pos.net_quantity, 10)
        self.assertAlmostEqual(pos.avg_entry_price, 0.50, places=4)
        self.assertAlmostEqual(pos.cash, 995.0, places=2)

        # Fill 2: Buy 10 more at $0.60
        pos.update_fill(side="buy", price=0.60, size=10, fee=0.0)
        self.assertEqual(pos.net_quantity, 20)
        self.assertAlmostEqual(pos.avg_entry_price, 0.55, places=4)
        self.assertAlmostEqual(pos.cash, 989.0, places=2)

        # Mark-to-market at $0.58
        self.assertAlmostEqual(pos.unrealized_pnl(0.58), 20 * (0.58 - 0.55), places=2)  # +$0.60
        self.assertAlmostEqual(pos.realized_pnl, 0.0, places=2)

        # Fill 3: Sell 10 contracts at $0.65 (unwinding long)
        realized = pos.update_fill(side="sell", price=0.65, size=10, fee=0.0)
        # Unwound 10 contracts at ($0.65 - $0.55) = $1.00 realized PnL
        self.assertAlmostEqual(realized, 1.00, places=2)
        self.assertEqual(pos.net_quantity, 10)
        self.assertAlmostEqual(pos.realized_pnl, 1.00, places=2)

        # Fill 4: Sell remaining 10 contracts at $0.55
        pos.update_fill(side="sell", price=0.55, size=10, fee=0.0)
        self.assertEqual(pos.net_quantity, 0)
        self.assertAlmostEqual(pos.avg_entry_price, 0.0, places=4)
        self.assertAlmostEqual(pos.realized_pnl, 1.00, places=2)
        self.assertAlmostEqual(pos.total_pnl(0.55), 1.00, places=2)

    def test_kalshi_position_shorting(self) -> None:
        """Test short selling YES (holding negative inventory) and covering."""
        pos = KalshiPosition(ticker="TEST", cash=1000.0)

        # Short 20 contracts at $0.70
        pos.update_fill(side="sell", price=0.70, size=20, fee=0.0)
        self.assertEqual(pos.net_quantity, -20)
        self.assertAlmostEqual(pos.avg_entry_price, 0.70, places=4)

        # Mark-to-market at $0.60 -> profit of $0.10 * 20 = +$2.00
        self.assertAlmostEqual(pos.unrealized_pnl(0.60), 2.00, places=2)

        # Cover short by buying 20 contracts at $0.60
        pnl = pos.update_fill(side="buy", price=0.60, size=20, fee=0.0)
        self.assertAlmostEqual(pnl, 2.00, places=2)
        self.assertEqual(pos.net_quantity, 0)
        self.assertAlmostEqual(pos.realized_pnl, 2.00, places=2)

    def test_market_order_model(self) -> None:
        """Test MarketOrder remaining size and dict format."""
        order = MarketOrder(ticker="TEST", side="buy", price=0.50, size=50)
        self.assertEqual(order.remaining_size, 50)
        order.filled_size = 20
        self.assertEqual(order.remaining_size, 30)
        d = order.to_dict()
        self.assertEqual(d["side"], "buy")
        self.assertEqual(d["price"], 0.50)


if __name__ == "__main__":
    unittest.main()
