#!/usr/bin/env python3
"""
RECON Polymarket collector (Phase E, docs/v2/phase-e-collectors-spec.md §3.1).

Public Gamma and CLOB endpoints, no key. Polymarket geo-blocks Korean IPs (HTTP 451), so this
runs on the droplet. Every /events call carries exclude_tag_id=1 (Sports); without it one page
of 50 events is ~20 MB.

Writes data-sources/polymarket/latest.md and status.json.

    python3 scripts/collect_polymarket.py      exit 0 when ok, 1 otherwise
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collector_common as cc  # noqa: E402

NAME, LABEL = "polymarket", "Polymarket"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
SPORTS_TAG_ID = 1
SPORTS_SLUGS = {"sports", "esports", "games", "soccer", "football", "basketball", "baseball", "hockey",
                "tennis", "mma", "golf", "cricket", "nfl", "nba", "mlb", "nhl"}
NOT_NEW_SLUGS = {"recurring", "up-or-down", "hide-from-new"}
TOPICS = ["crypto", "fed-rates", "economy", "politics", "geopolitics", "ai", "tech"]
BOOKS_WANTED, BOOK_TRIES = 8, 12


def f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def usd(x) -> str:
    v = f(x)
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.2f}M"
    if v >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:.0f}"


def jlist(v) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip():
        try:
            r = json.loads(v)
            return r if isinstance(r, list) else []
        except ValueError:
            return []
    return []


def tag_slugs(ev: dict) -> set[str]:
    return {str(t.get("slug", "")).lower() for t in ev.get("tags") or [] if isinstance(t, dict)}


def is_sports_event(ev: dict) -> bool:
    return bool(tag_slugs(ev) & SPORTS_SLUGS)


def event_url(ev: dict) -> str:
    return f"https://polymarket.com/event/{ev.get('slug', '')}"


def live_markets(ev: dict) -> list[dict]:
    """Open, non-sports markets whose prices decode; adds _yes (0-1) and _tokens."""
    out = []
    for m in ev.get("markets") or []:
        if m.get("closed") or m.get("active") is False:
            continue
        if m.get("sportsMarketType") or m.get("gameId"):
            continue
        prices = jlist(m.get("outcomePrices"))
        if not prices:
            continue
        try:
            yes = float(prices[0])
        except (TypeError, ValueError):
            continue
        m = dict(m)
        m["_yes"] = yes
        m["_tokens"] = jlist(m.get("clobTokenIds"))
        m["_outcomes"] = jlist(m.get("outcomes"))
        out.append(m)
    return out


def lead(ev: dict, n: int = 3) -> list[dict]:
    """Leading markets: by YES price for mutually exclusive (negRisk) events, else by 24 h volume."""
    mk = live_markets(ev)
    if ev.get("negRisk") or ev.get("enableNegRisk"):
        mk.sort(key=lambda m: m["_yes"], reverse=True)
    else:
        mk.sort(key=lambda m: f(m.get("volume24hr")), reverse=True)
    return mk[:n]


def mname(m: dict) -> str:
    g = m.get("groupItemTitle")
    if g:
        return cc.one_line(g, 60)
    outs = m.get("_outcomes") or []
    if outs and str(outs[0]).lower() not in ("yes", "no"):
        return cc.one_line(str(outs[0]), 60)
    return cc.one_line(m.get("question", ""), 90)


def pts(x) -> float | None:
    return None if x is None or x == "" else 100 * f(x)


def pts_s(x) -> str:
    p = pts(x)
    return "n/a" if p is None else f"{p:+.0f} pts"


def yes_pct(m: dict) -> str:
    v = 100 * m["_yes"]
    return f"{v:.1f}%" if v < 1 or v > 99 else f"{v:.0f}%"


def ev_line(ev: dict, prefix: str = "") -> str:
    leading = " · ".join(f"\"{mname(m)}\" YES {yes_pct(m)} (1d {pts_s(m.get('oneDayPriceChange'))})"
                         for m in lead(ev)) or "n/a"
    end = cc.to_utc(ev.get("endDate"))
    return (f"- {prefix}{cc.one_line(ev.get('title', ''), 120)} — 24h vol {usd(ev.get('volume24hr'))} | "
            f"total vol {usd(ev.get('volume'))} | liq {usd(ev.get('liquidity'))} | OI {usd(ev.get('openInterest'))} | "
            f"ends {cc.fmt_utc(end, False)} | leading: {leading} | {event_url(ev)}")


def get_events(params: dict, budget: cc.Budget) -> list[dict]:
    q = {"closed": "false", "exclude_tag_id": SPORTS_TAG_ID, **params}
    data = cc.http_json(cc.build_url(f"{GAMMA}/events", q), budget)
    evs = data if isinstance(data, list) else data.get("data") or data.get("events") or []
    return [e for e in evs if isinstance(e, dict) and not is_sports_event(e)]


def book_depth(book: dict) -> dict | None:
    bids = [(f(l.get("price")), f(l.get("size"))) for l in book.get("bids") or []]
    asks = [(f(l.get("price")), f(l.get("size"))) for l in book.get("asks") or []]
    if not bids or not asks:
        return None
    bb, ba = max(p for p, _ in bids), min(p for p, _ in asks)
    mid = (bb + ba) / 2
    out = {"mid": mid, "spread": ba - bb}
    for w in (0.02, 0.05):
        out[f"bid{w}"] = sum(p * s for p, s in bids if p >= mid - w - 1e-9)
        out[f"ask{w}"] = sum(p * s for p, s in asks if p <= mid + w + 1e-9)
    return out


def collect(res: cc.SourceResult, stamp: datetime) -> None:
    budget = cc.Budget(seconds=45, requests=30, mbytes=25)
    now = stamp
    notes = res.notes
    try:
        top_raw = get_events({"order": "volume24hr", "ascending": "false", "limit": 50, "offset": 0}, budget)
    except cc.HTTPFailure as e:
        res.error = e.msg
        res.requests, res.bytes_in = budget.requests, budget.bytes_in
        return
    try:
        top_raw += get_events({"order": "volume24hr", "ascending": "false", "limit": 50, "offset": 50}, budget)
    except (cc.HTTPFailure, cc.BudgetExceeded) as e:
        notes.append(f"top events page 2: {e}")

    seen, top = set(), []
    for ev in sorted(top_raw, key=lambda e: f(e.get("volume24hr")), reverse=True):
        if ev.get("id") in seen or not live_markets(ev):
            continue
        seen.add(ev.get("id"))
        top.append(ev)
    all_events = list(top)

    by_topic: dict[str, list[dict]] = {}
    listed = {e.get("id") for e in top[:12]}  # an event is listed once across TOP and BY TOPIC
    for t in TOPICS:
        try:
            evs = get_events({"tag_slug": t, "order": "volume24hr", "ascending": "false", "limit": 8}, budget)
        except (cc.HTTPFailure, cc.BudgetExceeded) as e:
            notes.append(f"topic {t}: {e}")
            continue
        for ev in evs:
            ev["_topic"] = t
            if ev.get("id") not in {e.get("id") for e in all_events}:
                all_events.append(ev)
        by_topic[t] = [e for e in evs if e.get("id") not in listed and live_markets(e)][:4]
        listed |= {e.get("id") for e in by_topic[t]}

    new_evs: list[dict] = []
    try:
        new_evs = get_events({"start_date_min": cc.iso_z(now - timedelta(hours=48)), "order": "volume24hr",
                              "ascending": "false", "limit": 50}, budget)
    except (cc.HTTPFailure, cc.BudgetExceeded) as e:
        notes.append(f"new markets: {e}")
    resolving: list[dict] = []
    try:
        resolving = get_events({"end_date_min": cc.iso_z(now), "end_date_max": cc.iso_z(now + timedelta(days=7)),
                                "order": "volume24hr", "ascending": "false", "limit": 20}, budget)
    except (cc.HTTPFailure, cc.BudgetExceeded) as e:
        notes.append(f"resolving: {e}")

    def topic_of(ev: dict) -> str:
        if ev.get("_topic"):
            return ev["_topic"]
        sl = tag_slugs(ev)
        for t in TOPICS:
            if t in sl:
                return t
        labels = [t.get("label") for t in ev.get("tags") or [] if isinstance(t, dict) and t.get("label")]
        return (labels[0] if labels else "other").lower()

    lines = cc.header(LABEL, stamp, ["Source: Polymarket Gamma + CLOB public API, sports excluded (tag 1)"])

    lines += ["", "## TOP EVENTS BY 24H VOLUME (sports excluded)", ""]
    for ev in top[:12]:
        lines.append(ev_line(ev))

    lines += ["", "## BY TOPIC", ""]
    any_topic = False
    for t in TOPICS:
        for ev in by_topic.get(t, []):
            lines.append(ev_line(ev, f"[{t}] "))
            any_topic = True
    if not any_topic:
        lines.append("- No topic events beyond the top list.")

    movers, seen_m = [], set()
    for ev in all_events:
        for m in live_markets(ev):
            if m.get("id") in seen_m:
                continue
            seen_m.add(m.get("id"))
            d = pts(m.get("oneDayPriceChange"))
            if d is None or f(m.get("liquidityNum")) < 25_000 or f(m.get("volume24hr")) < 10_000 or abs(d) < 5:
                continue
            movers.append((abs(d), ev, m))
    movers.sort(key=lambda x: x[0], reverse=True)
    lines += ["", "## 24H MOVERS (liq ≥ $25K, 24h vol ≥ $10K, move ≥ 5 pts)", ""]
    if movers:
        for _, ev, m in movers[:10]:
            lines.append(f"- [{topic_of(ev)}] {cc.one_line(m.get('question', ''), 120)} YES {yes_pct(m)} "
                         f"(1d {pts_s(m.get('oneDayPriceChange'))}, 1w {pts_s(m.get('oneWeekPriceChange'))}) | "
                         f"24h vol {usd(m.get('volume24hr'))} | liq {usd(m.get('liquidityNum'))} | {event_url(ev)}")
    else:
        lines.append("- No market moved 5 points or more with $25K+ liquidity and $10K+ 24 h volume.")

    fresh_new = [e for e in new_evs if not (tag_slugs(e) & NOT_NEW_SLUGS) and f(e.get("volume24hr")) >= 5_000
                 and live_markets(e) and (cc.to_utc(e.get("endDate")) or now) >= now]
    lines += ["", "## NEW MARKETS (started in the last 48 h, 24h vol ≥ $5K)", ""]
    if fresh_new:
        for ev in fresh_new[:8]:
            st = cc.to_utc(ev.get("startDate") or ev.get("createdAt"))
            lines.append(f"- {cc.one_line(ev.get('title', ''), 120)} — started {cc.fmt_utc(st)} | "
                         f"24h vol {usd(ev.get('volume24hr'))} | liq {usd(ev.get('liquidity'))} | "
                         f"ends {cc.fmt_utc(cc.to_utc(ev.get('endDate')), False)} | {event_url(ev)}")
    else:
        lines.append("- No new non-sports market with $5K+ 24 h volume in the last 48 h.")

    lines += ["", "## RESOLVING IN THE NEXT 7 DAYS", ""]
    rows = 0
    for ev in resolving:
        win = [m for m in live_markets(ev)
               if now < (cc.to_utc(m.get("endDate")) or now) <= now + timedelta(days=7)]
        if not win:
            continue
        m = max(win, key=lambda x: f(x.get("volume24hr")))
        lines.append(f"- {cc.one_line(ev.get('title', ''), 100)}: \"{mname(m)}\" — ends "
                     f"{cc.fmt_utc(cc.to_utc(m.get('endDate')))} | YES {yes_pct(m)} | 24h vol {usd(ev.get('volume24hr'))} | "
                     f"liq {usd(ev.get('liquidity'))} | {event_url(ev)}")
        rows += 1
        if rows >= 10:
            break
    if not rows:
        lines.append("- No non-sports event resolves in the next 7 days with 24 h volume.")

    lines += ["", "## BOOK DEPTH (top non-sports markets)", ""]
    books, tries = 0, 0
    cand = top[:12] + [e for t in TOPICS for e in by_topic.get(t, [])]
    for ev in cand:
        if books >= BOOKS_WANTED or tries >= BOOK_TRIES:
            break
        mk = sorted(live_markets(ev), key=lambda m: f(m.get("volume24hr")), reverse=True)
        if not mk or not mk[0]["_tokens"]:
            continue
        m = mk[0]
        tries += 1
        try:
            book = cc.http_json(cc.build_url(f"{CLOB}/book", {"token_id": m["_tokens"][0]}), budget)
        except cc.HTTPFailure as e:
            if e.status == 404:  # resolved market: skip, not an error
                continue
            notes.append(f"book: {e.msg}")
            continue
        except cc.BudgetExceeded as e:
            notes.append(f"book: {e}")
            break
        d = book_depth(book)
        if not d:
            continue
        liq = f(m.get("liquidityNum"))
        ratio = f"{f(m.get('volume24hr')) / liq:.1f}" if liq > 0 else "n/a"
        lines.append(f"- {cc.one_line(m.get('question', ''), 120)}: mid {100 * d['mid']:.1f}¢ | "
                     f"spread {100 * d['spread']:.1f}¢ | depth within 2¢: {usd(d['bid0.02'])} bid / {usd(d['ask0.02'])} ask | "
                     f"within 5¢: {usd(d['bid0.05'])} / {usd(d['ask0.05'])} | 24h vol ÷ liquidity {ratio} | {event_url(ev)}")
        books += 1
    if not books:
        lines.append("- No order book could be read.")

    if budget.exhausted:
        lines.insert(3, f"## NOTE: budget reached after {budget.requests} requests; partial pull")
    lines.append("")
    res.text = "\n".join(lines)
    res.ok = bool(top)
    res.requests, res.bytes_in = budget.requests, budget.bytes_in
    notes.append(f"events: top {len(top)}, all {len(all_events)}, new {len(fresh_new)}/{len(new_evs)}, "
                 f"resolving {rows}, books {books}/{tries}")


if __name__ == "__main__":
    sys.exit(cc.run_main(NAME, LABEL, collect))
