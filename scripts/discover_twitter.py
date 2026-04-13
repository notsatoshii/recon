#!/usr/bin/env python3
"""
RECON Twitter/X Account Discovery

Takes seed accounts and maps their social graphs to find new high-signal accounts.
Methods:
  1. Retweet mining: Who do seeds RT? High-RT targets = high-signal accounts.
  2. Reply mining: Who replies to seeds with high engagement? Rising voices.
  3. List discovery: What public lists are seeds on? Pull all members.
  4. Following overlap: Who do multiple seeds follow? Consensus picks.

Usage:
    python3 scripts/discover_twitter.py                    # Full discovery
    python3 scripts/discover_twitter.py --method retweets  # Just retweet mining
    python3 scripts/discover_twitter.py --method lists     # Just list discovery
    python3 scripts/discover_twitter.py --method following  # Following overlap
    python3 scripts/discover_twitter.py --dry-run          # Show what would run

Output:
    config/discovered_accounts.yaml   — Ranked accounts by discovery method
    config/discovered_accounts.csv    — Flat CSV for review

Requires: twscrape with at least one logged-in account.
    python3 scripts/collect_twitter.py --add-account USERNAME PASSWORD
"""

import asyncio
import argparse
import json
import sys
import yaml
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

RECON_HOME = Path("/home/recon/recon")
SEEDS_FILE = RECON_HOME / "config" / "twitter_seeds.yaml"
OUTPUT_YAML = RECON_HOME / "config" / "discovered_accounts.yaml"
OUTPUT_CSV = RECON_HOME / "config" / "discovered_accounts.csv"
DB_PATH = Path("/home/recon/.recon_twscrape.db")

# Accounts to never recommend (bots, aggregators with no signal)
BLOCKLIST = {"elikibazo", "crypto_banter", "whale_alert"}

# How many tweets to scan per seed for retweet/reply mining
TWEETS_PER_SEED = 50
# How many accounts to follow per seed for following overlap
FOLLOWING_PER_SEED = 200


def load_seeds() -> dict:
    with open(SEEDS_FILE) as f:
        data = yaml.safe_load(f)
    return data


def all_seed_handles(seeds: dict) -> set:
    """Flatten all seed handles into a set (lowercased)."""
    handles = set()
    for cat, accts in seeds.items():
        if isinstance(accts, list):
            for h in accts:
                handles.add(h.strip().lower())
    return handles


async def mine_retweets(api, seeds: dict, existing: set) -> Counter:
    """
    For each seed, scan recent tweets. Extract accounts they RT.
    Accounts RTed by multiple seeds = high signal.
    """
    rt_counts = Counter()  # handle -> count of seeds that RT them
    rt_engagement = defaultdict(int)  # handle -> total likes on RTs

    for cat, handles in seeds.items():
        if not isinstance(handles, list):
            continue
        for handle in handles:
            print(f"  [RT] Scanning @{handle}...")
            try:
                count = 0
                async for tweet in api.user_tweets(handle, limit=TWEETS_PER_SEED):
                    if tweet.rawContent and tweet.rawContent.startswith("RT @"):
                        # Extract RTed handle
                        rt_text = tweet.rawContent[4:]
                        if ":" in rt_text:
                            rt_handle = rt_text.split(":")[0].strip().lower()
                            if rt_handle not in existing and rt_handle not in BLOCKLIST:
                                rt_counts[rt_handle] += 1
                                rt_engagement[rt_handle] += tweet.likeCount or 0
                    count += 1
                    if count >= TWEETS_PER_SEED:
                        break
            except Exception as e:
                print(f"    WARN: @{handle}: {str(e)[:60]}")

    # Combine: score = (seeds that RT) * 10 + log(engagement)
    import math
    scored = {}
    for handle, count in rt_counts.items():
        eng = rt_engagement.get(handle, 0)
        scored[handle] = count * 10 + (math.log10(eng + 1) * 2)

    return Counter(scored)


async def mine_replies(api, seeds: dict, existing: set) -> Counter:
    """
    Search for replies to seed accounts. High-engagement replies
    from non-seeds = rising voices worth following.
    """
    reply_scores = Counter()

    for cat, handles in seeds.items():
        if not isinstance(handles, list):
            continue
        for handle in handles[:5]:  # Limit to top 5 per category to stay under rate limits
            print(f"  [REPLY] Scanning replies to @{handle}...")
            try:
                count = 0
                async for tweet in api.search(f"to:{handle}", limit=30):
                    if tweet.user and tweet.user.username:
                        replier = tweet.user.username.lower()
                        if replier not in existing and replier not in BLOCKLIST:
                            likes = tweet.likeCount or 0
                            if likes >= 5:  # Only count replies with some engagement
                                reply_scores[replier] += likes
                    count += 1
                    if count >= 30:
                        break
            except Exception as e:
                print(f"    WARN: replies to @{handle}: {str(e)[:60]}")

    return reply_scores


