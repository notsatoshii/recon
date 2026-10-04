"""Programmatic evidence checks (no LLM).

- verify_quote: is an agent's evidence quote really in the package?
- NumberIndex / DateIndex / brief_claims: are the numbers and dates in the brief in the package, the raw
  data or the scorecard?
- prediction_expiry: a scorecard prediction's expiry, computed from its first stated horizon.
- url_check, repeated_numbers, citation_overlap: cheap format and diversity checks.

Matching is deliberately simple and explainable: normalised substring for quotes, then a
shingle overlap with every number present for "partial"; numbers match within 0.6 % (or 0.05
absolute below 10), so "$85K" matches 84,848 and "86.6B" matches 86.61B.
"""
from __future__ import annotations

import bisect
import calendar
import math
import re
from datetime import date, timedelta
from itertools import combinations

_TRANS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                        "−": "-", " ": " ", " ": " ", " ": " "})


def norm(text: str) -> str:
    t = (text or "").translate(_TRANS).lower()
    t = re.sub(r"[*_`#>|]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


# ── numbers ───────────────────────────────────────────────────

_MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9,
         "t": 1e12, "tn": 1e12, "trillion": 1e12}
_NUM = re.compile(
    r"(?<![\w.])(?P<sign>[-+])?(?P<cur>[$€£₩])?\s?(?P<n>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?P<suf>%|k|m|mn|b|bn|t|tn|thousand|million|billion|trillion)\b|(?P<pct>%))?",
    re.I)
_SKIP = re.compile(r"\b\d{4}-\d\d-\d\d\b|\b\d{1,2}:\d\d(?::\d\d)?\b|\bq[1-4]\b|\b(?:19|20)\d\d-\d\d\b", re.I)


def numbers(text: str) -> list[dict]:
    """Significant numbers in text: [{raw, value, scaled}]. Skips dates, times, years, and bare
    integers up to 31 (days, counts, list numbers)."""
    t = re.sub(r"https?://\S+", " ", (text or "").translate(_TRANS))
    t = _SKIP.sub(" ", t)
    out = []
    for m in _NUM.finditer(t):
        n = m.group("n")
        suf = (m.group("suf") or m.group("pct") or "").lower()
        try:
            v = float(n.replace(",", ""))
        except ValueError:
            continue
        bare_int = "." not in n and not suf and not m.group("cur")
        if bare_int and (v <= 31 or 1900 <= v <= 2100):
            continue
        scaled = v * _MULT.get(suf, 1.0)
        out.append({"raw": m.group(0).strip(), "value": v, "scaled": scaled, "pct": suf == "%",
                    "cur": m.group("cur") or ""})
    return out


def _close(a: float, b: float) -> bool:
    if a == b:
        return True
    if abs(a) < 10 and abs(b) < 10:
        return abs(a - b) <= 0.051
    return abs(a - b) <= 0.006 * max(abs(a), abs(b))


class NumberIndex:
    """Sorted values from a set of named documents; lookup within the tolerance above."""

    def __init__(self, docs: dict[str, str]):
        pairs = []
        for name, text in docs.items():
            for x in numbers(text):
                pairs.append((x["value"], name))
                if x["scaled"] != x["value"]:
                    pairs.append((x["scaled"], name))
        pairs.sort()
        self.vals = [p[0] for p in pairs]
        self.names = [p[1] for p in pairs]

    def find(self, x: dict) -> str | None:
        for v in {x["value"], x["scaled"]}:
            lo = bisect.bisect_left(self.vals, v - max(0.051, abs(v) * 0.006))
            hi = bisect.bisect_right(self.vals, v + max(0.051, abs(v) * 0.006))
            for i in range(lo, hi):
                if _close(v, self.vals[i]):
                    return self.names[i]
        return None


