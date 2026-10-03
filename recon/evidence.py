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
        out.append({"raw": m.group(0).strip(), "value": v, "scaled": scaled, "pct": suf == "%"})
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
