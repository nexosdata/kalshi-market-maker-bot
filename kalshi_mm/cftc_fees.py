"""CFTC Rule 40.2 quadratic fee models and maker rebate engine for Kalshi.

Under Kalshi's CFTC Rule 40.2 regulatory filing, binary contract taker fees
are governed by the quadratic schedule:
    Fee = ceil(0.07 * C * P * (1 - P) * 100) / 100
where:
    - C = Number of contracts traded
    - P = Execution price bounded in [0.01, 0.99]
    - P * (1 - P) represents Bernoulli variance, reaching maximum fee at P = 0.50.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union


@dataclass(frozen=True)
class MakerFeeSchedule:
    """Designated Contract Market (DCM) liquidity maker fee & rebate tiers."""

    tier_name: str = "Standard Maker"
    maker_fee_per_contract: float = 0.0  # 0.00 means 0% maker fee
    is_rebate: bool = False  # True if negative fee (liquidity rebate)

    @classmethod
    def standard(cls) -> MakerFeeSchedule:
        """Standard DCM tier: 0% maker fee."""
        return cls("Standard", 0.0, False)

    @classmethod
    def active_mm(cls) -> MakerFeeSchedule:
        """Active MM tier: $0.0010 ($0.10 cent) rebate per contract."""
        return cls("Active MM", -0.0010, True)

    @classmethod
    def institutional(cls) -> MakerFeeSchedule:
        """Institutional Designated Market Maker: $0.0025 ($0.25 cent) rebate."""
        return cls("Institutional DMM", -0.0025, True)


def calculate_cftc_taker_fee(
    contracts: Union[int, float],
    price: float,
    round_to_cents: bool = True,
) -> float:
    """Exact CFTC Rule 40.2 quadratic taker fee formula.

    Formula:
        Fee = ceil(0.07 * C * P * (1 - P) * 100) / 100

    Args:
        contracts: Number of contracts (C)
        price: Contract price P in range [0.01, 0.99]
        round_to_cents: Whether to apply ceil() to the nearest whole cent ($0.01).

    Returns:
        Total taker fee in USD.
    """
    c = float(contracts)
    if c <= 0 or price <= 0.0 or price >= 1.0:
        return 0.0

    # Ensure price is bounded within valid range
    p = max(0.001, min(0.999, price))
    raw_cents = 0.07 * c * p * (1.0 - p) * 100.0

    if round_to_cents:
        # Round floating point to 9 digits to prevent floating precision overshoot
        cents_rounded = math.ceil(round(raw_cents, 9))
        return round(cents_rounded / 100.0, 4)

    return round(raw_cents / 100.0, 6)


def calculate_cftc_maker_fee(
    contracts: Union[int, float],
    price: float,
    maker_fee_rate: float = 0.0,
) -> float:
    """Calculate maker fee or rebate for limit orders.

    Args:
        contracts: Number of contracts
        price: Execution price
        maker_fee_rate: Fee per contract in USD (positive = fee, negative = rebate).
            Default is 0.00 (Kalshi standard DCM 0% maker fee).

    Returns:
        Total maker fee in USD (negative represents rebate received).
    """
    c = float(contracts)
    if c <= 0 or price <= 0.0 or price >= 1.0:
        return 0.0

    raw_fee = c * maker_fee_rate
    return round(raw_fee, 4)


def calculate_net_spread_after_cftc_fees(
    bid_price: float,
    ask_price: float,
    contracts: int = 10,
    is_maker: bool = True,
    maker_fee_rate: float = 0.0,
) -> float:
    """Calculate net captured spread in USD for a round-trip buy & sell fill.

    Args:
        bid_price: Price bought at
        ask_price: Price sold at
        contracts: Order size
        is_maker: If True, uses maker fee schedule (0% or rebate). If False, uses CFTC taker fees.
        maker_fee_rate: Custom maker fee / rebate per contract.

    Returns:
        Net spread profit in USD.
    """
    if ask_price <= bid_price or contracts <= 0:
        return 0.0

    gross_pnl = (ask_price - bid_price) * contracts

    if is_maker:
        buy_fee = calculate_cftc_maker_fee(contracts, bid_price, maker_fee_rate)
        sell_fee = calculate_cftc_maker_fee(contracts, ask_price, maker_fee_rate)
    else:
        buy_fee = calculate_cftc_taker_fee(contracts, bid_price)
        sell_fee = calculate_cftc_taker_fee(contracts, ask_price)

    net_pnl = gross_pnl - (buy_fee + sell_fee)
    return round(net_pnl, 4)


def calculate_effective_cost(
    contracts: int,
    price: float,
    is_maker: bool = True,
    maker_fee_rate: float = 0.0,
) -> float:
    """Total capital outlay including applicable CFTC fees.

    Args:
        contracts: Quantity of contracts
        price: Price per contract
        is_maker: True if limit order maker, False if taker
        maker_fee_rate: Maker rate if is_maker is True

    Returns:
        Total dollar cost in USD.
    """
    notional = contracts * price
    fee = (
        calculate_cftc_maker_fee(contracts, price, maker_fee_rate)
        if is_maker
        else calculate_cftc_taker_fee(contracts, price)
    )
    return round(notional + fee, 4)


def calculate_taker_breakeven_spread(p_mid: float, contracts: int = 100) -> float:
    """Minimum round-trip bid-ask spread required for a taker to break even."""
    taker_fee_bid = calculate_cftc_taker_fee(contracts, p_mid)
    taker_fee_ask = calculate_cftc_taker_fee(contracts, p_mid)
    total_fee = taker_fee_bid + taker_fee_ask
    return round(total_fee / contracts, 4)


def get_cftc_fee_curve(
    contracts: int = 100,
    step_cents: int = 5,
) -> List[Dict[str, Union[float, int]]]:
    """Generate the discrete CFTC Rule 40.2 fee curve across the [0.01, 0.99] price spectrum."""
    curve = []
    for price_cent in range(step_cents, 100, step_cents):
        price = price_cent / 100.0
        fee_usd = calculate_cftc_taker_fee(contracts, price)
        fee_per_contract = (fee_usd / contracts) * 100.0  # in cents
        notional_usd = contracts * price
        fee_pct_notional = (fee_usd / notional_usd) * 100.0 if notional_usd > 0 else 0.0

        curve.append({
            "price": round(price, 2),
            "contracts": contracts,
            "fee_usd": round(fee_usd, 4),
            "fee_per_contract_cents": round(fee_per_contract, 3),
            "fee_pct_notional": round(fee_pct_notional, 3),
        })
    return curve
