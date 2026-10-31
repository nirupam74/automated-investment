"""Local control panel for the trading engine.

Runs a small web app at http://localhost:5000 that shows the engine's live
state and lets you arm/disarm it and stop everything. It talks to the
orchestrator through two files: it writes your toggles to controls.json (which
the orchestrator reads at the top of every loop), and it reads the orchestrator's
status from state.json. The dashboard itself never touches IBKR or places orders.

Run it in its own terminal:  python dashboard.py
"""
import base64
import glob
import importlib
import json
import os
import time

from urllib.parse import urlparse

from flask import (Flask, Response, abort, jsonify, redirect, render_template_string,
                   request, session, url_for)
from werkzeug.security import check_password_hash

import config  # importing config loads the .env file

app = Flask(__name__)
# No fallback key: a known key lets anyone forge a signed-in session. A random one
# just means signing in again after a restart.
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(32)
app.config.update(SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_HTTPONLY=True)
PASSWORD_HASH = os.environ.get("DASHBOARD_PASSWORD_HASH")
STATE_FILE = "state.json"
CONTROLS_FILE = "controls.json"


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def read_controls():
    return read_json(CONTROLS_FILE,
                     {"kill_switch": config.KILL_SWITCH, "dry_run": config.DRY_RUN})


def write_controls(c):
    with open(CONTROLS_FILE, "w") as f:
        json.dump(c, f, indent=2)


def log_tail(n=50):
    files = sorted(glob.glob("logs/run_*.log"))
    if not files:
        return []
    with open(files[-1]) as f:
        lines = f.readlines()
    return [ln.rstrip() for ln in lines[-n:]]


def read_review():
    try:
        with open("daily_review.txt") as f:
            return f.read()
    except Exception:
        return ""


