"""Institutional Avellaneda-Stoikov Market Making Strategy adapted for Kalshi prediction markets.

Features:
- Micro-price adjustment with Order Book Imbalance (OBI) drift
- Quadratic Bernoulli price variance: sigma^2 * 4 * p * (1 - p)
- Bounded reservation price: r = s - q * gamma * sigma^2 * tau
- Closed-form optimal bid/ask spread
- Discrete cent tick grid quantization ($0.01 tick size, [0.01, 0.99] bounds)
- Asymmetric inventory-skewed quoting sizes with hard/soft position limits
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Tuple

from kalshi_mm.models import KalshiOrderBook, KalshiPosition, KalshiQuote


@dataclass
class StrategyConfig:
    """Hyperparameters for Avellaneda-Stoikov market maker."""

    gamma: float = 0.15  # Risk aversion parameter (higher = faster inventory mean reversion)
    base_sigma: float = 0.30  # Baseline annual / session volatility
    tau: float = 1.0  # Normalized time horizon remaining (e.g. 1.0 down to 0.0)
    kappa: float = 1.5  # Order book liquidity density / fill intensity
    base_quote_size: int = 10  # Standard quote lot size (contracts)
    max_position: int = 100  # Hard inventory limit (|q| <= max_position)
    min_spread_cents: float = 2.0  # Minimum bid/ask spread in cents
    max_spread_cents: float = 10.0  # Maximum quote spread in cents
    tick_size: float = 0.01  # Kalshi 1-cent tick grid
    min_price: float = 0.01  # Lower bound ($0.01)
    max_price: float = 0.99  # Upper bound ($0.99)
    obi_drift_weight: float = 0.02  # Drift factor per unit OBI [-1, 1] in dollars
    micro_price_weight: float = 0.50  # Blend ratio between volume-weighted micro-price and mid


class KalshiAvellanedaStoikovMM:
    """Avellaneda-Stoikov Market Maker engine tailored for Kalshi DCM binary markets."""

    def __init__(self, config: Optional[StrategyConfig] = None) -> None:
        self.config = config or StrategyConfig()

    def calculate_drift_adjusted_mid(self, orderbook: KalshiOrderBook) -> float:
        """Calculate microstructural reference price combining mid, micro-price, and OBI drift."""
        bb = orderbook.best_bid
        ba = orderbook.best_ask

        if bb is None or ba is None:
            return orderbook.mid_price or 0.50

        raw_mid = (bb + ba) / 2.0
        micro = orderbook.micro_price

        # Blend mid and micro price
        w = self.config.micro_price_weight
        blended_ref = (1.0 - w) * raw_mid + w * micro

        # Add Order Book Imbalance (OBI) drift
        obi = orderbook.order_book_imbalance(levels=1)
        drift = obi * self.config.obi_drift_weight
        drift_adjusted = blended_ref + drift

        # Clamp within valid bounds
        return max(self.config.min_price, min(self.config.max_price, drift_adjusted))

    def calculate_binary_volatility(self, p: float) -> float:
        """Calculate state-dependent Bernoulli volatility for binary contract with price P.

        For a binary option, Var(P) = p * (1 - p).
        We scale base_sigma by sqrt(4 * p * (1 - p)), which peaks at p = 0.50.
        """
        clamped_p = max(0.01, min(0.99, p))
        bernoulli_scale = math.sqrt(4.0 * clamped_p * (1.0 - clamped_p))
        return self.config.base_sigma * bernoulli_scale

    def calculate_reservation_price(
        self,
        ref_price: float,
        inventory_q: int,
        sigma: float,
        tau: Optional[float] = None,
    ) -> float:
        """Calculate Avellaneda-Stoikov reservation (indifference) price.

        Formula:
            r(s, q, tau) = s - q * gamma * sigma^2 * tau

        When long (q > 0), reservation price shifts down (willing to sell cheaper).
        When short (q < 0), reservation price shifts up (willing to buy higher).
        """
        t = tau if tau is not None else self.config.tau
        variance = sigma ** 2
        inventory_penalty = inventory_q * self.config.gamma * variance * t

        r = ref_price - inventory_penalty
        return max(self.config.min_price, min(self.config.max_price, r))

    def calculate_optimal_half_spread(self, sigma: float, tau: Optional[float] = None) -> float:
        """Calculate Avellaneda-Stoikov closed form half-spread delta*.

        Formula:
            delta*(s) = (gamma * sigma^2 * tau / 2) + (1 / gamma) * ln(1 + gamma / kappa)
        """
        t = tau if tau is not None else self.config.tau
        gamma = self.config.gamma
        kappa = self.config.kappa
        variance = sigma ** 2

        term1 = (gamma * variance * t) / 2.0
        term2 = (1.0 / gamma) * math.log(1.0 + (gamma / kappa))
        raw_half_spread = term1 + term2

        # Convert min and max spread constraints from cents to dollars
        min_half_spread = (self.config.min_spread_cents / 100.0) / 2.0
        max_half_spread = (self.config.max_spread_cents / 100.0) / 2.0

        clamped_half_spread = max(min_half_spread, min(max_half_spread, raw_half_spread))
        return clamped_half_spread

    def quantize_to_tick(self, price: float) -> float:
        """Snap continuous price to the discrete Kalshi 1-cent tick grid."""
        tick = self.config.tick_size
        snapped = round(round(price / tick) * tick, 4)
        return snapped

    def compute_quote_sizes(self, inventory_q: int) -> Tuple[int, int]:
        """Compute asymmetric quote sizes based on current inventory position."""
        q_max = self.config.max_position
        base_size = self.config.base_quote_size

        if inventory_q >= q_max:
            # Reached positive position limit: do NOT buy more
            bid_size = 0
            ask_size = int(round(base_size * 1.5))
        elif inventory_q <= -q_max:
            # Reached negative position limit: do NOT sell more
            bid_size = int(round(base_size * 1.5))
            ask_size = 0
        else:
            # Linear skewing: if long, smaller bid size and larger ask size
            bid_multiplier = max(0.1, 1.0 - (inventory_q / q_max))
            ask_multiplier = max(0.1, 1.0 + (inventory_q / q_max))

            bid_size = max(1, int(round(base_size * bid_multiplier)))
            ask_size = max(1, int(round(base_size * ask_multiplier)))

        return bid_size, ask_size

    def generate_quote(
        self,
        orderbook: KalshiOrderBook,
        position: KalshiPosition,
        tau: Optional[float] = None,
    ) -> KalshiQuote:
        """Generate optimal two-sided Kalshi quote for the current market state."""
        ref_price = self.calculate_drift_adjusted_mid(orderbook)
        sigma = self.calculate_binary_volatility(ref_price)
        t = tau if tau is not None else self.config.tau

        reservation_price = self.calculate_reservation_price(ref_price, position.net_quantity, sigma, t)
        half_spread = self.calculate_optimal_half_spread(sigma, t)

        inventory_skew = reservation_price - ref_price

        raw_bid = reservation_price - half_spread
        raw_ask = reservation_price + half_spread

        # Snap to discrete 1-cent ticks
        bid_price = self.quantize_to_tick(raw_bid)
        ask_price = self.quantize_to_tick(raw_ask)

        # Enforce contract boundary constraints [0.01, 0.99]
        bid_price = max(self.config.min_price, min(self.config.max_price - self.config.tick_size, bid_price))
        ask_price = max(bid_price + self.config.tick_size, min(self.config.max_price, ask_price))

        # Enforce minimum 1 tick spread between bid and ask
        if ask_price <= bid_price:
            if bid_price + self.config.tick_size <= self.config.max_price:
                ask_price = round(bid_price + self.config.tick_size, 2)
            else:
                bid_price = round(ask_price - self.config.tick_size, 2)

        bid_size, ask_size = self.compute_quote_sizes(position.net_quantity)

        return KalshiQuote(
            ticker=orderbook.ticker,
            bid_price=round(bid_price, 2),
            ask_price=round(ask_price, 2),
            bid_size=bid_size,
            ask_size=ask_size,
            reservation_price=round(reservation_price, 4),
            half_spread=round(half_spread, 4),
            inventory_skew=round(inventory_skew, 4),
        )
