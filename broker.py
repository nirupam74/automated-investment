"""
broker.py — thin wrapper around Alpaca's official alpaca-py SDK.

Handles both stocks ("AAPL") and crypto ("BTC/USD"), picking the right data
client based on whether the symbol contains a slash.
"""

import os
from datetime import datetime, timedelta

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame


def _is_crypto(symbol: str) -> bool:
    return "/" in symbol


def norm(symbol: str) -> str:
    """Normalize for matching: 'BTC/USD' and 'BTCUSD' both -> 'BTCUSD'.

    Alpaca uses 'BTC/USD' when placing orders but reports positions without the
    slash, so we normalize whenever we compare 'do we hold this?'.
    """
    return symbol.replace("/", "").upper()


class Broker:
    def __init__(self):
        key = os.environ["ALPACA_API_KEY"]
        secret = os.environ["ALPACA_SECRET_KEY"]
        # paper=True unless LIVE_TRADING is exactly "true". This is the master switch.
        self.live = os.environ.get("LIVE_TRADING", "false").strip().lower() == "true"
        self.trading = TradingClient(key, secret, paper=not self.live)
        self.stock_data = StockHistoricalDataClient(key, secret)
        self.crypto_data = CryptoHistoricalDataClient(key, secret)

    # --- account state -----------------------------------------------------
    def equity(self) -> float:
        return float(self.trading.get_account().equity)

    def positions(self) -> dict:
        """Returns {normalized_symbol: market_value} for everything currently held."""
        out = {}
        for p in self.trading.get_all_positions():
            out[norm(p.symbol)] = float(p.market_value)
        return out

    # --- market data -------------------------------------------------------
    def recent_bars(self, symbol: str, lookback: int):
        """Returns a compact list of recent OHLCV candles to hand to Claude."""
        start = datetime.utcnow() - timedelta(days=14)
        if _is_crypto(symbol):
            req = CryptoBarsRequest(symbol_or_symbols=symbol,
                                    timeframe=TimeFrame.Hour, start=start)
            bars = self.crypto_data.get_crypto_bars(req)
        else:
            req = StockBarsRequest(symbol_or_symbols=symbol,
                                   timeframe=TimeFrame.Hour, start=start)
            bars = self.stock_data.get_stock_bars(req)

        rows = bars.data.get(symbol, [])[-lookback:]
        return [{"t": str(b.timestamp), "o": b.open, "h": b.high,
                 "l": b.low, "c": b.close, "v": b.volume} for b in rows]

    def last_price(self, symbol: str) -> float:
        bars = self.recent_bars(symbol, 1)
        return bars[-1]["c"] if bars else 0.0

    def recent_closes(self, symbol: str, lookback: int) -> list:
        return [b["c"] for b in self.recent_bars(symbol, lookback)]

    # --- execution ---------------------------------------------------------
    def market_order(self, symbol: str, notional: float, side: str):
        """Place a market order sized in dollars (notional)."""
        order = MarketOrderRequest(
            symbol=symbol,
            notional=round(notional, 2),
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.GTC if _is_crypto(symbol) else TimeInForce.DAY,
        )
        return self.trading.submit_order(order)

    def close_position(self, symbol: str):
        """Flatten the entire position in a symbol (used on a sell signal)."""
        return self.trading.close_position(norm(symbol))
