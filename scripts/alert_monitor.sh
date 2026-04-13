#!/usr/bin/env bash
set -euo pipefail
#
# RECON Alert Monitor — Threshold-based alerting between daily runs
#
# Checks key metrics every N minutes and fires Telegram alerts
# when thresholds are crossed. Designed to run via cron (every 15-30 min)
# or as a long-running loop.
#
# Usage:
#   ./alert_monitor.sh              # Single check
#   ./alert_monitor.sh --loop 15    # Check every 15 minutes
#

RECON_HOME="/home/recon/recon"
STATE_FILE="$RECON_HOME/config/alert_state.json"
LOG_FILE="$RECON_HOME/logs/alerts.log"

source /home/recon/.recon.env 2>/dev/null || true

log() { echo "[$(date +"%Y-%m-%d %H:%M:%S")] [ALERT] $1" | tee -a "$LOG_FILE"; }

send_alert() {
    local msg="$1"
    log "FIRING: $msg"
    [ -z "${RECON_TELEGRAM_TOKEN:-}" ] && { log "Telegram not configured"; return; }
    curl -s -X POST "https://api.telegram.org/bot${RECON_TELEGRAM_TOKEN}/sendMessage" \
        -H "Content-Type: application/json" \
        -d "{\"chat_id\":\"${RECON_TELEGRAM_CHAT_ID}\",\"text\":$(echo "$msg" | python3 -c 'import sys,json;print(json.dumps(sys.stdin.read()))')}" > /dev/null
}

mkdir -p "$(dirname "$LOG_FILE")"

# Initialize state file if missing
[ ! -f "$STATE_FILE" ] && echo '{}' > "$STATE_FILE"

run_checks() {
    log "Running threshold checks..."

    python3 << 'PYALERT'
import json
import urllib.request
import os
import sys
from datetime import datetime
from pathlib import Path

STATE_FILE = Path("/home/recon/recon/config/alert_state.json")
COOLDOWN_MINUTES = 60  # Don't re-fire same alert within this window

def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))

