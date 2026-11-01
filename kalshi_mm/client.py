"""Resilient NaxosData API Gateway client and realistic synthetic orderbook generator.

Provides high-throughput Level 2 snapshot feeds for Kalshi prediction markets via
the NaxosData API Gateway (/api/v1/orderbooks/KALSHI/{ticker}/snapshot), with
automatic fallback to an institutional synthetic orderbook generator for offline demo
and paper-trading.
"""

from __future__ import annotations

import json
import logging
import math
import random
import time
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from kalshi_mm.models import KalshiMarket, KalshiOrderBook, PriceLevel

logger = logging.getLogger("kalshi_mm.client")


class SyntheticKalshiOrderBookGenerator:
    """Stateful institutional synthetic orderbook and market generator for demo/paper-trading.

    Simulates realistic price dynamics for Kalshi binary contracts:
    - Bounded mean-reverting jump-diffusion process in [0.02, 0.98]
    - Realistic L2 order book ladders with Poisson depth distribution
    - Order Book Imbalance (OBI) micro-drift
    """

    DEFAULT_MARKETS = [
        {
            "ticker": "KXFED-24DEC-T5.00",
            "title": "Fed funds target rate above 5.00% at end of year 2024",
            "initial_mid": 0.54,
            "volatility": 0.02,
        },
        {
            "ticker": "KXRECESSION-24",
            "title": "US enters NBER designated recession in 2024",
            "initial_mid": 0.22,
            "volatility": 0.015,
        },
        {
            "ticker": "KXINFLATION-3.0",
            "title": "CPI Year-over-Year above 3.0% for next release",
            "initial_mid": 0.68,
            "volatility": 0.025,
        },
        {
            "ticker": "PRES-2024",
            "title": "Presidential Election 2024: Winner Outcome",
            "initial_mid": 0.50,
            "volatility": 0.03,
        },
    ]

    def __init__(self, seed: Optional[int] = None) -> None:
        if seed is not None:
            random.seed(seed)
        self._states: Dict[str, Dict[str, Any]] = {}
        for m in self.DEFAULT_MARKETS:
            self._states[m["ticker"]] = {
                "title": m["title"],
                "mid": m["initial_mid"],
                "volatility": m["volatility"],
                "step": 0,
            }

    def get_markets(self) -> List[KalshiMarket]:
        """Return list of available synthetic markets."""
        markets = []
        for ticker, state in self._states.items():
            markets.append(
                KalshiMarket(
                    ticker=ticker,
                    title=state["title"],
                    status="active",
                    volume_24h=145200.0,
                    open_interest=58000.0,
                    tick_size=0.01,
                    min_price=0.01,
                    max_price=0.99,
                )
            )
        return markets

    def get_market(self, ticker: str) -> KalshiMarket:
        """Get market metadata for ticker."""
        if ticker not in self._states:
            self._states[ticker] = {
                "title": f"Kalshi Event Contract {ticker}",
                "mid": 0.50,
                "volatility": 0.02,
                "step": 0,
            }
        state = self._states[ticker]
        return KalshiMarket(
            ticker=ticker,
            title=state["title"],
            status="active",
            volume_24h=100000.0,
            open_interest=45000.0,
            tick_size=0.01,
            min_price=0.01,
            max_price=0.99,
        )

    def simulate_next_tick(self, ticker: str) -> KalshiOrderBook:
        """Advance time step and generate updated Level 2 orderbook snapshot."""
        if ticker not in self._states:
            self.get_market(ticker)

        state = self._states[ticker]
        mid = state["mid"]
        vol = state["volatility"]

        # Ornstein-Uhlenbeck mean-reverting drift towards 0.50 + Gaussian noise
        mean_reversion = 0.05 * (0.50 - mid)
        shock = random.gauss(0.0, vol)
        jump = random.choice([0.0, 0.0, 0.0, 0.0, 0.02, -0.02]) if random.random() < 0.1 else 0.0

        new_mid = mid + mean_reversion + shock + jump
        # Clamp mid inside [0.03, 0.97] to leave room for bids/asks
        new_mid = max(0.04, min(0.96, new_mid))
        state["mid"] = new_mid
        state["step"] += 1

        # Cent tick quantization for spread
        spread_cents = random.choice([2, 3, 4, 5])
        half_spread = (spread_cents / 2.0) / 100.0

        best_bid_price = round(math.floor((new_mid - half_spread) * 100.0) / 100.0, 2)
        best_ask_price = round(math.ceil((new_mid + half_spread) * 100.0) / 100.0, 2)

        if best_ask_price <= best_bid_price:
            best_ask_price = round(best_bid_price + 0.01, 2)

        # Generate L2 depth (5 levels each side)
        bids: List[PriceLevel] = []
        asks: List[PriceLevel] = []

        curr_bid = best_bid_price
        for i in range(5):
            if curr_bid < 0.01:
                break
            # Queue size with Poisson/exponential distribution
            size = int(max(10, random.expovariate(1.0 / 120.0)))
            bids.append(PriceLevel(price=curr_bid, size=size))
            curr_bid = round(curr_bid - 0.01, 2)

        curr_ask = best_ask_price
        for i in range(5):
            if curr_ask > 0.99:
                break
            size = int(max(10, random.expovariate(1.0 / 120.0)))
            asks.append(PriceLevel(price=curr_ask, size=size))
            curr_ask = round(curr_ask + 0.01, 2)

        return KalshiOrderBook(
            ticker=ticker,
            timestamp=time.time(),
            bids=bids,
            asks=asks,
        )


