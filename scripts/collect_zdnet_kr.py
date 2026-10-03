#!/usr/bin/env python3
"""
RECON Korean AI and 가상자산 feed (Phase E, docs/v2/phase-e-collectors-spec.md §3.4).

Two RSS requests: ZDNet Korea (feedburner, RFC 822 dates with +0900) for AI and 가상자산, and
디지털애셋 (naive pubDate, read as KST) for 가상자산 only. ZDNet's feed holds only its 30 newest
items, so a once-a-day pull would otherwise miss half the day's crypto coverage.
Window: RECON_FRESH_HOURS (72). Either feed failing alone still writes the other.

Writes data-sources/zdnet_kr/latest.md and status.json.

    python3 scripts/collect_zdnet_kr.py        exit 0 when ok, 1 otherwise
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collector_common as cc  # noqa: E402

NAME, LABEL = "zdnet_kr", "ZDNet Korea"
FEEDS = [
    ("ZDNet", "https://feeds.feedburner.com/zdkorea", {"AI", "가상자산"}),
    ("디지털애셋", "https://www.digitalasset.works/rss/allArticle.xml", {"가상자산"}),
]
KEYWORDS = {
    "AI": ["AI", "인공지능", "생성형", "LLM", "에이전트", "GPU", "데이터센터", "오픈AI", "앤트로픽",
           "챗GPT", "클로드", "제미나이", "AI 기본법", "AI 교육"],
    "가상자산": ["가상자산", "암호화폐", "비트코인", "이더리움", "블록체인", "스테이블코인", "거래소",
             "업비트", "빗썸", "토큰증권", "STO", "디지털자산"],
}
MAX_ITEMS = {"AI": 12, "가상자산": 8}
HEADINGS = {"AI": "AI", "가상자산": "가상자산·블록체인"}


def compile_keywords(words: list[str]) -> re.Pattern:
    """Latin words on ASCII word boundaries (AI not in MAIN, STO not in STORY; 오픈AI and AI가
    still match, since Hangul is a non-word character under re.ASCII); Hangul as substrings."""
    parts = []
    for w in words:
        if re.fullmatch(r"[A-Za-z]+", w):
            parts.append(rf"\b{re.escape(w)}\b")
        else:
            parts.append(re.escape(w))
    return re.compile("|".join(parts), re.ASCII)


MATCHERS = {k: compile_keywords(v) for k, v in KEYWORDS.items()}


def tag_for(title: str, cats: list[str], desc: str, allowed: set[str]) -> str | None:
    hay = " ".join([title, " ".join(cats), desc[:300]])
    for tag in ("AI", "가상자산"):  # an item matching both is tagged AI once
        if tag in allowed and MATCHERS[tag].search(hay):
            return tag
    return None


def clean_desc(desc: str, title: str) -> str:
    d = cc.strip_html(desc)
    d = re.sub(r"^\s*\[[^\]]{1,20}\]\s*", "", d)  # "[지디넷코리아]" prefix
    d = cc.one_line(d)
    t = cc.one_line(title)
    if not d or d[:30] == t[:30] or d in t:
        return ""
    return cc.one_line(d, 120)


def collect(res: cc.SourceResult, stamp: datetime) -> None:
    budget = cc.Budget(seconds=20, requests=2, mbytes=10)
    cutoff = stamp - timedelta(hours=cc.FRESH_HOURS)
    found: dict[str, list[tuple[datetime, str]]] = {"AI": [], "가상자산": []}
    seen_links, ok_feeds, errors = set(), 0, []
    for feed, url, allowed in FEEDS:
        try:
            items = cc.parse_rss(cc.http_text(url, budget))
        except (cc.HTTPFailure, cc.BudgetExceeded) as e:
            errors.append(f"{feed}: {getattr(e, 'msg', e)}")
            continue
        except Exception as e:  # malformed XML
            errors.append(f"{feed}: {type(e).__name__}: {e}")
            continue
        ok_feeds += 1
        for it in items:
            title = cc.one_line(cc.strip_html(it.get("title", "")))
            link = (it.get("link") or "").strip()
            when = cc.to_utc(it.get("published"))
            if not title or not link or link in seen_links:
                continue
            if when is None or when < cutoff:
                res.stale_items_dropped += 1
                continue
            desc = it.get("summary") or ""
            tag = tag_for(title, it.get("categories") or [], cc.strip_html(desc), allowed)
            if not tag:
                continue
            seen_links.add(link)
            extra = clean_desc(desc, title)
            src = f" [{feed}]" if tag == "가상자산" else ""
            line = f"- [{cc.fmt_utc(when)}] [{tag}]{src} {title}" + (f" — {extra}" if extra else "") + f" | {link}"
            found[tag].append((when, line))
    res.requests, res.bytes_in = budget.requests, budget.bytes_in
    res.notes += errors
    if ok_feeds == 0:
        res.error = "; ".join(errors) or "no feed"
        return

    lines = cc.header(LABEL, stamp, ["Source: ZDNet Korea RSS (feedburner) + 디지털애셋 RSS; last 72 h; titles in Korean"])
    if errors:
        lines.append(f"## NOTE: {'; '.join(errors)}")
    for tag in ("AI", "가상자산"):
        rows = sorted(found[tag], key=lambda x: x[0], reverse=True)[:MAX_ITEMS[tag]]
        lines += ["", f"## {HEADINGS[tag]}", ""]
        lines += [r[1] for r in rows] or [f"- No {tag} item in the last 72 h."]
    lines.append("")
    res.text = "\n".join(lines)
    res.ok = bool(found["AI"] or found["가상자산"])
    if not res.ok:
        res.error = "no matching items"
    res.notes.append(f"feeds ok {ok_feeds}/2, AI {len(found['AI'])}, 가상자산 {len(found['가상자산'])}")


if __name__ == "__main__":
    sys.exit(cc.run_main(NAME, LABEL, collect))
