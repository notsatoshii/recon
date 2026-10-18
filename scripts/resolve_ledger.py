#!/usr/bin/env python3
"""resolve_ledger.py -- resolution lines for the question ledger (docs/v2/phase-d-debate-upgrade.md, step 1).

Nothing wrote resolution lines before Phase D: open_ledger() skipped resolved questions that never existed and
no Brier score could be computed. This module resolves what it can and appends RESOLUTION_LINEs (phase-c-spec
§2.6, schemas.RESOLUTION_LINE) to the ledger:

  threshold / direction  a daily series over the question's window from the sources config/resolvers.json maps
                         its metric, settles_with and text to: CoinGecko prices, DeFiLlama total TVL, stablecoin
                         supply and DEX volume, Fear & Greed, GitHub stars (current count only), or the same
                         package line in the archived run folders (briefs/<day>/00_data_package.md: the figure the
                         package reported, which is what 'reported total DeFi TVL' questions ask about).
  event                  one FAST call over the day's package (the orchestrator's resolve phase: event_groups,
                         event_items, check_events); kept only with a verified data quote, else the question stays open.
  any question           void 7 days after resolves_on when still open (method void_rule).

The window rule comes from the question text: 'by / before / within' = any day in the window settles it (an early
yes), 'remain / stay / hold / through' = every day must, else the value on resolves_on. Days run from the day after
the question was asked through resolves_on (never past today).

CLI (API and package lines only; event questions need the orchestrator's model call):
    python3 scripts/resolve_ledger.py --ledger config/questions/ledger.jsonl [--today YYYY-MM-DD] [--home DIR]
                                      [--no-net] [--write]
Without --write it prints the resolution lines it would append.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from recon import debate, evidence  # noqa: E402

VOID_AFTER = 7          # days after resolves_on an open question becomes void
EVENT_LAG = 1           # an event question is still asked about this many days after resolves_on (reporting lag)
MAX_EVENT_GROUPS = 8
UA = {"User-Agent": "RECON/2.0 (ledger resolver)"}


def home() -> Path:
    return Path(os.environ.get("RECON_HOME") or REPO)


def plus_days(day: str, n: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def days_between(start: str, end: str) -> list[str]:
    """ISO days strictly after `start` through `end`."""
    try:
        a, b = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError):
        return []
    return [(a + timedelta(days=k)).isoformat() for k in range(1, (b - a).days + 1)]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── ledger ─────────────────────────────────────────────────────────────────────────────

def load_ledger(path: Path) -> list[dict]:
    out = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    for l in text.splitlines():
        try:
            out.append(json.loads(l))
        except json.JSONDecodeError:
            continue
    return out


def resolved_ids(lines: list[dict]) -> set[str]:
    return {l.get("ledger_id") for l in lines if l.get("type") == "resolution"}


def open_questions(lines: list[dict], today: str) -> list[dict]:
    """Question lines not yet resolved, asked before today, with a resolves_on date (judgment questions have none)."""
    done = resolved_ids(lines)
    out, seen = [], set()
    for l in lines:
        q = l.get("question") or {}
        lid = l.get("ledger_id")
        if l.get("type") != "question" or not lid or lid in done or lid in seen:
            continue
        if not re.fullmatch(r"\d{4}-\d\d-\d\d", q.get("resolves_on") or "") or not (l.get("day") or "") < today:
            continue
        seen.add(lid)
        out.append(l)
    return out


def resolution(line: dict, outcome: str, method: str, value=None, source: str = "", quote: str = "") -> dict:
    return {"type": "resolution", "ledger_id": line["ledger_id"], "outcome": outcome,
            "value": None if value is None else float(value), "source": source, "quote": (quote or "")[:300],
            "method": method, "resolved_at": now_iso()}


# ── levels and rules ───────────────────────────────────────────────────────────────────

_MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9,
         "t": 1e12, "tn": 1e12, "trillion": 1e12}
_LEVEL = re.compile(r"(?<![\w.])(?P<sign>[-+−])?\s?[$€£₩]?\s?(?P<n>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
                    r"\s?(?P<suf>%|k|mn|m|bn|b|tn|t|thousand|million|billion|trillion)?(?![\w])", re.I)


def parse_level(text: str) -> float | None:
    """The first figure in a level or a package value, signed and scaled: '$90 billion' 9e10, '-5%' -5,
    '5,000 stars' 5000, '+13.7%' 13.7, '86.61B' 8.661e10. None when there is no figure."""
    t = re.sub(r"\b\d{4}-\d\d-\d\d\b", " ", text or "")
    m = _LEVEL.search(t)
    if not m:
        return None
    v = float(m.group("n").replace(",", "")) * _MULT.get((m.group("suf") or "").lower(), 1.0)
    return -v if m.group("sign") in ("-", "−") else v


def window_rule(text: str) -> str:
    """'all' (remain / stay / hold / through), 'any' (by / before / within), else 'on' (the value on resolves_on)."""
    t = (text or "").lower()
    if re.search(r"\b(remain|remains|stay|stays|hold|holds|through|throughout|keep|keeps)\b", t):
        return "all"
    if re.search(r"\b(by|before|within|at any point|at some point)\b", t):
        return "any"
    return "on"


def comparator_of(q: dict) -> str:
    c = (q.get("comparator") or "").strip()
    if c in (">", ">=", "<", "<="):
        return c
    t = (q.get("text") or "").lower()
    if re.search(r"\b(rise|rises|up|increase|increases|above|exceed|exceeds|grow|grows|higher|positive|top)\b", t):
        return ">"
    if re.search(r"\b(fall|falls|down|decline|declines|below|drop|drops|lower|negative|shrink|shrinks)\b", t):
        return "<"
    return ""


def compare(v: float, comp: str, level: float) -> bool:
    return {">": v > level, ">=": v >= level, "<": v < level, "<=": v <= level}[comp]


def decide(rule: str, comp: str, level: float, series: dict, start: str, end: str, today: str,
           current_only: bool = False):
    """(outcome 'yes'/'no' or None, value, day) from a daily series {day: value} over the window. A current-only
    series (GitHub stars) holds one value, today's: it settles an early 'any' yes inside the window and, on the
    resolution day itself, a no; after resolves_on it settles nothing (the count may have crossed later)."""
    last = min(end, today)
    days = [d for d in days_between(start, last) if d in series]
    if current_only:
        if today not in series or not start < today <= end:
            return None, None, None
        v = series[today]
        hit = compare(v, comp, level)
        if rule == "any" and hit:
            return "yes", v, today
        if rule == "all" and not hit:
            return "no", v, today
        if today == end:
            return ("yes" if hit else "no"), v, today
        return None, None, None
    if not days:
        return None, None, None
    if rule == "any":
        for d in days:
            if compare(series[d], comp, level):
                return "yes", series[d], d
        if today >= end and end in series:
            return "no", series[end], end
        return None, None, None
    if rule == "all":
        for d in days:
            if not compare(series[d], comp, level):
                return "no", series[d], d
        if today >= end and end in series:
            return "yes", series[end], end
        return None, None, None
    if today >= end and end in series:
        return ("yes" if compare(series[end], comp, level) else "no"), series[end], end
    return None, None, None


# ── sources ────────────────────────────────────────────────────────────────────────────

class Fetcher:
    """GET JSON with a per-run cache; with net=False (dry runs, replays, tests) every request returns None."""

    def __init__(self, net: bool = True, timeout: int = 12):
        self.net, self.timeout, self.cache, self.errors = net, timeout, {}, []

    def json(self, url: str):
        if not self.net:
            return None
        if url not in self.cache:
            try:
                req = urllib.request.Request(url, headers=UA)
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    self.cache[url] = json.loads(r.read().decode("utf-8", "replace"))
            except Exception as e:  # noqa: BLE001 - a source that fails leaves the question open
                self.errors.append(f"{url[:80]}: {type(e).__name__}")
                self.cache[url] = None
        return self.cache[url]


def _utc_day(ts: float) -> str:
    if ts > 1e11:   # milliseconds
        ts /= 1000
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def src_coingecko(f: Fetcher, spec: dict, start: str, today: str, line: dict, home_dir: Path):
    n = max(2, (date.fromisoformat(today) - date.fromisoformat(start)).days + 2)
    d = f.json(f"https://api.coingecko.com/api/v3/coins/{spec['id']}/market_chart?vs_currency=usd&days={n}&interval=daily")
    if not d or not d.get("prices"):
        return None
    return {_utc_day(ts): float(v) for ts, v in d["prices"]}


def src_defillama_tvl(f: Fetcher, spec, start, today, line, home_dir):
    d = f.json("https://api.llama.fi/v2/historicalChainTvl")
    if not isinstance(d, list):
        return None
    return {_utc_day(x["date"]): float(x["tvl"]) for x in d if x.get("date") and x.get("tvl") is not None}


def src_defillama_stablecoin(f: Fetcher, spec, start, today, line, home_dir):
    d = f.json(f"https://stablecoins.llama.fi/stablecoincharts/all?stablecoin={spec.get('id', 1)}")
    if not isinstance(d, list):
        return None
    out = {}
    for x in d:
        v = ((x.get("totalCirculatingUSD") or {}).get("peggedUSD"))
        if x.get("date") and v is not None:
            out[_utc_day(float(x["date"]))] = float(v)
    return out


def src_defillama_dex_change7d(f: Fetcher, spec, start, today, line, home_dir):
    d = f.json("https://api.llama.fi/overview/dexs?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true"
               "&dataType=dailyVolume")
    chart = (d or {}).get("totalDataChart") if isinstance(d, dict) else None
    if not chart:
        return None
    vol = {_utc_day(float(ts)): float(v) for ts, v in chart}
    out = {}
    for day, v in vol.items():
        prev = vol.get(plus_days(day, -7))
        if prev:
            out[day] = round((v / prev - 1) * 100, 2)
    return out


def src_fear_greed(f: Fetcher, spec, start, today, line, home_dir):
    d = f.json("https://api.alternative.me/fng/?limit=90")
    rows = (d or {}).get("data") if isinstance(d, dict) else None
    if not rows:
        return None
    return {_utc_day(float(x["timestamp"])): float(x["value"]) for x in rows if x.get("timestamp")}


_REPO = re.compile(r"github\.com/([\w.-]+/[\w.-]+)|\b([\w.-]+/[\w.-]+)\b")


def src_github_stars(f: Fetcher, spec, start, today, line, home_dir):
    q = line.get("question") or {}
    repo = ""
    for t in (q.get("baseline_quote"), q.get("metric"), q.get("settles_with"), q.get("text")):
        for m in _REPO.finditer(t or ""):
            cand = (m.group(1) or m.group(2) or "").rstrip(".)")
            if cand and not cand.lower().startswith(("http", "www")) and "/" in cand and not re.match(r"^\d", cand):
                repo = cand
                break
        if repo:
            break
    if not repo:
        return None
    d = f.json(f"https://api.github.com/repos/{repo}")
    if not isinstance(d, dict) or d.get("stargazers_count") is None:
        return None
    return {today: float(d["stargazers_count"])}


def _norm_line(s: str) -> str:
    return evidence.norm(s).strip(" .\"'-")


def package_key(pkg: str, baseline: str) -> tuple[str, str] | None:
    """(heading, label) of the line carrying the baseline quote: the nearest heading above it and the line's text
    up to its first colon ('- Current:' under '## TOTAL DEFI TVL'). A copy under a CROSS-SOURCE heading is used only
    when there is no other. None without a colon label."""
    want = _norm_line(baseline)
    if len(want) < 6:
        return None
    heading, hits = "", []
    for line in (pkg or "").split("\n"):
        if line.startswith("#"):
            heading = line.strip()
            continue
        if want in _norm_line(line):
            s = line.strip()
            if ":" in s:
                hits.append((heading, s.split(":", 1)[0] + ":"))
    hits.sort(key=lambda h: "CROSS-SOURCE" in h[0].upper())
    return hits[0] if hits else None


def package_value(pkg: str, key: tuple[str, str]) -> float | None:
    """The figure on the line with this label under this heading; without the heading, the label's line when the
    label occurs once in the package."""
    heading, label = key
    want = _norm_line(label)
    on, anywhere = not heading, []
    for line in (pkg or "").split("\n"):
        if line.startswith("#"):
            on = line.strip() == heading or not heading
            continue
        if _norm_line(line).startswith(want):
            v = parse_level(line.split(":", 1)[-1])
            if on:
                return v
            anywhere.append(v)
    return anywhere[0] if len(anywhere) == 1 else None


def src_package(f: Fetcher, spec, start, today, line, home_dir):
    q = line.get("question") or {}
    base = home_dir / "briefs" / start / "00_data_package.md"
    try:
        key = package_key(base.read_text(encoding="utf-8", errors="ignore"), q.get("baseline_quote") or "")
    except OSError:
        return None
    if not key:
        return None
    out = {}
    for d in days_between(start, min(q.get("resolves_on") or today, today)):
        p = home_dir / "briefs" / d / "00_data_package.md"
        if p.exists():
            v = package_value(p.read_text(encoding="utf-8", errors="ignore"), key)
            if v is not None:
                out[d] = v
    return out


SOURCES = {"coingecko": src_coingecko, "defillama_tvl": src_defillama_tvl,
           "defillama_stablecoin": src_defillama_stablecoin, "defillama_dex_change7d": src_defillama_dex_change7d,
           "fear_greed": src_fear_greed, "github_stars": src_github_stars, "package": src_package}
CURRENT_ONLY = {"github_stars"}


def load_resolvers(home_dir: Path) -> list[dict]:
    try:
        return json.loads((home_dir / "config" / "resolvers.json").read_text(encoding="utf-8")).get("resolvers", [])
    except (OSError, json.JSONDecodeError):
        return []


def match_resolver(q: dict, resolvers: list[dict]) -> dict | None:
    text = " ".join(q.get(k) or "" for k in ("metric", "settles_with", "text"))
    for r in resolvers:
        if all(re.search(p, text, re.I) for p in r.get("all", [])) and \
                not any(re.search(p, text, re.I) for p in r.get("none", [])):
            return r
    return None


def resolve_numeric(lines: list[dict], today: str, fetcher: Fetcher, home_dir: Path | None = None,
                    resolvers: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Resolution lines for open threshold and direction questions, and a note per question tried."""
    home_dir = home_dir or home()
    resolvers = load_resolvers(home_dir) if resolvers is None else resolvers
    out, notes = [], []
    for l in open_questions(lines, today):
        q = l.get("question") or {}
        if q.get("kind") not in ("threshold", "direction"):
            continue
        r = match_resolver(q, resolvers)
        if not r:
            notes.append({"ledger_id": l["ledger_id"], "note": "no resolver matches the metric"})
            continue
        comp = comparator_of(q)
        start, end = l.get("day") or "", q.get("resolves_on") or ""
        rule = window_rule(q.get("text") or "")
        done = False
        for spec in r.get("sources", []):
            fn = SOURCES.get(spec.get("type"))
            if not fn:
                continue
            series = fn(fetcher, spec, start, today, l, home_dir)
            if not series:
                continue
            level = parse_level(q.get("threshold") or "")
            if level is None:                      # a direction question without a level: against the start value
                level = series.get(start)
                if level is None:
                    level = parse_level(q.get("baseline_quote") or "") if spec.get("type") == "package" else None
            if level is None or not comp:
                notes.append({"ledger_id": l["ledger_id"], "note": f"{r['name']}: no level or comparator"})
                break
            outcome, value, day = decide(rule, comp, level, series, start, end, today,
                                         current_only=spec.get("type") in CURRENT_ONLY)
            if outcome:
                method = "package" if spec.get("type") == "package" else "api"
                out.append(resolution(l, outcome, method, value,
                                      source=f"{r['name']} via {spec['type']} on {day} ({rule} {comp} {level:g})"))
                done = True
                break
        if not done:
            notes.append({"ledger_id": l["ledger_id"], "note": f"{r['name']}: not settled yet"})
    return out, notes