def bot_config():
    try:
        importlib.reload(config)   # reflect live edits to config.py without a dashboard restart
    except Exception:
        pass
    keys = ["RISK_PER_TRADE_PCT", "MAX_POSITION_PCT", "MAX_DAILY_LOSS_PCT",
            "MAX_OPEN_POSITIONS", "MAX_TRADES_PER_RUN", "CYCLE_MINUTES",
            "MIN_CONVICTION_TO_ENTER"]
    return {k: getattr(config, k, None) for k in keys}


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AIS — Console</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="apple-touch-icon" href="/icon-180.png">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#070A11">
<style>
  :root{
    --void:#070A11; --panel:#0D1320; --panel-2:#121A28; --raise:#172234;
    --line:#1E2A3E; --line-2:#162032;
    --ink:#EAEEF6; --dim:#AEB9CC; --mute:#6C798E;
    --beam:#5BE1FF; --beam-dim:#2C6E80;
    --gain:#2FD08A; --loss:#FF5C6C; --armed:#FFB84D;
    --disp:"Space Grotesk",-apple-system,system-ui,"Segoe UI",sans-serif;
    --mono:"JetBrains Mono",ui-monospace,"SF Mono",Menlo,Consolas,monospace;
    --sans:-apple-system,system-ui,"Segoe UI",Roboto,sans-serif;
  }
  *{box-sizing:border-box}
  html{min-height:100%;background:var(--void) radial-gradient(1100px 600px at 78% -10%, rgba(91,225,255,.06), transparent 66%) fixed}
  body{margin:0;color:var(--ink);font-family:var(--sans);font-size:14px;line-height:1.55;-webkit-font-smoothing:antialiased}
  body::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
    background-image:radial-gradient(circle at 1px 1px, rgba(122,165,210,.045) 1px, transparent 0);
    background-size:36px 36px;-webkit-mask-image:linear-gradient(180deg,#000,transparent 85%);mask-image:linear-gradient(180deg,#000,transparent 85%)}
  a{color:inherit;text-decoration:none}

  .app{display:grid;grid-template-columns:230px 1fr;min-height:100vh}

  /* sidebar */
  .sidebar{border-right:1px solid var(--line);padding:22px 16px;display:flex;flex-direction:column;gap:26px;position:sticky;top:0;height:100vh}
  .logo{font-family:var(--disp);font-weight:700;font-size:15px;letter-spacing:.12em;display:flex;align-items:center;gap:10px}
  .logo .mk{width:10px;height:10px;border-radius:2px;background:var(--beam);box-shadow:0 0 10px var(--beam),0 0 24px rgba(91,225,255,.5);transform:rotate(45deg)}
  .logo small{color:var(--mute);font-weight:500;font-size:8px;letter-spacing:.12em;text-transform:uppercase;margin-top:3px}
  .logo-txt{display:flex;flex-direction:column;line-height:1.05}
  nav{display:flex;flex-direction:column;gap:3px}
  .nav{font-family:var(--disp);font-size:13px;font-weight:500;letter-spacing:.04em;color:var(--dim);
    padding:10px 12px;border-radius:9px;cursor:pointer;display:flex;align-items:center;gap:11px;transition:.14s;border:1px solid transparent}
  .nav .ico{width:7px;height:7px;border-radius:2px;background:var(--mute);transform:rotate(45deg);transition:.14s}
  .nav:hover{background:var(--panel);color:var(--ink)}
  .nav.active{background:var(--panel-2);color:var(--ink);border-color:var(--line)}
  .nav.active .ico{background:var(--beam);box-shadow:0 0 9px var(--beam)}
  .side-foot{margin-top:auto;font-family:var(--disp);font-size:11px;letter-spacing:.06em;color:var(--mute);
    display:flex;align-items:center;gap:9px;border-top:1px solid var(--line);padding-top:16px}

  /* main */
  .main{min-width:0;padding:0 30px 48px}
  .topbar{position:sticky;top:0;z-index:5;display:flex;align-items:center;justify-content:space-between;gap:14px;
    padding:18px 0;margin-bottom:8px;background:linear-gradient(180deg,var(--void) 60%,transparent);backdrop-filter:blur(4px)}
  .topbar h1{font-family:var(--disp);font-size:18px;font-weight:600;letter-spacing:.01em;margin:0}
  .controls{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
  .pill{font-family:var(--disp);font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;
    padding:6px 11px;border-radius:20px;border:1px solid var(--line);color:var(--mute)}
  .pill.safe{color:var(--gain);border-color:rgba(47,208,138,.4);background:rgba(47,208,138,.07)}
  .pill.armed{color:var(--armed);border-color:rgba(255,184,77,.4);background:rgba(255,184,77,.07)}
  .pill.halt{color:var(--loss);border-color:rgba(255,92,108,.4);background:rgba(255,92,108,.07)}

  button{font-family:var(--disp);font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;
    border-radius:9px;border:1px solid var(--line);background:var(--panel-2);color:var(--dim);padding:10px 14px;cursor:pointer;transition:.14s}
  button:hover{border-color:var(--beam-dim);color:var(--ink);box-shadow:inset 0 0 0 1px var(--beam-dim)}
  button:focus-visible{outline:2px solid var(--beam);outline-offset:2px}
  button.stop{background:transparent;border-color:var(--loss);color:var(--loss)}
  button.stop:hover{background:var(--loss);color:#0a0a0a;box-shadow:0 0 20px rgba(255,92,108,.4)}
  button.flatten{border-color:rgba(255,92,108,.4);color:var(--loss)}
  button.flatten:hover{background:var(--loss);color:#0a0a0a;box-shadow:0 0 16px rgba(255,92,108,.35)}
  button.resume{background:transparent;border-color:var(--gain);color:var(--gain)}
  button.resume:hover{background:var(--gain);color:#06210f;box-shadow:0 0 20px rgba(47,208,138,.4)}

  .view{display:none;animation:fade .25s ease}
  .view.active{display:block}
  @keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}

  /* posture hero */
  .banner{position:relative;overflow:hidden;border-radius:16px;padding:28px;margin-bottom:18px;border:1px solid var(--line);
    background:linear-gradient(180deg,var(--panel),var(--void))}
  .banner::before{content:"";position:absolute;inset:0;pointer-events:none;transition:background .4s;
    background:radial-gradient(620px 240px at 14% 0%, var(--glow,transparent), transparent 72%)}
  .banner::after{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--edge,var(--mute));box-shadow:0 0 18px var(--edge,transparent)}
  .posture{position:relative}
  .eyebrow{font-family:var(--disp);font-size:10px;letter-spacing:.3em;text-transform:uppercase;color:var(--mute)}
  .state{font-family:var(--disp);font-size:40px;font-weight:700;line-height:1;margin:7px 0 6px}
  .sub{color:var(--dim);font-size:13.5px}
  .banner.safe{--glow:rgba(47,208,138,.15);--edge:var(--gain)} .banner.safe .state{color:var(--gain);text-shadow:0 0 26px rgba(47,208,138,.35)}
  .banner.armed{--glow:rgba(255,184,77,.17);--edge:var(--armed)} .banner.armed .state{color:var(--armed);text-shadow:0 0 26px rgba(255,184,77,.4)}
  .banner.halt{--glow:rgba(255,92,108,.17);--edge:var(--loss)} .banner.halt .state{color:var(--loss);text-shadow:0 0 26px rgba(255,92,108,.4)}

  /* stats */
  .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}
  .stat{position:relative;background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:15px 16px;overflow:hidden;transition:.16s}
  .stat::before{content:"";position:absolute;left:0;right:0;top:0;height:1px;opacity:.6;background:linear-gradient(90deg,transparent,var(--beam-dim),transparent)}
  .stat:hover{border-color:var(--beam-dim);transform:translateY(-1px)}
  .stat .k{color:var(--mute);font-size:10px;letter-spacing:.16em;text-transform:uppercase;font-family:var(--disp)}
  .stat .v{font-family:var(--mono);font-size:20px;font-weight:500;margin-top:6px;letter-spacing:-.01em}

  /* cards */
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
  @media(max-width:900px){.grid{grid-template-columns:1fr}}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:15px;padding:18px;margin-bottom:16px}
  .card h2{font-family:var(--disp);font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--mute);
    margin:0 0 15px;font-weight:600;display:flex;align-items:center;gap:9px}
  .card h2::before{content:"";width:5px;height:5px;border-radius:1px;background:var(--beam);box-shadow:0 0 8px var(--beam);transform:rotate(45deg);flex:none}
  .card.flush{margin-bottom:0}

  table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:13px}
  th{text-align:left;color:var(--mute);font-weight:500;font-size:10px;letter-spacing:.1em;text-transform:uppercase;
    font-family:var(--disp);padding:0 8px 11px;border-bottom:1px solid var(--line)}
  td{padding:11px 8px;border-bottom:1px solid var(--line-2)}
  tr:last-child td{border-bottom:none}
  tbody tr{transition:background .14s}
  tbody tr:hover td{background:rgba(91,225,255,.03)}
  .gain{color:var(--gain)} .loss{color:var(--loss)} .muted{color:var(--mute)}

  .tag{font-family:var(--disp);font-size:10px;font-weight:600;letter-spacing:.07em;padding:3px 9px;border-radius:6px;border:1px solid var(--line);text-transform:uppercase}
  .tag.APPROVED{color:var(--gain);border-color:rgba(47,208,138,.4);background:rgba(47,208,138,.08)}
  .tag.REJECTED{color:var(--loss);border-color:rgba(255,92,108,.35);background:rgba(255,92,108,.07)}
  .tag.SKIPPED{color:var(--mute)}
  .tag.BLOCKED{color:var(--armed);border-color:rgba(255,184,77,.4);background:rgba(255,184,77,.08)}

  .read{font-family:var(--sans);line-height:1.7;color:var(--dim);font-size:13.5px}
  .log{background:#05080F;border:1px solid var(--line);border-radius:12px;padding:15px;font-family:var(--mono);
    font-size:12px;line-height:1.6;color:#9FB0C6;max-height:340px;overflow:auto;white-space:pre-wrap}
  .log::-webkit-scrollbar{width:9px}.log::-webkit-scrollbar-thumb{background:var(--line);border-radius:8px}

  /* symbols */
  .search{width:100%;background:var(--void);border:1px solid var(--line);border-radius:11px;color:var(--ink);
    font-family:var(--mono);font-size:13px;padding:13px 15px;margin-bottom:16px}
  .search:focus{outline:none;border-color:var(--beam-dim);box-shadow:0 0 0 1px var(--beam-dim)}
  .search::placeholder{color:var(--mute)}
  .symgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:13px}
  .sym{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;transition:.16s}
  .sym:hover{border-color:var(--beam-dim);transform:translateY(-2px)}
  .sym .top{display:flex;align-items:center;justify-content:space-between;margin-bottom:13px}
  .sym .tk{font-family:var(--disp);font-size:18px;font-weight:700;letter-spacing:.03em}
  .sym .px{font-family:var(--mono);font-size:14px}
  .sym .chg{font-family:var(--mono);font-size:12px}
  .sym .rows{display:grid;grid-template-columns:1fr 1fr;gap:9px 14px;font-family:var(--mono);font-size:12px;color:var(--dim)}
  .sym .rows .lbl{color:var(--mute);font-size:10px;letter-spacing:.08em;text-transform:uppercase;font-family:var(--disp)}
  .badge{font-family:var(--disp);font-size:9px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;padding:3px 7px;border-radius:5px;border:1px solid var(--line)}
  .badge.up{color:var(--gain);border-color:rgba(47,208,138,.4)} .badge.down{color:var(--loss);border-color:rgba(255,92,108,.35)}
  .badge.live{color:var(--beam);border-color:var(--beam-dim);background:rgba(91,225,255,.06)}
  .empty{color:var(--mute);font-family:var(--mono);font-size:13px;padding:24px;text-align:center}

  /* account */
  .profile{display:flex;align-items:center;gap:16px;margin-bottom:18px}
  .avatar{width:54px;height:54px;border-radius:14px;background:linear-gradient(135deg,var(--beam-dim),var(--panel-2));
    display:flex;align-items:center;justify-content:center;font-family:var(--disp);font-weight:700;font-size:19px;letter-spacing:.04em;
    border:1px solid var(--line);color:var(--ink)}
  .profile .nm{font-family:var(--disp);font-size:19px;font-weight:600}
  .profile .ro{color:var(--mute);font-size:12px;letter-spacing:.1em;text-transform:uppercase;font-family:var(--disp);margin-top:3px}
  .kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:13px;overflow:hidden}
  .kv .cell{background:var(--panel);padding:15px 16px}
  .kv .lbl{color:var(--mute);font-size:10px;letter-spacing:.14em;text-transform:uppercase;font-family:var(--disp)}
  .kv .val{font-family:var(--mono);font-size:16px;margin-top:6px}

  .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--mute);flex:none}
  .dot.on{background:var(--gain);animation:pulse 2s infinite}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(47,208,138,.5)}70%{box-shadow:0 0 0 7px rgba(47,208,138,0)}100%{box-shadow:0 0 0 0 rgba(47,208,138,0)}}

  /* mobile */
  @media(max-width:860px){
    .app{grid-template-columns:1fr}
    .sidebar{position:static;height:auto;flex-direction:row;align-items:center;gap:14px;overflow-x:auto;padding:14px 16px}
    nav{flex-direction:row;gap:2px}
    .side-foot{display:none}
    .main{padding:0 16px 40px}
    .topbar{flex-wrap:wrap}
  }
  @media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="logo"><svg width="30" height="30" viewBox="0 0 512 512" style="flex:none" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="lbeam" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#2C8FB0"/><stop offset="1" stop-color="#5BE1FF"/></linearGradient></defs><rect width="512" height="512" rx="116" fill="#0E1626"/><rect x="6" y="6" width="500" height="500" rx="110" fill="none" stroke="#22304a" stroke-width="6"/><polyline points="118,356 210,286 298,326 392,168" fill="none" stroke="url(#lbeam)" stroke-width="34" stroke-linecap="round" stroke-linejoin="round"/><g transform="rotate(45 392 168)"><rect x="372" y="148" width="40" height="40" rx="6" fill="#5BE1FF"/></g></svg><span class="logo-txt">AIS<small>Automated Investment Setup</small></span></div>
    <nav>
      <a class="nav active" data-view="overview"><span class="ico"></span>Overview</a>
      <a class="nav" data-view="symbols"><span class="ico"></span>Symbols</a>
      <a class="nav" data-view="account"><span class="ico"></span>Account</a>
      <a class="nav" data-view="activity"><span class="ico"></span>Activity</a>
    </nav>
    <div class="side-foot"><span class="dot" id="hbdot"></span><span id="hb">connecting…</span></div>
  </aside>

  <main class="main">
    <header class="topbar">
      <h1 id="viewTitle">Overview</h1>
      <div class="controls">
        <span id="modePill" class="pill">—</span>
        <button id="armBtn" onclick="toggleArm()">—</button>
        <button id="flattenBtn" class="flatten" onclick="flattenAll()">Sell all</button>
        <button id="killBtn" class="stop" onclick="toggleKill()">Stop all</button>
        <a class="pill" href="/logout" style="text-transform:none;letter-spacing:.02em">Log out</a>
      </div>
    </header>

    <!-- OVERVIEW -->
    <section class="view active" data-view="overview">
      <div id="banner" class="banner safe">
        <div class="posture">
          <div class="eyebrow">Engine posture</div>
          <div class="state" id="stateText">LOADING…</div>
          <div class="sub" id="stateSub">connecting to engine</div>
        </div>
      </div>
      <div class="stats">
        <div class="stat"><div class="k">Net Liquidation</div><div class="v" id="netliq">—</div></div>
        <div class="stat"><div class="k">Cash</div><div class="v" id="cash">—</div></div>
        <div class="stat"><div class="k">Open Positions</div><div class="v" id="poscount">—</div></div>
        <div class="stat"><div class="k">USD / CAD</div><div class="v" id="fx">—</div></div>
        <div class="stat"><div class="k">Market</div><div class="v" id="market">—</div></div>
        <div class="stat"><div class="k">Last Cycle</div><div class="v" id="lastcycle">—</div></div>
      </div>
      <div class="grid">
        <div class="card flush">
          <h2>Positions</h2>
          <table><thead><tr><th>Symbol</th><th>Qty</th><th>Avg</th><th>Last</th><th>Unreal.</th></tr></thead>
          <tbody id="positions"><tr><td class="muted" colspan="5">No open positions.</td></tr></tbody></table>
        </div>
        <div class="card flush">
          <h2>Market Read</h2>
          <div class="read" id="marketread">Waiting for the first cycle…</div>
        </div>
      </div>
      <div class="card flush">
        <h2>Decisions — latest cycle</h2>
        <table><thead><tr><th>Symbol</th><th>Action</th><th>Shares</th><th>Status</th><th>Reason</th></tr></thead>
        <tbody id="decisions"><tr><td class="muted" colspan="5">No decisions yet.</td></tr></tbody></table>
      </div>
    </section>

    <!-- SYMBOLS -->
    <section class="view" data-view="symbols">
      <div class="card flush">
        <h2>Symbol Explorer — search the full universe</h2>
        <input class="search" id="symSearch" placeholder="Search the full S&P 500 + ETF universe…" oninput="renderSymbols()">
        <div class="symgrid" id="symgrid"></div>
      </div>
    </section>

    <!-- ACCOUNT -->
    <section class="view" data-view="account">
      <div class="card">
        <div class="profile">
          <div class="avatar">NG</div>
          <div><div class="nm">Nirupam Gupta</div><div class="ro">Operator</div></div>
        </div>
        <div class="kv" id="acctKv"></div>
      </div>
      <div class="card flush">
        <h2>Engine Configuration</h2>
        <div class="kv" id="cfgKv"></div>
      </div>
    </section>

    <!-- ACTIVITY -->
    <section class="view" data-view="activity">
      <div class="card">
        <h2>End-of-Day Review
          <button id="copyReviewBtn" onclick="copyReview()" style="margin-left:auto">Copy</button>
        </h2>
        <div class="log" id="review" style="max-height:440px">Generated automatically after each market close.</div>
      </div>
      <div class="card flush">
        <h2>Live Log</h2>
        <div class="log" id="log">—</div>
      </div>
    </section>
  </main>
