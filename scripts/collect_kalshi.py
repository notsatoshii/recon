#!/usr/bin/env python3
"""
RECON Kalshi collector (Phase E, docs/v2/phase-e-collectors-spec.md §3.2).

Public market data, no key. Writes data-sources/kalshi/latest.md, status.json and prev.json.

Selection: the desk watchlist (config/kalshi_series.json) first, then per category the series
list walked in lifetime-volume order, keeping the first 5 series that have an open event
(at most 12 probes per category). Events calls run in 4 threads.

    python3 scripts/collect_kalshi.py         exit 0 when ok, 1 otherwise
"""
from __future__ import annotations

import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collector_common as cc  # noqa: E402

NAME, LABEL = "kalshi", "Kalshi"
BASE = "https://api.elections.kalshi.com/trade-api/v2"
CATEGORIES = ["Economics", "Financials", "Crypto", "Politics", "Science and Technology",
              "Companies", "World"]
SKIP_FREQ = {"hourly", "fifteen_min"}
SKIP_TICKER = re.compile(r"15M|1H$")
KEEP_PER_CAT, PROBES_PER_CAT = 5, 12
EVENTS_PER_SERIES = 3
LADDER_SERIES = {"KXBTCD": "BTC", "KXETHD": "ETH"}
DEFAULT_WATCHLIST = ["KXFEDDECISION", "KXFED", "KXRATECUTCOUNT", "KXCPI", "KXPAYROLLS", "KXBTCD",
                     "KXETHD", "KXBTCMAXY", "KXLLM1", "KXTOPMODEL"]


def f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def prob(m: dict) -> float | None:
    """Mid of yes bid/ask when both exist and the spread is at most 10c, else last price (0-1)."""
    bid, ask = m.get("yes_bid_dollars"), m.get("yes_ask_dollars")
    if bid not in (None, "") and ask not in (None, ""):
        b, a = f(bid), f(ask)
        if a > 0 and a - b <= 0.10 + 1e-9:
            return (a + b) / 2
    last = m.get("last_price_dollars")
    return f(last) if last not in (None, "") else None


def change_pts(m: dict, now: datetime | None = None) -> float | None:
    """24 h change in points, None when there is no baseline (market opened in the last 24 h,
    or no previous price: Kalshi reports 0 then, which would read as a +99 pt move)."""
    if m.get("previous_price_dollars") in (None, "") or m.get("last_price_dollars") in (None, ""):
        return None
    opened = cc.to_utc(m.get("open_time"))
    if now is not None and opened is not None and opened > now - timedelta(hours=24):
        return None
    if f(m["previous_price_dollars"]) <= 0:
        return None
    return 100 * (f(m["last_price_dollars"]) - f(m["previous_price_dollars"]))


def ch_str(ch: float | None) -> str:
    return "24h n/a, new" if ch is None else f"24h {ch:+.0f} pts"


def pct(p: float | None) -> str:
    if p is None:
        return "n/a"
    v = 100 * p
    return f"{v:.1f}%" if v < 1 or v > 99 else f"{v:.0f}%"


def num(x: float) -> str:
    return f"{x:,.0f}"


def url_for(series: str) -> str:
    return f"https://kalshi.com/markets/{series.lower()}"


def load_watchlist() -> list[str]:
    data = cc.load_json(cc.CONFIG_DIR / "kalshi_series.json", None)
    tickers = data.get("watchlist") if isinstance(data, dict) else data
    out, seen = [], set()
    for t in tickers or DEFAULT_WATCHLIST:
        t = str(t).strip().upper()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def events_for(series: str, budget: cc.Budget, now: datetime) -> list[dict]:
    """Open events of a series with nested markets, close_time > now, nearest first."""
    data = cc.http_json(cc.build_url(f"{BASE}/events", {
        "series_ticker": series, "status": "open", "with_nested_markets": "true", "limit": 10}), budget)
    return live_events(data.get("events") or [], now)


