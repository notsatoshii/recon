#!/usr/bin/env bash
set -euo pipefail

RECON_HOME="/home/recon/recon"
TODAY=$(date +%Y-%m-%d)
DATA_DIR="$RECON_HOME/data-sources"
LOG_FILE="$RECON_HOME/logs/${TODAY}.log"

log() {
    local ts=$(date +"%H:%M:%S")
    echo "[$ts] [DATA] $1" | tee -a "$LOG_FILE"
}

mkdir -p "$DATA_DIR"/{reddit,onchain,news} "$RECON_HOME/briefs/$TODAY" "$(dirname "$LOG_FILE")"

log "========== DATA COLLECTION -- $TODAY =========="

# ─── REDDIT (PRAW) ──────────────────────────────────────────

log "Collecting Reddit data..."

python3 << 'PYREDDIT'
import os, sys
try:
    import praw
except ImportError:
    print("PRAW not installed. Run: pip install praw")
    with open("/home/recon/recon/data-sources/reddit/latest.md", "w") as f:
        f.write("# Reddit Data\n## NOT CONFIGURED\nInstall PRAW: pip install praw\n")
    sys.exit(0)

cid = os.environ.get("REDDIT_CLIENT_ID", "")
csec = os.environ.get("REDDIT_CLIENT_SECRET", "")
if not cid or not csec:
    print("Reddit API not configured. Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET.")
    with open("/home/recon/recon/data-sources/reddit/latest.md", "w") as f:
        f.write("# Reddit Data\n## NOT CONFIGURED\nSet REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in ~/.recon.env\nCreate app at: https://www.reddit.com/prefs/apps\n")
    sys.exit(0)

reddit = praw.Reddit(client_id=cid, client_secret=csec,
                     user_agent=os.environ.get("REDDIT_USER_AGENT", "RECON/1.0"))

SUBS = {
    "crypto_core": ["cryptocurrency","Bitcoin","ethereum","CryptoMarkets","defi","ethfinance","CryptoTechnology","ethtrader","altcoin","web3","NFT"],
    "prediction_markets": ["Polymarket","PredictionMarkets"],
    "trading": ["algotrading","wallstreetbets","options"],
    "chains": ["solana","bnbchainofficial","basechain"],
    "ai": ["MachineLearning","artificial","LocalLLaMA","ChatGPT","ClaudeAI","singularity","StableDiffusion","ArtificialIntelligence"],
    "politics": ["politics","PoliticalDiscussion","geopolitics","NeutralPolitics","worldnews","economics","moderatepolitics","neoliberal","conservative"],
    "economics": ["economics","finance","stocks","FluentInFinance"],
}

lines = [f"# Reddit Intelligence\n## {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M UTC')}\n"]

for cat, subs in SUBS.items():
    lines.append(f"\n---\n## {cat.upper()}\n")
    for sub_name in subs:
        try:
            sub = reddit.subreddit(sub_name)
            posts = [p for p in sub.hot(limit=6) if not p.stickied][:5]
            lines.append(f"### r/{sub_name}")
            for p in posts:
                lines.append(f"- [{p.score}pts, {p.num_comments}cmt] {p.title[:180]}")
                if p.num_comments > 20:
                    p.comment_sort = "top"
                    p.comments.replace_more(limit=0)
                    for c in p.comments[:2]:
                        lines.append(f"  > ({c.score}pts) {c.body[:120].replace(chr(10),' ')}")
            lines.append("")
        except Exception as e:
            lines.append(f"### r/{sub_name} -- ERROR: {str(e)[:60]}\n")

with open("/home/recon/recon/data-sources/reddit/latest.md", "w") as f:
    f.write("\n".join(lines))
print(f"Reddit: {len(lines)} lines from {sum(len(v) for v in SUBS.values())} subreddits")
PYREDDIT

log "  Reddit: $(wc -l < "$DATA_DIR/reddit/latest.md" 2>/dev/null || echo FAILED) lines"

# ─── TWITTER/X (twscrape, no API key) ──────────────────────

log "Collecting Twitter/X data..."
mkdir -p "$DATA_DIR/twitter"

if python3 -c "import twscrape" 2>/dev/null; then
    python3 "$RECON_HOME/scripts/collect_twitter.py" 2>&1 | while read line; do log "  $line"; done
else
    log "  twscrape not installed -- skipping Twitter collection"
    echo "# Twitter/X Intelligence\n## NOT CONFIGURED\nInstall: pip install twscrape" > "$DATA_DIR/twitter/latest.md"
fi

log "  Twitter: $(wc -l < "$DATA_DIR/twitter/latest.md" 2>/dev/null || echo SKIPPED) lines"

# ─── ON-CHAIN (DeFiLlama + CoinGecko, free APIs) ───────────

log "Collecting on-chain data..."