async def mine_lists(api, seeds: dict, existing: set) -> Counter:
    """
    For each seed, find public lists they're on, then pull all members.
    Accounts appearing on multiple lists = vetted by domain experts.
    """
    list_members = Counter()
    lists_found = 0

    for cat, handles in seeds.items():
        if not isinstance(handles, list):
            continue
        for handle in handles[:8]:  # Top 8 per category
            print(f"  [LIST] Finding lists for @{handle}...")
            try:
                # Get user ID first
                user = await api.user_by_login(handle)
                if not user:
                    continue

                # Get lists the user is a member of
                count = 0
                async for lst in api.user_lists(user.id, limit=10):
                    lists_found += 1
                    list_name = lst.name if hasattr(lst, 'name') else 'unknown'
                    member_count = lst.memberCount if hasattr(lst, 'memberCount') else 0
                    print(f"    List: \"{list_name}\" ({member_count} members)")

                    # Pull members of this list
                    try:
                        mc = 0
                        async for member in api.list_members(lst.id, limit=100):
                            if member.username:
                                mh = member.username.lower()
                                if mh not in existing and mh not in BLOCKLIST:
                                    list_members[mh] += 1
                            mc += 1
                            if mc >= 100:
                                break
                    except Exception:
                        pass  # Some lists are private

                    count += 1
                    if count >= 10:
                        break
            except Exception as e:
                err = str(e)[:60]
                if "not found" not in err.lower():
                    print(f"    WARN: @{handle} lists: {err}")

    print(f"  [LIST] Found {lists_found} lists, {len(list_members)} unique accounts")
    return list_members


async def mine_following_overlap(api, seeds: dict, existing: set) -> Counter:
    """
    Pull who each seed follows. Accounts followed by 3+ seeds = consensus picks.
    """
    following_counts = Counter()

    for cat, handles in seeds.items():
        if not isinstance(handles, list):
            continue
        for handle in handles[:5]:  # Top 5 per category
            print(f"  [FOLLOW] Pulling following for @{handle}...")
            try:
                user = await api.user_by_login(handle)
                if not user:
                    continue
                count = 0
                async for followed in api.following(user.id, limit=FOLLOWING_PER_SEED):
                    if followed.username:
                        fh = followed.username.lower()
                        if fh not in existing and fh not in BLOCKLIST:
                            following_counts[fh] += 1
                    count += 1
                    if count >= FOLLOWING_PER_SEED:
                        break
            except Exception as e:
                print(f"    WARN: @{handle} following: {str(e)[:60]}")

    # Only keep accounts followed by 3+ seeds
    return Counter({h: c for h, c in following_counts.items() if c >= 3})


def categorize_discovered(rt_scores, reply_scores, list_scores, follow_scores) -> dict:
    """
    Merge all discovery methods, rank, and output structured results.
    """
    all_accounts = defaultdict(lambda: {
        "score": 0,
        "methods": [],
        "rt_seeds": 0,
        "reply_engagement": 0,
        "list_appearances": 0,
        "follow_overlap": 0,
    })

    for handle, score in rt_scores.items():
        all_accounts[handle]["score"] += score
        all_accounts[handle]["methods"].append("retweet")
        all_accounts[handle]["rt_seeds"] = rt_scores[handle]

    for handle, score in reply_scores.items():
        all_accounts[handle]["score"] += score * 0.5  # Weight replies lower
        all_accounts[handle]["methods"].append("reply")
        all_accounts[handle]["reply_engagement"] = score

    for handle, count in list_scores.items():
        all_accounts[handle]["score"] += count * 15  # Lists are high signal
        all_accounts[handle]["methods"].append("list")
        all_accounts[handle]["list_appearances"] = count

    for handle, count in follow_scores.items():
        all_accounts[handle]["score"] += count * 8  # Following overlap is solid
        all_accounts[handle]["methods"].append("following")
        all_accounts[handle]["follow_overlap"] = count

    # Sort by score descending
    ranked = sorted(all_accounts.items(), key=lambda x: x[1]["score"], reverse=True)
    return dict(ranked[:200])  # Top 200