# ── dates ─────────────────────────────────────────────────────

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS["sept"] = 9
_MON = (r"(?:January|February|March|April|May|June|July|August|September|October|November|December|"
        r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\b")
# Capitalised month names only, so 'may 5' (the verb) is no date; a written day is 1-31, not followed by a digit
# or a clock colon ('Sept 12:30'); 'September 12:' (a WHAT TO WATCH lead) still counts.
_DATE = re.compile(
    r"(?<!\d)(?P<iy>(?:19|20)\d\d)-(?P<im>\d\d)-(?P<id>\d\d)(?!\d)"
    r"|(?<!\w)(?P<m1>" + _MON + r")\.?\s+(?P<d1>[0-3]?\d)(?:st|nd|rd|th)?(?!\d|:\d)(?:,?\s+(?P<y1>(?:19|20)\d\d)\b)?"
    r"|(?<![\w:.$])(?P<d2>[0-3]?\d)(?:st|nd|rd|th)?\s+(?P<m2>" + _MON + r")\.?(?:,?\s+(?P<y2>(?:19|20)\d\d)\b)?")


def dates(text: str) -> list[dict]:
    """Calendar dates in text: [{raw, key 'MM-DD', year 'YYYY' or ''}]. ISO dates and written dates
    ('September 24', 'Sept. 24, 2026', '24 September 2026'); a month without a day ('August 2026') is no date."""
    t = re.sub(r"https?://\S+", " ", (text or "").translate(_TRANS))
    out = []
    for m in _DATE.finditer(t):
        if m.group("iy"):
            y, mo, d = m.group("iy"), int(m.group("im")), int(m.group("id"))
        else:
            mo = _MONTHS[(m.group("m1") or m.group("m2")).lower()]
            d = int(m.group("d1") or m.group("d2"))
            y = m.group("y1") or m.group("y2") or ""
        if 1 <= mo <= 12 and 1 <= d <= 31:
            out.append({"raw": m.group(0).strip(), "key": f"{mo:02d}-{d:02d}", "year": y})
    return out


class DateIndex:
    """The dates in a set of named documents. A brief date with a year matches the same date, or the same
    month and day written without a year; a brief date without a year matches that month and day in any year."""

    def __init__(self, docs: dict[str, str]):
        self.full: dict[str, str] = {}
        self.md: dict[str, str] = {}
        self.yearless: dict[str, str] = {}
        for name, text in docs.items():
            for x in dates(text):
                self.md.setdefault(x["key"], name)
                if x["year"]:
                    self.full.setdefault(f"{x['year']}-{x['key']}", name)
                else:
                    self.yearless.setdefault(x["key"], name)

    def find(self, x: dict) -> str | None:
        if x["year"]:
            return self.full.get(f"{x['year']}-{x['key']}") or self.yearless.get(x["key"])
        return self.md.get(x["key"])


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "eleven": 11, "twelve": 12}
_N = r"(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
# 'over the next 3-6 months', 'within 90 days', 'Over 1-2 weeks', 'next 72 hours', 'a 6-month window' is no horizon
_H_NUM = re.compile(r"(?i)\b(?:(?:over|within|in|for|during)\s+(?:the\s+)?(?:(?:next|coming|following)\s+)?|"
                    r"(?:next|coming)\s+)(?:(?P<lo>" + _N + r")\s*(?:-|to)\s*)?(?P<n>" + _N + r")\s*-?\s*"
                    r"(?P<unit>hours?|days?|sessions?|weeks?|months?|quarters?|years?)\b")
# 'over the next session', 'within the next week', 'next month', 'this week'
_H_ONE = re.compile(r"(?i)\b(?:(?:over|within|in|for|during|through)\s+)?(?:the\s+)?(?P<w>next|coming|this|following)\s+"
                    r"(?P<unit>session|day|week|month|quarter|year)\b")
_H_WORD = re.compile(r"(?i)\b(?P<w>tomorrow|today)\b")
# 'by April 17', 'by August 2026', 'by Q3 2026', 'by year-end'
_H_BY = re.compile(r"\b(?:[Bb]y|[Bb]efore|[Uu]ntil)\s+(?:(?:the\s+)?end\s+of\s+(?=[A-Z]))?(?:(?P<mon>" + _MON +
                   r")\.?(?:\s+(?P<d>[0-3]?\d)(?:st|nd|rd|th)?(?!\d))?(?:,?\s+(?P<y>(?:19|20)\d\d))?"
                   r"|(?P<q>Q[1-4])\s+(?P<qy>(?:19|20)\d\d)|(?P<ye>year[- ]end|(?:the\s+)?end\s+of\s+(?:the\s+)?year\b))")
