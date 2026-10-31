"""
logger.py — append-only CSV records so paper-trading results are measured, not
eyeballed. Two files under logs/:

  equity.csv    one row per cycle  : ts, strategy, equity, open_positions
  decisions.csv one row per call   : ts, strategy, symbol, holding, action,
                                     status, reason, price

'status' is one of: hold, skip (risk-rejected), declined (you said no),
executed, failed. Read them later with report.py.
"""

import os
import csv
from datetime import datetime, timezone

LOG_DIR = "logs"
EQUITY = os.path.join(LOG_DIR, "equity.csv")
DECISIONS = os.path.join(LOG_DIR, "decisions.csv")

_EQ_HEADER = ["ts", "strategy", "equity", "open_positions"]
_DEC_HEADER = ["ts", "strategy", "symbol", "holding", "action",
               "status", "reason", "price"]


class Logger:
    def __init__(self, strategy_name: str):
        self.strategy = strategy_name
        os.makedirs(LOG_DIR, exist_ok=True)
        self._ensure(EQUITY, _EQ_HEADER)
        self._ensure(DECISIONS, _DEC_HEADER)

    @staticmethod
    def _ensure(path, header):
        if not os.path.exists(path):
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(header)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def equity(self, equity: float, open_positions: int):
        with open(EQUITY, "a", newline="") as f:
            csv.writer(f).writerow([self._now(), self.strategy,
                                    round(equity, 2), open_positions])

    def decision(self, symbol: str, holding: bool, action: str,
                 status: str, reason: str = "", price: float = 0.0):
        with open(DECISIONS, "a", newline="") as f:
            csv.writer(f).writerow([self._now(), self.strategy, symbol, holding,
                                    action, status, reason, round(price, 4)])
