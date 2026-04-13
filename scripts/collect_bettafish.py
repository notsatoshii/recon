#!/usr/bin/env python3
"""
BettaFish English Adaptation — Multi-Agent Sentiment Analysis
Adapted from 666ghj/BettaFish (微舆) for English crypto/DeFi/prediction market sources.

Architecture follows BettaFish's pattern:
  - MindSpider (crawling) → replaced with our existing data sources
  - QueryEngine (search) → keyword extraction from collected data
  - MediaEngine (media analysis) → sentiment scoring from news + social
  - InsightEngine (deep analysis) → trend detection + anomaly flagging
  - ReportEngine (synthesis) → structured sentiment report

Output: /home/recon/recon/data-sources/bettafish/latest.md
"""

import json
import os
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

RECON_HOME = Path("/home/recon/recon")
OUTPUT_FILE = RECON_HOME / "data-sources" / "bettafish" / "latest.md"

# Sentiment keywords for simple lexicon-based analysis
BULLISH_WORDS = {
    "bullish", "moon", "pump", "rally", "breakout", "surge", "soar", "all-time high",
    "ath", "buy", "accumulate", "undervalued", "adoption", "partnership", "launch",
    "upgrade", "milestone", "growth", "inflows", "institutional", "approval",
}
BEARISH_WORDS = {
    "bearish", "dump", "crash", "plunge", "collapse", "sell", "overvalued", "scam",
    "hack", "exploit", "rug", "outflows", "regulatory", "ban", "lawsuit", "sec",
    "investigation", "fear", "panic", "liquidation", "capitulation", "warning",
}
NEUTRAL_SIGNALS = {"mixed", "unchanged", "stable", "sideways", "consolidation"}


