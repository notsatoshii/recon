#!/usr/bin/env bash
set -euo pipefail

RECON_HOME="${RECON_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"; export RECON_HOME
# Env file discovery: RECON_ENV, then repo, parent, $HOME, legacy v1 location
for _f in "${RECON_ENV:-}" "$RECON_HOME/.recon.env" "$RECON_HOME/../.recon.env" "$HOME/.recon.env" /home/recon/.recon.env; do
    [ -n "$_f" ] && [ -f "$_f" ] && { set -a; source "$_f"; set +a; break; }
done
RECON_TWSCRAPE_DB="${RECON_TWSCRAPE_DB:-$HOME/.recon_twscrape.db}"; export RECON_TWSCRAPE_DB
_venv="${RECON_VENV:-$RECON_HOME/../recon-venv}"; RECON_PY="$_venv/bin/python"; [ -x "$RECON_PY" ] || RECON_PY=python3
TODAY=$(date +%Y-%m-%d)
DATA_DIR="$RECON_HOME/data-sources"
LOG_FILE="${RECON_LOG_FILE:-$RECON_HOME/logs/${TODAY}.log}"

log() {
    local ts=$(date +"%H:%M:%S")
    echo "[$ts] [DATA] $1" | tee -a "$LOG_FILE"
}

mkdir -p "$DATA_DIR"/{reddit,onchain,news} "$RECON_HOME/briefs/$TODAY" "$(dirname "$LOG_FILE")"

log "========== DATA COLLECTION -- $TODAY =========="

# ─── REDDIT (RSS feeds, no API key needed) ─────────────────

log "Collecting Reddit data (in the background, alongside X)..."

python3 << 'PYREDDIT' &
import os, re, json
import sys, time, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone
# Trimmed 2026-10-04 from 40 to 21 subreddits that serve the desks (40 on RSS drew ~30 HTTP 429s a
# run and only 8-9 subs came back). Unauthenticated RSS from the droplet gets roughly one request
# per 10-45 s, so: requests are paced by Reddit's x-ratelimit-* headers, the fetch order rotates
# daily, a sub that cannot be fetched today reuses its last good pull if it is under 48 h old
# (labelled), and the block stops after RECON_REDDIT_MAX_SECONDS (default 420).
SUBS = {
    "crypto_core": ["CryptoCurrency","Bitcoin","ethereum","CryptoMarkets","defi","ethfinance"],
    "prediction_markets": ["Polymarket","PredictionMarkets"],
    "trading": ["wallstreetbets"],
    "chains": ["solana","BASEchain"],
    "ai": ["MachineLearning","LocalLLaMA","ChatGPT","ClaudeAI"],
    "politics": ["geopolitics","worldnews","NeutralPolitics"],
    "economics": ["Economics","finance","stocks"],
}
SPACING = float(os.environ.get("RECON_REDDIT_SPACING", "2"))
BUDGET = float(os.environ.get("RECON_REDDIT_MAX_SECONDS", "420"))
CACHE_H = 48
_start = time.monotonic()
CACHE = os.path.join(os.environ["RECON_HOME"], "data-sources", "reddit", "cache.json")

