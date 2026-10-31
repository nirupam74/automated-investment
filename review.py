"""AI performance review - read-only, human-gated.

Refreshes the trade ledger from IBKR, computes the real summary stats, and asks
Claude for a plain-English analysis with CAUTIOUS, small-sample-aware notes. It
never changes the bot - it hands you a report and you decide what, if anything,
to do. Run it whenever (it gets useful only after dozens of trades).

    python review.py
"""
import glob
import json
from collections import defaultdict

import anthropic
from ib_async import IB

import config          # loads .env (ANTHROPIC_API_KEY, IB host/port)
import trades
try:
    from risk import group_of
except Exception:
    def group_of(s):
        return s

CLIENT_ID = 17


def compute_stats(rows):
    n = len(rows)
    wins = [r for r in rows if r["realized_pnl"] > 0]
    losses = [r for r in rows if r["realized_pnl"] < 0]
    gross_win = sum(r["realized_pnl"] for r in wins)
    gross_loss = -sum(r["realized_pnl"] for r in losses)
    by_sym, by_grp = defaultdict(float), defaultdict(float)
    for r in rows:
        by_sym[r["symbol"]] += r["realized_pnl"]
        by_grp[group_of(r["symbol"])] += r["realized_pnl"]
    return {
        "closed_trades": n,
        "win_rate_pct": round(len(wins) / n * 100, 1) if n else 0,
        "wins": len(wins), "losses": len(losses),
        "total_realized_pnl": round(sum(r["realized_pnl"] for r in rows), 2),
        "avg_win": round(gross_win / len(wins), 2) if wins else 0,
        "avg_loss": round(gross_loss / len(losses), 2) if losses else 0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "pnl_by_symbol": {k: round(v, 2) for k, v in sorted(by_sym.items(), key=lambda x: x[1])},
        "pnl_by_group": {k: round(v, 2) for k, v in sorted(by_grp.items(), key=lambda x: x[1])},
    }


def refresh_ledger():
    try:
        ib = IB()
        ib.connect(config.IB_HOST, config.IB_PORT, clientId=CLIENT_ID)
        try:
            return trades.record(ib)
        finally:
            ib.disconnect()
    except Exception as e:
        print(f"(couldn't refresh from IBKR, reviewing the existing ledger: {e})")
        return 0


def recent_log(n=40):
    files = sorted(glob.glob("logs/run_*.log"))
    if not files:
        return "(no log)"
    return "\n".join(open(files[-1]).read().splitlines()[-n:])


def ask_claude(stats, log_tail):
    client = anthropic.Anthropic()   # ANTHROPIC_API_KEY from env
    prompt = f"""You are a trading-performance analyst reviewing an automated PAPER trading bot.
Be rigorous and humble. The sample is small and dominated by randomness - say that plainly and
quantify the uncertainty where you can. Do NOT recommend aggressive changes. Frame any suggestion
as something for a HUMAN to consider deliberately, and only with much more data. Never imply the
bot should change itself automatically. If the sample is too small to conclude anything, say so
directly rather than inventing patterns.

Realized stats from closed trades:
{json.dumps(stats, indent=2)}

Recent decision-log lines (qualitative context only):
{log_tail}

Write a concise plain-English review: what these numbers do and do not support, any patterns that
might deserve a human's attention later, and at most one or two cautious things to watch - each
explicitly caveated by the small sample. End with a one-line bottom line."""
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=1000,
        messages=[{"role": "user", "content": prompt}])
    return "".join(getattr(b, "text", "") for b in msg.content)


def main():
    added = refresh_ledger()
    rows = trades.load()
    if not rows:
        print("No closed trades in the ledger yet. Once the bot closes some positions "
              "and this has run to capture them, there'll be something to review.")
        return
    stats = compute_stats(rows)
    print(f"Ledger: {len(rows)} closed trades ({added} new this run). "
          f"Win rate {stats['win_rate_pct']}%, total realized {stats['total_realized_pnl']:+.2f}.")
    print("\n" + "=" * 64 + "\n  AI PERFORMANCE REVIEW  (read-only - you decide)\n" + "=" * 64)
    print(ask_claude(stats, recent_log()))
    print("=" * 64)


if __name__ == "__main__":
    main()
