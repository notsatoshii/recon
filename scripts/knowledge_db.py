#!/usr/bin/env python3
"""
RECON Knowledge Database
SQLite with FTS5 for historical querying across all past runs.

Stores:
- Daily briefs and debate records
- Agent takes and votes
- Data source snapshots
- BettaFish sentiment scores
- Cross-source signals

Agents can query: "What did we say about Polymarket volume 2 weeks ago?"
                   "When was the last time Fear & Greed was below 20?"
                   "What risks did the Skeptic flag that actually materialized?"

Usage:
    # Index today's run
    python3 scripts/knowledge_db.py index <briefs_dir>

    # Query the knowledge base
    python3 scripts/knowledge_db.py query "prediction market volume decline"

    # Generate context for agents (recent history summary)
    python3 scripts/knowledge_db.py context --days 7
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RECON_HOME = Path("/home/recon/recon")
DB_PATH = RECON_HOME / "config" / "knowledge.db"


def get_db():
    """Get database connection, creating tables if needed."""
    db = sqlite3.connect(str(DB_PATH))
    db.execute("PRAGMA journal_mode=WAL")

    # Core tables
    db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            date TEXT PRIMARY KEY,
            brief TEXT,
            debate_record TEXT,
            data_package_size INTEGER,
            agent_count INTEGER,
            duration_seconds INTEGER,
            indexed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS agent_takes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            agent TEXT NOT NULL,
            take TEXT NOT NULL,
            vote TEXT,
            UNIQUE(date, agent)
        );

        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            source TEXT,
            content TEXT NOT NULL,
            metadata TEXT
        );

        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL,
            unit TEXT,
            source TEXT,
            UNIQUE(date, metric, source)
        );

        -- FTS5 virtual tables for full-text search
        CREATE VIRTUAL TABLE IF NOT EXISTS briefs_fts USING fts5(
            date, content, tokenize='porter'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS takes_fts USING fts5(
            date, agent, content, tokenize='porter'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS signals_fts USING fts5(
            date, signal_type, source, content, tokenize='porter'
        );
    """)
    return db


def index_run(briefs_dir: str):
    """Index a daily run into the knowledge database."""
    briefs_path = Path(briefs_dir)
    date = briefs_path.name  # Expected format: YYYY-MM-DD

    db = get_db()

    # Check if already indexed
    existing = db.execute("SELECT date FROM runs WHERE date=?", (date,)).fetchone()
    if existing:
        print(f"  Already indexed: {date}")
        return

    print(f"  Indexing run: {date}")

    # Index brief
    brief_file = briefs_path / "07_daily_brief.md"
    brief_text = brief_file.read_text() if brief_file.exists() else ""

    # Index debate record
    debate_file = briefs_path / "07_full_record.md"
    debate_text = debate_file.read_text() if debate_file.exists() else ""

    # Index data package size
    pkg_file = briefs_path / "00_data_package.md"
    pkg_size = pkg_file.stat().st_size if pkg_file.exists() else 0

    # Count agents from take files
    take_files = list(briefs_path.glob("03_take_*.md"))
    agent_count = len(take_files)

    db.execute(
        "INSERT OR REPLACE INTO runs (date, brief, debate_record, data_package_size, agent_count, indexed_at) VALUES (?,?,?,?,?,?)",
        (date, brief_text, debate_text, pkg_size, agent_count, datetime.now(timezone.utc).isoformat())
    )

    # Index into FTS
    db.execute("DELETE FROM briefs_fts WHERE date=?", (date,))
    if brief_text:
        db.execute("INSERT INTO briefs_fts (date, content) VALUES (?,?)", (date, brief_text))

    # Index agent takes
    for take_file in take_files:
        agent = take_file.stem.replace("03_take_", "")
        take_text = take_file.read_text()

        # Load vote if exists
        vote_file = briefs_path / f"06_vote_{agent}.md"
        vote_text = vote_file.read_text() if vote_file.exists() else ""

        db.execute(
            "INSERT OR REPLACE INTO agent_takes (date, agent, take, vote) VALUES (?,?,?,?)",
            (date, agent, take_text, vote_text)
        )

        db.execute("DELETE FROM takes_fts WHERE date=? AND agent=?", (date, agent))
        db.execute(
            "INSERT INTO takes_fts (date, agent, content) VALUES (?,?,?)",
            (date, agent, take_text + "\n" + vote_text)
        )

    # Extract and index key metrics from data package
    if pkg_file.exists():
        pkg_text = pkg_file.read_text()
        extract_metrics(db, date, pkg_text)

    # Index cross-source signals from dedup report
    dedup_file = briefs_path / "00_dedup_report.md"
    if dedup_file.exists():
        dedup_text = dedup_file.read_text()
        for line in dedup_text.split("\n"):
            if line.startswith("- ") and "Also in:" in dedup_text:
                db.execute(
                    "INSERT INTO signals (date, signal_type, source, content) VALUES (?,?,?,?)",
                    (date, "cross_source", "dedup", line[2:])
                )

    db.commit()
    db.close()

    stats = f"brief={len(brief_text)}b, agents={agent_count}, debate={len(debate_text)}b"
    print(f"  Indexed: {date} ({stats})")


