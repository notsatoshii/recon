#!/usr/bin/env python3
"""
World Monitor Data Extraction
Pulls real intelligence data from World Monitor's Redis cache (seeded from 65+ sources).
Falls back to direct free APIs if WM container is not running.

Output: /home/recon/recon/data-sources/worldmonitor/latest.md
"""

import json
import os
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RECON_HOME = Path("/home/recon/recon")
OUTPUT_FILE = RECON_HOME / "data-sources" / "worldmonitor" / "latest.md"


def get_json(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RECON/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def redis_get(key):
    """Pull data directly from World Monitor's Redis container."""
    try:
        result = subprocess.run(
            ["docker", "exec", "worldmonitor-redis", "redis-cli", "GET", key],
            capture_output=True, text=True, timeout=10
        )
        raw = result.stdout.strip()
        if raw and raw != "(nil)":
            return json.loads(raw)
    except Exception:
        pass
    return None


def wm_available():
    """Check if World Monitor Redis is running."""
    try:
        result = subprocess.run(
            ["docker", "exec", "worldmonitor-redis", "redis-cli", "PING"],
            capture_output=True, text=True, timeout=5
        )
        return "PONG" in result.stdout
    except Exception:
        return False


def collect_from_wm():
    """Pull intelligence data from World Monitor Redis."""
    out = []

    # ── GDELT Global Intelligence ───────────────────────────
    gdelt = redis_get("intelligence:gdelt-intel:v1")
    if gdelt and gdelt.get("topics"):
        out.append("## GLOBAL INTELLIGENCE (GDELT)\n")
        for topic in gdelt["topics"][:8]:
            tid = topic.get("id", "?")
            articles = topic.get("articles", [])
            out.append(f"### {tid.upper()} ({len(articles)} reports)")
            for a in articles[:4]:
                title = a.get("title", "?")[:150]
                source = a.get("source", "?")
                out.append(f"- [{source}] {title}")
            out.append("")

    # ── Prediction Markets ──────────────────────────────────
    predictions = redis_get("prediction:markets-bootstrap:v1")
    if predictions:
        out.append("## PREDICTION MARKETS (Polymarket)\n")
        for category in ["geopolitical", "finance", "tech"]:
            markets = predictions.get(category, [])
            if markets:
                out.append(f"### {category.upper()}")
                for m in markets[:5]:
                    title = m.get("title", "?")[:120]
                    yes = m.get("yesPrice", 0)
                    vol = m.get("volume", 0)
                    out.append(f"- {title} — YES: {yes}% | vol: ${vol:,.0f}")
                out.append("")

    # ── Crypto Markets ──────────────────────────────────────
    crypto = redis_get("market:crypto:v1")
    if crypto and crypto.get("quotes"):
        out.append("## CRYPTO MARKETS (World Monitor)\n")
        for q in crypto["quotes"][:10]:
            name = q.get("name", "?")
            symbol = q.get("symbol", "?")
            price = q.get("price", 0)
            change = q.get("change", 0)
            out.append(f"- {name} ({symbol}): ${price:,.2f} ({change:+.2f}%)")
        out.append("")

    # ── Stablecoins ─────────────────────────────────────────
    stables = redis_get("market:stablecoins:v1")
    if stables:
        summary = stables.get("summary", {})
        out.append("## STABLECOIN HEALTH\n")
        out.append(f"- Total market cap: ${summary.get('totalMarketCap', 0):,.0f}")
        out.append(f"- 24h volume: ${summary.get('totalVolume24h', 0):,.0f}")
        out.append(f"- Health status: {summary.get('healthStatus', '?')}")
        out.append(f"- Depegged: {summary.get('depeggedCount', 0)}")
        for s in stables.get("stablecoins", [])[:5]:
            name = s.get("name", "?")
            mcap = s.get("marketCap", 0)
            peg = s.get("pegDeviation", 0)
            out.append(f"- {name}: mcap ${mcap:,.0f} peg deviation {peg:+.4f}")
        out.append("")

    # ── Economic Calendar ───────────────────────────────────
    econ = redis_get("economic:econ-calendar:v1")
    if econ and econ.get("events"):
        out.append("## ECONOMIC CALENDAR\n")
        high_impact = [e for e in econ["events"] if e.get("impact") == "high"][:10]
        for e in high_impact:
            event = e.get("event", "?")
            country = e.get("country", "?")
            date = e.get("date", "?")
            prev = e.get("previous", "")
            est = e.get("estimate", "")
            out.append(f"- [{date}] {country}: {event} (prev: {prev}, est: {est})")
        out.append("")

    # ── Unrest Events ───────────────────────────────────────
    unrest = redis_get("unrest:events:v1")
    if unrest and unrest.get("events"):
        out.append("## UNREST & PROTESTS\n")
        for e in unrest["events"][:8]:
            title = e.get("title", "?")
            etype = e.get("eventType", "?").replace("UNREST_EVENT_TYPE_", "")
            country = e.get("country", "?")
            reports = e.get("occurrences", 0)
            out.append(f"- {country}: {title} [{etype}] ({reports} reports)")
        out.append("")

    # ── Forecasts & Predictions ─────────────────────────────
    forecasts = redis_get("forecast:predictions:v2")
    if forecasts and forecasts.get("predictions"):
        out.append("## AI FORECASTS (World Monitor)\n")
        for f in forecasts["predictions"][:8]:
            title = f.get("title", "?")[:120]
            domain = f.get("domain", "?")
            region = f.get("region", "?")
            scenario = f.get("scenario", "")[:150]
            out.append(f"- [{domain}/{region}] {title}")
            if scenario:
                out.append(f"  {scenario}")
        out.append("")

    # ── Regulatory Actions ──────────────────────────────────
    reg = redis_get("seed-meta:regulatory:actions")
    if not reg:
        # Try alternative key
        reg_data = redis_get("regulatory:actions:v1")
        if reg_data:
            reg = reg_data
    if reg:
        out.append("## REGULATORY ACTIONS\n")
        if isinstance(reg, list):
            for r in reg[:5]:
                out.append(f"- {json.dumps(r)[:200]}")
        elif isinstance(reg, dict):
            out.append(f"- {json.dumps(reg)[:300]}")
        out.append("")

    # ── Cyber Threats ───────────────────────────────────────
    cyber = redis_get("seed-meta:cyber:threats")
    if cyber:
        out.append("## CYBER THREATS\n")
        if isinstance(cyber, dict):
            for k, v in list(cyber.items())[:5]:
                out.append(f"- {k}: {json.dumps(v)[:150]}")
        out.append("")

    # ── Conflict Intelligence (ACLED) ──────────────────────
    conflict = redis_get("seed-meta:conflict:acled-intel")
    if conflict and isinstance(conflict, dict):
        events = conflict.get("events", conflict.get("data", []))
        if isinstance(events, list) and events:
            out.append("## CONFLICT INTELLIGENCE\n")
            for e in events[:8]:
                if isinstance(e, dict):
                    country = e.get("country", "?")
                    etype = e.get("event_type", e.get("type", "?"))
                    notes = e.get("notes", e.get("description", ""))[:150]
                    out.append(f"- {country}: {etype}")
                    if notes: out.append(f"  {notes}")
            out.append("")

    # ── Iran Events ────────────────────────────────────────
    iran = redis_get("seed-meta:conflict:iran-events")
    if not iran:
        iran = redis_get("intelligence:snapshot:v1:mena:latest")
    if iran and isinstance(iran, dict):
        out.append("## IRAN / MIDDLE EAST\n")
        if "events" in iran:
            for e in iran["events"][:5]:
                out.append(f"- {json.dumps(e)[:200]}")
        elif "summary" in iran:
            out.append(f"- {str(iran['summary'])[:500]}")
        elif "analysis" in iran:
            out.append(f"- {str(iran['analysis'])[:500]}")
        else:
            # Try to extract whatever we can
            for k in ["headline", "title", "description", "content"]:
                if k in iran:
                    out.append(f"- {str(iran[k])[:300]}")
                    break
        out.append("")

    # ── Hormuz Tracker ─────────────────────────────────────
    hormuz = redis_get("supply_chain:hormuz_tracker:v1")
    if hormuz and isinstance(hormuz, dict):
        out.append("## STRAIT OF HORMUZ TRACKER\n")
        status = hormuz.get("status", hormuz.get("summary", ""))
        if status:
            out.append(f"- Status: {str(status)[:300]}")
        for k in ["vessels", "transits", "disruptions", "blockade"]:
            if k in hormuz:
                out.append(f"- {k}: {json.dumps(hormuz[k])[:200]}")
        out.append("")

    # ── Regional Intelligence Snapshots ────────────────────
    regions = ["global", "mena", "europe", "north-america", "east-asia"]
    briefs = redis_get("intelligence:regional-briefs:summary:v1")
    if briefs and isinstance(briefs, dict):
        out.append("## REGIONAL INTELLIGENCE BRIEFS\n")
        for region in regions:
            brief = briefs.get(region, "")
            if brief:
                out.append(f"### {region.upper()}")
                out.append(f"{str(brief)[:300]}")
                out.append("")

    # ── Sanctions Pressure ─────────────────────────────────
    sanctions = redis_get("seed-meta:conflict:sanctions-pressure")
    if sanctions and isinstance(sanctions, dict):
        out.append("## SANCTIONS & PRESSURE\n")
        for k, v in list(sanctions.items())[:5]:
            out.append(f"- {k}: {json.dumps(v)[:150]}")
        out.append("")

    # ── Market Sentiment ───────────────────────────────────
    sentiment = redis_get("market:aaii-sentiment:v1")
    if sentiment and isinstance(sentiment, dict):
        out.append("## MARKET SENTIMENT (AAII)\n")
        bull = sentiment.get("bullish", "?")
        bear = sentiment.get("bearish", "?")
        neutral = sentiment.get("neutral", "?")
        out.append(f"- Bullish: {bull}% | Bearish: {bear}% | Neutral: {neutral}%")
        out.append("")

    # ── Disease Outbreaks ──────────────────────────────────
    disease = redis_get("health:disease-outbreaks:v1")
    if disease and isinstance(disease, (dict, list)):
        items = disease if isinstance(disease, list) else disease.get("outbreaks", disease.get("events", []))
        if isinstance(items, list) and items:
            out.append("## DISEASE OUTBREAKS\n")
            for d in items[:5]:
                if isinstance(d, dict):
                    name = d.get("disease", d.get("name", "?"))
                    country = d.get("country", d.get("location", "?"))
                    status = d.get("status", "")
                    out.append(f"- {name} — {country} {status}")
            out.append("")

    return out


def collect_direct_fallback():
    """Direct API fallback when World Monitor is not running."""
    out = []

    # USGS Earthquakes (always free, always works)
    out.append("## SEISMIC ACTIVITY (24h)\n")
    eq = get_json("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson")
    if eq:
        features = eq.get("features", [])
        if features:
            for f in features[:8]:
                props = f.get("properties", {})
                mag = props.get("mag", "?")
                place = props.get("place", "?")
                ts = props.get("time", 0)
                t = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%H:%M UTC") if ts else "?"
                out.append(f"- M{mag} — {place} ({t})")
        else:
            out.append("- No M4.5+ earthquakes in last 24h")
    out.append("")

    # Fear & Greed (already in on-chain, but important for geopolitical context)
    out.append("## MARKET FEAR INDEX\n")
    fng = get_json("https://api.alternative.me/fng/?limit=1")
    if fng and fng.get("data"):
        d = fng["data"][0]
        out.append(f"- Fear & Greed: {d.get('value','?')}/100 ({d.get('value_classification','?')})")
    out.append("")

    return out


def main():
    now = datetime.now(timezone.utc)
    lines = [
        f"# World Monitor Intelligence",
        f"## {now.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    if wm_available():
        print("WorldMonitor: Redis available, pulling intelligence data...")
        wm_data = collect_from_wm()
        if wm_data:
            lines.extend(wm_data)
            lines.append(f"\n*Source: World Monitor (79 feeds, {now.strftime('%H:%M UTC')})*")
        else:
            print("WorldMonitor: Redis has no parsed data, using fallback...")
            lines.extend(collect_direct_fallback())
    else:
        print("WorldMonitor: Container not running, using direct APIs...")
        lines.extend(collect_direct_fallback())

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text("\n".join(lines))
    print(f"WorldMonitor: Report written ({len(lines)} lines)")


if __name__ == "__main__":
    main()
