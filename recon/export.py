#!/usr/bin/env python3
"""Export RECON run records for the RUBRIC recon page.

Reads the v1-layout run directories (briefs/<date>/03_take_*.md, 04a_*, 04c_*, 05_resp_*,
05_5_deepdive_*, 06_vote_*, 07_*), the day log, and logs/llm_calls.log, and writes

    <out>/index.json
    <out>/runs/<date>.json

in the schema recon.html consumes (see rubric/recon-sample). v1 has no desks, triage,
threads, or claims check yet; those fields are filled with what v1 knows and left empty
otherwise, so the page renders real runs today and richer ones after Phase 2.

Runs written by recon/orchestrator.py (briefs/<run>/run.json) are exported as they are: typed
questions, positions, verified cites and the claims check included. A Phase D run (debate_mode "d", or
phases/debate/ with open / reply / delphi records) also gets a full phase_d block from its phase files (cross-exam
pairs, Delphi revisions, judge items, pre-mortem, moves line, calibration), and its edges, agents' challenges and
debates list are filled from the cross-exam pairs where run.json lacks them. Every other run.json is unchanged.

Usage: python3 recon/export.py [--out DIR] [--days N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RECON_HOME = Path(os.environ.get("RECON_HOME") or Path(__file__).resolve().parent.parent)
KST = timezone(timedelta(hours=9))

AGENTS = ["trader", "narrator", "builder", "analyst", "skeptic", "policy_analyst",
          "user_agent", "macro_strategist", "ai_engineer"]
LAYER = {"reddit": "reddit", "twitter": "twitter", "onchain": "onchain", "news": "news",
         "ai_tools": "ai_tools", "fundraising": "fundraising", "worldmonitor": "worldmonitor",
         "bettafish": "bettafish", "polymarket": "polymarket", "kalshi": "kalshi", "changelogs": "changelogs",
         "zdnet_kr": "zdnet_kr"}
PHASE_E = {"polymarket", "kalshi", "changelogs", "zdnet_kr"}   # 72 h freshness rule from day one (phase-e §2)
FRESH_HOURS = float(os.environ.get("RECON_FRESH_HOURS", "72"))


def read(p: Path, default: str = "") -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return default


def ts(date: str, hms: str) -> str:
    return f"{date}T{hms}+09:00"


def secs(date: str, a: str, b: str) -> int:
    fa = datetime.strptime(f"{date} {a}", "%Y-%m-%d %H:%M:%S")
    fb = datetime.strptime(f"{date} {b}", "%Y-%m-%d %H:%M:%S")
    if fb < fa:
        fb += timedelta(days=1)
    return int((fb - fa).total_seconds())


def parse_day_log(date: str) -> dict:
    """Window of the LAST run in logs/<date>.log plus phase timings."""
    text = read(RECON_HOME / "logs" / f"{date}.log")
    lines = text.splitlines()
    starts = [i for i, l in enumerate(lines) if re.search(r"\] RECON starting|\] PHASE -1:", l)]
    if not starts:
        return {}
    seg = lines[starts[-1]:]
    t = lambda l: re.match(r"\[(\d\d:\d\d:\d\d)\]", l).group(1) if re.match(r"\[(\d\d:\d\d:\d\d)\]", l) else None
    start = t(seg[0])
    end = None
    complete = False
    phases = []
    for l in seg:
        m = re.match(r"\[(\d\d:\d\d:\d\d)\] (PHASE [^:]+|DELIVERING|RECON.*COMPLETE in.*)", l)
        if m:
            phases.append({"name": m.group(2)[:40], "at": m.group(1)})
            end = m.group(1)
            if "COMPLETE" in m.group(2):
                complete = True
    telegram = any("DELIVERING" in l for l in seg) and not any("Telegram suppressed" in l and "RECON DAILY BRIEF" in l for l in seg)
    env = None
    for l in seg:
        m = re.search(r"Environment: ENVIRONMENT: ([A-Z-]+)", l)
        if m:
            env = m.group(1)
    return {"start": start, "end": end or start, "complete": complete, "phases": phases,
            "telegram": telegram, "environment": env}


def parse_llm_calls(date: str, start: str, end: str) -> list[dict]:
    """Calls from logs/llm_calls.log inside the run window (log lines carry only HH:MM:SS)."""
    out = []
    for l in read(RECON_HOME / "logs" / "llm_calls.log").splitlines():
        m = re.match(r"\[(\d\d:\d\d:\d\d)\] (.*)", l)
        if not m:
            continue
        hms, rest = m.groups()
        if not (start <= hms <= end):
            continue
        kv = dict(re.findall(r"(\w+)=(\S+)", rest))
        if kv.get("provider") in (None, "FAILED"):
            continue
        out.append({
            "agent": kv.get("agent", "-"), "tier": kv.get("tier", "?"), "model": kv.get("model", "?"),
            "in_tok": int(kv.get("in_tok", 0) or 0), "cached_tok": int(kv.get("cached_tok", 0) or 0),
            "out_tok": int(kv.get("out_tok", 0) or 0), "seconds": float(kv.get("duration", "0s").rstrip("s") or 0),
            "at": hms,
        })
    return out


PHASE_ORDER = ["take", "challenge", "response", "deep_dive", "vote", "memory", "state"]


def assign_phases(calls: list[dict], agent: str, n_challenges: int, has_resp: bool, has_dd: bool) -> list[dict]:
    """v1 logs no phase; infer it from call order per agent."""
    seq = ["take"] + ["challenge"] * n_challenges + (["response"] if has_resp else []) + \
          (["deep_dive"] if has_dd else []) + ["vote", "memory", "state"]
    mine = [c for c in calls if c["agent"] == agent]
    out = []
    for i, c in enumerate(mine):
        out.append({"phase": seq[i] if i < len(seq) else "other", "tier": c["tier"], "model": c["model"],
                    "in_tok": c["in_tok"], "out_tok": c["out_tok"], "seconds": c["seconds"]})
    return out


SECTION_LAYER = [("reddit", "reddit"), ("twitter", "twitter"), ("on-chain", "onchain"), ("onchain", "onchain"),
                 ("news", "news"), ("ai & tools", "ai_tools"), ("fundraising", "fundraising"),
                 ("bettafish", "bettafish"), ("world monitor", "worldmonitor"), ("polymarket", "polymarket"),
                 ("kalshi", "kalshi"), ("changelogs", "changelogs"), ("zdnet korea", "zdnet_kr")]


def header_time(stamp: str, finished: datetime | None) -> str:
    """'YYYY-MM-DD HH:MM' from a '## ... UTC' header, as KST ISO. Until 2026-10-04 five collectors wrote Seoul time
    under a UTC label; data is collected before a run ends, so a 'UTC' stamp later than the run's end is read as KST."""
    t = datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    if finished and t > finished + timedelta(minutes=5):
        t = t.replace(tzinfo=KST)
    return t.astimezone(KST).isoformat(timespec="seconds")