def live_events(events: list[dict], now: datetime) -> list[dict]:
    out = []
    for ev in events:
        mk = [m for m in ev.get("markets") or []
              if (cc.to_utc(m.get("close_time")) or now) > now and m.get("status") in (None, "active", "open")]
        if not mk:
            continue
        ev = dict(ev)
        ev["markets"] = mk
        ev["_close"] = min(cc.to_utc(m["close_time"]) for m in mk)
        ev["_vol24"] = sum(f(m.get("volume_24h_fp")) for m in mk)
        ev["_oi"] = sum(f(m.get("open_interest_fp")) for m in mk)
        out.append(ev)
    out.sort(key=lambda e: e["_close"])
    return out


def ladder_event(series: str, budget: cc.Budget, now: datetime) -> dict | None:
    """Nearest daily 5 pm ET event of an hourly crypto series closing >= 12 h after now."""
    data = cc.http_json(cc.build_url(f"{BASE}/events", {
        "series_ticker": series, "status": "open", "limit": 200}), budget)
    cands = []
    for ev in data.get("events") or []:
        t = ev.get("event_ticker", "")
        when = cc.to_utc(ev.get("strike_date"))
        if not t.endswith("17") or when is None:
            continue
        if when >= now + timedelta(hours=12):
            cands.append((when, t))
    if not cands:
        return None
    _, ticker = min(cands)
    full = cc.http_json(cc.build_url(f"{BASE}/events/{ticker}", {"with_nested_markets": "true"}), budget)
    ev = full.get("event") or {}
    if "markets" not in ev:
        ev["markets"] = full.get("markets") or []
    evs = live_events([ev], now)
    return evs[0] if evs else None


def category_walk(cat: str, skip: set[str], budget: cc.Budget, now: datetime, notes: list[str]) -> list[tuple[str, str, list[dict]]]:
    data = cc.http_json(cc.build_url(f"{BASE}/series", {"category": cat, "include_volume": "true"}), budget)
    series = data.get("series") or []
    series.sort(key=lambda s: f(s.get("volume_fp", s.get("volume"))), reverse=True)
    kept, probes = [], 0
    for s in series:
        t = s.get("ticker", "")
        if not t or t in skip or s.get("frequency") in SKIP_FREQ or SKIP_TICKER.search(t):
            continue
        if len(kept) >= KEEP_PER_CAT or probes >= PROBES_PER_CAT:
            break
        probes += 1
        try:
            evs = events_for(t, budget, now)
        except cc.BudgetExceeded:
            raise
        except cc.HTTPFailure as e:
            notes.append(f"{cat}/{t}: {e.msg}")
            continue
        if evs:
            kept.append((t, cat, evs[:EVENTS_PER_SERIES]))
    notes.append(f"{cat}: kept {len(kept)} of {probes} probed series")
    return kept


def top_markets(ev: dict, n: int = 3) -> list[dict]:
    mk = [m for m in ev["markets"] if prob(m) is not None]
    if ev.get("mutually_exclusive"):
        mk.sort(key=lambda m: prob(m) or 0, reverse=True)
    else:
        mk.sort(key=lambda m: (f(m.get("volume_24h_fp")), prob(m) or 0), reverse=True)
    return mk[:n]


def mlabel(m: dict) -> str:
    return cc.one_line(m.get("yes_sub_title") or m.get("subtitle") or m.get("title") or m.get("ticker", ""), 60)


def ev_title(ev: dict) -> str:
    t = cc.one_line(ev.get("title", ""), 110)
    sub = cc.one_line(ev.get("sub_title", ""), 40)
    return f"{t} ({sub})" if sub and sub.lower() not in t.lower() else t


def tops_str(ev: dict, now: datetime) -> str:
    parts = []
    for m in top_markets(ev):
        parts.append(f"\"{mlabel(m)}\" {pct(prob(m))} ({ch_str(change_pts(m, now))})")
    return " · ".join(parts)