# 'by 2026-09-18', 'through 2026-09-30', 'on 2026-10-02': ISO dates, the format the orchestrator writes itself
_H_ISO = re.compile(r"(?i)\b(?:by|before|until|through|thru|on)\s+(?:the\s+)?(?P<iso>(?:19|20)\d\d-\d\d-\d\d)\b")
# '(p=60%; resolves 2026-09-18; metric)': agentmem's structured resolves_on, which outranks any prose horizon
_H_RES = re.compile(r"(?i)\bresolves\s+(?:on\s+|by\s+)?(?P<iso>(?:19|20)\d\d-\d\d-\d\d)\b")


def _plus(made: date, n: float, unit: str) -> date:
    u = unit.lower().rstrip("s")
    if u == "hour":
        return made + timedelta(days=max(1, math.ceil(n / 24)))
    if u in ("day", "session"):
        return made + timedelta(days=math.ceil(n))
    if u == "week":
        return made + timedelta(days=math.ceil(7 * n))
    return _add_months(made, math.ceil({"month": 1, "quarter": 3, "year": 12}[u] * n))


def _by_date(m: re.Match, made: date) -> date:
    if m.group("q"):
        y, mo = int(m.group("qy")), 3 * int(m.group("q")[1])
        return date(y, mo, calendar.monthrange(y, mo)[1])
    if m.group("ye"):
        return date(made.year, 12, 31)
    mo = _MONTHS[m.group("mon").lower()]
    y = int(m.group("y")) if m.group("y") else made.year
    day = int(m.group("d")) if m.group("d") else calendar.monthrange(y, mo)[1]
    end = date(y, mo, day)
    if not m.group("y") and end < made:
        end = date(y + 1, mo, min(day, calendar.monthrange(y + 1, mo)[1]))
    return end


def prediction_expiry(text: str, made: str) -> dict:
    """{horizon, expiry} for a scorecard prediction made on `made` (YYYY-MM-DD). The horizon is the first one
    the prediction states, as written ('Over the next session'); a range counts to its upper end ('3-6 months'
    -> +6 months); a session is a day; 'by April 17' is the first April 17 on or after `made`; 'by/through/on
    2026-09-18' is that date, and agentmem's structured 'resolves 2026-09-18' wins over any prose. No horizon ->
    both ''. Computed here so the synthesizer copies an expiry instead of deriving one: 09-11 c9 printed
    +3 years for 'Over the next session, ... over the next 2-3 years' (§20.7 #79)."""
    t = (text or "").translate(_TRANS)
    try:
        d0 = date.fromisoformat(made)
    except ValueError:
        return {"horizon": "", "expiry": ""}
    for m in _H_RES.finditer(t):
        try:
            return {"horizon": m.group(0).strip(), "expiry": date.fromisoformat(m.group("iso")).isoformat()}
        except ValueError:
            continue
    found = []
    for m in _H_ISO.finditer(t):
        try:
            found.append((m.start(), m.group(0), date.fromisoformat(m.group("iso"))))
            break
        except ValueError:
            continue
    m = _H_NUM.search(t)
    if m:
        n = m.group("n").lower()
        found.append((m.start(), m.group(0), _plus(d0, float(_WORDNUM.get(n, n)), m.group("unit"))))
    m = _H_ONE.search(t)
    if m:
        today = m.group("w").lower() == "this" and m.group("unit").lower() in ("day", "session")
        found.append((m.start(), m.group(0), d0 if today else _plus(d0, 1, m.group("unit"))))
    m = _H_WORD.search(t)
    if m:
        found.append((m.start(), m.group(0), d0 + timedelta(days=int(m.group("w").lower() == "tomorrow"))))
    for m in _H_BY.finditer(t):
        try:
            found.append((m.start(), m.group(0), _by_date(m, d0)))
            break
        except ValueError:
            continue
    if not found:
        return {"horizon": "", "expiry": ""}
    _, horizon, end = min(found, key=lambda f: f[0])
    return {"horizon": horizon.strip(), "expiry": end.isoformat()}


# ── quotes ────────────────────────────────────────────────────

