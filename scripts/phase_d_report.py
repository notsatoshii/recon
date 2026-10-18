#!/usr/bin/env python3
"""phase_d_report.py -- the Phase D replay pass bar per run (docs/v2/phase-d-debate-upgrade.md 'Validation, then on').
No LLM, no network: reads briefs/<run>/run.json and phases/*.json.

    python3 scripts/phase_d_report.py <run id> [<run id> ...] [--home DIR]

Per run: day type, calls (bar <= 30), wall minutes (bar <= 20), cross-exam pairs and Delphi calls, whether every
cross-examined split ends with an indicator the judge marks resolvable, evidence verification (verified + partial
over claimed, bar >= 80%; the debate calls' own rate beside it), agent names in the brief (bar 0), split-sheet
blocks missing from WHERE THE VIEWS SPLIT (the round-0 count phrases; bar 0), and the share of moves made on
argument alone (reported, no bar). Exit 0 when every run passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BAR_CALLS, BAR_MINUTES, BAR_EVIDENCE = 30, 20, 0.80


def jload(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def row(home: Path, run_id: str) -> dict:
    d = home / "briefs" / run_id
    run = jload(d / "run.json", {}) or {}
    pairing = jload(d / "phases" / "pairing.json", {}) or {}
    pos = jload(d / "phases" / "positions.json", {}) or {}
    checks = jload(d / "phases" / "checks.json", {}) or {}
    summ = pos.get("summary") or {}
    ev = run.get("evidence") or {}
    calls = (run.get("usage") or {}).get("calls")
    wall = (run.get("usage") or {}).get("wall_seconds")
    pairs = len(pairing.get("pairs") or [])
    judge = summ.get("judge") or {}
    top_ok = judge.get("top_splits_resolvable") if pairs else None
    r = {"run": run_id, "found": bool(run), "status": run.get("status"), "mode": run.get("debate_mode"),
         "day_type": pairing.get("day_type"), "calls": calls, "minutes": round(wall / 60, 1) if wall else None,
         "pairs": pairs, "delphi_calls": (summ.get("delphi") or {}).get("calls"),
         "premortem": summ.get("premortem"), "top_splits_resolvable": top_ok,
         "judge_resolvable": f"{judge.get('resolvable', 0)}/{judge.get('items', 0)}",
         "evidence_rate": ev.get("rate"), "debate_evidence_rate": summ.get("debate_evidence_rate"),
         "agent_names": len(checks.get("agent_names") or []), "split_missing": len(checks.get("split_missing") or []),
         "moves": summ.get("moves"), "argument_only_share": summ.get("argument_only_share"),
         "held_splits": summ.get("held_splits")}
    fails = []
    if not r["found"]:
        fails.append("no run.json")
    if r["mode"] != "d":
        fails.append(f"debate mode {r['mode']!r}, not Phase D")
    if calls is None or calls > BAR_CALLS:
        fails.append(f"calls {calls} > {BAR_CALLS}")
    if r["minutes"] is None or r["minutes"] > BAR_MINUTES:
        fails.append(f"wall {r['minutes']} min > {BAR_MINUTES}")
    if pairs and not top_ok:
        fails.append("a cross-examined split has no indicator the judge marks resolvable")
    if r["evidence_rate"] is None or r["evidence_rate"] < BAR_EVIDENCE:
        fails.append(f"evidence verification {r['evidence_rate']} < {BAR_EVIDENCE}")
    if r["agent_names"]:
        fails.append(f"{r['agent_names']} agent name(s) in the brief")
    if r["split_missing"]:
        fails.append(f"{r['split_missing']} split-sheet block(s) missing from the brief")
    r["fails"] = fails
    r["pass"] = not fails
    return r


def render(rows: list[dict]) -> str:
    cols = ["run", "day_type", "calls", "minutes", "pairs", "delphi_calls", "premortem", "top_splits_resolvable",
            "judge_resolvable", "evidence_rate", "debate_evidence_rate", "agent_names", "split_missing", "moves",
            "argument_only_share", "status", "pass"]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join("—" if r.get(c) is None else str(r.get(c)) for c in cols) + " |")
    for r in rows:
        for f in r["fails"]:
            out.append(f"- {r['run']}: {f}")
    moved = [r for r in rows if r.get("moves")]
    if moved:
        tot = sum(r["moves"] for r in moved)
        arg = sum(round((r["argument_only_share"] or 0) * r["moves"]) for r in moved)
        out.append(f"- moved on argument alone: {arg} of {tot} moves across {len(moved)} run(s)")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Phase D replay pass bar")
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--home", default=os.environ.get("RECON_HOME") or str(REPO))
    a = ap.parse_args(argv)
    rows = [row(Path(a.home), r) for r in a.runs]
    print(render(rows))
    return 0 if all(r["pass"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
