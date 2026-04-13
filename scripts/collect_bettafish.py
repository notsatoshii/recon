#!/usr/bin/env python3
"""
BettaFish English Adaptation — Multi-Agent Sentiment Analysis
Adapted from 666ghj/BettaFish (微舆) for English crypto/DeFi/prediction market sources.

Architecture:
  1. Ingests raw social + news data (Reddit, Twitter, News) collected by collect_data.sh
  2. QueryEngine: Topic extraction + frequency analysis
  3. InsightEngine: Quantitative anomaly detection (Fear&Greed, volume spikes, price moves)
  4. MediaEngine: Claude-powered deep sentiment analysis on the social/news text
  5. ReportEngine: Structured sentiment intelligence report

Output: /home/recon/recon/data-sources/bettafish/latest.md
"""

import json
import os
import re
import subprocess
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

RECON_HOME = Path("/home/recon/recon")
OUTPUT_FILE = RECON_HOME / "data-sources" / "bettafish" / "latest.md"


def get_json(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RECON-BettaFish/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def load_source(name):
    """Load a raw data source file."""
    path = RECON_HOME / "data-sources" / name / "latest.md"
    if path.exists():
        return path.read_text()
    return ""


def query_engine(sources: dict) -> dict:
    """Extract trending topics and key entities from all sources."""
    topics = Counter()
    total_lines = 0

    for name, text in sources.items():
        for line in text.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            total_lines += 1
            # Extract capitalized entities
            entities = re.findall(r'\b[A-Z][A-Za-z]{2,}(?:\s+[A-Z][A-Za-z]+)*\b', line)
            for e in entities:
                if e not in ("The", "This", "That", "With", "From", "About", "NOT", "ERROR",
                             "CONFIGURED", "API", "RSS", "UTC", "COLLECTION", "SUMMARY"):
                    topics[e] += 1

    return {
        "topics": topics.most_common(25),
        "total_lines": total_lines,
        "sources": list(sources.keys()),
    }


def insight_engine() -> list:
    """Quantitative anomaly detection from market APIs."""
    insights = []

    # Fear & Greed
    fng = get_json("https://api.alternative.me/fng/?limit=7")
    if fng and fng.get("data"):
        data = fng["data"]
        current = int(data[0].get("value", 50))
        classification = data[0].get("value_classification", "?")
        insights.append(f"Fear & Greed Index: {current}/100 ({classification})")

        if len(data) >= 7:
            week_avg = sum(int(d.get("value", 50)) for d in data) / len(data)
            trend = "improving" if current > week_avg else "deteriorating"
            insights.append(f"7-day F&G average: {week_avg:.0f} — trend {trend}")

        if current <= 20:
            insights.append("ANOMALY: Extreme Fear — historically a contrarian buy signal")
        elif current >= 80:
            insights.append("ANOMALY: Extreme Greed — historically signals overheating")

    # Price momentum
    prices = get_json(
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true"
    )
    if prices:
        for coin in ["bitcoin", "ethereum", "solana"]:
            d = prices.get(coin, {})
            change = d.get("usd_24h_change", 0) or 0
            if abs(change) > 5:
                direction = "surging" if change > 0 else "plunging"
                insights.append(f"ANOMALY: {coin.upper()} {direction} {change:+.1f}% in 24h")

    # DEX volume anomaly
    dex = get_json("https://api.llama.fi/overview/dexs?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true")
    if dex and "_error" not in dex:
        change_7d = dex.get("change_7d")
        if change_7d and abs(change_7d) > 25:
            direction = "spiking" if change_7d > 0 else "collapsing"
            insights.append(f"ANOMALY: DEX volume {direction} {change_7d:+.1f}% over 7 days")

    return insights


def media_engine_llm(sources: dict) -> str:
    """
    Deep sentiment analysis using Claude.
    Reads Reddit + Twitter + News text and produces structured sentiment assessment.
    """
    # Combine social/news text (truncate to fit in context)
    combined = ""
    for name in ["reddit", "twitter", "news"]:
        text = sources.get(name, "")
        if text:
            combined += f"\n--- {name.upper()} ---\n{text[:15000]}\n"

    if not combined.strip() or len(combined) < 100:
        return "INSUFFICIENT DATA: Social and news sources are empty or not configured."

    prompt = f"""You are BettaFish MediaEngine — a sentiment analysis system for crypto markets.

Analyze the following social media and news data. Produce a structured sentiment report covering:

1. OVERALL SENTIMENT: One word (BULLISH / SLIGHTLY_BULLISH / NEUTRAL / SLIGHTLY_BEARISH / BEARISH) with a confidence score (0-100%) and 1-sentence reasoning.

2. NARRATIVE ANALYSIS: What are the top 3-5 narratives people are discussing? For each: narrative name, sentiment (bull/bear/mixed), lifecycle stage (forming/accelerating/peak/dying), and which sources it appears in.

3. DIVERGENCES: Where do different sources disagree? (e.g., Reddit bearish but news bullish on same topic). These are the most valuable signals.

4. OPINION SHIFTS: Anything that feels like a sentiment change from typical crypto discourse? New fears, new excitement, unusual consensus?

5. CONTROVERSY & RISK FLAGS: Anything contentious, divisive, or signaling potential trouble (regulatory, exploits, rugs, scams)?

Be specific. Cite actual headlines/posts. Don't hedge — give clear directional reads.

--- DATA ---
{combined[:25000]}
--- END DATA ---"""

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", "claude-sonnet-4-20250514"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        else:
            return f"LLM ANALYSIS FAILED: {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return "LLM ANALYSIS TIMEOUT: Claude call exceeded 120s"
    except Exception as e:
        return f"LLM ANALYSIS ERROR: {str(e)[:200]}"


def generate_report(query_data: dict, insights: list, llm_analysis: str) -> str:
    """Assemble the final BettaFish sentiment intelligence report."""
    now = datetime.now(timezone.utc)

    lines = [
        f"# BettaFish Sentiment Intelligence",
        f"## {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"## Sources: {', '.join(query_data['sources'])} ({query_data['total_lines']} data points)",
        "",
    ]

    # Quantitative insights first (fast, reliable)
    lines.append("## MARKET SIGNALS (Quantitative)\n")
    if insights:
        for i in insights:
            lines.append(f"- {i}")
    else:
        lines.append("- No significant anomalies detected")
    lines.append("")

    # LLM-powered deep analysis
    lines.append("## SENTIMENT ANALYSIS (Claude-powered)\n")
    lines.append(llm_analysis)
    lines.append("")

    # Trending topics (statistical)
    lines.append("## TRENDING TOPICS (frequency analysis)\n")
    if query_data["topics"]:
        for topic, count in query_data["topics"][:20]:
            lines.append(f"- {topic} ({count}x)")
    lines.append("")

    lines.append(f"---\n*BettaFish v2 | {now.strftime('%H:%M UTC')} | Adapted from 666ghj/BettaFish*")
    return "\n".join(lines)


def main():
    print("BettaFish: Loading raw sources...")

    # Load raw social + news data
    sources = {}
    for name in ["reddit", "twitter", "news"]:
        text = load_source(name)
        if text and len(text) > 50:
            sources[name] = text
            print(f"  Loaded {name}: {len(text)} bytes")

    if not sources:
        print("BettaFish: No source data available, writing stub")
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text("# BettaFish Sentiment Intelligence\n## NO DATA\nWaiting for Reddit/Twitter/News collection.\n")
        return

    # Phase 1: QueryEngine — topic extraction
    print("  QueryEngine: extracting topics...")
    query_data = query_engine(sources)
    print(f"  QueryEngine: {len(query_data['topics'])} topics from {query_data['total_lines']} lines")

    # Phase 2: InsightEngine — quantitative anomalies
    print("  InsightEngine: checking market signals...")
    insights = insight_engine()
    print(f"  InsightEngine: {len(insights)} signals")

    # Phase 3: MediaEngine — Claude-powered deep sentiment analysis
    print("  MediaEngine: running LLM sentiment analysis (this takes ~30s)...")
    llm_analysis = media_engine_llm(sources)
    print(f"  MediaEngine: {len(llm_analysis)} chars of analysis")

    # Phase 4: ReportEngine — assemble report
    print("  ReportEngine: generating report...")
    report = generate_report(query_data, insights, llm_analysis)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(report)
    print(f"BettaFish: Report written ({len(report.split(chr(10)))} lines)")


if __name__ == "__main__":
    main()