</div>

<script>
let CTRL = {kill_switch:false, dry_run:true};
let SYMS = [];           // engine's per-symbol read (snapshot)
let WL = [];             // current shortlist
let LB = {};             // full leaderboard: every swept name's score/trend

function money(v){ return v==null ? "—" : Number(v).toLocaleString(undefined,{maximumFractionDigits:0}); }
function num(v,d){ return v==null ? "—" : Number(v).toFixed(d==null?2:d); }
function fmt(v){ return v==null ? "—" : v; }
// Decision reasons and symbols come from the model, which reads outside news: escape before innerHTML.
function esc(v){ return String(v).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

/* view routing */
document.querySelectorAll('.nav').forEach(n=>{
  n.addEventListener('click',()=>{
    const v=n.dataset.view;
    document.querySelectorAll('.nav').forEach(x=>x.classList.toggle('active',x===n));
    document.querySelectorAll('.view').forEach(s=>s.classList.toggle('active',s.dataset.view===v));
    document.getElementById('viewTitle').textContent = n.textContent.trim();
  });
});

async function refresh(){
  try{
    const r = await fetch('/api/state'); const d = await r.json();
    const s = d.state || {}; CTRL = d.controls || CTRL;
    SYMS = d.symbols || []; WL = d.watchlist || []; LB = d.leaderboard || {};

    // posture
    const b=document.getElementById('banner'), st=document.getElementById('stateText'),
          sub=document.getElementById('stateSub'), arm=document.getElementById('armBtn'),
          kill=document.getElementById('killBtn'), pill=document.getElementById('modePill');
    const mode = CTRL.kill_switch ? 'halt' : (CTRL.dry_run ? 'safe' : 'armed');
    b.className='banner '+mode; pill.className='pill '+mode;
    if(CTRL.kill_switch){ st.textContent='HALTED'; sub.textContent='kill switch on — no analysis, no orders';
      pill.textContent='Halted'; kill.textContent='Resume'; kill.className='stop resume'; }
    else { st.textContent = CTRL.dry_run ? 'SAFE' : 'ARMED';
      sub.textContent = CTRL.dry_run ? 'analyzing only — no orders are placed' : 'placing orders on your paper account';
      pill.textContent = CTRL.dry_run ? 'Safe' : 'Armed';
      kill.textContent='Stop all'; kill.className='stop'; }
    arm.textContent = CTRL.dry_run ? 'Arm' : 'Disarm'; arm.disabled = CTRL.kill_switch;

    // stats
    const a=s.account||{};
    document.getElementById('netliq').textContent = a.net_liq!=null ? money(a.net_liq)+' '+(a.currency||'') : '—';
    document.getElementById('cash').textContent = a.cash!=null ? money(a.cash)+' '+(a.currency||'') : '—';
    document.getElementById('poscount').textContent = (s.positions||[]).length;
    document.getElementById('fx').textContent = (d.fx&&d.fx.usd_cad)!=null ? num(d.fx.usd_cad,4) : '—';
    document.getElementById('market').textContent = s.market_open ? 'OPEN' : 'CLOSED';
    document.getElementById('lastcycle').textContent = s.last_cycle_at ? s.last_cycle_at.slice(11,16)+' UTC' : '—';

    // positions
    const pos=s.positions||[];
    document.getElementById('positions').innerHTML = pos.length ? pos.map(p=>{
      const u=p.unrealized_pct, cls=u==null?'muted':(u>=0?'gain':'loss');
      const ut=u==null?'—':(u>=0?'+':'')+u.toFixed(2)+'%';
      return `<tr><td>${esc(p.symbol)}</td><td>${fmt(p.position)}</td><td>${fmt(p.avgCost)}</td><td>${fmt(p.last_price)}</td><td class="${cls}">${ut}</td></tr>`;
    }).join('') : '<tr><td class="muted" colspan="5">No open positions.</td></tr>';

    document.getElementById('marketread').textContent = s.market_read || 'Waiting for the first cycle…';

    // decisions
    const dec=s.decisions||[];
    document.getElementById('decisions').innerHTML = dec.length ? dec.map(x=>
      `<tr><td>${esc(x.symbol)}</td><td>${esc(x.action)}</td><td>${esc(x.shares||0)}</td><td><span class="tag ${esc(x.status)}">${esc(x.status)}</span></td><td class="muted">${esc(x.reason||'')}</td></tr>`
    ).join('') : '<tr><td class="muted" colspan="5">No decisions yet.</td></tr>';

    document.getElementById('review').textContent = d.review || 'Generated automatically after each market close.';
    document.getElementById('log').textContent = (d.log||[]).join('\n') || '—';

    renderSymbols();
    renderAccount(s, d.config||{});

    // heartbeat
    const hbd=document.getElementById('hbdot'), hb=document.getElementById('hb');
    if(s.heartbeat_at){ hbd.className='dot on'; hb.textContent='Engine live · '+s.heartbeat_at.slice(11,19); }
    else { hbd.className='dot'; hb.textContent='Engine offline'; }
  }catch(e){
    document.getElementById('stateSub').textContent='Dashboard cannot reach engine state.';
  }
}

function renderSymbols(){
  const q=(document.getElementById('symSearch').value||'').toUpperCase().trim();
  const detail={}; SYMS.forEach(x=>{ if(x.symbol) detail[x.symbol]=x; });
  let names=Object.keys(LB);
  SYMS.forEach(x=>{ if(x.symbol && !(x.symbol in LB)) names.push(x.symbol); });
  names.sort((a,b)=>((LB[b]?LB[b].score:-99)-(LB[a]?LB[a].score:-99)));
  const list = q ? names.filter(n=>n.includes(q)) : names.slice(0,48);
  const g=document.getElementById('symgrid');
  if(!names.length){ g.innerHTML='<div class="empty">The engine has not swept any names yet \u2014 they appear as it scores them (it sweeps the full S&P 500 over ~a day).</div>'; return; }
  if(!list.length){ g.innerHTML='<div class="empty">No symbols match \u201c'+esc(q)+'\u201d.</div>'; return; }
  const head = q ? '' : '<div style="grid-column:1/-1;color:var(--mute);font-family:var(--mono);font-size:11px;margin-bottom:2px">Showing the '+list.length+' strongest of '+names.length+' tracked \u2014 search to find any name.</div>';
  g.innerHTML=head+list.map(n=>{
    const d=detail[n], lb=LB[n], onWL=WL.includes(n);
    const up=(d?d.trend:(lb?lb.trend:''))==='up';
    if(d){
      const chg=d.pct_change_day, chgTxt=chg==null?'\u2014':(chg>=0?'+':'')+Number(chg).toFixed(2)+'%';
      return `<div class="sym">
        <div class="top"><div class="tk">${esc(n)}</div><span class="badge ${up?'up':'down'}">${up?'Uptrend':'Downtrend'}</span></div>
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:13px">
          <span class="px">$${fmt(d.last_price)}</span><span class="chg ${chg>=0?'gain':'loss'}">${chgTxt}</span>
        </div>
        <div class="rows">
          <div><div class="lbl">RSI 14</div>${num(d.rsi14_daily,1)}</div>
          <div><div class="lbl">MACD hist</div>${num(d.macd_hist,2)}</div>
          <div><div class="lbl">ATR 14</div>${num(d.atr14,2)}</div>
          <div><div class="lbl">vs SMA50</div>${d.pct_from_sma50==null?'\u2014':(d.pct_from_sma50>=0?'+':'')+Number(d.pct_from_sma50).toFixed(1)+'%'}</div>
        </div>
        ${onWL?'<div style="margin-top:13px"><span class="badge live">On shortlist</span></div>':''}
      </div>`;
    } else {
      const ago = lb&&lb.ts ? Math.round((Date.now()/1000 - lb.ts)/3600) : null;
      return `<div class="sym">
        <div class="top"><div class="tk">${esc(n)}</div><span class="badge ${up?'up':'down'}">${up?'Uptrend':'Downtrend'}</span></div>
        <div class="rows">
          <div><div class="lbl">Momentum</div>${lb?Number(lb.score).toFixed(3):'\u2014'}</div>
          <div><div class="lbl">Swept</div>${ago==null?'\u2014':(ago<=0?'just now':ago+'h ago')}</div>
        </div>
        ${onWL?'<div style="margin-top:13px"><span class="badge live">On shortlist</span></div>':''}
      </div>`;
    }
  }).join('');
}

function renderAccount(s, cfg){
  const a=s.account||{};
  const acct=[
    ['Broker','IBKR Paper'],
    ['Equity', a.net_liq!=null ? money(a.net_liq)+' '+(a.currency||'') : '—'],
    ['Cash', a.cash!=null ? money(a.cash)+' '+(a.currency||'') : '—'],
    ['Mode', CTRL.kill_switch?'Halted':(CTRL.dry_run?'Safe (paper analyze)':'Armed')],
    ['Market', s.market_open?'Open':'Closed'],
    ['Last cycle', s.last_cycle_at? s.last_cycle_at.slice(11,16)+' UTC':'—'],
  ];
  document.getElementById('acctKv').innerHTML = acct.map(([l,v])=>`<div class="cell"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  const cf=[
    ['Risk / trade', cfg.RISK_PER_TRADE_PCT!=null?cfg.RISK_PER_TRADE_PCT+'%':'—'],
    ['Max position', cfg.MAX_POSITION_PCT!=null?cfg.MAX_POSITION_PCT+'%':'—'],
    ['Daily loss cap', cfg.MAX_DAILY_LOSS_PCT!=null?cfg.MAX_DAILY_LOSS_PCT+'%':'—'],
    ['Max positions', fmt(cfg.MAX_OPEN_POSITIONS)],
    ['Trades / cycle', fmt(cfg.MAX_TRADES_PER_RUN)],
    ['Cycle', cfg.CYCLE_MINUTES!=null?cfg.CYCLE_MINUTES+' min':'—'],
    ['Min conviction', fmt(cfg.MIN_CONVICTION_TO_ENTER)],
    ['Universe','Full S&P 500 (rolling) + ETFs: QQQ SPY GLD USO · crypto: IBIT ETHA BSOL XRPC'],
  ];
  document.getElementById('cfgKv').innerHTML = cf.map(([l,v])=>`<div class="cell"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
}

async function setControl(patch, confirmMsg){
  if(confirmMsg && !confirm(confirmMsg)) return;
  await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(patch)});
  refresh();
}
function toggleKill(){
  if(CTRL.kill_switch) setControl({kill_switch:false});
  else setControl({kill_switch:true}, 'Stop ALL trading? The engine halts analysis and orders until you resume.');
}
function toggleArm(){
  if(CTRL.dry_run) setControl({dry_run:false}, 'Arm the engine? It will start placing orders on your PAPER account.');
  else setControl({dry_run:true});
}
async function flattenAll(){
  if(!confirm('Sell ALL open positions at market now, and cancel their stops/targets? This cannot be undone.')) return;
  try{ await fetch('/api/flatten',{method:'POST'}); alert('Flatten requested. The engine will close all positions within a few seconds.'); }
  catch(e){ alert('Could not send the flatten request.'); }
  refresh();
}
function copyReview(){
  const t=document.getElementById('review').textContent||'';
  const btn=document.getElementById('copyReviewBtn');
  navigator.clipboard.writeText(t).then(()=>{ const o=btn.textContent; btn.textContent='Copied!'; setTimeout(()=>btn.textContent=o,1500); })
    .catch(()=>alert('Copy failed — select the text manually.'));
}