def source_record(name: str, layer: str, txt: str, finished: datetime | None) -> dict:
    m = re.search(r"^## (\d{4}-\d\d-\d\d \d\d:\d\d) UTC", txt, re.M)
    fetched = header_time(m.group(1), finished) if m else None
    bad = re.search(r"SOURCE UNAVAILABLE|NOT CONFIGURED|FEED ERROR", txt[:400] if txt else "")
    items = len(re.findall(r"^- ", txt, re.M))
    ok = bool(txt) and len(txt) > 300 and not bad and items > 0
    err = None
    if not txt:
        err = "not in this run"
    elif bad:
        err = bad.group(0).lower()
    elif items == 0:
        err = "no items"
    stale = re.search(r"SOURCE STALE", txt[:600] if txt else "")
    if stale:
        ok, err = False, "stale (not shown in the package)"
    elif m and finished and (layer in PHASE_E or os.environ.get("RECON_FRESH_V1", "0") == "1"):
        age = (finished - datetime.fromisoformat(fetched)).total_seconds() / 3600
        if age > FRESH_HOURS:
            ok, err = False, f"stale ({age:.0f} h)"
    return {"name": name, "layer": layer, "ok": ok, "items": items, "fetched_at": fetched,
            "bytes": len(txt.encode("utf-8")), "error": err}


def run_sections(rd: Path) -> dict:
    """layer -> section text from this run's own files (00_raw_data.md first, then 00_data_package.md)."""
    found = {}
    for fname in ("00_raw_data.md", "00_data_package.md"):
        text = read(rd / fname)
        heads = list(re.finditer(r"^# (.+?) Intelligence.*$", text, re.M))
        for i, h in enumerate(heads):
            layer = next((l for k, l in SECTION_LAYER if k in h.group(1).lower()), None)
            if layer and layer not in found:
                end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
                found[layer] = text[h.start():end]
    return found


def source_records_dir(rd: Path, finished: str | None = None) -> tuple[list[dict], str]:
    """Source records read from one run folder's own raw data / package (the orchestrator's run.json)."""
    fin = datetime.fromisoformat(finished) if finished else None
    found = run_sections(rd)
    return [source_record(n, l, found.get(l, ""), fin) for n, l in LAYER.items()], "run" if found else "none"


def source_records(date: str, finished: str | None = None) -> tuple[list[dict], str]:
    """One record per source layer, plus the scope: "run" when read from this run's own raw data / package (what the
    run actually read; later exports cannot change it), else "export" (data-sources/<name>/latest.md as of the
    export, the old behaviour, for runs without those files)."""
    fin = datetime.fromisoformat(finished) if finished else None
    found = run_sections(RECON_HOME / "briefs" / date)
    if found:
        return [source_record(n, l, found.get(l, ""), fin) for n, l in LAYER.items()], "run"
    recs = []
    for n, l in LAYER.items():
        p = RECON_HOME / "data-sources" / n / "latest.md"
        r = source_record(n, l, read(p), None)
        if p.exists():  # the file's own write time; its header may be Seoul time labelled UTC
            r["fetched_at"] = datetime.fromtimestamp(p.stat().st_mtime, KST).isoformat(timespec="seconds")
        recs.append(r)
    return recs, "export"


