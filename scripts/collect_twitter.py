#!/usr/bin/env python3
"""
RECON Twitter/X Data Collection (No API Key)
Uses twscrape for authenticated scraping via account sessions.

Setup (one-time):
    python3 scripts/collect_twitter.py --add-account USERNAME PASSWORD
    python3 scripts/collect_twitter.py --add-account USERNAME PASSWORD EMAIL EMAIL_PASSWORD

Run:
    python3 scripts/collect_twitter.py
"""

import asyncio
import argparse
import os
import sys
import yaml
from datetime import datetime, timezone, timedelta
from pathlib import Path

RECON_HOME = Path("/home/recon/recon")
SEEDS_FILE = RECON_HOME / "config" / "twitter_seeds.yaml"
OUTPUT_FILE = RECON_HOME / "data-sources" / "twitter" / "latest.md"
DB_PATH = Path("/home/recon/.recon_twscrape.db")
TWEETS_PER_ACCOUNT = 5
MAX_ACCOUNTS_PER_CATEGORY = 15


def load_seeds() -> dict:
    """Load seed accounts from YAML config."""
    if not SEEDS_FILE.exists():
        print(f"ERROR: Seeds file not found: {SEEDS_FILE}")
        sys.exit(1)
    with open(SEEDS_FILE) as f:
        data = yaml.safe_load(f)
    # Deduplicate within each category
    for cat in data:
        if isinstance(data[cat], list):
            seen = set()
            deduped = []
            for handle in data[cat]:
                h = handle.strip().lower()
                if h not in seen:
                    seen.add(h)
                    deduped.append(handle.strip())
            data[cat] = deduped[:MAX_ACCOUNTS_PER_CATEGORY]
    return data


async def add_account(username: str, password: str, email: str = "", email_password: str = ""):
    """Add a scraper account to twscrape pool."""
    from twscrape import AccountsPool
    pool = AccountsPool(str(DB_PATH))
    await pool.add_account(username, password, email, email_password)
    await pool.login_all()
    print(f"Account @{username} added and logged in.")


async def check_accounts():
    """Check status of scraper accounts."""
    from twscrape import AccountsPool
    pool = AccountsPool(str(DB_PATH))
    accounts = await pool.accounts_info()
    if not accounts:
        print("No scraper accounts configured.")
        print("Add one: python3 scripts/collect_twitter.py --add-account USERNAME PASSWORD")
        return False
    for acc in accounts:
        print(f"  @{acc['username']}: {'ACTIVE' if acc['active'] else 'INACTIVE'} (logged in: {acc['logged_in']})")
    return any(a['active'] for a in accounts)


async def scrape_user_tweets(api, handle: str, limit: int = TWEETS_PER_ACCOUNT) -> list:
    """Get recent tweets from a single user."""
    tweets = []
    try:
        async for tweet in api.user_tweets(handle, limit=limit):
            # Skip if older than 48 hours
            if tweet.date < datetime.now(timezone.utc) - timedelta(hours=48):
                continue
            tweets.append({
                "text": tweet.rawContent[:500] if tweet.rawContent else "",
                "likes": tweet.likeCount or 0,
                "retweets": tweet.retweetCount or 0,
                "replies": tweet.replyCount or 0,
                "date": tweet.date.strftime("%Y-%m-%d %H:%M") if tweet.date else "",
                "url": tweet.url or "",
                "is_retweet": tweet.rawContent.startswith("RT @") if tweet.rawContent else False,
                "views": tweet.viewCount or 0,
            })
    except Exception as e:
        err = str(e)[:80]
        # Don't spam errors for suspended/protected accounts
        if "404" not in err and "403" not in err:
            print(f"  WARN: @{handle}: {err}")
    return tweets


async def scrape_search(api, query: str, limit: int = 10) -> list:
    """Search tweets by query (for topic monitoring)."""
    tweets = []
    try:
        async for tweet in api.search(query, limit=limit):
            if tweet.date and tweet.date < datetime.now(timezone.utc) - timedelta(hours=48):
                continue
            tweets.append({
                "text": tweet.rawContent[:500] if tweet.rawContent else "",
                "likes": tweet.likeCount or 0,
                "retweets": tweet.retweetCount or 0,
                "user": tweet.user.username if tweet.user else "unknown",
                "date": tweet.date.strftime("%Y-%m-%d %H:%M") if tweet.date else "",
                "views": tweet.viewCount or 0,
            })
    except Exception as e:
        print(f"  WARN: Search '{query}': {str(e)[:80]}")
    return tweets


