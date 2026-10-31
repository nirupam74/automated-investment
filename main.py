"""
main.py — the live (paper) loop.

It runs ONE strategy (set config.STRATEGY to "sma" or "claude") across your
symbols, through the same risk gate and the same execution path, and logs every
cycle and decision to CSV so results can be compared (see report.py). That
sameness is the point: swap the strategy, hold everything else constant.

Cycle per symbol:
  1. Daily loss kill-switch check.
  2. Pull recent closes (+ news, if Claude strategy and USE_SENTIMENT).
  3. Strategy decides buy / sell / hold.
  4. Risk gate vets it.
  5. Optional human approval.
  6. Execute via Alpaca (paper unless LIVE_TRADING=true): buy by notional,
     sell flattens the whole position.
  7. Log the decision and the equity.

Run:  python main.py     Stop: Ctrl-C
"""

import sys
import time
from dotenv import load_dotenv

load_dotenv()

import config
from broker import Broker, norm
from risk import RiskGate
from strategy import SmaCrossStrategy, ClaudeStrategy
from logger import Logger
import sentiment


def build_strategy():
    if config.STRATEGY == "claude":
        return ClaudeStrategy(config.CLAUDE_MODEL)
    return SmaCrossStrategy(config.SMA_SHORT, config.SMA_LONG)


def run_cycle(broker, strat, gate, uses_claude, log):
    if gate.daily_loss_breached():
        return False

    equity = broker.equity()
    positions = broker.positions()
    log.equity(equity, len(positions))
    print(f"\n[cycle] strategy={config.STRATEGY} equity=${equity:,.2f} "
          f"positions={positions or 'none'}")

    for symbol in config.SYMBOLS:
        closes = broker.recent_closes(symbol, config.BARS_LOOKBACK)
        if not closes:
            print(f"  - {symbol}: no data")
            continue
        price = closes[-1]
        holding = norm(symbol) in positions

        news = None
        if uses_claude and config.USE_SENTIMENT:
            news = sentiment.summarize_for_prompt(symbol, sentiment.get_headlines(symbol))

        action = strat.decide(closes, holding, symbol=symbol, sentiment=news)
        if action == "hold":
            print(f"  - {symbol}: hold")
            log.decision(symbol, holding, "hold", "hold", price=price)
            continue

        notional = config.MAX_NOTIONAL_PER_TRADE if action == "buy" else 0.0
        ok, reason = gate.check(symbol, action, notional)
        if not ok:
            print(f"  - skip {symbol}: {reason}")
            log.decision(symbol, holding, action, "skip", reason, price)
            continue

        size = f"${notional:.2f}" if action == "buy" else "(flatten)"
        print(f"  > PROPOSE {action.upper()} {size} {symbol}")
        if config.REQUIRE_HUMAN_APPROVAL:
            if input("    approve? [y/N] ").strip().lower() != "y":
                print("    declined.")
                log.decision(symbol, holding, action, "declined", price=price)
                continue

        try:
            if action == "buy":
                broker.market_order(symbol, notional, "buy")
            else:
                broker.close_position(symbol)
            print("    executed.")
            log.decision(symbol, holding, action, "executed", price=price)
        except Exception as e:
            print(f"    order failed: {e}")
            log.decision(symbol, holding, action, "failed", str(e), price)

    return True


def main():
    # Approval prompts need a keyboard. If we're detached (e.g. Docker), refuse
    # to start rather than hang or silently do nothing.
    if config.REQUIRE_HUMAN_APPROVAL and not sys.stdin.isatty():
        print("REQUIRE_HUMAN_APPROVAL is True but there is no interactive terminal "
              "(running detached?). Set REQUIRE_HUMAN_APPROVAL=False in config.py to "
              "run unattended, or run in an interactive shell. Exiting.")
        return

    broker = Broker()
    mode = "LIVE (real money)" if broker.live else "PAPER (simulated)"
    print(f"=== Trading bot starting | {mode} | strategy={config.STRATEGY} ===")
    if broker.live:
        if input("This will trade REAL money. Type 'I UNDERSTAND' to continue: ") != "I UNDERSTAND":
            print("Aborted.")
            return

    strat = build_strategy()
    uses_claude = config.STRATEGY == "claude"
    gate = RiskGate(broker)
    log = Logger(config.STRATEGY)

    try:
        while True:
            if not run_cycle(broker, strat, gate, uses_claude, log):
                break
            print(f"[sleep] {config.LOOP_INTERVAL_SECONDS}s until next cycle")
            time.sleep(config.LOOP_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped by user.")


if __name__ == "__main__":
    main()