def _shingles(words: list[str], k: int = 4) -> set[tuple]:
    return {tuple(words[i:i + k]) for i in range(max(1, len(words) - k + 1))}


class Corpus:
    """Normalised documents for quote checks plus a number index."""

    def __init__(self, docs: dict[str, str]):
        self.docs = {k: norm(v) for k, v in docs.items() if v}
        self.index = NumberIndex(docs)
        self._sh: dict[str, set] = {}

    def _doc_shingles(self, name: str) -> set:
        if name not in self._sh:
            self._sh[name] = _shingles(re.findall(r"[\w$%.,']+", self.docs[name]))
        return self._sh[name]

    def verify_quote(self, quote: str) -> dict:
        """{status: verified|partial|unverified|empty, doc, numbers_found, numbers_total}."""
        q = norm(quote).strip(" .\"'")
        if len(q) < 8:
            return {"status": "empty", "doc": None, "numbers_found": 0, "numbers_total": 0}
        nums = numbers(quote)
        found_nums = [x for x in nums if self.index.find(x)]
        parts = [p.strip(" .\"'") for p in re.split(r"\.\.\.|…|\[\.\.\.\]", q) if len(p.strip()) >= 12] or [q]
        for name, d in self.docs.items():
            if all(p in d for p in parts):
                return {"status": "verified", "doc": name, "numbers_found": len(found_nums), "numbers_total": len(nums)}
        words = re.findall(r"[\w$%.,']+", q)
        sh = _shingles(words)
        best, best_doc = 0.0, None
        for name in self.docs:
            ds = self._doc_shingles(name)
            r = len(sh & ds) / len(sh) if sh else 0.0
            if r > best:
                best, best_doc = r, name
        status = "partial" if best >= 0.6 and len(found_nums) == len(nums) else "unverified"
        return {"status": status, "doc": best_doc if status == "partial" else None,
                "numbers_found": len(found_nums), "numbers_total": len(nums), "overlap": round(best, 2)}


# ── brief checks ─────────────────────────────────────────────

def _segments(brief: str) -> list[tuple[str, str]]:
    """(section, sentence-or-bullet) pairs from the brief."""
    out, section = [], ""
    for line in brief.splitlines():
        s = line.strip()
        if not s:
            continue
        if re.match(r"^#{1,4} ", s):
            section = re.sub(r"^#+\s*|\*", "", s).strip().upper()
            continue
        s = re.sub(r"^[-*•]\s+|^\d+[.)]\s+", "", s)
        for part in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(])", s):
            if part.strip():
                out.append((section, part.strip()))
    return out


def brief_claims(brief: str, source_docs: dict[str, str], debate_docs: dict[str, str],
                 takes: dict[str, str]) -> list[dict]:
    """One claim per brief sentence or bullet that carries a significant number or a calendar date.
    found_in_source is true when every number and date in it is in the package, the raw data or the scorecard
    the synthesizer read. A number or date found only in the debate (an agent's own arithmetic, a split
    sheet's settles-on date) is reported as such. Dates count since 09-11 c9: the SCORECARD shipped an
    expiry of 2029-09-10 that no source holds, and a numbers-only check passed it (§20.7 #79)."""
    src, deb = NumberIndex(source_docs), NumberIndex(debate_docs)
    src_d, deb_d = DateIndex(source_docs), DateIndex(debate_docs)
    take_idx = {a: NumberIndex({a: t}) for a, t in takes.items()}
    claims = []
    for section, seg in _segments(brief):
        nums, ds = numbers(seg), dates(seg)
        if not nums and not ds:
            continue
        missing, where, debate_only = [], set(), []
        for x, idx, didx in [(x, src, deb) for x in nums] + [(x, src_d, deb_d) for x in ds]:
            hit = idx.find(x)
            if hit:
                where.add(hit)
                continue
            if didx.find(x):
                debate_only.append(x["raw"])
            else:
                missing.append(x["raw"])
        agents = sorted(a for a, ix in take_idx.items() if any(ix.find(x) for x in nums))
        found = not missing and not debate_only
        if found:
            action = "none"
        elif missing:
            action = "flag: not in the package, raw data or debate: " + ", ".join(missing[:4])
        else:
            action = "note: derived in the debate, not in the package: " + ", ".join(debate_only[:4])
        claims.append({"claim": seg[:300], "section": section, "found_in_source": found,
                       "source": ", ".join(sorted(where)) if where else ("debate" if debate_only and not missing else ""),
                       "action": action, "agents": agents, "numbers": [x["raw"] for x in nums][:8],
                       "dates": [x["raw"] for x in ds][:8]})
    return claims