def ladder_line(sym: str, ev: dict) -> str | None:
    pts = []
    for m in ev["markets"]:
        if m.get("strike_type") not in ("greater", "greater_or_equal") or m.get("floor_strike") is None:
            continue
        p = prob(m)
        if p is not None:
            pts.append((f(m["floor_strike"]), p))
    pts.sort()
    if len(pts) < 3:
        return None

    def strike_at(target: float) -> float | None:
        # P(price > K) falls as K rises; find where it crosses target.
        for (k1, p1), (k2, p2) in zip(pts, pts[1:]):
            if p1 >= target >= p2:
                if p1 == p2:
                    return (k1 + k2) / 2
                return k1 + (p1 - target) * (k2 - k1) / (p1 - p2)
        return None

    med, lo, hi = strike_at(0.5), strike_at(0.75), strike_at(0.25)
    if med is None:
        return None
    close = ev["_close"]
    et = close - timedelta(hours=4)  # display only; Kalshi's daily crypto expiry is 5 pm ET
    rng = f"{'$' + num(lo) if lo else 'n/a'}–{'$' + num(hi) if hi else 'n/a'}"
    return (f"- {sym} at {cc.fmt_utc(close)} ({et.strftime('%b %d')} 5 pm ET close): market-implied "
            f"median ${num(med)} (25–75 %: {rng}), from {len(pts)} strikes | event "
            f"{ev.get('event_ticker')} | 24h vol {num(ev['_vol24'])} contracts | {url_for(ev.get('series_ticker', ''))}")


def macro_line(series: str, ev: dict) -> str | None:
    mk = [m for m in ev["markets"] if prob(m) is not None]
    if not mk:
        return None
    if ev.get("mutually_exclusive") or series == "KXFEDDECISION":
        mk.sort(key=lambda m: prob(m) or 0, reverse=True)
        parts = [f"{mlabel(m)} {pct(prob(m))}" for m in mk[:6]]
    else:
        mk.sort(key=lambda m: f(m.get("floor_strike")))
        idx = [i for i, m in enumerate(mk) if 0.02 <= (prob(m) or 0) <= 0.98]
        lo, hi = (max(0, idx[0] - 1), min(len(mk), idx[-1] + 2)) if idx else (0, len(mk))
        parts = [f"{mlabel(m)} {pct(prob(m))}" for m in mk[lo:hi][:8]]
    return (f"- {ev_title(ev)}: " + " · ".join(parts) +
            f" | closes {cc.fmt_utc(ev['_close'])} | 24h vol {num(ev['_vol24'])} | {url_for(series)}")