def extract_metrics(db, date, text):
    """Extract quantitative metrics from data package text."""
    import re

    patterns = [
        (r"Fear & Greed Index: (\d+)/100", "fear_greed_index", "", "alternative.me"),
        (r"Total crypto market cap: \$([0-9,]+)", "total_crypto_mcap", "USD", "coingecko"),
        (r"BTC dominance: ([0-9.]+)%", "btc_dominance", "%", "coingecko"),
        (r"BITCOIN: \$([0-9,.]+)", "btc_price", "USD", "coingecko"),
        (r"ETHEREUM: \$([0-9,.]+)", "eth_price", "USD", "coingecko"),
        (r"Total 24h DEX volume: \$([0-9,]+)", "dex_volume_24h", "USD", "defillama"),
        (r"Polymarket.*TVL: \$([0-9,]+)", "polymarket_tvl", "USD", "defillama"),
    ]

    for pattern, metric, unit, source in patterns:
        match = re.search(pattern, text)
        if match:
            value_str = match.group(1).replace(",", "")
            try:
                value = float(value_str)
                db.execute(
                    "INSERT OR REPLACE INTO metrics (date, metric, value, unit, source) VALUES (?,?,?,?,?)",
                    (date, metric, value, unit, source)
                )
            except ValueError:
                pass


def query(search_term: str, limit: int = 10):
    """Search the knowledge base."""
    db = get_db()

    print(f"\n=== Knowledge Base: \"{search_term}\" ===\n")

    # Search briefs
    results = db.execute(
        "SELECT date, snippet(briefs_fts, 1, '>>>', '<<<', '...', 50) FROM briefs_fts WHERE content MATCH ? ORDER BY rank LIMIT ?",
        (search_term, limit)
    ).fetchall()

    if results:
        print("BRIEFS:")
        for date, snippet in results:
            print(f"  [{date}] {snippet}")
        print()

    # Search agent takes
    results = db.execute(
        "SELECT date, agent, snippet(takes_fts, 2, '>>>', '<<<', '...', 50) FROM takes_fts WHERE content MATCH ? ORDER BY rank LIMIT ?",
        (search_term, limit)
    ).fetchall()

    if results:
        print("AGENT TAKES:")
        for date, agent, snippet in results:
            print(f"  [{date}] {agent}: {snippet}")
        print()

    # Search metrics
    results = db.execute(
        "SELECT date, metric, value, unit FROM metrics WHERE metric LIKE ? ORDER BY date DESC LIMIT ?",
        (f"%{search_term}%", limit)
    ).fetchall()

    if results:
        print("METRICS:")
        for date, metric, value, unit in results:
            print(f"  [{date}] {metric}: {value} {unit}")

    db.close()


def generate_context(days: int = 7):
    """Generate a historical context summary for agents."""
    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    lines = [f"# Historical Context (last {days} days)\n"]

    # Recent briefs (executive summaries only)
    runs = db.execute(
        "SELECT date, brief FROM runs WHERE date >= ? ORDER BY date DESC",
        (cutoff,)
    ).fetchall()

    if runs:
        lines.append("## Recent Briefs\n")
        for date, brief in runs:
            # Extract just the executive summary
            if "EXECUTIVE SUMMARY" in brief:
                start = brief.index("EXECUTIVE SUMMARY")
                # Find next ### heading
                rest = brief[start:]
                end = rest.find("\n###", 20)
                summary = rest[:end] if end > 0 else rest[:500]
                lines.append(f"### {date}")
                lines.append(summary.strip())
                lines.append("")

    # Key metrics trend
    lines.append("## Metric Trends\n")
    for metric in ["fear_greed_index", "btc_price", "dex_volume_24h", "polymarket_tvl"]:
        vals = db.execute(
            "SELECT date, value FROM metrics WHERE metric=? AND date >= ? ORDER BY date",
            (metric, cutoff)
        ).fetchall()
        if vals:
            first = vals[0][1]
            last = vals[-1][1]
            change = ((last - first) / first * 100) if first else 0
            lines.append(f"- {metric}: {first:,.0f} → {last:,.0f} ({change:+.1f}% over {len(vals)} days)")
    lines.append("")

    db.close()
    return "\n".join(lines)


def index_all():
    """Index all existing runs that haven't been indexed yet."""
    briefs_dir = RECON_HOME / "briefs"
    archive_dir = RECON_HOME / "archive"

    count = 0
    for d in sorted(briefs_dir.iterdir()):
        if d.is_dir() and (d / "07_daily_brief.md").exists():
            index_run(str(d))
            count += 1

    for d in sorted(archive_dir.iterdir()):
        if d.is_dir() and (d / "brief.md").exists():
            # Create a temporary briefs-like structure for indexing
            pass  # Archive has different structure, skip for now

    print(f"\nTotal indexed: {count} runs")


def main():
    parser = argparse.ArgumentParser(description="RECON Knowledge Database")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Initialize database")
    idx = sub.add_parser("index", help="Index a run")
    idx.add_argument("path", nargs="?", help="Path to briefs directory")
    idx.add_argument("--all", action="store_true", help="Index all existing runs")

    q = sub.add_parser("query", help="Search knowledge base")
    q.add_argument("term", help="Search term")
    q.add_argument("--limit", type=int, default=10)

    ctx = sub.add_parser("context", help="Generate historical context")
    ctx.add_argument("--days", type=int, default=7)

    sub.add_parser("stats", help="Show database statistics")

    args = parser.parse_args()

    if args.command == "init":
        get_db().close()
        print(f"Database initialized: {DB_PATH}")

    elif args.command == "index":
        if args.all:
            index_all()
        elif args.path:
            index_run(args.path)
        else:
            print("Specify a path or use --all")

    elif args.command == "query":
        query(args.term, args.limit)

    elif args.command == "context":
        print(generate_context(args.days))

    elif args.command == "stats":
        db = get_db()
        runs = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        takes = db.execute("SELECT COUNT(*) FROM agent_takes").fetchone()[0]
        metrics = db.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        signals = db.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        print(f"Runs: {runs} | Takes: {takes} | Metrics: {metrics} | Signals: {signals}")
        db.close()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