def get(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RECON/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None

def in_cooldown(state, key):
    last = state.get(key, {}).get("last_fired", "")
    if not last:
        return False
    try:
        fired = datetime.fromisoformat(last)
        return (datetime.now() - fired).total_seconds() < COOLDOWN_MINUTES * 60
    except Exception:
        return False

def fire(state, key, msg):
    if in_cooldown(state, key):
        return []
    state[key] = {
        "last_fired": datetime.now().isoformat(),
        "message": msg,
    }
    return [msg]

# ─── THRESHOLDS ────────────────────────────────────────────
# Edit these to tune sensitivity

BTC_MOVE_PCT = 5.0         # BTC 24h move > 5%
ETH_MOVE_PCT = 7.0         # ETH 24h move > 7%
FNG_EXTREME_LOW = 15       # Fear & Greed <= 15 (extreme fear)
FNG_EXTREME_HIGH = 85      # Fear & Greed >= 85 (extreme greed)
STABLECOIN_DEPEG_PCT = 1.0 # Stablecoin deviates > 1% from peg
VOLUME_SPIKE_MULT = 3.0    # 24h volume > 3x typical

alerts = []
state = load_state()

# ── BTC / ETH price moves ─────────────────────────────────
prices = get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true&include_24hr_vol=true")
if prices:
    for coin, threshold in [("bitcoin", BTC_MOVE_PCT), ("ethereum", ETH_MOVE_PCT), ("solana", 10.0)]:
        data = prices.get(coin, {})
        change = abs(data.get("usd_24h_change", 0) or 0)
        price = data.get("usd", 0)
        if change >= threshold:
            direction = "UP" if (data.get("usd_24h_change", 0) or 0) > 0 else "DOWN"
            alerts.extend(fire(state, f"{coin}_move",
                f"RECON ALERT: {coin.upper()} {direction} {change:.1f}% in 24h (${price:,.0f})"))

# ── Fear & Greed extremes ─────────────────────────────────
fng = get("https://api.alternative.me/fng/?limit=1")
if fng and fng.get("data"):
    val = int(fng["data"][0].get("value", 50))
    label = fng["data"][0].get("value_classification", "")
    if val <= FNG_EXTREME_LOW:
        alerts.extend(fire(state, "fng_low",
            f"RECON ALERT: EXTREME FEAR — Fear & Greed at {val}/100 ({label})"))
    elif val >= FNG_EXTREME_HIGH:
        alerts.extend(fire(state, "fng_high",
            f"RECON ALERT: EXTREME GREED — Fear & Greed at {val}/100 ({label})"))

# ── Stablecoin depeg ──────────────────────────────────────
stables = get("https://api.coingecko.com/api/v3/simple/price?ids=tether,usd-coin,dai,first-digital-usd&vs_currencies=usd")
if stables:
    for stable_id, name in [("tether","USDT"), ("usd-coin","USDC"), ("dai","DAI"), ("first-digital-usd","FDUSD")]:
        price = stables.get(stable_id, {}).get("usd", 1.0)
        if price and abs(price - 1.0) * 100 > STABLECOIN_DEPEG_PCT:
            direction = "above" if price > 1.0 else "below"
            alerts.extend(fire(state, f"depeg_{stable_id}",
                f"RECON ALERT: {name} DEPEG — trading at ${price:.4f} ({direction} peg by {abs(price-1.0)*100:.2f}%)"))

# ── Polymarket major market moves ─────────────────────────
try:
    pm_url = "https://gamma-api.polymarket.com/markets?closed=false&order=volume24hr&ascending=false&limit=5"
    pm_req = urllib.request.Request(pm_url, headers={"User-Agent": "RECON/1.0"})
    with urllib.request.urlopen(pm_req, timeout=15) as pm_r:
        pm_markets = json.loads(pm_r.read().decode())
    for m in pm_markets[:5]:
        vol_24h = float(m.get("volume24hr", 0) or 0)
        question = m.get("question", "?")[:100]
        # Alert on very high volume markets (>$5M/day = something big is happening)
        if vol_24h > 5_000_000:
            slug = m.get("conditionId", question[:20])
            alerts.extend(fire(state, f"pm_{slug[:30]}",
                f"RECON ALERT: Polymarket surge — \"{question}\" | 24h vol: ${vol_24h:,.0f}"))
except Exception:
    pass

# ── DeFi TVL crash ────────────────────────────────────────
tvl = get("https://api.llama.fi/v2/historicalChainTvl")
if isinstance(tvl, list) and len(tvl) >= 2:
    current = tvl[-1].get("tvl", 0)
    yesterday = tvl[-2].get("tvl", 0)
    if yesterday and current:
        change_pct = ((current - yesterday) / yesterday) * 100
        if change_pct <= -5.0:
            alerts.extend(fire(state, "tvl_crash",
                f"RECON ALERT: DeFi TVL dropped {abs(change_pct):.1f}% in 24h (${current:,.0f})"))

save_state(state)

# Output alerts (read by bash wrapper)
for a in alerts:
    print(f"ALERT:{a}")

if not alerts:
    print(f"OK: All thresholds normal ({datetime.now().strftime('%H:%M')})")
PYALERT
}

# Parse arguments
LOOP_INTERVAL=0
while [[ $# -gt 0 ]]; do
    case $1 in
        --loop) LOOP_INTERVAL="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [ "$LOOP_INTERVAL" -gt 0 ] 2>/dev/null; then
    log "Starting alert monitor (checking every ${LOOP_INTERVAL}m)"
    while true; do
        output=$(run_checks 2>&1)
        # Send any alerts via Telegram
        echo "$output" | grep "^ALERT:" | while read -r line; do
            msg="${line#ALERT:}"
            send_alert "$msg"
        done
        echo "$output" | grep -v "^ALERT:" | while read -r line; do
            log "$line"
        done
        sleep "$((LOOP_INTERVAL * 60))"
    done
else
    output=$(run_checks 2>&1)
    echo "$output" | grep "^ALERT:" | while read -r line; do
        msg="${line#ALERT:}"
        send_alert "$msg"
    done
    echo "$output" | grep -v "^ALERT:" | while read -r line; do
        log "$line"
    done
fi