# Package section title -> the data sources it is built from, for sections with no "# <Source> Intelligence"
# block inside (and as the fallback when a block is missing). Names match run["sources"][].name.
TITLE_SOURCES = [("cross-source", ["news", "reddit", "twitter"]), ("sentiment", ["bettafish"]),
                 ("geopolitical", ["worldmonitor"]), ("on-chain", ["onchain"]), ("news", ["news"]),
                 ("social", ["reddit", "twitter"]), ("ai", ["ai_tools"]), ("fundraising", ["fundraising"]),
                 ("prediction", ["polymarket", "kalshi"])]
LAYER_SOURCE = {v: k for k, v in LAYER.items()}


def _line_source(line: str) -> str | None:
    if "reddit.com" in line or re.search(r"\br/\w+", line):
        return "reddit"
    if re.search(r"https?://(x|twitter)\.com/", line):
        return "twitter"
    if re.search(r"https?://", line):
        return "news"
    return None


def section_sources(title: str, body: str) -> list[tuple[str, int]]:
    """(source name, bytes) pairs, largest first, for one package section. Sources come from the
    "# <Source> Intelligence" blocks the collector writes into the section; the cross-source block has
    none, so its items are attributed by URL (reddit / x.com / other links = news)."""
    blocks = list(re.finditer(r"^# (.+?) Intelligence.*$", body, re.M))
    sizes: dict[str, int] = {}
    for i, h in enumerate(blocks):
        layer = next((l for k, l in SECTION_LAYER if k in h.group(1).lower()), None)
        if layer:
            end = blocks[i + 1].start() if i + 1 < len(blocks) else len(body)
            src = LAYER_SOURCE.get(layer, layer)
            sizes[src] = sizes.get(src, 0) + len(body[h.start():end].encode("utf-8"))
    if not sizes and "cross-source" in title:
        for line in body.splitlines():
            src = _line_source(line)
            if src:
                sizes[src] = sizes.get(src, 0) + len(line.encode("utf-8")) + 1
    if not sizes:
        fallback = next((v for k, v in TITLE_SOURCES if title.startswith(k)), [])
        return [(s, 0) for s in fallback]
    return sorted(sizes.items(), key=lambda kv: -kv[1])


def package_sections(pkg: str) -> list[dict]:
    """One entry per "# SECTION n:" block. "source" is the data source most of the section came from (the
    name used in run["sources"], so the page can link them); "sources" lists every source in it."""
    parts = re.split(r"^# SECTION \d+: ", pkg, flags=re.M)
    out = []
    for part in parts[1:]:
        title, _, body = part.partition("\n")
        name = title.strip().lower().replace(" & ", "_").replace(" ", "_")
        srcs = section_sources(name, body)
        out.append({"name": name[:40], "source": srcs[0][0] if srcs else None,
                    "sources": [{"name": n, "bytes": b} for n, b in srcs],
                    "bytes": len(body.encode("utf-8")),
                    "items": len(re.findall(r"^- ", body, re.M)), "freshness": None})
    return out


def parse_vote(txt: str) -> dict:
    """Votes since 2026-10-04 use labelled lines (1. ACT ON: / 2. MARKET IS WRONG ABOUT: /
    3. UNDISCUSSED RISK:). Older votes are numbered, or follow the persona's own headings."""
    def clean(s):
        s = re.sub(r"^\W*(ACT ON|MARKET IS WRONG ABOUT|UNDISCUSSED RISK)\s*:\**\s*", "", s.strip(), flags=re.I)
        return re.sub(r"\s+", " ", s).strip()[:400]

    def grab(n):
        m = re.search(rf"(?:^|\n)\s*[#*]*\s*{n}[.)]\**\s*(.*?)(?=\n\s*[#*]*\s*{n + 1}[.)]|\Z)", txt, re.S)
        return clean(m.group(1)) if m else ""
    out = {"act_on": grab(1), "market_wrong_about": grab(2), "unseen_risk": grab(3)}
    if all(out.values()):
        return out
    labels = [("act_on", r"ACT ON"), ("market_wrong_about", r"MARKET IS WRONG ABOUT"), ("unseen_risk", r"UNDISCUSSED RISK")]
    for key, lab in labels:
        m = re.search(rf"{lab}\s*:\**\s*(.*?)(?=\n\s*[#*]*\s*(?:\d[.)]\s*)?\**(?:ACT ON|MARKET IS WRONG ABOUT|UNDISCUSSED RISK)\b|\Z)",
                      txt, re.S | re.I)
        if m and not out[key]:
            out[key] = clean(m.group(1))
    if not any(out.values()):
        # persona format: take the first three heading blocks or paragraphs in order
        blocks = [b for b in re.split(r"\n\s*#{1,4} [^\n]*\n|\n\s*\n", "\n" + txt) if b.strip()]
        for key, b in zip(("act_on", "market_wrong_about", "unseen_risk"), blocks):
            out[key] = clean(b)
    return out