def write_outputs(discovered: dict, seeds: dict):
    """Write discovered accounts to YAML and CSV."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # YAML output (structured, human-reviewable)
    yaml_data = {
        "metadata": {
            "generated": now,
            "seed_count": sum(len(v) for v in seeds.values() if isinstance(v, list)),
            "discovered_count": len(discovered),
            "note": "Review these and add the best to config/twitter_seeds.yaml",
        },
        "tier_1_high_signal": {},
        "tier_2_medium_signal": {},
        "tier_3_worth_watching": {},
    }

    for handle, info in discovered.items():
        entry = {
            "score": round(info["score"], 1),
            "found_via": info["methods"],
        }
        if info["list_appearances"]:
            entry["on_lists"] = info["list_appearances"]
        if info["follow_overlap"]:
            entry["followed_by_seeds"] = info["follow_overlap"]
        if info["rt_seeds"]:
            entry["retweeted_by_seeds"] = info["rt_seeds"]

        if info["score"] >= 50:
            yaml_data["tier_1_high_signal"][handle] = entry
        elif info["score"] >= 20:
            yaml_data["tier_2_medium_signal"][handle] = entry
        else:
            yaml_data["tier_3_worth_watching"][handle] = entry

    with open(OUTPUT_YAML, "w") as f:
        yaml.dump(yaml_data, f, default_flow_style=False, sort_keys=False)

    # CSV output (for spreadsheet review)
    with open(OUTPUT_CSV, "w") as f:
        f.write("handle,score,methods,list_appearances,follow_overlap,rt_seeds,reply_engagement\n")
        for handle, info in discovered.items():
            methods = "+".join(info["methods"])
            f.write(f"@{handle},{info['score']:.1f},{methods},"
                    f"{info['list_appearances']},{info['follow_overlap']},"
                    f"{info['rt_seeds']},{info['reply_engagement']}\n")

    t1 = len(yaml_data["tier_1_high_signal"])
    t2 = len(yaml_data["tier_2_medium_signal"])
    t3 = len(yaml_data["tier_3_worth_watching"])
    print(f"\nDiscovery complete:")
    print(f"  Tier 1 (high signal):    {t1} accounts")
    print(f"  Tier 2 (medium signal):  {t2} accounts")
    print(f"  Tier 3 (worth watching): {t3} accounts")
    print(f"  Output: {OUTPUT_YAML}")
    print(f"  Output: {OUTPUT_CSV}")
    print(f"\nReview tier 1, then add to config/twitter_seeds.yaml")


async def main():
    parser = argparse.ArgumentParser(description="RECON Twitter account discovery")
    parser.add_argument("--method", choices=["retweets", "replies", "lists", "following", "all"],
                        default="all", help="Discovery method to run")
    parser.add_argument("--dry-run", action="store_true", help="Show plan without running")
    args = parser.parse_args()

    seeds = load_seeds()
    existing = all_seed_handles(seeds)
    total_seeds = len(existing)

    print(f"RECON Account Discovery")
    print(f"  Seeds: {total_seeds} accounts across {len(seeds)} categories")
    print(f"  Method: {args.method}")
    print()

    if args.dry_run:
        print("DRY RUN — would scan:")
        for cat, handles in seeds.items():
            if isinstance(handles, list):
                print(f"  {cat}: {', '.join(handles[:5])}{'...' if len(handles) > 5 else ''}")
        return

    from twscrape import API
    api = API(str(DB_PATH))

    rt_scores = Counter()
    reply_scores = Counter()
    list_scores = Counter()
    follow_scores = Counter()

    if args.method in ("retweets", "all"):
        print("── PHASE 1: Retweet Mining ──")
        rt_scores = await mine_retweets(api, seeds, existing)
        print(f"  Found {len(rt_scores)} accounts via retweets\n")

    if args.method in ("replies", "all"):
        print("── PHASE 2: Reply Mining ──")
        reply_scores = await mine_replies(api, seeds, existing)
        print(f"  Found {len(reply_scores)} accounts via replies\n")

    if args.method in ("lists", "all"):
        print("── PHASE 3: List Discovery ──")
        list_scores = await mine_lists(api, seeds, existing)
        print(f"  Found {len(list_scores)} accounts via lists\n")

    if args.method in ("following", "all"):
        print("── PHASE 4: Following Overlap ──")
        follow_scores = await mine_following_overlap(api, seeds, existing)
        print(f"  Found {len(follow_scores)} accounts via following overlap\n")

    print("── Merging & Ranking ──")
    discovered = categorize_discovered(rt_scores, reply_scores, list_scores, follow_scores)
    write_outputs(discovered, seeds)


if __name__ == "__main__":
    asyncio.run(main())
