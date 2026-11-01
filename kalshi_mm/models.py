"""Data models for Kalshi prediction markets and institutional market making."""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class PriceLevel:
    """Represents a price level in an order book with price and aggregated quantity."""

    price: float
    size: float

    def to_dict(self) -> Dict[str, float]:
        return {"price": round(self.price, 4), "size": round(self.size, 2)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PriceLevel:
        return cls(price=float(data["price"]), size=float(data["size"]))


@dataclass
class KalshiMarket:
    """Kalshi market instrument definition and CFTC contract specifications."""

    ticker: str
    title: str
    subtitle: Optional[str] = None
    status: str = "active"
    yes_sub_title: str = "Yes"
    no_sub_title: str = "No"
    expiration_time: Optional[float] = None
    strike_price: Optional[float] = None
    volume_24h: float = 0.0
    open_interest: float = 0.0
    tick_size: float = 0.01  # Kalshi discrete 1-cent tick grid
    min_price: float = 0.01  # 1 cent lower bound
    max_price: float = 0.99  # 99 cents upper bound

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "title": self.title,
            "subtitle": self.subtitle,
            "status": self.status,
            "yes_sub_title": self.yes_sub_title,
            "no_sub_title": self.no_sub_title,
            "expiration_time": self.expiration_time,
            "strike_price": self.strike_price,
            "volume_24h": self.volume_24h,
            "open_interest": self.open_interest,
            "tick_size": self.tick_size,
            "min_price": self.min_price,
            "max_price": self.max_price,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> KalshiMarket:
        return cls(
            ticker=str(data.get("ticker", "")),
            title=str(data.get("title", "")),
            subtitle=data.get("subtitle"),
            status=str(data.get("status", "active")),
            yes_sub_title=str(data.get("yes_sub_title", "Yes")),
            no_sub_title=str(data.get("no_sub_title", "No")),
            expiration_time=float(data["expiration_time"]) if data.get("expiration_time") is not None else None,
            strike_price=float(data["strike_price"]) if data.get("strike_price") is not None else None,
            volume_24h=float(data.get("volume_24h", 0.0)),
            open_interest=float(data.get("open_interest", 0.0)),
            tick_size=float(data.get("tick_size", 0.01)),
            min_price=float(data.get("min_price", 0.01)),
            max_price=float(data.get("max_price", 0.99)),
        )


@dataclass
class KalshiOrderBook:
    """Institutional Level 2 orderbook for Kalshi binary contracts."""

    ticker: str
    timestamp: float = field(default_factory=time.time)
    bids: List[PriceLevel] = field(default_factory=list)
    asks: List[PriceLevel] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Guarantee canonical sorting: Bids descending, Asks ascending
        self.bids = sorted([b for b in self.bids if b.size > 0], key=lambda x: x.price, reverse=True)
        self.asks = sorted([a for a in self.asks if a.size > 0], key=lambda x: x.price)

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    @property
    def best_bid_size(self) -> float:
        return self.bids[0].size if self.bids else 0.0

    @property
    def best_ask_size(self) -> float:
        return self.asks[0].size if self.asks else 0.0

    @property
    def mid_price(self) -> Optional[float]:
        bb = self.best_bid
        ba = self.best_ask
        if bb is not None and ba is not None:
            return round((bb + ba) / 2.0, 4)
        return bb if bb is not None else ba

    @property
    def spread(self) -> Optional[float]:
        bb = self.best_bid
        ba = self.best_ask
        if bb is not None and ba is not None:
            return round(ba - bb, 4)
        return None

    @property
    def spread_cents(self) -> Optional[float]:
        sp = self.spread
        return round(sp * 100.0, 2) if sp is not None else None

    @property
    def micro_price(self) -> float:
        """Calculate micro-price (weighted mid) incorporating top-of-book volume imbalance.

        Micro-price = (P_bid * Q_ask + P_ask * Q_bid) / (Q_bid + Q_ask)
        If queue sizes are equal, collapses to standard mid-price.
        """
        bb = self.best_bid
        ba = self.best_ask
        if bb is None or ba is None:
            return self.mid_price or 0.50

        qb = self.best_bid_size
        qa = self.best_ask_size
        total_vol = qb + qa

        if total_vol <= 0:
            return round((bb + ba) / 2.0, 4)

        return round((bb * qa + ba * qb) / total_vol, 4)

    def order_book_imbalance(self, levels: int = 1) -> float:
        """Order Book Imbalance (OBI) metric in range [-1.0, 1.0].

        OBI = (Sum(Bid_Qty) - Sum(Ask_Qty)) / (Sum(Bid_Qty) + Sum(Ask_Qty))
        Positive indicates buying pressure; negative indicates selling pressure.
        """
        bid_vol = sum(b.size for b in self.bids[:levels])
        ask_vol = sum(a.size for a in self.asks[:levels])
        denom = bid_vol + ask_vol
        if denom <= 0:
            return 0.0
        return round((bid_vol - ask_vol) / denom, 4)

    def total_depth(self, side: str, levels: int = 5) -> float:
        """Cumulative depth up to N levels."""
        if side.lower() == "buy" or side.lower() == "bid":
            return sum(b.size for b in self.bids[:levels])
        return sum(a.size for a in self.asks[:levels])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "timestamp": self.timestamp,
            "bids": [b.to_dict() for b in self.bids],
            "asks": [a.to_dict() for a in self.asks],
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "mid_price": self.mid_price,
            "micro_price": self.micro_price,
            "spread": self.spread,
            "obi": self.order_book_imbalance(levels=1),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> KalshiOrderBook:
        bids = [PriceLevel.from_dict(b) for b in data.get("bids", [])]
        asks = [PriceLevel.from_dict(a) for a in data.get("asks", [])]
        return cls(
            ticker=str(data.get("ticker", "")),
            timestamp=float(data.get("timestamp", time.time())),
            bids=bids,
            asks=asks,
        )


@dataclass
class KalshiQuote:
    """Market maker two-sided quote for Kalshi YES contracts."""

    ticker: str
    bid_price: float
    ask_price: float
    bid_size: int
    ask_size: int
    reservation_price: float
    half_spread: float
    inventory_skew: float
    timestamp: float = field(default_factory=time.time)

    @property
    def spread(self) -> float:
        return round(self.ask_price - self.bid_price, 4)

    @property
    def spread_cents(self) -> float:
        return round(self.spread * 100.0, 2)

    @property
    def is_valid(self) -> bool:
        """True if quote is valid: bid < ask, sizes >= 0, prices in [0.01, 0.99]."""
        if self.bid_size < 0 or self.ask_size < 0:
            return False
        if self.bid_size > 0 and (self.bid_price < 0.01 or self.bid_price > 0.99):
            return False
        if self.ask_size > 0 and (self.ask_price < 0.01 or self.ask_price > 0.99):
            return False
        if self.bid_size > 0 and self.ask_size > 0:
            return self.bid_price < self.ask_price
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "bid_price": round(self.bid_price, 4),
            "ask_price": round(self.ask_price, 4),
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
            "reservation_price": round(self.reservation_price, 4),
            "half_spread": round(self.half_spread, 4),
            "inventory_skew": round(self.inventory_skew, 4),
            "spread": self.spread,
            "timestamp": self.timestamp,
            "is_valid": self.is_valid,
        }


