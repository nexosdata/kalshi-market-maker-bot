"""Unit tests for CLI parsing, dashboard rendering, and demo runner."""

import io
import unittest
from contextlib import redirect_stdout

from kalshi_mm.cli import (
    build_parser,
    render_cftc_fee_table,
    render_dashboard,
    run_live_or_demo_loop,
)
from kalshi_mm.models import KalshiOrderBook, KalshiPosition, KalshiQuote, PriceLevel


class TestCLI(unittest.TestCase):
    """Test suite for CLI functions and interface."""

    def test_parser_defaults(self) -> None:
        """Verify default CLI arguments."""
        parser = build_parser()
        args = parser.parse_args([])
        self.assertEqual(args.ticker, "KXFED-24DEC-T5.00")
        self.assertAlmostEqual(args.gamma, 0.15)
        self.assertEqual(args.quote_size, 10)
        self.assertEqual(args.max_position, 100)
        self.assertEqual(args.sim_steps, 25)
        self.assertFalse(args.cftc_fee_check)

    def test_parser_custom_args(self) -> None:
        """Verify custom CLI flags."""
        parser = build_parser()
        args = parser.parse_args([
            "--ticker", "PRES-2024",
            "--gamma", "0.25",
            "--quote-size", "25",
            "--demo",
            "--cftc-fee-check",
        ])
        self.assertEqual(args.ticker, "PRES-2024")
        self.assertAlmostEqual(args.gamma, 0.25)
        self.assertEqual(args.quote_size, 25)
        self.assertTrue(args.demo)
        self.assertTrue(args.cftc_fee_check)

    def test_render_cftc_fee_table(self) -> None:
        """Fee table prints without errors."""
        f = io.StringIO()
        with redirect_stdout(f):
            render_cftc_fee_table()
        out = f.getvalue()
        self.assertIn("CFTC RULE 40.2 TAKER FEE SCHEDULE", out)
        self.assertIn("0.50", out)

    def test_render_dashboard(self) -> None:
        """Dashboard renders without exceptions."""
        bids = [PriceLevel(0.50, 50)]
        asks = [PriceLevel(0.54, 50)]
        book = KalshiOrderBook(ticker="TEST", bids=bids, asks=asks)
        quote = KalshiQuote("TEST", 0.50, 0.54, 10, 10, 0.52, 0.02, 0.0)
        pos = KalshiPosition(ticker="TEST")

        f = io.StringIO()
        with redirect_stdout(f):
            render_dashboard(
                step=1,
                total_steps=10,
                ticker="TEST",
                book=book,
                quote=quote,
                position=pos,
                ref_price=0.52,
            )
        out = f.getvalue()
        self.assertIn("NAXOSDATA KALSHI MARKET MAKER", out)
        self.assertIn("MAKER BID", out)
        self.assertIn("MAKER ASK", out)

    def test_run_live_or_demo_loop(self) -> None:
        """Test headless short demo simulation run."""
        parser = build_parser()
        args = parser.parse_args([
            "--demo",
            "--sim-steps", "3",
            "--interval", "0.0",
        ])
        f = io.StringIO()
        with redirect_stdout(f):
            code = run_live_or_demo_loop(args)
        self.assertEqual(code, 0)
        out = f.getvalue()
        self.assertIn("SIMULATION COMPLETED SUCCESSFULLY", out)


if __name__ == "__main__":
    unittest.main()
