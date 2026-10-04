"""Phase C debate logic: pure functions, no I/O beyond what is passed in (docs/v2/phase-c-spec.md).

  gate_questions   §2.4 programmatic question gate (no LLM)
  lens_extras      §3 item 3 YOUR LENS DATA per agent (LENS_RAW; same rules as tests/lens_extras_probe.py)
  pair             §4 pairing by widest probability gap; day types debate / split_unpaired / consensus
  pick_red_team    §4.3 the consensus-day red-team agent
  budget_pairs     §1.1 pairs and crux check that fit the call budget (the last pair goes before the crux check)
  excerpts         §5.2 the lines around each verified quote, 4 KB shared between the sides
  challenge_checks §5.4 length, persona leakage, agreement opener
  crux_terms       §6 numbers, entities and metric words from the cruxes
  crux_keywords    §6 the cruxes' lower-case content words (event and judgment questions)
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
# The raw Polymarket and Kalshi blocks are no lens's data (sixth review, 2026-10-04): take.md asks for one
# lens quote per position, so trader would cite a market odds line on every question, the anchoring the
# first probe measured (9 of 9 at 60 %). They come back only if the e1 probe (§15.0) measures them. Odds
# lines elsewhere (World Monitor's Polymarket feed, SECTION 8) are class 'market' (ev_class), never data.
LENS_RAW: dict[str, list[str]] = {
    "macro_strategist": [
        "# World Monitor Intelligence",
        r"news~\bFed\b|FOMC|\bCPI\b|inflation|payroll|jobs report|tariff|treasur|\byields?\b|\bdollar"
        r"|\bDXY\b|recession|\bGDP\b|rate cut|rate hike|\bECB\b|\bBOJ\b|Powell|\boil\b|sanction|shutdown"
        r"|election|midterm|geopolit|China|Iran|Russia|Ukraine|Israel",
        "## ECONOMICS", "## POLITICS",
    ],
    "trader": [
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


# Resolution verbs of event questions: (question pattern, the forms a news line that reports the event uses).
# An announcement is also reported as 'X has new …' ('Microsoft has new AI privacy rules for schools').
_EVENT_VERBS = (
    (r"announc|unveil|introduc|launch|roll\s+out|releas|publish|debut|ship\b",
     r"announc\w*|unveil\w*|introduc\w*|launch\w*|roll(?:s|ed)?\s+out|releas\w*|publish\w*|debut\w*|ship(?:s|ped)"
     r"|ha(?:s|ve)\s+(?:a\s+)?new|adds?|added"),
    (r"approv|authori[sz]|greenlight|clear\b", r"approv\w*|authori[sz]\w*|greenl\w+|clear(?:s|ed)"),
    (r"\bpass|enact|\bsign\b|adopt|ratif", r"pass(?:es|ed)|enact\w*|sign(?:s|ed)|adopt\w*|ratif\w*"),
    (r"\bban\b|prohibit|outlaw", r"ban(?:s|ned)?|prohibit\w*|outlaw\w*"),
    (r"\bcut\b|\blower\b|\breduce\b", r"cuts?|lower(?:s|ed)|reduc(?:es|ed)"),
    (r"\bhike\b|\braise\b", r"hike[sd]?|rais(?:es|ed)"),
    (r"resum|reopen|restart|restor", r"resum\w*|reopen\w*|restart\w*|restor\w*"),
    (r"\bhalt|\bpause|suspend|shut\s+down", r"halt\w*|paus\w*|suspend\w*|shut(?:s)?\s+down"),
    (r"\blist\b", r"list(?:s|ed)"),
    (r"acquir|\bbuy\b|merge", r"acquir\w*|bought|buys|merg(?:es|ed)"),
    (r"sanction|impose|\blift\b", r"sanction\w*|impos(?:es|ed)|lift(?:s|ed)"),
    (r"deploy|dispatch|\bsend\b", r"deploy\w*|dispatch\w*|sen(?:ds|t)"),
)
# A line that says the event may happen, was discussed or did not happen does not report it.
_HEDGE = re.compile(r"\?|\b(?:will|would|could|may|might|plans?|planning|planned|expected|expects?|consider\w*|weigh\w*"
                    r"|mull\w*|propos\w*|seeks?|seeking|aims?|delay\w*|reject\w*|den(?:y|ies|ied)|not|no|never|won't"
                    r"|fail\w*|rumou?r\w*|reportedly|talks?|discuss\w*|eyes|eyeing|hints?|teases?|options?|soon)\b", re.I)
# Words of an event question that name no particular object.
_EVENT_GENERIC = {"major", "new", "concrete", "specific", "official", "formal", "formally", "publicly", "public", "any",
                  "another", "first", "least", "platform", "company", "companies", "provider", "providers", "firm",
                  "government", "country", "least", "one", "some", "further", "additional"}


def _object_terms(text: str) -> set[str]:
    """An event question's object terms: acronyms (2+ capitals) as written, other words of 3+ letters as their
    first 5 letters, without stop words, generic words and dates."""
    out = set()
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9]*", text or ""):
        if re.fullmatch(r"[A-Z][A-Z0-9]+", tok):
            out.add(tok)
            continue
        low = tok.lower()
        if len(low) < 3 or low in MARKET_STOP or low in _EVENT_GENERIC or _DATE_TOKEN.match(tok):
            continue
        out.add(low[:5])
    return out


def settled_line(question: str, locator) -> str:
    """The first package news line that already reports the event an event question asks about, or ''. It has
    the question's resolution verb in a reporting form ('announces', 'launched', 'has new'), more than half (and
    at least two) of the question's object terms (_object_terms, without the verb), every versioned name of the
    question as written ('GPT-6'), no hedge ('weighs', 'plans to', 'not', '?'), and is a data line (not social,
    not a market line). On the 09-10 package it finds 'Microsoft has new AI privacy rules for schools' for the
    school-privacy question and nothing for the Hormuz one."""
    if locator is None or "package" not in getattr(locator, "lines", {}):
        return ""
    best = None
    for qrx, lrx in _EVENT_VERBS:
        m = re.search(qrx, question or "", re.I)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), m.end(), lrx)
    if best is None:
        return ""
    verb_word = re.match(r"\S*", (question or "")[best[0]:]).group(0)
    terms = _object_terms(question) - _object_terms(verb_word)
    if len(terms) < 2:
        return ""
    line_rx = re.compile(rf"\b(?:{best[2]})\b", re.I)
    # A versioned name in the question ('GPT-6', 'Llama 4') has to be on the line as written: 'GPT-Live-1' does
    # not report GPT-6. Dates are not names.
    undated = re.sub(r"\b\d{4}-\d\d-\d\d\b|\b(?:by|on|before)\s+[A-Z][a-z]+\.?\s+\d{1,2}(?:,\s*\d{4})?", " ",
                     question or "")
    versions = [v for v in re.findall(r"\b[A-Za-z][\w.]*[-\s]?\d+(?:\.\d+)?\b", undated)
                if not re.fullmatch(r"(?:19|20)\d\d", v.split()[-1])]
    lines = locator.lines["package"]
    mkeys = _market_keys(locator)
    for i, ((sec, cls), line) in enumerate(zip(locator.labels("package"), lines)):
        s = line.strip()
        if not s.startswith("- ") or cls != "data" or market_line_in(sec, lines, i, mkeys):
            continue
        body = _URL.sub(" ", re.sub(r"^\s*-\s*(?:\[[^\]]*\]\s*)?", "", s))
        if not line_rx.search(body) or _HEDGE.search(body):
            continue
        flat = re.sub(r"[\s\-‐-―]", "", body.lower())
        if any(re.sub(r"[\s\-]", "", v.lower()) not in flat for v in versions):
            continue
        shared = len(terms & _object_terms(body))
        if shared >= 2 and 2 * shared > len(terms):
            return s
    return ""


def gate_questions(questions: list[dict], day: str, locator, open_ledger: list[dict] | None = None,
                   active: list[str] | None = None, depth: str = "normal", market: list[str] | None = None) -> dict:
    """Apply the §2.4 gate. Returns {kept, dropped, notes, needs_reask}. `locator` is an
    evidence.Locator over package, raw, view and social (rule 4). Kept questions get ids q1… in
    order, clamped weights and cleaned lenses. `market`: the run's prediction-market odds lines
    (market_lines()); a question one of them already prices is dropped (rule 7: the spread probe's
    takes anchored on a quoted market price, 9 of 9 at 60 %). A question whose settled_quote (the package
    line that already reports what settles it) is found in the package is dropped too (rule 8), and so is an
    event question whose event a package news line already reports (rule 9, settled_line)."""
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
        if reason is None and kind != "judgment":
            # rule 8: the package already settles it (09-10 c1: 'Will a major AI provider announce school-specific
            # privacy rules by 09-30?' beside 'Microsoft has new AI privacy rules for schools'; the widest debate
            # of the day argued over whether that item already settled it). Triage names the line; a line that
            # is found drops the question, one that is not found is a note.
            sq = (q.get("settled_quote") or "").strip()
            if sq:
                st = evidence.locate(sq, locator)["status"]
                if st in ("verified", "partial"):
                    reason = f"the package already settles it: {sq[:100]!r}"
                else:
                    notes.append(f"settled_quote not found ({st}) on {text[:60]!r}")
        if reason is None and kind == "event":
            # rule 9: the same, found by the program (seventh review, 2026-10-04: the 09-10 c6 triage left
            # settled_quote empty on the school-privacy question beside 'Microsoft has new AI privacy rules for
            # schools', and that question led WHERE THE VIEWS SPLIT). A package news line that reports the
            # question's event (its resolution verb, two of its object terms, no hedge) drops it.
            hit = settled_line(text, locator)
            if hit:
                reason = f"the package already reports it: {hit[:100]!r}"
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
        for key in ("metric", "comparator", "threshold", "baseline_quote", "settled_quote", "resolves_on",
                    "settles_with", "carried_from", "domain"):
            q.setdefault(key, "")
        out.append(q)
    dropped.sort(key=lambda d: d["order"])
    return {"kept": out, "dropped": [{k: v for k, v in d.items() if k != "order"} for d in dropped],
            "notes": notes, "needs_reask": len(out) <= 1}


# ── §1.1 budget ────────────────────────────────────────────────────────────────────────

def budget_pairs(used: int, budget: int, synth_calls: int, depth_target: int) -> tuple[int, bool]:
    """(pairs, crux_check) that fit: a pair costs 4 calls, the crux check 1. The crux check is kept while at
    least one pair fits with it: a day gives up its last pair before the crux check (decision 2026-10-04,
    after the 09-10 c1 replay: 3 pairs left the crux check out at 22 of 24 calls, and no debate on 09-10 or
    09-11 was useful; the crux check is the only way a closure on data is confirmed, §8). Only when no pair
    fits with it is it dropped for a pair."""
    free = budget - used - synth_calls
    with_crux, without_crux = max(0, (free - 1) // 4), max(0, free // 4)
    if with_crux >= 1:
        return min(depth_target, with_crux), True
    return min(depth_target, without_crux), False


# ── §4 pairing ─────────────────────────────────────────────────────────────────────────

def _ok(e: dict) -> bool:
    return e.get("status") in ("verified", "partial")


def eligible_agents(q: dict, p: dict, evq: dict, active: list[str]) -> list[str]:
    """Agents that may be a debate endpoint on q: a verified or partial quote, and on any question but a
    judgment one a data quote. A market odds line is class 'market' (ev_class), not data: an endpoint never
    rests on a market price alone."""
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


def _lone_end(vals, gap_min: int) -> str:
    """'hi' or 'lo' when one take alone carries the range to gap_min, else ''. Both must hold: without that take
    the range is under gap_min, and the take is an outlier (beyond the 1.5 x IQR fence of all takes), so a spread
    that is merely even and just over gap_min (28, 32, ..., 46, 50) is not one lens's split."""
    v = sorted(float(x) for x in vals)
    if len(v) < 3 or v[-1] - v[0] < gap_min:
        return ""
    q = statistics.quantiles(v, n=4, method="inclusive")
    iqr = q[2] - q[0]
    drop_hi, drop_lo = v[-2] - v[0], v[-1] - v[1]
    if drop_hi < gap_min and drop_hi <= drop_lo and v[-1] > q[2] + 1.5 * iqr:
        return "hi"
    if drop_lo < gap_min and v[0] < q[0] - 1.5 * iqr:
        return "lo"
    return ""


def lone_lens(vals, gap_min: int) -> bool:
    """§4.2: the take range reaches gap_min only through one lens, an outlier whose removal leaves a range under
    gap_min. One draw of one lens is not a split worth a debate slot: 09-11 c11 q3 (OpenAI Pro) staged a pair on a
    1-of-9 outlier (8 of 9 at 31-42%, IQR 5, one lens at 58: range 27, 11 without it), while c10 asked the same
    topic at range 19 with a yes majority, and the pair counts over the package's replays went 2, 1, 2 (§20.7 #83).
    A minority of two (58 and 60) or one dissenter on each side survives dropping any one lens and still pairs."""
    return bool(_lone_end(vals, gap_min))


