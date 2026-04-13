#!/usr/bin/env python3
"""
RECON Subreddit Discovery

Takes seed subreddits and discovers related high-activity subs.
Methods:
  1. Reddit's own subreddit search API
  2. Sidebar/wiki links from seed subs (related communities)
  3. Cross-post analysis: where do seed sub users also post?

Usage:
    python3 scripts/discover_subreddits.py
    python3 scripts/discover_subreddits.py --category crypto
    python3 scripts/discover_subreddits.py --min-subscribers 10000

Output:
    config/discovered_subreddits.yaml
"""

import os
import sys
import argparse
import json
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

RECON_HOME = Path("/home/recon/recon")
OUTPUT = RECON_HOME / "config" / "discovered_subreddits.yaml"

# Current seed subs (what we already monitor)
CURRENT_SEEDS = {
    "crypto_core": [
        "cryptocurrency", "Bitcoin", "ethereum", "CryptoMarkets", "defi",
        "ethfinance", "CryptoTechnology", "ethtrader", "altcoin", "web3",
    ],
    "prediction_markets": ["Polymarket", "PredictionMarkets"],
    "trading": ["algotrading", "wallstreetbets", "options"],
    "chains": ["solana", "bnbchainofficial", "basechain"],
    "nft_culture": ["NFT"],
    "ai": [
        "MachineLearning", "artificial", "LocalLLaMA", "ChatGPT",
        "ClaudeAI", "singularity", "StableDiffusion", "ArtificialIntelligence",
    ],
    "politics": [
        "politics", "PoliticalDiscussion", "geopolitics", "NeutralPolitics",
        "worldnews", "moderatepolitics", "neoliberal", "conservative",
    ],
    "economics": ["economics", "finance", "stocks", "FluentInFinance"],
}

# Search queries to find related subs
DISCOVERY_QUERIES = {
    "crypto": [
        "prediction market crypto", "defi leverage", "perpetual futures",
        "crypto derivatives", "on-chain analysis", "MEV", "layer 2",
        "base chain", "bnb chain defi", "crypto regulation",
    ],
    "ai": [
        "AI agents", "LLM fine-tuning", "AI crypto", "autonomous agents",
        "machine learning trading",
    ],
    "politics": [
        "prediction markets politics", "election forecasting", "political betting",
    ],
}


