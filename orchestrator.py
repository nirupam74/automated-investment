"""Phase 6 - Orchestrator (with live controls + state publishing).

Runs the full cycle (snapshot -> brain -> risk -> execution) every
config.CYCLE_MINUTES during US market hours. Every few seconds it re-reads
controls.json (written by the dashboard), so the STOP button and the arm/safe
toggle take effect almost immediately. After each cycle and on every poll it
writes state.json for the dashboard to display. A failed cycle is logged and
skipped, never crashes the loop. Ctrl+C to stop.

  python orchestrator.py          # scheduled loop
  python orchestrator.py --once   # one cycle now, ignoring market hours

Market hours are taken from the NYSE calendar (pandas_market_calendars), so US
holidays and half-day early closes are handled automatically. If that library
isn't available, it falls back to a plain weekday 9:30-16:00 ET check.
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import config

ET = ZoneInfo("America/New_York")
POLL_SECONDS = 5
STATE_FILE = "state.json"
CONTROLS_FILE = "controls.json"
WATCHLIST_FILE = "watchlist.json"
SCREEN_REFRESH_HOURS = 5   # re-run the S&P screener at most ~twice a session
FLATTEN_FILE = "flatten_request.json"   # dashboard SELL ALL drops this

try:
    import pandas_market_calendars as mcal
except Exception:
    mcal = None
_NYSE = None
_sched_cache = {"date": None, "open": None, "close": None, "ok": False}

_last = {"account": {}, "positions": [], "market_read": "", "decisions": [], "last_cycle_at": None}


def setup_logging():
    os.makedirs("logs", exist_ok=True)
    log = logging.getLogger("orchestrator")
    log.setLevel(logging.INFO)
    if log.handlers:
        return log
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(f"logs/run_{datetime.now(ET):%Y%m%d}.log")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(ch)
    return log


def market_is_open(now_et: datetime) -> bool:
    """True only when the NYSE is actually trading. Uses the exchange calendar
    (holidays + early closes) when available, with a plain-hours fallback."""
    if now_et.weekday() >= 5:
        return False
    if mcal is None:
        open_t = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        close_t = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        return open_t <= now_et <= close_t
    today = now_et.strftime("%Y-%m-%d")
    if _sched_cache["date"] != today:
        o = c = None
        ok = False
        try:
            global _NYSE
            if _NYSE is None:
                _NYSE = mcal.get_calendar("XNYS")
            sched = _NYSE.schedule(start_date=today, end_date=today)
            if not sched.empty:
                o = sched.iloc[0]["market_open"].to_pydatetime()
                c = sched.iloc[0]["market_close"].to_pydatetime()
            ok = True
        except Exception:
            ok = False
        _sched_cache.update(date=today, open=o, close=c, ok=ok)
    if not _sched_cache["ok"]:
        open_t = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        close_t = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        return open_t <= now_et <= close_t
    o, c = _sched_cache["open"], _sched_cache["close"]
    if o is None:        # holiday: closed all day
        return False
    return o <= now_et <= c


def read_controls():
    try:
        with open(CONTROLS_FILE) as f:
            c = json.load(f)
            return {"kill_switch": bool(c.get("kill_switch", config.KILL_SWITCH)),
                    "dry_run": bool(c.get("dry_run", config.DRY_RUN))}
    except Exception:
        c = {"kill_switch": config.KILL_SWITCH, "dry_run": config.DRY_RUN}
        with open(CONTROLS_FILE, "w") as f:
            json.dump(c, f, indent=2)
        return c


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_state(market_open: bool):
    state = {**_last, "market_open": market_open, "heartbeat_at": utcnow()}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def load_last_state():
    """Carry forward the last cycle's data across restarts so the panel
    isn't blank when the engine starts outside market hours."""
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        for k in _last:
            if k in s:
                _last[k] = s[k]
    except Exception:
        pass


def positions_view(snap):
    prices = {s["symbol"]: s.get("last_price") for s in snap.get("symbols", [])}
    out = []
    for p in snap.get("positions", []):
        sym, qty, avg = p.get("symbol"), p.get("position"), p.get("avgCost")
        last = prices.get(sym)
        upct = round((last - avg) / avg * 100, 2) if (last and avg) else None
        out.append({"symbol": sym, "position": qty, "avgCost": avg,
                    "last_price": last, "unrealized_pct": upct})
    return out


def dump_audit(snap, proposals, approved):
    for name, obj in (("snapshot.json", snap), ("proposals.json", proposals),
                      ("approved_orders.json", approved)):
        with open(name, "w") as f:
            json.dump(obj, f, indent=2)


def maybe_refresh_watchlist(log):
    """Run one rolling-sweep step of the screener every cycle: it scores a small
    slice of the full S&P 500 (plus the ETFs and current holdings), updates the
    leaderboard, and rebuilds the shortlist from the whole board. Each step is
    light enough to stay inside IBKR pacing, and the cursor sweeps the entire
    index about once a day. A failure is logged and ignored so the engine keeps
    the previous watchlist rather than going blind."""
    try:
        import screener
        from ib_async import IB
        ib = IB()
        ib.connect(config.IB_HOST, config.IB_PORT, clientId=screener.SCREENER_CLIENT_ID)
        ib.reqMarketDataType(config.MARKET_DATA_TYPE)
        try:
            shortlist, _ = screener.run_screener(ib)
        finally:
            ib.disconnect()
        if shortlist:
            with open(WATCHLIST_FILE, "w") as f:
                json.dump(shortlist, f, indent=2)
            log.info(f"watchlist refreshed ({len(shortlist)}): {', '.join(shortlist)}")
    except Exception as e:
        log.warning(f"watchlist sweep skipped, keeping previous list: {e}")