def void_lines(lines: list[dict], today: str, already: set[str] | None = None) -> list[dict]:
    already = already or set()
    out = []
    for l in open_questions(lines, today):
        if l["ledger_id"] in already:
            continue
        end = (l.get("question") or {}).get("resolves_on") or ""
        if end and plus_days(end, VOID_AFTER) < today:
            out.append(resolution(l, "void", "void_rule", source=f"open {VOID_AFTER} days after resolves_on {end}"))
    return out


# ── event questions: one model call over the day's package ─────────────────────────────

def event_groups(lines: list[dict], today: str, already: set[str] | None = None) -> list[dict]:
    """Open event questions still in their window (plus EVENT_LAG days), one group per question text and date:
    the same question asked on several days resolves together."""
    already = already or set()
    groups: dict[tuple, dict] = {}
    for l in open_questions(lines, today):
        q = l.get("question") or {}
        if q.get("kind") != "event" or l["ledger_id"] in already:
            continue
        end = q.get("resolves_on") or ""
        if today > plus_days(end, EVENT_LAG):
            continue
        key = (re.sub(r"\s+", " ", (q.get("text") or "").strip().lower()), end)
        g = groups.setdefault(key, {"text": q.get("text", ""), "resolves_on": end,
                                    "settles_with": q.get("settles_with", ""), "start": l.get("day", ""), "lines": []})
        g["start"] = min(g["start"], l.get("day", ""))
        g["lines"].append(l)
    out = sorted(groups.values(), key=lambda g: (g["resolves_on"], g["text"]))[:MAX_EVENT_GROUPS]
    for n, g in enumerate(out, 1):
        g["ref"] = f"e{n}"
    return out