def memory_entry(agent: str, date: str) -> tuple[str, int, int]:
    mem = read(RECON_HOME / "config" / "agent_memory" / f"{agent}.md")
    state_name = "user" if agent == "user_agent" else agent
    st = read(RECON_HOME / "config" / "agent_state" / f"{state_name}_state.md")
    i = mem.rfind(f"### Last updated: {date}")
    upd = re.sub(r"\s+", " ", mem[i:]).strip()[:600] if i >= 0 else ""
    return upd, len(mem.splitlines()), len(st.splitlines())


def predictions_from_memory(agent: str, date: str) -> list[dict]:
    mem = read(RECON_HOME / "config" / "agent_memory" / f"{agent}.md")
    out = []
    for m in re.finditer(rf"^- \[{date}\] (.+?) \[status: pending\]", mem, re.M):
        text = m.group(1).strip()
        d = re.search(r"(20\d\d-\d\d-\d\d)", text)
        out.append({"text": text[:200], "probability": None, "resolves_on": d.group(1) if d else None})
    return out[:6]


# ── Phase D (docs/v2/phase-d-debate-upgrade.md): the debate as the RUBRIC page reads it ──────────────────────────
# run.json keeps a thin phase_d block (scores, Delphi without evidence, judge items); the full record (steelman,
# "I am wrong if", rebuttal, replies, checked evidence, pre-mortem) is in phases/debate/*.json and phases/judge.json.

IF_TRUE = {"yes_more_likely": "yes more likely", "no_more_likely": "no more likely"}


def load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def indicator_text(ind: dict | None) -> str:
    """An INDICATOR in one line, as recon/debate.py indicator_text: 'Total DeFi TVL (DeFiLlama) < $85B by 2026-10-25'."""
    ind = ind or {}
    obs = str(ind.get("observable") or "").strip()
    if not obs:
        return ""
    src = str(ind.get("source") or "").strip()
    c = ind.get("comparator") or ""
    what = {"happens": "happens", "does_not_happen": "does not happen"}.get(c) or \
        " ".join(x for x in (c, str(ind.get("level") or "").strip()) if x)
    by = str(ind.get("by_date") or "").strip()
    return " ".join(x for x in (obs + (f" ({src})" if src else ""), what, f"by {by}" if by else "") if x)


def indicator_out(ind: dict | None) -> dict | None:
    """The indicator's fields plus 'text' (one readable line) and 'means' (what it does to the question if true)."""
    t = indicator_text(ind)
    if not t:
        return None
    keep = ("observable", "source", "comparator", "level", "by_date", "if_true")
    return {**{k: (ind or {}).get(k, "") for k in keep}, "text": t, "means": IF_TRUE.get((ind or {}).get("if_true"), "")}


def evidence_out(items) -> list[dict]:
    return [{"quote": e.get("quote", ""), "section": e.get("section", ""), "status": e.get("status") or "unverified",
             "cls": e.get("cls", "")} for e in items or [] if isinstance(e, dict) and e.get("quote")]


def evidence_counts(items: list[dict]) -> dict:
    n = {"claimed": len(items), "verified": 0, "partial": 0, "unverified": 0}
    for e in items:
        k = e["status"] if e["status"] in ("verified", "partial") else "unverified"
        n[k] += 1
    return n


def calibration_out(rd: Path, run: dict) -> dict | None:
    """Resolved count and the lenses' mean Brier, round 0 vs post-debate. The run's own snapshot first: a tagged run's
    state/questions/calibration.json, else the 00_brier.md it wrote, else the state dir's calibration.json when it is
    the same report (the state copy is rewritten every run)."""
    md = read(rd / "00_brier.md")
    stamp = re.search(r"^# Brier report \(([^)]+)\)", md, re.M)
    cands = [rd / "state" / "questions" / "calibration.json"]
    if run.get("state_dir"):
        cands.append(Path(run["state_dir"]) / "questions" / "calibration.json")
    for i, p in enumerate(cands):
        j = load_json(p) if p.exists() else None
        if not isinstance(j, dict) or (i > 0 and not (stamp and j.get("generated") == stamp.group(1))):
            continue
        g = j.get("group") or {}
        comps = {k: {"changed": v.get("changed"), "mean_delta": v.get("mean_delta"), "on": v.get("on")}
                 for k, v in (j.get("components") or {}).items() if isinstance(v, dict)}
        return {"generated": j.get("generated"), "resolved": j.get("resolved", 0), "yes": j.get("yes", 0),
                "no": j.get("no", 0), "void": j.get("void", 0), "round0_mean_brier": g.get("round0_mean_brier"),
                "post_mean_brier": g.get("post_mean_brier"), "components": comps,
                "agents_scored": len(j.get("per_agent") or {}), "weights": bool(j.get("weights")), "from": "json"}
    if not md:
        return None

    def val(s: str):
        s = s.strip()
        try:
            return float(s)
        except ValueError:
            return None
    m = re.search(r"Resolved yes/no: (\d+) \((\d+) yes, (\d+) no\); void (\d+)", md)
    g = re.search(r"^\| lenses' mean probability, mean Brier \| ([^|]+) \| ([^|]+) \|", md, re.M)
    comps = {}
    for c in re.finditer(r"^- (\w+): changed (\d+) questions, mean ΔBrier (\S+), P\(worse\) \S+, (on|off)\b", md, re.M):
        comps[c.group(1)] = {"changed": int(c.group(2)), "mean_delta": val(c.group(3).rstrip(",")), "on": c.group(4) == "on"}
    return {"generated": stamp.group(1) if stamp else None,
            "resolved": int(m.group(1)) if m else 0, "yes": int(m.group(2)) if m else 0, "no": int(m.group(3)) if m else 0,
            "void": int(m.group(4)) if m else 0, "round0_mean_brier": val(g.group(1)) if g else None,
            "post_mean_brier": val(g.group(2)) if g else None, "components": comps,
            "agents_scored": len(re.findall(r"^\| [a-z_]+ \| \d+ \|", md, re.M)),
            "weights": not re.search(r"Weights: none", md), "from": "md"}


