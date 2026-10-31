# Claude + Alpaca trading bot (paper-first)

A minimal, honest scaffold for AI-assisted automated trading. Claude proposes
trades; a hard-coded risk layer vets them; Alpaca executes — in **paper mode**
(simulated money) until you deliberately turn that off.

## What each file does
- `config.py` — symbols, risk limits, strategy selector, backtest settings. **Start here.**
- `strategy.py` — `SmaCrossStrategy` (baseline) and `ClaudeStrategy` (price + news), one shared interface.
- `sentiment.py` — pulls recent news from FMP to feed the Claude strategy (optional).
- `backtest.py` — replay the SMA strategy over history, for free, before risking anything.
- `broker.py` — talks to Alpaca (account, prices, orders); handles stocks and crypto.
- `risk.py` — the gate: rejects any proposal that breaks your limits.
- `main.py` — the live (paper) loop; runs whichever strategy `config.STRATEGY` selects.
- `logger.py` — writes every cycle's equity and every decision to `logs/*.csv`.
- `report.py` — reads the logs and prints a per-strategy comparison (return, drawdown, trades).


## The intended workflow
1. **Backtest the baseline** — `python backtest.py`. Does SMA-crossover beat buy-and-hold after costs? Usually not. Good to know for free.
2. **Paper-trade the baseline** — set `STRATEGY = "sma"` in `config.py`, run `python main.py` for a stretch.
3. **Paper-trade Claude + sentiment** — set `STRATEGY = "claude"` (and add an `FMP_API_KEY` for news), run again on the same symbols for a comparable stretch.
4. **Compare with numbers** — `python report.py` prints each strategy's return, drawdown, and trade count from the logs. Only consider live money for something that clearly and repeatedly wins. A fair comparison needs similar time windows — different weeks mean different markets.

## Setup
1. **Get keys.**
   - Anthropic API key: https://console.anthropic.com/
   - Alpaca **paper** keys: sign in at https://app.alpaca.markets/, switch to the
     *Paper Trading* account, then generate keys from the API Keys panel.
2. **Configure.** Copy `.env.example` to `.env` and fill in your keys. Leave
   `LIVE_TRADING=false`.
3. **Install.** `pip install -r requirements.txt`
4. **Run.** `python main.py`  (Ctrl-C to stop.)

With `REQUIRE_HUMAN_APPROVAL=True` (the default), the bot pauses and asks you to
confirm every single order in the terminal. That is the recommended way to start:
you see exactly what Claude wants to do and why, and nothing happens without your `y`.

## The four safety layers (in order)
1. **Paper mode** — no real money moves unless `LIVE_TRADING` is exactly `true`.
2. **Allowed universe** — the bot can only trade symbols you list in `config.py`.
3. **Risk gate** — per-trade cap, position-size cap, max open positions, and a
   daily-loss kill-switch that halts everything after a set drawdown.
4. **Human approval** — optional confirm-before-every-order.

These live in code, not in the prompt, on purpose: a model can be argued out of a
prompt instruction, but it cannot edit `risk.py`.

## Going live (don't rush this)
Only after weeks of paper trading you actually trust: set `LIVE_TRADING=true`,
restrict your Alpaca keys to trading (never enable withdrawals), and consider
keeping `REQUIRE_HUMAN_APPROVAL=True` even then.

## Running it on a cloud box (Docker)
Once you have a strategy that repeatedly wins in paper and want it running without
your laptop on, containerize it. **First set `REQUIRE_HUMAN_APPROVAL = False` in
`config.py`** — a detached container has no keyboard to approve trades (the bot
refuses to start otherwise). Then, with Docker installed on a small VM:

```
docker compose up -d --build     # build and run in the background
docker compose logs -f           # watch it
docker compose down              # stop
```

`restart: unless-stopped` keeps the loop alive across crashes and reboots, and
`logs/` is mounted to the host so your records survive rebuilds. Lock the VM's
firewall down and store `.env` only on the box — it holds keys that move money.

## Honest caveats
This is a learning scaffold, not a money printer. An LLM has no predictive edge on
markets; it can be confidently wrong, and automated systems fail in ways that cost
money fast (bugs, outages, volatile gaps). The well-documented base rate is that
most retail algorithmic trading underperforms simply holding a broad index fund.
This is not financial advice — it's a description of how the pieces fit together so
you can experiment safely with money you can afford to lose.
