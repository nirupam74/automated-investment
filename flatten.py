"""Manual flatten tool - close an open position cleanly.

It cancels any working orders for the symbol first (including bracket legs the
ENGINE placed on a different connection), then sends a market order to close,
using the DAY time-in-force IBKR's presets require. It reports the fill and
self-checks: if any order is still working afterward, it warns you.

    python flatten.py AAPL     # close the AAPL position
    python flatten.py ALL      # close every open position
    python flatten.py          # just list positions, change nothing

Safe by default: with no argument it only lists. It never opens a new position.
Stop the trading service first so it isn't competing for the API:
    systemctl stop trading

If it ever reports leftover orders, this clears every resting order account-wide
(safe only when you're flat):
    python -c "from ib_async import IB; ib=IB(); ib.connect('127.0.0.1',4002,clientId=16); ib.reqGlobalCancel(); ib.sleep(2); ib.disconnect()"
"""
import sys

from ib_async import IB, MarketOrder

import config

CLIENT_ID = 15   # distinct from engine (config), screener (12), check_orders (9)
DONE = ("Filled", "Cancelled", "Inactive", "ApiCancelled")


def open_positions(ib):
    return [p for p in ib.positions() if p.position != 0]


def all_open_trades(ib):
    """Open orders across ALL client connections. The engine places brackets on
    its own clientId, so our connection's openTrades() alone wouldn't see them;
    reqAllOpenOrders() surfaces them first."""
    try:
        ib.reqAllOpenOrders()
        ib.sleep(1.0)
    except Exception:
        pass
    return ib.openTrades()


def working_for(ib, sym):
    return [t for t in all_open_trades(ib)
            if getattr(t.contract, "symbol", None) == sym and t.orderStatus.status not in DONE]


def close_one(ib, pos):
    sym = pos.contract.symbol
    qty = pos.position
    side = "SELL" if qty > 0 else "BUY"     # sell to close a long, buy to cover a short
    amount = abs(qty)

    # 1) cancel any working orders for this symbol (bracket legs included)
    legs = working_for(ib, sym)
    for t in legs:
        ib.cancelOrder(t.order)
    if legs:
        ib.sleep(1.5)

    # 2) market close with DAY tif (preset requirement)
    contract = pos.contract
    contract.exchange = "SMART"
    ib.qualifyContracts(contract)
    order = MarketOrder(side, amount)
    order.tif = "DAY"
    trade = ib.placeOrder(contract, order)

    # 3) wait for the fill
    for _ in range(30):
        ib.sleep(0.5)
        if trade.orderStatus.status in DONE:
            break

    # 4) self-check: nothing should still be working for this symbol
    leftover = working_for(ib, sym)
    warn = f"  !! {len(leftover)} order(s) STILL working - run the global-cancel from the header" if leftover else ""
    print(f"  {sym}: {side} {amount} -> {trade.orderStatus.status} "
          f"(filled {trade.orderStatus.filled}, cancelled {len(legs)} working order(s)){warn}")
    return trade.orderStatus.status


def flatten_all(ib):
    """Cancel working orders and market-close every open position. Returns
    (results, swept) where results is a list of (symbol, status) and swept is the
    count of any leftover orphan orders cancelled. Used by the dashboard SELL ALL
    button (the orchestrator calls this on its next poll)."""
    results = []
    for pos in open_positions(ib):
        status = close_one(ib, pos)
        results.append((pos.contract.symbol, status))
    # final safety sweep: cancel anything still resting (account is now flat)
    leftover = [t for t in all_open_trades(ib) if t.orderStatus.status not in DONE]
    for t in leftover:
        ib.cancelOrder(t.order)
    return results, len(leftover)


def main():
    target = sys.argv[1].upper() if len(sys.argv) > 1 else None

    ib = IB()
    ib.connect(config.IB_HOST, config.IB_PORT, clientId=CLIENT_ID)
    try:
        pos = open_positions(ib)
        if not pos:
            print("Account is already flat - nothing to close.")
            # still surface any orphan resting orders
            orphans = all_open_trades(ib)
            live = [t for t in orphans if t.orderStatus.status not in DONE]
            if live:
                print(f"NOTE: {len(live)} resting order(s) with no position behind them:")
                for t in live:
                    print(f"  {t.contract.symbol} {t.order.action} {t.order.totalQuantity} "
                          f"{t.order.orderType} status={t.orderStatus.status}")
                print("Clear them with the global-cancel one-liner in this file's header.")
            return

        if target is None:
            print("Open positions:")
            for p in pos:
                print(f"  {p.contract.symbol:6} {p.position} @ {p.avgCost:.2f}")
            print("\nTo close: 'python flatten.py SYMBOL'  or  'python flatten.py ALL'")
            return

        to_close = pos if target == "ALL" else [p for p in pos if p.contract.symbol == target]
        if not to_close:
            print(f"No open position in {target}. Currently held: {[p.contract.symbol for p in pos]}")
            return

        print(f"Closing {len(to_close)} position(s)...")
        for p in to_close:
            close_one(ib, p)

        ib.sleep(1.5)
        left = open_positions(ib)
        if target == "ALL":
            print("\nAccount flat." if not left
                  else f"\nStill open: {[p.contract.symbol for p in left]} - re-run if needed.")
        else:
            still = [p for p in left if p.contract.symbol == target]
            print(f"\n{target} closed - flat." if not still
                  else f"\n{target} still shows {still[0].position} - re-run if needed.")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