def moves_line(summary: dict, delphi: list[dict]) -> str:
    """One plain line: '5 moves: 4 on a verified quote, 1 on argument alone. Delphi: 6 agents looked again at 7
    positions, 2 revised. Cross-exam: 2 pairs, no split held.'"""
    s = summary or {}
    out = []
    n, lab = s.get("moves"), s.get("labels") or {}
    if n is not None:
        parts = [f"{lab.get('verified quote', 0)} on a verified quote", f"{lab.get('argument', 0)} on argument alone"]
        out.append(f"{n} move{'' if n == 1 else 's'}" + (": " + ", ".join(parts) if n else "") + ".")
    if delphi:
        agents = len({d["agent"] for d in delphi})
        rev = sum(1 for d in delphi if d["verdict"] == "revise")
        out.append(f"Delphi: {agents} agent{'' if agents == 1 else 's'} looked again at {len(delphi)} "
                   f"position{'' if len(delphi) == 1 else 's'}, {rev} revised.")
    x = s.get("xexam") or {}
    if x.get("pairs"):
        held = x.get("held_splits", 0)
        out.append(f"Cross-exam: {x['pairs']} pair{'' if x['pairs'] == 1 else 's'}, "
                   f"{'no split' if not held else str(held) + ' split' + ('' if held == 1 else 's')} held.")
    return " ".join(out)


def has_phase_d(rd: Path, run: dict) -> bool:
    if run.get("debate_mode") == "d":
        return True
    d = rd / "phases" / "debate"
    return d.is_dir() and any(re.match(r"(open|reply|delphi)__", f.name) for f in d.glob("*.json"))