def url_check(brief: str, corpus_text: str) -> dict:
    urls = sorted(set(u.rstrip(").,;]>'\"") for u in re.findall(r"https?://[^\s)<>\]]+", brief)))
    missing = [u for u in urls if u not in corpus_text]
    return {"urls": len(urls), "missing": missing[:20]}


def repeated_numbers(brief: str, min_sections: int = 3) -> list[dict]:
    """Numbers (with a unit or 3+ digits) that appear in min_sections or more sections (F22)."""
    seen: dict[str, set] = {}
    for section, seg in _segments(brief):
        for x in numbers(seg):
            if x["value"] >= 100 or x["pct"] or x["scaled"] != x["value"]:
                key = f"{x['scaled']:.4g}{'%' if x['pct'] else ''}"
                seen.setdefault(key, set()).add(section)
    return [{"number": k, "sections": sorted(v)} for k, v in seen.items() if len(v) >= min_sections]


def citation_overlap(takes: dict[str, str]) -> dict:
    """Mean pairwise Jaccard overlap of the significant numbers each take cites (F6). 0 = all
    different, 1 = all the same."""
    sets = {a: {f"{x['scaled']:.4g}" for x in numbers(t) if x["value"] >= 100 or x["pct"] or x["scaled"] != x["value"]}
            for a, t in takes.items()}
    pairs = [(a, b) for a, b in combinations(sorted(sets), 2) if sets[a] or sets[b]]
    if not pairs:
        return {"mean_jaccard": None, "pairs": 0}
    vals = [len(sets[a] & sets[b]) / len(sets[a] | sets[b]) for a, b in pairs]
    return {"mean_jaccard": round(sum(vals) / len(vals), 3), "pairs": len(pairs),
            "numbers_per_take": {a: len(v) for a, v in sets.items()}}


# ── locate: where a quote is, with its package section and evidence class (Phase C §3 item 4) ──

SOCIAL_SECTIONS = ("SENTIMENT & MARKET MOOD", "SOCIAL INTELLIGENCE")
# '# <Name> Intelligence' blocks of 00_raw_data.md -> (package section, class)
RAW_BLOCKS = (("reddit", "SOCIAL INTELLIGENCE", "social"), ("twitter", "SOCIAL INTELLIGENCE", "social"),
              ("bettafish", "SENTIMENT & MARKET MOOD", "social"), ("on-chain", "ON-CHAIN & MARKET DATA", "data"),
              ("onchain", "ON-CHAIN & MARKET DATA", "data"), ("news", "NEWS INTELLIGENCE", "data"),
              ("ai & tools", "AI & TOOLS", "data"), ("fundraising", "FUNDRAISING", "data"),
              ("polymarket", "PREDICTION MARKETS", "data"), ("kalshi", "PREDICTION MARKETS", "data"),
              ("changelogs", "AI & TOOLS", "data"), ("zdnet", "NEWS INTELLIGENCE", "data"),
              ("world monitor", "GEOPOLITICAL CONTEXT", "data"))
DOC_ORDER = ("package", "raw", "view", "social")
_WORDS = re.compile(r"[\w$%.,']+")
# A line is social by its content, in any section (package SECTION 0 CROSS-SOURCE SIGNALS is made of
# tweets and Reddit titles): an X line '[Mon DD HH:MM] (…♥…🔁…)' (raw, or re-rendered '- @who [...]'),
# an X or Reddit URL, or an 'Also in:' line that names r/… or @… sources.
SOCIAL_LINE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:@\S+\s+)?\[(?:[A-Z][a-z]{2} \d{1,2}(?:, \d{4})?|\d{4}-\d\d-\d\d) \d\d:\d\d\]\s*\("
    r"|https?://(?:www\.|mobile\.|old\.)?(?:x\.com|twitter\.com|t\.co|reddit\.com|redd\.it)/"
    r"|^\s*\*?\s*Also in:.*(?:(?<![\w/])r/\w+|@\w+)", re.I)
