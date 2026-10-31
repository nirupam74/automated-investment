"""Central settings for the swing-trading engine."""

# Load secrets from a .env file (API key, dashboard password, etc.) if present.
# Keeps credentials out of code and out of version control.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- IB Gateway connection (paper) ---
IB_HOST = "127.0.0.1"
IB_PORT = 4002          # 4002 = Gateway paper, 4001 = Gateway live
IB_CLIENT_ID = 1

# --- Watchlist: US stocks to analyze ---
WATCHLIST = ["AAPL", "MSFT", "NVDA", "AMD", "TSLA"]

# --- Historical data windows ---
DAILY_DURATION = "1 Y"        # context / trend / daily indicators
DAILY_BAR = "1 day"
INTRADAY_DURATION = "5 D"     # entry-timing detail
INTRADAY_BAR = "15 mins"

# Market data type: 3 = delayed (works without a real-time subscription),
# 1 = live (switch to this once you add the US market-data bundle).
MARKET_DATA_TYPE = 3

# --- Claude (the brain) ---
# Sonnet 4.6 = strong analysis, affordable for a recurring loop ($3/$15 per Mtok).
# Swap to "claude-opus-4-8" for maximum reasoning at higher cost.
CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_MAX_TOKENS = 2000

# How much of your account to risk on a single trade idea (used by the brain
# only as guidance; the Phase 4 risk gate will enforce the real limits).
RISK_PER_TRADE_PCT = 1.0

# --- Risk gate (Phase 4) ---
KILL_SWITCH = False              # True blocks EVERY order, no exceptions
ALLOW_NEW_ENTRIES = True         # set False to permit only closes (manual halt)
MAX_OPEN_POSITIONS = 5           # cap on number of simultaneous positions
MAX_TRADES_PER_RUN = 3           # cap on new entries approved per run
MAX_POSITION_PCT = 20.0          # a single position may not exceed 20% of net liq
MIN_CONVICTION_TO_ENTER = 3      # new BUYs require conviction >= 3 (1-5 scale)
MAX_ENTRY_SLIPPAGE_PCT = 2.0     # reject a BUY whose entry is >2% from last price
REQUIRE_STOP_FOR_BUY = True      # never enter a long without a stop

# --- Execution (Phase 5) ---
DRY_RUN = True          # True = print planned orders but DO NOT transmit.
                        # Flip to False to actually place them on PAPER.
ORDER_TIF = "GTC"       # time-in-force for swing orders (GTC = good till cancelled)
DEFAULT_RR = 2.0        # if the brain gives no target, use this reward:risk multiple

# --- Orchestration (Phase 6) ---
CYCLE_MINUTES = 30      # run the full cycle every N minutes during market hours
