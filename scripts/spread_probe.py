#!/usr/bin/env python3
"""Phase C spread probe report (docs/v2/phase-c-spec.md §15.0). No LLM, no network: reads run folders.

    python3 scripts/spread_probe.py --packages 2026-09-11-p1 2026-10-04-p1 \
        --retest 2026-10-04-p1 2026-10-04-p2 [--lens 2026-10-04-p3 2026-10-04-p3h] [--out briefs/spread_probe.md]

Per package: each question's n, median, range, the largest take gap between two agents that straddles the
median (all agents, and eligible agents only: a verified quote, data-class unless the question is a
judgment, as pairing uses), and the agents on each side of 50.
Per agent: test-retest |dp| between the two --retest runs (same triage, takes rerun).
GAP_MIN = max(20, 2 x median retest |dp|) over all agents and questions, rounded up.
Per package: the questions whose largest eligible straddling gap is >= GAP_MIN and above the two agents'
combined retest noise (their own |dp| on that question when the package was retested, else each agent's
median |dp|).
Lens diversity (--lens: SYNTH model run, then high-effort run; skeptic and macro_strategist): distance from
the ANALYST median minus the agent's retest |dp|; a setting is kept when the adjusted distance rises on at
least half the questions.
Gate: at least one question per package clears. Prints GAP_MIN=, GATE=, LENS_TIER= lines and one model-log row.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from recon import debate, evidence  # noqa: E402

LENS_AGENTS = ("skeptic", "macro_strategist")


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def load_run(root: Path, run_id: str) -> dict:
    d = root / run_id
    tri = json.loads(read(d / "phases" / "triage.json") or "{}").get("data", {})
    takes = {}
    for f in sorted((d / "phases" / "takes").glob("*.json")):
        takes[f.stem] = json.loads(read(f)).get("data", {})
    loc = evidence.Locator({"package": read(d / "00_data_package.md"), "raw": read(d / "00_raw_data.md"),
                            "view": read(d / "01_filtered.md"), "social": read(d / "01_social.md")})
    p = {a: debate.take_values(t) for a, t in takes.items()}
    evq = {}
    for a, t in takes.items():
        for pos in t.get("positions") or []:
            items = []
            for e in pos.get("evidence") or []:
                qq = (e.get("quote") or "").strip()
                r = loc.locate(qq) if qq else {"status": "empty", "cls": ""}
                items.append({"quote": qq, "status": r["status"], "cls": debate.ev_class(r, loc)})
            evq.setdefault(a, {})[pos.get("question_id", "")] = items
    calls = [json.loads(l) for l in read(d / "phases" / "calls.jsonl").splitlines() if l.strip()]
    return {"id": run_id, "questions": tri.get("questions") or [], "p": p, "evq": evq, "calls": calls,
            "fed": {f.stem: json.loads(read(f)).get("fed", {}) for f in (d / "phases" / "takes").glob("*.json")}}


def straddle(q: dict, p: dict, agents: list[str]) -> tuple[int, str | None, str | None]:
    vals = {a: p[a][q["id"]] for a in agents if q["id"] in p.get(a, {})}
    if len(vals) < 2:
        return 0, None, None
    med = statistics.median(list(vals.values()))
    best = (0, None, None)
    for a in vals:
        for b in vals:
            if vals[a] <= med <= vals[b] and vals[b] - vals[a] > best[0]:
                best = (vals[b] - vals[a], a, b)
    return best


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Phase C spread probe report")
    ap.add_argument("--root", default=str(REPO / "briefs"))
    ap.add_argument("--packages", nargs="+", required=True, help="one run id per package (triage + takes)")
    ap.add_argument("--retest", nargs=2, required=True, metavar=("RUN_A", "RUN_B"))
    ap.add_argument("--lens", nargs="*", default=[], help="runs with the lens settings (SYNTH model, high effort)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    root = Path(a.root)
    runs = {r: load_run(root, r) for r in dict.fromkeys(a.packages + a.retest + a.lens)}
    ra, rb = runs[a.retest[0]], runs[a.retest[1]]
    retest: dict[str, dict[str, int]] = {}
    for ag in ra["p"]:
        for q, v in ra["p"][ag].items():
            if q in rb["p"].get(ag, {}):
                retest.setdefault(ag, {})[q] = abs(v - rb["p"][ag][q])
    all_d = [d for v in retest.values() for d in v.values()]
    med_retest = statistics.median(all_d) if all_d else 0
    gap_min = max(20, math.ceil(2 * med_retest))
    agent_noise = {ag: statistics.median(v.values()) for ag, v in retest.items() if v}

    out = [f"# Spread probe ({', '.join(a.packages)}; retest {a.retest[0]} vs {a.retest[1]})", "",
           f"GAP_MIN = max(20, 2 x median retest |dp| = 2 x {med_retest:g}) = **{gap_min}**", ""]
    gate_ok, clear_counts, rows = True, {}, []
    for rid in a.packages:
        r = runs[rid]
        agents = sorted(r["p"])
        out += [f"## {rid}", "", "| q | kind | n | median | range | widest straddle (all) | widest straddle (eligible) | "
                "noise | clears | >50 / <50 |", "|---|---|---|---|---|---|---|---|---|---|"]
        clears = 0
        for q in r["questions"]:
            vals = [r["p"][x][q["id"]] for x in agents if q["id"] in r["p"][x]]
            if not vals:
                continue
            gap_all, _, _ = straddle(q, r["p"], agents)
            elig = debate.eligible_agents(q, r["p"], r["evq"], agents)
            gap_el, lo, hi = straddle(q, r["p"], elig)
            same = rid in (a.retest[0], a.retest[1])
            noise = 0.0
            if lo and hi:
                if same:
                    noise = retest.get(lo, {}).get(q["id"], agent_noise.get(lo, med_retest)) + \
                            retest.get(hi, {}).get(q["id"], agent_noise.get(hi, med_retest))
                else:
                    noise = agent_noise.get(lo, med_retest) + agent_noise.get(hi, med_retest)
            ok = bool(lo and gap_el >= gap_min and gap_el > noise)
            clears += ok
            out.append(f"| {q['id']} | {q.get('kind', '')} | {len(vals)} | {statistics.median(vals):g} | "
                       f"{max(vals) - min(vals)} | {gap_all} | {gap_el} ({lo or '-'} {r['p'].get(lo, {}).get(q['id'], '')} / "
                       f"{hi or '-'} {r['p'].get(hi, {}).get(q['id'], '')}) | {noise:g} | {'yes' if ok else 'no'} | "
                       f"{sum(v > 50 for v in vals)} / {sum(v < 50 for v in vals)} |")
        lens_ok = sum(1 for f in r["fed"].values() if f.get("lens_extra_bytes", 0) >= 2000)
        out += ["", f"Questions clearing GAP_MIN against retest noise: **{clears}**; lens data >= 2 KB for "
                f"{lens_ok}/{len(r['fed'])} agents.", ""]
        clear_counts[rid] = clears
        gate_ok = gate_ok and clears >= 1
    out += ["## Test-retest |dp| per agent", "", "| agent | per question | median |", "|---|---|---|"]
    for ag in sorted(retest):
        out.append(f"| {ag} | {', '.join(f'{q}:{d}' for q, d in sorted(retest[ag].items()))} | {agent_noise[ag]:g} |")
    lens_tier = {}
    if a.lens:
        out += ["", "## Lens diversity (distance from the ANALYST median minus retest |dp|)", "",
                "| run | agent | adjusted distance per question (ANALYST -> setting) | rises on | keep |", "|---|---|---|---|---|"]
        base = runs[a.retest[0]]
        for lr in a.lens:
            r = runs[lr]
            for ag in LENS_AGENTS:
                if ag not in r["p"]:
                    continue
                rises, cells = 0, []
                for q in base["questions"]:
                    qid = q["id"]
                    vals = [base["p"][x][qid] for x in base["p"] if qid in base["p"][x]]
                    if not vals or qid not in r["p"][ag]:
                        continue
                    med = statistics.median(vals)
                    an = [run["p"][ag][qid] for run in (ra, rb) if qid in run["p"].get(ag, {})]
                    noise = retest.get(ag, {}).get(qid, agent_noise.get(ag, med_retest))
                    d0 = abs(statistics.mean(an) - med) - noise if an else 0
                    d1 = abs(r["p"][ag][qid] - med) - noise
                    rises += d1 > d0
                    cells.append(f"{qid} {d0:.0f}->{d1:.0f}")
                keep = cells and rises >= len(cells) / 2
                if keep:
                    lens_tier.setdefault(ag, lr)
                out.append(f"| {lr} | {ag} | {', '.join(cells)} | {rises}/{len(cells)} | {'yes' if keep else 'no'} |")
    calls = sum(len(r["calls"]) for r in runs.values())
    toks = sum(c.get("in_tok", 0) for r in runs.values() for c in r["calls"])
    cached = sum(c.get("cached_tok", 0) for r in runs.values() for c in r["calls"])
    outt = sum(c.get("out_tok", 0) for r in runs.values() for c in r["calls"])
    verdict = "pass" if gate_ok else "fail"
    out += ["", f"## Gate: **{verdict}** ({', '.join(f'{k}: {v}' for k, v in clear_counts.items())} question(s) clear)",
            f"LENS_TIER: {json.dumps(lens_tier) if lens_tier else 'empty (no setting raised retest-adjusted spread on half the questions)'}"]
    row = (f"| {a.retest[0][:10]} | **Phase C spread probe** ({', '.join(runs)}): GAP_MIN {gap_min} (median retest |dp| "
           f"{med_retest:g}); questions clearing per package {', '.join(f'{k} {v}' for k, v in clear_counts.items())}; gate "
           f"{verdict}; LENS_TIER {json.dumps(lens_tier) if lens_tier else 'empty'} | {calls} | {toks / 1e6:.2f} M "
           f"({cached / 1e6:.2f} M cached) | {outt / 1e3:.1f} K | — |")
    out += ["", "Model-log row:", "", row, ""]
    dest = Path(a.out) if a.out else root / "spread_probe.md"
    dest.write_text("\n".join(out), encoding="utf-8")
    print(f"GAP_MIN={gap_min}")
    print(f"GATE={verdict}")
    print(f"LENS_TIER={json.dumps(lens_tier)}")
    print(row)
    print(f"report: {dest}")
    return 0 if gate_ok else 3


if __name__ == "__main__":
    sys.exit(main())
