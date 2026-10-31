"""
backtest.py — replay history, no real money, no API calls.

It pulls historical daily bars from Alpaca (data access only — no trading, no
paper account needed beyond your keys), runs your strategy bar-by-bar with NO
lookahead (decide on today's close, fill at tomorrow's open), applies a simple
fee, and reports how you'd have done versus simply buying and holding.

That benchmark is the point. If a strategy can't beat buy-and-hold after costs,
it isn't an edge — it's just activity. Most don't. Better to learn that here,
for free, than with money.

Run:  python backtest.py
"""

import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

import config
from strategy import SmaCrossStrategy

from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame


def fetch_history(symbol: str, days: int):
    """Return chronological list of {'o','c'} daily bars."""
    key = os.environ["ALPACA_API_KEY"]
    secret = os.environ["ALPACA_SECRET_KEY"]
    start = datetime.utcnow() - timedelta(days=days)
    if "/" in symbol:
        client = CryptoHistoricalDataClient(key, secret)
        bars = client.get_crypto_bars(
            CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day, start=start))
    else:
        client = StockHistoricalDataClient(key, secret)
        bars = client.get_stock_bars(
            StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day, start=start))
    return [{"o": b.open, "c": b.close} for b in bars.data.get(symbol, [])]


def run_backtest():
    symbol = config.BACKTEST_SYMBOL
    bars = fetch_history(symbol, config.BACKTEST_DAYS)
    if len(bars) < config.SMA_LONG + 2:
        print("Not enough history returned to backtest.")
        return

    closes = [b["c"] for b in bars]
    opens = [b["o"] for b in bars]
    strat = SmaCrossStrategy(config.SMA_SHORT, config.SMA_LONG)
    fee = config.BACKTEST_FEE_BPS / 10_000.0

    cash = config.BACKTEST_CASH
    units = 0.0
    holding = False
    entry_price = 0.0
    trades, wins = 0, 0
    equity_curve = []

    # Decide on bar i's close; execute on bar i+1's open (no lookahead).
    for i in range(len(bars) - 1):
        action = strat.decide(closes[: i + 1], holding)
        fill = opens[i + 1]

        if action == "buy" and not holding:
            units = (cash * (1 - fee)) / fill
            entry_price = fill
            cash = 0.0
            holding = True
        elif action == "sell" and holding:
            cash = units * fill * (1 - fee)
            trades += 1
            if fill > entry_price:
                wins += 1
            units = 0.0
            holding = False

        equity_curve.append(cash + units * closes[i + 1])

    # Close any open position at the final price for a fair comparison.
    if holding:
        cash = units * closes[-1] * (1 - fee)
        trades += 1
        if closes[-1] > entry_price:
            wins += 1
        units = 0.0

    final_equity = cash
    strat_return = (final_equity / config.BACKTEST_CASH - 1) * 100
    hold_return = (closes[-1] / closes[0] - 1) * 100

    # Max drawdown on the strategy equity curve.
    peak, max_dd = equity_curve[0], 0.0
    for v in equity_curve:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak)

    print(f"\n=== Backtest: {symbol}, {len(bars)} daily bars "
          f"(SMA {config.SMA_SHORT}/{config.SMA_LONG}) ===")
    print(f"  Starting cash      : ${config.BACKTEST_CASH:,.2f}")
    print(f"  Ending equity      : ${final_equity:,.2f}")
    print(f"  Strategy return    : {strat_return:+.1f}%")
    print(f"  Buy & hold return  : {hold_return:+.1f}%   <-- the bar to beat")
    print(f"  Beat buy & hold?   : {'YES' if strat_return > hold_return else 'NO'}")
    print(f"  Round-trip trades  : {trades}")
    print(f"  Win rate           : {(wins / trades * 100) if trades else 0:.0f}%")
    print(f"  Max drawdown       : {max_dd * 100:.1f}%")
    print("\nNote: past performance does not predict future results, and a backtest")
    print("that beats buy-and-hold can still lose money live (overfitting, costs,")
    print("slippage, regime change). Treat a good result as 'worth paper-trading',")
    print("never as 'proven'.")


if __name__ == "__main__":
    run_backtest()
