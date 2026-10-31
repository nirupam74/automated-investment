"""S&P 500 + market/crypto ETF momentum screener - rolling-sweep edition.

The universe is the full S&P 500 plus market ETFs (QQQ, SPY, GLD, USO) and crypto
ETFs (IBIT=BTC, ETHA=ETH, BSOL=SOL, XRPC=XRP). IBKR data pacing makes scoring all
~500 names every cycle impossible, so instead each run scores a small rotating
SLICE, records every name's latest momentum score in a leaderboard (scores.json),
and rebuilds the shortlist as the top uptrends across the WHOLE board. The ETFs
and any current holdings are always re-scored so they never go stale. Over a day
the slice cursor sweeps the entire index.

Standalone + safe - never places an order, only builds a watchlist.

    python screener.py        # run one sweep step, print the leaderboard top

Design notes: a name on the far side of the sweep may be scored on up to ~a day
old data; leaderboard entries older than STALE_DAYS are dropped. Momentum setups
concentrate in liquid names, so the long tail rarely tops the board - that's fine.
"""
import json
import os
import time

from ib_async import IB, Stock, util

import config
import indicators as ind
from sp500 import SP500

# --- tunables ---
SHORTLIST_SIZE = 8         # names the brain ultimately sees
SWEEP_SIZE = 20            # S&P names scored per run (rotating); keeps pacing safe
STALE_DAYS = 2             # drop leaderboard entries older than this
SCREENER_CLIENT_ID = 12
PACING_SLEEP = 0.3

# Always re-scored every run so they never go stale.
MARKET_ETFS = ["QQQ", "SPY", "GLD", "USO"]
CRYPTO_ETFS = ["IBIT", "ETHA", "BSOL", "XRPC"]   # BTC, ETH, SOL, XRP
ALWAYS = MARKET_ETFS + CRYPTO_ETFS

WATCHLIST_FILE = "watchlist.json"
CURSOR_FILE = "screen_cursor.json"
SCORES_FILE = "scores.json"


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    try:
        with open(path, "w") as f:
            json.dump(obj, f)
    except Exception:
        pass


def momentum_score(ib, symbol):
    """Daily-bar momentum score; higher = stronger clean uptrend. (score, detail) or (None, reason)."""
    contract = Stock(symbol, "SMART", "USD")
    try:
        ib.qualifyContracts(contract)
        bars = ib.reqHistoricalData(contract, "", config.DAILY_DURATION, config.DAILY_BAR,
                                    "TRADES", useRTH=True)
    except Exception as e:
        return None, f"data error: {e}"
    df = util.df(bars)
    if df is None or len(df) < 50:
        return None, "insufficient data"
    close = df["close"]
    last = float(close.iloc[-1])
    sma20 = float(ind.sma(close, 20).iloc[-1])
    sma50 = float(ind.sma(close, 50).iloc[-1])
    _, _, macd_hist = ind.macd(close)
    hist = float(macd_hist.iloc[-1])
    if not sma50:
        return None, "no sma50"
    ext = (last - sma50) / sma50
    stack = (sma20 - sma50) / sma50
    macd_term = hist / last if last else 0.0
    score = ext + stack + 0.5 * macd_term
    return score, {"score": round(score, 4), "trend": "up" if last > sma50 else "down", "last": round(last, 2)}


def _holdings(ib):
    try:
        return [p.contract.symbol for p in ib.positions() if p.position != 0]
    except Exception:
        return []


def run_screener(ib):
    """One sweep step: score the next slice + ETFs + holdings, update the leaderboard,
    return (shortlist, scores). Shortlist is the strongest uptrends across the board."""
    cursor = _load(CURSOR_FILE, {}).get("i", 0)
    scores = _load(SCORES_FILE, {})

    n = len(SP500)
    slice_ = [SP500[(cursor + k) % n] for k in range(SWEEP_SIZE)]
    cursor = (cursor + SWEEP_SIZE) % n

    # always-fresh names first, then holdings, then the rotating slice; dedupe, keep order
    batch = list(dict.fromkeys(ALWAYS + _holdings(ib) + slice_))
    print(f"[screener] sweep scoring {len(batch)} name(s); cursor now {cursor}/{n}")

    now = time.time()
    for sym in batch:
        score, detail = momentum_score(ib, sym)
        if score is not None:
            scores[sym] = {"score": detail["score"], "trend": detail["trend"], "ts": now}
        time.sleep(PACING_SLEEP)

    # prune stale entries
    cutoff = now - STALE_DAYS * 86400
    scores = {k: v for k, v in scores.items() if v.get("ts", 0) >= cutoff}

    _save(CURSOR_FILE, {"i": cursor})
    _save(SCORES_FILE, scores)

    ups = [(k, v) for k, v in scores.items() if v.get("trend") == "up"]
    ups.sort(key=lambda x: x[1]["score"], reverse=True)
    shortlist = [k for k, _ in ups[:SHORTLIST_SIZE]]
    if not shortlist:
        shortlist = list(config.WATCHLIST)[:SHORTLIST_SIZE]
    return shortlist, scores


def main():
    ib = IB()
    ib.connect(config.IB_HOST, config.IB_PORT, clientId=SCREENER_CLIENT_ID)
    ib.reqMarketDataType(config.MARKET_DATA_TYPE)
    try:
        shortlist, scores = run_screener(ib)
    finally:
        ib.disconnect()
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(shortlist, f, indent=2)
    ups = sorted([(k, v) for k, v in scores.items() if v.get("trend") == "up"],
                 key=lambda x: x[1]["score"], reverse=True)
    print(f"\nLeaderboard: {len(scores)} names tracked, {len(ups)} in uptrend.")
    print("Top of board:")
    for k, v in ups[:SHORTLIST_SIZE]:
        print(f"  {k:6} {v['score']:8.4f}")
    print(f"\nShortlist -> {WATCHLIST_FILE}: {', '.join(shortlist)}")


if __name__ == "__main__":
    main()