def phase_d_block(rd: Path, run: dict) -> dict:
    """The page's phase_d block: cross-exam pairs, Delphi revisions, judge items, pre-mortem, moves line, calibration."""
    pd0 = run.get("phase_d") or {}
    qtext = {q.get("id"): q.get("text", "") for q in run.get("questions") or [] if isinstance(q, dict)}
    items = {}
    d = rd / "phases" / "debate"
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        rec = load_json(f)
        if isinstance(rec, dict):
            items[f.stem] = rec
    opens = {(r["agent"], r["question_id"]): r for r in items.values() if r.get("type") == "open"}
    replies = {(r["agent"], r["question_id"]): r for r in items.values() if r.get("type") == "reply"}
    jd = load_json(rd / "phases" / "judge.json") or {}
    jitems = ((jd.get("judge") or {}).get("items")) or ((pd0.get("judge") or {}).get("items")) or []
    judge_by_q = {}
    judge = []
    for it in jitems:
        if not isinstance(it, dict):
            continue
        j = {"question_id": it.get("question_id"), "question": qtext.get(it.get("question_id"), ""),
             "indicator": indicator_out(it.get("indicator")), "status": it.get("status", ""),
             "resolvable": bool(it.get("judge_resolvable", it.get("resolvable"))), "why_not": it.get("why_not", ""),
             "data_today": it.get("data_today", ""), "quote": it.get("quote", ""), "quote_status": it.get("quote_status", ""),
             "section": it.get("section", ""), "watch_line": it.get("watch_line", ""), "split_line": it.get("split_line", "")}
        judge.append(j)
        judge_by_q[j["question_id"]] = j

    scores = [x for x in pd0.get("xexam") or [] if isinstance(x, dict)]
    if not scores:  # run.json without the scores: rebuild the pairs from the openings
        seen = set()
        for (a, q), r in opens.items():
            key = (q, frozenset((a, r.get("target"))))
            if key in seen:
                continue
            seen.add(key)
            mine, theirs = r["data"].get("probability"), (opens.get((r.get("target"), q)) or {}).get("data", {}).get("probability")
            hi, lo = (a, r.get("target")) if (mine or 0) >= (theirs or 0) else (r.get("target"), a)
            scores.append({"question_id": q, "high": hi, "low": lo, "status": "two-sided" if (r.get("target"), q) in opens else "one-sided"})
    pairs = []
    for sc in scores:
        q = sc.get("question_id")
        moves = {m.get("agent"): m for m in sc.get("moves") or []}
        sides = []
        for role, other in (("high", "low"), ("low", "high")):
            a, b = sc.get(role), sc.get(other)
            op, rp, orp = opens.get((a, q)), replies.get((a, q)), replies.get((b, q))
            od = (op or {}).get("data") or {}
            rd_ = (rp or {}).get("data") or {}
            ev = evidence_out((op or {}).get("evidence"))
            mv = moves.get(a) or {}
            take = mv.get("take", ((rp or {}).get("move") or {}).get("take"))
            final = mv.get("new", ((rp or {}).get("move") or {}).get("new", take))
            oti = rd_.get("on_their_indicator") or {}
            their = ((orp or {}).get("data") or {}).get("on_their_indicator") or {}
            sides.append({
                "role": role, "agent": a, "opponent": b, "take": take, "final": final,
                "label": mv.get("label") or ((rp or {}).get("move") or {}).get("label") or ("held" if take == final else ""),
                "opening_probability": od.get("probability"), "steelman": od.get("steelman", ""),
                "i_am_wrong_if": indicator_out(od.get("i_am_wrong_if")), "rebuttal": od.get("rebuttal", ""),
                "evidence": ev, "evidence_counts": (sc.get("evidence") or {}).get(role) or evidence_counts(ev),
                "indicator_verdict": their.get("verdict", ""), "indicator_verdict_why": their.get("why", ""),
                "reply": ({"verdict": rd_.get("verdict", ""), "new_probability": rd_.get("new_probability"),
                           "reason": rd_.get("reason", ""),
                           "steelman_fair": (rd_.get("steelman_fair") or {}).get("verdict", ""),
                           "steelman_correction": (rd_.get("steelman_fair") or {}).get("correction", ""),
                           "on_their_indicator": {"verdict": oti.get("verdict", ""), "why": oti.get("why", ""),
                                                  "counter": indicator_out(oti.get("counter"))},
                           "evidence": evidence_out(rp.get("evidence"))} if rp else None),
            })
        pairs.append({"question_id": q, "question": qtext.get(q, ""), "status": sc.get("status", ""),
                      "gap_before": sc.get("gap_before"), "gap_after": sc.get("gap_after"),
                      "held_split": bool(sc.get("held_split")), "sides": sides,
                      "judge": judge_by_q.get(q)})

    delphi = []
    for r in sorted((x for x in items.values() if x.get("type") == "delphi"), key=lambda x: x.get("agent", "")):
        for v in r.get("revisions") or []:
            q = v.get("question_id")
            delphi.append({"agent": r.get("agent"), "question_id": q, "question": qtext.get(q, ""),
                           "verdict": v.get("verdict", ""), "from": v.get("take"), "to": v.get("new"),
                           "delta": (v["new"] - v["take"]) if isinstance(v.get("new"), (int, float)) and isinstance(v.get("take"), (int, float)) else None,
                           "moved_by": v.get("moved_by", ""), "reason": v.get("reason", ""), "label": v.get("label", ""),
                           "would_change_my_mind": indicator_out(v.get("indicator") or v.get("would_change_my_mind")),
                           "evidence": evidence_out(v.get("evidence"))})
    if not delphi:
        for r in pd0.get("delphi") or []:
            for v in r.get("revisions") or []:
                q = v.get("question_id")
                delphi.append({"agent": r.get("agent"), "question_id": q, "question": qtext.get(q, ""),
                               "verdict": v.get("verdict", ""), "from": v.get("take"), "to": v.get("new"),
                               "delta": None, "moved_by": v.get("moved_by", ""), "reason": v.get("reason", ""),
                               "label": v.get("label", ""), "would_change_my_mind": indicator_out(v.get("indicator")),
                               "evidence": []})

    pm = jd.get("premortem") or pd0.get("premortem")
    premortem = None
    if isinstance(pm, dict) and pm.get("agent"):
        pdd = pm.get("data") or {}
        signs = pm.get("early_signs") or [{"indicator": x, "ok": None} for x in pdd.get("early_signs") or []]
        q = pm.get("question_id") or pdd.get("question_id")
        premortem = {"agent": pm["agent"], "question_id": q, "question": qtext.get(q, ""), "median": pm.get("median"),
                     "probability": pdd.get("probability"), "consensus_view": pdd.get("consensus_view", ""),
                     "failure_story": pdd.get("failure_story", ""),
                     "early_signs": [{**(indicator_out(s.get("indicator")) or {}), "ok": s.get("ok")}
                                     for s in signs if isinstance(s, dict) and indicator_out(s.get("indicator"))],
                     "evidence": evidence_out(pm.get("evidence") or pdd.get("evidence"))}

    summary = pd0.get("summary") or run.get("debate_summary") or {}
    return {"version": 1, "plan": pd0.get("plan"), "gate": pd0.get("gate"), "pairs": pairs, "delphi": delphi,
            "judge": judge, "premortem": premortem, "summary": summary, "moves_line": moves_line(summary, delphi),
            "calibration": calibration_out(rd, run)}


