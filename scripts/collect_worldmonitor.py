#!/usr/bin/env python3
"""
World Monitor Data Extraction
Polls the local World Monitor instance (http://localhost:3000) for aggregated
geopolitical, financial, and regulatory intelligence.

Falls back to direct API calls if World Monitor is not running.

Output: /home/recon/recon/data-sources/worldmonitor/latest.md
"""

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RECON_HOME = Path("/home/recon/recon")
OUTPUT_FILE = RECON_HOME / "data-sources" / "worldmonitor" / "latest.md"
WM_BASE = "http://localhost:3080"


def get_json(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "RECON-WorldMonitor/1.0",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def check_worldmonitor():
    """Check if World Monitor is running."""
    d = get_json(f"{WM_BASE}/api/health", timeout=5)
    return d is not None


def collect_from_worldmonitor():
    """Pull data from local World Monitor API."""
    sections = {}

    endpoints = {
        "prediction_markets": f"{WM_BASE}/api/prediction/v1",
        "market_data": f"{WM_BASE}/api/market/v1",
        "economic": f"{WM_BASE}/api/economic/v1",
        "conflicts": f"{WM_BASE}/api/conflict/v1",
        "news": f"{WM_BASE}/api/news/v1",
    }

    for name, url in endpoints.items():
        data = get_json(url)
        if data:
            sections[name] = data

    return sections


def collect_direct_sources():
    """
    Direct data collection from World Monitor's upstream sources
    (used as fallback when WM container is not running).
    These are the same free sources WM aggregates.
    """
    out = []

    # ── USGS Earthquakes (significant, last 24h) ──────────────
    out.append("## SEISMIC ACTIVITY\n")
    eq = get_json("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_day.geojson")
    if eq:
        features = eq.get("features", [])
        if features:
            for f in features[:5]:
                props = f.get("properties", {})
                mag = props.get("mag", "?")
                place = props.get("place", "?")
                time_ms = props.get("time", 0)
                t = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if time_ms else "?"
                out.append(f"- M{mag} — {place} ({t})")
        else:
            out.append("- No significant earthquakes in last 24h")
    out.append("")

    # ── GDELT Global Events (conflict/cooperation tone) ───────
    out.append("## GLOBAL EVENT TONE (GDELT)\n")
    # GDELT GKG last 15 min tone
    gdelt = get_json("https://api.gdeltproject.org/api/v2/summary/summary?d=web&t=summary")
    if gdelt:
        out.append("- GDELT data available (see full dashboard for details)")
    else:
        out.append("- GDELT API: unavailable")
    out.append("")

    # ── ACLED Conflict Data (free public events) ──────────────
    out.append("## CONFLICT EVENTS\n")
    # Use public ACLED data export
    out.append("- ACLED conflict tracking: requires API key (free for researchers)")
    out.append("- Key regions: Middle East, Ukraine, Sudan, Myanmar")
    out.append("")

    # ── FRED Economic Indicators ──────────────────────────────
    out.append("## US ECONOMIC INDICATORS\n")
    # FRED doesn't require key for some data
    fred_series = {
        "DFF": "Fed Funds Rate",
        "T10Y2Y": "10Y-2Y Treasury Spread",
        "VIXCLS": "VIX (Fear Index)",
    }
    for series_id, label in fred_series.items():
        # FRED requires API key for JSON, use HTML scrape as indicator
        out.append(f"- {label}: FRED API key needed for live data")
    out.append("")

    # ── OpenSanctions / Sanctions Activity ────────────────────
    out.append("## SANCTIONS & REGULATORY\n")
    out.append("- Track OFAC/EU sanctions lists for crypto addresses")
    out.append("- Key watchlist: Tornado Cash, Russian entities, Hamas-linked wallets")
    out.append("")

    # ── UNHCR Displacement ────────────────────────────────────
    out.append("## DISPLACEMENT & HUMANITARIAN\n")
    unhcr = get_json("https://data.unhcr.org/population/get/sublocation?widget_id=283559&sv_id=54&population_group=5459,5460&forcesublocation=0&fromDate=2024-01-01")
    if unhcr:
        out.append("- UNHCR displacement data available")
    else:
        out.append("- UNHCR API: check for updated endpoints")
    out.append("")

    # ── Energy Prices ─────────────────────────────────────────
    out.append("## ENERGY PRICES\n")
    # Use a public energy data source
    out.append("- Oil (WTI/Brent): see CoinGecko or financial news for latest")
    out.append("- Natural Gas: EIA API key needed for live data")
    out.append("- Energy price spikes directly impact crypto mining costs and macro sentiment")
    out.append("")

    # ── Internet/Infrastructure Health ────────────────────────
    out.append("## INTERNET INFRASTRUCTURE\n")
    # Cloudflare Radar - public status
    out.append("- Internet outages: Cloudflare Radar (API key needed for detailed data)")
    out.append("- Major outages can disrupt exchanges, oracle feeds, and DeFi protocols")
    out.append("")

    return out


def format_worldmonitor_data(sections):
    """Format World Monitor API responses into markdown."""
    out = []

    for name, data in sections.items():
        out.append(f"## {name.upper().replace('_', ' ')}\n")
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, (list, dict)):
                    out.append(f"### {key}")
                    out.append(f"```json\n{json.dumps(value, indent=2)[:500]}\n```")
                else:
                    out.append(f"- {key}: {value}")
        elif isinstance(data, list):
            for item in data[:10]:
                if isinstance(item, dict):
                    title = item.get("title", item.get("name", str(item)[:100]))
                    out.append(f"- {title}")
                else:
                    out.append(f"- {str(item)[:200]}")
        out.append("")

    return out


def main():
    now = datetime.now(timezone.utc)
    lines = [
        f"# World Monitor Intelligence",
        f"## {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"## Source: World Monitor (koala73/worldmonitor) + direct APIs",
        "",
    ]

    # Try World Monitor first
    if check_worldmonitor():
        print("WorldMonitor: Container running, pulling from API...")
        sections = collect_from_worldmonitor()
        if sections:
            lines.extend(format_worldmonitor_data(sections))
            lines.append(f"\n*Data from World Monitor local instance*")
        else:
            print("WorldMonitor: API returned no data, falling back to direct sources")
            lines.extend(collect_direct_sources())
    else:
        print("WorldMonitor: Container not running, using direct sources")
        lines.extend(collect_direct_sources())

    # Always add seismic data (free, no key)
    eq = get_json("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson")
    if eq and "## SEISMIC ACTIVITY" not in "\n".join(lines):
        features = eq.get("features", [])
        if features:
            lines.append("## RECENT M4.5+ EARTHQUAKES (24h)\n")
            for f in features[:5]:
                props = f.get("properties", {})
                out_line = f"- M{props.get('mag','?')} — {props.get('place','?')}"
                lines.append(out_line)
            lines.append("")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text("\n".join(lines))
    print(f"WorldMonitor: Report written ({len(lines)} lines)")


if __name__ == "__main__":
    main()
