#!/usr/bin/env python3
"""
RECON Twitter/X Data Collection via Playwright + Nitter
Scrapes public Twitter data through Nitter instances using headless Chromium.
Playwright handles Cloudflare JS challenges automatically.

No API key needed, no Twitter account needed.

Output: /home/recon/recon/data-sources/twitter/latest.md
"""

import asyncio
import re
import sys
import yaml
from datetime import datetime, timezone
from pathlib import Path

RECON_HOME = Path("/home/recon/recon")
SEEDS_FILE = RECON_HOME / "config" / "twitter_seeds.yaml"
OUTPUT_FILE = RECON_HOME / "data-sources" / "twitter" / "latest.md"

NITTER_INSTANCE = "https://nitter.cz"
MAX_ACCOUNTS_PER_CATEGORY = 12
TWEETS_PER_ACCOUNT = 8
CONCURRENT_PAGES = 2  # Nitter is rate-sensitive, keep low
CF_WAIT = 6  # Seconds to wait for Cloudflare challenge


def load_seeds() -> dict:
    if not SEEDS_FILE.exists():
        return {}
    with open(SEEDS_FILE) as f:
        data = yaml.safe_load(f)
    for cat in data:
        if isinstance(data[cat], list):
            seen = set()
            deduped = []
            for h in data[cat]:
                h = h.strip()
                if h.lower() not in seen:
                    seen.add(h.lower())
                    deduped.append(h)
            data[cat] = deduped[:MAX_ACCOUNTS_PER_CATEGORY]
    return data


async def solve_cloudflare(page):
    """Wait for Cloudflare JS challenge to resolve."""
    content = await page.content()
    if "Just a moment" in content or "Checking your browser" in content:
        await page.wait_for_timeout(CF_WAIT * 1000)


async def scrape_profile(context, handle: str) -> list:
    """Scrape tweets from a Nitter profile page."""
    tweets = []
    page = await context.new_page()

    try:
        url = f"{NITTER_INSTANCE}/{handle}"
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await solve_cloudflare(page)

        content = await page.content()

        # Check if profile exists
        if "User \"" in content and "not found" in content:
            return []

        # Parse Nitter HTML for timeline items
        # Nitter uses .timeline-item for each tweet
        items = await page.query_selector_all(".timeline-item")

        for item in items[:TWEETS_PER_ACCOUNT]:
            try:
                # Tweet text
                text_el = await item.query_selector(".tweet-content")
                text = await text_el.inner_text() if text_el else ""

                # Stats
                stats = {}
                stat_container = await item.query_selector(".tweet-stat")
                # Nitter puts stats in icon-container spans
                for stat_type, icon_class in [("replies", "icon-comment"), ("retweets", "icon-retweet"), ("likes", "icon-heart")]:
                    el = await item.query_selector(f".{icon_class}")
                    if el:
                        parent = await el.evaluate_handle("el => el.parentElement")
                        stat_text = await parent.inner_text() if parent else "0"
                        stat_text = stat_text.strip().replace(",", "")
                        try:
                            stats[stat_type] = int(stat_text) if stat_text.isdigit() else 0
                        except (ValueError, AttributeError):
                            stats[stat_type] = 0

                # Timestamp
                time_el = await item.query_selector(".tweet-date a")
                timestamp = ""
                if time_el:
                    title = await time_el.get_attribute("title") or ""
                    timestamp = title[:16]

                # Check if retweet
                is_rt = False
                rt_el = await item.query_selector(".retweet-header")
                if rt_el:
                    is_rt = True

                if text and len(text.strip()) > 10:
                    tweets.append({
                        "text": text[:500],
                        "likes": stats.get("likes", 0),
                        "retweets": stats.get("retweets", 0),
                        "replies": stats.get("replies", 0),
                        "time": timestamp,
                        "is_rt": is_rt,
                    })

            except Exception:
                continue

    except Exception as e:
        err = str(e)[:80]
        if "timeout" not in err.lower():
            print(f"  WARN: @{handle}: {err}")

    finally:
        await page.close()

    return tweets