def event_items(groups: list[dict]) -> str:
    return "\n".join(f"[{g['ref']}] {debate.anonymise(g['text'])} (window: after {g['start']} through "
                     f"{g['resolves_on']}; settles with: {debate.anonymise(g['settles_with']) or 'not stated'})"
                     for g in groups) or "- none"


def check_events(data: dict, groups: list[dict], locator) -> tuple[list[dict], list[dict]]:
    """Resolution lines from the resolver's reply: an outcome counts only with a quote that is one verbatim data
    line (or a headline and its body inside one list item), not social, not a prediction-market odds line."""
    by_ref = {g["ref"]: g for g in groups}
    out, notes = [], []
    for it in (data or {}).get("items") or []:
        g = by_ref.get((it.get("ref") or "").strip())
        oc = it.get("outcome")
        if not g or oc not in ("yes", "no"):
            continue
        quote = (it.get("quote") or "").strip()
        st = locator.strict(quote, item=True) if quote else {"ok": False, "reason": "empty"}
        why = ""
        if not st.get("ok"):
            why = f"quote fails the strict check ({st.get('reason') or 'not found'})"
        elif st.get("cls") != "data":
            why = "quote is a social line"
        elif debate.market_at(locator, st["doc"], st["line"], st.get("section", "")) or \
                debate.is_market_line(st.get("section", ""), quote):
            why = "quote is a prediction-market odds line"
        if why:
            notes.append({"ref": g["ref"], "note": f"{oc} not kept: {why}"})
            continue
        for l in g["lines"]:
            out.append(resolution(l, oc, "package_llm", source=st.get("section") or st.get("doc") or "", quote=quote))
    return out, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resolve ledger questions by API and package lines (no model call)")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--today", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--home", default=str(home()), help="RECON_HOME with briefs/<day>/00_data_package.md")
    ap.add_argument("--no-net", action="store_true")
    ap.add_argument("--write", action="store_true", help="append the resolution lines to the ledger")
    a = ap.parse_args(argv)
    lines = load_ledger(Path(a.ledger))
    f = Fetcher(net=not a.no_net)
    res, notes = resolve_numeric(lines, a.today, f, Path(a.home))
    res += void_lines(lines, a.today, {r["ledger_id"] for r in res})
    for r in res:
        print(json.dumps(r, ensure_ascii=False))
    for n in notes:
        print(f"# {n['ledger_id']}: {n['note']}", file=sys.stderr)
    for e in f.errors:
        print(f"# fetch failed: {e}", file=sys.stderr)
    if a.write and res:
        have = resolved_ids(lines)
        new = [r for r in res if r["ledger_id"] not in have]
        with open(a.ledger, "a", encoding="utf-8") as fh:
            for r in new:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"# appended {len(new)} resolution line(s) to {a.ledger}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