@dataclass
class KalshiPosition:
    """Tracks inventory, cash balance, and PnL for Kalshi trading accounts."""

    ticker: str
    net_quantity: int = 0  # +q: long YES contracts, -q: short YES (long NO)
    realized_pnl: float = 0.0
    cash: float = 10000.0
    avg_entry_price: float = 0.0
    total_volume: int = 0
    total_fees: float = 0.0
    trades_count: int = 0

    def unrealized_pnl(self, mark_price: float) -> float:
        """Unrealized PnL at mark_price."""
        if self.net_quantity == 0:
            return 0.0
        return round(self.net_quantity * (mark_price - self.avg_entry_price), 4)

    def total_pnl(self, mark_price: float) -> float:
        """Total PnL = Realized PnL + Unrealized PnL - Total Fees."""
        return round(self.realized_pnl + self.unrealized_pnl(mark_price) - self.total_fees, 4)

    def update_fill(self, side: str, price: float, size: int, fee: float = 0.0) -> float:
        """Update inventory on order fill. Returns trade realized PnL."""
        if size <= 0:
            return 0.0

        side_clean = side.lower()
        trade_realized_pnl = 0.0
        self.total_volume += size
        self.trades_count += 1
        self.total_fees += fee

        if side_clean in ("buy", "bid"):
            cost = price * size + fee
            self.cash -= cost

            if self.net_quantity >= 0:
                # Accumulating long position
                new_qty = self.net_quantity + size
                self.avg_entry_price = (
                    (self.net_quantity * self.avg_entry_price + size * price) / new_qty
                    if new_qty > 0
                    else 0.0
                )
                self.net_quantity = new_qty
            else:
                # Covering short position
                covered_qty = min(abs(self.net_quantity), size)
                pnl_per_contract = self.avg_entry_price - price
                trade_realized_pnl = covered_qty * pnl_per_contract
                self.realized_pnl += trade_realized_pnl

                remaining_buy = size - covered_qty
                if remaining_buy > 0:
                    self.net_quantity = remaining_buy
                    self.avg_entry_price = price
                else:
                    self.net_quantity += size
                    if self.net_quantity == 0:
                        self.avg_entry_price = 0.0

        elif side_clean in ("sell", "ask"):
            revenue = price * size - fee
            self.cash += revenue

            if self.net_quantity <= 0:
                # Accumulating short position
                abs_qty = abs(self.net_quantity)
                new_qty = abs_qty + size
                self.avg_entry_price = (
                    (abs_qty * self.avg_entry_price + size * price) / new_qty
                    if new_qty > 0
                    else 0.0
                )
                self.net_quantity = -new_qty
            else:
                # Unwinding long position
                unwound_qty = min(self.net_quantity, size)
                pnl_per_contract = price - self.avg_entry_price
                trade_realized_pnl = unwound_qty * pnl_per_contract
                self.realized_pnl += trade_realized_pnl

                remaining_sell = size - unwound_qty
                if remaining_sell > 0:
                    self.net_quantity = -remaining_sell
                    self.avg_entry_price = price
                else:
                    self.net_quantity -= size
                    if self.net_quantity == 0:
                        self.avg_entry_price = 0.0

        return round(trade_realized_pnl, 4)

    def to_dict(self, mark_price: Optional[float] = None) -> Dict[str, Any]:
        mp = mark_price or self.avg_entry_price
        return {
            "ticker": self.ticker,
            "net_quantity": self.net_quantity,
            "cash": round(self.cash, 2),
            "avg_entry_price": round(self.avg_entry_price, 4),
            "realized_pnl": round(self.realized_pnl, 4),
            "unrealized_pnl": self.unrealized_pnl(mp),
            "total_pnl": self.total_pnl(mp),
            "total_fees": round(self.total_fees, 4),
            "total_volume": self.total_volume,
            "trades_count": self.trades_count,
        }


@dataclass
class MarketOrder:
    """Order representation for matching and lifecycle management."""

    ticker: str
    side: str  # 'buy' or 'sell'
    price: float
    size: int
    order_type: str = "limit"  # 'limit' or 'market'
    is_maker: bool = True
    order_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: float = field(default_factory=time.time)
    status: str = "open"  # 'open', 'filled', 'cancelled', 'rejected'
    filled_size: int = 0
    fee: float = 0.0

    @property
    def remaining_size(self) -> int:
        return max(0, self.size - self.filled_size)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "ticker": self.ticker,
            "side": self.side,
            "price": round(self.price, 4),
            "size": self.size,
            "order_type": self.order_type,
            "is_maker": self.is_maker,
            "status": self.status,
            "filled_size": self.filled_size,
            "fee": round(self.fee, 4),
            "timestamp": self.timestamp,
        }
