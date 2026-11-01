"""Rich ANSI terminal dashboard CLI for the Kalshi Market Maker Bot.

Displays live Level 2 orderbook feeds, Avellaneda-Stoikov reservation prices,
inventory skewing, CFTC Rule 40.2 fee metrics, and real-time realized PnL.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import List, Optional

from kalshi_mm.cftc_fees import (
    calculate_cftc_maker_fee,
    calculate_cftc_taker_fee,
    get_cftc_fee_curve,
)
from kalshi_mm.client import NaxosKalshiClient
from kalshi_mm.models import KalshiOrderBook, KalshiPosition, KalshiQuote
from kalshi_mm.simulator import MarketSimulator
from kalshi_mm.strategy import KalshiAvellanedaStoikovMM, StrategyConfig

# ANSI terminal formatting constants
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_GREEN = "\033[32m"
C_RED = "\033[31m"
C_YELLOW = "\033[33m"
C_BLUE = "\033[34m"
C_MAGENTA = "\033[35m"
C_CYAN = "\033[36m"
C_WHITE = "\033[37m"
C_BG_BLUE = "\033[44m"
C_CLEAR_SCREEN = "\033[2J\033[H"


def print_banner() -> None:
    """Print institutional header banner."""
    print(f"{C_BOLD}{C_CYAN}╔═══════════════════════════════════════════════════════════════════════════════════╗{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}║             NAXOSDATA KALSHI MARKET MAKER & CFTC LIQUIDITY ENGINE                 ║{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}║           Avellaneda-Stoikov Microstructure Engine | DCM Bounded Quotes           ║{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}╚═══════════════════════════════════════════════════════════════════════════════════╝{C_RESET}")


def render_cftc_fee_table() -> None:
    """Print CFTC Rule 40.2 quadratic fee curve table."""
    print_banner()
    print(f"\n{C_BOLD}{C_YELLOW}=== CFTC RULE 40.2 TAKER FEE SCHEDULE ==={C_RESET}")
    print(f"{C_DIM}Formula: Fee = ceil(0.07 * C * P * (1 - P) * 100) / 100{C_RESET}")
    print(f"{C_DIM}Evaluated for standard batch size of C = 100 contracts:{C_RESET}\n")

    header = (
        f"{'Price ($)':<10} | {'Price (¢)':<10} | {'P * (1 - P)':<12} | "
        f"{'Taker Fee ($)':<14} | {'Fee/Contract (¢)':<18} | {'Fee % Notional':<14}"
    )
    print(f"{C_BOLD}{header}{C_RESET}")
    print("-" * len(header))

    curve = get_cftc_fee_curve(contracts=100, step_cents=5)
    for row in curve:
        p = float(row["price"])
        p_cents = int(round(p * 100))
        variance = round(p * (1.0 - p), 4)
        fee_usd = float(row["fee_usd"])
        fee_cents = float(row["fee_per_contract_cents"])
        fee_pct = float(row["fee_pct_notional"])

        # Highlight maximum at 50 cents
        color = f"{C_BOLD}{C_YELLOW}" if p_cents == 50 else C_WHITE

        print(
            f"{color}${p:<9.2f} | {p_cents:<10} | {variance:<12.4f} | "
            f"${fee_usd:<13.2f} | {fee_cents:<18.3f} | {fee_pct:<13.2f}%{C_RESET}"
        )
    print("-" * len(header))
    print(f"{C_GREEN}* Note: Maker fee under Kalshi DCM standard tier is $0.00 (0% maker fee).{C_RESET}")
    print(f"{C_CYAN}* Market maker captured spread is exempt from quadratic taker fees.{C_RESET}\n")


def render_dashboard(
    step: int,
    total_steps: int,
    ticker: str,
    book: KalshiOrderBook,
    quote: KalshiQuote,
    position: KalshiPosition,
    ref_price: float,
    bid_filled: bool = False,
    ask_filled: bool = False,
) -> None:
    """Render full rich ANSI terminal market maker dashboard."""
    mid = book.mid_price or 0.50
    micro = book.micro_price
    obi = book.order_book_imbalance(levels=1)
    spread = book.spread_cents or 0.0

    print(C_CLEAR_SCREEN, end="")
    print_banner()

    # Step & Instrument Bar
    inv_color = C_GREEN if position.net_quantity > 0 else (C_RED if position.net_quantity < 0 else C_CYAN)
    pnl_total = position.total_pnl(mid)
    pnl_color = C_GREEN if pnl_total >= 0 else C_RED

    print(
        f"\n{C_BOLD}Contract:{C_RESET} {C_YELLOW}{ticker}{C_RESET} | "
        f"{C_BOLD}Step:{C_RESET} {step}/{total_steps} | "
        f"{C_BOLD}Inventory q:{C_RESET} {inv_color}{position.net_quantity:+d}{C_RESET} contracts | "
        f"{C_BOLD}Total PnL:{C_RESET} {pnl_color}${pnl_total:+.2f}{C_RESET}"
    )
    print("═" * 83)

    # Market Microstructure Stats
    print(f"{C_BOLD}{C_CYAN}[1] MARKET MICROSTRUCTURE FEED (NaxosData Gateway){C_RESET}")
    print(
        f"  Best Bid: {C_GREEN}${book.best_bid or 0.0:.2f}{C_RESET} ({int(book.best_bid_size)} lots)  |  "
        f"Best Ask: {C_RED}${book.best_ask or 0.0:.2f}{C_RESET} ({int(book.best_ask_size)} lots)  |  "
        f"Spread: {C_YELLOW}{spread:.1f}¢{C_RESET}"
    )
    print(
        f"  Mid Price: {C_WHITE}${mid:.4f}{C_RESET}  |  "
        f"Micro-Price: {C_CYAN}${micro:.4f}{C_RESET}  |  "
        f"OBI Imbalance: {C_YELLOW}{obi:+.3f}{C_RESET}"
    )

    # Strategy Skew & Reservation Price
    print(f"\n{C_BOLD}{C_CYAN}[2] AVELLANEDA-STOIKOV RESERVATION METRICS{C_RESET}")
    skew_direction = "BUY SKEW (Unload Short)" if quote.inventory_skew > 0 else ("SELL SKEW (Unload Long)" if quote.inventory_skew < 0 else "NEUTRAL")
    print(
        f"  Ref Fair Price: ${ref_price:.4f}  |  "
        f"Reservation r(s,q): {C_BOLD}{C_MAGENTA}${quote.reservation_price:.4f}{C_RESET}  |  "
        f"Half-Spread δ*: {quote.half_spread * 100:.2f}¢"
    )
    print(f"  Inventory Penalty: {inv_color}{quote.inventory_skew * 100:+.2f}¢{C_RESET}  |  Skew Mode: {C_YELLOW}{skew_direction}{C_RESET}")

    # Active MM Quotes
    print(f"\n{C_BOLD}{C_CYAN}[3] ACTIVE MM QUOTES (Discrete 1-Cent Cent Grid){C_RESET}")
    bid_fill_tag = f"{C_BG_BLUE} FILLED {C_RESET}" if bid_filled else ""
    ask_fill_tag = f"{C_BG_BLUE} FILLED {C_RESET}" if ask_filled else ""

    cftc_bid_fee = calculate_cftc_maker_fee(quote.bid_size, quote.bid_price)
    cftc_ask_fee = calculate_cftc_maker_fee(quote.ask_size, quote.ask_price)

    print(
        f"  >>> MAKER BID: {C_BOLD}{C_GREEN}${quote.bid_price:.2f}{C_RESET} x {quote.bid_size} contracts  "
        f"[Fee: ${cftc_bid_fee:.2f}] {bid_fill_tag}"
    )
    print(
        f"  >>> MAKER ASK: {C_BOLD}{C_RED}${quote.ask_price:.2f}{C_RESET} x {quote.ask_size} contracts  "
        f"[Fee: ${cftc_ask_fee:.2f}] {ask_fill_tag}"
    )
    print(f"  Quoted Spread: {C_BOLD}{C_YELLOW}{quote.spread_cents:.1f}¢{C_RESET} (${quote.spread:.2f})")

    # Orderbook Depth Ladder
    print(f"\n{C_BOLD}{C_CYAN}[4] LEVEL 2 ORDERBOOK LADDER{C_RESET}")
    print(f"  {'Side':<6} | {'Price ($)':<10} | {'Price (¢)':<10} | {'Depth (Lots)':<12} | {'Marker':<15}")
    print("  " + "-" * 58)

    # Top 3 asks ascending
    for a in reversed(book.asks[:3]):
        marker = f"{C_BOLD}{C_RED}<= OUR ASK{C_RESET}" if round(a.price, 2) == quote.ask_price else ""
        print(f"  {C_RED}{'ASK':<6}{C_RESET} | ${a.price:<9.2f} | {int(round(a.price * 100)):<10} | {int(a.size):<12} | {marker}")

    print(f"  {C_DIM}------ SPREAD: {spread:.1f}¢ (Micro-Price: ${micro:.3f}) ------{C_RESET}")

    # Top 3 bids descending
    for b in book.bids[:3]:
        marker = f"{C_BOLD}{C_GREEN}<= OUR BID{C_RESET}" if round(b.price, 2) == quote.bid_price else ""
        print(f"  {C_GREEN}{'BID':<6}{C_RESET} | ${b.price:<9.2f} | {int(round(b.price * 100)):<10} | {int(b.size):<12} | {marker}")

    # Position & Risk Account
    print(f"\n{C_BOLD}{C_CYAN}[5] POSITION & PnL ACCOUNT{C_RESET}")
    print(
        f"  Net Inventory: {inv_color}{position.net_quantity} contracts{C_RESET}  |  "
        f"Avg Entry: ${position.avg_entry_price:.4f}  |  "
        f"Trades Executed: {position.trades_count}"
    )
    print(
        f"  Cash Balance: ${position.cash:,.2f}  |  "
        f"Realized PnL: {pnl_color}${position.realized_pnl:+.2f}{C_RESET}  |  "
        f"Total Fees: ${position.total_fees:.2f}"
    )
    print("═" * 83)


def run_live_or_demo_loop(args: argparse.Namespace) -> int:
    """Run interactive market maker simulation or live client feed."""
    config = StrategyConfig(
        gamma=args.gamma,
        base_quote_size=args.quote_size,
        max_position=args.max_position,
        min_spread_cents=args.min_spread,
    )
    strategy = KalshiAvellanedaStoikovMM(config=config)

    if args.demo or args.mock or not args.api_key:
        print(f"{C_YELLOW}[INFO] Running in high-fidelity SIMULATION / DEMO mode.{C_RESET}")
        sim = MarketSimulator(
            ticker=args.ticker,
            strategy=strategy,
            initial_cash=args.initial_cash,
        )
        total_steps = args.sim_steps

        for s in range(1, total_steps + 1):
            rec = sim.step(step_num=s)
            book = sim._book_gen.simulate_next_tick(args.ticker)
            quote = strategy.generate_quote(book, sim.position)
            ref_price = strategy.calculate_drift_adjusted_mid(book)

            render_dashboard(
                step=s,
                total_steps=total_steps,
                ticker=args.ticker,
                book=book,
                quote=quote,
                position=sim.position,
                ref_price=ref_price,
                bid_filled=rec.bid_filled,
                ask_filled=rec.ask_filled,
            )

            if args.interval > 0 and s < total_steps:
                time.sleep(args.interval)

        # Print final simulation summary
        res = sim.run_simulation(steps=total_steps)
        print(f"\n{C_BOLD}{C_GREEN}=== SIMULATION COMPLETED SUCCESSFULLY ==={C_RESET}")
        print(f"Total Steps:        {res.steps}")
        print(f"Total Volume:       {res.total_volume} contracts")
        print(f"Trades Executed:    {res.total_trades} (Bids: {res.bids_filled_count}, Asks: {res.asks_filled_count})")
        print(f"Final Inventory:    {res.final_inventory} contracts")
        print(f"Realized PnL:       ${res.realized_pnl:+.2f}")
        print(f"Total Net PnL:      ${res.total_pnl:+.2f}")
        print(f"Max Drawdown:       ${res.max_drawdown_usd:.2f}")
        print(f"Sharpe Ratio:       {res.sharpe_ratio:.2f}\n")
        return 0

    # Live API mode
    print(f"{C_GREEN}[INFO] Connecting to NaxosData API Gateway at {args.base_url}...{C_RESET}")
    client = NaxosKalshiClient(
        api_key=args.api_key,
        base_url=args.base_url,
    )
    position = KalshiPosition(ticker=args.ticker, cash=args.initial_cash)

    try:
        for s in range(1, args.sim_steps + 1):
            book = client.get_orderbook_snapshot(args.ticker)
            quote = strategy.generate_quote(book, position)
            ref_price = strategy.calculate_drift_adjusted_mid(book)

            render_dashboard(
                step=s,
                total_steps=args.sim_steps,
                ticker=args.ticker,
                book=book,
                quote=quote,
                position=position,
                ref_price=ref_price,
            )
            time.sleep(max(0.5, args.interval))
    except KeyboardInterrupt:
        print(f"\n{C_YELLOW}[!] Market maker terminated by operator.{C_RESET}")
    finally:
        client.close()

    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="kalshi-mm",
        description="Institutional Avellaneda-Stoikov Market Maker & CFTC Fee Engine for Kalshi",
    )
    parser.add_argument(
        "--ticker",
        type=str,
        default="KXFED-24DEC-T5.00",
        help="Kalshi market instrument ticker (default: KXFED-24DEC-T5.00)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="NaxosData API key for production gateway access",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="https://api.naxosdata.com",
        help="NaxosData API Gateway base URL",
    )
    parser.add_argument(
        "--demo",
        "--mock",
        dest="demo",
        action="store_true",
        help="Run in offline synthetic demo simulation mode",
    )
    parser.add_argument(
        "--cftc-fee-check",
        action="store_true",
        help="Print CFTC Rule 40.2 quadratic fee curve and exit",
    )
    parser.add_argument(
        "--gamma",
        type=float,
        default=0.15,
        help="Avellaneda-Stoikov inventory risk aversion coefficient (default: 0.15)",
    )
    parser.add_argument(
        "--quote-size",
        type=int,
        default=10,
        help="Base order size in contracts (default: 10)",
    )
    parser.add_argument(
        "--max-position",
        type=int,
        default=100,
        help="Maximum inventory limit before quoting stops (default: 100)",
    )
    parser.add_argument(
        "--min-spread",
        type=float,
        default=2.0,
        help="Minimum bid-ask spread in cents (default: 2.0¢)",
    )
    parser.add_argument(
        "--initial-cash",
        type=float,
        default=10000.0,
        help="Initial simulated cash balance in USD (default: $10,000)",
    )
    parser.add_argument(
        "--sim-steps",
        type=int,
        default=25,
        help="Number of iterations / ticks to run (default: 25)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.08,
        help="Interval between steps in seconds (default: 0.08)",
    )
    return parser


def main() -> None:
    """CLI application entrypoint."""
    parser = build_parser()
    args = parser.parse_args()

    if args.cftc_fee_check:
        render_cftc_fee_table()
        sys.exit(0)

    exit_code = run_live_or_demo_loop(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
