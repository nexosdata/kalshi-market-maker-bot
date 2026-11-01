"""Unit tests for matching engine simulator and queue position mechanics."""

import unittest

from kalshi_mm.simulator import MarketSimulator, QueueTracker
from kalshi_mm.strategy import KalshiAvellanedaStoikovMM, StrategyConfig


class TestSimulator(unittest.TestCase):
    """Test suite for matching engine simulation and execution tracking."""

    def test_single_step_execution(self) -> None:
        """Execute one simulation step and verify record integrity."""
        sim = MarketSimulator(ticker="KXFED-24DEC-T5.00", seed=123)
        rec = sim.step(step_num=1)

        self.assertEqual(rec.step, 1)
        self.assertGreater(rec.mid_price, 0.0)
        self.assertGreater(rec.bid_quote, 0.0)
        self.assertGreater(rec.ask_quote, rec.bid_quote)
        self.assertIsInstance(rec.inventory, int)
        self.assertIsInstance(rec.total_pnl, float)

    def test_multi_step_simulation_run(self) -> None:
        """Run 25-step simulation and inspect final result metrics."""
        strategy = KalshiAvellanedaStoikovMM(
            config=StrategyConfig(
                gamma=0.20,
                base_quote_size=10,
                max_position=50,
            )
        )
        sim = MarketSimulator(
            ticker="KXFED-24DEC-T5.00",
            strategy=strategy,
            initial_cash=5000.0,
            seed=42,
        )

        res = sim.run_simulation(steps=25)

        self.assertEqual(res.ticker, "KXFED-24DEC-T5.00")
        self.assertEqual(res.steps, 25)
        self.assertEqual(len(res.records), 25)
        self.assertGreaterEqual(res.total_trades, 0)
        self.assertGreaterEqual(res.total_volume, 0)
        self.assertIsInstance(res.sharpe_ratio, float)
        self.assertGreaterEqual(res.max_drawdown_usd, 0.0)

        # Dictionary format
        d = res.to_dict()
        self.assertEqual(d["steps"], 25)
        self.assertIn("sharpe_ratio", d)
        self.assertIn("max_drawdown_usd", d)

    def test_queue_tracker(self) -> None:
        """Test FIFO queue tracker mechanics."""
        qt = QueueTracker(price=0.50, side="buy", queue_ahead=100, order_size=20)
        self.assertEqual(qt.queue_ahead, 100)
        self.assertEqual(qt.order_size, 20)
        self.assertEqual(qt.filled_size, 0)


if __name__ == "__main__":
    unittest.main()