def get_json(url):
    """Fetch JSON from Reddit API."""
    headers = {
        "User-Agent": os.environ.get("REDDIT_USER_AGENT", "RECON/1.0 discovery"),
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return None


def search_subreddits(query: str, limit: int = 10) -> list:
    """Search for subreddits matching a query."""
    url = f"https://www.reddit.com/subreddits/search.json?q={urllib.request.quote(query)}&limit={limit}&sort=relevance"
    data = get_json(url)
    results = []
    if data and "data" in data:
        for child in data["data"].get("children", []):
            sub = child.get("data", {})
            results.append({
                "name": sub.get("display_name", ""),
                "subscribers": sub.get("subscribers", 0),
                "active_users": sub.get("accounts_active", 0),
                "description": sub.get("public_description", "")[:120],
                "over18": sub.get("over18", False),
            })
    return results


def get_sub_info(subreddit: str) -> dict:
    """Get info about a specific subreddit."""
    url = f"https://www.reddit.com/r/{subreddit}/about.json"
    data = get_json(url)
    if data and "data" in data:
        sub = data["data"]
        return {
            "name": sub.get("display_name", subreddit),
            "subscribers": sub.get("subscribers", 0),
            "active_users": sub.get("accounts_active", 0),
            "description": sub.get("public_description", "")[:120],
        }
    return None


def all_current_subs() -> set:
    """Get all currently monitored subs (lowercased)."""
    subs = set()
    for cat, sub_list in CURRENT_SEEDS.items():
        for s in sub_list:
            subs.add(s.lower())
    return subs


def discover():
    """Run discovery."""
    existing = all_current_subs()
    discovered = defaultdict(list)
    seen = set()

    print("RECON Subreddit Discovery")
    print(f"  Currently monitoring: {len(existing)} subreddits")
    print()

    # Method 1: Search-based discovery
    for category, queries in DISCOVERY_QUERIES.items():
        print(f"── Searching: {category} ──")
        for query in queries:
            results = search_subreddits(query)
            if not results:
                continue
            for sub in results:
                name = sub["name"]
                if name.lower() in existing or name.lower() in seen:
                    continue
                if sub["subscribers"] < 5000:  # Skip tiny subs
                    continue
                if sub["over18"]:
                    continue
                seen.add(name.lower())
                sub["found_via"] = f"search: {query}"
                sub["category"] = category
                discovered[category].append(sub)
                print(f"  + r/{name} ({sub['subscribers']:,} subs, {sub['active_users'] or '?'} active)")

    # Method 2: Check specific subs we know about but aren't monitoring
    KNOWN_CANDIDATES = [
        # Crypto
        "CryptoCurrencyTrading", "SatoshiStreetBets", "Bitcoinmarkets",
        "ethdev", "cosmosnetwork", "algorand", "CardanoMarkets",
        "defi_protocol", "UniSwap", "Aave", "MakerDAO",
        # Prediction markets adjacent
        "sportsbetting", "sportsbook", "Kalshi",
        # AI
        "MLQuestions", "deeplearning", "reinforcementlearning",
        "Bard", "OpenAI", "AnthropicAI",
        # Macro/Finance
        "SecurityAnalysis", "ValueInvesting", "Forex",
        "GlobalMarkets", "CryptoReality",
        # Politics adjacent
        "law", "SupremeCourt", "IRstudies",
    ]

    print(f"\n── Checking known candidates ──")
    for sub_name in KNOWN_CANDIDATES:
        if sub_name.lower() in existing or sub_name.lower() in seen:
            continue
        info = get_sub_info(sub_name)
        if info and info["subscribers"] >= 5000:
            seen.add(sub_name.lower())
            # Categorize
            name_lower = sub_name.lower()
            if any(k in name_lower for k in ["crypto", "bitcoin", "eth", "defi", "swap", "aave", "maker", "uni"]):
                cat = "crypto"
            elif any(k in name_lower for k in ["bet", "kalshi", "predict"]):
                cat = "prediction_adjacent"
            elif any(k in name_lower for k in ["ml", "ai", "learn", "openai", "bard", "anthropic", "deep"]):
                cat = "ai"
            elif any(k in name_lower for k in ["invest", "forex", "market", "secur", "value", "finance"]):
                cat = "economics"
            else:
                cat = "politics"

            info["found_via"] = "known candidate"
            info["category"] = cat
            discovered[cat].append(info)
            print(f"  + r/{info['name']} ({info['subscribers']:,} subs)")

    # Write output
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    output = {
        "metadata": {
            "generated": now,
            "total_discovered": sum(len(v) for v in discovered.values()),
            "note": "Review and add the best to the SUBS dict in scripts/collect_data.sh",
        },
    }
    for cat, subs in discovered.items():
        # Sort by subscribers descending
        subs.sort(key=lambda x: x.get("subscribers", 0), reverse=True)
        output[cat] = [
            {
                "name": s["name"],
                "subscribers": s["subscribers"],
                "active": s.get("active_users", 0),
                "found_via": s["found_via"],
                "description": s.get("description", ""),
            }
            for s in subs
        ]

    try:
        import yaml
        with open(OUTPUT, "w") as f:
            yaml.dump(output, f, default_flow_style=False, sort_keys=False)
    except ImportError:
        with open(OUTPUT, "w") as f:
            json.dump(output, f, indent=2)

    total = sum(len(v) for v in discovered.values())
    print(f"\nDiscovery complete: {total} new subreddits found")
    print(f"Output: {OUTPUT}")
    print(f"Review, then add to SUBS in scripts/collect_data.sh")


if __name__ == "__main__":
    discover()
