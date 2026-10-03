#!/usr/bin/env python3
"""
RECON Deduplication Layer
Detects and merges duplicate stories across data sources.

Same event appears in CoinDesk RSS, CoinTelegraph RSS, Reddit, and Twitter.
Without dedup, agents waste context reading it 4 times.

This script reads the assembled raw data package and produces a deduplicated
version with cross-source annotations.

Usage: python3 scripts/deduplicate.py <input_file> <output_file>
"""

import re
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path


def extract_items(text: str) -> list:
    """Extract individual data items (headlines, posts, bullets) from markdown."""
    items = []
    current_source = "unknown"
    current_section = ""

    for line in text.split("\n"):
        line = line.strip()

        # Track which source/section we're in
        if line.startswith("# SECTION"):
            current_section = line
        elif line.startswith("### ") and not line.startswith("### r/"):
            current_source = line[4:].strip()
        elif line.startswith("### r/"):
            current_source = line[4:].strip()

        # Extract bullet items (the actual content)
        if line.startswith("- ") and len(line) > 20:
            # Clean the line for comparison
            clean = re.sub(r'\[.*?\]', '', line[2:])  # Remove markdown links
            clean = re.sub(r'\(.*?\)', '', clean)  # Remove parenthetical
            clean = re.sub(r'[^\w\s]', '', clean).lower().strip()

            if len(clean) > 15:
                items.append({
                    "raw": line,
                    "clean": clean,
                    "source": current_source,
                    "section": current_section,
                })

    return items


def similarity(a: str, b: str) -> float:
    """Calculate similarity between two cleaned text strings."""
    # Quick keyword overlap check first (fast)
    words_a = set(a.split())
    words_b = set(b.split())
    if len(words_a) < 3 or len(words_b) < 3:
        return 0.0

    overlap = len(words_a & words_b)
    min_len = min(len(words_a), len(words_b))
    keyword_sim = overlap / min_len if min_len > 0 else 0

    # If keyword overlap is promising, do full sequence matching
    if keyword_sim > 0.4:
        return SequenceMatcher(None, a, b).ratio()
    return keyword_sim * 0.5


def deduplicate(items: list, threshold: float = 0.55) -> list:
    """
    Group similar items together. Returns deduplicated list with
    cross-source annotations.
    """
    groups = []  # Each group is a list of similar items
    used = set()

    for i, item in enumerate(items):
        if i in used:
            continue

        group = [item]
        used.add(i)

        for j, other in enumerate(items):
            if j in used or j <= i:
                continue
            if similarity(item["clean"], other["clean"]) >= threshold:
                group.append(other)
                used.add(j)

        groups.append(group)

    return groups


def format_deduplicated(groups: list) -> str:
    """Format deduplicated groups back into markdown."""
    lines = []

    # Separate single items from multi-source items
    multi_source = [g for g in groups if len(g) > 1]
    single_source = [g for g in groups if len(g) == 1]

    if multi_source:
        lines.append("## CROSS-SOURCE SIGNALS (same story, multiple sources)\n")
        lines.append("*These items appeared in multiple data sources — higher signal weight.*\n")
        for group in multi_source:
            # Use the longest version as the primary
            primary = max(group, key=lambda x: len(x["raw"]))
            sources = list(set(g["source"] for g in group))
            lines.append(f"{primary['raw']}")
            lines.append(f"  *Also in: {', '.join(sources)} ({len(group)} sources)*")
        lines.append("")

    # Count total dedup stats
    total_items = sum(len(g) for g in groups)
    unique_items = len(groups)
    dupes_removed = total_items - unique_items

    lines.append(f"\n*Deduplication: {total_items} items → {unique_items} unique ({dupes_removed} duplicates merged)*\n")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 deduplicate.py <input_file> <output_file>")
        sys.exit(1)

    input_file = Path(sys.argv[1])
    output_file = Path(sys.argv[2])

    if not input_file.exists():
        print(f"Input file not found: {input_file}")
        sys.exit(1)

    text = input_file.read_text()
    items = extract_items(text)

    if len(items) < 5:
        print(f"Dedup: only {len(items)} items, skipping")
        # Write empty dedup report
        output_file.write_text("## DEDUPLICATION\n- Too few items to deduplicate\n")
        return

    groups = deduplicate(items)
    report = format_deduplicated(groups)

    output_file.write_text(report)

    total = sum(len(g) for g in groups)
    multi = sum(1 for g in groups if len(g) > 1)
    dupes = total - len(groups)
    print(f"Dedup: {total} items → {len(groups)} unique, {dupes} merged, {multi} cross-source signals")


if __name__ == "__main__":
    main()
