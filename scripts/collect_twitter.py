#!/usr/bin/env python3
"""
RECON Twitter/X collection via twscrape (account-backed, no browser, no paid API).

Reads config/twitter_seeds.yaml, pulls the latest tweets for each handle through X's
internal API using the burner account(s) stored in the twscrape database, and writes
data-sources/twitter/latest.md in the same layout v1 produced (category headers, one
"### @handle" block per account, one "- [time] (engagement) text url" line per tweet).

Setup (once, by a human, never by a script):
    1. Log in to x.com in a browser as the burner account.
    2. Copy the `auth_token` and `ct0` cookies.
    3. On the droplet:  $RECON_VENV/bin/twscrape --db $RECON_TWSCRAPE_DB add_cookie <name>
       and paste "auth_token=...; ct0=..." when prompted.

Environment:
    RECON_HOME                repo root
    RECON_TWSCRAPE_DB         accounts db (default ~/.recon_twscrape.db)
    RECON_TWITTER_PER_CAT     handles per category (default 8)
    RECON_TWITTER_PER_USER    tweets per handle (default 8)
    RECON_TWITTER_MAX_MINUTES time budget; stops and writes what it has (default 12)
    RECON_TWITTER_SEARCHES    "0" to skip topic searches (default 1)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RECON_HOME = Path(os.environ.get("RECON_HOME") or Path(__file__).resolve().parent.parent)
SEEDS_FILE = RECON_HOME / "config" / "twitter_seeds.yaml"
OUTPUT_FILE = RECON_HOME / "data-sources" / "twitter" / "latest.md"
USER_ID_CACHE = RECON_HOME / "config" / "twitter_user_ids.json"
DB_PATH = os.environ.get("RECON_TWSCRAPE_DB") or str(Path.home() / ".recon_twscrape.db")

PER_CATEGORY = int(os.environ.get("RECON_TWITTER_PER_CAT", "8"))
PER_USER = int(os.environ.get("RECON_TWITTER_PER_USER", "8"))
MAX_MINUTES = float(os.environ.get("RECON_TWITTER_MAX_MINUTES", "12"))
DO_SEARCHES = os.environ.get("RECON_TWITTER_SEARCHES", "1") != "0"

# Kept from v1: a few topic searches on top of the account list.
TOPIC_SEARCHES = [
    "polymarket",
    "prediction market",
    "kalshi",
    "leveraged perpetuals",
]


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def load_seeds() -> dict[str, list[str]]:
    import yaml  # PyYAML, present in the venv
    data = yaml.safe_load(SEEDS_FILE.read_text(encoding="utf-8")) or {}
    seeds: dict[str, list[str]] = {}
    seen: set[str] = set()
    for cat, handles in data.items():
        if not isinstance(handles, list):
            continue
        clean = []
        for h in handles:
            h = str(h).strip().lstrip("@")
            if h and h.lower() not in seen:
                seen.add(h.lower())
                clean.append(h)
        seeds[cat] = clean[:PER_CATEGORY]
    return seeds


def load_id_cache() -> dict[str, int]:
    try:
        return {k.lower(): int(v) for k, v in json.loads(USER_ID_CACHE.read_text()).items()}
    except Exception:
        return {}


def save_id_cache(cache: dict[str, int]) -> None:
    try:
        USER_ID_CACHE.write_text(json.dumps(cache, indent=0, sort_keys=True))
    except OSError:
        pass


def fmt_tweet(t, include_user: bool = False) -> str:
    when = t.date.astimezone(timezone.utc).strftime("%b %d, %Y %H:%M") if t.date else ""
    eng = []
    if t.likeCount:
        eng.append(f"{t.likeCount}♥")
    if t.retweetCount:
        eng.append(f"{t.retweetCount}\U0001F501")
    if t.replyCount:
        eng.append(f"{t.replyCount}\U0001F4AC")
    rt = "RT " if getattr(t, "retweetedTweet", None) else ""
    who = f"@{t.user.username}: " if include_user and t.user else ""
    text = " ".join((t.rawContent or "").split())[:280]
    url = t.url or ""
    return f"- [{when}] ({' '.join(eng)}) {rt}{who}{text} {url}".rstrip()


def write_not_configured(reason: str) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    OUTPUT_FILE.write_text(
        "# Twitter/X Intelligence\n"
        f"## {now}\n"
        "## SOURCE UNAVAILABLE TODAY\n"
        f"{reason}\n"
        "Setup: log in to x.com as the burner account, copy the auth_token and ct0 cookies, then run\n"
        "  twscrape --db $RECON_TWSCRAPE_DB add_cookie <name>\n",
        encoding="utf-8",
    )


async def run() -> int:
    try:
        from twscrape import API, gather
        from twscrape.logger import set_log_level
    except ImportError as e:
        write_not_configured(f"twscrape not installed in this Python ({e}).")
        log("twscrape not installed; wrote SOURCE UNAVAILABLE")
        return 0
    set_log_level("ERROR")

    api = API(DB_PATH, raise_when_no_account=True)
    accounts = await api.pool.accounts_info()
    active = [a for a in accounts if a.get("active")]
    if not active:
        write_not_configured(f"No active X account in {DB_PATH} ({len(accounts)} stored, 0 active).")
        log(f"no active accounts in {DB_PATH}; wrote SOURCE UNAVAILABLE")
        return 0
    log(f"twscrape: {len(active)} active account(s), db={DB_PATH}")

    seeds = load_seeds()
    total = sum(len(v) for v in seeds.values())
    log(f"seeds: {total} handles across {len(seeds)} categories (cap {PER_CATEGORY}/category)")
    ids = load_id_cache()
    deadline = time.monotonic() + MAX_MINUTES * 60

    now = datetime.now(timezone.utc)
    lines = [
        "# Twitter/X Intelligence",
        f"## {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"## Source: twscrape via X internal API ({len(active)} account(s))",
        "",
    ]
    fetched = failed = skipped = 0
    stopped_early = False

    def out_of_time() -> bool:
        return time.monotonic() > deadline

    for cat, handles in seeds.items():
        if not handles:
            continue
        lines.append(f"\n---\n## {cat.upper().replace('_', ' ')}\n")
        for handle in handles:
            if out_of_time():
                stopped_early = True
                skipped += 1
                continue
            try:
                uid = ids.get(handle.lower())
                if uid is None:
                    user = await api.user_by_login(handle)
                    if user is None:
                        failed += 1
                        lines.append(f"### @{handle} (not found)")
                        continue
                    uid = user.id
                    ids[handle.lower()] = uid
                tweets = await gather(api.user_tweets(uid, limit=PER_USER))
                tweets = [t for t in tweets if t is not None][:PER_USER]
                lines.append(f"### @{handle} ({len(tweets)} tweets)")
                for t in tweets:
                    lines.append(fmt_tweet(t))
                lines.append("")
                fetched += 1
            except Exception as e:  # NoAccountError, network, parsing changes
                name = type(e).__name__
                failed += 1
                lines.append(f"### @{handle} (error: {name})")
                if name == "NoAccountError":
                    log("all accounts rate-limited; stopping early")
                    stopped_early = True
                    deadline = 0  # force skip of the rest
        save_id_cache(ids)

    if DO_SEARCHES and not out_of_time():
        lines.append("\n---\n## TOPIC SEARCHES\n")
        for q in TOPIC_SEARCHES:
            if out_of_time():
                break
            try:
                results = await gather(api.search(q, limit=10))
                lines.append(f'### "{q}" ({len(results)} results)')
                for t in results[:10]:
                    lines.append(fmt_tweet(t, include_user=True))
                lines.append("")
            except Exception as e:
                lines.append(f'### "{q}" (error: {type(e).__name__})')

    lines.append("")
    lines.append(f"<!-- twitter: {fetched} accounts fetched, {failed} failed, {skipped} skipped"
                 f"{', stopped early (time/limit)' if stopped_early else ''} -->")
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"Twitter: {fetched}/{total} accounts fetched ({failed} failed, {skipped} skipped"
        f"{', stopped early' if stopped_early else ''})")
    return 0


def main() -> int:
    if not SEEDS_FILE.exists():
        write_not_configured(f"seeds file missing: {SEEDS_FILE}")
        return 0
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