def lone_outlier(vals_by_agent: dict, gap_min: int) -> dict | None:
    """The lens that alone carries a question's take range to gap_min (lone_lens), its take, the range, the range
    without it and the median; None otherwise."""
    end = _lone_end(list(vals_by_agent.values()), gap_min)
    if not end:
        return None
    v = sorted(vals_by_agent.values())
    pick = max if end == "hi" else min
    far = pick(vals_by_agent.items(), key=lambda kv: kv[1])
    return {"agent": far[0], "p": int(far[1]), "range": int(v[-1] - v[0]),
            "trimmed_range": int(v[-2] - v[0] if end == "hi" else v[-1] - v[1]), "median": float(statistics.median(v))}


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
                "candidates_considered": 0, "unpaired": [], "red_team": None, "eligible": elig, "lone_outliers": []}

    def quotes(a, qid):
        return {qnorm(e.get("quote")) for e in evq.get(a, {}).get(qid, []) if _ok(e)}

    def vcount(a, qid):
        return sum(1 for e in evq.get(a, {}).get(qid, []) if _ok(e))

    cands, ranges, lone = [], {}, []
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
        # A range that one lens alone carries to gap_min gets no debate slot and no undebated block: it is not
        # wide (no split_unpaired, no unpaired entry), and on a day with no other split it is a consensus day,
        # where the red team (furthest eligible agent from the median) can argue it in one call (§4.2, §20.7 #83).
        lo_x = lone_outlier({a: p[a][qid] for a in active if qid in p[a]}, gap_min)
        if lo_x:
            lone.append({"question_id": qid, **lo_x})
            continue
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
    # A pair whose gap is under GAP_MIN + 2 x FREE_MOVE can fall below GAP_MIN on the two free moves alone, with
    # no qualifying evidence, and stop being a held split (09-11 q3 Hormuz, 22 -> 13 on two free moves: the
    # only debated split lost). Such pairs rank after every pair with that margin, whatever their score; they
    # are still debated when slots remain (§4.2, §20.7 #78).
    robust = gap_min + 2 * FREE_MOVE
    cands.sort(key=lambda c: (c[1] < robust, -c[0], -c[1], -c[2], c[3], c[4], c[5]))
    if off_reason:
        target = 0
    # load_skipped: questions with a candidate the load cap skipped (in either pass) ahead of a pair accepted later
    # in that pass. Such a question lost its slot to the cap, not to a higher-ranked split, so it is unpaired even
    # when the day reaches its target (§4.3/§11.1; review 2026-10-04: q2/q3 skipped on load while q4 took the
    # slot, and both direction splits left the sheet).
    pairs, used_q, load, load_skipped = [], set(), Counter(), set()
    for cap in (1, 2):
        skipped = []
        for c in cands:
            if len(pairs) >= target:
                break
            if c[3] in used_q:
                continue
            if load[c[4]] >= cap or load[c[5]] >= cap:
                skipped.append(c[3])
                continue
            pairs.append(c)
            used_q.add(c[3])
            load[c[4]] += 1
            load[c[5]] += 1
            load_skipped.update(skipped)
            skipped = []
    out_pairs = [{"question_id": c[3], "high": c[5], "low": c[4], "p_high": p[c[5]][c[3]], "p_low": p[c[4]][c[3]],
                  "gap": c[1], "score": c[0], "both_lenses": c[6], "repeat_of_yesterday": c[7]} for c in pairs]
    lone_q = {x["question_id"] for x in lone}
    base = {"depth": depth, "target": target, "gap_min": gap_min, "candidates_considered": len(cands),
            "eligible": elig, "lone_outliers": lone}
    if out_pairs:
        # A question with a candidate pair that the load cap left out while slots remained (its debaters already
        # argue two pairs), or that the cap skipped ahead of a lower-ranked pair that then took the slot, is unpaired, not 'more splits than slots': §11.1 gives it the split_unpaired bar.
        # A question whose take range is >= gap_min but has no candidate pair at all (its dissenters fail
        # eligibility) is unpaired for the same reason it would be on a split_unpaired day: it must not lose its
        # block only because another question formed a pair (review 2026-10-04).
        paired, cand_q = {c[3] for c in pairs}, {c[3] for c in cands}
        unp = []
        capped = (cand_q if len(pairs) < target else load_skipped) - paired
        for qid in sorted(capped, key=lambda x: (-ranges[x], x)):
            unp.append({"question_id": qid, "range": int(ranges[qid]), "reason": UNPAIRED_LOAD_CAP})
        for qid in sorted((q for q, r in ranges.items() if r >= gap_min and q not in cand_q and q not in lone_q),
                          key=lambda x: (-ranges[x], x)):
            unp.append({"question_id": qid, "range": int(ranges[qid]), "reason": UNPAIRED_NO_ELIGIBLE})
        unp.sort(key=lambda u: (-u["range"], u["question_id"]))
        return {"day_type": "debate", "pairs": out_pairs, "unpaired": unp, "red_team": None, **base}
    wide = sorted(((qid, r) for qid, r in ranges.items() if r >= gap_min and qid not in lone_q),
                  key=lambda x: (-x[1], x[0]))
    if off_reason:
        if wide:
            return {"day_type": "split_unpaired", "pairs": [], "red_team": None,
                    "unpaired": [{"question_id": qid, "range": int(r), "reason": off_reason} for qid, r in wide[:3]],
                    **base}
        return {"day_type": "consensus", "pairs": [], "unpaired": [], "red_team": None, **base}
    if cands or wide:
        unp = []
        for qid, r in wide[:3]:
            why = "the call budget leaves no pair" if cands else UNPAIRED_NO_ELIGIBLE
            unp.append({"question_id": qid, "range": int(r), "reason": why})
        return {"day_type": "split_unpaired", "pairs": [], "unpaired": unp, "red_team": None, **base}
    # A one-lens question is the day's only split: the red team argues it (widest range, then weight) rather than
    # the top-weight question, or it leaves no pair, no block and no red-team call and drops out of the brief
    # (§4.2/§4.3, review 2026-10-04).
    qs = [q for q in questions if q["id"] in lone_q] or [q for q in questions if q["id"] in ranges] or list(questions)
    top = sorted(qs, key=lambda q: ((-ranges.get(q["id"], 0), -int(q.get("weight", 1) or 1)) if lone_q else
                                    (-int(q.get("weight", 1) or 1), -ranges.get(q["id"], 0)), q["id"]))[0]
    rt = pick_red_team(top, p, evq, active)
    return {"day_type": "consensus", "pairs": [], "unpaired": [], "red_team": rt, **base}


UNPAIRED_LOAD_CAP = "load cap: its debaters already argue another pair"
UNPAIRED_NO_ELIGIBLE = "no eligible pair straddles the median with the gap (evidence missing or social-only)"


def dropped_unpaired(res: dict, full: dict, why: str, p: dict | None = None) -> list[dict]:
    """§4.3/§11.1. The questions whose candidate pair the depth target would have debated but the day did not:
    `res` is pair() at the budgeted target, `full` pair() at the depth target, `why` 'budget' or 'ceiling'.
    Returns res's own unpaired (load cap, including a question the cap skipped ahead of a pair that took the
    slot) plus every question paired, or load-capped, in `full` that `res` left without a pair, one entry each, `range` the take range from `p` ({agent: {qid: int}}; the pair's gap
    without it) (fifth review: budget_pairs(12, 24, 2, 3) gives up the last pair on a
    normal day, and its split vanished from the brief when the take range was under 40)."""
    paired = {x["question_id"] for x in res.get("pairs") or []}
    out = {u["question_id"]: u for u in res.get("unpaired") or [] if u["question_id"] not in paired}
    reason = ("the call ceiling leaves no pair" if why == "ceiling" else
              "the call budget gave up this pair (depth target would have debated it)")
    def rng(qid, fallback):
        vals = [v[qid] for v in (p or {}).values() if qid in v]
        return int(max(vals) - min(vals)) if vals else int(fallback)
    for x in full.get("pairs") or []:
        if x["question_id"] not in paired and x["question_id"] not in out:
            out[x["question_id"]] = {"question_id": x["question_id"], "range": rng(x["question_id"], x["gap"]),
                                     "reason": reason}
    for u in full.get("unpaired") or []:
        if u["question_id"] not in paired and u["question_id"] not in out:
            out[u["question_id"]] = dict(u)
    return sorted(out.values(), key=lambda u: (-u["range"], u["question_id"]))


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


_JOIN = r"[ _-]"    # joiner inside a multi-word agent name: 'macro strategist', 'macro_strategist', 'macro-strategist'


def _name_forms(agent: str) -> list[str]:
    """Regex forms that name an agent: multi-word names in any case, joined by a space, an underscore or
    a hyphen ('policy_analyst', 'the policy analyst', 'AI-engineer', 'User Agent', 'MACRO STRATEGIST',
    'Policy-Analyst' as one name, not 'Policy-' plus 'Analyst'); single
    words in Title Case ('Trader') and UPPER ('TRADER'). Lower-case single words ('a trader would sell',
    'analyst consensus') are common nouns here; anonymise() catches 'the skeptic argues' separately."""
    w = agent.split("_")
    if len(w) > 1:
        return ["(?i:" + _JOIN.join(re.escape(x) for x in w) + ")"]
    return [re.escape(agent.capitalize()), re.escape(agent.upper())]


_NAMES_ALT = "|".join(f for a in sorted(AGENTS, key=len, reverse=True) for f in _name_forms(a))
_NAME_RX = re.compile(r"(?:(?P<at>@)(?i:" + "|".join(re.escape(a) for a in sorted(AGENTS, key=len, reverse=True))
                      + r")|(?P<the>\b[Tt]he\s+)?\b(?:" + _NAMES_ALT + r"))(?!s\b)(?!\.ai\b)(?!\w)")
# A lower-case role noun used as a name in debate-written text: 'the skeptic argues', 'the trader's
# read', 'the analyst is right'. Followed by a possessive, a common verb, a word ending in a single s
# (argues, overstates; not 'consensus', 'class', 'thesis', 'basis'), a clause break ('the skeptic,',
# 'with the analyst;', 'I disagree with the skeptic.', 'The skeptic? Wrong.'), a relative or participle
# continuation ('the skeptic who argues', 'the trader overweighting momentum'), or another role ('the
# skeptic and the trader'); or led by 'As the' ('As the
# skeptic, I see 30%') or 'As a' ('As a skeptic, I put this at 30%'). Also a role noun followed by a word that makes it a camp ('the skeptic case', 'the
# trader camp reads', 'the skeptic view'), a '-lens'/'-side' compound ('the trader-lens view', consumed so
# 'the other view view' folds to 'the other view'), and one adverb before the verb ('the skeptic here overstates').
_ROLE_VERBS = (r"is|was|has|had|would|will|can|could|should|might|may|must|does|did|says|said|argues|argued|"
               r"thinks|thought|believes|claims|claimed|expects|expected|sees|saw|reads|read|notes|noted|"
               r"overstates|understates|misses|missed|ignores|ignored|concedes|conceded|holds|held|puts|put|"
               r"assumes|assumed|treats|treated|wants|insists|underweights|overweights|relies|leans|cites|cited|"
               r"flagged|warned|doubted|stressed|overstated|understated|relied|leaned")
_ROLES = r"skeptic|trader|narrator|builder|analyst"
_ROLE_NOUNS = r"view|case|camp|side|lens|position|read|argument|take"
# The one adverb before the verb: here/still/also/now/just/even or any single '-ly' word, bare or in
# parentheses ('the skeptic sharply overstates', 'the skeptic (rightly) flags'). '-ly' adjectives and nouns
# that sit before a noun ('the analyst weekly notes', 'the trader daily flows') are not adverbs.
_ADV_WORD = (r"(?:here|still|also|now|just|even|(?!(?:daily|weekly|monthly|quarterly|yearly|hourly|nightly|"
             r"early|family|rally|supply|assembly|ally)\b)[a-z]+ly)")
_ROLE_ADV = r"(?:(?:" + _ADV_WORD + r"|\(" + _ADV_WORD + r"\))\s+)?"
# A participle after the role ('the trader overweighting momentum'); common '-ing' nouns that follow a
# role used as a noun ('the analyst meeting', 'the trader pricing model') are not participles.
_ROLE_ING = (r"(?!(?:meeting|briefing|rating|pricing|ranking|morning|evening|funding|setting|thing|building|"
             r"listing|filing|holding|reading|offering|spring|string|training|trading|timing|ceiling)s?\b)[a-z]{2,}ing\b")
_ROLE_RX = re.compile(r"\b(?P<the>[Tt]he)\s+(?:" + _ROLES + r")"
                      r"(?:[-‑](?:lens|side)\b(?:['’]s\b)?|(?=['’]s\b)['’]s|(?=\s+(?:" + _ROLE_NOUNS + r")s?\b)"
                      r"|(?=\s+" + _ROLE_ADV + r"(?:" + _ROLE_VERBS + r")\b)|(?=\s+" + _ROLE_ADV + r"[a-z]+[^siu\s]s\b)"
                      r"|(?=\s*(?:[,;:)?!]|\.(?!\w)))|(?=\s+(?:who|that|which)\b)|(?=\s+" + _ROLE_ADV + _ROLE_ING + r")"
                      r"|(?=\s+and\s+the\s+(?:" + _ROLES + r"|other view)\b))"
                      r"|(?<=\bAs )(?P<as_the>the|an?)\s+(?:" + _ROLES + r")\b(?!['’]s\b)(?![ \t]+[a-z])")
# Two names in a row, after the passes above: 'the other view and the other view' -> 'one view and the other';
# a role left second in the pair ('the other view and the trader disagree') goes with it.
_PAIR_RX = re.compile(r"\b(?P<the>[Tt]he) other view\s+and\s+the\s+(?:other view|" + _ROLES + r")\b(?P<poss>['’]s)?")


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
        return ("The other view" if (m.group("the") or "").startswith("T") else "the other view") + poss

    def sub_pair(m):
        one = "One view and the other" if m.group("the").startswith("T") else "one view and the other"
        return one + (m.group("poss") or "")
    t = _NAME_RX.sub(sub, text or "")
    t = _ROLE_RX.sub(sub_role, t)
    t = _PAIR_RX.sub(sub_pair, t)
    t = re.sub(r"\b(other view)\s+view\b", r"\1", t)   # 'Policy-Analyst view' -> 'the other view', not '... view view'
    t = re.sub(r"(^|[.!?]\s+)(the other view|one view and)", lambda m: m.group(1) + m.group(2)[0].upper() + m.group(2)[1:], t)
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


def _token_parts(tok: str) -> list[str]:
    """A token as the entity candidates it stands for. 'OIRA/White' -> 'OIRA', 'White' (alternatives);
    a hyphenated compound with a lower-case part ('Astra-driven', 'Pro-signup', 'OpenAI-confirmed') -> its
    capitalised parts, since the compound itself never occurs elsewhere (the 09-11 crux search found 0 hits
    on 'Astra-driven'); a compound of proper parts ('GPT-5', 'US-China', 'Llama-3.1') stays whole."""
    if "/" in tok:
        return [p for piece in tok.split("/") if piece for p in _token_parts(piece.strip(".-"))]
    parts = [p for p in tok.split("-") if p]
    if len(parts) > 1 and any(p[0].islower() for p in parts):
        return [p for p in parts if not p[0].islower()]
    return [tok]


# A compass word is a common word, but before a name it makes a different place: 'South Korea' is not 'North
# Korea', 'South China' is not 'China'. entities() keeps such a compound whole instead of dropping the qualifier
# and pinning the bare name (eighth review follow-up, 2026-10-04: the subject 'Korea' of 'Will South Korea
# announce a naval deployment ...?' pinned 'North Korea test-fires ballistic missile ...').
COMPASS = {"North", "South", "East", "West", "Northern", "Southern", "Eastern", "Western"}


def entities(text: str, vocab: set[str] | None = None, mid_common: bool = True) -> list[str]:
    """Named things in a text: tokens with a capital or a digit, minus stop words, dates and numbers. A Title-case
    token (capital, then lower case, no digit) that is a common English word is not an entity anywhere in the
    sentence (seventh review, 2026-10-04: a mid-sentence 'House', 'Treasury' or 'Senate' in a crux let 'House
    passes defense appropriations bill' qualify on an unrelated event question); one that the corpus also uses
    in lower case is dropped only at a sentence start. All-caps tokens and tokens with digits always stay.
    `mid_common=False` keeps the older rule (common words dropped only at a sentence start), for recall where a
    missed name costs more than a generic one (market_match). A dropped COMPASS word directly before a name
    (one space) stays on it: 'South Korea', never the bare 'Korea' that also names 'North Korea'."""
    vocab = vocab or set()
    common_words()          # fail loudly when the list is missing
    out = []
    qual = None             # (qualifier, end offset) of a dropped 'South' / 'North' right before this token
    for m in _TOKEN.finditer(text or ""):
        before = (text[:m.start()]).rstrip()
        initial = not before or before[-1] in ".!?:;\n\"'(" or before.endswith("—")
        prev, qual = qual, None
        joined = bool(prev and text[prev[1]:m.start()] == " ")
        for k, tok in enumerate(_token_parts(m.group(0).strip(".-/"))):
            tok = tok.strip(".-/")
            if len(tok) < 3 or _is_number_token(tok):
                continue
            if not (re.search(r"[A-Z]", tok) or re.search(r"\d", tok)):
                continue
            if tok.lower() in STOP or _DATE_TOKEN.match(tok):
                continue
            title = tok[0].isupper() and tok[1:].islower() and not re.search(r"\d", tok)
            start = bool(initial or k)
            if title and ((is_common(tok) and (start or mid_common)) or (start and tok.lower() in vocab)):
                if tok in COMPASS and tok == m.group(0):
                    qual = (tok, m.end())
                continue
            out.append(f"{prev[0]} {tok}" if joined and k == 0 else tok)
    out += _HANGUL.findall(text or "")
    return list(dict.fromkeys(out))


