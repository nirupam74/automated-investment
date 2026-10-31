"""
sentiment.py — pull recent news headlines for a symbol from Financial Modeling Prep.

Used by ClaudeStrategy so the model reasons over more than raw price. Uses only
the standard library (urllib) so there's no extra dependency.

Get a free key at https://site.financialmodelingprep.com/ and put it in .env as
FMP_API_KEY. Note: FMP's free tier is rate-limited and some news endpoints may
require a paid plan — this module degrades gracefully (returns "no news") on any
error so a sentiment hiccup never blocks or crashes the bot.

If FMP changes a path, you only need to edit FMP_BASE / the endpoint names below;
verify against https://site.financialmodelingprep.com/developer/docs/stable
"""

import os
import json
import urllib.parse
import urllib.request

FMP_BASE = "https://financialmodelingprep.com/stable"


def _fmp_symbol(symbol: str) -> str:
    # Alpaca crypto "BTC/USD" -> FMP "BTCUSD"; stocks pass through unchanged.
    return symbol.replace("/", "").upper()


def get_headlines(symbol: str, limit: int = 8) -> list[dict]:
    key = os.environ.get("FMP_API_KEY")
    if not key:
        return []
    endpoint = "news/crypto" if "/" in symbol else "news/stock"
    params = urllib.parse.urlencode(
        {"symbols": _fmp_symbol(symbol), "limit": limit, "apikey": key})
    url = f"{FMP_BASE}/{endpoint}?{params}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:  # network error, rate limit, plan restriction, bad JSON
        print(f"  ! sentiment fetch failed for {symbol}: {e}")
        return []

    out = []
    for item in (data or [])[:limit]:
        out.append({
            "title": item.get("title", ""),
            "snippet": (item.get("text") or "")[:240],
            "site": item.get("site") or item.get("publisher") or "",
            "date": item.get("publishedDate") or item.get("date") or "",
        })
    return out


def summarize_for_prompt(symbol: str, headlines: list[dict]) -> str:
    """Compact text block to hand to the model."""
    if not headlines:
        return "No recent news available."
    lines = [f"- [{h['date']}] {h['title']} ({h['site']}): {h['snippet']}"
             for h in headlines]
    return f"Recent news for {symbol}:\n" + "\n".join(lines)
