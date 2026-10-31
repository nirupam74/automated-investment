"""
report.py — turn the logs into a comparison. Run after paper-trading each strategy.

  python report.py

Prints, per strategy: starting/ending equity, return %, max drawdown, cycles
logged, and executed buy/sell counts.

HONEST CAVEAT: this only compares fairly if the strategies ran over similar time
periods. Two strategies tested in different weeks faced different markets, so a
difference may be luck, not skill. The cleanest fair test is to run each over the
same span (or longer spans that average out luck). Treat small gaps as noise.
"""

import os
import csv
from collections import defaultdict
from logger import EQUITY, DECISIONS


def _load(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def _max_drawdown(curve):
    peak, mdd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak if peak else 0.0)
    return mdd


def main():
    eq_rows = _load(EQUITY)
    dec_rows = _load(DECISIONS)
    if not eq_rows:
        print("No logs yet. Run main.py to generate logs/equity.csv first.")
        return

    curves = defaultdict(list)
    for r in eq_rows:
        curves[r["strategy"]].append(float(r["equity"]))

    executed = defaultdict(lambda: {"buy": 0, "sell": 0})
    for r in dec_rows:
        if r["status"] == "executed" and r["action"] in ("buy", "sell"):
            executed[r["strategy"]][r["action"]] += 1

    print("\n=== Paper-trading comparison ===")
    for strat, curve in curves.items():
        ret = (curve[-1] / curve[0] - 1) * 100 if curve[0] else 0.0
        print(f"\n[{strat}]")
        print(f"  cycles logged   : {len(curve)}")
        print(f"  start equity    : ${curve[0]:,.2f}")
        print(f"  end equity      : ${curve[-1]:,.2f}")
        print(f"  return          : {ret:+.2f}%")
        print(f"  max drawdown    : {_max_drawdown(curve) * 100:.1f}%")
        print(f"  executed trades : {executed[strat]['buy']} buys, "
              f"{executed[strat]['sell']} sells")
    print("\n(Fair comparison requires similar time windows — see note in report.py.)")


if __name__ == "__main__":
    main()