def format_tweet(t: dict, include_user: bool = False) -> str:
    """Format a single tweet for the intelligence report."""
    engagement = f"{t.get('likes',0)}♥ {t.get('retweets',0)}🔁 {t.get('replies',0)}💬"
    views = t.get('views', 0)
    if views and views > 0:
        engagement += f" {views:,}👁"
    prefix = f"@{t['user']}: " if include_user and t.get('user') else ""
    text = t.get('text', '').replace('\n', ' ').strip()
    if t.get('is_retweet'):
        text = f"[RT] {text}"
    return f"- [{t.get('date','')}] ({engagement}) {prefix}{text[:300]}"


async def collect():
    """Main collection routine."""
    from twscrape import API

    api = API(str(DB_PATH))
    seeds = load_seeds()
    now = datetime.now(timezone.utc)

    lines = [
        f"# Twitter/X Intelligence",
        f"## {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"## Source: twscrape (no API key)",
        "",
    ]

    total_tweets = 0
    failed_accounts = []

    # ─── SEED ACCOUNT TWEETS ────────────────────────────────
    for category, handles in seeds.items():
        if not isinstance(handles, list):
            continue
        lines.append(f"\n---\n## {category.upper().replace('_', ' ')}\n")

        for handle in handles:
            tweets = await scrape_user_tweets(api, handle)
            if tweets:
                lines.append(f"### @{handle} ({len(tweets)} recent)")
                for t in sorted(tweets, key=lambda x: x.get('likes', 0), reverse=True):
                    lines.append(format_tweet(t))
                lines.append("")
                total_tweets += len(tweets)
            else:
                failed_accounts.append(handle)

    # ─── TOPIC SEARCHES (high-signal queries) ───────────────
    SEARCHES = [
        "prediction market regulation",
        "Polymarket volume",
        "leveraged prediction",
        "DeFi leverage perpetual",
        "Base chain DeFi",
        "BNB prediction market",
        "crypto regulation CFTC",
        "prediction market manipulation",
    ]

    lines.append(f"\n---\n## TOPIC SEARCHES\n")
    for query in SEARCHES:
        results = await scrape_search(api, query, limit=5)
        if results:
            lines.append(f"### \"{query}\" ({len(results)} results)")
            for t in sorted(results, key=lambda x: x.get('likes', 0), reverse=True):
                lines.append(format_tweet(t, include_user=True))
            lines.append("")
            total_tweets += len(results)

    # ─── SUMMARY ────────────────────────────────────────────
    lines.append(f"\n---\n## COLLECTION SUMMARY")
    lines.append(f"- Total tweets collected: {total_tweets}")
    lines.append(f"- Failed/empty accounts: {len(failed_accounts)}")
    if failed_accounts:
        lines.append(f"- Failed: {', '.join(failed_accounts[:20])}")
    lines.append("")

    # Write output
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        f.write("\n".join(lines))

    print(f"Twitter: {total_tweets} tweets from {sum(len(v) for v in seeds.values() if isinstance(v, list))} accounts + {len(SEARCHES)} searches")
    return total_tweets


async def main():
    parser = argparse.ArgumentParser(description="RECON Twitter/X collector")
    parser.add_argument("--add-account", nargs="+", metavar="ARG",
                        help="Add scraper account: USERNAME PASSWORD [EMAIL] [EMAIL_PASSWORD]")
    parser.add_argument("--check", action="store_true", help="Check scraper account status")
    parser.add_argument("--collect", action="store_true", default=True, help="Run collection")

    args = parser.parse_args()

    if args.add_account:
        parts = args.add_account
        if len(parts) < 2:
            print("Usage: --add-account USERNAME PASSWORD [EMAIL] [EMAIL_PASSWORD]")
            sys.exit(1)
        await add_account(*parts[:4])
        return

    if args.check:
        await check_accounts()
        return

    # Default: collect
    try:
        from twscrape import API
        api = API(str(DB_PATH))
    except Exception as e:
        print(f"twscrape init failed: {e}")
        # Write fallback file so pipeline doesn't break
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_FILE, "w") as f:
            f.write("# Twitter/X Intelligence\n## NOT CONFIGURED\n"
                    "Add a scraper account: python3 scripts/collect_twitter.py --add-account USERNAME PASSWORD\n")
        sys.exit(0)

    count = await collect()
    if count == 0:
        print("WARNING: Zero tweets collected. Check account status: python3 scripts/collect_twitter.py --check")


if __name__ == "__main__":
    asyncio.run(main())
