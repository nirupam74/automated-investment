"""Phase 4 - The risk gate.

Deterministic. No AI. Takes the brain's proposals plus current account state
and decides which, if any, are allowed to become real orders. Risk-reducing
actions (closing/selling) are always permitted; new entries must clear every
check. Closing actions are processed first so they can't be crowded out by the
per-run trade cap.

Output: approved_orders.json (the vetted order list) + a printed decision log.
This file places NO orders. Execution is Phase 5, and it will only ever act on
approved_orders.json.

Daily-loss circuit breaker: at the start of each ET trading day the account's
equity is recorded as a baseline (daily_baseline.json). On every run, if equity
has fallen MAX_DAILY_LOSS_PCT or more below that baseline, ALL new entries are
blocked for the rest of the day. Risk-reducing exits are never affected. Resets
automatically on the next trading day.

FX-aware sizing: the account base is CAD but trades are USD. We convert net
liquidation to USD using the snapshot's USD.CAD rate before sizing, so risk
percentages and the position cap are computed in the same currency as prices.
"""
import json
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import config

ET = ZoneInfo("America/New_York")
BASELINE_FILE = "daily_baseline.json"

# Work off only this much of the account (base currency, CAD), not the full
# balance. Positions are sized as if the account held this amount; the rest is
# left untouched. Set to None to use the whole account. Overridable in config.
TRADING_CAPITAL_CAP = getattr(config, "TRADING_CAPITAL_CAP", 500_000)

# --- Volatility-based sizing ---
# The stop is never tighter than ATR_STOP_MULT x the symbol's ATR, so a more
# volatile name gets a wider stop and therefore a smaller position (dollar risk
# stays constant). Calmer names get a tighter stop and a larger position.
ATR_STOP_MULT = getattr(config, "ATR_STOP_MULT", 2.0)

# --- Correlation filter ---
# Cap how many names from the same correlated group can be held/added at once,
# so the bot can't quietly turn into one giant sector bet (e.g. six semis = one
# trade). Symbols not listed are their own group, so they're never capped.
_GROUP_MEMBERS = {
    "semis": ["NVDA", "AMD", "AVGO", "QCOM", "AMAT", "MU", "INTC", "TXN"],
    "bigtech": ["AAPL", "MSFT", "GOOGL", "META", "AMZN", "ORCL", "ADBE", "CRM", "NOW", "CSCO", "NFLX"],
    "market": ["SPY", "QQQ"],
    "financials": ["JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP"],
    "health": ["UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE"],
    "consumer": ["WMT", "COST", "HD", "MCD", "NKE", "KO", "PEP", "PG"],
    "energy": ["XOM", "CVX", "USO"],
    "industrial": ["CAT", "BA", "GE"],
    "gold": ["GLD"],
    "crypto": ["IBIT"],
    "comm": ["DIS", "VZ", "T"],
}
GROUPS = {sym: g for g, syms in _GROUP_MEMBERS.items() for sym in syms}
MAX_PER_GROUP = getattr(config, "MAX_PER_GROUP", 2)


def group_of(sym):
    return GROUPS.get(sym, sym)


def load_inputs():
    with open("snapshot.json") as f:
        snapshot = json.load(f)
    with open("proposals.json") as f:
        proposals = json.load(f)
    return snapshot, proposals


def build_lookups(snapshot):
    prices, positions, atrs = {}, {}, {}
    for s in snapshot.get("symbols", []):
        if "last_price" in s and s["last_price"] is not None:
            prices[s["symbol"]] = s["last_price"]
        if s.get("atr14") is not None:
            atrs[s["symbol"]] = s["atr14"]
    for p in snapshot.get("positions", []):
        positions[p["symbol"]] = p.get("position", 0) or 0
    nlv = snapshot.get("account", {}).get("NetLiquidation", {})
    net_liq = nlv.get("value")
    currency = nlv.get("currency")
    fx = snapshot.get("fx", {}).get("usd_cad")
    return prices, positions, atrs, net_liq, currency, fx