METRIC_SET = set(METRIC_WORDS)


# A versioned product name carries its version as digits ('GPT-6.5', 'Llama-3.1-70B', 'F-35'): a name, not a
# figure. evidence.numbers reads '6.5' and '70B' (70 billion) out of them (tenth review, 2026-10-04: on 09-11
# c14 q3 'GPT-6 Astra' sat on the only crux hit), so the crux terms and the lines they are matched against drop
# a letter-led token with a hyphenated digit part before reading numbers. A figure after a space ('Brent 85.2')
# stays a number.
_VERSIONED = re.compile(r"(?<![\w$€£₩.])[A-Za-z][A-Za-z0-9]*"
                        r"(?:[-‐‑–][A-Za-z0-9]*\d[A-Za-z0-9]*(?:\.\d+)*)+(?![\w%])")


def crux_numbers(text: str) -> list[dict]:
    """evidence.numbers without the digits of versioned product names (_VERSIONED): the numbers a crux term or a
    crux-matched line carries."""
    return evidence.numbers(_VERSIONED.sub(" ", text or ""))


def crux_terms(texts: list[str], vocab: set[str] | None = None) -> dict:
    """Numbers, entities and metric words of the cruxes. An entity and a metric word from the same token
    ('TVL', 'Volume') count once, as the metric word: a generic metric is not a specific term."""
    nums, ents, mets = [], [], []
    for t in texts:
        if not t:
            continue
        nums += crux_numbers(t)
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


# Words every event crux uses whatever it is about ('an official announcement reported by a major wire service
# before the cutoff'): they say nothing about what this disagreement turns on, so they are never crux keywords.
CRUX_BOILERPLATE = {
    "official", "officially", "announce", "announced", "announces", "announcement", "announcements", "report",
    "reports", "reported", "reporting", "statement", "statements", "government", "public", "publicly", "page",
    "record", "records", "document", "documentation", "whether", "resolution", "resolve", "resolves", "cutoff",
    "date", "dated", "deadline", "before", "after", "until", "within", "without", "specific", "specifying",
    "specifies", "specify", "defined", "define", "concrete", "named", "naming", "qualifying", "qualify", "major",
    "wire", "service", "authoritative", "evidence", "confirm", "confirmed", "confirms", "verifiable", "would",
    "could", "should", "still", "again", "enough", "sufficient", "immediate", "provides", "provide", "shows",
    "showing", "says", "said", "more", "other", "every", "least", "following", "followed", "there", "their", "than",
    "then", "into", "onto", "about", "also", "only", "some", "such", "being", "been", "have", "does", "will",
    "shall", "must", "made", "make", "makes", "under", "over", "while", "where", "which", "potentially",
    "constitute", "constitutes", "item", "items", "either", "neither", "both", "each", "none", "ruling", "rules",
    "rule", "explicit", "explicitly", "clear", "clearly", "formal", "formally", "credible", "credibly", "outlet",
    "outlets", "source", "sources", "rather", "level", "claim", "claims", "observable", "observed",
}

# Time words and generic verbs and nouns a crux uses to frame any outcome ('within the next week', 'remains closed
# to new users', 'accepting', 'offering ... again'): they say nothing about what this disagreement turns on, so
# they are never crux keywords either (ninth review, 2026-10-04: on 09-11 c9 q5 'next' was the only keyword on
# 'GPT-6 Astra: The next generation in intelligence for work - OpenAI', which was gated as crux_data).
CRUX_GENERIC = {
    "next", "week", "weeks", "weekly", "weekend", "month", "months", "monthly", "year", "years", "yearly", "hour",
    "hours", "minute", "minutes", "time", "times", "timing", "soon", "later", "last", "past", "recent", "recently",
    "current", "currently", "early", "earlier", "late", "ahead", "upcoming", "ongoing", "already", "longer",
    "remain", "remains", "remained", "remaining", "stay", "stays", "keep", "keeps", "continue", "continues",
    "user", "users", "customer", "customers", "people", "person", "accept", "accepts", "accepting", "accepted",
    "offer", "offers", "offering", "offered", "able", "ability", "many", "much", "most", "first", "like",
    "likely", "unlikely", "possible", "possibly", "expect", "expected", "expects",
}


# Inflections a crux keyword may carry and still be the same word (eighth review follow-up, 2026-10-04): a
# 5-letter prefix let 'mission' match 'missile', 'commits' match 'commission' and 'approval' match 'approves'.
_KW_SUFFIXES = (("ments", ""), ("ment", ""), ("ings", ""), ("ing", ""), ("ies", "y"), ("ied", "y"), ("es", ""),
                ("ed", ""), ("s", ""))


def _kw_base(w: str) -> str:
    """A keyword's base: the word minus one inflection (-s, -es, -ies, -ed, -ing, -ment) and a final silent 'e',
    with a doubled final consonant undone ('deployment' / 'deployed' / 'deploys' -> 'deploy'; 'approve' /
    'approves' / 'approved' -> 'approv'; 'commits' / 'committed' -> 'commit'). Derivations stay apart:
    'approval' is not 'approves', 'commission' is not 'commits', 'missile' is not 'mission'."""
    w = (w or "").lower()
    for suf, rep in _KW_SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 4 and not (suf == "s" and w.endswith(("ss", "us", "is"))):
            b = w[: len(w) - len(suf)] + rep
            if not rep and suf != "s" and len(b) >= 5 and b[-1] == b[-2] and b[-1] in "bdgmnprt":
                b = b[:-1]                                   # 'committed' -> 'commit', 'planning' -> 'plan'
            w = b
            break
    if w.endswith("e") and len(w) >= 5 and not w.endswith(("ee", "ie")):
        w = w[:-1]                                           # 'approve' -> 'approv', 'escape' -> 'escap'
    return w


def _lower_words(text: str) -> list[str]:
    """The all-lower-case words of four letters or more ('Hormuz', 'Seoul', 'Whether' are not among them; a
    hyphenated 'option-level' gives 'option' and 'level'). Lower-case a line first to take all its words."""
    return [w for w in re.findall(r"[A-Za-z]+", text or "") if len(w) >= 4 and w.islower()]


def crux_keywords(texts: list[str], question: str = "") -> list[str]:
    """The crux's own content words on an event or judgment question (eighth review, 2026-10-04): lower-case words
    of four letters or more in the crux texts ('troop', 'deployment', 'options', 'capacity'), minus stop words,
    metric words, any word that shares a base with CRUX_BOILERPLATE or with a word of the question ('contribution'
    on 'Will South Korea announce a concrete Hormuz security contribution?'). Returned as bases (_kw_base): a
    keyword matches a line word only as the same word up to an inflection, never as a shared prefix.
    Capitalised words are entities and stay out: only words the cruxes write in lower case count."""
    skip = {_kw_base(w) for w in _lower_words((question or "").lower())} | _BOILER_BASES
    out = []
    for t in texts or []:
        for w in _lower_words(t):
            if w in STOP or w in METRIC_SET:
                continue
            st = _kw_base(w)
            if st in skip:
                continue
            out.append(st)
    return list(dict.fromkeys(out))


_BOILER_BASES = {_kw_base(w) for w in CRUX_BOILERPLATE | CRUX_GENERIC}


def resolution_words(question: str) -> list[str]:
    """The question's own content words on an event or judgment question, as bases (_kw_base): its resolution verb
    and object ('resum', 'subscription', 'sign' on 'Will OpenAI resume new Pro subscription sign-ups by ...?'),
    minus stop, metric, boilerplate and generic words; names stay out (only words the question writes in lower
    case). crux_keywords leaves them out (every line about the
    subject carries them, so they cannot make a crux-search hit), but a crux entity on a line backs the crux
    only beside one of them or a crux keyword (entity_backed)."""
    out = []
    for w in _lower_words(question or ""):
        if w in STOP or w in METRIC_SET:
            continue
        st = _kw_base(w)
        if st not in _BOILER_BASES:
            out.append(st)
    return list(dict.fromkeys(out))


def keyword_hits(line: str, keywords) -> list[str]:
    """The crux keywords (bases, _kw_base) a line carries (any case)."""
    if not keywords:
        return []
    have = {_kw_base(w) for w in _lower_words((line or "").lower())}
    return [k for k in keywords if k in have]


# On an event or judgment question the pinned subject plus this many distinct crux keywords counts as a crux
# entity: a crux hit (§6) and a qualifying quote (§7.2).
KEYWORD_PAIR = 2


def subject_keyword_pair(h: dict) -> bool:
    """A line that names the question's pinned subject and at least KEYWORD_PAIR crux keywords ('South Korea says
    Hormuz talks concern contribution options, not troop deployment' on a crux about troop or escort roles)."""
    return bool(h.get("pinned")) and len(h.get("keywords") or []) >= KEYWORD_PAIR


def entity_backed(h: dict, terms: dict) -> bool:
    """A crux entity on the line that says something about the crux (ninth review, 2026-10-04). Where the cruxes
    have keywords (event and judgment questions) a crux entity alone is a name the story is told around, not the
    fact the split turns on: 'GPT-6 Astra: The next generation in intelligence for work - OpenAI' names Astra and
    the pinned OpenAI on a crux about Astra serving capacity reopening Pro sign-ups, and says nothing about
    capacity or sign-ups. There it counts only with a crux keyword, a word of the question's own resolution
    (`resolution`: resolution_words, 'resume' / 'sign-ups') or a crux number on the same line. A second crux
    entity is not enough (tenth review, 2026-10-04, 09-11 c14 q3: 'Introducing ChatGPT for Financial Services,
    combining built-in financial data and GPT-6 Astra ...' names ChatGPT and Astra, says nothing about sign-ups
    or capacity, was the crux hit and moved both sides as crux_data). Without keywords (threshold and direction
    questions, or cruxes with no content word) any crux entity counts, as before."""
    if not h.get("entities"):
        return False
    if not terms.get("keywords"):
        return True
    return bool(h.get("keywords") or h.get("resolution") or h.get("numbers"))


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


def _ent_in(e: str, line: str) -> bool:
    return e in line if _HANGUL.fullmatch(e) else bool(re.search(rf"(?<!\w){re.escape(e)}(?!\w)", line))


def term_hits(line: str, terms: dict) -> dict:
    """The crux terms a line carries. `entities` never holds the question's own entities (`subject`, every
    kind) or a pinned one (`pinned`, event and judgment questions; drop_frequent_entities): a line that only
    names what the question is about says nothing about the crux. Pinned entities found on the line are
    reported apart under `pinned` and only rank crux-search hits (§6); they never score a hit or qualify a
    quote (§7.2)."""
    out_low = {str(x).lower() for x in list(terms.get("pinned", [])) + list(terms.get("subject", []))}
    ents = [e for e in terms.get("entities", []) if e.lower() not in METRIC_SET and e.lower() not in out_low
            and _ent_in(e, line)]
    pins = [e for e in terms.get("pinned", []) if _ent_in(e, line)]
    low = line.lower()
    mets = [w for w in terms.get("metrics", []) if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low)]
    nums = []
    if terms.get("numbers"):
        xs = crux_numbers(line)
        for y in terms["numbers"]:
            # A percentage under 10 matches within ±0.051 points, so 'above 1%' would match every '+1.03% 24h' of
            # an on-chain page: it counts only on a line that also carries a crux entity or metric word (seventh
            # review, 2026-10-04: 'Solana TVL: $9.12B (+1.03% 24h)' qualified on a stablecoin-supply crux).
            if y.get("pct") and abs(y.get("scaled", 0)) < 10 and not (ents or mets):
                continue
            if any(same_number(x, y) for x in xs):
                nums.append(y["raw"])
    kws = keyword_hits(line, terms.get("keywords"))
    res = keyword_hits(line, terms.get("resolution"))
    return {"entities": ents, "numbers": nums, "metrics": mets, "pinned": pins, "keywords": kws, "resolution": res}


def shares_term(quote: str, terms: dict) -> bool:
    """Any crux term, metric words included (reporting only)."""
    h = term_hits(quote or "", terms)
    return bool(h["entities"] or h["numbers"] or h["metrics"] or h["keywords"])


def number_backed(line: str, h: dict, terms: dict, kind: str = "") -> bool:
    """Whether a crux number on the line counts (2026-10-04, §20.7 #80). On a threshold or direction question
    the cruxes repeat the question's own figure, so any line carrying that figure would match: there the number
    needs a crux entity, a metric word or the question's subject (`subject`, aliases included) on the same line,
    as a small percentage already does on every kind (term_hits). Other kinds keep the bare number."""
    if kind not in ("threshold", "direction"):
        return True
    return bool(h.get("entities") or h.get("metrics")
                or any(_ent_in(str(e), line) for e in terms.get("subject", []) if str(e).strip()))


def shares_specific(quote: str, terms: dict, kind: str = "") -> bool:
    """What a qualifying quote needs (§7.2): a crux number (same_number), or a crux entity together with a
    number of its own. The entities are the cruxes' entities minus the frequent ones (> 2% of the corpus
    lines: 'Fed', 'ETF') and minus the question's own entities (`subject`, every kind), so 'BTC dominance:
    58.6%' does not qualify on a BTC price-threshold crux. On an event or judgment question (`kind`) a crux
    entity alone is enough, since headlines about events carry no number, but it has to be an entity other
    than the question's own: a headline that only names the subject says nothing about the crux.
    An entity alone on a threshold or direction question, or a metric word alone, never counts.
    Eighth review: on an event or judgment question the pinned subject plus KEYWORD_PAIR crux keywords counts as
    a crux entity (subject_keyword_pair; `keywords` is set only on those kinds, orchestrator.search_terms).
    §20.7 #80 (2026-10-04, 09-11 c8 q1): on a threshold or direction question the crux nearly always repeats
    the question's threshold, so a crux number counts only on a line that also carries a crux entity, a metric
    word or the question's subject (number_backed). 'Apeing's Crypto Presale Crosses $75K Raised ...' qualified
    on '$75,000' alone on 'Will Bitcoin trade below $75,000 by 2026-09-18?' and moved a side 15 points."""
    h = term_hits(quote or "", terms)
    if h["numbers"] and number_backed(quote or "", h, terms, kind):
        return True
    if kind in ("event", "judgment") and subject_keyword_pair(h):
        return True
    if not entity_backed(h, terms):
        return False
    return kind in ("event", "judgment") or bool(crux_numbers(quote or ""))