_CORE_STRIP = re.compile(r"https?://\S+|^\s*[-*]\s*|@\S+\s+(?=\[)|\[[^\]]{0,40}\]\s*|\([^)]*[♥🔁💬][^)]*\)\s*")
STRICT_MIN_CHARS = 40     # a qualifying quote is at least this long, or carries a number


def _section_of(name: str) -> tuple[str, str]:
    n = name.strip().upper()
    return n, ("social" if any(n.startswith(s) for s in SOCIAL_SECTIONS) else "data")


def social_line(line: str) -> bool:
    return bool(SOCIAL_LINE.search(line or ""))


def _core(line: str) -> str:
    """A line without its URL, X bracket and engagement counts: what makes two renderings the same item."""
    return norm(_CORE_STRIP.sub(" ", line or "")).strip(" .\"'")


class Locator:
    """Quote lookup over the run's documents (package, raw, view, social), built once per run.

    locate(quote) -> {status, section, cls, doc, line, numbers_found, numbers_total}; `line` is the
    1-based line number in `doc`. `section` comes from the package section of the hit (raw file: the
    '# <Name> Intelligence' block; view: its '# SECTION: <name>' label; 01_social.md: social). `cls` is
    `social` for the social sections and blocks and for any line that is social by content
    (SOCIAL_LINE), `data` otherwise. When the same item has a social and a data rendering it is social;
    a data hit that is a different item wins.

    Status: `verified` = the whole quote verbatim (normalised) and every number in it found. A quote
    stitched with '...' never verifies: at best `partial`, and only when every piece is found (pieces
    under 12 characters included) and every number is found. Otherwise `partial` = 4-gram overlap
    >= 0.6 with every number present, anchored to the single line with the highest 4-gram overlap.

    strict(quote) is the evidence-gate check (§7.2): a single-line verbatim match with no ellipsis,
    every number found, and at least STRICT_MIN_CHARS characters or a number. locate() stays loose
    (reporting, eligibility, excerpts); only strict() lets a quote justify a move."""

    def __init__(self, docs: dict[str, str]):
        self.raw_docs = {k: v for k, v in docs.items() if v}
        self.lines: dict[str, list[str]] = {k: v.split("\n") for k, v in self.raw_docs.items()}
        self.index = NumberIndex(self.raw_docs)
        self._norm: dict[str, tuple[str, list[int], list[int], list[int]]] = {}
        self._labels: dict[str, list[tuple[str, str]]] = {}
        self._doc_sh: dict[str, set] = {}
        self._line_sh: dict[str, list[set]] = {}
        self._cache: dict[str, dict] = {}
        self._strict: dict[tuple[str, bool], dict] = {}

    def order(self) -> list[str]:
        return [d for d in DOC_ORDER if d in self.lines] + [d for d in self.lines if d not in DOC_ORDER]

    def _normed(self, name: str) -> tuple[str, list[int], list[int], list[int]]:
        """(normalised text, start offset of each kept line, that line's index, its normalised length)."""
        if name not in self._norm:
            parts, starts, idx, lens, pos = [], [], [], [], 0
            for n, line in enumerate(self.lines[name]):
                t = norm(line)
                if not t:
                    continue
                starts.append(pos)
                idx.append(n)
                lens.append(len(t))
                parts.append(t)
                pos += len(t) + 1
            self._norm[name] = (" ".join(parts), starts, idx, lens)
        return self._norm[name]

    def labels(self, name: str) -> list[tuple[str, str]]:
        """(section, class) for every line of a document."""
        if name in self._labels:
            return self._labels[name]
        out, cur = [], ("", "data")
        for line in self.lines[name]:
            if name == "package":
                m = re.match(r"^# SECTION \d+: (.+)$", line)
                if m:
                    cur = _section_of(m.group(1))
            elif name == "view":
                m = re.match(r"^# SECTION(?: \d+)?: (.+)$", line)
                if m:
                    cur = _section_of(m.group(1))
            elif name == "raw":
                m = re.match(r"^# (.+?) Intelligence", line)
                if m:
                    h = m.group(1).lower()
                    hit = next(((sec, cls) for k, sec, cls in RAW_BLOCKS if k in h), None)
                    cur = hit or (m.group(1).upper(), "data")
            elif name == "social":
                cur = ("SOCIAL INTELLIGENCE", "social")
            out.append((cur[0], "social") if cur[1] == "data" and social_line(line) else cur)
        self._labels[name] = out
        return out

    def label(self, name: str, line: int) -> tuple[str, str]:
        lab = self.labels(name)
        return lab[line - 1] if 1 <= line <= len(lab) else ("", "data")

    def line_text(self, name: str, line: int) -> str:
        ls = self.lines.get(name) or []
        return ls[line - 1] if 1 <= line <= len(ls) else ""

    def _occurrences(self, name: str, part: str, limit: int = 8) -> list[tuple[int, bool]]:
        """[(1-based line, single_line)] for the first `limit` occurrences of a normalised part."""
        text, starts, idx, lens = self._normed(name)
        out, pos = [], text.find(part) if part else -1
        while pos >= 0 and len(out) < limit and idx:
            k = max(0, bisect.bisect_right(starts, pos) - 1)
            out.append((idx[k] + 1, pos + len(part) <= starts[k] + lens[k]))
            pos = text.find(part, pos + 1)
        return out

    def _pick(self, hits: list[tuple[str, int]]) -> tuple[str, int, str, str]:
        """The hit that classifies a quote: social when the data hits are the same item as a social hit."""
        lab = [(d, n, *self.label(d, n)) for d, n in hits]
        data = [h for h in lab if h[3] == "data"]
        social = [h for h in lab if h[3] == "social"]
        if data and social:
            cores = [_core(self.line_text(d, n)) for d, n, _, _ in social]
            for h in data:
                c = _core(self.line_text(h[0], h[1]))
                if not any(c and s and (c in s or s in c) for s in cores):
                    return h
            return social[0]
        return (data or social or lab)[0]

    def locate(self, quote: str) -> dict:
        key = quote or ""
        if key not in self._cache:
            self._cache[key] = self._locate(key)
        return dict(self._cache[key])

    def _locate(self, quote: str) -> dict:
        q = norm(quote).strip(" .\"'")
        nums = numbers(quote)
        found_nums = [x for x in nums if self.index.find(x)]
        all_nums = len(found_nums) == len(nums)
        base = {"numbers_found": len(found_nums), "numbers_total": len(nums)}
        if len(q) < 8:
            return {"status": "empty", "section": "", "cls": "", "doc": None, "line": None, **base}
        pieces = [p.strip(" .\"'") for p in re.split(r"\.\.\.|…|\[\.\.\.\]", q)]
        pieces = [p for p in pieces if p]
        stitched = len(pieces) > 1
        hits = []
        for name in self.order():
            text = self._normed(name)[0]
            if pieces and all(p in text for p in pieces):
                hits += [(name, n) for n, _ in self._occurrences(name, max(pieces, key=len))]
        if hits and all_nums:
            name, line, sec, cls = self._pick(hits)
            out = {"status": "partial" if stitched else "verified", "section": sec, "cls": cls, "doc": name,
                   "line": line, **base}
            if stitched:
                out["stitched"] = True
            return out
        sh = _shingles(_WORDS.findall(q))
        best, best_doc = 0.0, None
        for name in self.order():
            if name not in self._doc_sh:
                self._doc_sh[name] = _shingles(_WORDS.findall(self._normed(name)[0]))
            r = len(sh & self._doc_sh[name]) / len(sh) if sh else 0.0
            if r > best:
                best, best_doc = r, name
        if best >= 0.6 and all_nums and best_doc:
            line = self._anchor(best_doc, sh)
            sec, cls = self.label(best_doc, line)
            return {"status": "partial", "section": sec, "cls": cls, "doc": best_doc, "line": line,
                    "overlap": round(best, 2), **base}
        return {"status": "unverified", "section": "", "cls": "", "doc": None, "line": None,
                "overlap": round(best, 2), **base}

    def strict(self, quote: str, item: bool = False) -> dict:
        """§7.2 gate check: {ok, reason, doc, line, section, cls}. `item=True` (the referee's quote check, §8) also
        accepts a quote that runs from a list item's headline into its indented body and stays inside that item
        ('- [Thu, 10 Sep 2026] OpenAI puts Pro subscriptions on hold due to Astra demand' + '  The company said
        ...'); `line` is then the first line it covers. The gate keeps one line."""
        key = (quote or "", bool(item))
        if key not in self._strict:
            self._strict[key] = self._strict_check(*key)
        return dict(self._strict[key])

    def _item_occurrences(self, name: str, part: str, span: int = 3, limit: int = 8) -> list[int]:
        """1-based first lines of the occurrences of a normalised part that cross a line break but stay inside one
        list item: every line after the first is an indented, non-empty continuation (no blank line between),
        at most `span` lines below the first (debate._same_item's item)."""
        text, starts, idx, lens = self._normed(name)
        raw = self.lines[name]
        out, pos = [], text.find(part) if part else -1
        while pos >= 0 and len(out) < limit and idx:
            k0 = max(0, bisect.bisect_right(starts, pos) - 1)
            k1 = max(0, bisect.bisect_right(starts, pos + len(part) - 1) - 1)
            if 0 < k1 - k0 <= span and idx[k1] - idx[k0] == k1 - k0                     and all(raw[idx[k]][:1] in (" ", "	") for k in range(k0 + 1, k1 + 1)):
                out.append(idx[k0] + 1)
            pos = text.find(part, pos + 1)
        return out

    def _strict_check(self, quote: str, item: bool = False) -> dict:
        q = norm(quote).strip(" .\"'")
        out = {"ok": False, "reason": "", "doc": None, "line": None, "section": "", "cls": ""}
        if len(q) < 8:
            return {**out, "reason": "empty"}
        if re.search(r"\.\.\.|…|\[\.\.\.\]", q):
            return {**out, "reason": "stitched quote"}
        nums = numbers(quote)
        if any(not self.index.find(x) for x in nums):
            return {**out, "reason": "a number is not in the run folder"}
        if len(q) < STRICT_MIN_CHARS and not nums:
            return {**out, "reason": f"under {STRICT_MIN_CHARS} characters with no number"}
        hits = []
        for name in self.order():
            hits += [(name, n) for n, single in self._occurrences(name, q) if single]
        if not hits and item:
            for name in self.order():
                hits += [(name, n) for n in self._item_occurrences(name, q)]
        if not hits:
            return {**out, "reason": "not a single verbatim line"}
        name, line, sec, cls = self._pick(hits)
        return {"ok": True, "reason": "", "doc": name, "line": line, "section": sec, "cls": cls}

    def positions(self, quote: str) -> set[tuple[str, int]]:
        """Every (doc, line) a quote sits on (verbatim occurrences, or the anchor of a partial match)."""
        q = norm(quote).strip(" .\"'")
        if len(q) < 8:
            return set()
        pieces = [p.strip(" .\"'") for p in re.split(r"\.\.\.|…|\[\.\.\.\]", q) if p.strip(" .\"'")]
        out = set()
        for name in self.order():
            for p in pieces:
                out |= {(name, n) for n, _ in self._occurrences(name, p)}
        if not out:
            loc = self.locate(quote)
            if loc.get("doc"):
                out.add((loc["doc"], loc["line"]))
        return out

    def _anchor(self, name: str, sh: set) -> int:
        if name not in self._line_sh:
            self._line_sh[name] = [_shingles(_WORDS.findall(norm(l))) if l.strip() else set()
                                   for l in self.lines[name]]
        best, at = -1, 1
        for n, ls in enumerate(self._line_sh[name]):
            c = len(sh & ls)
            if c > best:
                best, at = c, n + 1
        return at


def locate(quote: str, docs) -> dict:
    """{status, section, cls, doc, line} for a quote over docs (package, raw, view, social). `docs` is a
    Locator (built once per run) or a dict of texts (a Locator is built for the call)."""
    loc = docs if isinstance(docs, Locator) else Locator(docs)
    return loc.locate(quote)
