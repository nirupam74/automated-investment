"""Phase 3 - The brain.

Loads snapshot.json, sends it to Claude with a swing-trading system prompt,
and gets back structured trade *proposals* via a forced tool call (guaranteed
schema-conforming JSON, no fragile text parsing).

The brain only PROPOSES. Nothing here places an order. The Phase 4 risk gate
will validate every proposal against hard limits before anything executes.
Set your key first:  export ANTHROPIC_API_KEY=sk-ant-...
"""
import json

from anthropic import Anthropic

import config

SYSTEM_PROMPT = """You are a swing-trading analyst for a single personal US-equities account.
Your time horizon is multi-day to a few weeks.

You are given a market snapshot (account state, open positions, and per-symbol
indicators). Analyze it and propose trades using the propose_trades tool.

How to think:
- Trend: price vs the 50-day (the `trend` field) and distance from SMA20/SMA50.
- Momentum: RSI (overbought >70, oversold <30) and the MACD histogram (sign and direction).
- Volatility: use ATR to place stops. For a long, a reasonable stop is roughly
  entry minus 1.5x ATR; size the position so the loss to that stop is about
  the account's risk-per-trade budget.
- Context: the 20-day high/low (breakout/breakdown) and volume vs its average
  (is the move backed by participation?).

Rules:
- Only consider symbols present in the snapshot.
- Account for OPEN POSITIONS: you may propose trimming, adding, or closing them.
- Be selective. If setups are weak, it is correct to propose few or no new
  trades and mark others HOLD. Do not force trades.
- Every proposal must have a rationale that cites specific numbers from the snapshot.
- You only propose. A separate deterministic risk system validates and executes,
  so never assume your numbers are final.

Respond directly, with no preamble."""

PROPOSE_TRADES_TOOL = {
    "name": "propose_trades",
    "description": "Submit the market read and a ranked list of swing-trade proposals.",
    "input_schema": {
        "type": "object",
        "properties": {
            "market_read": {
                "type": "string",
                "description": "2-3 sentence overall read of the watchlist right now.",
            },
            "proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "action": {"type": "string", "enum": ["BUY", "SELL", "CLOSE", "HOLD"]},
                        "conviction": {"type": "integer", "minimum": 1, "maximum": 5},
                        "entry": {"type": ["number", "null"]},
                        "stop": {"type": ["number", "null"]},
                        "target": {"type": ["number", "null"]},
                        "size_shares": {"type": ["integer", "null"]},
                        "rationale": {"type": "string"},
                    },
                    "required": ["symbol", "action", "conviction", "rationale"],
                },
            },
        },
        "required": ["market_read", "proposals"],
    },
}


def extract_tool_input(response):
    """Pull the propose_trades input out of the response content blocks."""
    for block in response.content:
        if block.type == "tool_use" and block.name == "propose_trades":
            return block.input
    raise RuntimeError("Model did not return a propose_trades tool call.")


def get_proposals(snapshot: dict) -> dict:
    client = Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    user_msg = (
        "Here is the current market snapshot. Risk budget per trade is "
        f"{config.RISK_PER_TRADE_PCT}% of the account.\n\n"
        f"{json.dumps(snapshot, indent=2)}"
    )

    response = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=config.CLAUDE_MAX_TOKENS,
        system=SYSTEM_PROMPT,
        tools=[PROPOSE_TRADES_TOOL],
        tool_choice={"type": "tool", "name": "propose_trades"},
        messages=[{"role": "user", "content": user_msg}],
    )
    return extract_tool_input(response)


if __name__ == "__main__":
    with open("snapshot.json") as f:
        snapshot = json.load(f)

    result = get_proposals(snapshot)

    with open("proposals.json", "w") as f:
        json.dump(result, f, indent=2)

    print("MARKET READ:", result["market_read"], "\n")
    print(f"{'SYM':6} {'ACTION':6} {'CONV':4} {'ENTRY':>8} {'STOP':>8} {'TARGET':>8}  RATIONALE")
    print("-" * 100)
    for p in result["proposals"]:
        print(f"{p['symbol']:6} {p['action']:6} {str(p.get('conviction','')):4} "
              f"{str(p.get('entry','')):>8} {str(p.get('stop','')):>8} {str(p.get('target','')):>8}  "
              f"{p['rationale'][:60]}")
    print("\nFull detail written to proposals.json")
