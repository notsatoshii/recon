"""Phase C debate logic: pure functions, no I/O beyond what is passed in (docs/v2/phase-c-spec.md).

  gate_questions   §2.4 programmatic question gate (no LLM)
  lens_extras      §3 item 3 YOUR LENS DATA per agent (LENS_RAW; same rules as tests/lens_extras_probe.py)
  pair             §4 pairing by widest probability gap; day types debate / split_unpaired / consensus
  pick_red_team    §4.3 the consensus-day red-team agent
  budget_pairs     §1.1 pairs and crux check that fit the call budget
  excerpts         §5.2 the lines around each verified quote, 4 KB shared between the sides
  challenge_checks §5.4 length, persona leakage, agreement opener
  crux_terms       §6 numbers, entities and metric words from the cruxes
  crux_search      §6 lines on disk about the crux that neither side quoted
  norm_p           §7.2 fractions rescaled, clamped to 0-100
  gate_move        §7.2 the evidence gate on a response's move (FREE_MOVE points on argument alone)
  score_debate     §9.1 per-debate scores (in_split, live_split, held_split, useful, closure_without_evidence, effect)
  question_stats   §10 per-question stats; final_positions: gated value if debated, else the take
  agent_run_score  §9.2 per agent per run
  split_sheet      §11.1-11.2 blocks for WHERE THE VIEWS SPLIT; render_split_sheet (<= 6 KB)
  lens_notes       §11.3 what each lens found, labelled by lens, verified data only (<= 8 KB)
  anonymise        §11.2 agent names -> "the other view"
  brief_checks     §11.5 agent names, count mismatches, block count, process words
  legacy_moves / legacy_response   §14.1 adapters for Phase B readers (agentmem, run.json)
"""
from __future__ import annotations

import math
import os
import re
import statistics
from collections import Counter
from itertools import combinations
from pathlib import Path

from recon import evidence

AGENTS = ["trader", "narrator", "builder", "analyst", "skeptic", "policy_analyst",
          "user_agent", "macro_strategist", "ai_engineer"]

GAP_MIN_DEFAULT = 20          # measured by the spread probe (§15.0: max(20, 2 x median retest |dp| 2-3) = 20, 2026-10-04); env RECON_PAIR_GAP
FREE_MOVE = 5                 # points an agent may move on argument alone (§7.2)
CRUX_GAP = int(os.environ.get("RECON_CRUX_GAP", "15"))
QUESTIONS_BY_DEPTH = {"quiet": 3, "normal": 4, "risk": 5}
PAIRS_BY_DEPTH = {"quiet": 1, "normal": 3, "risk": 3}
REQUIRED_DOMAINS = ("markets_crypto", "macro_policy", "ai_product")
DOMAIN_LENSES = {"markets_crypto": ["trader", "analyst"], "macro_policy": ["macro_strategist", "policy_analyst"],
                 "ai_product": ["ai_engineer", "builder"], "korea": ["policy_analyst", "user_agent"],
                 "prediction_markets": ["trader", "skeptic"]}
LENS_LABEL = {"trader": "markets lens", "narrator": "narrative lens", "builder": "products lens",
              "analyst": "sector-model lens", "skeptic": "risk lens", "policy_analyst": "policy lens",
              "user_agent": "adoption lens", "macro_strategist": "macro lens", "ai_engineer": "AI tools lens"}
# Per-agent tier override (§15.0). Spread probe 2026-10-04: on the SYNTH model at the analyst tier's medium
# effort (p3) the skeptic's and the macro strategist's retest-adjusted distance from the median rose on 3/4
# and 4/4 questions (high effort alone: 2/4 each), so those two lenses take on the `lens` tier: the SYNTH
# model at medium effort, the setting measured (recon/llm.py; not sol at high effort, which no probe measured).
LENS_TIER: dict[str, str] = {"skeptic": "lens", "macro_strategist": "lens"}
MARKET_SECTION = "PREDICTION MARKETS"   # SECTION 8 and the raw Polymarket / Kalshi blocks (evidence.RAW_BLOCKS)
SPLIT_SHEET_CAP = 6000
LENS_NOTES_CAP = 8000

# ── lens extras (§3 item 3 = phase-e §4.5b) ─────────────────────────────────────────────
ENTRY_CAP = 3000
AGENT_CAP = 6000
LENS_WARN_BELOW = 2000
NEWS_BLOCKS = ("# News Intelligence", "# Twitter/X Intelligence")
LENS_RAW: dict[str, list[str]] = {
    "macro_strategist": [
        "# World Monitor Intelligence", "# Kalshi Intelligence",
        r"news~\bFed\b|FOMC|\bCPI\b|inflation|payroll|jobs report|tariff|treasur|\byields?\b|\bdollar"
        r"|\bDXY\b|recession|\bGDP\b|rate cut|rate hike|\bECB\b|\bBOJ\b|Powell|\boil\b|sanction|shutdown"
        r"|election|midterm|geopolit|China|Iran|Russia|Ukraine|Israel",
        "## ECONOMICS", "## POLITICS",
    ],
    "trader": [
        "# Polymarket Intelligence", "# Kalshi Intelligence",
        r"news~liquidat|funding rate|open interest|short squeeze|whale|leverag|\boptions\b|\bperps?\b"
        r"|ETF.{0,12}(in|out)flow|\bsupport\b|\bresistance\b|\bshorts?\b|\blongs?\b",
    ],
    "analyst": [
        "## CRYPTO / WEB3 ROUNDS", "## AI ROUNDS", "## CROSS-SOURCE SIGNALS",
        r"news~\bTVL\b|stablecoin|market share|revenue|earnings|valuation|inflows?\b|outflows?\b|\bETFs?\b",
    ],
    "skeptic": [
        r"news~\bhack|exploit|depeg|breach|drain|\brug|scam|fraud|lawsuit|outage|insolven|bankrupt"
        r"|delist|vulnerab",
        "## 5. Controversy & Risk Flags", "## 2. Narrative Analysis", "## 3. Divergences",
    ],
    "policy_analyst": [
        "## KOREA — CRYPTO & MARKETS",
        r"news~regulat|\bSEC\b|\bCFTC\b|\bESMA\b|CLARITY|\bMiCA\b|lawmaker|Congress|\bsenat|금융위|금감원",
        "# ZDNet Korea Intelligence",
    ],
    "ai_engineer": [
        "## AI NEWSLETTER SOURCES",
        r"news~OpenAI|Anthropic|Claude|Gemini|\bGPT|\bLLM|Llama|DeepSeek|Qwen|Mistral|inference"
        r"|benchmark|\bGPU|Nvidia",
        "# Changelogs Intelligence", "## GITHUB TRENDING",
    ],
    "builder": [
        "## AI & TECH NEWS",
        r"news~launch|\bships?\b|shipped|\breleas|open.?source|\bSDK|\bAPI\b|mainnet|testnet|upgrade"
        r"|github|developer|\bprotocol",
        "# Changelogs Intelligence", "## GITHUB TRENDING", "## HACKER NEWS",
    ],
    "user_agent": ["## KOREA — AI", "## AI EDUCATION & WORKFORCE", "# ZDNet Korea Intelligence",
                   "# Reddit Intelligence"],
    "narrator": ["# Reddit Intelligence", "# Twitter/X Intelligence"],
}
_TWEET = re.compile(r"^- (?:@\S+ )?\[[^\]]*\] \([^)]*\) (?P<text>.+)$")
_URL = re.compile(r"https?://\S+")


def nbytes(s: str) -> int:
    return len(s.encode("utf-8"))


def _level(line: str) -> int:
    return len(line) - len(line.lstrip("#"))


def _block(text: str, heading: str) -> list[str] | None:
    """First block whose heading line starts with `heading` ('# ' up to the next '# ' line, '## ' up
    to the next '# ' or '## ' line), heading included."""
    lines = text.split("\n")
    lvl = _level(heading)
    for n, line in enumerate(lines):
        if line.startswith(heading):
            j = n + 1
            while j < len(lines) and not (lines[j].startswith("# ") or (lvl == 2 and lines[j].startswith("## "))):
                j += 1
            return lines[n:j]
    return None


class _View:
    """What the shared block already carries: a line is in the view when its stripped text is a view
    line, its tweet text (first 120 characters) is in the view, or its last URL (> 24 chars) is."""

    def __init__(self, text: str):
        self.text = text
        self.lines = {l.strip() for l in text.split("\n")}
        self.urls = {u.rstrip(").,]") for u in _URL.findall(text)}

    def has(self, line: str) -> bool:
        s = line.strip()
        if s in self.lines:
            return True
        m = _TWEET.match(s)
        if m and m.group("text")[:120] in self.text:
            return True
        urls = [u.rstrip(").,]") for u in _URL.findall(s)]
        return bool(urls) and len(urls[-1]) > 24 and urls[-1] in self.urls


def _entry_lines(entry: str, raw: str, pkg: str) -> list[str] | None:
    if entry.startswith("news~"):
        rx = re.compile(entry[5:], re.I)
        return [line for h in NEWS_BLOCKS for line in _block(raw, h) or []
                if line.strip().startswith("- ") and rx.search(line)]
    return _block(raw, entry) or _block(pkg, entry)


def _extras_for(lines: list[str], view: _View, added: set[str], budget: int) -> list[str]:
    out, used, stack, item_added = [], 0, [], False
    for line in lines:
        s = line.strip()
        if not s or s == "---" or s.startswith("<!--"):
            continue
        if s.startswith("#"):
            lv = _level(s)
            stack = [h for h in stack if h[0] < lv] + [[lv, line.rstrip(), False]]
            item_added = False
            continue
        continuation = line.startswith(" ") and not s.startswith("- ")
        if continuation and not item_added:
            continue
        if view.has(line) or s in added:
            item_added = item_added and continuation
            continue
        heads = [h for h in stack if not h[2]]
        add = [h[1] for h in heads] + [line.rstrip()]
        size = sum(nbytes(a) + 1 for a in add)
        if used + size > budget:
            break
        for h in heads:
            h[2] = True
        out.extend(add)
        added.add(s)
        used += size
        item_added = True
    return out


def _lens_extras_one(raw: str, pkg: str, view: _View, entries: list[str], taken: set[str]) -> tuple[str, list[dict]]:
    parts, report = [], []
    for e in entries:
        lines = _entry_lines(e, raw, pkg)
        if lines is None:
            report.append({"entry": e, "bytes": 0, "found": False})
            continue
        label = [f"## News and X lines matching /{e[5:]}/"] if e.startswith("news~") else []
        used = nbytes("\n\n".join(parts)) + (2 if parts else 0) + sum(nbytes(l) + 1 for l in label)
        got = _extras_for(lines, view, taken, min(ENTRY_CAP, AGENT_CAP - used))
        text = "\n".join(label + got) if got else ""
        report.append({"entry": e, "bytes": nbytes(text), "found": True})
        if got:
            parts.append(text)
    return "\n\n".join(parts), report


def lens_extras(raw: str, pkg: str, view_text: str) -> dict[str, dict]:
    """{agent: {text, bytes, entries}} for all nine agents, computed in table order (a line goes to the
    first agent that picks it), whoever is active, so a lens's block never depends on the roster."""
    view, taken, out = _View(view_text), set(), {}
    for agent, entries in LENS_RAW.items():
        text, rep = _lens_extras_one(raw or "", pkg or "", view, entries, taken)
        out[agent] = {"text": text, "bytes": nbytes(text), "entries": rep}
    return out


# ── small helpers ──────────────────────────────────────────────────────────────────────

def clamp_int(v, lo: int = 0, hi: int = 100) -> int:
    return int(max(lo, min(hi, round(float(v)))))


