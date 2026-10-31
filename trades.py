"""Persistent trade ledger, built from IBKR's own execution records.

record(ib) pulls the account's executions and appends any new *closing* fills
(the ones that carry a realized P&L) to trades.jsonl, deduped by execution id.
This is broker ground-truth, not the engine's own logs, so the performance
numbers can be trusted. load() reads them back.
"""
import json
import os

LEDGER = "trades.jsonl"
_SENTINEL = 1e15   # IBKR uses a huge double for "realized P&L not applicable"


def _seen_ids():
    ids = set()
    if os.path.exists(LEDGER):
        with open(LEDGER) as f:
            for line in f:
                try:
                    ids.add(json.loads(line)["exec_id"])
                except Exception:
                    pass
    return ids


def record(ib):
    """Append new realized (closing) fills to the ledger. Returns count added."""
    try:
        fills = ib.reqExecutions()
    except Exception:
        fills = ib.fills()
    seen = _seen_ids()
    added = 0
    with open(LEDGER, "a") as f:
        for fl in fills:
            ex = getattr(fl, "execution", None)
            cr = getattr(fl, "commissionReport", None)
            if ex is None:
                continue
            pnl = getattr(cr, "realizedPNL", None) if cr else None
            try:
                pnl = float(pnl)
            except (TypeError, ValueError):
                continue
            if abs(pnl) >= _SENTINEL:      # opening fills carry the sentinel, skip them
                continue
            if ex.execId in seen:
                continue
            rec = {
                "exec_id": ex.execId,
                "time": str(ex.time),
                "symbol": getattr(fl.contract, "symbol", "?"),
                "side": ex.side,
                "shares": float(ex.shares),
                "price": float(ex.price),
                "realized_pnl": pnl,
            }
            f.write(json.dumps(rec) + "\n")
            seen.add(ex.execId)
            added += 1
    return added


def load():
    rows = []
    if os.path.exists(LEDGER):
        with open(LEDGER) as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows
