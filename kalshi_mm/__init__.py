"""Kalshi Market Maker Bot.

Institutional Avellaneda-Stoikov Market Making Engine & CFTC Fee Optimizer
for Kalshi Prediction Markets, powered by the NaxosData API Gateway.
"""

from kalshi_mm.models import (
    KalshiMarket,
    KalshiOrderBook,
    KalshiPosition,
    KalshiQuote,
    MarketOrder,
    PriceLevel,
)
from kalshi_mm.cftc_fees import (
    calculate_cftc_maker_fee,
    calculate_cftc_taker_fee,
    calculate_net_spread_after_cftc_fees,
    calculate_effective_cost,
    get_cftc_fee_curve,
    MakerFeeSchedule,
)
from kalshi_mm.strategy import (
    KalshiAvellanedaStoikovMM,
    StrategyConfig,
)
from kalshi_mm.client import (
    NaxosKalshiClient,
    SyntheticKalshiOrderBookGenerator,
)
from kalshi_mm.simulator import (
    MarketSimulator,
    SimulationResult,
)

__version__ = "1.0.0"
__all__ = [
    "KalshiMarket",
    "KalshiOrderBook",
    "KalshiPosition",
    "KalshiQuote",
    "MarketOrder",
    "PriceLevel",
    "calculate_cftc_maker_fee",
    "calculate_cftc_taker_fee",
    "calculate_net_spread_after_cftc_fees",
    "calculate_effective_cost",
    "get_cftc_fee_curve",
    "MakerFeeSchedule",
    "KalshiAvellanedaStoikovMM",
    "StrategyConfig",
    "NaxosKalshiClient",
    "SyntheticKalshiOrderBookGenerator",
    "MarketSimulator",
    "SimulationResult",
]