def usd_equity(net_liq, currency, fx):
    """Net liquidation expressed in USD, so sizing matches USD prices. If the
    base is already USD, or no rate is available, returns net_liq unchanged."""
    if net_liq is None:
        return None
    if currency and currency != "USD" and fx:
        return net_liq / fx
    return net_liq


def daily_loss_status(net_liq):
    """Track start-of-day equity and report intraday drawdown vs the daily limit.
    Returns (tripped, dd_pct, baseline). Currency-agnostic (a ratio), so it runs
    on the base-currency net_liq. Only new entries are ever affected."""
    limit = getattr(config, "MAX_DAILY_LOSS_PCT", None)
    today = datetime.now(ET).strftime("%Y-%m-%d")
    base = None
    try:
        with open(BASELINE_FILE) as f:
            data = json.load(f)
        if data.get("date") == today:
            base = data.get("start_equity")
    except Exception:
        pass
    if base is None:
        base = net_liq
        try:
            with open(BASELINE_FILE, "w") as f:
                json.dump({"date": today, "start_equity": net_liq}, f)
        except Exception:
            pass
    dd_pct = (net_liq - base) / base * 100 if base else 0.0
    tripped = limit is not None and dd_pct <= -abs(limit)
    return tripped, dd_pct, base


def evaluate(snapshot, proposals):
    prices, positions, atrs, net_liq, currency, fx = build_lookups(snapshot)
    # The allow-list is this cycle's screened universe: a buy is only valid for
    # a symbol that was actually snapshotted (guards against a hallucinated
    # ticker from the brain). Screened dynamically, this replaces the old static
    # config.WATCHLIST check.
    universe = {s.get("symbol") for s in snapshot.get("symbols", [])}
    decisions = []   # every proposal, with verdict + reason
    approved = []     # the orders that passed

    def decide(symbol, action, shares, status, reason, **extra):
        row = {"symbol": symbol, "action": action, "shares": shares,
               "status": status, "reason": reason, **extra}
        decisions.append(row)
        if status == "APPROVED":
            approved.append({"symbol": symbol, "action": action, "shares": shares, **extra})

    if config.KILL_SWITCH:
        for p in proposals.get("proposals", []):
            decide(p["symbol"], p["action"], 0, "BLOCKED", "kill switch is ON")
        return decisions, approved

    if net_liq is None:
        raise RuntimeError("No net liquidation value in snapshot; cannot size trades.")

    # Size off only the capped working capital (base currency), then convert to
    # USD. The daily-loss breaker below still watches the real account balance.
    working_capital = min(net_liq, TRADING_CAPITAL_CAP) if TRADING_CAPITAL_CAP else net_liq
    sizing_equity = usd_equity(working_capital, currency, fx)

    # Daily-loss circuit breaker (uses base-currency net_liq; it's a ratio).
    breaker_tripped, dd_pct, day_base = daily_loss_status(net_liq)

    # Closes/sells first (risk-reducing), then buys by conviction (highest first).
    props = proposals.get("proposals", [])
    closes = [p for p in props if p["action"] in ("CLOSE", "SELL")]
    buys = sorted([p for p in props if p["action"] == "BUY"],
                  key=lambda p: p.get("conviction", 0), reverse=True)
    holds = [p for p in props if p["action"] == "HOLD"]

    # --- Closes / sells: always allowed if a position exists ---
    for p in closes:
        sym = p["symbol"]
        pos = positions.get(sym, 0)
        if pos <= 0:
            decide(sym, p["action"], 0, "REJECTED", "no open long position to close")
        else:
            decide(sym, "SELL", int(abs(pos)), "APPROVED", "closing existing position")

    # --- New entries: must clear every check ---
    open_count = sum(1 for q in positions.values() if q != 0)
    # how many names per correlated group we already hold (for the correlation cap)
    group_count = {}
    for s, q in positions.items():
        if q != 0:
            g = group_of(s)
            group_count[g] = group_count.get(g, 0) + 1
    trades_used = 0
    for p in buys:
        sym = p["symbol"]
        if breaker_tripped:
            decide(sym, "BUY", 0, "BLOCKED",
                   f"daily loss limit hit (down {dd_pct:.2f}%, limit {abs(config.MAX_DAILY_LOSS_PCT)}%)")
            continue
        if not config.ALLOW_NEW_ENTRIES:
            decide(sym, "BUY", 0, "BLOCKED", "new entries halted (ALLOW_NEW_ENTRIES=False)")
            continue
        if sym not in universe:
            decide(sym, "BUY", 0, "REJECTED", "symbol not in this cycle's screened set")
            continue
        if trades_used >= config.MAX_TRADES_PER_RUN:
            decide(sym, "BUY", 0, "REJECTED", "per-run trade cap reached")
            continue
        if sym not in positions and open_count >= config.MAX_OPEN_POSITIONS:
            decide(sym, "BUY", 0, "REJECTED", "max open positions reached")
            continue
        g = group_of(sym)
        if sym not in positions and group_count.get(g, 0) >= MAX_PER_GROUP:
            decide(sym, "BUY", 0, "REJECTED",
                   f"correlation cap: already {MAX_PER_GROUP} in {g}")
            continue
        if p.get("conviction", 0) < config.MIN_CONVICTION_TO_ENTER:
            decide(sym, "BUY", 0, "REJECTED",
                   f"conviction {p.get('conviction')} < {config.MIN_CONVICTION_TO_ENTER}")
            continue

        last = prices.get(sym)
        entry = p.get("entry") or last
        stop = p.get("stop")
        if entry is None:
            decide(sym, "BUY", 0, "REJECTED", "no price available")
            continue
        if config.REQUIRE_STOP_FOR_BUY and stop is None:
            decide(sym, "BUY", 0, "REJECTED", "no stop provided")
            continue
        if last and abs(entry - last) / last * 100 > config.MAX_ENTRY_SLIPPAGE_PCT:
            decide(sym, "BUY", 0, "REJECTED",
                   f"entry {entry} >{config.MAX_ENTRY_SLIPPAGE_PCT}% from last {last}")
            continue

        # Volatility floor on the stop: use the wider of the brain's stop and
        # ATR_STOP_MULT x ATR. A more volatile name gets a wider stop and thus a
        # smaller position (dollar risk held constant). The stop we place matches
        # what we size off, so there's no mismatch.
        atr = atrs.get(sym)
        brain_dist = entry - stop
        atr_dist = ATR_STOP_MULT * atr if atr else 0.0
        risk_per_share = max(brain_dist, atr_dist)
        if risk_per_share <= 0:
            decide(sym, "BUY", 0, "REJECTED", "stop is not below entry")
            continue
        eff_stop = round(entry - risk_per_share, 2)

        dollar_risk = sizing_equity * config.RISK_PER_TRADE_PCT / 100
        shares = int(dollar_risk / risk_per_share)
        max_by_size = int(sizing_equity * config.MAX_POSITION_PCT / 100 / entry)
        shares = min(shares, max_by_size)
        if shares <= 0:
            decide(sym, "BUY", 0, "REJECTED", "size rounds to zero")
            continue

        vol_note = " (ATR-widened)" if atr_dist > brain_dist else ""
        decide(sym, "BUY", shares, "APPROVED",
               f"risk {config.RISK_PER_TRADE_PCT}% -> {shares} sh, stop {eff_stop}{vol_note}",
               entry=entry, stop=eff_stop, target=p.get("target"))
        trades_used += 1
        if sym not in positions:
            group_count[g] = group_count.get(g, 0) + 1
            open_count += 1

    for p in holds:
        decide(p["symbol"], "HOLD", 0, "SKIPPED", "no action proposed")

    return decisions, approved


if __name__ == "__main__":
    snapshot, proposals = load_inputs()
    decisions, approved = evaluate(snapshot, proposals)

    with open("approved_orders.json", "w") as f:
        json.dump(approved, f, indent=2)

    print(f"{'SYM':6} {'ACTION':6} {'SHARES':>6}  {'STATUS':9} REASON")
    print("-" * 90)
    for d in decisions:
        print(f"{d['symbol']:6} {d['action']:6} {str(d['shares']):>6}  "
              f"{d['status']:9} {d['reason']}")
    print(f"\n{len(approved)} order(s) approved -> approved_orders.json")
