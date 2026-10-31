"""Phase 5 - Execution.

Reads approved_orders.json and places orders on IBKR. New entries go in as
BRACKET orders: the entry limit, a protective stop, and a take-profit target
are submitted together, so a position is never live without its stop already
working in the market. Closes go in as market sells.

SAFETY: DRY_RUN defaults to True. In dry-run it prints exactly what it would
place and connects to nothing. Set config.DRY_RUN = False to actually transmit
to your PAPER account.
"""
import json

import config


def plan_order(od: dict) -> dict:
    """Turn an approved order into a concrete order plan (fills in a target if missing)."""
    if od["action"] == "BUY":
        entry, stop, target = od.get("entry"), od.get("stop"), od.get("target")
        if target is None and entry is not None and stop is not None:
            target = round(entry + config.DEFAULT_RR * (entry - stop), 2)
        return {"symbol": od["symbol"], "action": "BUY", "qty": od["shares"],
                "entry": entry, "stop": stop, "target": target}
    # SELL / CLOSE -> exit at market
    return {"symbol": od["symbol"], "action": "SELL", "qty": od["shares"],
            "entry": None, "stop": None, "target": None}


def print_plan(plans):
    print(f"{'SYM':6} {'ACTION':6} {'QTY':>5} {'ENTRY':>9} {'STOP':>9} {'TARGET':>9}  TYPE")
    print("-" * 80)
    for p in plans:
        kind = "bracket (entry+stop+target)" if p["action"] == "BUY" else "market exit"
        print(f"{p['symbol']:6} {p['action']:6} {p['qty']:>5} "
              f"{str(p['entry']):>9} {str(p['stop']):>9} {str(p['target']):>9}  {kind}")


def execute(plans):
    from ib_async import IB, Stock, MarketOrder  # imported only when actually placing

    ib = IB()
    ib.connect(config.IB_HOST, config.IB_PORT, clientId=config.IB_CLIENT_ID)
    results = []
    for p in plans:
        contract = Stock(p["symbol"], "SMART", "USD")
        ib.qualifyContracts(contract)

        if p["action"] == "BUY":
            bracket = ib.bracketOrder(
                "BUY", p["qty"],
                limitPrice=p["entry"],
                takeProfitPrice=p["target"],
                stopLossPrice=p["stop"],
            )
            for o in bracket:
                o.tif = config.ORDER_TIF
            for o in bracket:
                ib.placeOrder(contract, o)
            results.append((p["symbol"], "bracket placed"))
        else:
            ib.placeOrder(contract, MarketOrder("SELL", p["qty"]))
            results.append((p["symbol"], "market sell placed"))

    ib.sleep(2)
    ib.disconnect()
    return results


if __name__ == "__main__":
    with open("approved_orders.json") as f:
        orders = json.load(f)

    if not orders:
        print("No approved orders to execute. Nothing to do.")
        raise SystemExit

    plans = [plan_order(o) for o in orders]

    print(f"{'DRY RUN' if config.DRY_RUN else 'LIVE (paper) EXECUTION'} - {len(plans)} order(s)\n")
    print_plan(plans)

    if config.DRY_RUN:
        print("\nDRY_RUN is on. Nothing was transmitted. "
              "Set config.DRY_RUN = False to place these on paper.")
    else:
        print()
        for sym, status in execute(plans):
            print(f"  {sym}: {status}")
        print("\nOrders transmitted to paper. Check IB Gateway / your account to confirm.")
