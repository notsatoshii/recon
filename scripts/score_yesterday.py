#!/usr/bin/env python3
"""
RECON Prediction Scorer

Extracts predictions from agent state files and yesterday's brief,
then uses today's data to score them. Outputs a scorecard that agents
read before making today's takes — closing the feedback loop.

Called by run_recon.sh PHASE -1 (before data collection).
"""

import os
import re
import json
import sqlite3
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

RECON_HOME = Path("/home/recon/recon")
STATE_DIR = RECON_HOME / "config" / "agent_state"
MEMORY_DIR = RECON_HOME / "config" / "agent_memory"
ARCHIVE_DIR = RECON_HOME / "archive"
KNOWLEDGE_DB = RECON_HOME / "config" / "knowledge.db"

TODAY = datetime.now().strftime("%Y-%m-%d")
YESTERDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def get_market_snapshot():
    """Fetch current prices for scoring price predictions."""
    snapshot = {}
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,bnb&vs_currencies=usd"
        req = urllib.request.Request(url, headers={"User-Agent": "RECON/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        for coin, vals in data.items():
            snapshot[coin] = vals.get("usd", 0)
    except Exception as e:
        print(f"  Warning: couldn't fetch prices: {e}")
    return snapshot


def get_fear_greed():
    """Fetch current Fear & Greed index."""
    try:
        url = "https://api.alternative.me/fng/?limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "RECON/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        return int(data["data"][0]["value"])
    except Exception:
        return None


def extract_predictions_from_state(agent: str, state_path: Path) -> list:
    """Extract predictions from agent state files."""
    predictions = []
    if not state_path.exists():
        return predictions

    content = state_path.read_text()
    # Look for PREDICTIONS lines in state entries
    for match in re.finditer(
        r"###\s+(\d{4}-\d{2}-\d{2})\n.*?PREDICTIONS?:\s*(.+?)(?:\n- |\n###|\Z)",
        content, re.DOTALL
    ):
        date = match.group(1)
        pred_text = match.group(2).strip()
        if pred_text and pred_text != "none" and pred_text != "None":
            predictions.append({
                "agent": agent,
                "date": date,
                "prediction": pred_text,
            })

    return predictions


def extract_predictions_from_memory(agent: str, memory_path: Path) -> list:
    """Extract predictions from agent memory files."""
    predictions = []
    if not memory_path.exists():
        return predictions

    content = memory_path.read_text()
    # Look for "Prior Predictions" section
    pred_section = re.search(
        r"### Prior Predictions\n(.*?)(?:\n###|\Z)", content, re.DOTALL
    )
    if not pred_section:
        return predictions

    for line in pred_section.group(1).strip().split("\n"):
        line = line.strip()
        if line.startswith("-") and "pending" in line.lower():
            # Extract date and prediction text
            match = re.match(r"-\s*\[?(\d{4}-\d{2}-\d{2})?\]?\s*(.+)", line)
            if match:
                date = match.group(1) or "unknown"
                pred = match.group(2).strip()
                # Remove status tags
                pred = re.sub(r"\[status:.*?\]", "", pred).strip()
                if pred:
                    predictions.append({
                        "agent": agent,
                        "date": date,
                        "prediction": pred,
                    })

    return predictions


def get_yesterday_brief() -> str:
    """Load yesterday's brief for context."""
    brief_path = ARCHIVE_DIR / YESTERDAY / "brief.md"
    if brief_path.exists():
        return brief_path.read_text()[:5000]
    return ""


def build_scorecard(predictions: list, market: dict, fng: int | None) -> str:
    """Build the scorecard markdown."""
    lines = [
        f"# PREDICTION SCORECARD",
        f"## Scored: {TODAY} (predictions from prior sessions)",
        "",
    ]

    if not predictions:
        lines.append("No testable predictions found in agent state/memory files.")
        lines.append("This is expected on first run — agents will start making predictions today.")
        lines.append("")
        lines.append("## Current Market Snapshot (for agent reference)")
        if market:
            for coin, price in sorted(market.items()):
                lines.append(f"- {coin}: ${price:,.2f}")
        if fng is not None:
            lines.append(f"- Fear & Greed: {fng}/100")
        return "\n".join(lines)

    # Market context for scoring
    lines.append("## Market Snapshot")
    if market:
        for coin, price in sorted(market.items()):
            lines.append(f"- {coin}: ${price:,.2f}")
    if fng is not None:
        lines.append(f"- Fear & Greed: {fng}/100")
    lines.append("")

    # Group predictions by agent
    by_agent = {}
    for p in predictions:
        by_agent.setdefault(p["agent"], []).append(p)

    lines.append("## Pending Predictions")
    lines.append("*Agents: review your predictions below. Note which were confirmed, wrong, or still pending.*")
    lines.append("")

    for agent, preds in sorted(by_agent.items()):
        lines.append(f"### {agent.upper()}")
        for p in preds:
            lines.append(f"- [{p['date']}] {p['prediction']}")
        lines.append("")

    # Summary stats
    total = len(predictions)
    agents_with_preds = len(by_agent)
    lines.append(f"---")
    lines.append(f"**Total predictions tracked: {total} from {agents_with_preds} agents**")

    return "\n".join(lines)


def main():
    print(f"Scoring predictions for {TODAY} (looking at state from {YESTERDAY} and earlier)")

    # Collect predictions from all sources
    all_predictions = []

    # 1. Agent state files
    agents = [
        "trader", "narrator", "builder", "analyst", "skeptic",
        "policy_analyst", "user", "macro_strategist"
    ]
    for agent in agents:
        state_file = STATE_DIR / f"{agent}_state.md"
        preds = extract_predictions_from_state(agent, state_file)
        all_predictions.extend(preds)

    # 2. Agent memory files (legacy format)
    memory_agents = [
        "trader", "narrator", "builder", "analyst", "skeptic",
        "policy_analyst", "user_agent", "macro_strategist"
    ]
    for agent in memory_agents:
        memory_file = MEMORY_DIR / f"{agent}.md"
        preds = extract_predictions_from_memory(agent, memory_file)
        all_predictions.extend(preds)

    print(f"  Found {len(all_predictions)} predictions across state/memory files")

    # Get current market data for scoring context
    market = get_market_snapshot()
    fng = get_fear_greed()

    if market:
        print(f"  Market snapshot: BTC=${market.get('bitcoin', 0):,.0f} ETH=${market.get('ethereum', 0):,.0f}")

    # Build and write scorecard
    scorecard = build_scorecard(all_predictions, market, fng)

    brief_dir = RECON_HOME / "briefs" / TODAY
    brief_dir.mkdir(parents=True, exist_ok=True)
    scorecard_path = brief_dir / "00_scorecard.md"
    scorecard_path.write_text(scorecard)
    print(f"  Scorecard written to {scorecard_path}")


if __name__ == "__main__":
    main()
