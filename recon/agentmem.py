"""Agent memory and state, written by code from the typed outputs (replaces 18 FAST calls a run).

Memory (config/agent_memory/<agent>.md) keeps the format the bash pipeline and score_yesterday.py
read: a two-line header, "### Last updated", then Active Tracking / Predictions / Recurring Themes /
Lessons Learned / Archived. One current copy; dated blocks that older runs appended are dropped
(they live in the state file).

State (config/agent_state/<agent>_state.md, user_agent -> user_state.md) gets one dated entry per
day with POSITION / PREDICTIONS / CHANGED / WATCHING / QUESTIONS lines; a rerun on the same day
replaces that day's entry instead of adding a second one.
"""
from __future__ import annotations

import re
from pathlib import Path

SECTIONS = ["Active Tracking", "Predictions", "Recurring Themes", "Lessons Learned", "Archived"]
CAPS = {"Active Tracking": 14, "Predictions": 25, "Recurring Themes": 12, "Lessons Learned": 12, "Archived": 20}


def state_name(agent: str) -> str:
    return "user" if agent == "user_agent" else agent


def _one_line(s: str, n: int = 220) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def parse_memory(text: str) -> tuple[list[str], dict[str, list[str]]]:
    lines = text.splitlines()
    header = [l for l in lines[:2] if l.startswith("#") and not l.startswith("###")]
    secs: dict[str, list[str]] = {k: [] for k in SECTIONS}
    cur = None
    for l in lines:
        m = re.match(r"^###\s+(.+?)\s*$", l)
        if m:
            name = m.group(1)
            cur = next((k for k in SECTIONS if name.lower().startswith(k.lower())), None)
            continue
        if cur and l.strip().startswith("- "):
            secs[cur].append(l.strip())
    return header, secs


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for i in items:
        k = re.sub(r"\W+", " ", i.lower()).strip()
        if k and k not in seen:
            seen.add(k)
            out.append(i)
    return out


def update_memory(path: Path, agent: str, date: str, take: dict, response: dict | None,
                  moves: list[dict], questions: dict[str, str]) -> dict:
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    header, secs = parse_memory(old)
    if not header:
        header = [f"# {agent.upper().replace('_', ' ')} — Running Memory", "## Updated after each run. Accumulates over time."]
    tag = f"[{date}]"

    watching = [f"- {tag} {_one_line(w, 200)}" for w in (take.get("watching") or [])[:6] if w.strip()]
    keep = [l for l in secs["Active Tracking"] if not l.startswith(f"- {tag}")]
    secs["Active Tracking"] = _dedupe(watching + keep)[:CAPS["Active Tracking"]]

    preds = [l for l in secs["Predictions"] if not l.startswith(f"- {tag}")]
    p = take.get("prediction") or {}
    if (p.get("text") or "").strip():
        extra = "; ".join(x for x in (f"metric: {_one_line(p.get('metric', ''), 100)}" if p.get("metric") else "",
                                       f"resolves {p['resolves_on']}" if p.get("resolves_on") else "") if x)
        conf = p.get("probability")
        conf_s = f", confidence: {int(round(conf))}%" if isinstance(conf, (int, float)) else ""
        preds.append(f"- {tag} {_one_line(p['text'], 240)}{' (' + extra + ')' if extra else ''} [status: pending{conf_s}]")
    if len(preds) > CAPS["Predictions"]:
        resolved = [l for l in preds if "[status: pending" not in l]
        while len(preds) > CAPS["Predictions"] and resolved:
            r = resolved.pop(0)
            preds.remove(r)
            secs["Archived"].append(_one_line(r, 200))
        preds = preds[-CAPS["Predictions"]:]
    secs["Predictions"] = preds

    lessons = [l for l in secs["Lessons Learned"] if not l.startswith(f"- {tag}")]
    if response and response.get("verdict") in ("narrow", "concede"):
        lessons.append(f"- {tag} {response['verdict']}ed under challenge: {_one_line(response.get('text', ''), 220)}")
    for mv in moves:
        if abs(mv["delta"]) >= 10:
            q = _one_line(questions.get(mv["question_id"], mv["question_id"]), 90)
            lessons.append(f"- {tag} moved {mv['from']:.0f}→{mv['to']:.0f} on \"{q}\": {_one_line(mv.get('reason', ''), 120)}")
    secs["Lessons Learned"] = lessons[-CAPS["Lessons Learned"]:]
    secs["Archived"] = secs["Archived"][-CAPS["Archived"]:]
    secs["Recurring Themes"] = secs["Recurring Themes"][:CAPS["Recurring Themes"]]

    out = header[:2] + ["", f"### Last updated: {date}", ""]
    for k in SECTIONS:
        out.append(f"### {k}")
        out += secs[k] or ["- none"]
        out.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text("\n".join(out), encoding="utf-8")
    tmp.replace(path)
    return {"lines": len(out), "watching": len(watching), "predictions": len(secs["Predictions"])}


def update_state(path: Path, agent: str, date: str, take: dict, response: dict | None,
                 moves: list[dict], final: dict[str, float]) -> dict:
    old = path.read_text(encoding="utf-8") if path.exists() else f"# {agent} state\n"
    # drop any entry already written for this date (a rerun replaces it)
    old = re.sub(rf"\n*### {re.escape(date)}\n(?:(?!\n### ).)*", "", old, flags=re.S).rstrip() + "\n"
    p = take.get("prediction") or {}
    pred = _one_line(p.get("text", ""), 300) or "none"
    if p.get("text"):
        bits = [f"p={int(round(p['probability']))}%" if isinstance(p.get("probability"), (int, float)) else "",
                f"resolves {p['resolves_on']}" if p.get("resolves_on") else "",
                _one_line(p.get("metric", ""), 80)]
        bits = [b for b in bits if b]
        if bits:
            pred += " (" + "; ".join(bits) + ")"
    changed = "none"
    if response:
        changed = f"{response.get('verdict', 'hold')}: {_one_line(response.get('text', ''), 260)}"
    if moves:
        changed += " | moves: " + ", ".join(f"{m['question_id']} {m['from']:.0f}→{m['to']:.0f}" for m in moves)
    entry = [f"### {date}",
             f"- POSITION: {_one_line(take.get('summary', ''), 300)}",
             f"- PREDICTIONS: {pred}",
             f"- CHANGED: {changed}",
             f"- WATCHING: {_one_line('; '.join(take.get('watching') or []), 400) or 'none'}",
             f"- QUESTIONS: {' · '.join(f'[{q}] {v:.0f}%' for q, v in sorted(final.items())) or 'none'}"]
    text = old + "\n" + "\n".join(entry) + "\n"
    lines = text.splitlines()
    if len(lines) > 200:
        text = "\n".join(lines[:5] + ["", "### [older entries trimmed]", ""] + lines[-150:]) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return {"lines": len(text.splitlines())}