def norm_p(value, ref_values) -> tuple[int | None, list[str]]:
    """(integer 0-100, flags), or (None, ['no probability']) for a missing or non-numeric value.
    Only a non-integer value strictly between 0 and 1, when the reference values (the agent's
    normalised integer take values) go above 1, is a fraction (0.65 for 65 %): it is multiplied by 100
    and flagged 'fraction rescaled'. An honest '1' stays 1. 'clamped' when clamping changed it."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None, ["no probability"]
    if isinstance(value, bool) or math.isnan(v) or math.isinf(v):
        return None, ["no probability"]
    flags: list[str] = []
    if isinstance(ref_values, dict):          # take_values() returns {qid: int}; iterate the values, not the keys
        ref_values = list(ref_values.values())
    refs = [float(r) for r in (ref_values or []) if isinstance(r, (int, float)) and not isinstance(r, bool)]
    if 0 < v < 1 and refs and max(refs) > 1:
        v *= 100
        flags.append("fraction rescaled")
    r = round(v)
    c = max(0, min(100, r))
    if c != r:
        flags.append("clamped")
    return int(c), flags


def take_values(take: dict) -> dict[str, int]:
    """{qid: integer 0-100} from a TAKE's positions. Fractions are rescaled: when every value is <= 1
    and one is strictly between 0 and 1, all are (0.65 -> 65, 1 -> 100); when the take mixes scales
    (0.65 next to 70), only the values strictly between 0 and 1 are (0.65 -> 65; an honest 1 stays 1)."""
    raw = {}
    for p in (take or {}).get("positions") or []:
        try:
            v = float(p.get("probability"))
        except (TypeError, ValueError):
            continue
        if math.isnan(v) or math.isinf(v):
            continue
        raw[p.get("question_id", "")] = v
    if raw and any(0 < v < 1 for v in raw.values()):
        if all(v <= 1.0 for v in raw.values()):
            raw = {q: v * 100 for q, v in raw.items()}
        else:
            raw = {q: (v * 100 if 0 < v < 1 else v) for q, v in raw.items()}
    return {q: clamp_int(v) for q, v in raw.items() if q}


def words(text: str) -> int:
    return len((text or "").split())


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s]


def qnorm(quote: str) -> str:
    return evidence.norm(quote or "").strip(" .\"'")


def jaccard(a: str, b: str) -> float:
    wa = set(re.findall(r"\w+", (a or "").lower()))
    wb = set(re.findall(r"\w+", (b or "").lower()))
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


# ── §2.4 the question gate ─────────────────────────────────────────────────────────────

_WH = re.compile(r"^\s*(how|what|which|why|when|where|who|어떻게|무엇|왜|언제|누가)\b", re.I)
_DATE = re.compile(r"^\d{4}-\d\d-\d\d$")


def _days_between(a: str, b: str) -> int | None:
    from datetime import date
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except (TypeError, ValueError):
        return None


def clamp_weight(w) -> tuple[int, bool]:
    try:
        v = float(w)
    except (TypeError, ValueError):
        return 1, True
    c = int(round(min(3, max(1, v))))
    return c, c != v


def gate_questions(questions: list[dict], day: str, locator, open_ledger: list[dict] | None = None,
                   active: list[str] | None = None, depth: str = "normal", market: list[str] | None = None) -> dict:
    """Apply the §2.4 gate. Returns {kept, dropped, notes, needs_reask}. `locator` is an
    evidence.Locator over package, raw, view and social (rule 4). Kept questions get ids q1… in
    order, clamped weights and cleaned lenses. `market`: the run's prediction-market odds lines
    (market_lines()); a question one of them already prices is dropped (rule 7: the spread probe's
    takes anchored on a quoted market price, 9 of 9 at 60 %)."""
    active = list(active or AGENTS)
    open_ledger = open_ledger or []
    dropped, notes = [], []
    cands = []
    for n, q0 in enumerate(questions or []):
        q = dict(q0)
        text = (q.get("text") or "").strip()
        q["text"] = text
        w, changed = clamp_weight(q.get("weight", 1))
        if changed:
            notes.append(f"weight clamped: {q.get('weight')!r} -> {w} on {text[:60]!r}")
        q["weight"] = w
        kind = q.get("kind") or "event"
        q["kind"] = kind
        reason = None
        if not text or len(text) > 220 or not text.endswith("?") or _WH.match(text):
            reason = "not a yes/no question of at most 220 characters ending with ?"
        elif kind != "judgment":
            d = _days_between(day, q.get("resolves_on") or "")
            if d is None or not (1 <= d <= 30):
                reason = f"resolves_on {q.get('resolves_on')!r} is not 1-30 days after {day}"
        if reason is None and kind in ("threshold", "direction"):
            bq = (q.get("baseline_quote") or "").strip()
            st = evidence.locate(bq, locator)["status"] if bq else "empty"
            if st not in ("verified", "partial"):
                reason = f"baseline_quote not found in the package ({st})"
        if reason is None and market:
            hit = market_match(text, market)
            if hit:
                reason = f"a prediction market already prices it: {hit[:100]!r}"
        if reason:
            dropped.append({"text": text, "reason": reason, "order": n})
            continue
        q["_order"] = n
        cands.append(q)
    # rule 5: silent repeats (sequential, against questions kept earlier today and the open ledger)
    kept5 = []
    for q in cands:
        rep = next((k for k in kept5 if jaccard(k["text"], q["text"]) >= 0.6), None)
        if rep is not None:
            dropped.append({"text": q["text"], "reason": "repeats a question kept earlier today", "order": q["_order"]})
            continue
        if not (q.get("carried_from") or "").strip():
            old = next((o for o in open_ledger if jaccard((o.get("question") or {}).get("text", ""), q["text"]) >= 0.6), None)
            if old is not None:
                dropped.append({"text": q["text"], "reason": f"silent repeat of open question {old.get('ledger_id')}",
                                "order": q["_order"]})
                continue
        kept5.append(q)
    # rule 3: one judgment question (the higher weight); rule 6: at most 2 carried (the higher weights)
    def keep_top(items, pred, k, why):
        sel = [q for q in items if pred(q)]
        best = sorted(sel, key=lambda q: (-q["weight"], q["_order"]))[:k]
        out = []
        for q in items:
            if pred(q) and q not in best:
                dropped.append({"text": q["text"], "reason": why, "order": q["_order"]})
            else:
                out.append(q)
        return out
    kept = keep_top(kept5, lambda q: q["kind"] == "judgment", 1, "a second judgment question")
    kept = keep_top(kept, lambda q: bool((q.get("carried_from") or "").strip()), 2, "more than 2 carried questions")
    # the day's count: by weight, then domain coverage, then order
    n_keep = QUESTIONS_BY_DEPTH.get(depth, 4)
    pool, chosen, covered = list(kept), [], set()
    while pool and len(chosen) < n_keep:
        pool.sort(key=lambda q: (-q["weight"], 0 if (q.get("domain") in REQUIRED_DOMAINS and q.get("domain") not in covered) else 1,
                                 q["_order"]))
        q = pool.pop(0)
        chosen.append(q)
        covered.add(q.get("domain"))
    for q in pool:
        dropped.append({"text": q["text"], "reason": f"over the day's count ({n_keep} for {depth})", "order": q["_order"]})
    chosen.sort(key=lambda q: q["_order"])
    out = []
    for k, q in enumerate(chosen, 1):
        q = {key: v for key, v in q.items() if key != "_order"}
        q["id"] = f"q{k}"
        lenses = [a for a in dict.fromkeys(q.get("lenses") or []) if a in active]
        if len(lenses) < 2:
            for a in DOMAIN_LENSES.get(q.get("domain"), []):
                if a in active and a not in lenses:
                    lenses.append(a)
        q["lenses"] = lenses[:5]
        for key in ("metric", "comparator", "threshold", "baseline_quote", "resolves_on", "settles_with", "carried_from",
                    "domain"):
            q.setdefault(key, "")
        out.append(q)
    dropped.sort(key=lambda d: d["order"])
    return {"kept": out, "dropped": [{k: v for k, v in d.items() if k != "order"} for d in dropped],
            "notes": notes, "needs_reask": len(out) <= 1}


# ── §1.1 budget ────────────────────────────────────────────────────────────────────────

def budget_pairs(used: int, budget: int, synth_calls: int, depth_target: int) -> tuple[int, bool]:
    """(pairs, crux_check) that fit: a pair costs 4 calls; the crux check is dropped first, then pairs."""
    free = budget - used - synth_calls
    with_crux, without_crux = max(0, (free - 1) // 4), max(0, free // 4)
    if with_crux >= depth_target:
        return depth_target, True
    if without_crux > with_crux:
        return min(depth_target, without_crux), False
    return with_crux, free - 4 * with_crux >= 1


# ── §4 pairing ─────────────────────────────────────────────────────────────────────────

def _ok(e: dict) -> bool:
    return e.get("status") in ("verified", "partial")


def eligible_agents(q: dict, p: dict, evq: dict, active: list[str]) -> list[str]:
    out = []
    for a in active:
        if q["id"] not in p.get(a, {}):
            continue
        ok = [e for e in evq.get(a, {}).get(q["id"], []) if _ok(e)]
        if not ok:
            continue
        if q.get("kind") != "judgment" and not any(e.get("cls") == "data" for e in ok):
            continue
        out.append(a)
    return sorted(out)


def median(vals):
    return statistics.median(vals) if vals else None


def pick_red_team(q: dict, p: dict, evq: dict, active: list[str]) -> dict | None:
    vals = [p[a][q["id"]] for a in active if q["id"] in p.get(a, {})]
    if not vals:
        return None
    med = statistics.median(vals)
    el = eligible_agents(q, p, evq, active)
    if el:
        far = sorted(el, key=lambda a: (-abs(p[a][q["id"]] - med), a))[0]
        dist = abs(p[far][q["id"]] - med)
        if dist >= 10:
            return {"agent": far, "question_id": q["id"], "median": float(med), "distance": float(dist),
                    "reason": f"furthest eligible agent from the median ({dist:.0f} points)"}
    if "skeptic" in active and q["id"] in p.get("skeptic", {}):
        return {"agent": "skeptic", "question_id": q["id"], "median": float(med),
                "distance": float(abs(p["skeptic"][q["id"]] - med)), "reason": "no eligible agent 10+ points from the median"}
    def data_ev(a):
        return sum(1 for e in evq.get(a, {}).get(q["id"], []) if _ok(e) and e.get("cls") == "data")
    cand = sorted([a for a in active if q["id"] in p.get(a, {})], key=lambda a: (-data_ev(a), a))
    if not cand:
        return None
    a = cand[0]
    return {"agent": a, "question_id": q["id"], "median": float(med), "distance": float(abs(p[a][q["id"]] - med)),
            "reason": "most verified data evidence on the question"}


def pair(questions: list[dict], p: dict, evq: dict, active: list[str], depth: str, target: int,
         gap_min: int = GAP_MIN_DEFAULT, yesterday_pairs: set | None = None, off_reason: str | None = None) -> dict:
    """§4.2-4.3. p: {agent: {qid: int}}; evq: {agent: {qid: [EV_CHECKED]}}. Returns the PAIRING
    fields except positions_evidence and budget. `off_reason`: the debate is switched off (the §0.1 spread
    gate has not passed): no pairs and no red team; a split is split_unpaired (undebated blocks, §11.1)
    with that reason, anything else a consensus day without the red-team call."""
    yesterday_pairs = yesterday_pairs or set()
    active = sorted(a for a in active if a in p)
    elig = {q: [] for q in ("q1", "q2", "q3", "q4", "q5")}
    if not questions:
        return {"day_type": "no_questions", "depth": depth, "target": target, "gap_min": gap_min, "pairs": [],
                "candidates_considered": 0, "unpaired": [], "red_team": None, "eligible": elig}

    def quotes(a, qid):
        return {qnorm(e.get("quote")) for e in evq.get(a, {}).get(qid, []) if _ok(e)}

    def vcount(a, qid):
        return sum(1 for e in evq.get(a, {}).get(qid, []) if _ok(e))

    cands, ranges = [], {}
    for q in questions:
        qid = q["id"]
        vals = [p[a][qid] for a in active if qid in p[a]]
        if len(vals) < 3:
            continue
        med = statistics.median(vals)
        ranges[qid] = max(vals) - min(vals)
        w = int(round(min(3, max(1, float(q.get("weight", 1) or 1)))))
        el = eligible_agents(q, p, evq, active)
        if qid in elig:
            elig[qid] = el
        for a, b in combinations(el, 2):
            lo, hi = sorted((a, b), key=lambda x: (p[x][qid], x))
            gap = p[hi][qid] - p[lo][qid]
            if gap < gap_min:
                continue
            if not (p[lo][qid] <= med <= p[hi][qid]):
                continue
            score = float(gap * w)
            both = a in (q.get("lenses") or []) and b in (q.get("lenses") or [])
            if both:
                score *= 1.15
            rep = frozenset((a, b)) in yesterday_pairs
            if rep:
                score -= 10
            qa, qb = quotes(a, qid), quotes(b, qid)
            if qa and qa == qb:
                score -= 5
            cands.append((round(score, 2), gap, vcount(a, qid) + vcount(b, qid), qid, lo, hi, both, rep))
    cands.sort(key=lambda c: (-c[0], -c[1], -c[2], c[3], c[4], c[5]))
    if off_reason:
        target = 0
    pairs, used_q, load = [], set(), Counter()
    for cap in (1, 2):
        for c in cands:
            if len(pairs) >= target:
                break
            if c[3] in used_q or load[c[4]] >= cap or load[c[5]] >= cap:
                continue
            pairs.append(c)
            used_q.add(c[3])
            load[c[4]] += 1
            load[c[5]] += 1
    out_pairs = [{"question_id": c[3], "high": c[5], "low": c[4], "p_high": p[c[5]][c[3]], "p_low": p[c[4]][c[3]],
                  "gap": c[1], "score": c[0], "both_lenses": c[6], "repeat_of_yesterday": c[7]} for c in pairs]
    base = {"depth": depth, "target": target, "gap_min": gap_min, "candidates_considered": len(cands),
            "eligible": elig}
    if out_pairs:
        return {"day_type": "debate", "pairs": out_pairs, "unpaired": [], "red_team": None, **base}
    wide = sorted(((qid, r) for qid, r in ranges.items() if r >= gap_min), key=lambda x: (-x[1], x[0]))
    if off_reason:
        if wide:
            return {"day_type": "split_unpaired", "pairs": [], "red_team": None,
                    "unpaired": [{"question_id": qid, "range": int(r), "reason": off_reason} for qid, r in wide[:3]],
                    **base}
        return {"day_type": "consensus", "pairs": [], "unpaired": [], "red_team": None, **base}
    if cands or wide:
        unp = []
        for qid, r in wide[:3]:
            why = ("the call budget leaves no pair" if cands else
                   "no eligible pair straddles the median with the gap (evidence missing or social-only)")
            unp.append({"question_id": qid, "range": int(r), "reason": why})
        return {"day_type": "split_unpaired", "pairs": [], "unpaired": unp, "red_team": None, **base}
    qs = [q for q in questions if q["id"] in ranges] or list(questions)
    top = sorted(qs, key=lambda q: (-int(q.get("weight", 1) or 1), -ranges.get(q["id"], 0), q["id"]))[0]
    rt = pick_red_team(top, p, evq, active)
    return {"day_type": "consensus", "pairs": [], "unpaired": [], "red_team": rt, **base}


# ── §5.2 excerpts ──────────────────────────────────────────────────────────────────────

def excerpts(sides: list[list[dict]], locator, cap: int = 4096, k: int = 3) -> str:
    """The quote's line ± k lines in the document where locate() found it (a partial match uses its
    anchor line), merged when they overlap, `cap` bytes shared equally between the sides. Unverified
    quotes are listed as '(not found in the package)'. Each side is a list of {section, quote}."""
    share = cap // max(1, len(sides))
    seen_windows: list[tuple[str, int, int]] = []
    out, missing = [], []
    for side in sides:
        used, wins = 0, []
        for e in side:
            q = (e.get("quote") or "").strip()
            if not q:
                continue
            loc = locator.locate(q)
            if loc["status"] not in ("verified", "partial") or not loc.get("doc"):
                missing.append(q[:200])
                continue
            n = len(locator.lines[loc["doc"]])
            lo, hi = max(1, loc["line"] - k), min(n, loc["line"] + k)
            merged = False
            for w in wins:
                if w[0] == loc["doc"] and lo <= w[2] + 1 and hi >= w[1] - 1:
                    w[1], w[2] = min(w[1], lo), max(w[2], hi)
                    merged = True
                    break
            if not merged:
                wins.append([loc["doc"], lo, hi, loc["section"]])
        for doc, lo, hi, sec in wins:
            if any(d == doc and lo >= a and hi <= b for d, a, b in seen_windows):
                continue
            head = f"[{sec or doc} · {doc} lines {lo}-{hi}]"
            body = [locator.lines[doc][i - 1][:400] for i in range(lo, hi + 1) if locator.lines[doc][i - 1].strip()]
            chunk = [head]
            for line in body:
                if used + nbytes("\n".join(chunk + [line])) > share:
                    break
                chunk.append(line)
            if len(chunk) > 1:
                out.append("\n".join(chunk))
                used += nbytes("\n".join(chunk)) + 2
                seen_windows.append((doc, lo, hi))
            if used >= share:
                break
    for q in dict.fromkeys(missing):
        out.append(f"\"{q}\" (not found in the package)")
    return "\n\n".join(out) or "(no excerpts: neither side's evidence was found in the package)"


# ── §5.4 challenge checks and §11.2 anonymiser ─────────────────────────────────────────

LEAKAGE = re.compile(r"ROADMAP|BUILD NOW|content angle|Post now|allocation:|portfolio:|^#+ ", re.M)
AGREE = re.compile(r"\b(correctly|is right|rightly|sound|agree|aligned|fair point)\b", re.I)


def _name_forms(agent: str) -> list[str]:
    """Regex forms that name an agent: multi-word names in any case, joined by a space or an underscore
    ('policy_analyst', 'the policy analyst', 'AI engineer', 'User Agent', 'MACRO STRATEGIST'); single
    words in Title Case ('Trader') and UPPER ('TRADER'). Lower-case single words ('a trader would sell',
    'analyst consensus') are common nouns here; anonymise() catches 'the skeptic argues' separately."""
    w = agent.split("_")
    if len(w) > 1:
        return ["(?i:" + r"[ _]".join(re.escape(x) for x in w) + ")"]
    return [re.escape(agent.capitalize()), re.escape(agent.upper())]


_NAMES_ALT = "|".join(f for a in sorted(AGENTS, key=len, reverse=True) for f in _name_forms(a))
_NAME_RX = re.compile(r"(?:(?P<at>@)(?i:" + "|".join(re.escape(a) for a in sorted(AGENTS, key=len, reverse=True))
                      + r")|(?P<the>\b[Tt]he\s+)?\b(?:" + _NAMES_ALT + r"))(?!s\b)(?!\.ai\b)(?!\w)")
# A lower-case role noun used as a name in debate-written text: 'the skeptic argues', 'the trader's
# read', 'the analyst is right'. Followed by a possessive, a common verb, or a word ending in a single s
# (argues, overstates; not 'consensus', 'class', 'thesis', 'basis').
_ROLE_VERBS = (r"is|was|has|had|would|will|can|could|should|might|may|must|does|did|says|said|argues|argued|"
               r"thinks|thought|believes|claims|claimed|expects|expected|sees|saw|reads|read|notes|noted|"
               r"overstates|understates|misses|missed|ignores|ignored|concedes|conceded|holds|held|puts|put|"
               r"assumes|assumed|treats|treated|wants|insists|underweights|overweights|relies|leans|cites|cited")
_ROLE_RX = re.compile(r"\b(?P<the>[Tt]he)\s+(?:skeptic|trader|narrator|builder|analyst)"
                      r"(?:(?=['’]s\b)['’]s|(?=\s+(?:" + _ROLE_VERBS + r")\b)|(?=\s+[a-z]+[^siu\s]s\b))")


def leakage_flags(text: str) -> bool:
    return bool(LEAKAGE.search(text or ""))


def anonymise(text: str) -> str:
    """Agent names -> 'the other view': multi-word names in any case ('policy_analyst', 'the macro
    strategist', 'AI engineer'), single names in Title Case and UPPER, with or without 'the', '@agent',
    and a lower-case role noun used as a name ('the skeptic argues', 'the trader's read'). Lower-case
    role nouns used as nouns ('a trader would sell', 'analyst consensus') stay."""
    def sub(m):
        the = m.group("the") or ""
        return "The other view" if the.startswith("T") else "the other view"

    def sub_role(m):
        poss = m.group(0)[-2:] if re.search(r"['’]s$", m.group(0)) else ""
        return ("The other view" if m.group("the").startswith("T") else "the other view") + poss
    t = _NAME_RX.sub(sub, text or "")
    t = _ROLE_RX.sub(sub_role, t)
    t = re.sub(r"(^|[.!?]\s+)the other view", lambda m: m.group(1) + "The other view", t)
    return t


def clean_text(text: str, limit: int) -> str:
    """anonymise, drop leakage lines, cut at a sentence boundary to fit `limit` bytes."""
    lines = [l for l in anonymise(text).split("\n") if not LEAKAGE.search(l)]
    t = re.sub(r"\s+", " ", " ".join(lines)).strip()
    if nbytes(t) <= limit:
        return t
    out = ""
    for s in sentences(t):
        cand = (out + " " + s).strip()
        if nbytes(cand) > limit:
            break
        out = cand
    if not out:
        out = t.encode("utf-8")[:max(0, limit - 3)].decode("utf-8", "ignore").rsplit(" ", 1)[0] + "…"
    return out


def challenge_checks(data: dict) -> list[str]:
    flags = []
    if words(data.get("rebuttal") or data.get("case")) > 140 or len(sentences(data.get("steelman", ""))) > 3:
        flags.append("over length")
    texts = [data.get(k, "") for k in ("steelman", "rebuttal", "case", "consensus_view")]
    texts += [str(v) for v in (data.get("crux") or {}).values()]
    texts += [str(v) for v in (data.get("would_change_my_mind") or {}).values()]
    if any(leakage_flags(t) for t in texts):
        flags.append("persona leakage")
    first = " ".join((data.get("rebuttal") or data.get("case") or "").split()[:12])
    if AGREE.search(first):
        flags.append("opens by agreeing")
    return flags


def anonymise_record(data: dict) -> dict:
    """A copy of a challenge or red-team reply with every string anonymised (for the responder and
    the split sheet; the raw reply is kept as written)."""
    if isinstance(data, dict):
        return {k: anonymise_record(v) for k, v in data.items()}
    if isinstance(data, list):
        return [anonymise_record(v) for v in data]
    if isinstance(data, str):
        return anonymise(data)
    return data


# ── §6 crux search ─────────────────────────────────────────────────────────────────────

METRIC_WORDS = ["tvl", "volume", "liquidity", "price", "yield", "apy", "funding", "open interest", "inflow",
                "outflow", "spread", "depth", "market cap", "dominance", "supply", "peg", "거래대금", "시가총액"]
STOP = {"the", "this", "that", "these", "those", "if", "will", "what", "when", "where", "which", "who", "why",
        "how", "it", "its", "a", "an", "and", "but", "or", "in", "on", "at", "by", "for", "of", "to", "from",
        "with", "is", "are", "was", "were", "be", "yes", "no", "not", "today", "tomorrow", "yesterday", "per",
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
        "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun",
        "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
        "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
_DATE_TOKEN = re.compile(r"^(\d{4}-\d\d-\d\d|\d{1,2}/\d{1,2}(/\d{2,4})?|Q[1-4]|[1-4]Q\d{2}|H[12]|20\d\d|UTC|KST|EDT|EST|GMT)$", re.I)
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9&.\-/]*[A-Za-z0-9]|[A-Za-z0-9]")
_HANGUL = re.compile(r"[가-힣]{2,}")
_COMMON: set[str] | None = None


def common_words() -> set[str]:
    global _COMMON
    if _COMMON is None:
        p = Path(__file__).resolve().parent / "data" / "common_words.txt"
        try:
            _COMMON = {w.lower() for line in p.read_text(encoding="utf-8").splitlines()
                       if not line.lstrip().startswith("#") for w in line.split()}
        except OSError as e:
            # Without the list every sentence-initial 'Volume' or 'Price' would count as an entity.
            raise RuntimeError(f"crux search needs {p} (the common English words list): {e}") from e
        if len(_COMMON) < 1000:
            raise RuntimeError(f"{p} holds only {len(_COMMON)} words; expected about 5,000")
    return _COMMON


def is_common(word: str) -> bool:
    """In the common-words list, directly or as a plain inflection of a listed base form
    (-s, -es, -ies, -ed, -d, -ing, -ly, -er, -est; 'Prices', 'Rises', 'Falling', 'Sharply')."""
    w = (word or "").lower()
    common = common_words()
    if w in common:
        return True
    cands = []
    for suf, rep in (("ies", "y"), ("es", ""), ("s", ""), ("ied", "y"), ("ed", ""), ("ed", "e"), ("d", ""),
                     ("ing", ""), ("ing", "e"), ("ly", ""), ("ily", "y"), ("er", ""), ("er", "e"), ("est", ""),
                     ("est", "e")):
        if w.endswith(suf) and len(w) - len(suf) >= 2:
            stem = w[: len(w) - len(suf)] + rep
            cands.append(stem)
            if len(stem) >= 3 and stem[-1] == stem[-2] and not rep:   # 'stopped' -> 'stop', 'bigger' -> 'big'
                cands.append(stem[:-1])
    return any(c in common for c in cands)


def lowercase_vocab(texts) -> set[str]:
    """Words that appear in lower case in the corpus (for the sentence-initial entity rule)."""
    out: set[str] = set()
    for t in texts:
        out.update(re.findall(r"\b[a-z][a-z\-]{1,}\b", t or ""))
    return out


def _is_number_token(tok: str) -> bool:
    return bool(re.fullmatch(r"[$€£₩]?[\d.,]+[%kKmMbBtT]?n?", tok))


def entities(text: str, vocab: set[str] | None = None) -> list[str]:
    vocab = vocab or set()
    common_words()          # fail loudly when the list is missing
    out = []
    for m in _TOKEN.finditer(text or ""):
        tok = m.group(0).strip(".-/")
        if len(tok) < 3 or _is_number_token(tok):
            continue
        if not (re.search(r"[A-Z]", tok) or re.search(r"\d", tok)):
            continue
        if tok.lower() in STOP or _DATE_TOKEN.match(tok):
            continue
        before = (text[:m.start()]).rstrip()
        initial = not before or before[-1] in ".!?:;\n\"'(" or before.endswith("—")
        if initial and tok[0].isupper() and tok[1:].islower() and (is_common(tok) or tok.lower() in vocab):
            continue
        out.append(tok)
    out += _HANGUL.findall(text or "")
    return list(dict.fromkeys(out))


METRIC_SET = set(METRIC_WORDS)


def crux_terms(texts: list[str], vocab: set[str] | None = None) -> dict:
    """Numbers, entities and metric words of the cruxes. An entity and a metric word from the same token
    ('TVL', 'Volume') count once, as the metric word: a generic metric is not a specific term."""
    nums, ents, mets = [], [], []
    for t in texts:
        if not t:
            continue
        nums += evidence.numbers(t)
        ents += entities(t, vocab)
        low = t.lower()
        mets += [w for w in METRIC_WORDS if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low)]
    seen, uniq = set(), []
    for x in nums:
        key = (round(x["scaled"], 6), bool(x.get("pct")), x.get("cur", ""))
        if key not in seen:
            seen.add(key)
            uniq.append({"raw": x["raw"], "value": x["value"], "scaled": x["scaled"], "pct": x.get("pct", False),
                         "cur": x.get("cur", "")})
    ents = [e for e in dict.fromkeys(ents) if e.lower() not in METRIC_SET]
    return {"numbers": uniq, "entities": ents, "metrics": list(dict.fromkeys(mets))}


def same_number(x: dict, y: dict) -> bool:
    """Two numbers are the same figure when their scaled values match within the evidence tolerance,
    both or neither are percentages, and their currencies do not differ. The bare mantissa never
    matches: '85K' is not '85', '$85.2M' or '85.1%'; '$1B' is not '1.0%'."""
    if bool(x.get("pct")) != bool(y.get("pct")):
        return False
    cx, cy = x.get("cur", ""), y.get("cur", "")
    if cx and cy and cx != cy:
        return False
    return evidence._close(x["scaled"], y["scaled"])


def _num_match(xs: list[dict], ys: list[dict]) -> list[str]:
    hit = []
    for y in ys:
        for x in xs:
            if same_number(x, y):
                hit.append(y["raw"])
                break
    return hit


def term_hits(line: str, terms: dict) -> dict:
    ents = [e for e in terms.get("entities", []) if e.lower() not in METRIC_SET
            and (re.search(rf"(?<!\w){re.escape(e)}(?!\w)", line) if not _HANGUL.fullmatch(e) else e in line)]
    nums = _num_match(evidence.numbers(line), terms.get("numbers", [])) if terms.get("numbers") else []
    low = line.lower()
    mets = [w for w in terms.get("metrics", []) if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low)]
    return {"entities": ents, "numbers": nums, "metrics": mets}


def shares_term(quote: str, terms: dict) -> bool:
    """Any crux term, metric words included (reporting only)."""
    h = term_hits(quote or "", terms)
    return bool(h["entities"] or h["numbers"] or h["metrics"])


def shares_specific(quote: str, terms: dict) -> bool:
    """What a qualifying quote needs (§7.2): a crux number, or a crux entity together with a number of
    its own. An entity alone ('BTC', 'Fed') or a metric word alone does not count."""
    h = term_hits(quote or "", terms)
    if h["numbers"]:
        return True
    return bool(h["entities"]) and bool(evidence.numbers(quote or ""))


def drop_frequent_entities(terms: dict, docs: dict[str, str], max_share: float = 0.02, keep_always=()) -> dict:
    """Entities that sit on more than `max_share` of the corpus's non-empty lines ('BTC', 'ETF', 'Fed',
    'DeFi' on hundreds of package lines) say nothing specific about a crux: drop them from the terms.
    `keep_always`: entities of the question itself ('South Korea', 'Hormuz' on an event question) stay
    however often they occur: on an event question they are what a line has to be about (§7.2)."""
    lines = [l for d in docs.values() for l in (d or "").split("\n") if l.strip()]
    if not lines or not terms.get("entities"):
        return terms
    pinned = {str(x).lower() for x in keep_always or ()}
    limit = max(3, int(max_share * len(lines)))
    keep, dropped = [], []
    for e in terms["entities"]:
        if e.lower() in pinned:
            keep.append(e)
            continue
        if _HANGUL.fullmatch(e):
            n = sum(1 for l in lines if e in l)
        else:
            rx = re.compile(rf"(?<!\w){re.escape(e)}(?!\w)")
            n = sum(1 for l in lines if rx.search(l))
        (dropped if n > limit else keep).append(e)
    return {**terms, "entities": keep, "frequent_entities": dropped}


def crux_search(terms: dict, docs: dict[str, str], exclude_quotes, locator=None, top: int = 12,
                block_bytes: int = 3000, referee_bytes: int = 5000, exclude_positions=None) -> dict:
    """Data lines in the run folder's raw file, package and social extract that score
    3 x entities + 2 x numbers + 1 x metric words >= 4, match two distinct terms, at least one a
    number or an entity, and are not already quoted by either side (by text, or by the line a quote
    sits on: `exclude_positions` = {(doc, line)}). Social lines (by section or content) are left out:
    they cannot justify a move. Top `top` hits."""
    excl = [qnorm(q) for q in exclude_quotes if q and len(qnorm(q)) >= 12]
    excl_pos = set(exclude_positions or ())
    hits, seen = [], set()
    for doc in ("raw", "package", "social"):
        text = docs.get(doc) or ""
        lines = text.split("\n")
        for n, line in enumerate(lines, 1):
            s = line.strip()
            if len(s) < 12 or s.startswith("#"):
                continue
            ns = evidence.norm(s)
            if ns in seen or (doc, n) in excl_pos:
                continue
            h = term_hits(s, terms)
            distinct = len(h["entities"]) + len(h["numbers"]) + len(h["metrics"])
            score = 3 * len(h["entities"]) + 2 * len(h["numbers"]) + len(h["metrics"])
            if score < 4 or distinct < 2 or not (h["entities"] or h["numbers"]):
                continue
            if any(q in ns or (len(ns) >= 12 and ns in q) for q in excl):
                continue
            if locator is not None and doc in locator.lines:
                sec, cls = locator.label(doc, n)
            else:
                sec, cls = "", ("social" if doc == "social" or evidence.social_line(s) else "data")
            if cls == "social" or is_market_line(sec, s):
                continue
            seen.add(ns)
            ctx = [lines[i].strip()[:300] for i in (n - 2, n) if 0 <= i < len(lines) and lines[i].strip()]
            hits.append({"doc": doc, "line": n, "section": sec, "cls": cls, "score": score, "text": s[:400],
                         "terms": h, "context": ctx})
    hits.sort(key=lambda h: (-h["score"], ("raw", "package", "social").index(h["doc"]), h["line"]))
    hits = hits[:top]

    def block(cap):
        out, used = [], 0
        for h in hits:
            piece = f"- [{h['section'] or h['doc']}] {h['text']}"
            if used + nbytes(piece) + 1 > cap:
                break
            out.append(piece)
            used += nbytes(piece) + 1
        return "\n".join(out) or "(nothing found on disk for this crux)"
    return {"terms": {"numbers": [x["raw"] for x in terms.get("numbers", [])], "entities": terms.get("entities", []),
                      "metrics": terms.get("metrics", [])},
            "hits": hits, "block": block(block_bytes), "referee_block": block(referee_bytes)}


# ── prediction-market lines (phase-e SECTION 8) ────────────────────────────────────────

_PROB = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d)?%|\d{1,2}(?:\.\d)?¢")
MARKET_STOP = STOP | {"above", "below", "more", "less", "than", "before", "after", "over", "under", "least", "most",
                      "into", "their", "there", "about", "again", "still", "close", "next", "week", "month", "year",
                      "contracts", "leading", "total", "ends", "polymarket", "kalshi", "markets"}  # + the odds-line boilerplate


# Odds by content, in any section: World Monitor's Polymarket feed sits in SECTION 2 GEOPOLITICAL CONTEXT
# ('Fed Rate Hike by September 2026 Meeting? — YES: 59.5% | vol: …'), the POLYMARKET LIVE MARKETS block in
# SECTION 3 puts 'YES: 40% | 24h vol …' under the question line, the Polymarket collector writes
# '"…" YES 59%' and 'YES 59%', and Kalshi writes 'top: "…" 59% (+3)'.
_ODDS = re.compile(r"\bYES:?\s*\d{1,3}(?:\.\d+)?%|\bNO:\s*\d{1,3}(?:\.\d+)?%"
                   r"|\btop:\s*\"[^\"]{1,120}\"\s*\d{1,3}(?:\.\d+)?%"
                   r"|\"[^\"]{1,120}\"\s+\d{1,3}(?:\.\d+)?%\s*\((?:[+\-\u2212]|0\b|new\b|flat\b)")
_ODDS_CONT = re.compile(r"^\s*(?:YES|NO):?\s*\d{1,3}(?:\.\d+)?%")   # an odds line under its question line


def odds_line(line: str) -> bool:
    """A line that carries market odds by its content, whatever section it sits in."""
    return bool(_ODDS.search(line or ""))


def is_market_line(section: str, line: str) -> bool:
    """A prediction-market odds line: a line in the PREDICTION MARKETS section (package SECTION 8, raw Polymarket
    and Kalshi blocks) carrying a probability, or a line that carries odds by its content in any section
    (odds_line: World Monitor's Polymarket block in GEOPOLITICAL CONTEXT, POLYMARKET LIVE MARKETS in ON-CHAIN).
    Market odds are what traders believe, not data about the crux: they never qualify a move (§7.2), are not
    crux hits (§6) and cannot confirm a closure (§8)."""
    return ((section or "").upper().startswith(MARKET_SECTION) and bool(_PROB.search(line or ""))) or odds_line(line)


def market_lines(locator) -> list[str]:
    """The odds lines of the run folder (package and raw) for the question gate's market rule. An odds line
    under its question line ('- Will the Fed …?' then '  YES: 60% | …') is joined to that question."""
    if locator is None or not hasattr(locator, "labels"):
        return []
    out = []
    for doc in ("package", "raw"):
        if doc not in getattr(locator, "lines", {}):
            continue
        lines = locator.lines[doc]
        for n, ((sec, _), line) in enumerate(zip(locator.labels(doc), lines)):
            s = line.strip()
            if _ODDS_CONT.match(line) and n and lines[n - 1].strip().startswith("- "):
                out.append(f"{lines[n - 1].strip()} — {s}")
            elif s.startswith("- ") and is_market_line(sec, line):
                out.append(s)
    return list(dict.fromkeys(out))


def _stems(text: str) -> set[str]:
    return {w[:4] for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in MARKET_STOP}


def market_match(question: str, lines: list[str]) -> str:
    """The first odds line that prices this question, or ''. A line prices it when it carries a probability
    and shares at least one entity of the question plus a second specific term: another entity, one of the
    question's numbers (same figure, §6 rules), or two content words ('Fed' + 'hike' + 'rate')."""
    if not lines:
        return ""
    q_ents = [e for e in entities(question) if e.lower() not in MARKET_STOP]
    if not q_ents:
        return ""
    q_nums = evidence.numbers(question)
    ent_stems = {w[:4] for e in q_ents for w in re.findall(r"[a-z]{4,}", e.lower())}
    q_words = _stems(question) - ent_stems
    for line in lines:
        ents = {e.lower() for e in q_ents if re.search(rf"(?<!\w){re.escape(e)}(?!\w)", line, re.I)}
        if not ents:
            continue
        nums = _num_match(evidence.numbers(line), q_nums) if q_nums else []
        if len(ents) >= 2 or nums or len(q_words & _stems(line)) >= 2:
            return line
    return ""


# ── §7.2 the evidence gate ─────────────────────────────────────────────────────────────

EVIDENCE_MOVE_PER_ITEM = 10   # points beyond FREE_MOVE that one qualifying quote (one line) allows
EVIDENCE_MOVE_MAX = 20        # at most this many points beyond FREE_MOVE, however many quotes qualify


def line_key(locator, doc, line) -> str:
    """What makes two qualifying lines the same fact: the line's _core() text (no URL, X bracket, engagement
    counts, list dash), so a headline repeated in CROSS-SOURCE and NEWS, or in the package and the raw file,
    is one line. Falls back to 'doc:line' when the line has no core text."""
    core = evidence._core(locator.line_text(doc, line)) if locator is not None and doc else ""
    return core or f"{doc}:{line}"


def quote_positions(quotes, locator) -> set:
    out = set()
    for q in quotes or []:
        if q and locator is not None:
            out |= locator.positions(q)
    return out


def gate_move(agent: str, take_p: int, requested, own_take_values, other_take_p: int, verdict: str,
              new_evidence: list[dict], own_quotes, other_quotes, crux_hits, terms: dict, locator,
              kind: str = "", shared_lines=None) -> dict:
    """The gated move of one side (SIDE_MOVE fields plus `flags`).

    own_quotes: this side's take and challenge quotes; other_quotes: the other side's; crux_hits: this
    pair's crux-search hits ({doc, line, text}). An item's source is decided by where it sits, not by
    exact text: `own`/`challenger` when it sits on a line a quote of that side sits on, or overlaps one
    of those quotes as text; `crux_data` when it sits on a crux hit line; else `other`.

    An item qualifies when Locator.strict passes (single verbatim line, no '...', every number found,
    40+ characters or a number), its line is data (social by content counts as social), its source is
    crux_data or other, it is not a prediction-market odds line, and it carries a crux number, or a crux
    entity together with a number (shares_specific); on an event or judgment question (`kind`) a crux
    entity alone is enough, since headlines about events carry no number. Qualifying items only extend the
    move beyond FREE_MOVE: +EVIDENCE_MOVE_PER_ITEM per distinct qualifying line, at most EVIDENCE_MOVE_MAX
    (5 + 10 per line, 25 in total; spec §7.2, response.md step 3).

    A line is identified by line_key() (its _core() text), so the same headline in two places is one line.
    The allowance belongs to the pair, not to each side: a line that the other side's response also
    qualified on (`shared_lines`, line keys, set by the responses phase once both sides answered) gives
    each side half (5 points), so one copied crux line closes a split by at most 5 + 5 + 10 = 20 points.
    cap_pair() then caps the pair's total closure (different lines on each side)."""
    new_p, nflags = norm_p(requested, own_take_values)
    flags: list[str] = []
    if new_p is None:
        new_p = int(take_p)
        flags.append("no probability")
    d = new_p - take_p
    own_pos, oth_pos = quote_positions(own_quotes, locator), quote_positions(other_quotes, locator)
    own_n = [x for x in (qnorm(q) for q in own_quotes or [] if q) if len(x) >= 12]
    oth_n = [x for x in (qnorm(q) for q in other_quotes or [] if q) if len(x) >= 12]
    hit_pos = {(h.get("doc"), h.get("line")) for h in crux_hits or []}
    hit_txt = [evidence.norm(h.get("text", "")) for h in crux_hits or []]
    items, qual_lines = [], set()
    for e in new_evidence or []:
        q = (e.get("quote") or "").strip()
        loc = locator.locate(q) if q else {"status": "empty", "cls": ""}
        nq = qnorm(q)
        pos = locator.positions(q) if q else set()

        def overlaps(lst):
            return any(nq and (nq in o or o in nq) for o in lst)
        if nq and (pos & own_pos or overlaps(own_n)):
            src = "own"
        elif nq and (pos & oth_pos or overlaps(oth_n)):
            src = "challenger"
        elif nq and (pos & hit_pos or any(nq in h for h in hit_txt)):
            src = "crux_data"
        else:
            src = "other"
        st = locator.strict(q) if q else {"ok": False, "reason": "empty", "cls": ""}
        market = bool(st.get("ok")) and is_market_line(st.get("section", ""), locator.line_text(st["doc"], st["line"]) or q)
        specific = shares_specific(q, terms) or (kind in ("event", "judgment") and bool(term_hits(q, terms)["entities"]))
        qual = bool(st["ok"] and st.get("cls") == "data" and src in ("crux_data", "other") and not market and specific)
        why = "" if qual else (st.get("reason") or ("social line" if st.get("cls") == "social" else
                               f"cited before ({src})" if src in ("own", "challenger") else
                               "prediction-market odds line" if market else
                               "no crux entity" if kind in ("event", "judgment") else "no crux number or entity"))
        if qual:
            qual_lines.add(line_key(locator, st["doc"], st["line"]))
        items.append({"section": e.get("section", ""), "quote": q[:300], "status": loc["status"],
                      "cls": loc.get("cls") or "", "new_evidence_source": src, "qualifies": qual,
                      **({"why_not": why} if not qual else {})})
    qualifying = [x for x in items if x["qualifies"]]
    shared = qual_lines & set(shared_lines or ())
    extra = EVIDENCE_MOVE_PER_ITEM * len(qual_lines - shared) + (EVIDENCE_MOVE_PER_ITEM // 2) * len(shared)
    allowed = FREE_MOVE + min(EVIDENCE_MOVE_MAX, extra)
    if shared:
        flags.append("shared evidence line, allowance split")
    if abs(d) <= allowed:
        gated = new_p
    else:
        gated = take_p + int(math.copysign(allowed, d))
        if qualifying:
            flags.append(f"evidence move capped at {allowed}")
        elif any(x["new_evidence_source"] in ("crux_data", "other") and x["cls"] == "social" for x in items):
            flags.append("social evidence only, capped")
        elif any(x["new_evidence_source"] in ("crux_data", "other") and x["status"] in ("verified", "partial")
                 for x in items):
            flags.append("evidence not qualifying, capped")
        elif any(x["new_evidence_source"] == "challenger" for x in items):
            flags.append("argument only, capped")
        else:
            flags.append("update without evidence, capped")
    gated = max(0, min(100, gated))
    toward = other_take_p - take_p
    moved = gated - take_p
    if verdict == "concede" and abs(moved) < 5:
        flags.append("verbal concession")
    if verdict == "hold" and abs(d) > FREE_MOVE:
        flags.append("hold but moved")
    if verdict in ("narrow", "concede") and moved and toward and (moved > 0) != (toward > 0):
        flags.append("moved away")
    # A soft move is more than the free move towards the opponent without qualifying evidence (§7.2, §15.5 d).
    # The gate makes it impossible on the gated value, so it is an invariant check; `soft request` records the
    # behaviour the gate cut back: the agent asked for more than the free move without qualifying evidence.
    if toward and moved and (moved > 0) == (toward > 0) and abs(moved) > FREE_MOVE and not qualifying:
        flags.append("soft move")
    if toward and d and (d > 0) == (toward > 0) and abs(d) > FREE_MOVE and not qualifying:
        flags.append("soft request")
    src = "none"
    if any(x["new_evidence_source"] == "crux_data" for x in qualifying):
        src = "crux_data"
    elif qualifying:
        src = "other"
    return {"agent": agent, "take": int(take_p), "requested": None if "no probability" in flags else new_p,
            "gated": int(gated), "delta": int(gated - take_p), "clamped": "clamped" in nflags,
            "rescaled": "fraction rescaled" in nflags, "new_evidence": items,
            "new_evidence_verified": sum(1 for x in items if x["status"] in ("verified", "partial")),
            "new_data_evidence": sum(1 for x in items if x["status"] in ("verified", "partial") and x["cls"] == "data"),
            "evidence_source": src, "flags": flags}


def qualifying_lines(move: dict, new_evidence: list[dict], locator) -> set:
    """Line keys (line_key) of a gated move's qualifying items; `new_evidence` is the response's own list (the
    move stores quotes cut to 300 characters), in the same order as move['new_evidence']."""
    out = set()
    for x, e in zip((move or {}).get("new_evidence") or [], new_evidence or []):
        q = (e.get("quote") or "").strip()
        if x.get("qualifies") and q:
            st = locator.strict(q)
            if st.get("ok"):
                out.add(line_key(locator, st["doc"], st["line"]))
    return out


def pair_allowance(keys_high, keys_low) -> int:
    """The most a pair may close in total (§7.2): FREE_MOVE for each side plus EVIDENCE_MOVE_PER_ITEM per distinct
    qualifying line across both sides, the EVIDENCE_MOVE_MAX item cap applied once for the pair."""
    distinct = set(keys_high or ()) | set(keys_low or ())
    return 2 * FREE_MOVE + min(EVIDENCE_MOVE_MAX, EVIDENCE_MOVE_PER_ITEM * len(distinct))


def cap_pair(move_high: dict | None, move_low: dict | None, keys_high, keys_low) -> tuple[dict | None, dict | None, bool]:
    """Cap the pair's total closure: the high side's move down plus the low side's move up may not exceed
    pair_allowance(). Each side keeps its free part (up to FREE_MOVE towards the other side); the evidence parts
    beyond it are scaled down together to fit. Moves away from the other side are left alone. Idempotent.
    Returns (move_high, move_low, capped); the moves are copies, flagged 'pair evidence cap'."""
    def toward(m, side):
        if not m:
            return 0
        d = m["gated"] - m["take"]
        return max(0, -d) if side == "high" else max(0, d)
    th, tl = toward(move_high, "high"), toward(move_low, "low")
    allowed = pair_allowance(keys_high, keys_low)
    if th + tl <= allowed:
        return move_high, move_low, False
    fh, fl = min(th, FREE_MOVE), min(tl, FREE_MOVE)
    eh, el = th - fh, tl - fl
    pool = max(0, allowed - fh - fl)
    if eh + el:
        nh = int(eh * pool // (eh + el))
        nl = min(el, pool - nh)
    else:
        nh = nl = 0
    out = []
    for m, side, free, ev in ((move_high, "high", fh, nh), (move_low, "low", fl, nl)):
        if not m:
            out.append(m)
            continue
        m = dict(m)
        new = m["take"] - (free + ev) if side == "high" else m["take"] + (free + ev)
        if new != m["gated"]:
            m["gated"] = int(max(0, min(100, new)))
            m["delta"] = int(m["gated"] - m["take"])
            m["flags"] = list(m.get("flags") or []) + ([f"pair evidence cap {allowed}"]
                                                      if not any(str(f).startswith("pair evidence cap") for f in m.get("flags") or []) else [])
        out.append(m)
    return out[0], out[1], True


# ── §9.1 per-debate scores ─────────────────────────────────────────────────────────────

VERDICT_RANK = {"hold": 0, "narrow": 1, "concede": 2}


def _ev_counts(items: list[dict]) -> dict:
    return {"claimed": len(items), "verified": sum(1 for e in items if e.get("status") in ("verified", "partial")),
            "data": sum(1 for e in items if e.get("status") in ("verified", "partial") and e.get("cls") == "data")}


def score_debate(pr: dict, ch_by: dict, resp_of: dict, crux_check: dict | None, gap_min: int) -> dict:
    """pr: a PAIR; ch_by[side]: the challenge record written BY that side ('high'/'low') or None;
    resp_of[side]: that side's response record (with 'move', 'data') or None."""
    hi, lo = pr["high"], pr["low"]
    gap_before = int(pr["p_high"] - pr["p_low"])
    moves = [r["move"] for r in (resp_of.get("high"), resp_of.get("low")) if r]
    fin_hi = resp_of["high"]["move"]["gated"] if resp_of.get("high") else pr["p_high"]
    fin_lo = resp_of["low"]["move"]["gated"] if resp_of.get("low") else pr["p_low"]
    nresp = len(moves)
    status = "two-sided" if nresp == 2 else ("one-sided" if nresp == 1 else "failed")
    gap_after = None if status == "failed" else int(abs(fin_hi - fin_lo))
    # Closed on data: a side moved more than the free move towards the other side on qualifying crux
    # data (the free 5 points never count). That alone does not end the split: a responder can copy a
    # crux-hit line. The split only stops being live when the neutral crux check ran on THIS debate and
    # confirmed the data: resolved 'yes', or 'partly' leaning the way the mover moved. Otherwise the
    # block stays and is marked narrowed_on_data.
    closed, movers = False, []
    for side, m in (("high", resp_of.get("high")), ("low", resp_of.get("low"))):
        if not m:
            continue
        mv = m["move"]
        toward = (mv["delta"] < 0) if side == "high" else (mv["delta"] > 0)
        if mv["evidence_source"] == "crux_data" and toward and abs(mv["delta"]) > FREE_MOVE:
            closed = True
            movers.append("lower" if side == "high" else "higher")
    cc = crux_check or {}
    # The referee confirms a closure only when its verdict points the way the mover moved ('yes' and 'partly'
    # alike: a 'yes, leans higher' after the high side came down says the data favours the high view) and its
    # quote would qualify a move: a strict single data line, not social, not a market odds line
    # (ph_cruxcheck sets quote_qualifies; an artifact without it confirms nothing).
    quote_ok = cc.get("quote_qualifies") is True
    confirmed = bool(closed and crux_check and quote_ok and cc.get("resolved") in ("yes", "partly")
                     and cc.get("leans") in movers)
    if closed and cc.get("resolved") == "no":
        closed = False
    ga = gap_after if gap_after is not None else gap_before
    cwe = 0 if closed else max(0, gap_before - ga)
    if gap_after is None:
        effect = f"no responses (split of {gap_before})"
    elif ga < gap_before:
        how = ("crux data, confirmed by the data check" if confirmed else "crux data" if closed else
               "new data" if any(m["evidence_source"] == "other" for m in moves) else "argument")
        effect = f"narrowed from {gap_before} to {ga} on {how}"
    elif ga > gap_before:
        effect = f"widened from {gap_before} to {ga}"
    else:
        effect = f"held at {gap_before}"
    # in_split: the selection rule for WHERE THE VIEWS SPLIT (§11.1): the block stays unless data closed the
    # split and the crux check confirmed it. True for nearly every staged pair by construction.
    in_split = ga >= 20 or (gap_before >= gap_min and not confirmed)
    # live_split: measured after the debate, independent of the selection rule: answered, still >= gap_min apart
    # and not confirmed closed.
    live = status != "failed" and ga >= gap_min and not confirmed
    narrowed_on_data = bool(closed and not confirmed and ga < gap_before)
    data_move = any(abs(m["delta"]) > FREE_MOVE and m["evidence_source"] != "none" for m in moves)
    flags = []
    for side, agent in (("high", hi), ("low", lo)):
        ch = ch_by.get(side)
        if ch:
            flags += [{"agent": agent, "flag": f, "where": "challenge"} for f in ch.get("checks", {}).get("flags", [])]
        r = resp_of.get(side)
        if r:
            flags += [{"agent": agent, "flag": f, "where": "response"} for f in r["move"].get("flags", [])]
            if (r["data"].get("steelman_fair") or {}).get("verdict") == "no":
                flags.append({"agent": agent, "flag": "rejected steelman", "where": "response"})

    def side_ev(side):
        items = list((ch_by.get(side) or {}).get("checks", {}).get("evidence", []))
        items += list(((resp_of.get(side) or {}).get("move") or {}).get("new_evidence", []))
        return _ev_counts(items)

    def rdata(side, *keys):
        d = (resp_of.get(side) or {}).get("data") or {}
        for k in keys:
            d = d.get(k, "") if isinstance(d, dict) else ""
        return d or ""
    agreed = bool(resp_of.get("high") and resp_of.get("low")
                  and rdata("high", "crux_agreed", "verdict") == "yes" and rdata("low", "crux_agreed", "verdict") == "yes")

    def stated_crux(side):
        return bool((((ch_by.get(side) or {}).get("data") or {}).get("crux") or {}).get("claim", "").strip())
    # held_split (pass bar §15.5 b): both sides answered, the split is still >= gap_min, not confirmed closed,
    # and both challenges stated a crux. A failed or one-sided debate cannot pass it.
    held = bool(status == "two-sided" and ga >= gap_min and not confirmed and stated_crux("high") and stated_crux("low"))
    # useful (§9.1): judged on what the debate produced, never on having been staged. A qualifying data move,
    # a crux check with a qualifying quote that resolved yes or leaned partly, or a split that held (or widened)
    # with an agreed crux. A failed debate is never useful.
    cc_useful = quote_ok and (cc.get("resolved") == "yes" or
                              (cc.get("resolved") == "partly" and cc.get("leans") not in (None, "", "neither")))
    useful = bool(status != "failed" and (data_move or cc_useful or (gap_after is not None and ga >= gap_before and agreed)))
    return {
        "question_id": pr["question_id"], "high": hi, "low": lo, "status": status,
        "gap_before": gap_before, "gap_after": gap_after,
        "moves": [{k: v for k, v in m.items() if k != "flags"} for m in moves],
        "verdicts": {"high": rdata("high", "verdict"), "low": rdata("low", "verdict")},
        "steelman_fair": {"high": rdata("high", "steelman_fair", "verdict"), "low": rdata("low", "steelman_fair", "verdict")},
        "crux_agreed": agreed,
        "evidence": {"high": side_ev("high"), "low": side_ev("low")},
        "flags": flags,
        "crux_check": {"resolved": cc.get("resolved", ""), "leans": cc.get("leans", "")} if crux_check else None,
        "closed_on_data": closed, "narrowed_on_data": narrowed_on_data, "closure_without_evidence": int(cwe),
        "effect": effect, "in_split": bool(in_split), "live_split": bool(live), "held_split": held,
        "useful": useful,
    }


# ── §10 final positions and stats ──────────────────────────────────────────────────────

def question_stats(values) -> dict:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"n": 0, "median": None, "mean": None, "min": None, "max": None, "range": None, "iqr": None,
                "majority_side": "", "majority_count": 0, "minority_count": 0}
    med = statistics.median(vals)
    above = sum(1 for v in vals if v > 50)
    below = sum(1 for v in vals if v < 50)
    if med > 50:
        side, maj, mino = "yes", above, below
    elif med < 50:
        side, maj, mino = "no", below, above
    else:
        side, maj, mino = "even", max(above, below), min(above, below)
    iqr = 0.0
    if len(vals) >= 2:
        qs = statistics.quantiles(vals, n=4, method="inclusive")
        iqr = qs[2] - qs[0]
    return {"n": len(vals), "median": round(med, 1), "mean": round(sum(vals) / len(vals), 1), "min": min(vals),
            "max": max(vals), "range": max(vals) - min(vals), "iqr": round(iqr, 1), "majority_side": side,
            "majority_count": maj, "minority_count": mino}


def final_positions(takes_p: dict, gated: dict) -> dict:
    """{agent: {qid: final}}: the gated response value where the agent debated the question, else
    the take value."""
    out = {}
    for a, qs in takes_p.items():
        out[a] = dict(qs)
        for q, v in (gated.get(a) or {}).items():
            out[a][q] = int(v)
    return out


# ── §9.2 per agent per run ─────────────────────────────────────────────────────────────

def agent_run_score(agent: str, run_id: str, day: str, takes_p: dict, debates: list[dict], ev_items: list[dict],
                    numbers_by_agent: dict, flags: list[dict], novel: str) -> dict:
    meds = {}
    for q in {q for v in takes_p.values() for q in v}:
        vals = [v[q] for v in takes_p.values() if q in v]
        if vals:
            meds[q] = statistics.median(vals)
    mine = takes_p.get(agent, {})
    dist = [abs(v - meds[q]) for q, v in mine.items() if q in meds]
    ok = [e for e in ev_items if e.get("status") in ("verified", "partial")]
    claimed = [e for e in ev_items if e.get("status") != "empty"]
    others = set().union(*[s for a, s in numbers_by_agent.items() if a != agent]) if len(numbers_by_agent) > 1 else set()
    moves = [m for d in debates for m in d.get("moves", []) if m.get("agent") == agent]
    myflags = [f for f in flags if f.get("agent") == agent]
    return {
        "run_id": run_id, "day": day, "agent": agent,
        "distinctness": round(sum(dist) / len(dist), 2) if dist else None,
        "endpoint_count": sum(1 for d in debates if agent in (d.get("high"), d.get("low"))),
        "evidence_rate": round(len(ok) / len(claimed), 3) if claimed else None,
        "data_share": round(sum(1 for e in ok if e.get("cls") == "data") / len(ok), 3) if ok else None,
        "unique_numbers": len(numbers_by_agent.get(agent, set()) - others),
        "moves_with_evidence": sum(1 for m in moves if m.get("evidence_source") != "none" and m.get("delta")),
        "moves_capped": sum(1 for f in myflags if f["flag"].endswith("capped")
                            or f["flag"].startswith("evidence move capped")),
        "verbal_concessions": sum(1 for f in myflags if f["flag"] == "verbal concession"),
        "steelmen_rejected": sum(1 for d in debates for f in d.get("flags", [])
                                 if f["flag"] == "rejected steelman" and f["agent"] != agent
                                 and agent in (d.get("high"), d.get("low"))),
        "leakage_flags": sum(1 for f in myflags if f["flag"] == "persona leakage"),
        "novel": novel or "",
    }


# ── citation overlap per question and lens-quote share (§15.5, review 2026-10-04) ─────────

def question_overlap(takes: dict) -> dict:
    """Per question, the mean pairwise Jaccard overlap of the quotes the takes cite for it. Takes now answer
    the same 4-5 questions, so whole-take number overlap is not comparable with the free-form v1 takes;
    this compares like with like. {per_question: {qid: x}, mean: x}."""
    from itertools import combinations as _comb
    per = {}
    qids = sorted({p.get("question_id") for t in takes.values() for p in (t or {}).get("positions") or []
                   if p.get("question_id")})
    for qid in qids:
        sets = {}
        for a, t in takes.items():
            for p in (t or {}).get("positions") or []:
                if p.get("question_id") == qid:
                    sets[a] = {qnorm(e.get("quote")) for e in p.get("evidence") or [] if len(qnorm(e.get("quote"))) >= 12}
        pairs = [(a, b) for a, b in _comb(sorted(sets), 2) if sets[a] or sets[b]]
        if pairs:
            per[qid] = round(sum(len(sets[a] & sets[b]) / len(sets[a] | sets[b]) for a, b in pairs) / len(pairs), 3)
    return {"per_question": per, "mean": round(sum(per.values()) / len(per), 3) if per else None}


def lens_quote_share(takes: dict, lens: dict) -> dict:
    """Per agent, the share of its positions that cite at least one quote from its own YOUR LENS DATA
    block (take.md asks for one per position). {per_agent: {agent: x}, mean: x}."""
    per = {}
    for a, t in takes.items():
        body = evidence.norm((lens.get(a) or {}).get("text") or "")
        pos = (t or {}).get("positions") or []
        if not pos:
            continue
        hit = sum(1 for p in pos if body and any(len(qnorm(e.get("quote"))) >= 12 and qnorm(e.get("quote")) in body
                                                 for e in p.get("evidence") or []))
        per[a] = round(hit / len(pos), 3)
    return {"per_agent": per, "mean": round(sum(per.values()) / len(per), 3) if per else None}


# ── §11 split sheet ────────────────────────────────────────────────────────────────────

def _rng(vals) -> str:
    lo, hi = int(round(min(vals))), int(round(max(vals)))
    return f"{lo}%" if lo == hi else f"{lo}–{hi}%"


def counts_of(finals: dict[str, int]) -> dict:
    vals = list(finals.values())
    st = question_stats(vals)
    return {"n": st["n"], "majority": st["majority_count"], "minority": st["minority_count"],
            "median": st["median"] if st["median"] is not None else 0.0,
            "range": [int(round(st["min"])), int(round(st["max"]))] if vals else [0, 0]}


def count_phrase(finals: dict[str, int], typ: str) -> str:
    vals = list(finals.values())
    if not vals:
        return ""
    st = question_stats(vals)
    n, med = st["n"], st["median"]
    if typ == "consensus":
        d = int(math.ceil(max(abs(v - med) for v in vals)))
        return f"all {n} lenses within {d} points of {int(round(med))}%"
    side = st["majority_side"]
    if side == "even":
        above = [v for v in vals if v > 50]
        below = [v for v in vals if v < 50]
        big, small = (above, below) if len(above) >= len(below) else (below, above)
        maj_vals, min_vals = big, small
    elif side == "yes":
        maj_vals, min_vals = [v for v in vals if v > 50], [v for v in vals if v < 50]
    else:
        maj_vals, min_vals = [v for v in vals if v < 50], [v for v in vals if v > 50]
    at50 = n - len(maj_vals) - len(min_vals)
    if typ == "direction" and min_vals:
        s = f"{len(maj_vals)} of {n} lenses put it at {_rng(maj_vals)}; {len(min_vals)} put it at {_rng(min_vals)}"
        return s + (f"; {at50} sit at 50%" if at50 else "")
    lean = "yes" if side == "yes" else ("no" if side == "no" else "neither way")
    if not maj_vals:
        return f"all {n} lenses sit at 50%"
    lo, hi = int(round(min(maj_vals))), int(round(max(maj_vals)))
    if len(maj_vals) == n:
        return f"all {n} lenses lean {lean}, from {lo}% to {hi}%"
    return f"{len(maj_vals)} of {n} lenses lean {lean}, from {lo}% to {hi}%; {at50} sit at 50%"


def _first_quote(items: list[dict], locator, data_only: bool = True) -> str:
    for e in items or []:
        q = (e.get("quote") or "").strip()
        if not q:
            continue
        loc = locator.locate(q) if locator is not None else {"status": e.get("status"), "cls": e.get("cls")}
        if loc.get("status") == "verified" and (loc.get("cls") == "data" or not data_only):
            return q[:200]
    return ""


def split_sheet(day: str, run_id: str, day_type: str, questions: list[dict], take_p: dict, finals: dict,
                debates: list[dict], challenges: dict, responses: dict, takes: dict, locator, gap_min: int,
                red_team: dict | None = None, red_team_agent: str | None = None, crux_check: dict | None = None,
                ledger: list[dict] | None = None) -> dict:
    """§11.1-11.2. challenges: {(challenger, qid): record}; responses: {(agent, qid): record};
    takes: {agent: TAKE}; crux_check: {question_id, data, ...} when §8 ran."""
    ledger = ledger or []
    qby = {q["id"]: q for q in questions}
    dby = {d["question_id"]: d for d in debates}

    def fin(qid):
        return {a: v[qid] for a, v in finals.items() if qid in v}

    def tk(qid):
        return {a: v[qid] for a, v in take_p.items() if qid in v}

    def reason_of(a, qid):
        for p in (takes.get(a) or {}).get("positions", []):
            if p.get("question_id") == qid:
                return p.get("reason", ""), p.get("evidence", [])
        return "", []

    def verified_data(a, qid):
        return sum(1 for e in reason_of(a, qid)[1] if locator is not None
                   and locator.locate(e.get("quote", "")).get("status") in ("verified", "partial")
                   and locator.locate(e.get("quote", "")).get("cls") == "data")

    def majority_agent(qid, side_fn):
        f = fin(qid)
        maj = [a for a, v in f.items() if side_fn(v)]
        if not maj:
            return None
        med = statistics.median([f[a] for a in maj])
        return sorted(maj, key=lambda a: (abs(f[a] - med), -verified_data(a, qid), a))[0]

    def carried(q):
        cf = (q.get("carried_from") or "").strip()
        if not cf:
            return ""
        old = next((l for l in ledger if l.get("ledger_id") == cf), None)
        if not old:
            return ""
        a = (old.get("final_stats") or {}).get("median")
        b = question_stats(list(fin(q["id"]).values())).get("median")
        if a is None or b is None:
            return ""
        return f"unchanged since {old.get('day', cf[:10])[5:]} (median {a:.0f}% → {b:.0f}%)"

    cands = []
    for d in debates:
        if d.get("in_split", d.get("live_split")) and d["question_id"] in qby:
            w = int(qby[d["question_id"]].get("weight", 1) or 1)
            cands.append((0, -w * d["gap_before"], d["question_id"]))
    for q in questions:
        if q["id"] in dby:
            continue
        st_take = question_stats(list(tk(q["id"]).values()))
        st_fin = question_stats(list(fin(q["id"]).values()))
        if st_take["n"] < 3:
            continue
        rng = st_take["range"] or 0
        if day_type == "split_unpaired":
            ok = rng >= gap_min and (st_fin["minority_count"] >= 1 or rng >= gap_min)
        else:
            ok = st_fin["minority_count"] >= 2 and rng >= 40
        if ok:
            cands.append((1, -int(q.get("weight", 1) or 1) * rng, q["id"]))
    cands.sort()
    blocks = []
    for _, _, qid in cands[:3]:
        q = qby[qid]
        # A debated block is about the split the debate was on: its type, counts and count phrase come from
        # the take values (gap_before), not from post-debate finals, so a direction split two responders
        # narrowed never prints as 'all 9 lenses lean yes' beside a minority view (§11.2, review 2026-10-04).
        f = tk(qid) if qid in dby else fin(qid)
        st = question_stats(list(f.values()))
        typ = "direction" if st["minority_count"] >= 1 else "degree"
        if typ == "degree":
            rng = st["range"] or 0
            need = gap_min if day_type == "split_unpaired" else 30
            dd = dby.get(qid)
            if rng < need and not (dd and dd.get("gap_before", 0) >= gap_min):
                continue
        side = st["majority_side"]
        on_maj = (lambda v: v > 50) if side == "yes" else ((lambda v: v < 50) if side == "no" else (lambda v: v >= 50))
        d = dby.get(qid)
        base_text, base_quote, min_text, min_quote, min_src, crux = "", "", "", "", "reason", ""
        wcm_dates = []
        if d:
            hi, lo = d["high"], d["low"]
            med = st["median"]
            if typ == "direction":
                # Pairs straddle the median, not 50: a debater is the minority voice only when its take sits
                # on the minority side of 50 (90 vs 55 with two lenses at 30 has none: undebated rule below).
                on_min = [a for a in (hi, lo) if a in f and not on_maj(f[a]) and f[a] != 50]
                min_agent = on_min[0] if on_min else None
            else:
                min_agent = hi if abs(take_p[hi][qid] - med) >= abs(take_p[lo][qid] - med) else lo
            maj_agent = (lo if min_agent == hi else hi) if min_agent else None
            ch_min = challenges.get((min_agent, qid)) if min_agent else None
            ch_maj = challenges.get((maj_agent, qid)) if maj_agent else None
            if min_agent is None:
                mins = [a for a, v in f.items() if not on_maj(v) and v != 50]
                ext = sorted(mins, key=lambda a: (-abs(f[a] - med), a))[0] if mins else None
                min_text, ev = reason_of(ext, qid) if ext else ("", [])
                min_quote = _first_quote(ev, locator, data_only=False)
                min_src = "reason"
            elif ch_min and not ({"persona leakage", "over length"} & set(ch_min.get("checks", {}).get("flags", []))):
                min_text = ch_min["data"].get("rebuttal", "")
                min_quote = _first_quote(ch_min["data"].get("evidence"), locator, data_only=False)
                min_src = "rebuttal"
            elif ch_maj and ch_maj["data"].get("steelman"):
                min_text, min_src = ch_maj["data"]["steelman"], "steelman"
            else:
                min_text, ev = reason_of(min_agent, qid)
                min_quote = _first_quote(ev, locator, data_only=False)
                min_src = "reason"
            if ch_maj and on_maj(f.get(maj_agent, 50)):
                base_text = ch_maj["data"].get("rebuttal", "")
                base_quote = _first_quote(ch_maj["data"].get("evidence"), locator)
            ra = responses.get((min_agent, qid))
            rb = responses.get((maj_agent, qid))
            agreed = d.get("crux_agreed")
            src_ch = ch_maj if agreed and not ch_min else (ch_min or ch_maj)
            if src_ch is None:
                src_ch = challenges.get((hi, qid)) or challenges.get((lo, qid))
            crux = ((src_ch or {}).get("data", {}).get("crux") or {}).get("claim", "")
            for ch in (ch_min, ch_maj) if min_agent else (challenges.get((hi, qid)), challenges.get((lo, qid))):
                dt = ((ch or {}).get("data", {}).get("would_change_my_mind") or {}).get("by_date", "")
                if dt and _DATE.match(dt):
                    wcm_dates.append(dt)
            _ = (ra, rb)
        else:
            med = st["median"]
            if typ == "direction":
                mins = [a for a, v in f.items() if not on_maj(v) and v != 50]
            else:
                mins = list(f)
            if mins:
                ext = sorted(mins, key=lambda a: (-abs(f[a] - med), a))[0]
                min_text, ev = reason_of(ext, qid)
                min_quote = _first_quote(ev, locator, data_only=False)
            min_src = "reason"
        if not base_text:
            ma = majority_agent(qid, on_maj)
            if ma:
                base_text, ev = reason_of(ma, qid)
                base_quote = base_quote or _first_quote(ev, locator)
        cc = None
        if crux_check and crux_check.get("question_id") == qid and crux_check.get("data"):
            c = crux_check["data"]
            cc = {"resolved": c.get("resolved", ""), "what_the_data_says": clean_text(c.get("what_the_data_says", ""), 500),
                  "quote": (c.get("quote") or "")[:200]}
        if crux_check and crux_check.get("question_id") == qid and (crux_check.get("data") or {}).get("settles_on", {}).get("observable"):
            so = crux_check["data"]["settles_on"]
            settles = {"observable": so.get("observable", ""), "by_date": so.get("by_date", "") if _DATE.match(so.get("by_date", "") or "") else ""}
        elif q.get("resolves_on") or q.get("settles_with"):
            settles = {"observable": q.get("settles_with", ""), "by_date": q.get("resolves_on", "")}
        else:
            settles = {"observable": "", "by_date": min(wcm_dates) if wcm_dates else ""}
        settles = {"observable": anonymise(settles.get("observable", "")), "by_date": settles.get("by_date", "")}
        blocks.append({
            "type": typ, "debated": bool(d), "question_id": qid, "ledger_id": q.get("ledger_id", ""),
            "question": anonymise(q.get("text", "")), "resolves_on": q.get("resolves_on", ""),
            "settles_with": anonymise(q.get("settles_with", "")),
            "narrowed_on_data": bool(d and d.get("narrowed_on_data")),
            "counts": counts_of(f), "count_phrase": count_phrase(f, typ),
            "base_case": {"text": clean_text(base_text, 700), "quote": base_quote},
            "minority_case": {"text": clean_text(min_text, 900), "quote": min_quote, "source": min_src},
            "crux": clean_text(crux, 300) if d else "", "crux_check": cc, "settles_on": settles,
            "carried": carried(q),
        })
    if not blocks and red_team and red_team.get("data"):
        rt = red_team["data"]
        qid = red_team.get("question_id") or rt.get("question_id")
        q = qby.get(qid)
        if q:
            f = fin(qid)
            st = question_stats(list(f.values()))
            close = sorted(f, key=lambda a: (abs(f[a] - st["median"]), -verified_data(a, qid), a))
            bt, ev = reason_of(close[0], qid) if close else ("", [])
            cc = None
            if crux_check and crux_check.get("question_id") == qid and crux_check.get("data"):
                c = crux_check["data"]
                cc = {"resolved": c.get("resolved", ""), "what_the_data_says": clean_text(c.get("what_the_data_says", ""), 500),
                      "quote": (c.get("quote") or "")[:200]}
            wc = rt.get("would_change_my_mind") or {}
            so = (crux_check or {}).get("data", {}).get("settles_on") if cc else None
            settles = ({"observable": so.get("observable", ""), "by_date": so.get("by_date", "")} if so else
                       {"observable": q.get("settles_with") or wc.get("observable", ""),
                        "by_date": q.get("resolves_on") or (wc.get("by_date", "") if _DATE.match(wc.get("by_date", "") or "") else "")})
            settles = {"observable": anonymise(settles.get("observable", "")), "by_date": settles.get("by_date", "")}
            blocks.append({
                "type": "consensus", "debated": False, "question_id": qid, "ledger_id": q.get("ledger_id", ""),
                "question": anonymise(q.get("text", "")), "resolves_on": q.get("resolves_on", ""),
                "settles_with": anonymise(q.get("settles_with", "")), "narrowed_on_data": False,
                "counts": counts_of(f), "count_phrase": count_phrase(f, "consensus"),
                "base_case": {"text": clean_text(bt, 700), "quote": _first_quote(ev, locator)},
                "minority_case": {"text": clean_text(rt.get("case", ""), 900),
                                  "quote": _first_quote(rt.get("evidence"), locator, data_only=False), "source": "red_team"},
                "crux": clean_text((rt.get("crux") or {}).get("claim", ""), 300), "crux_check": cc, "settles_on": settles,
                "carried": carried(q),
            })
    no_split = ""
    if not blocks:
        top = sorted(questions, key=lambda q: (-int(q.get("weight", 1) or 1), q["id"]))
        if top:
            st = question_stats(list(fin(top[0]["id"]).values()))
            no_split = (f"no split today: the lenses broadly agree; top question median "
                        f"{st['median']:.0f}% on \"{top[0].get('text', '')}\"" if st["median"] is not None
                        else "no split today")
        else:
            no_split = "no split today: no questions of the day"
    sheet = {"day": day, "run_id": run_id, "day_type": day_type, "blocks": blocks, "no_split_line": anonymise(no_split),
             "bytes": 0}
    sheet = fit_sheet(sheet)
    return sheet


def render_split_sheet(sheet: dict) -> str:
    out = [f"# SPLIT SHEET — {sheet.get('day', '')} ({sheet.get('day_type', '')})", ""]
    if not sheet.get("blocks"):
        out.append(sheet.get("no_split_line") or "no split today")
        return "\n".join(out) + "\n"
    for n, bl in enumerate(sheet["blocks"], 1):
        out.append(f"## Block {n} [{bl['type']}{', undebated' if not bl['debated'] and bl['type'] != 'consensus' else ''}]")
        out.append(f"Question: {bl['question']}")
        out.append(f"Count phrase (copy exactly): {bl['count_phrase']}")
        out.append(f"Base case: {bl['base_case']['text']}" + (f" (data: \"{bl['base_case']['quote']}\")" if bl['base_case']['quote'] else ""))
        label = "Red-team case" if bl["type"] == "consensus" else "Minority view"
        out.append(f"{label}: {bl['minority_case']['text']}"
                   + (f" (data: \"{bl['minority_case']['quote']}\")" if bl['minority_case']['quote'] else ""))
        if bl.get("crux"):
            out.append(f"What it turns on: {bl['crux']}")
        if bl.get("crux_check"):
            c = bl["crux_check"]
            out.append(f"Data check: {c['resolved']}: {c['what_the_data_says']}" + (f" (\"{c['quote']}\")" if c.get("quote") else ""))
        so = bl.get("settles_on") or {}
        if so.get("observable") or so.get("by_date"):
            out.append(f"Settles on: {so.get('observable', '')}" + (f" by {so['by_date']}" if so.get("by_date") else ""))
        if bl.get("narrowed_on_data"):
            out.append("Note: new data narrowed this split today without settling it.")
        if bl.get("carried"):
            out.append(f"Carried: {bl['carried']}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def fit_sheet(sheet: dict, cap: int = SPLIT_SHEET_CAP) -> dict:
    """<= cap bytes rendered: quotes dropped first, then cases shortened evenly."""
    text = render_split_sheet(sheet)
    if nbytes(text) > cap:
        for bl in sheet["blocks"]:
            bl["base_case"]["quote"] = ""
            bl["minority_case"]["quote"] = ""
            if bl.get("crux_check"):
                bl["crux_check"]["quote"] = ""
        text = render_split_sheet(sheet)
    lim = 900
    while nbytes(text) > cap and lim > 80:
        lim = int(lim * 0.8)
        for bl in sheet["blocks"]:
            bl["base_case"]["text"] = clean_text(bl["base_case"]["text"], int(lim * 0.8))
            bl["minority_case"]["text"] = clean_text(bl["minority_case"]["text"], lim)
            if bl.get("crux_check"):
                bl["crux_check"]["what_the_data_says"] = clean_text(bl["crux_check"]["what_the_data_says"], int(lim * 0.5))
        text = render_split_sheet(sheet)
    sheet["bytes"] = nbytes(text)
    return sheet


# ── §11.3 lens notes ───────────────────────────────────────────────────────────────────

def lens_notes(takes: dict, active: list[str], locator, cap: int = LENS_NOTES_CAP) -> str:
    entries = []
    for a in active:
        t = takes.get(a)
        if not t:
            continue
        lines = [f"## {LENS_LABEL.get(a, 'lens').upper()}"]
        if t.get("summary"):
            lines.append(f"Read: {clean_text(t['summary'], 400)}")
        n = 0
        for c in t.get("claims") or []:
            q = (c.get("quote") or "").strip()
            if not q:
                continue
            loc = locator.locate(q)
            if loc["status"] == "verified" and loc.get("cls") == "data":
                lines.append(f"- {clean_text(c.get('claim', ''), 300)} (\"{q[:200]}\")")
                n += 1
            if n >= 2:
                break
        if t.get("novel"):
            lines.append(f"Others may miss: {clean_text(t['novel'], 300)}")
        p = t.get("prediction") or {}
        if p.get("text"):
            pr = p.get("probability")
            npr = norm_p(pr, [100])[0] if isinstance(pr, (int, float)) else None
            prs = f"{npr}%" if npr is not None else ""
            bits = [b for b in (prs, f"by {p['resolves_on']}" if p.get("resolves_on") else "") if b]
            lines.append(f"Prediction: {clean_text(p['text'], 300)}" + (f" ({', '.join(bits)})" if bits else ""))
        entries.append(lines)
    out, used = ["# LENS NOTES (labelled by lens; verified data claims only)", ""], 0
    used = nbytes("\n".join(out))
    per = max(400, (cap - used) // max(1, len(entries)))
    for lines in entries:
        text = "\n".join(lines)
        while nbytes(text) > per and len(lines) > 2:
            lines = lines[:-1]
            text = "\n".join(lines)
        if used + nbytes(text) + 2 > cap:
            break
        out.append(text)
        out.append("")
        used += nbytes(text) + 2
    return "\n".join(out).rstrip() + "\n"


# ── §11.5 checks before delivery ───────────────────────────────────────────────────────

SNAKE_NAMES = re.compile(r"(?i)\b(policy_analyst|user_agent|macro_strategist|ai_engineer)\b")
# Checked across the whole brief: upper-case names ('MACRO_STRATEGIST', 'ANALYST'), '### NAME' headings,
# 'Analyst: WRONG' labels, and 'the macro strategist' (multi-word names with 'the'). Title-case single names
# and bare spaced names stay limited to the two debate-fed sections, where real news cannot hit them.
UPPER_NAMES = re.compile(r"\b(TRADER|NARRATOR|BUILDER|ANALYST|SKEPTIC|POLICY[ _]ANALYST|USER[ _]AGENT|MACRO[ _]STRATEGIST"
                         r"|AI[ _]ENGINEER)\b(?!S\b)")
HEAD_NAMES = re.compile(r"(?im)^#{1,6}\s*\**\s*(trader|narrator|builder|analyst|skeptic|policy[ _]analyst|user[ _]agent"
                        r"|macro[ _]strategist|ai[ _]engineer)\b")
LABEL_NAMES = re.compile(r"(?m)^\s*(?:[-*•]\s*)?(?:\*\*)?(Trader|Narrator|Builder|Analyst|Skeptic|Policy Analyst|User Agent"
                         r"|Macro Strategist|AI Engineer)(?:\*\*)?\s*(?:\*\*)?:")
THE_NAMES = re.compile(r"(?i)\bthe (policy analyst|user agent|macro strategist|ai engineer)\b(?!s\b)")
SPACED_NAMES = re.compile(r"(?i)\b(policy analyst|user agent|macro strategist|ai engineer)\b(?!s\b)")
TITLE_NAMES = re.compile(r"\b(Trader|Narrator|Builder|Analyst|Skeptic|Policy Analyst|User Agent|Macro Strategist|AI Engineer)"
                         r"\b(?!s)(?!\.ai)(?! [A-Z][a-z])")
PROCESS = re.compile(r"(?i)\b(our|the) (agents?|lenses|analysts) (debated|conceded|challenged|voted)\b"
                     r"|\bvot(e|ed) of (the )?(agents|lenses)\b|\bour lenses\b|\bthe debate showed\b")
_NOFM = re.compile(r"\b(\d+) of (\d+)\b")


def brief_section(brief: str, name: str) -> str:
    out, on = [], False
    for line in (brief or "").splitlines():
        if re.match(r"^#{1,4} ", line):
            h = re.sub(r"\*", "", re.sub(r"^#+\s*", "", line)).strip().upper()
            on = name in h
            continue
        if on:
            out.append(line)
    return "\n".join(out)


def brief_checks(brief: str, sheet: dict | None) -> dict:
    whole = []
    for rx in (SNAKE_NAMES, UPPER_NAMES, HEAD_NAMES, LABEL_NAMES, THE_NAMES):
        whole += [(m.start(), m.group(0).strip()) for m in rx.finditer(brief or "")]
    seen_at, agent_names = set(), []
    for st, g in sorted(whole):
        if any(abs(st - s0) < 3 for s0 in seen_at):
            continue
        seen_at.add(st)
        agent_names.append(g)
    for sec in ("WHAT IT MEANS", "WHERE THE VIEWS SPLIT"):
        body = brief_section(brief, sec)
        hits = [(m.start(), m.group(0)) for m in TITLE_NAMES.finditer(body)]
        hits += [(m.start(), m.group(0)) for m in SPACED_NAMES.finditer(body)
                 if not any(st == m.start() for st, _ in hits)]
        agent_names += [f"{g} ({sec})" for _, g in sorted(hits)]
    split = brief_section(brief, "WHERE THE VIEWS SPLIT")
    allowed = set()
    blocks = (sheet or {}).get("blocks") or []
    for bl in blocks:
        c = bl.get("counts") or {}
        for k in ("majority", "minority"):
            allowed.add((int(c.get(k, -1)), int(c.get("n", -1))))
    mism = [m.group(0) for m in _NOFM.finditer(split) if (int(m.group(1)), int(m.group(2))) not in allowed]
    n_blocks = len(blocks)
    consensus = any(bl.get("type") == "consensus" for bl in blocks)
    block_issues = []
    if n_blocks > 3:
        block_issues.append(f"{n_blocks} blocks in the split sheet")
    if consensus and "case against" not in split.lower():
        block_issues.append("consensus day without 'case against'")
    if (sheet or {}).get("day_type") == "split_unpaired" and "no real split" in split.lower():
        block_issues.append("split_unpaired day prints 'No real split'")
    return {"agent_names": agent_names, "count_mismatch": mism, "blocks": block_issues,
            "process_words": [m.group(0) for m in PROCESS.finditer(brief or "")]}


# ── §14.1 adapters for Phase B readers ─────────────────────────────────────────────────

def legacy_moves(agent: str, responses: list[dict]) -> list[dict]:
    """[{question_id, from, to, delta, reason}] from this agent's gated debate moves."""
    out = []
    for r in responses:
        if r.get("agent") != agent:
            continue
        mv = r["move"]
        out.append({"question_id": r["question_id"], "from": float(mv["take"]), "to": float(mv["gated"]),
                    "delta": float(mv["gated"] - mv["take"]), "reason": (r.get("data") or {}).get("reason", "")})
    return out


def legacy_response(agent: str, responses: list[dict]) -> dict | None:
    mine = [r for r in responses if r.get("agent") == agent]
    if not mine:
        return None
    verdict = max((r["data"].get("verdict", "hold") for r in mine), key=lambda v: VERDICT_RANK.get(v, 0))
    return {"verdict": verdict, "text": " ".join(r["data"].get("reason", "") for r in mine).strip(),
            "final_positions": [{"question_id": r["question_id"], "probability": r["move"]["gated"],
                                 "reason": r["data"].get("reason", "")} for r in mine]}