# A question's subject under its other common name: a crux that says 'Bitcoin' on a 'BTC' question is about
# the same subject, so 'Bitcoin dominance: 58.6%' is no more about the crux than 'BTC dominance: 58.6%'.
# One shared map, built from ALIAS_GROUPS: crypto tickers and the policy bodies a market line names under a
# different name than triage does ('FOMC' / 'Federal Reserve' questions vs the 'Fed Rate Hike …' odds line).
ALIAS_GROUPS = (("BTC", "Bitcoin"), ("ETH", "Ethereum", "Ether"), ("SOL", "Solana"), ("XRP", "Ripple"),
                ("Fed", "FOMC", "Federal Reserve"), ("SEC", "Securities and Exchange Commission"),
                ("CFTC", "Commodity Futures Trading Commission"), ("ECB", "European Central Bank"),
                ("BOJ", "BoJ", "Bank of Japan"), ("BOE", "BoE", "Bank of England"), ("BOK", "BoK", "Bank of Korea"),
                ("PBOC", "PBoC", "People's Bank of China"), ("IMF", "International Monetary Fund"),
                ("OPEC", "OPEC+"))
SUBJECT_ALIASES = {a.lower(): tuple(b for b in g if b.lower() != a.lower()) for g in ALIAS_GROUPS for a in g}


def drop_frequent_entities(terms: dict, docs: dict[str, str], max_share: float = 0.02, keep_always=(),
                           subject=()) -> dict:
    """Entities that sit on more than `max_share` of the corpus's non-empty lines ('BTC', 'ETF', 'Fed',
    'DeFi' on hundreds of package lines) say nothing specific about a crux: drop them from the terms.
    `subject`: the question's own entities ('BTC' on 'Will BTC close above $90,000?'), on every kind of
    question. They leave `entities` (stored under `subject`) however rare they are: a line that names the
    question's subject plus any number of its own ('BTC dominance: 58.6%') is not about the crux.
    `keep_always`: the question's entities again on an event or judgment question ('South Korea', 'Hormuz').
    They go to `pinned`: they rank crux-search hits about the subject first but, like `subject`, never score
    a hit or qualify a quote (§6, §7.2). Threshold and direction questions pin nothing
    (orchestrator.search_terms)."""
    pinned = list(dict.fromkeys(str(x) for x in keep_always or () if str(x).strip()))
    subj = list(dict.fromkeys([str(x) for x in subject or () if str(x).strip()] + pinned))
    subj += [a for x in subj for a in SUBJECT_ALIASES.get(x.lower(), ()) if a.lower() not in {y.lower() for y in subj}]
    out_low = {x.lower() for x in subj}
    lines = [l for d in docs.values() for l in (d or "").split("\n") if l.strip()]
    if not lines:
        return {**terms, "entities": [e for e in terms.get("entities", []) if e.lower() not in out_low],
                "pinned": pinned, "subject": subj, "frequent_entities": [], "frequent_numbers": []}
    limit = max(3, int(max_share * len(lines)))
    keep, dropped = [], []
    for e in terms.get("entities", []):
        if e.lower() in out_low:
            continue
        if _HANGUL.fullmatch(e):
            n = sum(1 for l in lines if e in l)
        else:
            rx = re.compile(rf"(?<!\w){re.escape(e)}(?!\w)")
            n = sum(1 for l in lines if rx.search(l))
        (dropped if n > limit else keep).append(e)
    # Crux numbers follow the same rule (seventh review): a figure that sits on more than max_share of the lines
    # (same_number: '1%' on every '+0.98%' and '+1.03%' change of an on-chain page) says nothing about the crux.
    nkeep, ndropped = [], []
    if terms.get("numbers"):
        line_nums = [xs for xs in (crux_numbers(l) for l in lines) if xs]
        for y in terms["numbers"]:
            n = sum(1 for xs in line_nums if any(same_number(x, y) for x in xs))
            (ndropped if n > limit else nkeep).append(y)
    # Crux keywords too (eighth review): a keyword on more than max_share of the lines ('market', 'price') is generic.
    kkeep, kdropped = [], []
    if terms.get("keywords"):
        line_kw = [{_kw_base(w) for w in _lower_words(l.lower())} for l in lines]
        for k in terms["keywords"]:
            (kdropped if sum(1 for ws in line_kw if k in ws) > limit else kkeep).append(k)
    out = {**terms, "entities": keep, "numbers": nkeep, "pinned": pinned, "subject": subj,
           "frequent_entities": dropped, "frequent_numbers": [y["raw"] for y in ndropped]}
    if "keywords" in terms:
        out.update(keywords=kkeep, frequent_keywords=kdropped)
    return out


def crux_search(terms: dict, docs: dict[str, str], exclude_quotes, locator=None, top: int = 12,
                block_bytes: int = 3000, referee_bytes: int = 5000, exclude_positions=None, sides=None,
                kind: str | None = None) -> dict:
    """Data lines in the run folder's raw file, package and social extract that score
    3 x entities + 2 x numbers + 1 x metric words >= 4, match two distinct terms, at least one a
    number or an entity, and are not already quoted by either side (by text, or by the line a quote
    sits on: `exclude_positions` = {(doc, line)}). Social lines (by section or content) are left out:
    they cannot justify a move. Top `top` hits.

    The score uses the same terms as the evidence gate (term_hits: no frequent entity, no pinned question
    subject), so the block each responder is shown, and told is worth +10 a line, is not filled with generic
    lines about the subject ('BTC dominance: 58.6%' on a BTC price crux). A pinned subject (event and judgment
    questions) counts once, as 2 points and one distinct term, so the subject plus one crux entity or number is
    a hit; among hits of equal score, lines that also name it come first. `pool` counts the candidate lines at
    each step: lines with any term or subject entity, lines that would pass with the subject scored as a crux
    entity, lines that pass, and the hits left after the quote exclusion.

    Eighth review (2026-10-04): event cruxes are written in words, not names or numbers ('troop', 'escort',
    'capacity'), so on an event or judgment question `keywords` (crux_keywords) score 1 each and the pinned
    subject plus KEYWORD_PAIR keywords is a hit: 'South Korea says Hormuz talks concern contribution options,
    not troop deployment' on a crux about a troop or escort role. Keywords also score with a crux entity, like
    metric words. On the c6 Hormuz pairs this takes the passing lines from 1 (quoted by both sides) to 3, with 1
    hit left after the quote exclusion on each day; the OpenAI Pro pair passes 1 line, quoted by both.

    Shared pool (2026-10-04, 09-11 c8 q3): `sides` = (high side's quotes, low side's quotes). When no hit is left
    after the quote exclusion, the passing lines that both sides quoted are the single source both views read
    differently: those lines plus the other lines of the same list item (headline and its indented body) come
    back as `shared`, with a `shared_block` for the referee (§8). A line only one side quoted stays out (it is
    that side's evidence, shown to the referee as an excerpt). They are never `hits`: the
    responders' block and the gate (crux_data) do not change, so a shared line cannot move a side or confirm a
    closure; it only lets the crux check read the one fact the split turns on."""
    excl = [qnorm(q) for q in exclude_quotes if q and len(qnorm(q)) >= 12]
    excl_pos = set(exclude_positions or ())
    side_norms = [[qnorm(q) for q in (sq or []) if q and len(qnorm(q)) >= 12] for sq in (sides or ())]
    side_pos = [quote_positions(sq, locator) for sq in (sides or ())]
    # Story keys (09-11 c8): a syndicated copy of a quoted line ('... - Bloomberg News - TradingView' beside the
    # quoted '... - Bloomberg.com') is the same story, so it counts as quoted, never as a hit neither side quoted.
    all_lines = {d: (docs.get(d) or "").split("\n") for d in ("raw", "package", "social")}

    def text_at(d, n):
        if locator is not None and d in locator.lines:
            return locator.line_text(d, n)
        ls = all_lines.get(d) or []
        return ls[n - 1] if 1 <= n <= len(ls) else ""

    def keys_of(quotes, pos):
        out = {evidence.story_key(text_at(d, n)) for d, n in pos} | {evidence.story_key(q) for q in quotes or [] if q}
        return {k for k in out if len(k) >= 12}
    excl_keys = keys_of(exclude_quotes, excl_pos)
    side_keys = [keys_of(sq, side_pos[i]) for i, sq in enumerate(sides or ())]
    passed: list[tuple] = []   # (doc, n, ns) of every line that passed, quoted or not
    hits, seen = [], set()
    # The candidate pool at each step (seventh review, 2026-10-04: the 09-10 / 09-11 c6 Hormuz and OpenAI Pro
    # pairs had 0 hits; this says whether the corpus, the subject rule or the quote exclusion emptied it).
    pool = {"term_lines": set(), "pass_with_subject": set(), "pass": set(), "after_quote_exclusion": 0}
    subj = [str(x) for x in terms.get("subject", [])]
    mkeys = _market_keys(locator) if locator is not None else market_question_keys(all_lines)
    # The question kind for event_belief_line: given, or event when the terms carry the event/judgment keywords
    # (orchestrator.search_terms sets 'keywords' only on those kinds).
    if kind is None:
        kind = "event" if "keywords" in (terms or {}) else ""
    for doc in ("raw", "package", "social"):
        lines = all_lines[doc]
        for n, line in enumerate(lines, 1):
            s = line.strip()
            if len(s) < 12 or s.startswith("#"):
                continue
            ns = evidence.norm(s)
            if locator is not None and doc in locator.lines:
                sec, cls = locator.label(doc, n)
            else:
                sec, cls = "", ("social" if doc == "social" or evidence.social_line(s) else "data")
            if cls == "social" or market_line_in(sec, lines, n - 1, mkeys, terms, kind):
                continue
            h = term_hits(s, terms)
            # On an event or judgment question the pinned subject counts once (2 points, one distinct term), so a
            # line naming the subject plus one crux entity or number is a hit (seventh review). It still never
            # qualifies a move or a referee quote (shares_specific), and alone it is never a hit.
            # Eighth review: crux keywords (event and judgment questions only) score 1 each like metric words, and
            # the pinned subject plus KEYWORD_PAIR of them stands in for a crux entity (subject_keyword_pair).
            pin = 1 if h["pinned"] else 0
            nkw = len(h["keywords"])
            distinct = len(h["entities"]) + len(h["numbers"]) + len(h["metrics"]) + nkw + pin
            score = 3 * len(h["entities"]) + 2 * len(h["numbers"]) + len(h["metrics"]) + nkw + 2 * pin
            n_subj = len([e for e in subj if _ent_in(e, s)])
            if distinct or n_subj:
                pool["term_lines"].add(ns)
            if score - 2 * pin + 3 * n_subj >= 4 and distinct - pin + n_subj >= 2:
                pool["pass_with_subject"].add(ns)
            # Ninth review: where the cruxes have keywords, a crux entity needs a keyword, a number or (tenth review)
            # a word of the question's resolution beside it (entity_backed), never just a second crux entity.
            if score < 4 or distinct < 2 or not (entity_backed(h, terms) or h["numbers"] or subject_keyword_pair(h)):
                continue
            pool["pass"].add(ns)
            passed.append((doc, n, ns))
            sk = evidence.story_key(s) or ns
            if sk in seen or (doc, n) in excl_pos or sk in excl_keys:
                continue
            if any(q in ns or (len(ns) >= 12 and ns in q) for q in excl):
                continue
            seen.add(sk)
            ctx = [lines[i].strip()[:300] for i in (n - 2, n) if 0 <= i < len(lines) and lines[i].strip()]
            hits.append({"doc": doc, "line": n, "section": sec, "cls": cls, "score": score, "text": s[:400],
                         "terms": h, "context": ctx})
    pool = {k: (len(v) if isinstance(v, set) else v) for k, v in pool.items()}
    pool["after_quote_exclusion"] = len(hits)
    hits.sort(key=lambda h: (-h["score"], -len(h["terms"].get("pinned") or []),
                             ("raw", "package", "social").index(h["doc"]), h["line"]))
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

    def quoted_by(i, doc, n, ns):
        return ((doc, n) in side_pos[i] or evidence.story_key(all_lines[doc][n - 1]) in side_keys[i]
                or any(q in ns or (len(ns) >= 12 and ns in q) for q in side_norms[i]))

    shared: list[dict] = []
    both = [(d, n) for d, n, ns in passed if quoted_by(0, d, n, ns) and quoted_by(1, d, n, ns)] \
        if len(side_norms) == 2 and not hits else []
    if both:
        sseen: set[str] = set()
        passed_at = set(both)
        for doc, n in both:
            for m in _same_item(all_lines[doc], n):
                s = all_lines[doc][m - 1].strip()
                ns = evidence.story_key(s) or evidence.norm(s)
                if len(s) < 12 or s.startswith("#") or ns in sseen:
                    continue
                if locator is not None and doc in locator.lines:
                    sec, cls = locator.label(doc, m)
                else:
                    sec, cls = "", ("social" if doc == "social" or evidence.social_line(s) else "data")
                if cls == "social" or market_line_in(sec, all_lines[doc], m - 1, mkeys, terms, kind):
                    continue
                sseen.add(ns)
                shared.append({"doc": doc, "line": m, "section": sec, "cls": cls, "text": s[:400],
                               "quoted_by_both": (doc, m) in passed_at})
    pool["shared"] = len(shared)

    def shared_block(cap):
        out, used = [], 0
        for h in shared:
            piece = f"- [{h['section'] or h['doc']}] {h['text']}"
            if used + nbytes(piece) + 1 > cap:
                break
            out.append(piece)
            used += nbytes(piece) + 1
        if not out:
            return ""
        return "\n".join(["(no line on disk that neither view quoted: this is the one story both views cite; "
                          "say what it shows about the crux)"] + out)
    return {"terms": {"numbers": [x["raw"] for x in terms.get("numbers", [])], "entities": terms.get("entities", []),
                      "metrics": terms.get("metrics", []), "keywords": terms.get("keywords", []),
                      "pinned": terms.get("pinned", []),
                      "subject": terms.get("subject", [])},
            "hits": hits, "pool": pool, "block": block(block_bytes), "referee_block": block(referee_bytes),
            "shared": shared, "shared_block": shared_block(referee_bytes)}


def _same_item(lines: list[str], n: int, span: int = 3) -> list[int]:
    """1-based line numbers of the list item line n belongs to: its head (the nearest line above, within
    `span`, that is not indented) and the indented body lines under that head (up to `span`). A headline
    '- [Thu, 10 Sep 2026] OpenAI puts Pro subscriptions on hold ...' and its '  The company said ...' body."""
    def indented(i):
        raw = lines[i - 1] if 0 < i <= len(lines) else ""
        return bool(raw.strip()) and raw[:1] in (" ", "\t")
    head = n
    while indented(head) and head > 1 and n - head < span:
        head -= 1
    out = [head]
    m = head + 1
    while m <= len(lines) and indented(m) and m - head <= span:
        out.append(m)
        m += 1
    return sorted(set(out) | {n})


# ── prediction-market lines (phase-e SECTION 8) ────────────────────────────────────────

MARKET_STOP = STOP | {"above", "below", "more", "less", "than", "before", "after", "over", "under", "least", "most",
                      "into", "their", "there", "about", "again", "still", "close", "next", "week", "month", "year",
                      "contracts", "leading", "total", "ends", "polymarket", "kalshi", "markets"}  # + the odds-line boilerplate


# Odds by content, in any section: World Monitor's Polymarket feed sits in SECTION 2 GEOPOLITICAL CONTEXT
# ('Fed Rate Hike by September 2026 Meeting? — YES: 59.5% | vol: …'), the POLYMARKET LIVE MARKETS block in
# SECTION 3 puts 'YES: 40% | 24h vol …' under the question line, the Polymarket collector writes
# '"…" YES 59%' and 'YES 59%', and Kalshi writes 'top: "…" 59% (+3)'.
# Kalshi's CRYPTO PRICE LADDERS line ('- BTC at ... close: market-implied median $84,816 (25-75 %: $84,398-$85,223),
# from 80 strikes | ...') prices every strike of that close. It sits in SECTION 8 and the raw Kalshi block, and the
# package copies it into SECTION 0 CROSS-SOURCE SIGNALS, so it is recognised by content too (seventh review).
_ODDS = re.compile(r"\bYES:?\s*\d{1,3}(?:\.\d+)?\s?%|\bNO:\s*\d{1,3}(?:\.\d+)?\s?%"
                   r"|\btop:\s*\"[^\"]{1,120}\"\s*\d{1,3}(?:\.\d+)?\s?%"
                   r"|\"[^\"]{1,120}\"\s+\d{1,3}(?:\.\d+)?\s?%\s*\((?:[+\-\u2212]|0\b|new\b|flat\b)"
                   r"|\bmarket-implied\b"
                   # Collector formats the forms above miss (Phase C, 2026-10-04): Kalshi 24H MOVERS and Polymarket
                   # '"Above 108" 76% (24h +53 pts)' / '(1d -18 pts)', the Kalshi strike list 'Above 3.75% 99.5% ·
                   # Above 4.00% 14%', Polymarket CLOB depth 'mid 9.5¢' and NEW MARKETS listings
                   # '— started 2026-10-02 22:17 UTC | 24h vol $21K'.
                   r"|\d{1,3}(?:\.\d+)?\s?%\s*\((?:24h|1d)\s+(?:[+\-\u2212]?\d+(?:\.\d+)?\s*pts\b|n/a\b)"
                   r"|\b(?:Above|Below)\s+\$?\d[\d,]*(?:\.\d+)?%?\s+\d{1,3}(?:\.\d+)?\s?%"
                   r"|\bmid\s+\d{1,3}(?:\.\d+)?\s?\u00a2"
                   r"|\bstarted\s+\d{4}-\d\d-\d\d\s+\d\d:\d\d\s+UTC\s*\|\s*24h vol\b")
# Odds quoted in a headline ('Brent Tops $106 And Hike Odds Reach 64%', 'a 30% chance of a cut'): what traders
# or forecasters believe, not data about the question. Market for evidence (market_line_in, so market_at,
# ev_class, crux_search and quote_qualifies), but not an odds line for the question gate's market rule
# (market_lines), where a news headline is not a priced market.
# Phase C (2026-10-04): the belief words are wider than 'odds' and 'chance'. 'Traders price a 78% probability of an
# October Fed cut as CME FedWatch odds firm' and 'Polymarket bettors give 64% likelihood that the Fed cuts' were
# classed data and qualified the full 25-point move. A venue name (FedWatch, Polymarket, Kalshi) counts only next
# to an unsigned percentage that is not a period change, so DeFiLlama's '- Kalshi: $433,518,327 (+7.0% 7d)' volume
# line stays data; bare 'implied' does not count ('30-day implied volatility at 52%' is options data), only
# 'implied at 78%' or '78% implied'.
# Phase C (2026-10-04, second pass): 'Traders see 78 percent chance' (AP style, no '%'), 'futures imply 72%',
# 'Market sees ... at 85%', 'Traders are pricing an October Fed cut at 85%', 'Polymarket: ... contract trades at
# 64 cents' and swaps 'fully price ... 90% for December' were still data, and each moved the skeptic 40 -> 55 on
# an October-cut crux. The percentage may be spelled out (percent, per cent, pct); a belief subject (traders,
# market(s), investors, forecasters, futures, swaps, bettors) takes put/see/give/assign/imply/price in any form; the
# verbs 'pricing'/'priced' count with 'at N%' a few words on (not the noun: 'Producer prices rose at a 0.3% pace'
# and 'priced the note at 99.5% of par' stay data); a price in cents counts next to Polymarket or Kalshi or after
# 'contract trades at' ('Corn futures fell 5 cents a bushel' stays data).
# Phase C (2026-10-04, third pass): 'Odds that the Fed cuts in October jump to 78%', 'Polymarket contract on an
# October Fed cut jumps to 64%', '78% on CME FedWatch', '85% priced in', 'with 90% certainty' and 'boost wagers on
# ... to 78%' were data, qualified, and moved the skeptic 40 -> 55. The belief word and the venue reach 10 words to
# the percentage, a venue may follow the percentage by up to 3 words, and wager(s)/wagering are belief words.
# Phase C (2026-10-04, fourth pass): the run collects Korean prediction-market news (c8 raw data carries 폴리마켓 and
# 칼시 headlines), and 'CME 페드워치에 따르면 ... 인하 확률은 78%로 높아졌다' was data; so were English belief subjects
# with show/signal/reflect/expect ('Fed funds futures show 78%', 'Rate futures signal 72%'), 'seen at 78% by rate
# futures' and '78% baked in'. Each qualified and moved the skeptic 40 -> 55 on a threshold crux. Korean: 확률 or
# 가능성 with a subject/object particle and up to 3 words before the percentage ('가능성에 국채 금리 4.1%' stays
# data), or the percentage then 확률/가능성; 베팅 or a venue (페드워치, 폴리마켓, 칼시) within 40 characters before
# or 15 after an unsigned percentage. The English verbs skip a price move ('futures show a 1.2% gain' stays data).
# Phase C (2026-10-04, fifth pass): widened by class, not by phrase. 'Traders peg an October Fed cut at 75%', 'Wall
# Street sees a 75% shot', 'October Fed cut now 80% likely, CME data show', '... expectations to 80%', '... pricing to
# 80%', 'Manifold users put ...', 'Metaculus community forecast ... rises to 35%', 'PredictIt shares ... trade at 64
# cents' and 'Polymarket traders now favour an October Fed cut, 64-36' were data, qualified, and moved the skeptic
# 40 -> 55 on an October-cut crux. Belief verbs take peg and favour; belief subjects take users, punters and Wall
# Street; venues take Manifold, Metaculus, PredictIt, Myriad and Limitless; an unsigned percentage then likely /
# unlikely / probable / shot / proposition is belief, as are expectations or pricing 'to N%' (not inflation or price
# expectations), a community or crowd forecast, 'a 3-in-4 shot', and a belief subject or venue favouring one side
# with an N-M split. On event and judgment questions event_belief_line() backs this up by meaning, not wording.
_HO_PCT =r"\d{1,3}(?:\.\d+)?\s?(?:%|percent\b|per\s?cent\b|pct\b)"
_HO_KO_PCT = r"(?<![+\-−.\d])\d{1,3}(?:\.\d+)?\s?(?:%|퍼센트)"
_HO_KO_WORD = r"(?:확률|가능성)"
_HO_KO_VENUE = r"(?:베팅|페드워치|폴리마켓|칼시)"
_HO_SHOW = r"(?:shows?|showing|showed|shown|signal(?:s|l?ed|l?ing)?|reflect(?:s|ed|ing)?|expect(?:s|ed|ing)?)"
_HO_MOVE = (r"(?!\s+(?:gains?|drops?|rises?|falls?|declines?|loss(?:es)?|increases?|jumps?|surges?|slides?|moves?"
            r"|rally|rallies|higher|lower|up|down|advances?|decreases?|climbs?)\b)")
_HO_CENTS = r"\d{1,3}(?:\.\d+)?\s?(?:¢|cents?\b)"
_HO_WORD = r"(?:odds|chances?|probabilit(?:y|ies)|likelihood|bets?|bettors?|betting|wagers?|wagering)"
_HO_VENUE = r"(?:Fed\s?Watch|Polymarket|Kalshi|Manifold|Metaculus|PredictIt|Myriad|Limitless)"
_HO_SUBJ = r"(?:traders|markets?|investors|forecasters|futures|swaps|bettors|users|punters|Wall\s+Street)"
_HO_VERB = (r"(?:puts?|putting|sees?|seeing|saw|gives?|giving|gave|assigns?|assigning|assigned"
            r"|impl(?:y|ies|ying|ied)|pric(?:e|es|ed|ing)|peg(?:s|ged|ging)?|favou?r(?:s|ed|ing)?)")
_HO_UNSIGNED = r"(?<![+\-−±.\d])"
_HO_NUMWORD = r"(?:one|two|three|four|five|six|seven|eight|nine|\d{1,2})"
_HEADLINE_ODDS = re.compile(
    rf"\b{_HO_WORD}\b(?:\W+\w+){{0,10}}?\W+{_HO_PCT}"                           # 'Hike Odds Reach 64%'
    rf"|{_HO_PCT}\s+(?:\w+\s+)?(?:{_HO_WORD}|{_HO_VENUE}|implied)\b"            # '78% probability', '54.5% Polymarket odds'
    rf"|{_HO_PCT}(?:\W+\w+){{0,3}}?\W+{_HO_VENUE}\b"                            # '78% on CME FedWatch'
    rf"|{_HO_PCT}\s+priced\b|\bwith\s+{_HO_PCT}\s+certainty\b"                  # '85% priced in', 'with 90% certainty'
    rf"|\b{_HO_VENUE}\b(?:\W+\w+){{0,10}}?\W+(?<![+\-−.\d]){_HO_PCT}"      # 'Polymarket bettors give 64%'
    r"(?!\s*\(?\s*(?:7d|24h|1d|30d|wow|yoy|mom)\b)"                              # ... not '(+7.0% 7d)'
    rf"|\b(?:price[sd]?|pricing)\s+(?:in\s+)?(?:an?\s+|about\s+|around\s+|roughly\s+|nearly\s+)?{_HO_PCT}"
    rf"|\b(?:pricing|priced)\b(?:\W+\w+){{0,6}}?\s+at\s+{_HO_PCT}(?!\s+of\s+par)"  # 'pricing an October cut at 85%'
    rf"|\bimpl(?:y|ies|ying|ied)\s+(?:at\s+|an?\s+|about\s+|around\s+)?{_HO_PCT}"  # 'implied at 80%', 'imply 72%'
    rf"|\b{_HO_SUBJ}\s+(?:(?:now|are|were|is|still|fully|also|have|had|largely|mostly)\s+){{0,2}}{_HO_VERB}\b(?!\s+vol)"
    rf"(?:\W+\w+){{0,8}}?\W+(?<!\bup )(?<!\bdown ){_HO_UNSIGNED}{_HO_PCT}{_HO_MOVE}"  # 'traders put a cut at 90%'
    rf"|\b{_HO_VENUE}\b(?:\W+\w+){{0,8}}?\W+{_HO_CENTS}"                       # 'Polymarket: ... at 64 cents'
    rf"|{_HO_CENTS}\s+(?:\w+\s+){{0,2}}?(?:on\s+|at\s+)?{_HO_VENUE}\b"          # 'at 71¢ on Kalshi'
    rf"|\bcontracts?\s+(?:trades?|trading|traded|priced|sits?|is|at)\s+(?:at\s+|near\s+|around\s+)?{_HO_CENTS}"
    rf"|\b{_HO_SUBJ}\s+(?:(?:now|are|were|is|still|fully|also|have|had|largely|mostly)\s+){{0,2}}{_HO_SHOW}\b"
    rf"(?:\W+\w+){{0,8}}?\W+(?<![+\-−.\d]){_HO_PCT}{_HO_MOVE}"             # 'futures show 78%', 'signal 72%'
    rf"|\bseen\s+(?:at\s+)?{_HO_PCT}(?:\W+\w+){{0,4}}?\W+{_HO_SUBJ}\b"          # 'seen at 78% by rate futures'
    rf"|{_HO_PCT}\s+baked\s+in\b"                                              # '78% baked in'
    rf"|{_HO_UNSIGNED}{_HO_PCT}\s+(?:likely|unlikely|probable|improbable|shot|proposition)\b"  # '80% likely'
    rf"|(?<!inflation )(?<!price )\b(?:expectations|pricing)\s+(?:to|at|near|around)\s+{_HO_PCT}"  # '... to 80%'
    rf"|\b(?:community|crowd|aggregate)\s+forecasts?\b(?:\W+\w+){{0,10}}?\W+{_HO_PCT}"  # 'community forecast ... 35%'
    rf"|\b{_HO_NUMWORD}[\s-]in[\s-]{_HO_NUMWORD}\s+(?:shot|chance|odds|probability|likelihood)\b"  # 'a 3-in-4 shot'
    rf"|\b(?:{_HO_VENUE}|{_HO_SUBJ})\b(?:\W+\w+){{0,3}}?\W+favou?r(?:s|ed|ing)?\b[^\n]{{0,80}}?"
    r"(?<![\d.$])[1-9]\d?\s?[-–][1-9]\d?(?![\d%.\-])"                             # '... favour a cut, 64-36'
    rf"|{_HO_KO_WORD}(?:은|는|이|가|을|를|도)?\s+(?:[^\s%]+\s+){{0,3}}?(?:약\s*)?{_HO_KO_PCT}"  # '인하 확률은 78%'
    rf"|{_HO_KO_PCT}\s*(?:의\s*)?{_HO_KO_WORD}"                                 # '78% 확률로'
    rf"|{_HO_KO_VENUE}[^\n]{{0,40}}?{_HO_KO_PCT}(?!\s*\(?\s*(?:7d|24h|1d|30d)\b)"  # '페드워치에 따르면 ... 78%'
    rf"|{_HO_KO_PCT}[^\n]{{0,15}}?{_HO_KO_VENUE}"                               # '78% 베팅'
    rf"|\b(?:prediction|betting)\s+markets?\s*:[^\n]{{0,120}}?{_HO_UNSIGNED}{_HO_PCT}"  # 'Prediction markets: ... 30%'
    rf"|\b(?:{_HO_VENUE}|CME|futures|swaps)\s*:\s*{_HO_UNSIGNED}\d{{1,2}}(?:\.\d)?\s?%{_HO_MOVE}"  # 'futures: 78%'
    rf"|{_HO_UNSIGNED}{_HO_PCT}\s+according\s+to\s+(?:the\s+)?(?:{_HO_VENUE}|CME|(?:prediction|betting)\s+markets?)\b",
    re.I)
_LADDER = re.compile(r"\bmarket-implied\b")
_ODDS_CONT = re.compile(r"^\s*(?:YES|NO):?\s*\d{1,3}(?:\.\d+)?\s?%")   # an odds line under its question line


def odds_line(line: str) -> bool:
    """A line that carries market odds by its content, whatever section it sits in."""
    return bool(_ODDS.search(line or ""))


