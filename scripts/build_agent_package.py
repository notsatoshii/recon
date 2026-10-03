#!/usr/bin/env python3
"""Build what the agents and the synthesizer read from the day's full data package.

The collector writes briefs/<date>/00_data_package.md (all sections, ~170 KB on a full
day). Agents used to get its first 90 KB, so AI & tools, fundraising and most of X never
reached them (improvement plan F1). This script gives every section its own byte cap, so
every section is visible, and writes three files:

    <run>/01_filtered.md          agent view: every section, each capped (~65 KB total)
    <run>/01_social.md            ranked social posts with URLs, for MARKET MOOD (~8 KB)
    <run>/00_scorecard_recent.md  scorecard: market snapshot + predictions from the
                                  last 30 days (the 77 April-June legacy ones stay on disk)

and prints one line per section (input bytes -> output bytes). Exit code 2 when a section
that has content in the package ends up with nothing in the agent view (a cap bug); the
full package stays on disk untouched.

Usage: build_agent_package.py <run_dir>
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Byte caps per package section, in package order. Total ~65 KB.
CAPS = {
    "CROSS-SOURCE SIGNALS": 4000,
    "SENTIMENT & MARKET MOOD": 7000,
    "GEOPOLITICAL CONTEXT": 6500,
    "ON-CHAIN & MARKET DATA": 9000,
    "NEWS INTELLIGENCE": 13000,
    "SOCIAL INTELLIGENCE": 16000,   # Reddit 6,000 + X 10,000
    "AI & TOOLS": 6000,
    "FUNDRAISING": 6000,
}
REDDIT_CAP = 6000
X_CAP = 10000
SOCIAL_EXTRACT_CAP = 8000
SCORECARD_CAP = 6000
SCORECARD_DAYS = 30
X_MAX_AGE_H = int(os.environ.get("RECON_TWITTER_MAX_AGE_H", "72"))
X_STALE_DAYS = 2  # an X pull this much older than the package is not shown

# Fallback when the package was assembled without "# SECTION n:" markers (--skip-collect).
FALLBACK_NAMES = {
    "reddit": "SOCIAL INTELLIGENCE", "twitter/x": "SOCIAL INTELLIGENCE",
    "on-chain": "ON-CHAIN & MARKET DATA", "news": "NEWS INTELLIGENCE",
    "world monitor": "GEOPOLITICAL CONTEXT", "bettafish sentiment": "SENTIMENT & MARKET MOOD",
    "ai & tools": "AI & TOOLS", "fundraising": "FUNDRAISING",
}

NOISE = re.compile(r"^### (r/\S+ -- (ERROR|empty)|@\S+ \((0 tweets|not found|error))")
DISCUSSION = re.compile(r"\b(daily|weekly|monthly|casual|general)\b.*\b(discussion|thread|questions)\b"
                        r"|discussion (thread|hub)|megathread|\blounge\b|\bjobs\b|^\[meta\]|start here|new here\?",
                        re.I)


# ── helpers ───────────────────────────────────────────────────

def nbytes(s: str) -> int:
    return len(s.encode("utf-8"))


def cut_lines(text: str, budget: int) -> str:
    """Keep whole lines up to budget bytes; note how many lines were cut."""
    if nbytes(text) <= budget:
        return text
    out, used = [], 0
    lines = text.split("\n")
    for i, line in enumerate(lines):
        b = nbytes(line) + 1
        if used + b > budget - 40:
            rest = sum(1 for l in lines[i:] if l.strip())
            if rest:
                out.append(f"[... {rest} more lines cut for length]")
            break
        out.append(line)
        used += b
    return "\n".join(out)


def split_chunks(body: str) -> list[str]:
    """Split a section body at '# ' / '## ' headings (keeps the heading with its chunk)."""
    chunks, cur = [], []
    for line in body.split("\n"):
        if re.match(r"^#{1,2} ", line) and any(l.strip() for l in cur):
            chunks.append("\n".join(cur))
            cur = []
        cur.append(line)
    if any(l.strip() for l in cur):
        chunks.append("\n".join(cur))
    return chunks


def fair_share(chunks: list[str], budget: int) -> str:
    """Water-filling: small chunks keep everything, large ones share what is left."""
    sizes = [nbytes(c) for c in chunks]
    alloc = [0] * len(chunks)
    left, remaining = budget, sorted(range(len(chunks)), key=lambda i: sizes[i])
    while remaining:
        share = left // len(remaining)
        i = remaining.pop(0)
        alloc[i] = min(sizes[i], share)
        left -= alloc[i]
    return "\n".join(cut_lines(c, a) for c, a in zip(chunks, alloc) if a > 60)


def drop_noise(text: str) -> str:
    out = []
    for line in text.split("\n"):
        if NOISE.match(line):
            continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


# ── X / Twitter ──────────────────────────────────────────────

TWEET = re.compile(r"^- \[(?P<when>[A-Z][a-z]{2} \d\d, \d{4} \d\d:\d\d)\] \((?P<eng>[^)]*)\) (?P<rest>.*)$")


def eng_num(eng: str, sym: str) -> int:
    m = re.search(r"([\d.,]+)\s*([KkMm]?)" + re.escape(sym), eng)
    if not m:
        return 0
    v = float(m.group(1).replace(",", ""))
    return int(v * {"k": 1e3, "m": 1e6}.get(m.group(2).lower(), 1))


def parse_tweets(block: str) -> tuple[list[dict], datetime | None]:
    pulled = None
    m = re.search(r"^## (\d{4}-\d\d-\d\d \d\d:\d\d) UTC", block, re.M)
    if m:
        pulled = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    tweets, cat, handle = [], "", ""
    for line in block.split("\n"):
        if line.startswith("## ") and not re.match(r"## (\d{4}-|Source|NOTE)", line):
            cat = line[3:].strip()
        elif line.startswith("### @"):
            handle = line[4:].split(" ")[0]
        elif line.startswith('### "'):
            handle = ""
        t = TWEET.match(line)
        if not t:
            continue
        rest = t.group("rest").strip()
        if rest.startswith("RT "):  # reposts carry the original's counts and are mostly off-topic
            continue
        text =re.sub(r"https://(t\.co|x\.com)/\S+", "", rest).strip()
        if len(text) < 25:  # link-only or near-empty tweets
            continue
        who = handle
        um = re.match(r"(RT )?(@\w+): ", rest)
        if um:
            who = um.group(2)
            rest = rest[um.end():]
        um_url = re.search(r"https://x\.com/\S+/status/\d+\s*$", rest)
        url = um_url.group(0).strip() if um_url else ""
        body = re.sub(r"\s*https://(t\.co|x\.com)/\S+", "", rest).strip()
        try:
            when = datetime.strptime(t.group("when"), "%b %d, %Y %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            when = None
        eng = t.group("eng")
        score = eng_num(eng, "♥") + 3 * eng_num(eng, "\U0001F501") + 2 * eng_num(eng, "\U0001F4AC")
        tweets.append({"cat": cat or "OTHER", "who": who or "@?", "when": when, "eng": eng,
                       "rest": f"{body[:230]} {url}".strip(), "score": score})
    return tweets, pulled


def pick_round_robin(by_cat: dict[str, list[dict]], budget: int, render) -> list[dict]:
    """Take the best item of each category in turn until the byte budget is used."""
    queues = {c: sorted(v, key=lambda t: -t["score"]) for c, v in by_cat.items() if v}
    picked, used = [], 0
    while queues:
        for c in list(queues):
            item = queues[c].pop(0)
            b = nbytes(render(item)) + 1
            if used + b <= budget:
                picked.append(item)
                used += b
            if not queues[c]:
                del queues[c]
        if used > budget - 150:
            break
    return picked


def x_view(block: str, pkg_date: datetime | None, budget: int) -> tuple[str, list[dict]]:
    tweets, pulled = parse_tweets(block)
    if "SOURCE UNAVAILABLE" in block[:600]:
        return "# Twitter/X Intelligence\nX/Twitter: source unavailable today.\n", []
    if pulled and pkg_date and (pkg_date - pulled) > timedelta(days=X_STALE_DAYS):
        return (f"# Twitter/X Intelligence\nX/Twitter: the latest pull is from {pulled:%Y-%m-%d}, "
                f"older than {X_STALE_DAYS} days; not shown.\n"), []
    ref = pulled or pkg_date
    if ref:
        tweets = [t for t in tweets if t["when"] is None or ref - t["when"] <= timedelta(hours=X_MAX_AGE_H)]
    total = len(tweets)
    if not tweets:
        return "# Twitter/X Intelligence\nX/Twitter: no tweets from the last 72 h in today's pull.\n", []

    def render(t):
        when = t["when"].strftime("%b %d %H:%M") if t["when"] else ""
        return f"- {t['who']} [{when}] ({t['eng']}) {t['rest']}"

    by_cat: dict[str, list[dict]] = {}
    for t in tweets:
        by_cat.setdefault(t["cat"], []).append(t)
    head = (f"# Twitter/X Intelligence\n## Pulled {pulled:%Y-%m-%d %H:%M} UTC" if pulled else "# Twitter/X Intelligence")
    head += f"\n## Top tweets by engagement per category ({{n}} of {total} from the last {X_MAX_AGE_H} h)\n"
    picked = pick_round_robin(by_cat, budget - 250, render)
    out = [head.format(n=len(picked))]
    for cat in by_cat:
        sel = sorted([t for t in picked if t["cat"] == cat], key=lambda t: -t["score"])
        if sel:
            out.append(f"## {cat}")
            out.extend(render(t) for t in sel)
            out.append("")
    return "\n".join(out), picked


# ── Reddit ───────────────────────────────────────────────────

def reddit_posts(block: str) -> list[dict]:
    posts, cat, sub = [], "", ""
    lines = block.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("## ") and not re.match(r"## \d{4}-", line):
            cat = line[3:].strip()
        elif line.startswith("### r/"):
            sub = line[4:].split(" ")[0]
        elif line.startswith("- ") and sub:
            m = re.match(r"- (.*?)\s+(https://www\.reddit\.com/\S+)$", line)
            if not m:
                continue
            nxt = lines[i + 1].strip() if i + 1 < len(lines) and lines[i + 1].startswith("  ") else ""
            if "submitted by" in nxt:
                nxt = ""
            posts.append({"cat": cat, "sub": sub, "title": m.group(1), "url": m.group(2),
                          "snippet": nxt, "rank": len([p for p in posts if p["sub"] == sub])})
    return posts


# ── package ──────────────────────────────────────────────────

def split_sections(pkg: str) -> tuple[str, list[tuple[str, str, str]]]:
    marks = list(re.finditer(r"^# SECTION \d+: (.+)$", pkg, re.M))
    if marks:
        head = pkg[:marks[0].start()]
        secs = []
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(pkg)
            secs.append((m.group(1).strip(), pkg[m.end():end], m.group(0)[2:].strip()))
        return head, secs
    # fallback: '# <Name> Intelligence' blocks, grouped by section name
    marks = list(re.finditer(r"^# (.+?) Intelligence\s*$", pkg, re.M))
    head = pkg[:marks[0].start()] if marks else pkg
    grouped: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(pkg)
        name = FALLBACK_NAMES.get(m.group(1).strip().lower(), m.group(1).strip().upper())
        grouped[name] = grouped.get(name, "") + "\n" + pkg[m.start():end]
    order = list(CAPS)
    items = sorted(grouped.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)
    return head, [(n, b, f"SECTION: {n}") for n, b in items]


def content_bytes(text: str) -> int:
    """Bytes of real content: list items and prose, not headings or rules."""
    return sum(nbytes(l) for l in text.split("\n")
               if l.strip() and not l.startswith("#") and l.strip() != "---" and not l.startswith("<!--"))


def package_date(pkg: str) -> datetime | None:
    m = re.search(r"^# (?:RECON INTELLIGENCE PACKAGE|RAW DATA) -- (\d{4}-\d\d-\d\d)", pkg, re.M)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(hours=23)


def build(run: Path) -> int:
    pkg = (run / "00_data_package.md").read_text(encoding="utf-8", errors="ignore")
    pdate = package_date(pkg)
    head, sections = split_sections(pkg)
    out = [head.rstrip() + "\n\nAGENT VIEW: every section below is capped separately so all of them fit; "
           "the full package stays on disk.\n"]
    report, failed = [], []
    tweets_picked: list[dict] = []
    posts: list[dict] = []
    for name, body, label in sections:
        cap = CAPS.get(name, 4000)
        if name == "SOCIAL INTELLIGENCE":
            parts = re.split(r"(?m)^(?=# Twitter/X Intelligence)", body, maxsplit=1)
            reddit = parts[0]
            xblock = parts[1] if len(parts) > 1 else ""
            posts = reddit_posts(reddit)
            rview = fair_share(split_chunks(drop_noise(reddit)), REDDIT_CAP)
            xv, tweets_picked = x_view(xblock, pdate, X_CAP) if xblock else ("", [])
            view = rview + "\n\n" + xv
        else:
            view = fair_share(split_chunks(drop_noise(body)), cap)
        out.append(f"---\n\n# {label}\n\n{view.strip()}\n")
        ib, ob = content_bytes(body), content_bytes(view)
        report.append({"section": name, "in_bytes": nbytes(body), "out_bytes": nbytes(view),
                       "in_content": ib, "out_content": ob})
        if ib > 0 and ob == 0:
            failed.append(name)
    view_text = "\n".join(out)
    (run / "01_filtered.md").write_text(view_text, encoding="utf-8")

    # social extract for MARKET MOOD
    soc = ["# SOCIAL POSTS FOR MARKET MOOD",
           "Quote only from these. X lines carry engagement (likes / reposts / replies); Reddit RSS has none,",
           "so Reddit posts are listed in each subreddit's hot order. Discussion-thread titles are left out.", ""]
    used = sum(nbytes(s) + 1 for s in soc)
    xs = sorted(tweets_picked, key=lambda t: -t["score"])
    if xs:
        soc.append("## X (top by engagement)")
        for t in xs:
            line = f"- {t['who']} ({t['eng']}) {t['rest']}"
            if used + nbytes(line) > SOCIAL_EXTRACT_CAP * 0.6:
                break
            soc.append(line)
            used += nbytes(line) + 1
    else:
        soc.append("## X\n- No usable X posts today.")
    soc.append("\n## Reddit (hot posts with URLs)")
    rp = [p for p in posts if not DISCUSSION.search(p["title"])]
    rp.sort(key=lambda p: (p["rank"], p["cat"]))
    for p in rp:
        line = f"- {p['sub']}: {p['title'][:180]} {p['url']}" + (f"\n  {p['snippet'][:140]}" if p["snippet"] else "")
        if used + nbytes(line) > SOCIAL_EXTRACT_CAP:
            break
        soc.append(line)
        used += nbytes(line) + 1
    if not rp:
        soc.append("- No Reddit posts today.")
    (run / "01_social.md").write_text("\n".join(soc) + "\n", encoding="utf-8")

    # recent scorecard
    sc_in = run / "00_scorecard.md"
    sc_note = "no scorecard"
    if sc_in.exists():
        sc = sc_in.read_text(encoding="utf-8", errors="ignore")
        ref = pdate or datetime.now(timezone.utc)
        cutoff = (ref - timedelta(days=SCORECARD_DAYS)).strftime("%Y-%m-%d")
        keep, dropped, agent_hdr = [], 0, None
        for line in sc.split("\n"):
            d = re.match(r"^- \[(\d{4}-\d\d-\d\d)\]", line)
            if line.startswith("### "):
                agent_hdr = line
                continue
            if d and d.group(1) < cutoff:
                dropped += 1
                continue
            if re.match(r"^\s+- ", line):  # undated legacy items carried over from old memory files
                dropped += 1
                continue
            if d and agent_hdr:
                keep.append(agent_hdr)
                agent_hdr = None
            keep.append(line)
        txt0 = re.sub(r"\n{3,}", "\n\n", "\n".join(keep))
        keep = txt0.split("\n")
        txt = "\n".join(keep).rstrip() + (
            f"\n\n({dropped} older (before {cutoff}) or undated legacy predictions are not shown; they stay in 00_scorecard.md.)\n"
            if dropped else "\n")
        (run / "00_scorecard_recent.md").write_text(cut_lines(txt, SCORECARD_CAP), encoding="utf-8")
        sc_note = f"scorecard {nbytes(sc)} -> {min(nbytes(txt), SCORECARD_CAP)} bytes ({dropped} legacy predictions left out)"

    (run / "01_package_report.json").write_text(json.dumps(
        {"agent_view_bytes": nbytes(view_text), "full_package_bytes": nbytes(pkg), "sections": report,
         "x_tweets_shown": len(tweets_picked), "reddit_posts": len(posts)}, indent=1), encoding="utf-8")

    for r in report:
        flag = "  <-- EMPTY IN VIEW" if r["section"] in failed else ("  (empty at source)" if r["in_content"] == 0 else "")
        print(f"{r['section']}: {r['in_bytes']} -> {r['out_bytes']} bytes{flag}")
    print(f"agent view {nbytes(view_text)} bytes (full package {nbytes(pkg)}); "
          f"X tweets shown {len(tweets_picked)}; Reddit posts {len(posts)}; social extract "
          f"{nbytes(chr(10).join(soc))} bytes; {sc_note}")
    if failed:
        print(f"FAIL: sections with content that the view dropped: {', '.join(failed)}")
        return 2
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    sys.exit(build(Path(sys.argv[1])))
