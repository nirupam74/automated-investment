"""Phase 2 - Snapshot assembler.

Connects to IB Gateway (paper), and for each watchlist symbol pulls daily +
intraday bars, computes swing-trading indicators, and bundles everything
(plus account state and open positions) into one JSON snapshot. This snapshot
is the *only* thing the Phase 3 brain will see, so its quality caps the
quality of every decision downstream.
"""
import json
import math
from datetime import datetime, timezone

from ib_async import IB, Stock, Forex, util

import config
import indicators as ind


def clean(x):
    """Round floats and convert NaN/inf to None so the JSON is tidy."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return x
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, 4)


def get_account(ib: IB) -> dict:
    wanted = {"NetLiquidation", "TotalCashValue", "BuyingPower"}
    acct = {}
    for row in ib.accountSummary():
        if row.tag in wanted:
            acct[row.tag] = {"value": clean(row.value), "currency": row.currency}
    return acct


def get_fx_usdcad(ib: IB):
    """Current USD.CAD rate (CAD per 1 USD). Used to size USD trades correctly
    against a CAD-base account. Returns None if it can't be fetched."""
    try:
        bars = ib.reqHistoricalData(
            Forex("USDCAD"), "", "1 D", "1 day", "MIDPOINT", useRTH=False)
        if bars:
            return clean(bars[-1].close)
    except Exception:
        pass
    return None


def get_positions(ib: IB) -> list:
    out = []
    for p in ib.positions():
        out.append({
            "symbol": getattr(p.contract, "symbol", None),
            "position": clean(p.position),
            "avgCost": clean(p.avgCost),
        })
    return out


def analyze_symbol(ib: IB, symbol: str) -> dict:
    contract = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(contract)

    daily_bars = ib.reqHistoricalData(
        contract, "", config.DAILY_DURATION, config.DAILY_BAR,
        "TRADES", useRTH=True,
    )
    intra_bars = ib.reqHistoricalData(
        contract, "", config.INTRADAY_DURATION, config.INTRADAY_BAR,
        "TRADES", useRTH=True,
    )

    df_d = util.df(daily_bars)
    df_i = util.df(intra_bars)

    if df_d is None or len(df_d) < 50:
        return {"symbol": symbol, "error": "insufficient daily data (check market-data permissions)"}

    close = df_d["close"]
    last_price = float(df_i["close"].iloc[-1]) if df_i is not None and len(df_i) else float(close.iloc[-1])
    prev_close = float(close.iloc[-2])

    macd_line, macd_signal, macd_hist = ind.macd(close)
    sma20 = ind.sma(close, 20).iloc[-1]
    sma50 = ind.sma(close, 50).iloc[-1]
    rsi_d = ind.rsi(close, 14).iloc[-1]
    rsi_i = ind.rsi(df_i["close"], 14).iloc[-1] if df_i is not None and len(df_i) > 14 else None
    atr_d = ind.atr(df_d, 14).iloc[-1]

    high_20 = float(df_d["high"].tail(20).max())
    low_20 = float(df_d["low"].tail(20).min())
    avg_vol_20 = float(df_d["volume"].tail(20).mean())
    last_vol = float(df_d["volume"].iloc[-1])

    return {
        "symbol": symbol,
        "last_price": clean(last_price),
        "pct_change_day": clean((last_price - prev_close) / prev_close * 100),
        "sma20": clean(sma20),
        "sma50": clean(sma50),
        "ema9": clean(ind.ema(close, 9).iloc[-1]),
        "ema21": clean(ind.ema(close, 21).iloc[-1]),
        "pct_from_sma20": clean((last_price - sma20) / sma20 * 100),
        "pct_from_sma50": clean((last_price - sma50) / sma50 * 100),
        "trend": "up" if last_price > sma50 else "down",
        "rsi14_daily": clean(rsi_d),
        "rsi14_intraday": clean(rsi_i),
        "macd": clean(macd_line.iloc[-1]),
        "macd_signal": clean(macd_signal.iloc[-1]),
        "macd_hist": clean(macd_hist.iloc[-1]),
        "atr14": clean(atr_d),
        "high_20d": clean(high_20),
        "low_20d": clean(low_20),
        "avg_vol_20d": clean(avg_vol_20),
        "last_vol": clean(last_vol),
        "vol_vs_avg": clean(last_vol / avg_vol_20) if avg_vol_20 else None,
    }


def effective_watchlist():
    """Symbols to snapshot this cycle. Prefer the screener's watchlist.json
    (the dynamic S&P shortlist) when it's present and non-empty; otherwise fall
    back to the static config.WATCHLIST so the engine always has a universe."""
    try:
        with open("watchlist.json") as f:
            wl = json.load(f)
        if isinstance(wl, list) and wl:
            return wl
    except Exception:
        pass
    return config.WATCHLIST


def build_snapshot() -> dict:
    ib = IB()
    ib.connect(config.IB_HOST, config.IB_PORT, clientId=config.IB_CLIENT_ID)
    ib.reqMarketDataType(config.MARKET_DATA_TYPE)

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "account": get_account(ib),
        "fx": {"usd_cad": get_fx_usdcad(ib)},
        "positions": get_positions(ib),
        "symbols": [],
    }

    for sym in effective_watchlist():
        try:
            snapshot["symbols"].append(analyze_symbol(ib, sym))
        except Exception as e:
            snapshot["symbols"].append({"symbol": sym, "error": str(e)})

    ib.disconnect()
    return snapshot


if __name__ == "__main__":
    snap = build_snapshot()

    with open("snapshot.json", "w") as f:
        json.dump(snap, f, indent=2)

    print(f"Snapshot @ {snap['generated_at']}")
    acct = snap["account"].get("NetLiquidation", {})
    print(f"Net liquidation: {acct.get('value')} {acct.get('currency')}")
    print(f"USD.CAD rate: {snap['fx'].get('usd_cad')}")
    print(f"Open positions: {len(snap['positions'])}\n")
    for s in snap["symbols"]:
        if "error" in s:
            print(f"  {s['symbol']:6} ERROR: {s['error']}")
        else:
            print(f"  {s['symbol']:6} ${s['last_price']:<8} "
                  f"trend={s['trend']:<4} RSI={s['rsi14_daily']:<5} "
                  f"MACDh={s['macd_hist']:<7} ATR={s['atr14']}")
    print("\nFull detail written to snapshot.json")