def record_trades(log):
    """Append any newly-closed trades to the ledger (broker ground truth) so the
    review tool has trustworthy numbers. Exception-safe; never disrupts a cycle."""
    try:
        import trades
        from ib_async import IB
        ib = IB()
        ib.connect(config.IB_HOST, config.IB_PORT, clientId=18)
        try:
            n = trades.record(ib)
        finally:
            ib.disconnect()
        if n:
            log.info(f"ledger: recorded {n} closed trade(s)")
    except Exception as e:
        log.warning(f"trade ledger update skipped: {e}")


def run_cycle(log, effective_dry_run: bool, market_open: bool):
    from snapshot import build_snapshot
    from brain import get_proposals
    from risk import evaluate
    from executor import plan_order, execute

    log.info("=== cycle start ===")
    maybe_refresh_watchlist(log)
    snap = build_snapshot()
    acct = snap.get("account", {})
    nlv = acct.get("NetLiquidation", {})
    cash = acct.get("TotalCashValue", {})
    log.info(f"snapshot: net_liq={nlv.get('value')} {nlv.get('currency')}, "
             f"positions={len(snap.get('positions', []))}")
    errs = [s["symbol"] for s in snap.get("symbols", []) if "error" in s]
    if errs:
        log.warning(f"data errors for {errs} - those symbols sit out this cycle")

    proposals = get_proposals(snap)
    log.info(f"market read: {proposals.get('market_read', '')}")
    decisions, approved = evaluate(snap, proposals)
    for d in decisions:
        log.info(f"  {d['symbol']:6} {d['action']:5} {d['status']:9} {d['reason']}")

    dump_audit(snap, proposals, approved)

    # publish to the dashboard
    _last["account"] = {"net_liq": nlv.get("value"), "cash": cash.get("value"),
                        "currency": nlv.get("currency")}
    _last["positions"] = positions_view(snap)
    _last["market_read"] = proposals.get("market_read", "")
    _last["decisions"] = decisions
    _last["last_cycle_at"] = utcnow()

    if approved:
        plans = [plan_order(o) for o in approved]
        if effective_dry_run:
            for p in plans:
                log.info(f"  PLAN (not sent) {p['symbol']} {p['action']} {p['qty']} "
                         f"entry={p['entry']} stop={p['stop']} target={p['target']}")
        else:
            time.sleep(2)
            for sym, status in execute(plans):
                log.info(f"  EXECUTED {sym}: {status}")
    else:
        log.info("no approved orders this cycle")

    write_state(market_open)
    record_trades(log)
    log.info("=== cycle end ===")


def handle_flatten_request(log):
    """If the dashboard dropped a SELL ALL request, execute it: cancel working
    orders and market-close every position, then remove the request. Closing is
    risk-reducing, so this runs regardless of arm/safe state. The request file is
    consumed first so a mid-flatten crash can't re-trigger it in a loop."""
    if not os.path.exists(FLATTEN_FILE):
        return
    try:
        os.remove(FLATTEN_FILE)
    except OSError:
        pass
    log.info("SELL ALL requested from dashboard - flattening now")
    try:
        import flatten
        from ib_async import IB
        ib = IB()
        ib.connect(config.IB_HOST, config.IB_PORT, clientId=flatten.CLIENT_ID)
        try:
            results, swept = flatten.flatten_all(ib)
        finally:
            ib.disconnect()
        if results:
            for sym, status in results:
                log.info(f"  flattened {sym}: {status}")
        else:
            log.info("  nothing to flatten - already flat")
        if swept:
            log.info(f"  swept {swept} leftover order(s)")
    except Exception as e:
        log.exception(f"flatten request failed: {e}")


def main():
    log = setup_logging()
    load_last_state()
    c = read_controls()
    log.info(f"Orchestrator started. dry_run={c['dry_run']}, kill_switch={c['kill_switch']}, "
             f"cycle={config.CYCLE_MINUTES}min")

    if "--once" in sys.argv:
        log.info("--once: running a single cycle now, ignoring market hours.")
        try:
            run_cycle(log, effective_dry_run=c["dry_run"],
                      market_open=market_is_open(datetime.now(ET)))
        except Exception as e:
            log.exception(f"cycle failed: {e}")
        return

    last_cycle_ts = 0.0
    prev_status = None
    try:
        while True:
            c = read_controls()
            handle_flatten_request(log)
            now = datetime.now(ET)
            open_now = market_is_open(now)

            if c["kill_switch"]:
                status = "HALTED (kill switch on)"
            elif not open_now:
                status = f"market closed ({now:%a %H:%M} ET)"
            else:
                status = "armed-and-trading" if not c["dry_run"] else "safe-mode (analyzing only)"

            if status != prev_status:
                log.info(status)
                prev_status = status

            if not c["kill_switch"] and open_now and \
               (time.time() - last_cycle_ts >= config.CYCLE_MINUTES * 60):
                try:
                    run_cycle(log, effective_dry_run=c["dry_run"], market_open=open_now)
                except Exception as e:
                    log.exception(f"cycle failed (loop continues): {e}")
                last_cycle_ts = time.time()
            else:
                write_state(open_now)

            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        log.info("Stopped by user (Ctrl+C).")


if __name__ == "__main__":
    main()
