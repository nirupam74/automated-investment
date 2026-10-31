"""
strategy.py — a strategy is just: given price history (and optionally news),
what do I do now?

All strategies share ONE interface:
    decide(closes, holding, symbol=None, sentiment=None) -> "buy" | "sell" | "hold"
so the live bot and the backtester can swap them freely. SmaCross ignores the
optional symbol/sentiment args; ClaudeStrategy uses them.
"""

import json
from statistics import mean


class Strategy:
    def decide(self, closes: list[float], holding: bool,
               symbol: str = None, sentiment: str = None) -> str:
        raise NotImplementedError


class SmaCrossStrategy(Strategy):
    """Classic moving-average crossover. Long-only. Deterministic and fast — the
    backtestable baseline. If a fancier idea can't beat this (and buy-and-hold),
    it isn't worth risking money on.
    """

    def __init__(self, short_window: int = 10, long_window: int = 30):
        self.short = short_window
        self.long = long_window

    def decide(self, closes, holding, symbol=None, sentiment=None) -> str:
        if len(closes) < self.long + 1:
            return "hold"

        def sma(series, n):
            return mean(series[-n:])

        short_now, long_now = sma(closes, self.short), sma(closes, self.long)
        short_prev, long_prev = sma(closes[:-1], self.short), sma(closes[:-1], self.long)
        crossed_up = short_prev <= long_prev and short_now > long_now
        crossed_down = short_prev >= long_prev and short_now < long_now

        if crossed_up and not holding:
            return "buy"
        if crossed_down and holding:
            return "sell"
        return "hold"


class ClaudeStrategy(Strategy):
    """Live-only strategy: hands Claude recent prices PLUS news sentiment and asks
    for a single action. Do NOT backtest this — one API call per bar over months
    of history is slow, costly, and non-deterministic (so not reproducible).
    Backtest with SmaCross; deploy this and compare on a paper account.
    """

    _SYSTEM = (
        "You are a cautious, long-only trading analyst. Given a symbol, recent "
        "closing prices (oldest first), whether the account currently holds it, "
        "and recent news, decide a single action. 'hold' (do nothing) is valid and "
        "often correct. Only buy on a genuinely constructive setup; only sell to "
        "exit a position that has turned. Respond with EXACTLY one word: buy, sell, "
        "or hold. No punctuation, no explanation."
    )

    def __init__(self, model: str, recent_n: int = 30):
        from anthropic import Anthropic  # imported lazily so backtests don't need it
        self.client = Anthropic()
        self.model = model
        self.recent_n = recent_n

    def decide(self, closes, holding, symbol=None, sentiment=None) -> str:
        payload = {
            "symbol": symbol,
            "currently_holding": holding,
            "recent_closes": [round(c, 4) for c in closes[-self.recent_n:]],
            "news": sentiment or "No news provided.",
        }
        msg = self.client.messages.create(
            model=self.model, max_tokens=16, system=self._SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload)}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip().lower()
        for action in ("buy", "sell", "hold"):
            if action in text:
                return action
        return "hold"
