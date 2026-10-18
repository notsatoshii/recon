#!/usr/bin/env python3
"""brier_report.py -- Brier scores over the resolved question ledger (docs/v2/phase-d-debate-upgrade.md, step 2).

Joins the ledger's question lines with their resolution lines (yes / no; void and open ones are left out) and
scores every probability as (p/100 - outcome)^2, lower is better:

  per agent      mean and median Brier of its round-0 take values and of its finals (post-debate)
  the lenses     Brier of the lenses' MEAN probability, round 0 vs post-debate (mean, not median: an outlier that
                 moves in rarely shifts the median)
  components     Phase D ledger lines carry stages {delphi: {agent: p}, xexam: {agent: p}}. Delphi is scored on
                 the questions it changed (take values -> after Delphi), the cross-exam on the questions it changed
                 (after Delphi -> finals), each as the mean ΔBrier of the lenses' mean with a paired bootstrap
                 P(ΔBrier > 0). The production gate turns a component off when it changed at least
                 GATE_MIN_CHANGED questions, its mean ΔBrier > 0 and P > GATE_P.
  weights        only from WEIGHTS_MIN resolved questions on: per agent (mean Brier of all agents / its own),
                 shrunk towards 1 by n / (n + SHRINK) and clipped to 0.5-2x; the weighted-mean Brier is published
                 beside the plain mean, never in place of it.

Writes <ledger dir>/calibration.json (the orchestrator reads components.<name>.on) and, with --md, a markdown
report. --scorecard appends an aggregate CALIBRATION section (no agent names) to a run's 00_scorecard.md.

    python3 scripts/brier_report.py --ledger config/questions/ledger.jsonl [--md out.md] [--scorecard FILE]
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

GATE_MIN_CHANGED = 20
GATE_P = 0.8
WEIGHTS_MIN = 40
SHRINK = 20
BOOT = 2000


def load(path: Path) -> list[dict]:
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


def brier(p: float, outcome: int) -> float:
    return (float(p) / 100.0 - outcome) ** 2


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def scored(lines: list[dict]) -> list[dict]:
    """[{ledger_id, day, kind, outcome (0/1), takes {agent: p}, delphi {agent: p}, finals {agent: p}}] for every
    question line with a yes/no resolution (the newest question line per ledger id)."""
    res = {}
    for l in lines:
        if l.get("type") == "resolution" and l.get("outcome") in ("yes", "no"):
            res[l.get("ledger_id")] = 1 if l["outcome"] == "yes" else 0
    qs = {}
    for l in lines:
        if l.get("type") == "question" and l.get("ledger_id") in res:
            qs[l["ledger_id"]] = l
    out = []
    for lid, l in qs.items():
        takes = {a: float(v) for a, v in (l.get("take_values") or {}).items() if isinstance(v, (int, float))}
        finals = {a: float(v) for a, v in (l.get("finals") or {}).items() if isinstance(v, (int, float))} or dict(takes)
        st = l.get("stages") or {}
        delphi = dict(takes)
        delphi.update({a: float(v) for a, v in (st.get("delphi") or {}).items() if isinstance(v, (int, float))})
        if not takes:
            continue
        out.append({"ledger_id": lid, "day": l.get("day", ""), "kind": (l.get("question") or {}).get("kind", ""),
                    "outcome": res[lid], "takes": takes, "delphi": delphi, "finals": finals,
                    "has_stages": bool(st.get("delphi") or st.get("xexam"))})
    return sorted(out, key=lambda x: (x["day"], x["ledger_id"]))


def bootstrap_p(deltas: list[float], n: int = BOOT, seed: int = 7) -> float | None:
    """P(mean ΔBrier > 0) over paired bootstrap resamples of the changed questions."""
    if not deltas:
        return None
    rnd = random.Random(seed)
    k = len(deltas)
    hits = sum(1 for _ in range(n) if sum(deltas[rnd.randrange(k)] for _ in range(k)) / k > 0)
    return round(hits / n, 3)


def component(rows: list[dict], before: str, after: str) -> dict:
    deltas = []
    for r in rows:
        b, a = r[before], r[after]
        if not r["has_stages"] or b == a:
            continue
        deltas.append(brier(mean(a.values()), r["outcome"]) - brier(mean(b.values()), r["outcome"]))
    m = mean(deltas)
    p = bootstrap_p(deltas)
    off = bool(len(deltas) >= GATE_MIN_CHANGED and m is not None and m > 0 and p is not None and p > GATE_P)
    return {"changed": len(deltas), "mean_delta": None if m is None else round(m, 4), "p_worse": p, "on": not off,
            "rule": f"off when changed >= {GATE_MIN_CHANGED}, mean ΔBrier > 0 and P(ΔBrier > 0) > {GATE_P}"}


def report(lines: list[dict]) -> dict:
    rows = scored(lines)
    res = [l for l in lines if l.get("type") == "resolution"]
    agents = sorted({a for r in rows for a in r["takes"]})
    per_agent = {}
    for a in agents:
        r0 = [brier(r["takes"][a], r["outcome"]) for r in rows if a in r["takes"]]
        post = [brier(r["finals"][a], r["outcome"]) for r in rows if a in r["finals"]]
        per_agent[a] = {"n": len(r0), "round0_mean": round(mean(r0), 4) if r0 else None,
                        "round0_median": round(statistics.median(r0), 4) if r0 else None,
                        "post_mean": round(mean(post), 4) if post else None,
                        "post_median": round(statistics.median(post), 4) if post else None}
    g0 = [brier(mean(r["takes"].values()), r["outcome"]) for r in rows]
    g1 = [brier(mean(r["finals"].values()), r["outcome"]) for r in rows]
    out = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "resolved": len(rows), "yes": sum(r["outcome"] for r in rows),
           "no": sum(1 for r in rows if not r["outcome"]),
           "void": sum(1 for l in res if l.get("outcome") == "void"),
           "by_method": {m: sum(1 for l in res if l.get("method") == m) for m in sorted({l.get("method") for l in res})},
           "group": {"round0_mean_brier": round(mean(g0), 4) if g0 else None,
                     "post_mean_brier": round(mean(g1), 4) if g1 else None,
                     "round0_median_brier": round(statistics.median(g0), 4) if g0 else None,
                     "post_median_brier": round(statistics.median(g1), 4) if g1 else None},
           "per_agent": per_agent,
           "components": {"delphi": component(rows, "takes", "delphi"), "xexam": component(rows, "delphi", "finals")},
           "weights": None}
    if len(rows) >= WEIGHTS_MIN and per_agent:
        allm = mean(v["post_mean"] for v in per_agent.values())
        w = {}
        for a, v in per_agent.items():
            raw = (allm / v["post_mean"]) if v["post_mean"] else 2.0
            shrunk = 1 + (v["n"] / (v["n"] + SHRINK)) * (raw - 1)
            w[a] = round(max(0.5, min(2.0, shrunk)), 3)
        wb = []
        for r in rows:
            num = sum(w.get(a, 1) * p for a, p in r["finals"].items())
            den = sum(w.get(a, 1) for a in r["finals"])
            wb.append(brier(num / den, r["outcome"]))
        out["weights"] = {"per_agent": w, "weighted_mean_brier": round(mean(wb), 4),
                          "plain_mean_brier": out["group"]["post_mean_brier"]}
    return out


def scorecard_lines(rep: dict) -> list[str]:
    """The aggregate CALIBRATION section for a run's scorecard: counts and the lenses' mean, no agent names."""
    if not rep["resolved"]:
        return ["## CALIBRATION (resolved questions of the day)",
                f"- No question of the day has resolved yet ({rep['void']} void). Brier scores start with the first "
                "resolution.", ""]
    g = rep["group"]
    out = ["## CALIBRATION (resolved questions of the day)",
           f"- {rep['resolved']} resolved ({rep['yes']} yes, {rep['no']} no), {rep['void']} void.",
           f"- Brier score of the lenses' mean probability (lower is better; 0.25 is a coin flip): "
           f"{g['round0_mean_brier']:.3f} before the debate, {g['post_mean_brier']:.3f} after."]
    return out + [""]


def insert_calibration(body: str, section: list[str]) -> str:
    """The section goes above the market snapshot, near the top: the scorecard the takes read is cut to its first
    6 KB (build_agent_package.py SCORECARD_CAP), so at the end it would be lost on a long scorecard."""
    lines = body.split("\n")
    at = next((i for i, l in enumerate(lines) if l.startswith("## ") and "Snapshot" in l), None)
    if at is None:
        return body.rstrip() + "\n\n" + "\n".join(section)
    return "\n".join(lines[:at] + section + lines[at:])


def render_md(rep: dict) -> str:
    g = rep["group"]
    out = [f"# Brier report ({rep['generated']})", "",
           f"Resolved yes/no: {rep['resolved']} ({rep['yes']} yes, {rep['no']} no); void {rep['void']}; "
           f"by method {rep['by_method']}", "",
           "| | round 0 | post-debate |", "|---|---|---|",
           f"| lenses' mean probability, mean Brier | {g['round0_mean_brier']} | {g['post_mean_brier']} |",
           f"| lenses' mean probability, median Brier | {g['round0_median_brier']} | {g['post_median_brier']} |", "",
           "| agent | n | round 0 mean | round 0 median | post mean | post median |", "|---|---|---|---|---|---|"]
    for a, v in rep["per_agent"].items():
        out.append(f"| {a} | {v['n']} | {v['round0_mean']} | {v['round0_median']} | {v['post_mean']} | {v['post_median']} |")
    out += ["", "Components (production gate):"]
    for k, c in rep["components"].items():
        out.append(f"- {k}: changed {c['changed']} questions, mean ΔBrier {c['mean_delta']}, P(worse) {c['p_worse']}, "
                   f"{'on' if c['on'] else 'OFF'} ({c['rule']})")
    if rep["weights"]:
        out += ["", f"Weights (>= {WEIGHTS_MIN} resolved): {rep['weights']['per_agent']}; weighted-mean Brier "
                f"{rep['weights']['weighted_mean_brier']} beside the plain mean {rep['weights']['plain_mean_brier']}"]
    else:
        out += ["", f"Weights: none until {WEIGHTS_MIN} questions have resolved."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Brier scores over the resolved question ledger")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--json", help="output JSON (default: calibration.json beside the ledger)")
    ap.add_argument("--md", help="also write the markdown report here")
    ap.add_argument("--scorecard", help="append the aggregate CALIBRATION section to this scorecard file")
    a = ap.parse_args(argv)
    led = Path(a.ledger)
    rep = report(load(led))
    out = Path(a.json) if a.json else led.parent / "calibration.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.md:
        Path(a.md).write_text(render_md(rep), encoding="utf-8")
    if a.scorecard:
        p = Path(a.scorecard)
        body = p.read_text(encoding="utf-8") if p.exists() else ""
        if "## CALIBRATION" not in body:
            p.write_text(insert_calibration(body, scorecard_lines(rep)), encoding="utf-8")
    g = rep["group"]
    print(f"  Brier: {rep['resolved']} resolved, {rep['void']} void; lenses' mean {g['round0_mean_brier']} round 0 -> "
          f"{g['post_mean_brier']} post; components " +
          ", ".join(f"{k} {'on' if c['on'] else 'OFF'} ({c['changed']} changed)" for k, c in rep["components"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