def fill_debate_fields(run: dict, pd: dict) -> None:
    """The fields the page already reads (edges, agents' challenges_made / challenges_received, debates) from the
    cross-exam pairs, so the Flow and the edges show who argued with whom. Existing entries are kept, none doubled."""
    agents = {a.get("name"): a for a in run.get("agents") or [] if isinstance(a, dict)}
    edges = run.setdefault("edges", [])
    have_e = {(e.get("from"), e.get("to"), e.get("question_id")) for e in edges if isinstance(e, dict)}
    debates = run.get("debates") or []
    have_d = {(x.get("challenger"), x.get("target"), x.get("question_id")) for x in debates if isinstance(x, dict)}
    for p in pd["pairs"]:
        q = p["question_id"]
        for s in p["sides"]:
            a, b = s["agent"], s["opponent"]
            if not a or not b or not (s["steelman"] or s["rebuttal"]):
                continue
            if (a, b, q) not in have_e:
                edges.append({"from": a, "to": b, "type": "xexam", "question_id": q})
                have_e.add((a, b, q))
            item = {"type": "xexam", "text": s["rebuttal"], "crux": (s["i_am_wrong_if"] or {}).get("text", ""), "question_id": q}
            for who, key, peer, field in ((a, "challenges_made", b, "to"), (b, "challenges_received", a, "from")):
                ag = agents.get(who)
                if ag is None:
                    continue
                lst = ag.setdefault(key, [])
                if not any(c.get("type") == "xexam" and c.get(field) == peer and c.get("question_id") == q for c in lst):
                    lst.append({field: peer, **item})
            if (a, b, q) not in have_d:
                other = next((x for x in p["sides"] if x["agent"] == b), None)
                debates.append({"challenger": a, "target": b, "type": "xexam", "question_id": q,
                                "crux": item["crux"], "gap_before": p["gap_before"], "gap_after": p["gap_after"],
                                "response_verdict": ((other or {}).get("reply") or {}).get("verdict", "")})
                have_d.add((a, b, q))
    run["debates"] = debates