class NaxosKalshiClient:
    """Institutional API Gateway client for Kalshi order books and market feeds."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.naxosdata.com",
        timeout: float = 5.0,
        offline_mode: bool = False,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.offline_mode = offline_mode
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        self._synthetic = SyntheticKalshiOrderBookGenerator()
        self._httpx_client: Optional[Any] = None

        if HAS_HTTPX and not self.offline_mode:
            headers = {
                "Accept": "application/json",
                "User-Agent": "KalshiMM-Bot/1.0 (NaxosData Quant Engine)",
            }
            if self.api_key:
                headers["x-api-key"] = self.api_key
            self._httpx_client = httpx.Client(base_url=self.base_url, headers=headers, timeout=self.timeout)

    def _http_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Perform resilient GET request with retries and exponential backoff."""
        if self.offline_mode:
            return None

        url = f"{self.base_url}{path}"
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{query}"

        for attempt in range(self.max_retries):
            try:
                if self._httpx_client:
                    resp = self._httpx_client.get(path, params=params)
                    if resp.status_code == 200:
                        return resp.json()
                    logger.warning("HTTP GET %s returned status %d", path, resp.status_code)
                else:
                    req = Request(url)
                    req.add_header("Accept", "application/json")
                    req.add_header("User-Agent", "KalshiMM-Bot/1.0 (NaxosData Quant Engine)")
                    if self.api_key:
                        req.add_header("x-api-key", self.api_key)
                    with urlopen(req, timeout=self.timeout) as response:
                        if response.status == 200:
                            return json.loads(response.read().decode("utf-8"))
            except Exception as e:
                logger.debug("Request %s attempt %d failed: %s", path, attempt + 1, e)

            if attempt < self.max_retries - 1:
                sleep_time = self.backoff_factor * (2 ** attempt)
                time.sleep(sleep_time)

        return None

    def get_markets(self, venue: str = "KALSHI") -> List[KalshiMarket]:
        """Fetch list of Kalshi markets from NaxosData API Gateway or fallback to synthetic."""
        if not self.offline_mode:
            data = self._http_get("/api/v1/markets", params={"venue": venue})
            if data and "markets" in data:
                try:
                    return [KalshiMarket.from_dict(m) for m in data["markets"]]
                except Exception as e:
                    logger.warning("Failed to parse API markets payload: %s; falling back", e)

        return self._synthetic.get_markets()

    def get_market(self, ticker: str) -> KalshiMarket:
        """Fetch market contract metadata for a single Kalshi ticker."""
        if not self.offline_mode:
            data = self._http_get(f"/api/v1/markets/{ticker}", params={"venue": "KALSHI"})
            if data and "market" in data:
                try:
                    return KalshiMarket.from_dict(data["market"])
                except Exception as e:
                    logger.warning("Failed to parse market metadata: %s", e)

        return self._synthetic.get_market(ticker)

    def get_orderbook_snapshot(self, ticker: str) -> KalshiOrderBook:
        """Fetch Level 2 orderbook snapshot (/api/v1/orderbooks/KALSHI/{ticker}/snapshot)."""
        if not self.offline_mode:
            data = self._http_get(f"/api/v1/orderbooks/KALSHI/{ticker}/snapshot")
            if data and ("bids" in data or "orderbook" in data):
                ob_data = data.get("orderbook", data)
                try:
                    return KalshiOrderBook.from_dict(ob_data)
                except Exception as e:
                    logger.warning("Failed to parse L2 orderbook snapshot: %s; falling back", e)

        # Fallback to realistic synthetic generator
        return self._synthetic.simulate_next_tick(ticker)

    def check_health(self) -> bool:
        """Ping NaxosData API Gateway health status."""
        if self.offline_mode:
            return True
        data = self._http_get("/api/v1/health")
        return bool(data and data.get("status") in ("ok", "healthy"))

    def close(self) -> None:
        """Close underlying HTTP connections."""
        if self._httpx_client:
            try:
                self._httpx_client.close()
            except Exception:
                pass