python3 << 'PYCHAIN'
import json, urllib.request
from datetime import datetime

out = [f"# On-Chain Intelligence\n## {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}\n"]

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

# ── CHAIN TVLs (LEVER's chains + competitors) ──────────────
out.append("## CHAIN TVLs (LEVER-RELEVANT)\n")
chains = get("https://api.llama.fi/v2/chains")
if isinstance(chains, list):
    target_chains = {"Base", "BSC", "Ethereum", "Solana", "Polygon", "Arbitrum", "Optimism"}
    for c in sorted(chains, key=lambda x: x.get("tvl",0), reverse=True):
        if c.get("name") in target_chains:
            out.append(f"- {c['name']}: TVL ${c.get('tvl',0):,.0f}")
    out.append("")

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
out.append("## LEVER COMPETITORS (TVL + Details)\n")
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

# Stablecoin supply on LEVER's chains
out.append("### Stablecoin Supply by Chain (LEVER-relevant)\n")
sc_chains = get("https://stablecoins.llama.fi/stablecoinchains")
if isinstance(sc_chains, list):
    target = {"Base", "BSC", "Ethereum", "Solana", "Polygon", "Arbitrum"}
    for c in sorted(sc_chains, key=lambda x: x.get("totalCirculatingUSD",{}).get("peggedUSD",0) or 0, reverse=True):
        if c.get("name") in target:
            supply = c.get("totalCirculatingUSD",{}).get("peggedUSD",0) or 0
            out.append(f"- {c['name']}: ${supply:,.0f}")
    out.append("")

# ── YIELDS (opportunity cost for LEVER users) ──────────────
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

# ── BASE CHAIN ECOSYSTEM (LEVER's home) ────────────────────
out.append("## BASE CHAIN ECOSYSTEM\n")
protocols = get("https://api.llama.fi/protocols")
if isinstance(protocols, list):
    base_protos = [p for p in protocols if "Base" in (p.get("chains",[])) and p.get("category") not in ("CEX",)]
    for p in sorted(base_protos, key=lambda x: x.get("tvl",0) or 0, reverse=True)[:10]:
        tvl = p.get("tvl",0) or 0
        cat = p.get("category", "?")
        out.append(f"- {p['name']}: TVL ${tvl:,.0f} [{cat}]")
    out.append("")

    # BNB Chain ecosystem (XMarket's home)
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

with open("/home/recon/recon/data-sources/onchain/latest.md", "w") as f:
    f.write("\n".join(out))
print(f"On-chain: {len(out)} lines")
PYCHAIN

log "  On-chain: $(wc -l < "$DATA_DIR/onchain/latest.md" 2>/dev/null || echo FAILED) lines"

# ─── NEWS (RSS feeds) ───────────────────────────────────────

log "Collecting news data..."

python3 << 'PYNEWS'
import sys
try:
    import feedparser
except ImportError:
    print("feedparser not installed. Run: pip install feedparser")
    with open("/home/recon/recon/data-sources/news/latest.md", "w") as f:
        f.write("# News\n## NOT CONFIGURED\nInstall: pip install feedparser\n")
    sys.exit(0)

from datetime import datetime

out = [f"# News Intelligence\n## {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}\n"]

FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "The Block": "https://www.theblock.co/rss",
    "Decrypt": "https://decrypt.co/feed",
    "CoinTelegraph": "https://cointelegraph.com/rss",
    "DeFiant": "https://thedefiant.io/feed",
    "Blockworks": "https://blockworks.co/feed",
    "Unchained": "https://unchainedcrypto.com/feed/",
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

with open("/home/recon/recon/data-sources/news/latest.md", "w") as f:
    f.write("\n".join(out))
source_count = len(FEEDS) + (1 if cp_key else 0)
print(f"News: {len(out)} lines from {source_count} sources")
PYNEWS

log "  News: $(wc -l < "$DATA_DIR/news/latest.md" 2>/dev/null || echo FAILED) lines"

# ─── ASSEMBLE DATA PACKAGE ──────────────────────────────────

log "Assembling data package..."

BRIEF_DIR="$RECON_HOME/briefs/$TODAY"
PKG="$BRIEF_DIR/00_data_package.md"

echo "# RECON DATA PACKAGE -- $TODAY" > "$PKG"
echo "## Collected: $(date +'%H:%M:%S %Z')" >> "$PKG"
echo "" >> "$PKG"

for src in reddit twitter onchain news; do
    [ -f "$DATA_DIR/$src/latest.md" ] && {
        echo "---" >> "$PKG"
        echo "" >> "$PKG"
        cat "$DATA_DIR/$src/latest.md" >> "$PKG"
        echo "" >> "$PKG"
    }
done

log "  Package: $(wc -c < "$PKG") bytes"
log "========== DATA COLLECTION COMPLETE =========="
