"""Programmatic evidence checks (no LLM).

- verify_quote: is an agent's evidence quote really in the package?
- NumberIndex / brief_claims: are the numbers in the brief in the package or the raw data?
- url_check, repeated_numbers, citation_overlap: cheap format and diversity checks.

Matching is deliberately simple and explainable: normalised substring for quotes, then a
shingle overlap with every number present for "partial"; numbers match within 0.6 % (or 0.05
absolute below 10), so "$85K" matches 84,848 and "86.6B" matches 86.61B.
"""
from __future__ import annotations

import bisect
import re
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
    """One claim per brief sentence or bullet that carries a significant number. found_in_source is
    true when every number in it is in the package or the raw data the synthesizer read. A number
    found only in the debate (an agent's own arithmetic) is reported as such."""
    src = NumberIndex(source_docs)
    deb = NumberIndex(debate_docs)
    take_idx = {a: NumberIndex({a: t}) for a, t in takes.items()}
    claims = []
    for section, seg in _segments(brief):
        nums = numbers(seg)
        if not nums:
            continue
        missing, where, debate_only = [], set(), []
        for x in nums:
            hit = src.find(x)
            if hit:
                where.add(hit)
                continue
            if deb.find(x):
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
                       "action": action, "agents": agents, "numbers": [x["raw"] for x in nums][:8]})
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
        self._strict: dict[str, dict] = {}

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

    def strict(self, quote: str) -> dict:
        """§7.2 gate check: {ok, reason, doc, line, section, cls}."""
        key = quote or ""
        if key not in self._strict:
            self._strict[key] = self._strict_check(key)
        return dict(self._strict[key])

    def _strict_check(self, quote: str) -> dict:
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