def is_market_line(section: str, line: str) -> bool:
    """A prediction-market line: any content line in the PREDICTION MARKETS section (package SECTION 8, raw
    Polymarket and Kalshi blocks), whatever it carries (seventh review, 2026-10-04: the Kalshi ladder's '25–75 %'
    has no digit right before '%', so a probability test let it pass as data), or a line that carries odds by
    its content in any section (odds_line: World Monitor's Polymarket block in GEOPOLITICAL CONTEXT, POLYMARKET
    LIVE MARKETS in ON-CHAIN, a ladder line copied into CROSS-SOURCE SIGNALS). Market lines are what traders
    believe, not data about the crux: they never qualify a move (§7.2), are not crux hits (§6) and cannot
    confirm a closure (§8)."""
    s = (line or "").strip()
    if (section or "").upper().startswith(MARKET_SECTION) and s and not s.startswith(("#", "<!--")) and s != "---":
        return True
    return odds_line(line)


def ev_class(loc: dict, locator) -> str:
    """The evidence class of a located quote: 'social', 'data', '' (not found), or 'market' when the line it sits
    on is a prediction-market odds line (is_market_line). Odds are what traders believe, not data about the
    question: a 'market' item does not make an agent eligible as a debate endpoint (eligible_agents needs a
    data quote), is not a data quote in the split sheet or the lens notes, and never qualifies a move (§7.2)."""
    cls = (loc or {}).get("cls") or ""
    if cls == "data" and locator is not None and loc.get("doc") and loc.get("line"):
        if market_at(locator, loc["doc"], loc["line"], loc.get("section")):
            return "market"
    return cls


def odds_below(lines: list[str], i: int) -> bool:
    """Line i (0-based) is a market question: the next non-empty line is its odds ('  YES: 62% | 24h vol ...',
    POLYMARKET LIVE MARKETS)."""
    for nxt in lines[i + 1:]:
        if nxt.strip():
            return bool(_ODDS_CONT.match(nxt))
    return False


def market_question_keys(docs_lines: dict[str, list[str]]) -> set[str]:
    """The _core() text of every market item in the run's documents: a question line with its odds below
    (odds_below), and every list item in a PREDICTION MARKETS section (package SECTION 8, the raw Polymarket and
    Kalshi blocks, the view's section). The package copies such lines into SECTION 0 CROSS-SOURCE SIGNALS
    (scripts/deduplicate.py: the longest rendering verbatim, or a question without its odds, '- LAPTOP FDV above
    $500M one day after launch?' then an 'Also in:' line), so a copy is the same item and a market line too,
    whatever format the collector wrote it in. Phase C (2026-10-04): on the Polymarket + Kalshi replays 6 of 20
    cross-source items were data, and Locator picks the first data hit, the copy, so the section never applied."""
    out = set()
    loc = evidence.Locator({d: "\n".join(ls) for d, ls in docs_lines.items()})
    for doc, lines in docs_lines.items():
        labels = loc.labels(doc) if doc in loc.lines else []
        for i, line in enumerate(lines):
            if not line.strip().startswith(("- ", "* ")):
                continue
            sec = labels[i][0] if i < len(labels) else ""
            if odds_below(lines, i) or sec.upper().startswith(MARKET_SECTION):
                k = evidence._core(line)
                if len(k) >= 12:
                    out.add(k)
    return out


def _market_keys(locator) -> set[str]:
    keys = getattr(locator, "_market_question_keys", None)
    if keys is None:
        keys = market_question_keys(getattr(locator, "lines", {}) or {})
        try:
            locator._market_question_keys = keys
        except AttributeError:
            pass
    return keys


# event_belief_line: an unsigned percentage between 1 and 99 (one decimal at most, so a '3.75%' rate level never
# matches), not a share ('55% of the vote'), not a range or a period change, and not 'by N%'.
_EV_PCT = re.compile(r"(?<![+\-−±.\d$])(?<!\bby\s)(\d{1,2}(?:\.\d)?)\s?(?:%|percent\b|per\s?cent\b|pct\b)"
                     r"(?!\s*(?:of\b|\(|[-–]\s?\d|\d))", re.I)
# A metric or outcome word up to five words before the percentage or right after it says the number measures
# something (a level, a move, a tally, a result), so it is data: 'unemployment fell to 4.1%', '5% gain', 'won 52%'.
_EV_METRIC = re.compile(
    r"\b(?:rates?|yields?|prices?|inflation|cpi|pce|gdp|unemployment|jobless|payrolls?|growth|index|indices"
    r"|volatility|range|target|benchmark|bps|basis|points?|tariffs?|tax(?:es)?|votes?|voters?|turnout|polls?|polling"
    r"|approval|support|share|shares|stake|margin|revenue|sales|earnings|profits?|returns?|gains?|loss(?:es)?"
    r"|rose|fell|rises?|falls?|drops?|dropped|declin\w*|increas\w*|decreas\w*|higher|lower|up|down|grew|surg\w*"
    r"|slump\w*|tumbl\w*|rall\w*|advanc\w*|stocks?|bonds?|dollar|oil|gold|apy|apr|interest|mortgages?|wages?"
    r"|budget|deficit|debt|spending|output|exports?|imports?|production|capacity|income|savings|pace|yoy|mom|qoq"
    r"|annual\w*|won|wins?|winning|took|secured|received|garnered|captured|tallied|counted|majority|plurality"
    r"|discount|premium|stake|owned|ownership|holdings?|allocation|weight\w*|dominance|cut\s+by|hike\s+by)\b", re.I)


def event_belief_line(line: str, terms: dict | None, kind: str = "") -> bool:
    """On an event or judgment question, a line that puts an unsigned percentage on the question's own event
    with no metric word beside it is what someone believes about the event, not data about it: 'An October Fed
    cut at 75% after soft payrolls' on 'The Fed cuts rates at the October 2026 FOMC meeting'. An event happens
    or not; a bare percentage on it is a probability (Phase C, 2026-10-04, fifth pass: the phrase lists behind
    _HEADLINE_ODDS kept leaking). 'The Fed cuts rates by 0.25%', '... to 3.75%', 'won 52% of the vote' and
    'payrolls rose 4.1%' stay data. The line has to be about the crux (shares_specific)."""
    if kind not in ("event", "judgment") or not terms or not line:
        return False
    body = re.sub(r"^\s*[-*]\s*(?:\[[^\]]*\]\s*)?", "", line)
    for m in _EV_PCT.finditer(body):
        if not 1 <= float(m.group(1)) <= 99:
            continue
        before = " ".join(re.findall(r"[\w']+", body[:m.start()])[-5:])
        after = " ".join(re.findall(r"[\w']+", body[m.end():])[:1])
        if _EV_METRIC.search(before) or _EV_METRIC.search(after):
            continue
        if shares_specific(line, terms, kind):
            return True
    return False


# market_belief_line: belief by meaning, on every question kind (Phase C, 2026-10-04, sixth pass). Threshold and
# direction questions had no backstop: 'Polymarket YES shares on Bitcoin above $100K ... trade at $0.30', 'Kalshi
# market ... trades at 0.30', '... 30c on Kalshi', 'October FOMC cut now 78% according to CME' and 'Market consensus
# now 78% ...' were data, qualified and moved the skeptic 40 -> 55; event_belief_line only read '%' figures, so
# contract prices got past it on event questions too. A contract price ($0.NN, a bare 0.NN, NNc / NN¢) within ten
# words of YES/NO or 'contract' or on a line naming a venue or YES/NO shares, or an unsigned 1-99% (_EV_PCT) within six words before or four after a
# venue or belief subject, is market unless a metric word sits beside it ('CME open interest rose 5%').
_MB_PRICE = re.compile(r"(?<![\w.,$])(?:\$0?|0)\.\d{2}(?![\d.]|\s?(?:[kmbt]n?|mn|million|billion|trillion)\b|\s?(?:%|percent|per\s?cent|pct\b|bps\b|basis\b))"
                       r"|(?<![\w.,$])\d{1,2}\s?(?:¢|c\b|cents?\b)", re.I)
_MB_ANCHOR_PRICE = re.compile(r"\bcontracts?\b|\b(?-i:YES|NO)\b", re.I)
# A venue, a prediction-market label or YES/NO shares anywhere on the line marks its contract prices.
_MB_LINE_PRICE = re.compile(rf"\b(?:{_HO_VENUE}|(?:prediction|betting)\s+markets?|(?-i:YES|NO)\s+(?:shares?|contracts?))\b",
                            re.I)
_MB_ANCHOR_PCT = re.compile(rf"\b(?:{_HO_VENUE}|CME|(?:prediction|betting)\s+markets?|market\s+consensus"
                            r"|consensus\s+(?:of\s+)?(?:traders|markets?)|traders|bettors|punters|forecasters)\b", re.I)
_MB_METRIC = re.compile(r"\b(?:jump\w*|climb\w*|soar\w*|spik\w*|plung\w*|slid\w*|slip\w*|sank|sink\w*|rebound\w*"
                        r"|volumes?|ratio|positions?|positioning|exposure|leverage|funding|liquidations?|inflows?"
                        r"|outflows?|flows?|supply|cap|caps|utili[sz]ation|hashrate|fees?|open\w*|dividends?"
                        r"|pays?|paid|per\s+share|eps)\b", re.I)


def market_belief_line(line: str) -> bool:
    """A line whose number is a market belief by meaning, whatever the question kind: a contract price next to
    a venue, YES/NO or 'contract', or an unsigned 1-99% next to a venue or belief subject, with no metric word
    beside it (_MB_* above). 'XYZ shares trade at $0.30', 'Corn futures fell 5 cents' and 'CME open interest rose
    5%' stay data."""
    if not line:
        return False
    body = re.sub(r"^\s*[-*]\s*(?:\[[^\]]*\]\s*)?", "", line)
    for rx, anchor, nb, na in ((_MB_PRICE, _MB_ANCHOR_PRICE, 10, 4), (_EV_PCT, _MB_ANCHOR_PCT, 6, 4)):
        for m in rx.finditer(body):
            if rx is _EV_PCT and not 1 <= float(m.group(1)) <= 99:
                continue
            pre, post = body[:m.start()].split(), body[m.end():].split()
            near = " ".join(pre[-nb:]) + " # " + " ".join(post[:na])
            if not (anchor.search(near) or (rx is _MB_PRICE and _MB_LINE_PRICE.search(body))):
                continue
            beside = " ".join(pre[-5:]) + " # " + " ".join(post[:2])
            if _EV_METRIC.search(beside) or _MB_METRIC.search(beside):
                continue
            return True
    return False


def headline_odds(line: str) -> bool:
    """Market odds by content, by wording (_HEADLINE_ODDS) or by meaning (market_belief_line)."""
    return bool(_HEADLINE_ODDS.search(line or "")) or market_belief_line(line)


def market_line_in(section: str, lines: list[str], i: int, keys: set[str], terms: dict | None = None,
                   kind: str = "") -> bool:
    """is_market_line, aware of the neighbouring line and of the run's market questions: line i (0-based) of
    `lines` is a market line when its section or content says so, when the next non-empty line is its odds, or
    when it is a copy of a market question line (`keys`, market_question_keys). With the crux `terms` and the
    question `kind`, a bare percentage on an event or judgment question's own event is market too
    (event_belief_line)."""
    line = lines[i] if 0 <= i < len(lines) else ""
    if is_market_line(section, line) or odds_below(lines, i) or headline_odds(line):
        return True
    if event_belief_line(line, terms, kind):
        return True
    k = evidence._core(line)
    return len(k) >= 12 and k in keys


def market_at(locator, doc, line, section=None, terms: dict | None = None, kind: str = "") -> bool:
    """market_line_in for a located (doc, 1-based line). Fourth review #48, closed at every reader (ninth
    review, 2026-10-04): ev_class, the gate's strict qualification (gate_move), crux_search, quote_qualifies
    and gate rule 9 all ask this, not is_market_line on the bare line. On the 09-10, 09-11 and raw 09-11
    fixtures 56 question lines were classed data; on 09-10 the Iran-ceasefire question was the only crux hit
    and qualified a Hormuz move, and the LAPTOP $500M copy qualified a threshold move."""
    if locator is None or not doc or not line:
        return False
    lines = (getattr(locator, "lines", {}) or {}).get(doc) or []
    sec = section if section is not None else locator.label(doc, line)[0]
    return market_line_in(sec or "", lines, line - 1, _market_keys(locator), terms, kind)


def quote_qualifies(quote: str, terms: dict, kind: str, locator) -> dict:
    """The referee's quote check (§8, §9.1; orchestrator crux check): a quote confirms a closure only when it
    would qualify a move: Locator.strict passes (item=True: one line, or a headline and its body inside one list
    item, which the referee reads together; 09-11 c9 q5, §20.7 #77), the line is data, it is not a market line
    (market_at, so a POLYMARKET LIVE MARKETS question line or its CROSS-SOURCE copy is market), and it is about the
    crux (shares_specific). {qualifies, market, about, strict}. Every line the quote covers is checked with
    market_at, and the whole quote with is_market_line, headline_odds and event_belief_line (Phase C, 2026-10-04:
    only the headline line and odds_line were read, so a news headline quoted with its 'Traders put the odds ...
    at 78%' body qualified and could confirm a closure on market odds)."""
    st = locator.strict(quote, item=True) if quote and locator is not None else {"ok": False}
    sec = st.get("section", "")
    market = bool(st.get("ok")) and (
        any(market_at(locator, st["doc"], n, sec if n == st["line"] else None, terms, kind)
            for n in (st.get("lines") or [st["line"]]))
        or is_market_line(sec, quote) or headline_odds(quote) or event_belief_line(quote, terms, kind))
    about = shares_specific(quote, terms or {}, kind) if quote else False
    return {"qualifies": bool(st.get("ok") and st.get("cls") == "data" and not market and about),
            "market": market, "about": about, "strict": st}


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


_BPS = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:bps|bp|basis[- ]points?)(?!\w)", re.I)


def _bps(text: str) -> set[float]:
    """Basis-point figures ('25 bps', '25bps', '50 basis points'): evidence.numbers skips them as bare small
    integers, but on a rates question the size of the move is its specific figure."""
    return {float(m.group(1)) for m in _BPS.finditer(text or "")}


def market_match(question: str, lines: list[str]) -> str:
    """The first market line that prices this question, or ''. A line prices it when it shares at least one
    entity of the question (or its other name: 'Bitcoin' / 'BTC', 'FOMC' / 'Fed', ALIAS_GROUPS) plus a second
    specific term: another entity, one of the question's numbers (same figure, §6 rules; a basis-point size), or
    two content words ('Fed' + 'hike' + 'rate'). A price ladder ('market-implied median …', every strike of a daily close) prices any
    question on its asset that carries a currency figure."""
    if not lines:
        return ""
    # A body named in the question under any ALIAS_GROUPS name ('FOMC', 'Federal Reserve') is one entity
    # group: it matches a line under any of its names, and 'Federal Reserve' never counts as two entities.
    found = [(g, [a for a in g if re.search(rf"(?<!\w){re.escape(a)}(?!\w)", question)]) for g in ALIAS_GROUPS]
    found = [(g, hit) for g, hit in found if hit]
    named = {w.lower() for _, hit in found for a in hit for w in re.findall(r"[\w']+", a)}
    groups = [[hit[0], *[a for a in g if a != hit[0]]] for g, hit in found]
    for e in entities(question, mid_common=False):
        if e.lower() in MARKET_STOP or {w.lower() for w in re.findall(r"[\w']+", e)} <= named:
            continue
        groups.append([e, *SUBJECT_ALIASES.get(e.lower(), ())])
    if not groups:
        return ""
    q_nums = evidence.numbers(question)
    q_cur = any(x.get("cur") for x in q_nums)
    q_bps = _bps(question)
    ent_stems = {w[:4] for g in groups for e in g for w in re.findall(r"[a-z]{4,}", e.lower())}
    q_words = _stems(question) - ent_stems
    for line in lines:
        ents = {g[0].lower() for g in groups
                if any(re.search(rf"(?<!\w){re.escape(e)}(?!\w)", line, re.I) for e in g)}
        if not ents:
            continue
        if q_cur and _LADDER.search(line):
            return line
        nums = (_num_match(evidence.numbers(line), q_nums) if q_nums else []) or bool(q_bps & _bps(line))
        if len(ents) >= 2 or nums or len(q_words & _stems(line)) >= 2:
            return line
    return ""


