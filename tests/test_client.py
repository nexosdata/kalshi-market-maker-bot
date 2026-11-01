"""Unit tests for NaxosData API Gateway client and synthetic market generator."""

import unittest

from kalshi_mm.client import NaxosKalshiClient, SyntheticKalshiOrderBookGenerator
from kalshi_mm.models import KalshiMarket, KalshiOrderBook


class TestClient(unittest.TestCase):
    """Test suite for client and synthetic generator."""

    def test_synthetic_generator_markets(self) -> None:
        """Synthetic generator produces valid markets list."""
        gen = SyntheticKalshiOrderBookGenerator(seed=42)
        markets = gen.get_markets()
        self.assertGreaterEqual(len(markets), 3)

        m = markets[0]
        self.assertIsInstance(m, KalshiMarket)
        self.assertEqual(m.tick_size, 0.01)
        self.assertEqual(m.min_price, 0.01)
        self.assertEqual(m.max_price, 0.99)

    def test_synthetic_generator_orderbook(self) -> None:
        """Synthetic generator creates well-formed L2 ladders."""
        gen = SyntheticKalshiOrderBookGenerator(seed=42)
        book = gen.simulate_next_tick("KXFED-24DEC-T5.00")

        self.assertIsInstance(book, KalshiOrderBook)
        self.assertEqual(book.ticker, "KXFED-24DEC-T5.00")
        self.assertTrue(len(book.bids) > 0)
        self.assertTrue(len(book.asks) > 0)
        self.assertIsNotNone(book.best_bid)
        self.assertIsNotNone(book.best_ask)
        self.assertLess(book.best_bid, book.best_ask)

        # All prices bounded in [0.01, 0.99]
        for b in book.bids:
            self.assertGreaterEqual(b.price, 0.01)
            self.assertLessEqual(b.price, 0.99)
        for a in book.asks:
            self.assertGreaterEqual(a.price, 0.01)
            self.assertLessEqual(a.price, 0.99)

    def test_naxos_client_offline_mode(self) -> None:
        """NaxosKalshiClient in offline mode smoothly serves synthetic data."""
        client = NaxosKalshiClient(offline_mode=True)
        self.assertTrue(client.check_health())

        markets = client.get_markets()
        self.assertGreaterEqual(len(markets), 1)

        m = client.get_market("KXFED-24DEC-T5.00")
        self.assertEqual(m.ticker, "KXFED-24DEC-T5.00")

        snapshot = client.get_orderbook_snapshot("KXFED-24DEC-T5.00")
        self.assertIsInstance(snapshot, KalshiOrderBook)
        self.assertLess(snapshot.best_bid, snapshot.best_ask)
        client.close()

    def test_naxos_client_unreachable_endpoint_fallback(self) -> None:
        """Client gracefully falls back to synthetic generator when gateway is unreachable."""
        client = NaxosKalshiClient(
            base_url="http://127.0.0.1:59999",  # Non-existent local port
            timeout=0.1,
            max_retries=1,
            offline_mode=False,
        )
        snapshot = client.get_orderbook_snapshot("KXFED-24DEC-T5.00")
        self.assertIsInstance(snapshot, KalshiOrderBook)
        self.assertEqual(snapshot.ticker, "KXFED-24DEC-T5.00")
        client.close()


if __name__ == "__main__":
    unittest.main()
