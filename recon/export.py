#!/usr/bin/env python3
"""Export RECON run records for the RUBRIC recon page.

Reads the v1-layout run directories (briefs/<date>/03_take_*.md, 04a_*, 04c_*, 05_resp_*,
05_5_deepdive_*, 06_vote_*, 07_*), the day log, and logs/llm_calls.log, and writes

    <out>/index.json
    <out>/runs/<date>.json

in the schema recon.html consumes (see rubric/recon-sample). v1 has no desks, triage,
threads, or claims check yet; those fields are filled with what v1 knows and left empty
otherwise, so the page renders real runs today and richer ones after Phase 2.

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
         "bettafish": "bettafish"}


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
                 ("bettafish", "bettafish"), ("world monitor", "worldmonitor")]


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


def package_sections(pkg: str) -> list[dict]:
    parts = re.split(r"^# SECTION \d+: ", pkg, flags=re.M)
    out = []
    for part in parts[1:]:
        title, _, body = part.partition("\n")
        name = title.strip().lower().replace(" & ", "_").replace(" ", "_")
        out.append({"name": name[:40], "source": name.split("_")[0], "bytes": len(body.encode("utf-8")),
                    "items": len(re.findall(r"^- ", body, re.M)), "freshness": None})
    return out


def parse_vote(txt: str) -> dict:
    def grab(n):
        m = re.search(rf"(?:^|\n)\s*\**{n}[.)]\**\s*(.*?)(?=\n\s*\**{n + 1}[.)]|\Z)", txt, re.S)
        return re.sub(r"\s+", " ", m.group(1)).strip()[:400] if m else ""
    return {"act_on": grab(1), "market_wrong_about": grab(2), "unseen_risk": grab(3)}


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


def build_run(date: str) -> dict | None:
    rd = RECON_HOME / "briefs" / date
    if not rd.is_dir():
        return None
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
                    "memory_lines": mem_lines, "state_lines": st_lines, "bytes": min(len(pkg.encode("utf-8")), 90000)},
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
    dates = sorted([p.name for p in (RECON_HOME / "briefs").iterdir() if re.match(r"\d{4}-\d\d-\d\d$", p.name)], reverse=True)[:args.days]
    for d in dates:
        run = build_run(d)
        if not run:
            continue
        (out / "runs" / f"{d}.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        srcs = run["sources"]
        index.append({"date": d, "mode": run["mode"], "status": run["status"], "started": run["started"],
                      "finished": run["finished"], "wall_seconds": run["wall_seconds"], "calls": run["usage"]["calls"],
                      "agents_active": len(run["agents"]), "sources_ok": sum(1 for s in srcs if s["ok"]),
                      "sources_total": len(srcs), "brief_words": run["delivery"]["brief_words"], "path": f"runs/{d}.json"})
        print(f"  {d}: {run['status']} agents={len(run['agents'])} calls={run['usage']['calls']} words={run['delivery']['brief_words']}")
    (out / "index.json").write_text(json.dumps({"generated_at": datetime.now(KST).isoformat(timespec="seconds"), "runs": index}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"exported {len(index)} runs → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