# ── §7.2 the evidence gate ─────────────────────────────────────────────────────────────

EVIDENCE_MOVE_PER_ITEM = 10   # points beyond FREE_MOVE that one qualifying quote (one line) allows
EVIDENCE_MOVE_MAX = 20        # at most this many points beyond FREE_MOVE, however many quotes qualify


def item_head(locator, doc, line) -> int:
    """The line that names the list item `line` belongs to: for an indented body line under a '- ' headline
    (_same_item: no blank line between, within its span) the headline's line number, else `line` itself. A
    headline '- [Fri, 11 Sep 2026] Nvidia Delays Rubin Ultra Shipments ...' and its '  Supplier checks show ...'
    summary are one story (c8345bf 'one story = one line', §20.7 #77 reads them as one item)."""
    lines = (getattr(locator, "lines", {}) or {}).get(doc) if locator is not None and doc else None
    if not lines or not line or not 0 < line <= len(lines) or lines[line - 1][:1] not in (" ", "	"):
        return line
    head = _same_item(lines, line)[0]
    raw = lines[head - 1]
    return head if head < line and raw[:1] not in (" ", "	") and raw.strip().startswith("- ") else line


def line_key(locator, doc, line) -> str:
    """What makes two qualifying lines the same fact: the line's story key (evidence.story_key: the _core() text,
    no URL, X bracket, engagement counts or list dash, and no syndication tail ' - <outlet>'), so a headline
    repeated in CROSS-SOURCE and NEWS, in the package and the raw file, or carried by two outlets
    ('... - Bloomberg.com', '... - Bloomberg News - TradingView', 09-11 c8) is one line. A body line indented
    under a '- ' headline takes the headline's key (item_head): one list item is one line. Falls back to
    'doc:line' when the line has no core text."""
    if locator is not None and doc:
        line = item_head(locator, doc, line)
    core = evidence.story_key(locator.line_text(doc, line)) if locator is not None and doc else ""
    return core or f"{doc}:{line}"