def collect(res: cc.SourceResult, stamp: datetime) -> None:
    budget = cc.Budget(seconds=90, requests=110, mbytes=40)
    now = stamp
    watch = load_watchlist()
    picked: dict[str, tuple[str, list[dict]]] = {}  # series -> (category, events)
    ladders: dict[str, dict] = {}
    notes = res.notes

    def watch_task(t: str):
        if t in LADDER_SERIES:
            ev = ladder_event(t, budget, now)
            return t, ([ev] if ev else [])
        return t, events_for(t, budget, now)[:EVENTS_PER_SERIES]

    with ThreadPoolExecutor(4) as pool:
        wf = [pool.submit(watch_task, t) for t in watch]          # watchlist first
        cf = [pool.submit(category_walk, c, set(watch), budget, now, notes) for c in CATEGORIES]
        for fut in wf:
            try:
                t, evs = fut.result()
            except cc.BudgetExceeded as e:
                notes.append(f"watchlist: {e}")
                continue
            except cc.HTTPFailure as e:
                notes.append(f"watchlist {e.url.split('series_ticker=')[-1][:20]}: {e.msg}")
                continue
            if not evs:
                notes.append(f"watchlist {t}: no open event, skipped")
                continue
            if t in LADDER_SERIES:
                ladders[t] = evs[0]
            picked[t] = (evs[0].get("category") or "Watchlist", evs)
        for c, fut in zip(CATEGORIES, cf):
            try:
                for t, cat, evs in fut.result():
                    picked.setdefault(t, (cat, evs))
            except cc.BudgetExceeded as e:
                notes.append(f"{c}: {e}")
            except cc.HTTPFailure as e:
                notes.append(f"{c}: series list {e.msg}")

    res.requests, res.bytes_in = budget.requests, budget.bytes_in
    if not picked:
        res.error = notes[-1] if notes else "no series with open events"
        return

    events = []
    for series, (cat, evs) in picked.items():
        for ev in evs:
            ev["_cat"] = cat
            ev["_series"] = series
            events.append(ev)

    lines = cc.header(LABEL, stamp, ["Source: Kalshi public market data (api.elections.kalshi.com), no key"])
    if budget.exhausted:
        lines.append(f"## NOTE: budget reached after {budget.requests} requests; partial pull")

    lines += ["", "## TOP EVENTS BY 24H VOLUME", ""]
    top = sorted(events, key=lambda e: e["_vol24"], reverse=True)[:12]
    for ev in top:
        lines.append(f"- [{ev['_cat']}] {ev_title(ev)} — 24h vol {num(ev['_vol24'])} contracts | "
                     f"OI {num(ev['_oi'])} | next close {cc.fmt_utc(ev['_close'], False)} | top: {tops_str(ev, now)} | "
                     f"{url_for(ev['_series'])}")

    movers = []
    for ev in events:
        for m in ev["markets"]:
            ch = change_pts(m, now)
            if ch is not None and f(m.get("volume_24h_fp")) >= 500 and abs(ch) >= 5:
                movers.append((abs(ch), ev, m, ch))
    movers.sort(key=lambda x: x[0], reverse=True)
    lines += ["", "## 24H MOVERS (24h volume ≥ 500, move ≥ 5 pts)", ""]
    if movers:
        for _, ev, m, ch in movers[:10]:
            lines.append(f"- [{ev['_cat']}] {ev_title(ev)}: \"{mlabel(m)}\" {pct(prob(m))} (24h {ch:+.0f} pts) | "
                         f"24h vol {num(f(m.get('volume_24h_fp')))} | {url_for(ev['_series'])}")
    else:
        lines.append("- No market moved 5 points or more on 500+ contracts in 24 h (Kalshi).")

    soon = [e for e in events if now < e["_close"] <= now + timedelta(days=7)
            and e["_series"] not in LADDER_SERIES]
    soon.sort(key=lambda e: e["_vol24"], reverse=True)
    lines += ["", "## CLOSING IN THE NEXT 7 DAYS", ""]
    if soon:
        for ev in soon[:10]:
            lines.append(f"- [{ev['_cat']}] {ev_title(ev)} — next close {cc.fmt_utc(ev['_close'])} | top: {tops_str(ev, now)} | "
                         f"24h vol {num(ev['_vol24'])} | {url_for(ev['_series'])}")
    else:
        lines.append("- No tracked Kalshi event closes in the next 7 days.")

    lad = [l for l in (ladder_line(LADDER_SERIES[s], ladders[s]) for s in LADDER_SERIES if s in ladders) if l]
    if lad:
        lines += ["", "## CRYPTO PRICE LADDERS (nearest daily close ≥ 12 h out)", ""] + lad

    mac = []
    for s in ("KXFEDDECISION", "KXFED"):
        if s in picked:
            l = macro_line(s, picked[s][1][0])
            if l:
                mac.append(l)
    if mac:
        lines += ["", "## MACRO", ""] + mac

    lines.append("")
    res.text = "\n".join(lines)
    res.ok = True
    # Last prices for the next run (checks whether previous_price_dollars is really 24 h back).
    cc.save_json(cc.source_dir(NAME) / "prev.json", {
        "at": cc.iso_z(stamp),
        "last": {m["ticker"]: m.get("last_price_dollars") for ev in events for m in ev["markets"] if m.get("ticker")}})
    notes.append(f"series kept: {len(picked)}, events: {len(events)}")


if __name__ == "__main__":
    sys.exit(cc.run_main(NAME, LABEL, collect))