def build_run(date: str) -> dict | None:
    rd = RECON_HOME / "briefs" / date
    if not rd.is_dir():
        return None
    # Runs made by recon/orchestrator.py carry their own typed record; nothing to scrape. A Phase D run also gets the
    # full debate (phase_d) from its phase files; every other run.json is exported exactly as written.
    rj = rd / "run.json"
    if rj.exists():
        try:
            run = json.loads(rj.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            run = None
        if run is not None:
            if isinstance(run, dict) and has_phase_d(rd, run):
                run["phase_d"] = phase_d_block(rd, run)
                fill_debate_fields(run, run["phase_d"])
            return run
    takes = {a: read(rd / f"03_take_{a}.md") for a in AGENTS if (rd / f"03_take_{a}.md").exists()}
    if not takes:
        return None
    log = parse_day_log(date)
    start, end = log.get("start") or "00:00:00", log.get("end") or "23:59:59"
    calls = parse_llm_calls(date, start, end)

    # edges + challenges
    edges, made, received = [], {a: [] for a in takes}, {a: [] for a in takes}
    for f in sorted(rd.glob("04a_*_vs_*.md")):
        c, t = f.stem[4:].split("_vs_", 1)
        txt = read(f)
        edges.append({"from": c, "to": t, "type": "tension"})
        made.setdefault(c, []).append({"to": t, "type": "tension", "text": txt[:2000]})
        received.setdefault(t, []).append({"from": c, "type": "tension", "text": txt[:2000]})
    for f in sorted(rd.glob("04c_wildcard_*_vs_*.md")):
        c, t = f.stem[len("04c_wildcard_"):].split("_vs_", 1)
        txt = read(f)
        edges.append({"from": c, "to": t, "type": "wildcard"})
        made.setdefault(c, []).append({"to": t, "type": "wildcard", "text": txt[:2000]})
        received.setdefault(t, []).append({"from": c, "type": "wildcard", "text": txt[:2000]})
    dd = {f.stem[len("05_5_deepdive_"):]: read(f) for f in rd.glob("05_5_deepdive_*.md")}
    dd_agents = list(dd)
    if len(dd_agents) == 2:
        edges.append({"from": dd_agents[0], "to": dd_agents[1], "type": "deepdive"})

    pkg = read(rd / "00_data_package.md")
    # what the takes read: the per-section agent view since 2026-10-04, the first 90 KB before
    view = read(rd / "01_filtered.md")
    fed_bytes = len(view.encode("utf-8")) if (rd / "01_package_report.json").exists() else min(len(pkg.encode("utf-8")), 90000)
    sections = package_sections(pkg)
    persona_dir = RECON_HOME / "personas"
    agents = []
    for a, take in takes.items():
        resp = read(rd / f"05_resp_{a}.md")
        vote_txt = read(rd / f"06_vote_{a}.md")
        upd, mem_lines, st_lines = memory_entry(a, date)
        ph = hashlib.sha1(read(persona_dir / f"{a}.md").encode("utf-8")).hexdigest()[:8]
        agents.append({
            "name": a, "desk": "shared", "persona_hash": ph,
            "fed": {"package_sections": [s["name"] for s in sections], "raw_sections": [],
                    "memory_lines": mem_lines, "state_lines": st_lines, "bytes": fed_bytes},
            "cites": [],
            "take": take, "response": resp or None, "deep_dive": dd.get(a),
            "vote": parse_vote(vote_txt) if vote_txt else None,
            "predictions": predictions_from_memory(a, date),
            "memory_update": upd or None,
            "calls": assign_phases(calls, a, len(made.get(a, [])), bool(resp), a in dd),
            "challenges_made": made.get(a, []), "challenges_received": received.get(a, []),
        })

    draft = read(rd / "07_brief_draft.md")
    final = read(rd / "07_daily_brief.md")
    synth_calls = [{"phase": "synthesis", "tier": c["tier"], "model": c["model"], "in_tok": c["in_tok"],
                    "out_tok": c["out_tok"], "seconds": c["seconds"]} for c in calls if c["agent"] == "synthesizer"]
    by_tier: dict[str, dict] = {}
    for c in calls:
        t = by_tier.setdefault(c["tier"], {"calls": 0, "in_tok": 0, "cached_tok": 0, "out_tok": 0, "seconds": 0})
        t["calls"] += 1; t["in_tok"] += c["in_tok"]; t["cached_tok"] += c["cached_tok"]
        t["out_tok"] += c["out_tok"]; t["seconds"] += c["seconds"]
    weights = []
    rec = read(rd / "07_full_record.md")
    m = re.search(r"ENVIRONMENT: ([A-Z-]+)\s*WEIGHT: ([^\n]+)", rec)
    if m:
        for w in [x.strip() for x in m.group(2).split(",") if x.strip()]:
            weights.append({"desk": w, "weight": 1.0})
    status = "ok" if (final and log.get("complete")) else ("partial" if takes else "failed")
    wall = secs(date, start, end) if log else 0
    srcs, scope = source_records(date, ts(date, end))
    return {
        "date": date, "mode": "daily", "status": status,
        "started": ts(date, start), "finished": ts(date, end), "wall_seconds": wall,
        "checkpoint": None,
        "triage": {"environment": log.get("environment") or (m.group(1) if m else None), "weights": weights,
                   "active_agents": {"shared": list(takes)}, "depth": "full",
                   "reason": "v1 pipeline: all agents active every run", "calls": []},
        "sources": srcs, "sources_scope": scope, "sources_from": "exporter" if scope == "run" else "export",
        "package": {"compact_bytes": len(pkg.encode("utf-8")), "sections": sections},
        "agents": agents, "edges": edges,
        "synthesis": {"draft": draft, "claims": [], "final": final, "calls": synth_calls},
        "usage": {"calls": len(calls), "by_tier": by_tier, "wall_seconds": wall},
        "delivery": {"telegram": bool(log.get("telegram")), "brief_words": len(final.split())},
        "phases": log.get("phases", []),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(RECON_HOME / "exports"))
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    out = Path(args.out)
    (out / "runs").mkdir(parents=True, exist_ok=True)
    index = []
    # daily runs are briefs/<date>; orchestrator validation runs are briefs/<date>-<tag>; dry runs stay out
    # Phase C: dry runs (-dry, -dryc ...), replays (run.json mode "replay") and tagged orchestrator folders that
    # stopped before run.json (spread probe, RECON_STOP_AFTER) stay out of the RUBRIC export; a daily folder
    # without run.json (the bash fallback) is still scraped as before.
    dates = sorted([p.name for p in (RECON_HOME / "briefs").iterdir()
                    if re.match(r"\d{4}-\d\d-\d\d(-[A-Za-z0-9]+)?$", p.name) and not re.search(r"-dry", p.name)
                    and not (len(p.name) > 10 and (p / "phases").is_dir() and not (p / "run.json").exists())],
                   reverse=True)[:args.days]
    for d in dates:
        run = build_run(d)
        if not run or run.get("mode") == "replay":
            continue
        (out / "runs" / f"{d}.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        srcs = run["sources"]
        index.append({"date": d, "mode": run["mode"], "status": run["status"], "started": run["started"],
                      "finished": run["finished"], "wall_seconds": run["wall_seconds"], "calls": run["usage"]["calls"],
                      "agents_active": len(run["agents"]), "sources_ok": sum(1 for s in srcs if s["ok"]),
                      "sources_total": len(srcs), "brief_words": run["delivery"]["brief_words"], "path": f"runs/{d}.json",
                      "pipeline": run.get("pipeline", "bash")})
        print(f"  {d}: {run['status']} agents={len(run['agents'])} calls={run['usage']['calls']} words={run['delivery']['brief_words']}")
    (out / "index.json").write_text(json.dumps({"generated_at": datetime.now(KST).isoformat(timespec="seconds"), "runs": index}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"exported {len(index)} runs → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
