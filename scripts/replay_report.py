#!/usr/bin/env python3
"""Phase C replay report (docs/v2/phase-c-spec.md §15.3, §15.5). No LLM, no network: reads run folders and
the old exports.

    python3 scripts/replay_report.py 2026-09-10-c1 2026-09-11-c1 2026-10-04-c1 --old-dir <exports>/runs \
        [--stability 2026-10-04-c1 2026-10-04-c1s] [--probe briefs/spread_probe.md] \
        [--spread 2026-09-11-p1 2026-09-11-c8t1 ...]

Writes briefs/<run_id>/replay_report.md per run, briefs/replay_summary.md with the §15.5 pass bar across
the runs, and prints one model-log row per run. The old run is the export of the same day
(<old-dir>/<day>.json). Hindsight Brier (§15.4) needs the question resolver (Phase D): reported as pending.

Item (g), take-spread stability (§15.5, ninth review #73): every run of a day (the replays plus the --spread
runs, take-only reruns included) is one sample of that day's take spread. A day's pair count is reported as
the range over its samples, and each topic (questions sharing a subject across the runs' differing triage
wordings) as the take ranges it got and how often it cleared GAP_MIN: one replay's pair count is one sample.
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


def default_old_dir() -> Path:
    """Where the v1 exports live: <repo>/exports/runs on the droplet, where the replays run, else the laptop's
    ~/innovlabs/recon-exports/runs. The default was only the laptop path, so a replay report on the droplet found
    no old run and printed '— calls, 0.00 M input' as if the old run had used nothing (09-11 c13: 61 calls,
    1.36 M input in exports/runs/2026-09-11.json)."""
    cands = [REPO / "exports" / "runs", Path.home() / "innovlabs" / "recon-exports" / "runs"]
    return next((c for c in cands if c.is_dir()), cands[0])


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


def gap_ratios(debates: list[dict]) -> tuple[list[dict], float | None]:
    """Pass-bar item (c): gap_after/gap_before of each debate without crux data, and their median. The median
    alone hid a pair under the 0.6 bar (09-11 q3 macro_strategist/trader 22 -> 13, 0.59, beside a median of
    0.7), so the report prints each ratio beside it (§15.5 c, §20.7 #78)."""
    out = [{"question_id": dd["question_id"], "high": dd.get("high"), "low": dd.get("low"),
            "gap_before": dd["gap_before"], "gap_after": dd["gap_after"],
            "ratio": round(dd["gap_after"] / dd["gap_before"], 3), "below_bar": dd["gap_after"] / dd["gap_before"] < 0.6}
           for dd in debates
           if not dd.get("closed_on_data") and dd.get("gap_after") is not None and dd.get("gap_before")]
    med = statistics.median([dd["gap_after"] / dd["gap_before"] for dd in out]) if out else None
    return out, med


# A take range at most NEAR_MISS under GAP_MIN is a near miss: the probe's median retest |dp| is 3 (§15.0), so one
# rerun of the takes can carry it across. 09-11 c10: q1-q3 at 18/18/19 under GAP_MIN 20, 1 pair staged where c9
# staged 2, and nothing in the report said the pair count was one draw (§15.5 g, §20.7 #82).
NEAR_MISS = 3


# Pass-bar items judged on the run's debates (§15.5 b, c, d, debate evidence): NOT COUNTED on a run with no pair.
DEBATE_ITEMS = ("b held split (two-sided, gap_after >= GAP_MIN, cruxes stated)",
                "c gap_after/gap_before >= 0.6 (no crux data)",
                "d soft moves (> FREE_MOVE without qualifying evidence) <= 30%",
                "debate evidence >= 80%")


def near_misses(pos: dict, gap_min: int) -> list[dict]:
    """Questions whose take range is in [gap_min - NEAR_MISS, gap_min): not paired on this draw, paired on a
    likely rerun. The range is take_stats.range, else recomputed from take_p."""
    take_p = pos.get("take_p") or {}
    out = []
    for q in pos.get("questions") or []:
        rng = (q.get("take_stats") or {}).get("range")
        if rng is None:
            vals = [v[q["id"]] for v in take_p.values() if q.get("id") in v]
            rng = max(vals) - min(vals) if len(vals) >= 3 else None
        if rng is not None and gap_min - NEAR_MISS <= rng < gap_min:
            out.append({"question_id": q["id"], "range": int(rng)})
    return out


def lone_outliers(pos: dict, gap_min: int) -> list[dict]:
    """Questions whose take range reaches gap_min only through one lens (debate.lone_lens): pairing gives them no
    slot (§4.2, §20.7 #83); a resample of the takes, not this draw, says whether the split is real."""
    take_p = pos.get("take_p") or {}
    out = []
    for q in pos.get("questions") or []:
        x = debate.lone_outlier({a: v[q["id"]] for a, v in take_p.items() if q.get("id") in v}, gap_min)
        if x:
            out.append({"question_id": q["id"], "agent": x["agent"], "range": x["range"],
                        "trimmed_range": x["trimmed_range"]})
    return out


def render_ratios(per_debate: list[dict]) -> str:
    """'q3 macro_strategist/trader 22->13 0.59 (< 0.6)' per debate, '; '-joined."""
    return "; ".join(f"{x['question_id']} {x['high']}/{x['low']} {x['gap_before']}->{x['gap_after']} {x['ratio']:.2f}"
                     + (" (< 0.6)" if x.get("below_bar") else "") for x in per_debate)


def report(root: Path, run_id: str, old: dict | None, gap_min_probe: int | None,
           old_src: str | None = None) -> tuple[str, dict]:
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
    per_debate, ratio = gap_ratios(debates)
    soft = sum(1 for f in flags if f == "soft move")
    soft_req = sum(1 for f in flags if f == "soft request")
    # Debate endpoints per agent and per take tier (review 2026-10-04: is the spread the lenses or the models?)
    tier_of = {a: ((t.get("calls") or [{}])[0].get("tier") or debate.LENS_TIER.get(a, "analyst")) for a, t in takes.items()}
    endpoints: dict[str, int] = {}
    for x in pairing.get("pairs", []):
        for a in (x["high"], x["low"]):
            endpoints[a] = endpoints.get(a, 0) + 1
    by_tier: dict[str, int] = {}
    for a, n in endpoints.items():
        by_tier[tier_of.get(a, "analyst")] = by_tier.get(tier_of.get(a, "analyst"), 0) + n
    # Debate sides per tier (DEBATE_SCORE.tiers, seventh review): how far each tier's side moved towards the other
    # side, so a lens-tier endpoint defended on its own model can be compared with one defended on the analyst model.
    side_moves: dict[str, list[int]] = {}
    for dd in debates:
        tiers = dd.get("tiers") or {}
        for mv in dd.get("moves") or []:
            side = "high" if mv.get("agent") == dd.get("high") else "low"
            toward = -mv.get("delta", 0) if side == "high" else mv.get("delta", 0)
            t = tiers.get(side) or "unrecorded"
            side_moves.setdefault(t, []).append(int(toward))
    n_end = sum(endpoints.values())
    other_tier = sum(v for k, v in by_tier.items() if k != "analyst")
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
    gm_run = int(pairing.get("gap_min") or gap_min_probe or debate.GAP_MIN_DEFAULT)
    near = near_misses(pos, gm_run)
    near_txt = "; ".join(f"{x['question_id']} {x['range']}" for x in near) or "none"
    lone = lone_outliers(pos, gm_run)
    lone_txt = "; ".join(f"{x['question_id']} {x['range']} ({x['trimmed_range']} without {x['agent']})" for x in lone) or "none"
    m = {
        "run_id": run_id, "day": run.get("day"), "status": run.get("status"),
        "questions_kept": len(qs), "questions_dropped": len(dropped),
        "day_type": pairing.get("day_type"), "gap_min": pairing.get("gap_min"), "pairs": len(pairing.get("pairs", [])),
        "red_team": (pairing.get("red_team") or {}).get("agent"),
        "live_splits": sum(1 for dd in debates if dd.get("live_split")),
        # held split (§15.5 b): two-sided, still >= gap_min after the debate, not confirmed closed, both cruxes stated
        "held_splits": sum(1 for dd in debates if dd.get("held_split")),
        "useful": sum(1 for dd in debates if dd.get("useful")),
        "effects": [f"{dd['question_id']} {dd['high']}/{dd['low']}: {dd['effect']}" for dd in debates],
        "closure_without_evidence": dbs.get("closure_without_evidence"), "gap_ratio_no_crux": ratio,
        "gap_ratios_no_crux": per_debate,
        "evidence_rate": ev.get("rate"), "data_share": round(ev.get("data", 0) / max(1, ev.get("verified", 0) + ev.get("partial", 0)), 3) if ev else None,
        "debate_evidence_rate": dbs.get("debate_evidence_rate"),
        "concede": sum(1 for x in rl if x["data"].get("verdict") == "concede"), "responses": len(rl),
        "verbal_concessions": flags.count("verbal concession"), "soft_moves": soft, "soft_requests": soft_req,
        "endpoints": endpoints, "endpoints_by_tier": by_tier,
        "endpoint_share_off_analyst_tier": round(other_tier / n_end, 2) if n_end else None,
        "side_moves_by_tier": {t: {"sides": len(v), "mean_toward": round(sum(v) / len(v), 1)} for t, v in side_moves.items()},
        "moves_capped": sum(1 for f in flags if f.endswith("capped") or f.startswith("evidence move capped")),
        "new_evidence_source": {k: srcs.count(k) for k in ("crux_data", "challenger", "own", "other")},
        "leakage": ch_flags.count("persona leakage"), "agreement_openers": ch_flags.count("opens by agreeing"),
        "challenges": len(chs), "citation_overlap": (ev.get("citation_overlap") or {}).get("mean_jaccard"),
        "overlap_per_question": (ev.get("citation_overlap") or {}).get("mean_per_question"),
        "lens_quote_share": (ev.get("citation_overlap") or {}).get("lens_quote_share_mean"),
        "polymarket_mentions": len(re.findall(r"Polymarket", brief)), "f13_social_endpoints": f13,
        "sections_ok": heads[:11] == [s for s in schemas.BRIEF_SECTIONS] or bool(checks.get("sections_ok")),
        "words": len(brief.split()), "agent_names": checks.get("agent_names", []),
        "count_mismatch": checks.get("count_mismatch", []), "blocks_issues": checks.get("blocks", []),
        "calls": u.get("calls_logged", u.get("calls")), "budget_skips": len(u.get("budget_skips", [])),
        "in_tok": u.get("in_tok"), "cached_tok": u.get("cached_tok"), "out_tok": u.get("out_tok"),
        "wall": u.get("wall_seconds"), "ceiling": u.get("ceiling", 32), "lens_bytes": lens, "lens_ok": lens_ok,
        "spread": spread, "near_misses": near, "lone_outliers": lone, "top_pair": (pairing.get("pairs") or [{}])[0].get("question_id"),
    }
    o = old_metrics(old)
    # A run that staged no pair ran no debate: (b), (c), (d) and debate evidence have nothing to judge, and the
    # missing split is one take draw (09-11 c15: consensus, q3 19 one under GAP_MIN where c14 drew 34 on the same
    # package). Those items are None (NOT COUNTED) until take-only resamples say whether the day splits.
    m["debated"] = bool(m["pairs"]) or bool(debates)
    bar = {
        "b held split (two-sided, gap_after >= GAP_MIN, cruxes stated)": m["held_splits"] >= 1,
        "c gap_after/gap_before >= 0.6 (no crux data)": ratio is None or ratio >= 0.6,
        "d soft moves (> FREE_MOVE without qualifying evidence) <= 30%": (soft / len(rl) <= 0.3) if rl else True,
        # §15.5: 7 of 9 at 2 KB on 09-11 and 10-04; the 09-10 package predates the KOREA, AI EDUCATION and
        # fundraising blocks (measured 5/9), so its bar is every agent above 0
        **({"lens > 0 B for every agent (09-10 package)": bool(lens) and all(v > 0 for v in lens.values())}
           if str(run.get("day") or run_id[:10]) < "2026-09-11" else {"lens >= 2 KB for 7/9": lens_ok >= 7}),
        "debate evidence >= 80%": (dbs.get("debate_evidence_rate") or 0) >= 0.8 if rl or chs else True,
        "leakage <= 1": m["leakage"] <= 1,
        "calls <= ceiling": (m["calls"] or 0) <= m["ceiling"],
        "input <= 0.6 M": (m["in_tok"] or 0) <= 600_000,
        "brief 11 sections, no names, no mismatch": m["sections_ok"] and not m["agent_names"] and not m["count_mismatch"],
        "split_unpaired does not print No real split": not any("No real split" in x for x in m["blocks_issues"]),
        "F13: no social-only endpoint on a measurable question": not f13,
        # §15.5 as revised 2026-10-04: whole-take number overlap is not comparable with the free-form v1 takes
        # (the new takes answer the same questions); the bar is that takes cite their own lens data
        "lens-quote share >= 0.5": m["lens_quote_share"] is None or m["lens_quote_share"] >= 0.5,
    }
    if not m["debated"]:
        for k in DEBATE_ITEMS:
            bar[k] = None
    m["bar"] = bar
    m["not_counted_why"] = None if m["debated"] else (
        f"no debate ran on this draw ({m['day_type'] or 'no pairing'}, {m['pairs']} pairs"
        f"{'; near misses ' + near_txt if near else ''}"
        f"{'; one-lens ranges ' + lone_txt if lone else ''}): resample the takes twice before counting this run "
        f"(scripts/launch_run.py --resample {run_id})")
    tier_moves = "; ".join(f"{t} {v['sides']}, {v['mean_toward']}" for t, v in sorted(m["side_moves_by_tier"].items()))
    lines = [f"# Replay report — {run_id} (day {m['day']}, status {m['status']})", "",
             "Cold-start replay: no memory, state, ledger or historical context, unlike the original run.", "",
             *([f"Old run: {old_src}." if o else f"Old run: NO EXPORT ({old_src or 'none given'}); the old column is empty, "
                "not zero.", ""] if old_src or not o else []),
             "| metric | new | old |", "|---|---|---|",
             f"| questions kept / dropped by the gate | {m['questions_kept']} / {m['questions_dropped']} | — |",
             f"| take spread per question | {'; '.join(spread)} | — |",
             f"| day type, pairs, red team, gap_min | {m['day_type']}, {m['pairs']}, {m['red_team'] or '—'}, {m['gap_min']}"
             f"{f' (probe {gap_min_probe})' if gap_min_probe else ''} | — |",
             f"| near misses: take range within {NEAR_MISS} under gap_min {gm_run} (one draw: rerun --from-phase takes "
             f"before reading the pair count) | "
             f"{near_txt} | — |",
             f"| one-lens ranges: >= gap_min {gm_run} only through one lens, no debate slot (rerun --from-phase takes "
             f"twice before counting the run toward the pass bar) | {lone_txt} | — |",
             f"| live splits / held splits / useful debates | {m['live_splits']} / {m['held_splits']} / {m['useful']} | deep dive: {o.get('deep_dive', '—')} |",
             f"| effect per debate | {'; '.join(m['effects']) or '—'} | — |",
             f"| closure without evidence; median gap_after/gap_before (no crux data) | {m['closure_without_evidence']}; "
             f"{m['gap_ratio_no_crux'] if m['gap_ratio_no_crux'] is None else round(m['gap_ratio_no_crux'], 2)}"
             f"{f' (per debate: {render_ratios(per_debate)})' if per_debate else ''} | — |",
             f"| evidence verification rate (all / debate), data share | {m['evidence_rate']} / {m['debate_evidence_rate']}, {m['data_share']} | not measured |",
             f"| concessions, verbal concessions, soft moves (soft requests cut by the gate) | {m['concede']}/{m['responses']}, "
             f"{m['verbal_concessions']}, {m['soft_moves']} ({m['soft_requests']}) | updating: {o.get('updating', '—')} |",
             f"| debate endpoints per agent; by take tier (share off the analyst tier) | "
             f"{', '.join(f'{a} {n}' for a, n in sorted(endpoints.items())) or '—'}; {by_tier or '—'} "
             f"({m['endpoint_share_off_analyst_tier']}) | — |",
             f"| debate sides by call tier: sides, mean move towards the other side | "
             f"{tier_moves or '—'} | — |",
             f"| moves capped; new evidence sources | {m['moves_capped']}; {m['new_evidence_source']} | — |",
             f"| persona leakage, agreement openers | {m['leakage']}, {m['agreement_openers']} of {m['challenges']} | openers {o.get('agreement_openers', '—')} |",
             f"| citation overlap (mean Jaccard; per question; lens-quote share) | {m['citation_overlap']}; "
             f"{m['overlap_per_question']}; {m['lens_quote_share']} | {o.get('citation_overlap', '—')} |",
             f"| Polymarket mentions in the brief (F14) | {m['polymarket_mentions']} | {o.get('polymarket_mentions', '—')} |",
             f"| F13: social-only endpoints on measurable questions | {', '.join(f13) or 'none'} | — |",
             "| hindsight Brier (§15.4) | pending: needs the Phase D resolver | — |",
             f"| brief: sections ok, words, agent names, count mismatches | {m['sections_ok']}, {m['words']}, {len(m['agent_names'])}, {len(m['count_mismatch'])} | words {o.get('words', '—')} |",
             f"| calls (incl. re-asks), budget skips, input / cached / output tokens, wall | {m['calls']}, {m['budget_skips']}, "
             f"{(m['in_tok'] or 0) / 1e6:.2f} M / {(m['cached_tok'] or 0) / 1e6:.2f} M / {(m['out_tok'] or 0) / 1e3:.1f} K, {m['wall']} s | "
             + (f"{o.get('calls', '—')} calls, {(o.get('in_tok') or 0) / 1e6:.2f} M input |" if o else "no old export |"),
             f"| lens extras >= 2 KB | {lens_ok}/{len(lens)}: {', '.join(f'{a} {b}' for a, b in sorted(lens.items()))} | — |",
             "", "## Pass-bar items for this run (§15.5)", ""]
    lines += [f"- {'NOT COUNTED' if v is None else 'PASS' if v else 'FAIL'}: {k}" for k, v in bar.items()]
    if m["not_counted_why"]:
        lines.append(f"- NOT COUNTED: {m['not_counted_why']}")
    lines.append("")
    return "\n".join(lines), m


# ── (g) take-spread stability across same-day runs ─────────────────────────────────────

def run_sample(root: Path, run_id: str) -> dict | None:
    """One run's take spread: {run_id, day, questions [{id, text}], take_p {agent: {qid: int}}, pairs, target,
    gap_min}. A full replay reads positions.json; a take-only rerun (RECON_STOP_AFTER=takes) its takes/*.json
    and triage.json. pairs/target are None when the run stopped before pairing."""
    d = root / run_id
    pos = jload(d / "phases" / "positions.json", {}) or {}
    tri = jload(d / "phases" / "triage.json", {}) or {}
    pairing = jload(d / "phases" / "pairing.json", None)
    take_p = pos.get("take_p") or {}
    qs = [{"id": q["id"], "text": q.get("text") or ""} for q in pos.get("questions") or [] if q.get("id")]
    if not take_p:
        take_p = {a: debate.take_values(t.get("data") or {}) for a, t in items(d, "takes").items()}
        take_p = {a: v for a, v in take_p.items() if v}
    if not qs:
        qs = [{"id": q["id"], "text": q.get("text") or q.get("question") or ""}
              for q in (tri.get("data") or {}).get("questions") or [] if q.get("id")]
    if not take_p or not qs:
        return None
    return {"run_id": run_id, "day": run_id[:10], "questions": qs, "take_p": take_p,
            "pairs": len(pairing.get("pairs") or []) if isinstance(pairing, dict) else None,
            "target": pairing.get("target") if isinstance(pairing, dict) else None,
            "gap_min": pairing.get("gap_min") if isinstance(pairing, dict) else None}


def _subject(text: str) -> set[str]:
    return {e for e in debate.entities(text) if not any(c.isdigit() for c in e)}


def same_topic(a: str, b: str) -> bool:
    """Two triage wordings of one question: a shared subject entity ('OpenAI', 'South Korea'), or, when
    either names none, most of their words (Jaccard >= 0.5)."""
    sa, sb = _subject(a), _subject(b)
    if sa and sb:
        return bool(sa & sb)
    return debate.jaccard(a, b) >= 0.5


def spread_stability(samples: list[dict], gap_min: int = debate.GAP_MIN_DEFAULT) -> list[dict]:
    """Per day: the pair counts of its runs that reached pairing, and per topic the take range in every run
    that asked it, how many of those ranges reach gap_min, and whether that is unstable (cleared in some
    samples, not in others). Runs of one day are grouped by run id prefix (YYYY-MM-DD)."""
    out = []
    for day in sorted({s["day"] for s in samples}):
        runs = [s for s in samples if s["day"] == day]
        topics: list[dict] = []
        for s in runs:
            for q in s["questions"]:
                vals = [v[q["id"]] for v in s["take_p"].values() if q["id"] in v]
                if len(vals) < 3:
                    continue
                rng = int(max(vals) - min(vals))
                one = debate.lone_lens(vals, gap_min)
                t = next((t for t in topics if s["run_id"] not in t["runs"]
                          and any(same_topic(q["text"], x) for x in t["texts"])), None)
                if t is None:
                    t = {"label": ", ".join(sorted(_subject(q["text"]))) or q["text"][:40], "texts": [], "runs": [],
                         "ranges": [], "lone_flags": []}
                    topics.append(t)
                t["texts"].append(q["text"])
                t["runs"].append(s["run_id"])
                t["ranges"].append(rng)
                t["lone_flags"].append(one)
        for t in topics:
            t["asked"] = len(t["runs"])
            # a range one lens alone carries to gap_min does not clear: pairing gives it no slot (§20.7 #83)
            t["lone"] = sum(t["lone_flags"])
            t["clears"] = sum(1 for r, one in zip(t["ranges"], t["lone_flags"]) if r >= gap_min and not one)
            t["unstable"] = 0 < t["clears"] < t["asked"]
            # near misses that repeat across samples (never clearing) point at GAP_MIN, not at the draw (§20.7 #82)
            t["near"] = sum(1 for r in t["ranges"] if gap_min - NEAR_MISS <= r < gap_min)
            t["near_repeat"] = t["clears"] == 0 and t["near"] >= 2
        topics.sort(key=lambda t: (-t["clears"], -max(t["ranges"]), t["label"]))
        pairs = [s["pairs"] for s in runs if s["pairs"] is not None]
        targets = sorted({s["target"] for s in runs if s["target"] is not None})
        out.append({"day": day, "runs": [s["run_id"] for s in runs], "pairs": pairs, "targets": targets,
                    "gap_min": gap_min, "topics": topics})
    return out


def render_spread_stability(days: list[dict]) -> list[str]:
    lines = []
    for d in days:
        p = d["pairs"]
        pr = (f"pairs {min(p)}-{max(p)} over {len(p)} runs to pairing (target {'/'.join(map(str, d['targets']))})"
              if p else "no run reached pairing")
        lines.append(f"- (g) take-spread stability {d['day']}, {len(d['runs'])} samples ({', '.join(d['runs'])}): {pr}"
                     + ("; one replay's pair count is one sample" if p and min(p) != max(p) else ""))
        for t in d["topics"]:
            lines.append(f"  - {t['label']}: asked {t['asked']}/{len(d['runs'])}, take range "
                         f"{min(t['ranges'])}-{max(t['ranges'])} ({', '.join(map(str, t['ranges']))}), "
                         f"range >= GAP_MIN {d['gap_min']} in {t['clears']}/{t['asked']}"
                         + (f" (one lens carries the range in {t['lone']}: not counted)" if t.get("lone") else "")
                         + (" (UNSTABLE: the pair depends on the sample)" if t["unstable"] else "")
                         + (f" (NEAR MISS in {t['near']}/{t['asked']}, within {NEAR_MISS} under GAP_MIN: recheck "
                            f"GAP_MIN {d['gap_min']} against the probe)" if t.get("near_repeat") else ""))
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Phase C replay report")
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--root", default=str(REPO / "briefs"))
    ap.add_argument("--old-dir", default=None, help="v1 exports (<old-dir>/<day>.json); default: default_old_dir()")
    ap.add_argument("--stability", nargs=2, metavar=("RUN", "RERUN"))
    ap.add_argument("--probe", default=None, help="briefs/spread_probe.md, for GAP_MIN")
    ap.add_argument("--spread", nargs="+", default=[], metavar="RUN",
                    help="more runs (take-only reruns included) sampling the same days, for item (g)")
    a = ap.parse_args(argv)
    root = Path(a.root)
    gp = None
    if a.probe:
        mm = re.search(r"\*\*(\d+)\*\*", read(Path(a.probe)))
        gp = int(mm.group(1)) if mm else None
    allm = []
    for rid in a.runs:
        day = rid[:10]
        old_path = Path(a.old_dir) if a.old_dir else default_old_dir()
        old_path = old_path / f"{day}.json"
        old = jload(old_path)
        if not old:
            print(f"replay_report: no old export at {old_path}; the old column is empty", file=sys.stderr)
        text, m = report(root, rid, old, gp, str(old_path))
        (root / rid / "replay_report.md").write_text(text, encoding="utf-8")
        allm.append(m)
        print(f"| {day} | **Phase C replay {rid}** ({m['day_type']}): {m['questions_kept']} questions, {m['pairs']} pairs, "
              f"near misses {len(m['near_misses'])}, one-lens ranges {len(m['lone_outliers'])}, live splits {m['live_splits']}, held {m['held_splits']}, useful {m['useful']}, soft moves {m['soft_moves']}/{m['responses']} "
              f"(requests {m['soft_requests']}), endpoints by tier {m['endpoints_by_tier']}, "
              f"evidence {m['evidence_rate']}, overlap {m['citation_overlap']}, brief {m['words']} words, status {m['status']} "
              f"| {m['calls']} | {(m['in_tok'] or 0) / 1e6:.2f} M ({(m['cached_tok'] or 0) / 1e6:.2f} M cached) | "
              f"{(m['out_tok'] or 0) / 1e3:.1f} K | {m['wall']} s |")
    if len(allm) > 1 or a.stability or a.spread:
        s = ["# Phase C replays — pass bar (§15.5)", ""]
        # (b) is judged on the runs that debated; a run with no pair is one take draw, not a failed split (c15)
        skipped = [m for m in allm if not m["debated"]]
        skipped_txt = ", ".join(f"{m['run_id']}, {m['pairs']} pairs" for m in dict((m["run_id"], m) for m in skipped).values())
        if any(m["held_splits"] for m in allm):
            b = "PASS"
        elif len(skipped) < len(allm):
            b = "FAIL" + (f" (not counted: {skipped_txt})" if skipped else "")
        else:
            b = f"NOT JUDGED (no run debated; not counted: {skipped_txt}; resample the takes or replay another package)"
        s.append(f"- (b) a held split across the runs: {b}")
        ratios = [m["gap_ratio_no_crux"] for m in allm if m["gap_ratio_no_crux"] is not None]
        each = [x for m in allm for x in m.get("gap_ratios_no_crux") or []]
        s.append(f"- (c) median gap_after/gap_before without crux data: {statistics.median(ratios):.2f}"
                 f" (per debate: {render_ratios(each)})" if ratios else "- (c) no debates without crux data")
        soft = sum(m["soft_moves"] for m in allm)
        nresp = sum(m["responses"] for m in allm)
        s.append(f"- (d) soft moves: {soft}/{nresp} ({'PASS' if not nresp or soft / nresp <= 0.3 else 'FAIL'}); "
                 f"soft requests cut by the gate: {sum(m['soft_requests'] for m in allm)}")
        ends = sum(sum(m["endpoints"].values()) for m in allm)
        off = sum(sum(v for k, v in m["endpoints_by_tier"].items() if k != "analyst") for m in allm)
        if ends:
            s.append(f"- endpoints off the analyst tier: {off}/{ends} ({off / ends:.0%})"
                     + (" — most of the spread comes from the model, not the lens" if off / ends > 0.6 else ""))
        if a.stability:
            r1 = jload(root / a.stability[0] / "phases" / "pairing.json", {}) or {}
            r2 = jload(root / a.stability[1] / "phases" / "pairing.json", {}) or {}
            q1 = (r1.get("pairs") or [{}])[0].get("question_id")
            q2 = (r2.get("pairs") or [{}])[0].get("question_id")
            s.append(f"- (e) stability: top pair on {q1} vs {q2}: {'PASS' if q1 and q1 == q2 else 'FAIL'}")
        s.append("- (f) hindsight Brier: pending (Phase D resolver)")
        ids = list(dict.fromkeys(list(a.runs) + list(a.spread) + list(a.stability or [])))
        samples = [x for x in (run_sample(root, rid) for rid in ids) if x]
        gm = next((x["gap_min"] for x in samples if x["gap_min"]), None) or gp or debate.GAP_MIN_DEFAULT
        s += render_spread_stability(spread_stability(samples, int(gm)))
        for m in allm:
            fails = [k for k, v in m["bar"].items() if v is False]
            s.append(f"- {m['run_id']}: " + (f"not counted (no debate ran): {m['not_counted_why']}; " if not m["debated"] else "")
                     + ("all run items pass" if not fails else "fails: " + "; ".join(fails)))
        (root / "replay_summary.md").write_text("\n".join(s) + "\n", encoding="utf-8")
        print("\n".join(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
