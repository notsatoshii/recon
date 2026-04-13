#!/usr/bin/env python3
"""
RECON Twitter Account Discovery via Playwright + Nitter
Maps social graphs from seed accounts to find new high-signal accounts.

Method: For each seed account, scrape their recent tweets and extract:
  1. Who they retweet (RT mining) — accounts seeds amplify
  2. Who they quote-tweet — accounts seeds engage with
  3. Who they mention — accounts in their network

Accounts discovered by multiple seeds = high signal.

Usage:
    python3 scripts/discover_twitter_pw.py              # Full discovery
    python3 scripts/discover_twitter_pw.py --limit 20   # First 20 seeds only
"""

import asyncio
import argparse
import re
import yaml
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

RECON_HOME = Path("/home/recon/recon")
SEEDS_FILE = RECON_HOME / "config" / "twitter_seeds.yaml"
OUTPUT_YAML = RECON_HOME / "config" / "discovered_accounts.yaml"
OUTPUT_CSV = RECON_HOME / "config" / "discovered_accounts.csv"

NITTER_INSTANCE = "https://nitter.cz"
CF_WAIT = 6
CONCURRENT = 2


def load_seeds() -> dict:
    with open(SEEDS_FILE) as f:
        return yaml.safe_load(f)


def all_seed_handles(seeds: dict) -> set:
    handles = set()
    for cat, accts in seeds.items():
        if isinstance(accts, list):
            for h in accts:
                handles.add(h.strip().lower())
    return handles


async def solve_cf(page):
    content = await page.content()
    if "Just a moment" in content:
        await page.wait_for_timeout(CF_WAIT * 1000)


async def extract_mentions_from_profile(context, handle: str) -> dict:
    """Scrape a profile's tweets and extract all mentioned/RT'd accounts."""
    discovered = Counter()  # handle -> mention count
    rt_targets = Counter()  # handle -> RT count
    page = await context.new_page()

    try:
        await page.goto(f"{NITTER_INSTANCE}/{handle}", wait_until="domcontentloaded", timeout=20000)
        await solve_cf(page)

        content = await page.content()

        # Extract retweet targets from Nitter HTML
        # Nitter shows "RT by @handle" or retweet-header with username
        rt_matches = re.findall(r'class="retweet-header"[^>]*>.*?@(\w+)', content, re.DOTALL)
        for rt in rt_matches:
            rt_targets[rt.lower()] += 1

        # Extract @mentions from tweet text
        mention_matches = re.findall(r'@(\w{1,15})', content)
        for m in mention_matches:
            m_lower = m.lower()
            if m_lower != handle.lower() and len(m_lower) > 2:
                discovered[m_lower] += 1

        # Extract linked usernames from Nitter's HTML structure
        username_links = re.findall(r'href="/(\w+)"[^>]*class="username"', content)
        for u in username_links:
            u_lower = u.lower()
            if u_lower != handle.lower():
                discovered[u_lower] += 1

    except Exception as e:
        if "timeout" not in str(e).lower():
            print(f"  WARN @{handle}: {str(e)[:60]}")
    finally:
        await page.close()

    return {"mentions": discovered, "retweets": rt_targets}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Limit seed accounts to process")
    args = parser.parse_args()

    from playwright.async_api import async_playwright

    seeds = load_seeds()
    existing = all_seed_handles(seeds)
    total_seeds = len(existing)

    # Flatten all handles
    all_handles = []
    for cat, handles in seeds.items():
        if isinstance(handles, list):
            all_handles.extend(handles)

    if args.limit:
        all_handles = all_handles[:args.limit]

    print(f"RECON Twitter Discovery")
    print(f"  Seeds: {len(all_handles)} accounts to scan")
    print(f"  Existing: {total_seeds} in seed list")
    print()

    all_mentions = Counter()
    all_rts = Counter()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )

        # Warm up
        print("  Solving Cloudflare...")
        warmup = await context.new_page()
        await warmup.goto(f"{NITTER_INSTANCE}/Polymarket", wait_until="domcontentloaded", timeout=25000)
        await warmup.wait_for_timeout(CF_WAIT * 1000)
        await warmup.close()

        # Process seeds in batches
        for i in range(0, len(all_handles), CONCURRENT):
            batch = all_handles[i:i + CONCURRENT]
            tasks = [extract_mentions_from_profile(context, h) for h in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for handle, result in zip(batch, results):
                if isinstance(result, Exception):
                    continue
                if result:
                    for acct, count in result["mentions"].items():
                        if acct not in existing:
                            all_mentions[acct] += count
                    for acct, count in result["retweets"].items():
                        if acct not in existing:
                            all_rts[acct] += count

            progress = min(i + CONCURRENT, len(all_handles))
            print(f"  [{progress}/{len(all_handles)}] Scanned {', '.join(batch)}... "
                  f"({len(all_mentions)} mentions, {len(all_rts)} RTs so far)")
            await asyncio.sleep(3)

        await browser.close()

    # Score and rank
    scored = Counter()
    for handle, count in all_mentions.items():
        scored[handle] += count
    for handle, count in all_rts.items():
        scored[handle] += count * 3  # RTs are higher signal

    # Filter out low-signal (only mentioned once)
    scored = Counter({h: s for h, s in scored.items() if s >= 2})

    # Tier the results
    tier1 = [(h, s) for h, s in scored.most_common(200) if s >= 10]
    tier2 = [(h, s) for h, s in scored.most_common(200) if 5 <= s < 10]
    tier3 = [(h, s) for h, s in scored.most_common(200) if 2 <= s < 5]

    print(f"\n=== DISCOVERY RESULTS ===")
    print(f"Tier 1 (score 10+): {len(tier1)} accounts")
    print(f"Tier 2 (score 5-9): {len(tier2)} accounts")
    print(f"Tier 3 (score 2-4): {len(tier3)} accounts")
    print(f"Total unique discovered: {len(scored)}")

    # Write YAML output
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    output = {
        "metadata": {
            "generated": now,
            "seeds_scanned": len(all_handles),
            "total_discovered": len(scored),
            "note": "Add tier 1 accounts to config/twitter_seeds.yaml",
        },
        "tier_1_high_signal": {h: {"score": s, "rt_count": all_rts.get(h, 0), "mention_count": all_mentions.get(h, 0)} for h, s in tier1},
        "tier_2_medium_signal": {h: {"score": s} for h, s in tier2},
        "tier_3_worth_watching": {h: {"score": s} for h, s in tier3[:50]},
    }

    with open(OUTPUT_YAML, "w") as f:
        yaml.dump(output, f, default_flow_style=False, sort_keys=False)

    # Write CSV
    with open(OUTPUT_CSV, "w") as f:
        f.write("handle,score,rt_count,mention_count\n")
        for h, s in scored.most_common(200):
            f.write(f"@{h},{s},{all_rts.get(h,0)},{all_mentions.get(h,0)}\n")

    print(f"\nOutput: {OUTPUT_YAML}")
    print(f"Output: {OUTPUT_CSV}")

    # Print top 30 for immediate review
    print(f"\n=== TOP 30 DISCOVERED ===")
    for h, s in scored.most_common(30):
        rt = all_rts.get(h, 0)
        mn = all_mentions.get(h, 0)
        print(f"  @{h}: score={s} (RT'd {rt}x, mentioned {mn}x)")


if __name__ == "__main__":
    asyncio.run(main())
