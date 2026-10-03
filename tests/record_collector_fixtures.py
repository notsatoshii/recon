#!/usr/bin/env python3
"""
Record the Phase E collectors' endpoint responses as test fixtures (spec §5, "fixtures").

Run on the droplet (Polymarket answers HTTP 451 from Korea):

    python3 tests/record_collector_fixtures.py [--out tests/fixtures/collectors]

It runs the four collectors live in a scratch RECON_HOME with a frozen RECON_COLLECTOR_NOW and
RECON_COLLECTOR_RECORD set, so every response is saved under the name collector_common's
_fixture_path expects (the first 16 hex digits of the URL's sha1). Each response is then trimmed
to the fields the collectors read and to 200 KB at most, written to --out with manifest.json
(the frozen "now", the URL of every file, the live status lines), and the collectors are run once
more from the trimmed fixtures to check that each still produces an ok file.

The daily data-sources/ files are not touched. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COLLECTORS = ["polymarket", "kalshi", "changelogs", "zdnet_kr"]
MAX_BYTES = 200 * 1024
NOW = ""  # the frozen collection time, set in main()

PM_EVENT_KEYS = {"id", "title", "slug", "volume24hr", "volume", "liquidity", "openInterest", "endDate",
                 "startDate", "createdAt", "tags", "markets", "negRisk", "enableNegRisk", "closed", "active"}
PM_MARKET_KEYS = {"id", "question", "groupItemTitle", "outcomes", "outcomePrices", "oneDayPriceChange",
                  "oneWeekPriceChange", "volume24hr", "liquidityNum", "bestBid", "bestAsk", "spread", "endDate",
                  "clobTokenIds", "slug", "sportsMarketType", "gameId", "closed", "active"}
KS_SERIES_KEYS = {"ticker", "title", "frequency", "volume_fp", "volume", "category"}
KS_EVENT_KEYS = {"event_ticker", "series_ticker", "title", "sub_title", "category", "mutually_exclusive",
                 "strike_date", "markets"}
KS_MARKET_KEYS = {"ticker", "title", "subtitle", "yes_sub_title", "yes_bid_dollars", "yes_ask_dollars",
                  "last_price_dollars", "previous_price_dollars", "volume_24h_fp", "volume_fp",
                  "open_interest_fp", "close_time", "open_time", "floor_strike", "cap_strike", "strike_type",
                  "status"}
GH_KEYS = {"id", "tag_name", "name", "draft", "prerelease", "published_at", "created_at", "html_url", "body"}
TEXT_TAGS = {"description", "encoded", "content", "summary"}


def keep(d: dict, keys: set[str]) -> dict:
    return {k: v for k, v in d.items() if k in keys}


def f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# ── trimming ─────────────────────────────────────────────────

def trim_pm_events(data, n_events: int, n_markets: int):
    evs = data if isinstance(data, list) else data.get("data") or data.get("events") or []
    out = []
    for ev in evs[:n_events]:
        e = keep(ev, PM_EVENT_KEYS)
        e["tags"] = [keep(t, {"id", "slug", "label"}) for t in ev.get("tags") or [] if isinstance(t, dict)]
        mk = sorted(ev.get("markets") or [], key=lambda m: f(m.get("volume24hr")), reverse=True)
        e["markets"] = [keep(m, PM_MARKET_KEYS) for m in mk[:n_markets]]
        out.append(e)
    return out


def trim_book(data):
    bids = data.get("bids") or []
    asks = data.get("asks") or []
    if bids and asks:
        mid = (max(f(l.get("price")) for l in bids) + min(f(l.get("price")) for l in asks)) / 2
        bids = [l for l in bids if abs(f(l.get("price")) - mid) <= 0.15]
        asks = [l for l in asks if abs(f(l.get("price")) - mid) <= 0.15]
    return {**{k: v for k, v in data.items() if k not in ("bids", "asks")}, "bids": bids, "asks": asks}


def trim_ks(url: str, data, scale: int):
    if "/series" in url:
        series = sorted(data.get("series") or [], key=lambda s: f(s.get("volume_fp", s.get("volume"))),
                        reverse=True)
        return {"series": [keep(s, KS_SERIES_KEYS) for s in series[:max(20, 80 // scale)]]}
    if re.search(r"/events/[^/?]+", url):  # one event with nested markets (ladder)
        ev = keep(data.get("event") or {}, KS_EVENT_KEYS)
        ev["markets"] = [keep(m, KS_MARKET_KEYS) for m in (data.get("event") or {}).get("markets") or []]
        out = {"event": ev}
        if data.get("markets"):
            out["markets"] = [keep(m, KS_MARKET_KEYS) for m in data["markets"]]
        return out
    events = []
    for ev in data.get("events") or []:
        e = keep(ev, KS_EVENT_KEYS)
        if "markets" in ev:
            e["markets"] = [keep(m, KS_MARKET_KEYS) for m in ev.get("markets") or []]
        events.append(e)
    if "with_nested_markets" not in url:  # the ladder's event list: only tickers and dates are read
        events = [keep(e, {"event_ticker", "series_ticker", "strike_date", "title"}) for e in events]
    if "with_nested_markets" in url:  # the collector shows 3 events a series, nearest close first
        def next_close(e: dict) -> str:  # ISO Z strings compare in time order
            return min([m.get("close_time") or "9" for m in e.get("markets") or []
                        if (m.get("close_time") or "9") > NOW] or ["9"])
        events.sort(key=next_close)
        return {"events": events[:max(2, 5 // scale)]}
    return {"events": events[:max(5, 200 // scale)]}


def trim_gh(data, body_chars: int):
    out = []
    for r in data if isinstance(data, list) else []:
        r = keep(r, GH_KEYS)
        r["body"] = (r.get("body") or "")[:body_chars]
        out.append(r)
    return out


def trim_xml(body: bytes, chars: int, max_items: int) -> bytes:
    root = ET.fromstring(body)
    for el in root.iter():
        if el.tag.split("}")[-1] in TEXT_TAGS and el.text and len(el.text) > chars:
            el.text = el.text[:chars]
    for parent in list(root.iter()):
        items = [c for c in list(parent) if c.tag.split("}")[-1] in ("item", "entry")]
        for c in items[max_items:]:
            parent.remove(c)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def trim(url: str, body: bytes) -> bytes:
    """Trimmed body, at most MAX_BYTES; unknown shapes are kept as they are."""
    for scale in (1, 2, 4, 8):
        try:
            if "gamma-api.polymarket.com" in url:
                data = json.loads(body)
                out = json.dumps(trim_pm_events(data, max(4, 25 // scale), max(2, 6 // scale)),
                                 ensure_ascii=False).encode()
            elif "clob.polymarket.com" in url:
                out = json.dumps(trim_book(json.loads(body)), ensure_ascii=False).encode()
            elif "kalshi.com" in url:
                out = json.dumps(trim_ks(url, json.loads(body), scale), ensure_ascii=False).encode()
            elif "api.github.com" in url:
                out = json.dumps(trim_gh(json.loads(body), 1200 // scale), ensure_ascii=False).encode()
            else:
                out = trim_xml(body, 700 // scale, 60 // scale)
        except (ValueError, ET.ParseError):
            out = body[:MAX_BYTES]
        if len(out) <= MAX_BYTES:
            return out
    return out


# ── run ──────────────────────────────────────────────────────

def run_collectors(home: Path, env_extra: dict) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("RECON_COLLECTOR_FIXTURES", "RECON_COLLECTOR_RECORD", "RECON_FRESH_HOURS")}
    env.update({"RECON_HOME": str(home), "PYTHONIOENCODING": "utf-8", **env_extra})
    status = {}
    for name in COLLECTORS:
        p = subprocess.run([sys.executable, str(REPO / "scripts" / f"collect_{name}.py")], env=env,
                           capture_output=True, text=True, encoding="utf-8", timeout=180)
        status[name] = (p.stdout.strip().splitlines() or [p.stderr.strip()[-300:]])[0].strip()
        print(f"[{name}] exit {p.returncode}: {p.stdout.strip()}", flush=True)
        if p.stderr.strip():
            print(p.stderr.strip()[-1500:], flush=True)
    return status


def scratch_home() -> Path:
    home = Path(tempfile.mkdtemp(prefix="recon-fx-"))
    shutil.copytree(REPO / "config", home / "config",
                    ignore=shutil.ignore_patterns("*.db*", "agent_memory", "agent_state", "*.yaml", "*.csv"))
    (home / "data-sources").mkdir()
    return home


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "tests" / "fixtures" / "collectors"))
    ap.add_argument("--now", default=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z"))
    args = ap.parse_args()
    out = Path(args.out)
    global NOW
    NOW = args.now

    home = scratch_home()
    raw = home / "raw"
    print(f"recording with now={args.now} into {raw}", flush=True)
    live = run_collectors(home, {"RECON_COLLECTOR_NOW": args.now, "RECON_COLLECTOR_RECORD": str(raw)})

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    files = {}
    for line in (raw / "urls.tsv").read_text(encoding="utf-8").splitlines():
        stem, status, url = line.split("\t", 2)
        body = (raw / f"{stem}.body").read_bytes()
        small = trim(url, body) if status == "200" else body[:4096]
        (out / f"{stem}.body").write_bytes(small)
        st = out / f"{stem}.status"
        if status != "200":
            st.write_text(status)
        elif st.exists():  # a retry that succeeded replaces the failed attempt
            st.unlink()
        files[stem] = {"url": url, "status": int(status), "bytes_live": len(body), "bytes": len(small)}

    replay_home = scratch_home()
    replay = run_collectors(replay_home, {"RECON_COLLECTOR_NOW": args.now, "RECON_COLLECTOR_FIXTURES": str(out)})
    manifest = {"now": args.now, "recorded_on": socket.gethostname(),
                "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "live": live, "replay": replay, "files": dict(sorted(files.items()))}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(v["bytes"] for v in files.values())
    print(f"{len(files)} fixtures, {total / 1024:.0f} KB (live {sum(v['bytes_live'] for v in files.values()) / 1e6:.1f} MB)")
    for name in COLLECTORS:
        shutil.copy(replay_home / "data-sources" / name / "latest.md", out / f"replay_{name}.md")
    shutil.rmtree(home, ignore_errors=True)
    shutil.rmtree(replay_home, ignore_errors=True)
    bad = [n for n, s in replay.items() if ": ok," not in s]
    if bad:
        print(f"replay not ok: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