refresh();
setInterval(refresh, 4000);
</script>
</body>
</html>"""


LOGIN_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in — Trading Engine</title>
<style>
  body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
    background:#0E1116;color:#E6EDF3;font-family:-apple-system,system-ui,sans-serif}
  .box{background:#161B22;border:1px solid #2A323C;border-radius:14px;padding:32px;width:300px}
  h1{font-size:13px;letter-spacing:.16em;text-transform:uppercase;color:#8B98A5;margin:0 0 18px}
  input{width:100%;box-sizing:border-box;background:#0E1116;border:1px solid #2A323C;border-radius:8px;
    color:#E6EDF3;padding:12px;font-size:15px;margin-bottom:12px}
  button{width:100%;background:#58A6FF;border:none;border-radius:8px;color:#06210f;
    font-weight:700;font-size:14px;padding:12px;cursor:pointer}
  .err{color:#F85149;font-size:13px;margin-bottom:12px;min-height:16px}
</style></head>
<body><form class="box" method="post">
  <h1>Trading Engine</h1>
  <div class="err">{{ error }}</div>
  <input type="password" name="password" placeholder="Password" autofocus autocomplete="current-password">
  <button type="submit">Sign in</button>
</form></body></html>"""


@app.before_request
def require_login():
    # CSRF: browsers always send Origin on cross-origin POSTs; refuse any that is not us
    # (SameSite alone misses other localhost ports, which count as the same site).
    if request.method == "POST":
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            abort(403)
    if request.endpoint in ("login", "static", "favicon_svg", "favicon_ico", "icon_180", "icon_512", "manifest"):
        return
    if not session.get("auth"):
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        pw = request.form.get("password", "")
        if not PASSWORD_HASH:
            error = "No password set yet — run: python set_password.py"
        elif check_password_hash(PASSWORD_HASH, pw):
            session["auth"] = True
            return redirect(url_for("index"))
        else:
            error = "Incorrect password."
    return render_template_string(LOGIN_PAGE, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    return render_template_string(PAGE)


@app.route("/api/state")
def api_state():
    snap = read_json("snapshot.json", {})
    return jsonify({
        "state": read_json(STATE_FILE, {}),
        "controls": read_controls(),
        "log": log_tail(),
        "review": read_review(),
        "symbols": snap.get("symbols", []),
        "fx": snap.get("fx", {}),
        "watchlist": read_json("watchlist.json", []),
        "leaderboard": read_json("scores.json", {}),
        "config": bot_config(),
    })


@app.route("/api/flatten", methods=["POST"])
def api_flatten():
    # Drop a request the orchestrator picks up on its next poll. The dashboard
    # never touches IBKR itself; the engine does the closing.
    with open("flatten_request.json", "w") as f:
        json.dump({"requested_at": time.time()}, f)
    return jsonify({"ok": True})


@app.route("/api/control", methods=["POST"])
def api_control():
    c = read_controls()
    data = request.get_json(silent=True) or {}  # JSON content type only
    if "dry_run" in data:
        c["dry_run"] = bool(data["dry_run"])
    if "kill_switch" in data:
        c["kill_switch"] = bool(data["kill_switch"])
    write_controls(c)
    return jsonify(c)



FAVICON_SVG = r'''<svg viewBox="0 0 512 512" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#141D2E"/><stop offset="1" stop-color="#070A11"/>
    </linearGradient>
    <linearGradient id="beam" x1="0" y1="1" x2="1" y2="0">
      <stop offset="0" stop-color="#2C8FB0"/><stop offset="1" stop-color="#5BE1FF"/>
    </linearGradient>
    <radialGradient id="halo" cx="0.74" cy="0.32" r="0.5">
      <stop offset="0" stop-color="#5BE1FF" stop-opacity="0.30"/>
      <stop offset="1" stop-color="#5BE1FF" stop-opacity="0"/>
    </radialGradient>
    <filter id="glow" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="11"/></filter>
  </defs>
  <rect width="512" height="512" rx="116" fill="url(#bg)"/>
  <rect width="512" height="512" rx="116" fill="url(#halo)"/>
  <rect x="5" y="5" width="502" height="502" rx="112" fill="none" stroke="#22304a" stroke-width="3"/>
  <line x1="118" y1="398" x2="394" y2="398" stroke="#27374f" stroke-width="11" stroke-linecap="round"/>
  <polyline points="118,356 210,286 298,326 392,168" fill="none" stroke="#5BE1FF" stroke-width="32"
            stroke-linecap="round" stroke-linejoin="round" opacity="0.45" filter="url(#glow)"/>
  <polyline points="118,356 210,286 298,326 392,168" fill="none" stroke="url(#beam)" stroke-width="32"
            stroke-linecap="round" stroke-linejoin="round"/>
  <g transform="rotate(45 392 168)"><rect x="368" y="144" width="48" height="48" rx="8" fill="#9FF0FF"/></g>
  <g transform="rotate(45 392 168)"><rect x="376" y="152" width="32" height="32" rx="5" fill="#5BE1FF"/></g>
</svg>'''
ICON_180_B64 = "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"
ICON_512_B64 = "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"


@app.route("/favicon.svg")
def favicon_svg():
    return Response(FAVICON_SVG, mimetype="image/svg+xml")


@app.route("/favicon.ico")
def favicon_ico():
    return Response(base64.b64decode(ICON_180_B64), mimetype="image/png")


@app.route("/icon-180.png")
def icon_180():
    return Response(base64.b64decode(ICON_180_B64), mimetype="image/png")


@app.route("/icon-512.png")
def icon_512():
    return Response(base64.b64decode(ICON_512_B64), mimetype="image/png")


@app.route("/manifest.webmanifest")
def manifest():
    return jsonify({
        "name": "AIS \u2014 Automated Investment Setup",
        "short_name": "AIS",
        "icons": [
            {"src": "/icon-180.png", "sizes": "180x180", "type": "image/png"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
        "theme_color": "#070A11", "background_color": "#070A11",
        "display": "standalone", "start_url": "/",
    })


if __name__ == "__main__":
    print("Control panel running at http://localhost:5000  (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=5000, debug=False)
