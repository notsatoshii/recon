#!/usr/bin/env python3
"""Phase C replay report (docs/v2/phase-c-spec.md §15.3, §15.5). No LLM, no network: reads run folders and
the old exports.

    python3 scripts/replay_report.py 2026-09-10-c1 2026-09-11-c1 2026-10-04-c1 --old-dir <exports>/runs \
        [--stability 2026-10-04-c1 2026-10-04-c1s] [--probe briefs/spread_probe.md]

Writes briefs/<run_id>/replay_report.md per run, briefs/replay_summary.md with the §15.5 pass bar across
the runs, and prints one model-log row per run. The old run is the export of the same day
(<old-dir>/<day>.json). Hindsight Brier (§15.4) needs the question resolver (Phase D): reported as pending.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from recon import debate, evidence, schemas  # noqa: E402


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def jload(p: Path, default=None):
    try:
        return json.loads(read(p))
    except json.JSONDecodeError:
        return default


def items(d: Path, phase: str) -> dict:
    return {f.stem: jload(f, {}) for f in sorted((d / "phases" / phase).glob("*.json"))}


def old_metrics(old: dict | None) -> dict:
    if not old:
        return {}
    agents = old.get("agents") or []
    takes = {a["name"]: a.get("take") or "" for a in agents}
    upd = sum(1 for a in agents if re.search(r"I am updating my position|updating my position", a.get("response") or "", re.I))
    resp = sum(1 for a in agents if a.get("response"))
    chal = [c for a in agents for c in a.get("challenges_made") or []]
    agree = sum(1 for c in chal if debate.AGREE.search(" ".join((c.get("text") or "").split()[:12])))
    brief = (old.get("synthesis") or {}).get("final") or ""
    u = old.get("usage") or {}
    in_tok = u.get("in_tok") or sum((t or {}).get("in_tok", 0) for t in (u.get("by_tier") or {}).values())
    return {"citation_overlap": evidence.citation_overlap(takes).get("mean_jaccard"), "updating": f"{upd} of {resp}",
            "agreement_openers": f"{agree} of {len(chal)}", "polymarket_mentions": len(re.findall(r"Polymarket", brief)),
            "calls": u.get("calls"), "in_tok": in_tok, "words": len(brief.split()),
            "deep_dive": "yes" if any(a.get("deep_dive") for a in agents) else "no"}


def report(root: Path, run_id: str, old: dict | None, gap_min_probe: int | None) -> tuple[str, dict]:
    d = root / run_id
    run = jload(d / "run.json", {}) or {}
    tri = jload(d / "phases" / "triage.json", {}) or {}
    pairing = jload(d / "phases" / "pairing.json", {}) or {}
    pos = jload(d / "phases" / "positions.json", {}) or {}
    checks = jload(d / "phases" / "checks.json", {}) or {}
    chs, resps = items(d, "challenges"), items(d, "responses")
    takes = items(d, "takes")
    debates = pos.get("debates", [])
    rl = list(resps.values())
    flags = [f for x in rl for f in x["move"].get("flags", [])]
    srcs = [e.get("new_evidence_source") for x in rl for e in x["move"].get("new_evidence", [])]
    ch_flags = [f for c in chs.values() for f in (c.get("checks") or {}).get("flags", [])]
    no_crux = [dd for dd in debates if not dd.get("closed_on_data") and dd.get("gap_after") is not None and dd["gap_before"]]
    ratio = statistics.median([dd["gap_after"] / dd["gap_before"] for dd in no_crux]) if no_crux else None
    soft = sum(1 for f in flags if f == "soft move")
    lens = {a: (t.get("fed") or {}).get("lens_extra_bytes", 0) for a, t in takes.items()}
    lens_ok = sum(1 for v in lens.values() if v >= 2000)
    ev = run.get("evidence") or {}
    dbs = run.get("debate_summary") or pos.get("summary") or {}
    u = run.get("usage") or {}
    qs = (tri.get("data") or {}).get("questions") or []
    dropped = (tri.get("gate") or {}).get("dropped") or []
    brief = read(d / "07_daily_brief.md")
    heads = [re.sub(r"^#+\s*|\*", "", l).strip().upper() for l in brief.splitlines() if l.startswith("### ")]
    f13 = []
    pe = pairing.get("positions_evidence") or {}
    qkind = {q["id"]: q.get("kind") for q in qs}
    for x in pairing.get("pairs", []):
        if qkind.get(x["question_id"]) == "judgment":
            continue
        for a in (x["high"], x["low"]):
            okd = [e for e in (pe.get(a, {}) or {}).get(x["question_id"], []) if e.get("status") in ("verified", "partial")]
            if okd and not any(e.get("cls") == "data" for e in okd):
                f13.append(f"{x['question_id']}:{a}")
    spread = []
    for q in pos.get("questions", []):
        ts = q.get("take_stats") or {}
        spread.append(f"{q['id']} range {ts.get('range')} (median {ts.get('median')})")
    m = {
        "run_id": run_id, "day": run.get("day"), "status": run.get("status"),
        "questions_kept": len(qs), "questions_dropped": len(dropped),
        "day_type": pairing.get("day_type"), "gap_min": pairing.get("gap_min"), "pairs": len(pairing.get("pairs", [])),
        "red_team": (pairing.get("red_team") or {}).get("agent"),
        "live_splits": sum(1 for dd in debates if dd.get("live_split")),
        "useful": sum(1 for dd in debates if dd.get("useful")),
        "effects": [f"{dd['question_id']} {dd['high']}/{dd['low']}: {dd['effect']}" for dd in debates],
        "closure_without_evidence": dbs.get("closure_without_evidence"), "gap_ratio_no_crux": ratio,
        "evidence_rate": ev.get("rate"), "data_share": round(ev.get("data", 0) / max(1, ev.get("verified", 0) + ev.get("partial", 0)), 3) if ev else None,
        "debate_evidence_rate": dbs.get("debate_evidence_rate"),
        "concede": sum(1 for x in rl if x["data"].get("verdict") == "concede"), "responses": len(rl),
        "verbal_concessions": flags.count("verbal concession"), "soft_moves": soft,
        "moves_capped": sum(1 for f in flags if f.endswith("capped") or f.startswith("evidence move capped")),
        "new_evidence_source": {k: srcs.count(k) for k in ("crux_data", "challenger", "own", "other")},
        "leakage": ch_flags.count("persona leakage"), "agreement_openers": ch_flags.count("opens by agreeing"),
        "challenges": len(chs), "citation_overlap": (ev.get("citation_overlap") or {}).get("mean_jaccard"),
        "polymarket_mentions": len(re.findall(r"Polymarket", brief)), "f13_social_endpoints": f13,
        "sections_ok": heads[:11] == [s for s in schemas.BRIEF_SECTIONS] or bool(checks.get("sections_ok")),
        "words": len(brief.split()), "agent_names": checks.get("agent_names", []),
        "count_mismatch": checks.get("count_mismatch", []), "blocks_issues": checks.get("blocks", []),
        "calls": u.get("calls_logged", u.get("calls")), "budget_skips": len(u.get("budget_skips", [])),
        "in_tok": u.get("in_tok"), "cached_tok": u.get("cached_tok"), "out_tok": u.get("out_tok"),
        "wall": u.get("wall_seconds"), "ceiling": u.get("ceiling", 32), "lens_bytes": lens, "lens_ok": lens_ok,
        "spread": spread, "top_pair": (pairing.get("pairs") or [{}])[0].get("question_id"),
    }
    o = old_metrics(old)
    bar = {
        "b live split": m["live_splits"] >= 1,
        "c gap_after/gap_before >= 0.6 (no crux data)": ratio is None or ratio >= 0.6,
        "d soft moves <= 30%": (soft / len(rl) <= 0.3) if rl else True,
        "lens >= 2 KB for 7/9": lens_ok >= 7,
        "debate evidence >= 80%": (dbs.get("debate_evidence_rate") or 0) >= 0.8 if rl or chs else True,
        "leakage <= 1": m["leakage"] <= 1,
        "calls <= ceiling": (m["calls"] or 0) <= m["ceiling"],
        "input <= 0.6 M": (m["in_tok"] or 0) <= 600_000,
        "brief 11 sections, no names, no mismatch": m["sections_ok"] and not m["agent_names"] and not m["count_mismatch"],
        "split_unpaired does not print No real split": not any("No real split" in x for x in m["blocks_issues"]),
        "F13: no social-only endpoint on a measurable question": not f13,
        "citation overlap lower than old": (o.get("citation_overlap") is None or m["citation_overlap"] is None
                                            or m["citation_overlap"] < o["citation_overlap"]),
    }
    m["bar"] = bar
    lines = [f"# Replay report — {run_id} (day {m['day']}, status {m['status']})", "",
             "Cold-start replay: no memory, state, ledger or historical context, unlike the original run.", "",
             "| metric | new | old |", "|---|---|---|",
             f"| questions kept / dropped by the gate | {m['questions_kept']} / {m['questions_dropped']} | — |",
             f"| take spread per question | {'; '.join(spread)} | — |",
             f"| day type, pairs, red team, gap_min | {m['day_type']}, {m['pairs']}, {m['red_team'] or '—'}, {m['gap_min']}"
             f"{f' (probe {gap_min_probe})' if gap_min_probe else ''} | — |",
             f"| live splits / useful debates | {m['live_splits']} / {m['useful']} | deep dive: {o.get('deep_dive', '—')} |",
             f"| effect per debate | {'; '.join(m['effects']) or '—'} | — |",
             f"| closure without evidence; median gap_after/gap_before (no crux data) | {m['closure_without_evidence']}; "
             f"{m['gap_ratio_no_crux'] if m['gap_ratio_no_crux'] is None else round(m['gap_ratio_no_crux'], 2)} | — |",
             f"| evidence verification rate (all / debate), data share | {m['evidence_rate']} / {m['debate_evidence_rate']}, {m['data_share']} | not measured |",
             f"| concessions, verbal concessions, soft moves | {m['concede']}/{m['responses']}, {m['verbal_concessions']}, {m['soft_moves']} | updating: {o.get('updating', '—')} |",
             f"| moves capped; new evidence sources | {m['moves_capped']}; {m['new_evidence_source']} | — |",
             f"| persona leakage, agreement openers | {m['leakage']}, {m['agreement_openers']} of {m['challenges']} | openers {o.get('agreement_openers', '—')} |",
             f"| citation overlap (mean Jaccard) | {m['citation_overlap']} | {o.get('citation_overlap', '—')} |",
             f"| Polymarket mentions in the brief (F14) | {m['polymarket_mentions']} | {o.get('polymarket_mentions', '—')} |",
             f"| F13: social-only endpoints on measurable questions | {', '.join(f13) or 'none'} | — |",
             "| hindsight Brier (§15.4) | pending: needs the Phase D resolver | — |",
             f"| brief: sections ok, words, agent names, count mismatches | {m['sections_ok']}, {m['words']}, {len(m['agent_names'])}, {len(m['count_mismatch'])} | words {o.get('words', '—')} |",
             f"| calls (incl. re-asks), budget skips, input / cached / output tokens, wall | {m['calls']}, {m['budget_skips']}, "
             f"{(m['in_tok'] or 0) / 1e6:.2f} M / {(m['cached_tok'] or 0) / 1e6:.2f} M / {(m['out_tok'] or 0) / 1e3:.1f} K, {m['wall']} s | "
             f"{o.get('calls', '—')} calls, {(o.get('in_tok') or 0) / 1e6:.2f} M input |",
             f"| lens extras >= 2 KB | {lens_ok}/{len(lens)}: {', '.join(f'{a} {b}' for a, b in sorted(lens.items()))} | — |",
             "", "## Pass-bar items for this run (§15.5)", ""]
    lines += [f"- {'PASS' if v else 'FAIL'}: {k}" for k, v in bar.items()]
    lines.append("")
    return "\n".join(lines), m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Phase C replay report")
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--root", default=str(REPO / "briefs"))
    ap.add_argument("--old-dir", default=str(Path.home() / "innovlabs" / "recon-exports" / "runs"))
    ap.add_argument("--stability", nargs=2, metavar=("RUN", "RERUN"))
    ap.add_argument("--probe", default=None, help="briefs/spread_probe.md, for GAP_MIN")
    a = ap.parse_args(argv)
    root = Path(a.root)
    gp = None
    if a.probe:
        mm = re.search(r"\*\*(\d+)\*\*", read(Path(a.probe)))
        gp = int(mm.group(1)) if mm else None
    allm = []
    for rid in a.runs:
        day = rid[:10]
        old = jload(Path(a.old_dir) / f"{day}.json")
        text, m = report(root, rid, old, gp)
        (root / rid / "replay_report.md").write_text(text, encoding="utf-8")
        allm.append(m)
        print(f"| {day} | **Phase C replay {rid}** ({m['day_type']}): {m['questions_kept']} questions, {m['pairs']} pairs, "
              f"live splits {m['live_splits']}, useful {m['useful']}, soft moves {m['soft_moves']}/{m['responses']}, "
              f"evidence {m['evidence_rate']}, overlap {m['citation_overlap']}, brief {m['words']} words, status {m['status']} "
              f"| {m['calls']} | {(m['in_tok'] or 0) / 1e6:.2f} M ({(m['cached_tok'] or 0) / 1e6:.2f} M cached) | "
              f"{(m['out_tok'] or 0) / 1e3:.1f} K | {m['wall']} s |")
    if len(allm) > 1 or a.stability:
        s = ["# Phase C replays — pass bar (§15.5)", ""]
        s.append(f"- (b) a live split across the runs: {'PASS' if any(m['live_splits'] for m in allm) else 'FAIL'}")
        ratios = [m["gap_ratio_no_crux"] for m in allm if m["gap_ratio_no_crux"] is not None]
        s.append(f"- (c) median gap_after/gap_before without crux data: {statistics.median(ratios):.2f}"
                 if ratios else "- (c) no debates without crux data")
        soft = sum(m["soft_moves"] for m in allm)
        nresp = sum(m["responses"] for m in allm)
        s.append(f"- (d) soft moves: {soft}/{nresp} ({'PASS' if not nresp or soft / nresp <= 0.3 else 'FAIL'})")
        if a.stability:
            r1 = jload(root / a.stability[0] / "phases" / "pairing.json", {}) or {}
            r2 = jload(root / a.stability[1] / "phases" / "pairing.json", {}) or {}
            q1 = (r1.get("pairs") or [{}])[0].get("question_id")
            q2 = (r2.get("pairs") or [{}])[0].get("question_id")
            s.append(f"- (e) stability: top pair on {q1} vs {q2}: {'PASS' if q1 and q1 == q2 else 'FAIL'}")
        s.append("- (f) hindsight Brier: pending (Phase D resolver)")
        for m in allm:
            fails = [k for k, v in m["bar"].items() if not v]
            s.append(f"- {m['run_id']}: {'all run items pass' if not fails else 'fails: ' + '; '.join(fails)}")
        (root / "replay_summary.md").write_text("\n".join(s) + "\n", encoding="utf-8")
        print("\n".join(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
