"""Daily recap - a read-only readout of what the engine did today.

Reads the day's log plus the current JSON audit files and prints a digestible
summary: mode, account and daily P&L, the screened watchlist, open positions,
how many cycles ran, what the brain has been saying, every order it approved or
executed, a tally of why entries were held back, and any warnings. It touches
nothing live - pure reporting.

    python recap.py             # today (ET)
    python recap.py 2026-06-09  # a specific day's log
"""
import json
import re
import sys
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def money(v):
    return f"{v:,.2f}" if isinstance(v, (int, float)) else "n/a"


def parse_log(path):
    """Pull the day's narrative out of the log."""
    try:
        lines = open(path).read().splitlines()
    except FileNotFoundError:
        return None
    out = {"cycles": 0, "reads": [], "decisions": [], "executed": [],
           "plans": [], "warnings": [], "refreshes": [], "first": None, "last": None}
    for ln in lines:
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (\w+) (.*)", ln)
        if not m:
            continue
        ts, level, msg = m.groups()
        out["first"] = out["first"] or ts
        out["last"] = ts
        if "=== cycle start ===" in msg:
            out["cycles"] += 1
        elif msg.startswith("market read:"):
            out["reads"].append(msg[len("market read:"):].strip())
        elif msg.startswith("watchlist refreshed"):
            out["refreshes"].append(msg)
        elif "EXECUTED" in msg:
            out["executed"].append(f"{ts}  {msg.strip()}")
        elif "PLAN (not sent)" in msg:
            out["plans"].append(msg.strip())
        elif level == "WARNING" or "cycle failed" in msg:
            out["warnings"].append(f"{ts}  {msg.strip()}")
        else:
            dm = re.match(r"([A-Z]{1,6})\s+(BUY|SELL|HOLD|CLOSE)\s+"
                          r"(APPROVED|REJECTED|BLOCKED|SKIPPED)\s+(.*)", msg.strip())
            if dm:
                out["decisions"].append(dm.groups())
    return out


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(ET).strftime("%Y-%m-%d")
    logpath = f"logs/run_{day.replace('-', '')}.log"

    controls = load_json("controls.json", {})
    snap = load_json("snapshot.json", {})
    watchlist = load_json("watchlist.json", [])
    baseline = load_json("daily_baseline.json", {})

    nlv = snap.get("account", {}).get("NetLiquidation", {})
    equity, ccy = nlv.get("value"), nlv.get("currency", "")
    start_eq = baseline.get("start_equity") if baseline.get("date") == day else None
    pnl = ((equity - start_eq) / start_eq * 100) if (equity and start_eq) else None

    mode = "SAFE (analyze only)" if controls.get("dry_run", True) else "ARMED (placing orders)"
    if controls.get("kill_switch"):
        mode = "HALTED (kill switch ON)"

    print("=" * 62)
    print(f"  DAILY RECAP - {day}")
    print("=" * 62)
    print(f"Mode       : {mode}")
    line = f"Equity     : {money(equity)} {ccy}"
    if pnl is not None:
        line += f"   (day P&L {pnl:+.2f}% vs {money(start_eq)} open)"
    print(line)
    fx = snap.get("fx", {}).get("usd_cad")
    if fx:
        print(f"USD.CAD    : {fx}")
    print(f"Watchlist  : {', '.join(watchlist) if watchlist else '(none)'}")

    positions = snap.get("positions", [])
    if positions:
        print("Positions  :")
        for p in positions:
            print(f"    {str(p.get('symbol')):6} {p.get('position')} @ {money(p.get('avgCost'))}")
    else:
        print("Positions  : flat")

    log = parse_log(logpath)
    print("-" * 62)
    if not log:
        print(f"No log for {day} ({logpath}).")
        print("Either the engine hasn't run today, or it's a non-trading day.")
        print("=" * 62)
        return

    span = f"{log['first']} -> {log['last']} UTC" if log["first"] else "n/a"
    print(f"Cycles run : {log['cycles']}   ({span})")
    if log["refreshes"]:
        print(f"Screens    : {len(log['refreshes'])}  (last: {log['refreshes'][-1]})")

    appr = [d for d in log["decisions"] if d[2] == "APPROVED"]
    rej = [d for d in log["decisions"] if d[2] == "REJECTED"]
    blk = [d for d in log["decisions"] if d[2] == "BLOCKED"]
    print(f"Decisions  : {len(appr)} approved, {len(rej)} rejected, {len(blk)} blocked")

    if log["executed"]:
        print("Executed   :")
        for e in log["executed"]:
            print(f"    {e}")
    if log["plans"]:
        print(f"Would-have : {len(log['plans'])} order(s) planned but not sent (SAFE mode)")

    reasons = Counter()
    for d in rej + blk:
        reasons[re.sub(r"[-\d.]+", "N", d[3])] += 1   # group reasons, numbers -> N
    if reasons:
        print("Why held back:")
        for reason, n in reasons.most_common():
            print(f"    {n:3}x  {reason}")

    if log["warnings"]:
        print("Warnings   :")
        for w in log["warnings"][-5:]:
            print(f"    {w}")

    if log["reads"]:
        print("-" * 62)
        print("Latest market read:")
        print(f"  {log['reads'][-1]}")
    print("=" * 62)


if __name__ == "__main__":
    main()
