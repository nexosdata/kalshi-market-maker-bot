"""Matching engine simulator with FIFO queue positions, Poisson fills, and adverse selection.

Simulates market microstructure for Kalshi binary contracts:
- Level 2 queue position modeling (FIFO priority)
- Distance-dependent Poisson order arrival intensity: lambda(p) = A * exp(-k * delta)
- Adverse selection from informed order flow
- Continuous PnL, inventory variance, and CFTC fee tracking
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from kalshi_mm.cftc_fees import calculate_cftc_maker_fee
from kalshi_mm.client import SyntheticKalshiOrderBookGenerator
from kalshi_mm.models import KalshiOrderBook, KalshiPosition, KalshiQuote, MarketOrder
from kalshi_mm.strategy import KalshiAvellanedaStoikovMM, StrategyConfig


@dataclass
class QueueTracker:
    """Tracks queue priority ahead of our resting maker order at a price level."""

    price: float
    side: str  # 'buy' or 'sell'
    queue_ahead: int
    order_size: int
    filled_size: int = 0


@dataclass
class SimulationStepRecord:
    """Snapshot record of a single simulation iteration."""

    step: int
    timestamp: float
    mid_price: float
    micro_price: float
    bid_quote: float
    ask_quote: float
    bid_size: int
    ask_size: int
    inventory: int
    cash: float
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    bid_filled: bool
    ask_filled: bool
    bid_fill_size: int
    ask_fill_size: int


@dataclass
class SimulationResult:
    """Comprehensive performance and execution report of a market making simulation run."""

    ticker: str
    steps: int
    initial_cash: float
    final_cash: float
    final_inventory: int
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    total_volume: int
    total_trades: int
    bids_filled_count: int
    asks_filled_count: int
    total_fees_paid: float
    max_drawdown_usd: float
    sharpe_ratio: float
    records: List[SimulationStepRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "steps": self.steps,
            "initial_cash": round(self.initial_cash, 2),
            "final_cash": round(self.final_cash, 2),
            "final_inventory": self.final_inventory,
            "realized_pnl": round(self.realized_pnl, 4),
            "unrealized_pnl": round(self.unrealized_pnl, 4),
            "total_pnl": round(self.total_pnl, 4),
            "total_volume": self.total_volume,
            "total_trades": self.total_trades,
            "bids_filled_count": self.bids_filled_count,
            "asks_filled_count": self.asks_filled_count,
            "total_fees_paid": round(self.total_fees_paid, 4),
            "max_drawdown_usd": round(self.max_drawdown_usd, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
        }


class MarketSimulator:
    """Matching engine simulator for Kalshi market maker strategies."""

    def __init__(
        self,
        ticker: str = "KXFED-24DEC-T5.00",
        strategy: Optional[KalshiAvellanedaStoikovMM] = None,
        initial_cash: float = 10000.0,
        arrival_rate_base: float = 1.8,
        decay_k: float = 15.0,
        adverse_selection_prob: float = 0.25,
        maker_fee_rate: float = 0.0,
        seed: Optional[int] = None,
    ) -> None:
        self.ticker = ticker
        self.strategy = strategy or KalshiAvellanedaStoikovMM()
        self.position = KalshiPosition(ticker=ticker, cash=initial_cash)
        self.arrival_rate_base = arrival_rate_base
        self.decay_k = decay_k
        self.adverse_selection_prob = adverse_selection_prob
        self.maker_fee_rate = maker_fee_rate

        self._book_gen = SyntheticKalshiOrderBookGenerator(seed=seed)
        if seed is not None:
            random.seed(seed)

        self._bid_queue: Optional[QueueTracker] = None
        self._ask_queue: Optional[QueueTracker] = None
        self.records: List[SimulationStepRecord] = list()

    def _update_queues(self, quote: KalshiQuote, book: KalshiOrderBook) -> None:
        """Update or initialize FIFO queue position for active maker quotes."""
        # Bid side queue
        if quote.bid_size > 0:
            if self._bid_queue is None or self._bid_queue.price != quote.bid_price:
                # Calculate existing depth at or better than this price
                ahead_depth = 0
                for b in book.bids:
                    if b.price >= quote.bid_price:
                        ahead_depth += int(b.size)
                self._bid_queue = QueueTracker(
                    price=quote.bid_price,
                    side="buy",
                    queue_ahead=ahead_depth,
                    order_size=quote.bid_size,
                )
        else:
            self._bid_queue = None

        # Ask side queue
        if quote.ask_size > 0:
            if self._ask_queue is None or self._ask_queue.price != quote.ask_price:
                ahead_depth = 0
                for a in book.asks:
                    if a.price <= quote.ask_price:
                        ahead_depth += int(a.size)
                self._ask_queue = QueueTracker(
                    price=quote.ask_price,
                    side="sell",
                    queue_ahead=ahead_depth,
                    order_size=quote.ask_size,
                )
        else:
            self._ask_queue = None

    def _simulate_order_flow(
        self,
        quote: KalshiQuote,
        book: KalshiOrderBook,
    ) -> Tuple[int, int]:
        """Simulate Poisson taker arrival and adverse selection fills.

        Returns:
            Tuple of (bid_filled_contracts, ask_filled_contracts)
        """
        mid = book.mid_price or 0.50
        bid_fill = 0
        ask_fill = 0

        # Simulate market price shock / adverse selection
        adverse_shock = 0.0
        if random.random() < self.adverse_selection_prob:
            # Informed trade event: mid will shift
            adverse_shock = random.choice([-0.02, -0.01, 0.01, 0.02])

        effective_ref = mid + adverse_shock

        # Bid side fill logic
        if self._bid_queue and self._bid_queue.order_size > 0:
            dist = max(0.0, effective_ref - self._bid_queue.price)
            # Higher fill intensity if adverse shock moves price toward or below our bid
            intensity = self.arrival_rate_base * math.exp(-self.decay_k * dist)
            prob_arrival = 1.0 - math.exp(-intensity)

            if random.random() < prob_arrival:
                # Taker flow arrives
                incoming_taker_size = int(max(5, random.expovariate(1.0 / 40.0)))
                if self._bid_queue.queue_ahead > 0:
                    absorbed = min(self._bid_queue.queue_ahead, incoming_taker_size)
                    self._bid_queue.queue_ahead -= absorbed
                    incoming_taker_size -= absorbed

                if incoming_taker_size > 0:
                    fill = min(self._bid_queue.order_size - self._bid_queue.filled_size, incoming_taker_size)
                    self._bid_queue.filled_size += fill
                    bid_fill = fill
                    if self._bid_queue.filled_size >= self._bid_queue.order_size:
                        self._bid_queue = None

        # Ask side fill logic
        if self._ask_queue and self._ask_queue.order_size > 0:
            dist = max(0.0, self._ask_queue.price - effective_ref)
            intensity = self.arrival_rate_base * math.exp(-self.decay_k * dist)
            prob_arrival = 1.0 - math.exp(-intensity)

            if random.random() < prob_arrival:
                incoming_taker_size = int(max(5, random.expovariate(1.0 / 40.0)))
                if self._ask_queue.queue_ahead > 0:
                    absorbed = min(self._ask_queue.queue_ahead, incoming_taker_size)
                    self._ask_queue.queue_ahead -= absorbed
                    incoming_taker_size -= absorbed

                if incoming_taker_size > 0:
                    fill = min(self._ask_queue.order_size - self._ask_queue.filled_size, incoming_taker_size)
                    self._ask_queue.filled_size += fill
                    ask_fill = fill
                    if self._ask_queue.filled_size >= self._ask_queue.order_size:
                        self._ask_queue = None

        return bid_fill, ask_fill

    def step(self, step_num: int = 1) -> SimulationStepRecord:
        """Execute one simulation step."""
        book = self._book_gen.simulate_next_tick(self.ticker)
        mid = book.mid_price or 0.50
        micro = book.micro_price

        # Market Maker calculates optimal quote
        quote = self.strategy.generate_quote(book, self.position)
        self._update_queues(quote, book)

        # Simulate order flow and execution
        bid_fill, ask_fill = self._simulate_order_flow(quote, book)

        # Update position for fills
        if bid_fill > 0:
            fee = calculate_cftc_maker_fee(bid_fill, quote.bid_price, self.maker_fee_rate)
            self.position.update_fill("buy", quote.bid_price, bid_fill, fee)

        if ask_fill > 0:
            fee = calculate_cftc_maker_fee(ask_fill, quote.ask_price, self.maker_fee_rate)
            self.position.update_fill("sell", quote.ask_price, ask_fill, fee)

        rec = SimulationStepRecord(
            step=step_num,
            timestamp=time.time(),
            mid_price=mid,
            micro_price=micro,
            bid_quote=quote.bid_price,
            ask_quote=quote.ask_price,
            bid_size=quote.bid_size,
            ask_size=quote.ask_size,
            inventory=self.position.net_quantity,
            cash=self.position.cash,
            realized_pnl=self.position.realized_pnl,
            unrealized_pnl=self.position.unrealized_pnl(mid),
            total_pnl=self.position.total_pnl(mid),
            bid_filled=bid_fill > 0,
            ask_filled=ask_fill > 0,
            bid_fill_size=bid_fill,
            ask_fill_size=ask_fill,
        )
        self.records.append(rec)
        return rec

    def run_simulation(self, steps: int = 50) -> SimulationResult:
        """Run full multi-step simulation and compute summary metrics."""
        self.records.clear()
        initial_cash = self.position.cash
        bids_filled = 0
        asks_filled = 0

        pnl_series: List[float] = []
        high_watermark = 0.0
        max_drawdown = 0.0

        for s in range(1, steps + 1):
            rec = self.step(step_num=s)
            if rec.bid_filled:
                bids_filled += 1
            if rec.ask_filled:
                asks_filled += 1

            pnl = rec.total_pnl
            pnl_series.append(pnl)

            if pnl > high_watermark:
                high_watermark = pnl
            dd = high_watermark - pnl
            if dd > max_drawdown:
                max_drawdown = dd

        # Calculate Sharpe ratio on step PnL deltas
        deltas = [pnl_series[i] - pnl_series[i - 1] for i in range(1, len(pnl_series))]
        if deltas and len(deltas) > 1:
            mean_delta = sum(deltas) / len(deltas)
            var_delta = sum((d - mean_delta) ** 2 for d in deltas) / (len(deltas) - 1)
            std_delta = math.sqrt(var_delta) if var_delta > 0 else 1e-6
            sharpe = (mean_delta / std_delta) * math.sqrt(252 * 24 * 60) if std_delta > 0 else 0.0
        else:
            sharpe = 0.0

        last_rec = self.records[-1] if self.records else None
        final_inv = self.position.net_quantity
        mark_price = last_rec.mid_price if last_rec else 0.50

        return SimulationResult(
            ticker=self.ticker,
            steps=steps,
            initial_cash=initial_cash,
            final_cash=self.position.cash,
            final_inventory=final_inv,
            realized_pnl=self.position.realized_pnl,
            unrealized_pnl=self.position.unrealized_pnl(mark_price),
            total_pnl=self.position.total_pnl(mark_price),
            total_volume=self.position.total_volume,
            total_trades=self.position.trades_count,
            bids_filled_count=bids_filled,
            asks_filled_count=asks_filled,
            total_fees_paid=self.position.total_fees,
            max_drawdown_usd=max_drawdown,
            sharpe_ratio=sharpe,
            records=list(self.records),
        )