def get_json(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RECON-BettaFish/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def analyze_sentiment(text: str) -> dict:
    """Simple lexicon-based sentiment scoring (BettaFish MediaEngine equivalent)."""
    text_lower = text.lower()
    words = set(re.findall(r'\b\w+\b', text_lower))

    bull_hits = words & BULLISH_WORDS
    bear_hits = words & BEARISH_WORDS

    bull_count = len(bull_hits)
    bear_count = len(bear_hits)
    total = bull_count + bear_count

    if total == 0:
        return {"score": 0.0, "label": "neutral", "bull": [], "bear": []}

    score = (bull_count - bear_count) / total  # -1.0 to 1.0
    if score > 0.2:
        label = "bullish"
    elif score < -0.2:
        label = "bearish"
    else:
        label = "mixed"

    return {
        "score": round(score, 2),
        "label": label,
        "bull": list(bull_hits),
        "bear": list(bear_hits),
    }


def query_engine():
    """
    QueryEngine equivalent: Extract trending topics and keywords from data sources.
    Reads existing collected data files.
    """
    topics = Counter()
    sources_analyzed = 0

    for src in ["reddit", "twitter", "news", "onchain"]:
        path = RECON_HOME / "data-sources" / src / "latest.md"
        if not path.exists():
            continue
        text = path.read_text()
        sources_analyzed += 1

        # Extract meaningful terms (2+ word phrases from headers/bullets)
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("#") or line.startswith("-"):
                # Clean markdown
                clean = re.sub(r'[#\-*\[\]()]', '', line).strip()
                # Extract capitalized terms (likely entities)
                entities = re.findall(r'\b[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\b', clean)
                for e in entities:
                    if len(e) > 3 and e not in ("The", "This", "That", "With", "From", "About"):
                        topics[e] += 1

    # Top trending topics
    return {
        "topics": topics.most_common(20),
        "sources_analyzed": sources_analyzed,
    }


def media_engine():
    """
    MediaEngine equivalent: Analyze sentiment across all collected media.
    Returns per-source sentiment breakdown.
    """
    results = {}

    for src in ["reddit", "twitter", "news"]:
        path = RECON_HOME / "data-sources" / src / "latest.md"
        if not path.exists():
            continue

        text = path.read_text()
        lines = [l.strip() for l in text.split("\n") if l.strip().startswith("-")]

        sentiments = []
        for line in lines:
            s = analyze_sentiment(line)
            sentiments.append(s)

        if sentiments:
            avg_score = sum(s["score"] for s in sentiments) / len(sentiments)
            bull_count = sum(1 for s in sentiments if s["label"] == "bullish")
            bear_count = sum(1 for s in sentiments if s["label"] == "bearish")
            neutral_count = sum(1 for s in sentiments if s["label"] in ("neutral", "mixed"))

            results[src] = {
                "avg_score": round(avg_score, 3),
                "total_items": len(sentiments),
                "bullish": bull_count,
                "bearish": bear_count,
                "neutral": neutral_count,
                "label": "bullish" if avg_score > 0.1 else ("bearish" if avg_score < -0.1 else "neutral"),
            }

    return results


def insight_engine():
    """
    InsightEngine equivalent: Detect trends and anomalies.
    Uses Fear & Greed, price changes, and volume data.
    """
    insights = []

    # Fear & Greed
    fng = get_json("https://api.alternative.me/fng/?limit=7")
    if fng:
        data = fng.get("data", [])
        if data:
            current = int(data[0].get("value", 50))
            classification = data[0].get("value_classification", "?")
            insights.append(f"Fear & Greed: {current}/100 ({classification})")

            if len(data) >= 7:
                week_avg = sum(int(d.get("value", 50)) for d in data) / len(data)
                trend = "improving" if current > week_avg else "deteriorating"
                insights.append(f"7-day F&G average: {week_avg:.0f} — trend: {trend}")

            if current <= 20:
                insights.append("⚠ EXTREME FEAR: historically a contrarian buy signal")
            elif current >= 80:
                insights.append("⚠ EXTREME GREED: historically signals overheating")

    # Price momentum from CoinGecko
    prices = get_json(
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=bitcoin,ethereum,solana"
        "&vs_currencies=usd&include_24hr_change=true"
    )
    if prices:
        for coin in ["bitcoin", "ethereum", "solana"]:
            data = prices.get(coin, {})
            change = data.get("usd_24h_change", 0) or 0
            if abs(change) > 5:
                direction = "surging" if change > 0 else "plunging"
                insights.append(f"⚠ {coin.upper()} {direction}: {change:+.1f}% in 24h")

    # DEX volume anomaly check
    dex = get_json(
        "https://api.llama.fi/overview/dexs"
        "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
    )
    if dex and "_error" not in dex:
        change_7d = dex.get("change_7d")
        if change_7d and abs(change_7d) > 30:
            direction = "spiking" if change_7d > 0 else "collapsing"
            insights.append(f"⚠ DEX volume {direction}: {change_7d:+.1f}% 7d change")

    return insights


def generate_report(query_data, media_data, insight_data):
    """ReportEngine equivalent: Assemble the sentiment report."""
    now = datetime.now(timezone.utc)
    lines = [
        f"# BettaFish Sentiment Report",
        f"## {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"## Adapted from 666ghj/BettaFish for English crypto sources",
        "",
    ]

    # Overall sentiment
    all_scores = [v["avg_score"] for v in media_data.values()]
    if all_scores:
        overall = sum(all_scores) / len(all_scores)
        if overall > 0.15:
            overall_label = "BULLISH"
        elif overall < -0.15:
            overall_label = "BEARISH"
        elif overall > 0.05:
            overall_label = "SLIGHTLY BULLISH"
        elif overall < -0.05:
            overall_label = "SLIGHTLY BEARISH"
        else:
            overall_label = "NEUTRAL"
        lines.append(f"## OVERALL SENTIMENT: {overall_label} ({overall:+.3f})")
    else:
        lines.append("## OVERALL SENTIMENT: INSUFFICIENT DATA")
    lines.append("")

    # Insights (anomalies + trends)
    lines.append("## KEY INSIGHTS\n")
    if insight_data:
        for insight in insight_data:
            lines.append(f"- {insight}")
    else:
        lines.append("- No significant anomalies detected")
    lines.append("")

    # Per-source sentiment breakdown
    lines.append("## SENTIMENT BY SOURCE\n")
    for src, data in media_data.items():
        pct_bull = (data["bullish"] / data["total_items"] * 100) if data["total_items"] else 0
        pct_bear = (data["bearish"] / data["total_items"] * 100) if data["total_items"] else 0
        lines.append(f"### {src.upper()}")
        lines.append(f"- Sentiment: {data['label'].upper()} (score: {data['avg_score']:+.3f})")
        lines.append(f"- Items analyzed: {data['total_items']}")
        lines.append(f"- Bullish: {data['bullish']} ({pct_bull:.0f}%) | Bearish: {data['bearish']} ({pct_bear:.0f}%) | Neutral: {data['neutral']}")
        lines.append("")

    # Trending topics
    lines.append("## TRENDING TOPICS\n")
    if query_data["topics"]:
        for topic, count in query_data["topics"][:15]:
            lines.append(f"- {topic} (mentioned {count}x)")
    else:
        lines.append("- No trending topics detected")
    lines.append("")

    # Metadata
    lines.append(f"---\n*Sources analyzed: {query_data['sources_analyzed']} | Generated: {now.strftime('%H:%M UTC')}*")

    return "\n".join(lines)


def main():
    print("BettaFish: Running sentiment analysis...")

    # Phase 1: QueryEngine — extract topics
    print("  QueryEngine: extracting topics...")
    query_data = query_engine()
    print(f"  QueryEngine: {len(query_data['topics'])} topics from {query_data['sources_analyzed']} sources")

    # Phase 2: MediaEngine — sentiment analysis
    print("  MediaEngine: analyzing sentiment...")
    media_data = media_engine()
    for src, data in media_data.items():
        print(f"  MediaEngine: {src} = {data['label']} ({data['avg_score']:+.3f})")

    # Phase 3: InsightEngine — trends and anomalies
    print("  InsightEngine: detecting trends...")
    insight_data = insight_engine()
    print(f"  InsightEngine: {len(insight_data)} insights")

    # Phase 4: ReportEngine — generate report
    print("  ReportEngine: generating report...")
    report = generate_report(query_data, media_data, insight_data)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(report)
    print(f"BettaFish: Report written ({len(report.split(chr(10)))} lines)")


if __name__ == "__main__":
    main()