def story_keys(quotes, locator, positions=None) -> set:
    """The story keys a set of quotes stands on: the key of every line they sit on (`positions`, else
    quote_positions()) and of the headline of the list item each sits in (item_head), plus each quote's own key;
    keys under 12 characters are left out. So a body line of an item a side quoted by its headline, or the
    headline of an item it quoted by its body, is that side's evidence."""
    pos = quote_positions(quotes, locator) if positions is None else positions
    out = set()
    if locator is not None:
        for d, n in pos:
            out.add(evidence.story_key(locator.line_text(d, n)))
            h = item_head(locator, d, n)
            if h != n:
                out.add(evidence.story_key(locator.line_text(d, h)))
    out |= {evidence.story_key(q) for q in quotes or [] if q}
    return {k for k in out if len(k) >= 12}


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
    exact text: `own`/`challenger` when it sits on a line a quote of that side sits on, overlaps one
    of those quotes as text, or is the same story (story_keys: a syndicated copy, '... - Bloomberg.com' quoted and
    '... - Bloomberg News - TradingView' cited, 09-11 c8); `crux_data` when it sits on a crux hit line (or a copy
    of one); else `other`.

    An item qualifies when Locator.strict passes (single verbatim line, no '...', every number found,
    40+ characters or a number), its line is data (social by content counts as social), its source is
    crux_data or other, it is not a prediction-market odds line, and it carries a crux number, or a crux
    entity together with a number (shares_specific: frequent entities and the question's pinned subject do
    not count); on an event or judgment question (`kind`) a crux entity other than the question's subject is
    enough, since headlines about events carry no number. Qualifying items only extend the
    move beyond FREE_MOVE: +EVIDENCE_MOVE_PER_ITEM per distinct qualifying line, at most EVIDENCE_MOVE_MAX
    (5 + 10 per line, 25 in total; spec §7.2, response.md step 3).

    A line is identified by line_key() (its _core() text), so the same headline in two places is one line, and an
    indented body line under a '- ' headline is that headline's line (one list item, one story).
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
    # Story keys (09-11 c8): a syndicated copy of a line a side quoted ('... - Bloomberg.com' quoted, '... -
    # Bloomberg News - TradingView' cited) is that side's evidence, not new data.
    own_k, oth_k = story_keys(own_quotes, locator, own_pos), story_keys(other_quotes, locator, oth_pos)
    hit_k = {k for k in (evidence.story_key(h.get("text", "")) for h in crux_hits or []) if len(k) >= 12}
    items, qual_lines = [], set()
    for e in new_evidence or []:
        q = (e.get("quote") or "").strip()
        loc = locator.locate(q) if q else {"status": "empty", "cls": ""}
        nq = qnorm(q)
        pos = locator.positions(q) if q else set()
        qk = story_keys([q], locator, pos) if q else set()

        def overlaps(lst):
            return any(nq and (nq in o or o in nq) for o in lst)
        if nq and (pos & own_pos or overlaps(own_n) or qk & own_k):
            src = "own"
        elif nq and (pos & oth_pos or overlaps(oth_n) or qk & oth_k):
            src = "challenger"
        elif nq and (pos & hit_pos or any(nq in h for h in hit_txt) or qk & hit_k):
            src = "crux_data"
        else:
            src = "other"
        st = locator.strict(q) if q else {"ok": False, "reason": "empty", "cls": ""}
        market = bool(st.get("ok")) and (market_at(locator, st["doc"], st["line"], st.get("section", ""), terms, kind)
                                         or is_market_line(st.get("section", ""), q))
        specific = shares_specific(q, terms, kind)
        qual = bool(st["ok"] and st.get("cls") == "data" and src in ("crux_data", "other") and not market and specific)
        why = "" if qual else (st.get("reason") or ("social line" if st.get("cls") == "social" else
                               f"cited before ({src})" if src in ("own", "challenger") else
                               "prediction-market odds line" if market else
                               "no crux entity beyond the question's subject" if kind in ("event", "judgment")
                               else "crux number without a crux entity, metric word or the subject"
                               if term_hits(q, terms)["numbers"] else "no crux number or entity"))
        if qual:
            qual_lines.add(line_key(locator, st["doc"], st["line"]))
        items.append({"section": e.get("section", ""), "quote": q[:300], "status": loc["status"],
                      "cls": "market" if market else ev_class(loc, locator), "new_evidence_source": src, "qualifies": qual,
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
    # A side may close the split but never pass the other side's take (seventh review, 2026-10-04: at a gap of 20
    # a 25-point allowance let 40 go to 65 past 60, and gap_after then read 'narrowed from 20 to 5' for sides that
    # had swapped). cap_pair() stops both sides at each other's gated value once both have answered.
    if toward and (gated - other_take_p) * (1 if toward > 0 else -1) > 0:
        gated = int(other_take_p)
        flags.append("stopped at the other view")
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
    Neither side ends past the other (_no_cross). Returns (move_high, move_low, changed); the moves are copies,
    flagged 'pair evidence cap' or 'stopped at the other view'."""
    def toward(m, side):
        if not m:
            return 0
        d = m["gated"] - m["take"]
        return max(0, -d) if side == "high" else max(0, d)
    th, tl = toward(move_high, "high"), toward(move_low, "low")
    allowed = pair_allowance(keys_high, keys_low)
    if th + tl <= allowed:
        mh, ml, crossed = _no_cross(move_high, move_low)
        return mh, ml, crossed
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
    mh, ml, _ = _no_cross(out[0], out[1])
    return mh, ml, True


def _no_cross(move_high: dict | None, move_low: dict | None) -> tuple[dict | None, dict | None, bool]:
    """The high side may not end below the low side (gate_move stops each at the other's take; two sides moving
    towards each other can still pass). The side that moved further towards the other stops at the other's gated
    value, flagged 'stopped at the other view'; the moves are copies. (move_high, move_low, changed)."""
    if not (move_high and move_low) or move_high["gated"] >= move_low["gated"]:
        return move_high, move_low, False
    th = move_high["take"] - move_high["gated"]
    tl = move_low["gated"] - move_low["take"]
    mh, ml = dict(move_high), dict(move_low)
    if th >= tl:
        mover, at = mh, ml["gated"]
    else:
        mover, at = ml, mh["gated"]
    mover["gated"] = int(at)
    mover["delta"] = int(mover["gated"] - mover["take"])
    if "stopped at the other view" not in (mover.get("flags") or []):
        mover["flags"] = list(mover.get("flags") or []) + ["stopped at the other view"]
    return mh, ml, True


# ── §9.1 per-debate scores ─────────────────────────────────────────────────────────────

VERDICT_RANK = {"hold": 0, "narrow": 1, "concede": 2}


def _ev_counts(items: list[dict]) -> dict:
    return {"claimed": len(items), "verified": sum(1 for e in items if e.get("status") in ("verified", "partial")),
            "data": sum(1 for e in items if e.get("status") in ("verified", "partial") and e.get("cls") == "data")}


def side_tier(agent: str) -> str:
    """The tier an agent's take, challenge, red-team and response calls run on (§15.0): the side that formed a
    position defends it on the same model."""
    return LENS_TIER.get(agent, "analyst")


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
    # The gate never lets a side pass the other (gate_move, cap_pair); an older artifact whose sides swapped
    # closed the split completely, so it counts as 0, never as the distance between the swapped values.
    gap_after = None if status == "failed" else int(max(0, fin_hi - fin_lo))
    # Closed on data: a side moved more than the free move towards the other side on qualifying crux
    # data (the free 5 points never count). That alone does not end the split: a responder can copy a
    # crux-hit line. The split only stops being live when the neutral crux check ran on THIS debate and
    # confirmed the data: resolved 'yes', or 'partly' leaning the way the mover moved. Otherwise the
    # block stays and is marked narrowed_on_data.
    # A mover is any side whose gated move towards the other side is more than the free move, whatever its
    # evidence source (seventh review, 2026-10-04: a side that came down 15 on 'other' evidence beside a side
    # that came up 15 on crux data is two movers; counting only the crux-data side let the referee confirm a
    # split that both sides had collapsed). A side holds when its gated move is at most FREE_MOVE either way.
    closed, movers, held_sides = False, [], 0
    for side, m in (("high", resp_of.get("high")), ("low", resp_of.get("low"))):
        if not m:
            continue
        mv = m["move"]
        toward = (mv["delta"] < 0) if side == "high" else (mv["delta"] > 0)
        if toward and abs(mv["delta"]) > FREE_MOVE:
            movers.append({"lean": "lower" if side == "high" else "higher", "source": mv["evidence_source"]})
            if mv["evidence_source"] == "crux_data":
                closed = True
        elif abs(mv["delta"]) <= FREE_MOVE:
            held_sides += 1
    cc = crux_check or {}
    # The referee confirms a closure only when exactly one side moved, it moved on crux data, the other side
    # answered and held (|delta| <= FREE_MOVE), the verdict points the way the mover moved ('yes' and 'partly'
    # alike: a 'yes, leans higher' after the high side came down says the data favours the high view; 'higher'
    # is the view that started higher, crux_check.md), and its quote would qualify a move: a strict single
    # data line, not social, not a market odds line, about the pair's crux (ph_cruxcheck sets quote_qualifies;
    # an artifact without it confirms nothing). Two movers confirm nothing: a lean towards one view says the
    # other side was wrong to move, so the block stays.
    quote_ok = cc.get("quote_qualifies") is True
    confirmed = bool(closed and crux_check and quote_ok and len(movers) == 1 and held_sides == 1
                     and movers[0]["source"] == "crux_data"
                     and cc.get("resolved") in ("yes", "partly") and cc.get("leans") == movers[0]["lean"])
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
        "tiers": {s: str((((resp_of.get(s) or {}).get("calls") or [{}])[0] or {}).get("tier") or side_tier(a))
                  for s, a in (("high", hi), ("low", lo))},
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
    block (take.md asks for one per position; a market odds line does not count). {per_agent: {agent: x}, mean: x}."""
    per = {}
    for a, t in takes.items():
        body = evidence.norm((lens.get(a) or {}).get("text") or "")
        pos = (t or {}).get("positions") or []
        if not pos:
            continue
        hit = sum(1 for p in pos if body and any(len(qnorm(e.get("quote"))) >= 12 and qnorm(e.get("quote")) in body
                                                 and not odds_line(e.get("quote") or "")
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
        cls = ev_class(loc, locator)
        if loc.get("status") == "verified" and (cls == "data" or (not data_only and cls != "market")):
            return q[:200]
    return ""


def crux_check_usable(crux_check: dict | None) -> bool:
    """A crux check whose words may reach the brief: its quote was found verbatim (quote_status 'verified')
    and would qualify a move (quote_qualifies: strict, data, not an odds line, about the pair's crux).
    A referee whose citation failed is not shown: its free text could carry an unverified figure into
    WHERE THE VIEWS SPLIT and WHAT TO WATCH, and its settles_on does not replace the question's own."""
    cc = crux_check or {}
    return bool(cc.get("data") and cc.get("quote_status") == "verified" and cc["data"].get("quote_qualifies") is True)


def _crux_block(crux_check: dict | None, qid: str) -> dict | None:
    if not (crux_check and crux_check.get("question_id") == qid and crux_check_usable(crux_check)):
        return None
    c = crux_check["data"]
    return {"resolved": c.get("resolved", ""), "what_the_data_says": clean_text(c.get("what_the_data_says", ""), 500),
            "quote": (c.get("quote") or "")[:200]}


def split_sheet(day: str, run_id: str, day_type: str, questions: list[dict], take_p: dict, finals: dict,
                debates: list[dict], challenges: dict, responses: dict, takes: dict, locator, gap_min: int,
                red_team: dict | None = None, red_team_agent: str | None = None, crux_check: dict | None = None,
                ledger: list[dict] | None = None, unpaired: list[str] | None = None,
                lone_outliers: list[str] | None = None) -> dict:
    """§11.1-11.2. unpaired: question ids pairing.json lists as unpaired (no pair formed although a candidate
    pair existed: budget, ceiling or load cap; or a split with no eligible pair at all); they get the
    split_unpaired bar on any day type. challenges: {(challenger, qid): record}; responses: {(agent, qid): record};
    takes: {agent: TAKE}; crux_check: {question_id, data, quote_status} when §8 ran (shown only when
    crux_check_usable(); otherwise the block falls back to the question's resolves_on and settles_with).
    lone_outliers: question ids pairing.json lists as lone outliers; like any question whose take range reaches
    the bar only through one lens (lone_lens), they get no undebated block (§4.2, §20.7 #83)."""
    ledger = ledger or []
    unp = set(unpaired or [])
    lone = set(lone_outliers or [])

    def low_bar(qid):
        return day_type == "split_unpaired" or qid in unp
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
                   and ev_class(locator.locate(e.get("quote", "")), locator) == "data")

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
        # One lens carrying the range is not a split (§4.2): without this, rule 2 below (minority_count >= 2,
        # range >= 40) built a direction block on 45..62 + 100 (trimmed range 17) with the outlier in the majority.
        if q["id"] in lone or lone_lens(list(tk(q["id"]).values()), gap_min):
            continue
        rng = st_take["range"] or 0
        if low_bar(q["id"]):
            ok = rng >= gap_min and (st_fin["minority_count"] >= 1 or rng >= gap_min)
        else:
            ok = st_fin["minority_count"] >= 2 and rng >= 40
        if ok:
            cands.append((1, -int(q.get("weight", 1) or 1) * rng, q["id"]))
    cands.sort()
    blocks, order = [], []
    held_any = any(d.get("held_split") for d in debates)
    for _, _, qid in cands:
        q = qby[qid]
        # A debated block is about the split the debate was on: its type, counts and count phrase come from
        # the take values (gap_before), not from post-debate finals, so a direction split two responders
        # narrowed never prints as 'all 9 lenses lean yes' beside a minority view (§11.2, review 2026-10-04).
        f = tk(qid) if qid in dby else fin(qid)
        st = question_stats(list(f.values()))
        typ = "direction" if st["minority_count"] >= 1 else "degree"
        if typ == "degree":
            rng = st["range"] or 0
            need = gap_min if low_bar(qid) else 30
            dd = dby.get(qid)
            if rng < need and not (dd and dd.get("gap_before", 0) >= gap_min):
                continue
            # A degree split the debate closed below gap_min is not a split worth a block when a held split
            # exists (seventh review, 2026-10-04: 09-10 led with a 27 -> 17 degree block above the held Hormuz split).
            if dd and held_any and dd.get("gap_after") is not None and dd["gap_after"] < gap_min:
                continue
        side = st["majority_side"]
        on_maj = (lambda v: v > 50) if side == "yes" else ((lambda v: v < 50) if side == "no" else (lambda v: v >= 50))
        d = dby.get(qid)
        base_text, base_quote, min_text, min_quote, min_src, crux = "", "", "", "", "reason", ""
        wcm_dates = []
        min_agent = None
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
        cc = _crux_block(crux_check, qid)
        if cc and (crux_check["data"].get("settles_on") or {}).get("observable"):
            so = crux_check["data"]["settles_on"]
            settles = {"observable": so.get("observable", ""), "by_date": so.get("by_date", "") if _DATE.match(so.get("by_date", "") or "") else ""}
        elif q.get("resolves_on") or q.get("settles_with"):
            settles = {"observable": q.get("settles_with", ""), "by_date": q.get("resolves_on", "")}
        else:
            settles = {"observable": "", "by_date": min(wcm_dates) if wcm_dates else ""}
        settles = {"observable": anonymise(settles.get("observable", "")), "by_date": settles.get("by_date", "")}
        # A degree block: every lens leans the same way and the split is how far, so the base case states its own
        # level ('most lenses at 75–90%') and the minority its value: a cautious base case under 'all 9 lenses
        # lean yes' must not read as the opposite side (seventh review, 2026-10-04).
        base_level = min_level = ""
        if typ == "degree" and f:
            vals = sorted(f.values())
            q1, q3 = statistics.quantiles(vals, n=4, method="inclusive")[::2] if len(vals) >= 2 else (vals[0], vals[0])
            base_level = _rng([q1, q3])
            if d and min_agent and min_agent in f:
                min_level = f"{int(round(f[min_agent]))}%"
        note = ""
        if d and d.get("narrowed_on_data"):
            fq = fin(qid)
            note = narrowed_note(f.get(min_agent) if min_agent else None, fq.get(min_agent) if min_agent else None,
                                 d.get("gap_before"), d.get("gap_after"))
        blocks.append({
            "type": typ, "debated": bool(d), "question_id": qid, "ledger_id": q.get("ledger_id", ""),
            "question": anonymise(q.get("text", "")), "resolves_on": q.get("resolves_on", ""),
            "settles_with": anonymise(q.get("settles_with", "")),
            "narrowed_on_data": bool(d and d.get("narrowed_on_data")), "narrowed_note": note,
            "counts": counts_of(f), "count_phrase": count_phrase(f, typ),
            "base_case": {"text": clean_text(base_text, 700), "quote": base_quote, "level": base_level},
            "minority_case": {"text": clean_text(min_text, 900), "quote": min_quote, "source": min_src,
                              "level": min_level},
            "crux": clean_text(crux, 300) if d else "", "crux_check": cc, "settles_on": settles,
            "carried": carried(q),
        })
        # held and live splits first, then the other debated blocks, then the undebated ones; direction before
        # degree within each (seventh review). §11.1 'debated first': ranking direction above debated before the
        # [:3] cut let three undebated direction splits push out a debated degree block closed on data, and its
        # narrowed note with it. A closed degree block still goes when a held split exists (#70, above).
        order.append((0 if d and (d.get("held_split") or d.get("live_split")) else 1, not d,
                      0 if typ == "direction" else 1))
    blocks = [b for _, _, b in sorted(zip(order, range(len(blocks)), blocks), key=lambda x: (x[0], x[1]))][:3]
    if not blocks and red_team and red_team.get("data"):
        rt = red_team["data"]
        qid = red_team.get("question_id") or rt.get("question_id")
        q = qby.get(qid)
        if q:
            f = fin(qid)
            st = question_stats(list(f.values()))
            close = sorted(f, key=lambda a: (abs(f[a] - st["median"]), -verified_data(a, qid), a))
            bt, ev = reason_of(close[0], qid) if close else ("", [])
            cc = _crux_block(crux_check, qid)
            wc = rt.get("would_change_my_mind") or {}
            so = (crux_check or {}).get("data", {}).get("settles_on") if cc else None
            settles = ({"observable": so.get("observable", ""), "by_date": so.get("by_date", "")} if so else
                       {"observable": q.get("settles_with") or wc.get("observable", ""),
                        "by_date": q.get("resolves_on") or (wc.get("by_date", "") if _DATE.match(wc.get("by_date", "") or "") else "")})
            settles = {"observable": anonymise(settles.get("observable", "")), "by_date": settles.get("by_date", "")}
            blocks.append({
                "type": "consensus", "debated": False, "question_id": qid, "ledger_id": q.get("ledger_id", ""),
                "question": anonymise(q.get("text", "")), "resolves_on": q.get("resolves_on", ""),
                "settles_with": anonymise(q.get("settles_with", "")), "narrowed_on_data": False, "narrowed_note": "",
                "counts": counts_of(f), "count_phrase": count_phrase(f, "consensus"),
                "base_case": {"text": clean_text(bt, 700), "quote": _first_quote(ev, locator), "level": ""},
                "minority_case": {"text": clean_text(rt.get("case", ""), 900),
                                  "quote": _first_quote(rt.get("evidence"), locator, data_only=False), "source": "red_team",
                                  "level": ""},
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


NARROWED_NOTE = "New data narrowed this split today without settling it."


def narrowed_note(min_before, min_after, gap_before, gap_after) -> str:
    """The reader-facing line for a block new data narrowed without settling (narrowed_on_data), with the minority
    view's take and final values and the pair's gap before and after. The block's case texts and count phrase are
    pre-debate, so without this the brief gives the old position with no sign it moved (09-11 c11 q3: '58% the
    better estimate' after the minority view went 58 -> 52 and the gap 27 -> 21)."""
    bits = []
    if min_before is not None and min_after is not None and round(min_before) != round(min_after):
        bits.append(f"the minority view went from {int(round(min_before))}% to {int(round(min_after))}%")
    if gap_before is not None and gap_after is not None and gap_after < gap_before:
        bits.append(f"the gap between the views from {int(gap_before)} to {int(gap_after)} points")
    return NARROWED_NOTE[:-1] + (": " + ", and ".join(bits) if bits else "") + "."


def block_note(bl: dict) -> str:
    """The narrowed note a block carries ('' when not narrowed); an older sheet without the field gets the plain line."""
    if not bl.get("narrowed_on_data"):
        return ""
    return bl.get("narrowed_note") or NARROWED_NOTE


def render_split_sheet(sheet: dict) -> str:
    out = [f"# SPLIT SHEET — {sheet.get('day', '')} ({sheet.get('day_type', '')})", ""]
    if not sheet.get("blocks"):
        out.append(sheet.get("no_split_line") or "no split today")
        return "\n".join(out) + "\n"
    for n, bl in enumerate(sheet["blocks"], 1):
        out.append(f"## Block {n} [{bl['type']}{', undebated' if not bl['debated'] and bl['type'] != 'consensus' else ''}]")
        out.append(f"Question: {bl['question']}")
        out.append(f"Count phrase (copy exactly): {bl['count_phrase']}")
        blv, mlv = bl["base_case"].get("level"), bl["minority_case"].get("level")
        out.append(f"Base case{f' (most lenses at {blv})' if blv else ''}: {bl['base_case']['text']}"
                   + (f" (data: \"{bl['base_case']['quote']}\")" if bl['base_case']['quote'] else ""))
        label = "Red-team case" if bl["type"] == "consensus" else "Minority view"
        out.append(f"{label}{f' (at {mlv})' if mlv else ''}: {bl['minority_case']['text']}"
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
            out.append(f"Narrowed note (copy exactly): {block_note(bl)}")
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
            if loc["status"] == "verified" and ev_class(loc, locator) == "data":
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

# Snake case only across the whole brief. A hyphenated name counts there with 'the' (THE_NAMES) or in upper case;
# 'the User-Agent header' is real tech news, so a hyphenated user-agent counts only in the two debate-fed sections.
SNAKE_NAMES = re.compile(r"(?i)\b(policy_analyst|user_agent|macro_strategist|ai_engineer)\b")
# Checked across the whole brief: upper-case names ('MACRO_STRATEGIST', 'ANALYST'), '### NAME' headings,
# 'Analyst: WRONG' labels, and 'the macro strategist' (multi-word names with 'the'). Title-case single names
# and bare spaced names stay limited to the two debate-fed sections, where real news cannot hit them.
UPPER_NAMES = re.compile(r"\b(POLICY[ _-]ANALYST|USER[ _-]AGENT|MACRO[ _-]STRATEGIST|AI[ _-]ENGINEER|TRADER|NARRATOR"
                         r"|BUILDER|ANALYST|SKEPTIC)\b(?!S\b)")
HEAD_NAMES = re.compile(r"(?im)^#{1,6}\s*\**\s*(trader|narrator|builder|analyst|skeptic|policy[ _-]analyst|user[ _-]agent"
                        r"|macro[ _-]strategist|ai[ _-]engineer)\b")
LABEL_NAMES = re.compile(r"(?m)^\s*(?:[-*•]\s*)?(?:\*\*)?(Trader|Narrator|Builder|Analyst|Skeptic|Policy Analyst|User Agent"
                         r"|Macro Strategist|AI Engineer)(?:\*\*)?\s*(?:\*\*)?:")
# 'USER: WRONG': the scorecard header of user_agent's state file ('user_state.md' -> '### USER') as a label
USER_LABEL = re.compile(r"(?m)^\s*(?:[-*•]\s*)?(?:\*\*)?USER(?:\*\*)?\s*:|^#{1,6}\s*\**\s*USER\s*$")
THE_NAMES = re.compile(r"(?i)\bthe (policy[ _-]analyst|user[ _]agent|macro[ _-]strategist|ai[ _-]engineer)\b(?!s\b)")
SPACED_NAMES = re.compile(r"(?i)\b(policy[ _-]analyst|user[ _-]agent|macro[ _-]strategist|ai[ _-]engineer)\b(?!s\b)")
TITLE_NAMES = re.compile(r"\b(Policy[ -]Analyst|User[ -]Agent|Macro[ -]Strategist|AI[ -]Engineer|Trader|Narrator|Builder"
                         r"|Analyst|Skeptic)\b(?!s)(?!\.ai)(?! [A-Z][a-z])")
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


def _cov_norm(t: str) -> str:
    t = re.sub(r"[*_`]", "", (t or "").lower())
    t = re.sub(r"\s*[\u2010-\u2015\u2212-]\s*", "-", t)
    return re.sub(r"\s+", " ", t).strip(" .")


def split_missing(split: str, blocks: list[dict]) -> list[str]:
    """§11.5 coverage: every split-sheet block must reach WHERE THE VIEWS SPLIT. A block is covered by its
    count phrase as written, or else by its majority 'N of M' ('all N' on a consensus block or when every lens is on one side); each
    printed 'N of M' / 'all N' covers one block only, so two 7-of-9 blocks need two. A 'broadly agree' line on a
    sheet with a direction or degree block is flagged too (09-11 c8: a second block, or the whole section
    replaced by 'The lenses broadly agree today.', went unflagged and run.json said 'ok'). A block narrowed on data
    needs its narrowed note as written (09-11 c11 q3: the note was left out and the run said 'ok')."""
    body = _cov_norm(split)
    whole = body
    out, keys = [], []
    for i, bl in enumerate(blocks, 1):
        cp = _cov_norm(bl.get("count_phrase", ""))
        if cp and cp in body:
            body = body.replace(cp, " ", 1)
            continue
        keys.append((i, bl))
    for i, bl in keys:
        c = bl.get("counts") or {}
        n, maj = int(c.get("n", 0) or 0), int(c.get("majority", 0) or 0)
        key = f"all {n}" if bl.get("type") == "consensus" or maj in (0, n) else f"{maj} of {n}"
        m = re.search(rf"\b{re.escape(key)}\b", body)
        if n and m:
            body = body[:m.start()] + " " + body[m.end():]
            continue
        out.append(f"block {i} ({bl.get('type', '')}) not in WHERE THE VIEWS SPLIT: "
                   f"{bl.get('count_phrase') or key} on \"{(bl.get('question') or '')[:80]}\"")
    # A narrowed block's note must be copied as written, like a count phrase: the case texts are pre-debate.
    for i, bl in enumerate(blocks, 1):
        note = block_note(bl)
        if note and _cov_norm(note) not in whole:
            out.append(f"block {i} narrowed note not in WHERE THE VIEWS SPLIT: \"{note}\" on "
                       f"\"{(bl.get('question') or '')[:80]}\"")
    if any(bl.get("type") != "consensus" for bl in blocks) and re.search(r"\bbroadly agree", split or "", re.I):
        out.append(f"'broadly agree' printed with {len(blocks)} split block(s) on the sheet")
    return out


def brief_checks(brief: str, sheet: dict | None) -> dict:
    whole = []
    for rx in (SNAKE_NAMES, UPPER_NAMES, HEAD_NAMES, LABEL_NAMES, USER_LABEL, THE_NAMES):
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
        # lower-case role nouns used as names: the forms anonymise() rewrites ('As the skeptic,', 'the skeptic and the trader')
        hits += [(m.start(), m.group(0)) for m in _ROLE_RX.finditer(body)
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
            "split_missing": split_missing(split, blocks),
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