async def scrape_search(context, query: str) -> list:
    """Search Nitter for a query."""
    tweets = []
    page = await context.new_page()

    try:
        url = f"{NITTER_INSTANCE}/search?f=tweets&q={query.replace(' ', '+')}"
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await solve_cloudflare(page)

        items = await page.query_selector_all(".timeline-item")

        for item in items[:5]:
            try:
                text_el = await item.query_selector(".tweet-content")
                text = await text_el.inner_text() if text_el else ""

                # Get author
                user_el = await item.query_selector(".username")
                user = await user_el.inner_text() if user_el else ""
                user = user.strip().lstrip("@")

                # Likes
                likes = 0
                heart = await item.query_selector(".icon-heart")
                if heart:
                    parent = await heart.evaluate_handle("el => el.parentElement")
                    stat_text = await parent.inner_text() if parent else "0"
                    stat_text = stat_text.strip().replace(",", "")
                    try:
                        likes = int(stat_text) if stat_text.isdigit() else 0
                    except ValueError:
                        pass

                if text:
                    tweets.append({
                        "text": text[:500],
                        "user": user,
                        "likes": likes,
                    })
            except Exception:
                continue

    except Exception:
        pass
    finally:
        await page.close()

    return tweets


def format_tweet(t: dict, include_user: bool = False) -> str:
    likes = t.get("likes", 0)
    rts = t.get("retweets", 0)
    replies = t.get("replies", 0)
    parts = []
    if likes: parts.append(f"{likes}♥")
    if rts: parts.append(f"{rts}🔁")
    if replies: parts.append(f"{replies}💬")
    engagement = " ".join(parts) if parts else "0♥"
    prefix = f"@{t['user']}: " if include_user and t.get("user") else ""
    text = t.get("text", "").replace("\n", " ").strip()[:300]
    time_str = f"[{t['time']}] " if t.get("time") else ""
    rt_tag = "[RT] " if t.get("is_rt") else ""
    return f"- {time_str}({engagement}) {rt_tag}{prefix}{text}"


async def main():
    from playwright.async_api import async_playwright

    seeds = load_seeds()
    if not seeds:
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text("# Twitter/X Intelligence\n## NO SEEDS CONFIGURED\n")
        return

    now = datetime.now(timezone.utc)
    lines = [
        f"# Twitter/X Intelligence",
        f"## {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"## Source: Playwright + Nitter ({NITTER_INSTANCE})",
        "",
    ]

    total_tweets = 0
    failed = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )

        # Warm up — first request solves Cloudflare for the session
        print("  Warming up Nitter session (solving Cloudflare)...")
        warmup = await context.new_page()
        await warmup.goto(f"{NITTER_INSTANCE}/Polymarket", wait_until="domcontentloaded", timeout=25000)
        await warmup.wait_for_timeout(CF_WAIT * 1000)
        await warmup.close()

        # Scrape seed accounts
        all_handles = []
        handle_cats = {}
        for cat, handles in seeds.items():
            if not isinstance(handles, list):
                continue
            for h in handles:
                all_handles.append(h)
                handle_cats[h] = cat

        current_cat = ""
        for i in range(0, len(all_handles), CONCURRENT_PAGES):
            batch = all_handles[i:i + CONCURRENT_PAGES]
            tasks = [scrape_profile(context, h) for h in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for handle, result in zip(batch, results):
                cat = handle_cats.get(handle, "uncategorized")

                if isinstance(result, Exception) or not result:
                    failed.append(handle)
                    continue

                # Category header
                if cat != current_cat:
                    lines.append(f"\n---\n## {cat.upper().replace('_', ' ')}\n")
                    current_cat = cat

                lines.append(f"### @{handle} ({len(result)} tweets)")
                for t in sorted(result, key=lambda x: x.get("likes", 0), reverse=True):
                    lines.append(format_tweet(t))
                lines.append("")
                total_tweets += len(result)

            await asyncio.sleep(3)  # Rate limit between batches

        # Topic searches
        SEARCHES = [
            "prediction market",
            "DeFi regulation",
            "crypto macro outlook",
            "AI agents crypto",
        ]

        lines.append(f"\n---\n## TOPIC SEARCHES\n")
        for query in SEARCHES:
            results = await scrape_search(context, query)
            if results:
                lines.append(f"### \"{query}\" ({len(results)} results)")
                for t in sorted(results, key=lambda x: x.get("likes", 0), reverse=True):
                    lines.append(format_tweet(t, include_user=True))
                lines.append("")
                total_tweets += len(results)
            await asyncio.sleep(3)

        await browser.close()

    # Summary
    lines.append(f"\n---\n## COLLECTION SUMMARY")
    lines.append(f"- Total tweets: {total_tweets}")
    lines.append(f"- Accounts scraped: {len(all_handles) - len(failed)}/{len(all_handles)}")
    lines.append(f"- Failed/empty: {len(failed)}")
    if failed:
        lines.append(f"- Failed: {', '.join(failed[:20])}")
    lines.append("")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text("\n".join(lines))
    print(f"Twitter: {total_tweets} tweets from {len(all_handles) - len(failed)}/{len(all_handles)} accounts ({len(failed)} failed)")


if __name__ == "__main__":
    asyncio.run(main())