NS = {"atom": "http://www.w3.org/2005/Atom"}
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/atom+xml,application/xml,text/xml,*/*",
}
now = datetime.now(timezone.utc)
try:
    cache = json.load(open(CACHE))
except Exception:
    cache = {}

_next_ok = 0.0

def _pace(h):
    """Reddit sends x-ratelimit-remaining / -reset (seconds). When the window is used up,
    wait for its reset before the next request."""
    global _next_ok
    try:
        remaining = float(h.get("x-ratelimit-remaining") or 1)
        reset = float(h.get("x-ratelimit-reset") or 0)
    except (TypeError, ValueError):
        remaining, reset = 1.0, 0.0
    _next_ok = time.monotonic() + (min(reset, 60.0) + 1 if remaining < 1 else 0)

def fetch(url):
    """GET paced by Reddit's rate-limit headers; up to two retries on 429."""
    import urllib.error
    global _next_ok
    for attempt in (1, 2, 3):
        wait = _next_ok - time.monotonic()
        if wait > 0:
            if time.monotonic() + wait - _start > BUDGET:
                raise TimeoutError("time budget")
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                _pace(r.headers)
                return r.read().decode()
        except urllib.error.HTTPError as e:
            _pace(e.headers or {})
            if e.code != 429 or attempt == 3:
                raise
            if _next_ok <= time.monotonic():  # no usable header: back off 15 s
                _next_ok = time.monotonic() + 15

def parse(body):
    out = []
    root = ET.fromstring(body)
    for e in root.findall("atom:entry", NS)[:5]:
        title_el = e.find("atom:title", NS)
        title = title_el.text[:180] if title_el is not None and title_el.text else "?"
        link_el = e.find("atom:link", NS)
        post_url = link_el.get("href", "") if link_el is not None else ""
        content_el = e.find("atom:content", NS)
        summary = ""
        if content_el is not None and content_el.text:
            text = re.sub(r'<[^>]+>', ' ', content_el.text)
            text = re.sub(r'\s+', ' ', text).strip()[:150]
            if text and text != title:
                summary = text
        out.append(f"- {title} {post_url}".rstrip())
        if summary:
            out.append(f"  {summary}")
    return out

order = [(c, s) for c, subs in SUBS.items() for s in subs]
k = now.toordinal() % len(order)
order = order[k:] + order[:k]
blocks, fetched, cached, failed, rate_limited = {}, 0, 0, 0, 0
for cat, sub in order:
    err = ""
    if time.monotonic() - _start > BUDGET:
        err = "time budget"
    else:
        try:
            items = parse(fetch(f"https://www.reddit.com/r/{sub}/hot.rss"))
            if items:
                blocks[sub] = [f"### r/{sub}"] + items + [""]
                cache[sub] = {"at": now.isoformat(timespec="minutes"), "items": items}
                fetched += 1
            else:
                err = "empty"
        except Exception as e:
            err = str(e)[:60]
            rate_limited += "429" in err
        time.sleep(SPACING)
    if err:
        c = cache.get(sub)
        age_h = (now - datetime.fromisoformat(c["at"])).total_seconds() / 3600 if c else None
        if c and age_h <= CACHE_H:
            blocks[sub] = [f"### r/{sub} (cached from {c['at'][:16].replace('T', ' ')} UTC)"] + c["items"] + [""]
            cached += 1
        else:
            blocks[sub] = [f"### r/{sub} -- ERROR: {err}", ""]
            failed += 1

lines = [f"# Reddit Intelligence\n## {now.strftime('%Y-%m-%d %H:%M UTC')}\n"]
for cat, subs in SUBS.items():
    lines.append(f"\n---\n## {cat.upper()}\n")
    for s in subs:
        lines.extend(blocks.get(s, [f"### r/{s} -- ERROR: not reached", ""]))

with open(os.environ["RECON_HOME"] + "/data-sources/reddit/latest.md", "w") as f:
    f.write("\n".join(lines))
try:
    json.dump(cache, open(CACHE, "w"))
except OSError:
    pass
print(f"Reddit: {fetched}/{len(order)} subreddits fetched, {cached} from cache (<{CACHE_H} h), "
      f"{failed} missing ({rate_limited} on HTTP 429) in {int(time.monotonic() - _start)}s")
PYREDDIT
REDDIT_PID=$!

# ─── TWITTER/X (twscrape, account-backed X internal API) ─────

log "Collecting Twitter/X data..."
mkdir -p "$DATA_DIR/twitter"
# The collector writes a SOURCE UNAVAILABLE stub itself when twscrape or accounts are missing.
"$RECON_PY" "$RECON_HOME/scripts/collect_twitter.py" 2>&1 | while read line; do log "  $line"; done

log "  Twitter: $(wc -l < "$DATA_DIR/twitter/latest.md" 2>/dev/null || echo SKIPPED) lines"

wait "$REDDIT_PID" || log "  Reddit collector exited non-zero"
log "  Reddit: $(wc -l < "$DATA_DIR/reddit/latest.md" 2>/dev/null || echo FAILED) lines"

# ─── FUNDRAISING (RootData via Playwright) ────────────────

log "Collecting fundraising data..."
mkdir -p "$DATA_DIR/fundraising"

# RootData scraping is captcha-walled (audit 2026-09-10). Fundraising now comes from
# Google News RSS queries (free, keyless); DeFiLlama's raises API is tried in the on-chain step.
python3 << 'PYFUND'
import os, sys
import urllib.parse as _p
from datetime import datetime, timezone
try:
    import feedparser
except ImportError:
    feedparser = None

out = [f"# Fundraising Intelligence\n## {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
       "## Source: Google News RSS (crypto/web3 and AI rounds, last 7 days); RootData link only: https://www.rootdata.com/Fundraising\n"]

def gn(q):
    return "https://news.google.com/rss/search?q=" + _p.quote(q) + "&hl=en-US&gl=US&ceid=US:en"

QUERIES = {
    "CRYPTO / WEB3 ROUNDS": gn('(crypto OR web3 OR blockchain OR DeFi OR "prediction market") (raises OR raised OR "funding round" OR "Series A" OR "seed round") when:7d'),
    "AI ROUNDS": gn('("AI startup" OR "AI company" OR "AI lab") (raises OR raised OR "Series A" OR "Series B" OR "seed round" OR valuation) when:7d'),
    "KOREA ROUNDS": "https://news.google.com/rss/search?q=" + _p.quote("스타트업 투자 유치 (AI OR 인공지능 OR 블록체인 OR 가상자산) when:7d") + "&hl=ko&gl=KR&ceid=KR:ko",
}

if feedparser is None:
    out.append("feedparser not installed")
else:
    for label, url in QUERIES.items():
        out.append(f"## {label}\n")
        try:
            feed = feedparser.parse(url)
            entries = feed.entries[:15] if feed.entries else []
            if not entries:
                out.append("- (no items)\n"); continue
            for e in entries:
                out.append(f"- [{e.get('published','')[:16]}] {e.get('title','')[:200]} {e.get('link','')}")
            out.append("")
        except Exception as ex:
            out.append(f"- FEED ERROR: {str(ex)[:80]}\n")

path = os.path.join(os.environ["RECON_HOME"], "data-sources", "fundraising", "latest.md")
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w") as f:
    f.write("\n".join(out) + "\n")
print(f"Fundraising: {len(out)} lines from {len(QUERIES)} news queries")
PYFUND

log "  Fundraising: $(wc -l < "$DATA_DIR/fundraising/latest.md" 2>/dev/null || echo SKIPPED) lines"

# ─── ON-CHAIN (DeFiLlama + CoinGecko, free APIs) ───────────

log "Collecting on-chain data..."

python3 << 'PYCHAIN'
import os
import json, urllib.request
from datetime import datetime, timezone
out = [f"# On-Chain Intelligence\n## {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"]

def get(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"RECON/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"_error": str(e)}

# ── MARKET OVERVIEW ─────────────────────────────────────────
out.append("## MARKET OVERVIEW\n")

# CoinGecko global
g = get("https://api.coingecko.com/api/v3/global")
gd = g.get("data", {})
if gd:
    out.append(f"- Total crypto market cap: ${gd.get('total_market_cap',{}).get('usd',0):,.0f}")
    out.append(f"- 24h volume: ${gd.get('total_volume',{}).get('usd',0):,.0f}")
    out.append(f"- BTC dominance: {gd.get('market_cap_percentage',{}).get('btc',0):.1f}%")
    out.append(f"- ETH dominance: {gd.get('market_cap_percentage',{}).get('eth',0):.1f}%")
    out.append(f"- Active cryptocurrencies: {gd.get('active_cryptocurrencies',0)}")
    out.append("")

# Fear & Greed Index
fng = get("https://api.alternative.me/fng/?limit=3")
fng_data = fng.get("data", [])
if fng_data:
    latest = fng_data[0]
    out.append(f"- Fear & Greed Index: {latest.get('value','?')}/100 ({latest.get('value_classification','?')})")
    if len(fng_data) >= 2:
        prev = fng_data[1]
        out.append(f"- Yesterday: {prev.get('value','?')}/100 ({prev.get('value_classification','?')})")
    out.append("")

# Key prices with more detail
out.append("## KEY PRICES\n")
p = get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,bnb,base-protocol&vs_currencies=usd&include_24hr_change=true&include_market_cap=true&include_24hr_vol=true")
if "_error" not in p:
    for coin in ["bitcoin","ethereum","solana","bnb"]:
        data = p.get(coin, {})
        if data:
            price = data.get("usd", 0)
            change = data.get("usd_24h_change", 0) or 0
            vol = data.get("usd_24h_vol", 0) or 0
            mcap = data.get("usd_market_cap", 0) or 0
            out.append(f"- {coin.upper()}: ${price:,.2f} ({change:+.1f}% 24h) vol ${vol:,.0f} mcap ${mcap:,.0f}")
out.append("")

# Trending coins
trending = get("https://api.coingecko.com/api/v3/search/trending")
coins = trending.get("coins", [])
if coins:
    out.append("## TRENDING (CoinGecko)\n")
    for c in coins[:7]:
        item = c.get("item", {})
        out.append(f"- {item.get('name','?')} ({item.get('symbol','?')}) — rank #{item.get('market_cap_rank','?')}")
    out.append("")

# ── TOTAL DEFI TVL ──────────────────────────────────────────
out.append("## TOTAL DEFI TVL\n")
hist = get("https://api.llama.fi/v2/historicalChainTvl")
if isinstance(hist, list) and len(hist) > 0:
    out.append(f"- Current: ${hist[-1].get('tvl',0):,.0f}")
    if len(hist) >= 7:
        cur, prev = hist[-1].get("tvl",0), hist[-7].get("tvl",0)
        if prev: out.append(f"- 7d change: {((cur-prev)/prev)*100:+.1f}%")
    if len(hist) >= 30:
        prev30 = hist[-30].get("tvl", 0)
        if prev30: out.append(f"- 30d change: {((cur-prev30)/prev30)*100:+.1f}%")
    out.append("")

# ── CHAIN TVLs (KEY CHAINS) ──────────────
out.append("## CHAIN TVLs (KEY CHAINS)\n")
chains = get("https://api.llama.fi/v2/chains")
if isinstance(chains, list):
    target_chains = {"Base", "BSC", "Ethereum", "Solana", "Polygon", "Arbitrum", "Optimism"}
    for c in sorted(chains, key=lambda x: x.get("tvl",0), reverse=True):
        if c.get("name") in target_chains:
            out.append(f"- {c['name']}: TVL ${c.get('tvl',0):,.0f}")
    out.append("")

# ── POLYMARKET LIVE MARKETS (direct API) ─────────────────
out.append("## POLYMARKET LIVE MARKETS\n")
try:
    pm_url = "https://gamma-api.polymarket.com/markets?closed=false&order=volume24hr&ascending=false&limit=15"
    pm_req = urllib.request.Request(pm_url, headers={"User-Agent": "RECON/1.0"})
    with urllib.request.urlopen(pm_req, timeout=20) as pm_r:
        pm_markets = json.loads(pm_r.read().decode())
    if pm_markets:
        for m in pm_markets:
            question = m.get("question", "?")[:120]
            volume = float(m.get("volume", 0) or 0)
            volume_24h = float(m.get("volume24hr", 0) or 0)
            liquidity = float(m.get("liquidityNum", 0) or 0)
            # Get best price (outcome probabilities)
            outcomes = m.get("outcomePrices", "")
            if isinstance(outcomes, str) and outcomes:
                try:
                    prices = json.loads(outcomes)
                    if prices:
                        yes_price = float(prices[0]) * 100
                        out.append(f"- {question}")
                        out.append(f"  YES: {yes_price:.0f}% | 24h vol: ${volume_24h:,.0f} | total vol: ${volume:,.0f} | liq: ${liquidity:,.0f}")
                except (json.JSONDecodeError, IndexError, ValueError):
                    out.append(f"- {question} (vol: ${volume:,.0f})")
            else:
                out.append(f"- {question} (vol: ${volume:,.0f})")
        out.append("")
except Exception as e:
    out.append(f"Polymarket API error: {str(e)[:60]}\n")

# ── PREDICTION MARKET PROTOCOLS (CORE SECTOR) ──────────────
out.append("## PREDICTION MARKET PROTOCOLS\n")
for slug in ["polymarket", "azuro", "kalshi"]:
    d = get(f"https://api.llama.fi/protocol/{slug}")
    if "_error" not in d:
        tvls = d.get("currentChainTvls", {})
        total = sum(v for v in tvls.values() if isinstance(v, (int,float)))
        chains = [c for c in tvls.keys() if not c.endswith("-borrowed") and not c.endswith("-staking")]
        out.append(f"### {d.get('name', slug)}")
        out.append(f"- TVL: ${total:,.0f}")
        if chains: out.append(f"- Chains: {', '.join(chains)}")
        h = d.get("tvl", [])
        if len(h) >= 7:
            c7, p7 = h[-1].get("totalLiquidityUSD",0), h[-7].get("totalLiquidityUSD",0)
            if p7: out.append(f"- 7d TVL change: {((c7-p7)/p7)*100:+.1f}%")
        if len(h) >= 30:
            p30 = h[-30].get("totalLiquidityUSD",0)
            if p30: out.append(f"- 30d TVL change: {((c7-p30)/p30)*100:+.1f}%")
        if d.get("mcap"): out.append(f"- Market cap: ${d['mcap']:,.0f}")
        out.append("")

# Prediction market token prices
out.append("### Prediction Market Tokens\n")
pm_ids = "drift-protocol,azuro-protocol,gnosis,sx-network"
pm = get(f"https://api.coingecko.com/api/v3/simple/price?ids={pm_ids}&vs_currencies=usd&include_24hr_change=true&include_market_cap=true&include_24hr_vol=true")
if "_error" not in pm:
    for token, data in pm.items():
        price = data.get("usd", 0)
        change = data.get("usd_24h_change", 0) or 0
        vol = data.get("usd_24h_vol", 0) or 0
        mcap = data.get("usd_market_cap", 0) or 0
        if price: out.append(f"- {token}: ${price:.4f} ({change:+.1f}%) vol ${vol:,.0f} mcap ${mcap:,.0f}")
out.append("")

# ── DEX VOLUMES (daily trading activity) ────────────────────
out.append("## DEX VOLUMES (24h)\n")
dex = get("https://api.llama.fi/overview/dexs?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true")
if "_error" not in dex:
    out.append(f"- Total 24h DEX volume: ${dex.get('total24h',0):,.0f}")
    ch7 = dex.get("change_7d")
    if ch7: out.append(f"- 7d volume change: {ch7:+.1f}%")
    out.append("")

    # Prediction market + derivatives DEXs specifically
    out.append("### Prediction & Derivatives DEX Volume\n")
    targets = ["kalshi","polymarket","azuro","drift","hyperliquid","dydx","gmx","synthetix","aerodrome"]
    for p in sorted(dex.get("protocols",[]), key=lambda x: x.get("total24h",0) or 0, reverse=True):
        name_lower = p.get("name","").lower().replace(" ","")
        if any(t in name_lower for t in targets):
            vol24 = p.get("total24h",0) or 0
            vol7d = p.get("total7d",0) or 0
            ch = p.get("change_7d")
            chs = f" ({ch:+.1f}% 7d)" if ch else ""
            if vol24 > 0:
                out.append(f"- {p['name']}: 24h ${vol24:,.0f} | 7d ${vol7d:,.0f}{chs}")
    out.append("")

    # Top 10 overall for context
    out.append("### Top 10 DEXs by 24h Volume\n")
    for p in sorted(dex.get("protocols",[]), key=lambda x: x.get("total24h",0) or 0, reverse=True)[:10]:
        vol = p.get("total24h",0) or 0
        ch = p.get("change_7d")
        chs = f" ({ch:+.1f}% 7d)" if ch else ""
        out.append(f"- {p['name']}: ${vol:,.0f}{chs}")
    out.append("")

# ── FEE REVENUE (protocol health) ──────────────────────────
out.append("## FEE REVENUE (24h)\n")
fees = get("https://api.llama.fi/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true")
if "_error" not in fees:
    # Top fee earners
    out.append("### Top Fee Earners\n")
    for p in sorted(fees.get("protocols",[]), key=lambda x: x.get("total24h",0) or 0, reverse=True)[:12]:
        f24 = p.get("total24h",0) or 0
        if f24 > 100_000:
            out.append(f"- {p['name']}: ${f24:,.0f}/day")
    out.append("")

    # Prediction market / derivatives fees specifically
    out.append("### Prediction & Derivatives Fees\n")
    fee_targets = ["polymarket","kalshi","azuro","hyperliquid","dydx","gmx","drift","synthetix"]
    for p in sorted(fees.get("protocols",[]), key=lambda x: x.get("total24h",0) or 0, reverse=True):
        name_lower = p.get("name","").lower().replace(" ","")
        if any(t in name_lower for t in fee_targets):
            f24 = p.get("total24h",0) or 0
            if f24 > 0:
                out.append(f"- {p['name']}: ${f24:,.0f}/day")
    out.append("")

# ── COMPETITORS (detailed) ─────────────────────────────────
out.append("## DERIVATIVES PROTOCOLS (TVL + Details)\n")
for slug in ["synthetix","dydx","drift-protocol","hyperliquid","gmx"]:
    d = get(f"https://api.llama.fi/protocol/{slug}")
    if "_error" not in d:
        tvls = d.get("currentChainTvls", {})
        total = sum(v for v in tvls.values() if isinstance(v, (int,float)))
        chains = [c for c in tvls.keys() if not c.endswith("-borrowed") and not c.endswith("-staking")]
        out.append(f"### {d.get('name', slug)}")
        out.append(f"- TVL: ${total:,.0f}")
        if chains: out.append(f"- Chains: {', '.join(chains)}")
        if d.get("mcap"): out.append(f"- Market cap: ${d['mcap']:,.0f}")
        h = d.get("tvl", [])
        if len(h) >= 7:
            c7, p7 = h[-1].get("totalLiquidityUSD",0), h[-7].get("totalLiquidityUSD",0)
            if p7: out.append(f"- 7d TVL change: {((c7-p7)/p7)*100:+.1f}%")
        out.append("")

# ── STABLECOINS (capital flows) ─────────────────────────────
out.append("## STABLECOIN SUPPLY\n")
stables = get("https://stablecoins.llama.fi/stablecoins?includePrices=true")
if "_error" not in stables:
    for s in sorted(stables.get("peggedAssets",[]), key=lambda x: x.get("circulating",{}).get("peggedUSD",0) or 0, reverse=True)[:6]:
        mcap = s.get("circulating",{}).get("peggedUSD",0) or 0
        out.append(f"- {s['name']} ({s['symbol']}): ${mcap:,.0f}")
    out.append("")

# Stablecoin Supply by Chain (key chains)
out.append("### Stablecoin Supply by Chain (key chains)\n")
sc_chains = get("https://stablecoins.llama.fi/stablecoinchains")
if isinstance(sc_chains, list):
    target = {"Base", "BSC", "Ethereum", "Solana", "Polygon", "Arbitrum"}
    for c in sorted(sc_chains, key=lambda x: x.get("totalCirculatingUSD",{}).get("peggedUSD",0) or 0, reverse=True):
        if c.get("name") in target:
            supply = c.get("totalCirculatingUSD",{}).get("peggedUSD",0) or 0
            out.append(f"- {c['name']}: ${supply:,.0f}")
    out.append("")

# ── YIELDS (opportunity cost benchmarks) ──────────────
out.append("## TOP STABLECOIN YIELDS (opportunity cost)\n")
yields = get("https://yields.llama.fi/pools")
if "_error" not in yields:
    pools = yields.get("data", [])
    stable_pools = [p for p in pools if p.get("stablecoin") and (p.get("tvlUsd",0) or 0) > 10_000_000]
    for pool in sorted(stable_pools, key=lambda x: x.get("apy",0) or 0, reverse=True)[:8]:
        apy = pool.get("apy",0) or 0
        tvl = pool.get("tvlUsd",0) or 0
        out.append(f"- {pool['project']}/{pool.get('symbol','?')}: APY {apy:.1f}% TVL ${tvl:,.0f} ({pool.get('chain','?')})")
    out.append("")

# ── BASE CHAIN ECOSYSTEM (key ecosystem) ────────────────────
out.append("## BASE CHAIN ECOSYSTEM\n")
protocols = get("https://api.llama.fi/protocols")
if isinstance(protocols, list):
    base_protos = [p for p in protocols if "Base" in (p.get("chains",[])) and p.get("category") not in ("CEX",)]
    for p in sorted(base_protos, key=lambda x: x.get("tvl",0) or 0, reverse=True)[:10]:
        tvl = p.get("tvl",0) or 0
        cat = p.get("category", "?")
        out.append(f"- {p['name']}: TVL ${tvl:,.0f} [{cat}]")
    out.append("")

    # BNB Chain ecosystem (key ecosystem)
    out.append("## BNB CHAIN ECOSYSTEM\n")
    bnb_protos = [p for p in protocols if "BSC" in (p.get("chains",[])) and p.get("category") not in ("CEX",)]
    for p in sorted(bnb_protos, key=lambda x: x.get("tvl",0) or 0, reverse=True)[:10]:
        tvl = p.get("tvl",0) or 0
        cat = p.get("category", "?")
        out.append(f"- {p['name']}: TVL ${tvl:,.0f} [{cat}]")
    out.append("")

# ── BTC NETWORK HEALTH ──────────────────────────────────────
out.append("## BTC NETWORK HEALTH\n")
btc = get("https://api.blockchain.info/stats")
if "_error" not in btc:
    out.append(f"- Hash rate: {btc.get('hash_rate',0)/1e12:.1f} EH/s")
    out.append(f"- Transactions (24h): {btc.get('n_tx',0):,}")
    out.append(f"- Blocks mined (24h): {btc.get('n_blocks_mined',0)}")
    out.append(f"- BTC mined (24h): {btc.get('n_btc_mined',0)/1e8:.2f} BTC")
    out.append(f"- Difficulty: {btc.get('difficulty',0):,.0f}")
    out.append("")

# ── AI x CRYPTO TOKENS ─────────────────────────────────────
out.append("## AI x CRYPTO TOKENS\n")
ai_ids = "render-token,bittensor,fetch-ai,ocean-protocol,the-graph,worldcoin,akash-network,nosana,virtuals-protocol"
ai_prices = get(f"https://api.coingecko.com/api/v3/simple/price?ids={ai_ids}&vs_currencies=usd&include_24hr_change=true&include_market_cap=true")
if "_error" not in ai_prices:
    for token_id in sorted(ai_prices.keys(), key=lambda x: ai_prices[x].get("usd_market_cap", 0) or 0, reverse=True):
        data = ai_prices[token_id]
        price = data.get("usd", 0)
        change = data.get("usd_24h_change", 0) or 0
        mcap = data.get("usd_market_cap", 0) or 0
        if price:
            out.append(f"- {token_id}: ${price:.4f} ({change:+.1f}%) mcap ${mcap:,.0f}")
    out.append("")

# ── RECENT FUNDRAISING ROUNDS ───────────────────────────────
out.append("## RECENT FUNDRAISING ROUNDS\n")
# DeFiLlama raises endpoint (may be paywalled)
raises = get("https://api.llama.fi/raises")
if isinstance(raises, dict) and "raises" in raises:
    all_raises = raises["raises"]
    # Filter last 14 days
    import time as _time
    cutoff = _time.time() - (14 * 86400)
    recent = [r for r in all_raises if r.get("date", 0) > cutoff]
    recent.sort(key=lambda x: x.get("amount", 0) or 0, reverse=True)
    if recent:
        for r in recent[:15]:
            name = r.get("name", "?")
            amount = r.get("amount", 0) or 0
            round_type = r.get("round", "?")
            category = r.get("category", "?")
            lead = ", ".join(r.get("leadInvestors", [])[:3]) or "undisclosed"
            other = ", ".join(r.get("otherInvestors", [])[:3])
            chains = ", ".join(r.get("chains", [])) or ""
            amount_str = f"${amount/1e6:.1f}M" if amount >= 1e6 else f"${amount:,.0f}" if amount else "undisclosed"
            line = f"- {name}: {amount_str} {round_type} [{category}]"
            if lead != "undisclosed": line += f" | Lead: {lead}"
            if other: line += f" | Also: {other}"
            if chains: line += f" | Chains: {chains}"
            out.append(line)
        out.append("")
    else:
        out.append("No raises in last 14 days.\n")
elif isinstance(raises, list):
    import time as _time
    cutoff = _time.time() - (14 * 86400)
    recent = [r for r in raises if r.get("date", 0) > cutoff]
    recent.sort(key=lambda x: x.get("amount", 0) or 0, reverse=True)
    for r in recent[:15]:
        name = r.get("name", "?")
        amount = r.get("amount", 0) or 0
        round_type = r.get("round", "?")
        category = r.get("category", "?")
        lead = ", ".join(r.get("leadInvestors", [])[:3]) or "undisclosed"
        amount_str = f"${amount/1e6:.1f}M" if amount >= 1e6 else f"${amount:,.0f}" if amount else "undisclosed"
        out.append(f"- {name}: {amount_str} {round_type} [{category}] | Lead: {lead}")
    out.append("")
else:
    out.append("DeFiLlama raises API unavailable.\n")

# ── DEFI SECTOR OVERVIEW ───────────────────────────────────
out.append("## DEFI SECTOR (CoinGecko)\n")
defi = get("https://api.coingecko.com/api/v3/global/decentralized_finance_defi")
if "_error" not in defi:
    dd = defi.get("data", {})
    try:
        out.append(f"- DeFi market cap: ${float(dd.get('defi_market_cap',0)):,.0f}")
        out.append(f"- DEX 24h volume: ${float(dd.get('trading_volume_24h',0)):,.0f}")
        out.append(f"- DeFi dominance: {float(dd.get('defi_dominance',0)):.1f}%")
        out.append(f"- Top DeFi coin: {dd.get('top_coin_name','?')}")
    except (ValueError, TypeError):
        pass
    out.append("")

with open(os.environ["RECON_HOME"] + "/data-sources/onchain/latest.md", "w") as f:
    f.write("\n".join(out))
print(f"On-chain: {len(out)} lines")
PYCHAIN

log "  On-chain: $(wc -l < "$DATA_DIR/onchain/latest.md" 2>/dev/null || echo FAILED) lines"

# ─── NEWS (RSS feeds) ───────────────────────────────────────

log "Collecting news data..."

python3 << 'PYNEWS'
import os
import sys
try:
    import feedparser
except ImportError:
    print("feedparser not installed. Run: pip install feedparser")
    with open(os.environ["RECON_HOME"] + "/data-sources/news/latest.md", "w") as f:
        f.write("# News\n## NOT CONFIGURED\nInstall: pip install feedparser\n")
    sys.exit(0)

from datetime import datetime, timezone
out = [f"# News Intelligence\n## {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"]

FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "The Block": "https://www.theblock.co/rss",
    "Decrypt": "https://decrypt.co/feed",
    "CoinTelegraph": "https://cointelegraph.com/rss",
    "DeFiant": "https://thedefiant.io/feed",
    "Blockworks": "https://blockworks.co/feed",
    "Unchained": "https://unchainedcrypto.com/feed/",
}

AI_FEEDS = {
    "TechCrunch AI": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge AI": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "Ars Technica AI": "https://feeds.arstechnica.com/arstechnica/technology-lab",
}

for name, url in FEEDS.items():
    try:
        feed = feedparser.parse(url)
        entries = feed.entries[:5] if feed.entries else []
        if entries:
            out.append(f"### {name}")
            for e in entries:
                t = e.get("title","")[:180]
                pub = e.get("published","")[:16]
                s = e.get("summary","")[:120].replace("<p>","").replace("</p>","").replace("\n"," ")
                out.append(f"- [{pub}] {t}")
                if s: out.append(f"  {s}")
            out.append("")
    except:
        out.append(f"### {name} -- FEED ERROR\n")

# ─── AI & TECH NEWS ──────────────────────────────────────
out.append("\n## AI & TECH NEWS\n")
for name, url in AI_FEEDS.items():
    try:
        feed = feedparser.parse(url)
        entries = feed.entries[:5] if feed.entries else []
        if entries:
            out.append(f"### {name}")
            for e in entries:
                t = e.get("title","")[:180]
                pub = e.get("published","")[:16]
                s = e.get("summary","")[:120].replace("<p>","").replace("</p>","").replace("\n"," ")
                out.append(f"- [{pub}] {t}")
                if s: out.append(f"  {s}")
            out.append("")
    except:
        out.append(f"### {name} -- FEED ERROR\n")

# ─── AI NEWSLETTER / AI EDUCATION / KOREA (added 2026-09-11) ──
import urllib.parse as _p

def _gn(q, lang="en-US", gl="US", ceid="US:en"):
    return "https://news.google.com/rss/search?q=" + _p.quote(q) + f"&hl={lang}&gl={gl}&ceid={ceid}"

def _gn_ko(q):
    return _gn(q, "ko", "KR", "KR:ko")

def emit_feed(name, url, n):
    try:
        feed = feedparser.parse(url)
        entries = feed.entries[:n] if feed.entries else []
        if not entries:
            out.append(f"### {name} -- empty\n"); return
        out.append(f"### {name}")
        for e in entries:
            t = e.get("title", "")[:180]
            pub = e.get("published", "")[:16]
            link = e.get("link", "")
            s = e.get("summary", "")
            s = s.replace("<p>", "").replace("</p>", "").replace("\n", " ")
            if "news.google.com" in url:
                s = ""  # Google News summaries are HTML link lists, not text
            out.append(f"- [{pub}] {t} {link}".rstrip())
            if s and s[:100].strip() and not s.startswith("<"):
                out.append(f"  {s[:120]}")
        out.append("")
    except Exception as ex:
        out.append(f"### {name} -- FEED ERROR: {str(ex)[:60]}\n")

out.append("\n## AI NEWSLETTER SOURCES\n")
emit_feed("OpenAI News", "https://openai.com/news/rss.xml", 6)
emit_feed("Google News: model & tool releases", _gn('(OpenAI OR Anthropic OR "Google DeepMind" OR Meta AI OR Mistral OR xAI) (model OR release OR launch OR pricing) when:2d'), 10)
emit_feed("Google News: AI agents & coding tools", _gn('("AI agent" OR "coding agent" OR Codex OR "Claude Code" OR Cursor OR Copilot) when:2d'), 8)

out.append("\n## AI EDUCATION & WORKFORCE\n")
emit_feed("Google News: AI education & literacy", _gn('("AI education" OR "AI literacy" OR "AI training" OR "AI upskilling" OR "AI curriculum") when:3d'), 10)
emit_feed("Google News: AI at work", _gn('("AI in the workplace" OR "workers use AI" OR "employees AI tools" OR "AI productivity") when:3d'), 8)

out.append("\n## KOREA — AI\n")
emit_feed("AI타임스", "https://www.aitimes.com/rss/allArticle.xml", 10)
emit_feed("전자신문", "https://rss.etnews.com/Section901.xml", 6)
emit_feed("Google 뉴스: 인공지능", _gn_ko("인공지능 OR AI 도입 OR AI 에이전트 when:2d"), 10)
emit_feed("Google 뉴스: AI 교육·직무", _gn_ko("AI 교육 OR 인공지능 교육 OR AI 직무교육 OR AI 활용 교육 when:3d"), 8)

out.append("\n## KOREA — CRYPTO & MARKETS\n")
emit_feed("블록미디어", "https://www.blockmedia.co.kr/feed", 6)
emit_feed("토큰포스트", "https://www.tokenpost.kr/rss", 6)
emit_feed("Google 뉴스: 가상자산", _gn_ko("가상자산 OR 암호화폐 OR 비트코인 규제 when:2d"), 8)
emit_feed("Google 뉴스: 예측시장", _gn_ko("폴리마켓 OR 예측시장 OR 프리딕션마켓 when:7d"), 5)

# ─── CRYPTOPANIC (aggregated news with sentiment) ──────────
import os, urllib.request, json
cp_key = os.environ.get("CRYPTOPANIC_API_KEY", "")
if cp_key:
    out.append("### CryptoPanic (aggregated + sentiment)\n")
    try:
        req = urllib.request.Request(
            f"https://cryptopanic.com/api/v1/posts/?auth_token={cp_key}&public=true&kind=news&filter=hot",
            headers={"User-Agent": "RECON/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        for post in data.get("results", [])[:10]:
            title = post.get("title", "")[:180]
            source = post.get("source", {}).get("title", "?")
            votes = post.get("votes", {})
            pos = votes.get("positive", 0)
            neg = votes.get("negative", 0)
            sentiment = f"+{pos}/-{neg}" if (pos or neg) else ""
            out.append(f"- [{source}] {title} {sentiment}")
        out.append("")
    except Exception as e:
        out.append(f"### CryptoPanic -- ERROR: {str(e)[:60]}\n")

with open(os.environ["RECON_HOME"] + "/data-sources/news/latest.md", "w") as f:
    f.write("\n".join(out))
source_count = len(FEEDS) + (1 if cp_key else 0)
print(f"News: {len(out)} lines from {source_count} sources")
PYNEWS

log "  News: $(wc -l < "$DATA_DIR/news/latest.md" 2>/dev/null || echo FAILED) lines"

# ─── AI/TOOLS (GitHub Trending + Hacker News) ──────────────

log "Collecting AI/tools data..."
mkdir -p "$DATA_DIR/ai_tools"

python3 << 'PYAITOOLS'
import os
import json, urllib.request
from datetime import datetime, timedelta, timezone

out = [f"# AI & Tools Intelligence\n## {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"]

def get(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RECON/1.0", "Accept": "application/vnd.github.v3+json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"_error": str(e)}

# ── GITHUB TRENDING (AI/ML repos) ──────────────────────────
# Two searches: (1) new repos gaining traction, (2) established repos with recent activity
week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

out.append("## GITHUB TRENDING — NEW AI REPOS (created last 7 days)\n")
searches = [
    f"https://api.github.com/search/repositories?q=created:>{week_ago}+stars:>20&sort=stars&order=desc&per_page=15",
]
for search_url in searches:
    gh = get(search_url)
    if "_error" not in gh and "items" in gh:
        ai_keywords = ["ai", "llm", "gpt", "claude", "agent", "model", "transformer", "neural",
                       "ml", "machine-learning", "deep-learning", "inference", "rag", "embedding",
                       "fine-tun", "prompt", "chat", "copilot", "diffusion", "vision", "nlp"]
        for repo in gh.get("items", [])[:30]:
            name = repo.get("full_name", "?")
            desc = (repo.get("description") or "").lower()
            topics = [t.lower() for t in repo.get("topics", [])]
            # Filter for AI/ML relevance
            all_text = f"{name.lower()} {desc} {' '.join(topics)}"
            if any(kw in all_text for kw in ai_keywords):
                stars = repo.get("stargazers_count", 0)
                lang = repo.get("language", "?")
                url = repo.get("html_url", "")
                desc_clean = (repo.get("description") or "")[:120]
                out.append(f"- [{name}]({url}) -- {stars} stars [{lang}]")
                if desc_clean: out.append(f"  {desc_clean}")
        out.append("")

out.append("## GITHUB TRENDING — HOT AI REPOS (most starred recently)\n")
# Search for AI repos pushed in last 7 days, sorted by stars (catches established repos gaining momentum)
gh2 = get(f"https://api.github.com/search/repositories?q=topic:ai+topic:llm+pushed:>{week_ago}+stars:>500&sort=stars&order=desc&per_page=10")
if "_error" not in gh2 and "items" in gh2:
    for repo in gh2.get("items", [])[:10]:
        name = repo.get("full_name", "?")
        desc = (repo.get("description") or "")[:120]
        stars = repo.get("stargazers_count", 0)
        lang = repo.get("language", "?")
        url = repo.get("html_url", "")
        updated = repo.get("pushed_at", "")[:10]
        out.append(f"- [{name}]({url}) -- {stars} stars [{lang}] (updated {updated})")
        if desc: out.append(f"  {desc}")
    out.append("")
else:
    # Broader fallback
    gh3 = get(f"https://api.github.com/search/repositories?q=llm+agent+pushed:>{week_ago}+stars:>200&sort=stars&order=desc&per_page=10")
    if "_error" not in gh3 and "items" in gh3:
        for repo in gh3.get("items", [])[:10]:
            name = repo.get("full_name", "?")
            desc = (repo.get("description") or "")[:120]
            stars = repo.get("stargazers_count", 0)
            url = repo.get("html_url", "")
            out.append(f"- [{name}]({url}) -- {stars} stars")
            if desc: out.append(f"  {desc}")
        out.append("")
    else:
        out.append("GitHub API unavailable.\n")

# ── HACKER NEWS TOP (AI/tech relevant) ────────────────────
out.append("## HACKER NEWS TOP STORIES\n")
hn_top = get("https://hacker-news.firebaseio.com/v0/topstories.json")
if isinstance(hn_top, list):
    ai_keywords = ["ai", "llm", "gpt", "claude", "openai", "anthropic", "model", "agent", "transformer",
                   "machine learning", "neural", "gpu", "inference", "fine-tune", "rag", "vector",
                   "crypto", "blockchain", "defi", "bitcoin", "ethereum"]
    count = 0
    for story_id in hn_top[:50]:
        if count >= 10: break
        story = get(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json")
        if isinstance(story, dict) and story.get("title"):
            title = story["title"]
            title_lower = title.lower()
            # Include if AI/tech/crypto related
            if any(kw in title_lower for kw in ai_keywords) or count < 5:
                url = story.get("url", f"https://news.ycombinator.com/item?id={story_id}")
                score = story.get("score", 0)
                comments = story.get("descendants", 0)
                out.append(f"- [{title}]({url})")
                out.append(f"  {score} points, {comments} comments")
                count += 1
    out.append("")
else:
    out.append("HN API unavailable.\n")

with open(os.environ["RECON_HOME"] + "/data-sources/ai_tools/latest.md", "w") as f:
    f.write("\n".join(out))
print(f"AI/Tools: {len(out)} lines")
PYAITOOLS

log "  AI/Tools: $(wc -l < "$DATA_DIR/ai_tools/latest.md" 2>/dev/null || echo FAILED) lines"

# ═══════════════════════════════════════════════════════════
# LAYER 1 COMPLETE: Raw data collected
# Now run processing layers before assembling final package
# ═══════════════════════════════════════════════════════════

# RECON_RUN_DIR: the orchestrator collects into its own run folder (validation runs use briefs/<date>-<tag>)
BRIEF_DIR="${RECON_RUN_DIR:-$RECON_HOME/briefs/$TODAY}"; mkdir -p "$BRIEF_DIR"

# ─── ASSEMBLE RAW DATA (intermediate, for processing layers) ─

log "Assembling raw data for processing..."
RAW_PKG="$BRIEF_DIR/00_raw_data.md"

echo "# RAW DATA -- $TODAY" > "$RAW_PKG"
echo "## Collected: $(date +'%H:%M:%S %Z')" >> "$RAW_PKG"
echo "" >> "$RAW_PKG"

for src in reddit twitter onchain news ai_tools fundraising; do
    [ -f "$DATA_DIR/$src/latest.md" ] && {
        echo "---" >> "$RAW_PKG"
        echo "" >> "$RAW_PKG"
        cat "$DATA_DIR/$src/latest.md" >> "$RAW_PKG"
        echo "" >> "$RAW_PKG"
    }
done

log "  Raw data: $(wc -c < "$RAW_PKG") bytes"

# ─── LAYER 1.5: DEDUPLICATION ───────────────────────────────

log "Deduplicating cross-source items..."
DEDUP_REPORT="$BRIEF_DIR/00_dedup_report.md"
python3 "$RECON_HOME/scripts/deduplicate.py" "$RAW_PKG" "$DEDUP_REPORT" 2>&1 | while read line; do log "  $line"; done

# ─── LAYER 2: WORLD MONITOR (geopolitical context) ──────────

log "LAYER 2: World Monitor (geopolitical intelligence)..."
mkdir -p "$DATA_DIR/worldmonitor"
sg docker -c "python3 $RECON_HOME/scripts/collect_worldmonitor.py" 2>&1 | while read line; do log "  $line"; done
log "  WorldMonitor: $(wc -l < "$DATA_DIR/worldmonitor/latest.md" 2>/dev/null || echo SKIPPED) lines"

# ─── LAYER 3: BETTAFISH (sentiment analysis on raw data) ────

log "LAYER 3: BettaFish (sentiment analysis)..."
mkdir -p "$DATA_DIR/bettafish"
python3 "$RECON_HOME/scripts/collect_bettafish.py" 2>&1 | while read line; do log "  $line"; done
log "  BettaFish: $(wc -l < "$DATA_DIR/bettafish/latest.md" 2>/dev/null || echo SKIPPED) lines"

# ─── ASSEMBLE PROCESSED INTELLIGENCE PACKAGE ─────────────────
#
# Structure for agents:
#   1. SENTIMENT & MARKET MOOD (BettaFish) — read this first
#   2. GEOPOLITICAL CONTEXT (World Monitor) — macro backdrop
#   3. ON-CHAIN DATA — quantitative signals
#   4. NEWS INTELLIGENCE — what just happened
#   5. SOCIAL INTELLIGENCE — Reddit + Twitter discourse
#
# Agents receive processed context, not raw feeds.
# ─────────────────────────────────────────────────────────────

log "Assembling processed intelligence package..."
PKG="$BRIEF_DIR/00_data_package.md"

cat > "$PKG" << PKGHEADER
# RECON INTELLIGENCE PACKAGE -- $TODAY
## Assembled: $(date +'%H:%M:%S %Z')
## Structure: Sentiment → Geopolitical → On-Chain → News → Social

This package has been processed through three layers:
1. Deduplication: cross-source signals identified and duplicate stories merged
2. BettaFish: LLM-powered sentiment analysis on social and news data
3. World Monitor: geopolitical intelligence from 79 global sources

PKGHEADER

# 0. Cross-source signals (highest priority — same story from multiple sources)
if [ -f "$DEDUP_REPORT" ] && [ -s "$DEDUP_REPORT" ]; then
    echo "---" >> "$PKG"
    echo "" >> "$PKG"
    echo "# SECTION 0: CROSS-SOURCE SIGNALS" >> "$PKG"
    echo "" >> "$PKG"
    cat "$DEDUP_REPORT" >> "$PKG"
    echo "" >> "$PKG"
fi

# 1. Sentiment layer — sets the mood for all agents
echo "---" >> "$PKG"
echo "" >> "$PKG"
echo "# SECTION 1: SENTIMENT & MARKET MOOD" >> "$PKG"
echo "" >> "$PKG"
[ -f "$DATA_DIR/bettafish/latest.md" ] && cat "$DATA_DIR/bettafish/latest.md" >> "$PKG"
echo "" >> "$PKG"

# 2. Geopolitical context — macro backdrop
echo "---" >> "$PKG"
echo "" >> "$PKG"
echo "# SECTION 2: GEOPOLITICAL CONTEXT" >> "$PKG"
echo "" >> "$PKG"
[ -f "$DATA_DIR/worldmonitor/latest.md" ] && cat "$DATA_DIR/worldmonitor/latest.md" >> "$PKG"
echo "" >> "$PKG"

# 3. On-chain data — quantitative signals
echo "---" >> "$PKG"
echo "" >> "$PKG"
echo "# SECTION 3: ON-CHAIN & MARKET DATA" >> "$PKG"
echo "" >> "$PKG"
[ -f "$DATA_DIR/onchain/latest.md" ] && cat "$DATA_DIR/onchain/latest.md" >> "$PKG"
echo "" >> "$PKG"

# 4. News intelligence
echo "---" >> "$PKG"
echo "" >> "$PKG"
echo "# SECTION 4: NEWS INTELLIGENCE" >> "$PKG"
echo "" >> "$PKG"
[ -f "$DATA_DIR/news/latest.md" ] && cat "$DATA_DIR/news/latest.md" >> "$PKG"
echo "" >> "$PKG"

# 5. Social intelligence (Reddit + Twitter)
echo "---" >> "$PKG"
echo "" >> "$PKG"
echo "# SECTION 5: SOCIAL INTELLIGENCE" >> "$PKG"
echo "" >> "$PKG"
[ -f "$DATA_DIR/reddit/latest.md" ] && cat "$DATA_DIR/reddit/latest.md" >> "$PKG"
echo "" >> "$PKG"
[ -f "$DATA_DIR/twitter/latest.md" ] && cat "$DATA_DIR/twitter/latest.md" >> "$PKG"
echo "" >> "$PKG"

# 6. AI & tools (was only in ai-digest mode before 2026-09-11)
echo "---" >> "$PKG"
echo "" >> "$PKG"
echo "# SECTION 6: AI & TOOLS" >> "$PKG"
echo "" >> "$PKG"
[ -f "$DATA_DIR/ai_tools/latest.md" ] && cat "$DATA_DIR/ai_tools/latest.md" >> "$PKG"
echo "" >> "$PKG"

# 7. Fundraising (was only in fundraising mode before 2026-09-11)
echo "---" >> "$PKG"
echo "" >> "$PKG"
echo "# SECTION 7: FUNDRAISING" >> "$PKG"
echo "" >> "$PKG"
[ -f "$DATA_DIR/fundraising/latest.md" ] && cat "$DATA_DIR/fundraising/latest.md" >> "$PKG"
echo "" >> "$PKG"

log "  Intelligence package: $(wc -c < "$PKG") bytes ($(wc -l < "$PKG") lines)"

# ═══════════════════════════════════════════════════════════
# LAYER 4: DAILY DISCOVERY (expand source lists)
# Runs after collection so it doesn't slow down the main pipeline.
# New discoveries are added to seed lists for tomorrow's collection.
# ═══════════════════════════════════════════════════════════

log "LAYER 4: Source discovery (background, for tomorrow)..."

# Discover new subreddits (lightweight, uses Reddit search API)
if python3 -c "import yaml" 2>/dev/null; then
    python3 "$RECON_HOME/scripts/discover_subreddits.py" >> "$LOG_FILE" 2>&1 &
    log "  Subreddit discovery launched (background)"
fi

# Discover new Twitter accounts (requires twscrape account)
# Twitter discovery shares the burner account with collection and burns its rate limit; opt-in only.
if [ "${RECON_TWITTER_DISCOVERY:-0}" = "1" ] && python3 -c "import twscrape" 2>/dev/null; then
    # Only run discovery if twscrape has active accounts
    has_accounts=$(python3 -c "
import asyncio
from twscrape import AccountsPool
async def check():
    pool = AccountsPool('$RECON_TWSCRAPE_DB')
    accs = await pool.accounts_info()
    print('yes' if any(a['active'] for a in accs) else 'no')
asyncio.run(check())
" 2>/dev/null)
    if [ "$has_accounts" = "yes" ]; then
        python3 "$RECON_HOME/scripts/discover_twitter.py" --method retweets >> "$LOG_FILE" 2>&1 &
        log "  Twitter discovery launched (background, retweet mining)"
    else
        log "  Twitter discovery skipped (no active twscrape accounts)"
    fi
fi

log "========== DATA COLLECTION & PROCESSING COMPLETE =========="
