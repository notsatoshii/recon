#!/usr/bin/env python3
"""Measure Phase C's lens extras (phase-c-spec §3 item 3 = phase-e-collectors-spec §4.5b).

A reference implementation of the LENS_RAW rules, used to pick the table's entries from what is
really outside the agent view and to record the bytes per agent in the specs. Phase C's
`recon/debate.py: lens_extras` implements the same rules and table (the unit test of §17.1 can
compare against this file); this file is the measuring tool, not the pipeline. No network, no LLM.

    python3 tests/lens_extras_probe.py <run_dir> [--rebuild-view] [--phase-e <data-sources dir>]
                                       [--inventory | --dump <agent> | --json]

--rebuild-view   rebuild 01_filtered.md with the current scripts/build_agent_package.py in a
                 scratch copy first, as a --replay does (ph_package reruns the builder).
--phase-e DIR    simulate the phase-e §4 wiring in a scratch copy: the four collector files from
                 DIR/<name>/latest.md go into 00_raw_data.md (§4.1a) and into the package
                 (§4.2: ZDNet at the end of SECTION 4, changelogs at the end of SECTION 6, a new
                 SECTION 8: PREDICTION MARKETS), and the view is rebuilt with the §4.4 caps.
--inventory      list every '#'/'##' heading of the raw file and the package: body bytes, and
                 the bytes of its body lines that are not in the view (what an entry can add).
--dump AGENT     print that agent's YOUR LENS DATA text.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import build_agent_package as bap  # noqa: E402

ENTRY_CAP = 3000
AGENT_CAP = 6000
WARN_BELOW = 2000

# The table of phase-c-spec §3 item 3 = phase-e §4.5b (third review, 2026-10-04). Entries are
# picked from what the measured views leave out: on 09-10, 09-11 and 10-04 every on-chain, World
# Monitor and AI & Tools line is already in the view, so those headings add nothing. Table order
# matters: a line goes to the first agent (in this order) that picks it, so the narrow lenses come
# first and the broad ones (whole Reddit and X blocks) last; macro_strategist precedes trader so
# Kalshi goes to macro and Polymarket to trader.
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

PHASE_E_CAPS = {"PREDICTION MARKETS": 20000, "AI & TOOLS": 9000, "NEWS INTELLIGENCE": 18000}
PHASE_E_FALLBACK = {"polymarket": "PREDICTION MARKETS", "kalshi": "PREDICTION MARKETS",
                    "changelogs": "AI & TOOLS", "zdnet korea": "NEWS INTELLIGENCE"}
PHASE_E_SOURCES = ("polymarket", "kalshi", "changelogs", "zdnet_kr")
NEWS_BLOCKS = ("# News Intelligence", "# Twitter/X Intelligence")

TWEET = re.compile(r"^- (?:@\S+ )?\[[^\]]*\] \([^)]*\) (?P<text>.+)$")
URL = re.compile(r"https?://\S+")


def nbytes(s: str) -> int:
    return len(s.encode("utf-8"))


def level(line: str) -> int:
    return len(line) - len(line.lstrip("#"))


def block(text: str, heading: str) -> list[str] | None:
    """First block whose heading line starts with `heading` ('# ' up to the next '# ' line,
    '## ' up to the next '# ' or '## ' line), heading included."""
    lines = text.split("\n")
    lvl = level(heading)
    for i, line in enumerate(lines):
        if line.startswith(heading):
            j = i + 1
            while j < len(lines) and not (lines[j].startswith("# ") or (lvl == 2 and lines[j].startswith("## "))):
                j += 1
            return lines[i:j]
    return None


class View:
    """What the shared block already carries. A line is in the view when its stripped text is a
    view line, when its tweet text is in the view (the view re-renders X lines as
    '- @who [Mon DD HH:MM] (…) text'), or when its last URL (> 24 characters) is in the view
    (the same item rendered or cut differently)."""

    def __init__(self, text: str):
        self.text = text
        self.lines = {l.strip() for l in text.split("\n")}
        self.urls = {u.rstrip(").,]") for u in URL.findall(text)}

    def has(self, line: str) -> bool:
        s = line.strip()
        if s in self.lines:
            return True
        m = TWEET.match(s)
        if m and m.group("text")[:120] in self.text:
            return True
        urls = [u.rstrip(").,]") for u in URL.findall(s)]
        return bool(urls) and len(urls[-1]) > 24 and urls[-1] in self.urls


def entry_lines(entry: str, raw: str, pkg: str) -> list[str] | None:
    if entry.startswith("news~"):
        rx = re.compile(entry[5:], re.I)
        return [line for h in NEWS_BLOCKS for line in block(raw, h) or []
                if line.strip().startswith("- ") and rx.search(line)]
    return block(raw, entry) or block(pkg, entry)


def extras_for(lines: list[str], view: View, added: set[str], budget: int) -> list[str]:
    """Body lines not in the view and not given out before (to this agent or an earlier one),
    with the headings above them; whole lines, in order, up to `budget` bytes. An indented
    continuation line (a snippet under an item) is kept only when its item line was added."""
    out, used, stack, item_added = [], 0, [], False  # stack: [level, line, emitted]
    for line in lines:
        s = line.strip()
        if not s or s == "---" or s.startswith("<!--"):
            continue
        if s.startswith("#"):
            lv = level(s)
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
        b = sum(nbytes(a) + 1 for a in add)
        if used + b > budget:
            break
        for h in heads:
            h[2] = True
        out.extend(add)
        added.add(s)
        used += b
        item_added = True
    return out


def lens_extras(raw: str, pkg: str, view_text: str, entries: list[str],
                taken: set[str] | None = None) -> tuple[str, list[dict]]:
    """The YOUR LENS DATA text (at most AGENT_CAP bytes) and the bytes each entry added. `taken`
    holds the stripped lines earlier agents were given; this agent's lines are added to it."""
    view, parts, report = View(view_text), [], []
    added = taken if taken is not None else set()
    for e in entries:
        lines = entry_lines(e, raw, pkg)
        if lines is None:
            report.append({"entry": e, "bytes": 0, "found": False})
            continue
        label = [f"## News and X lines matching /{e[5:]}/"] if e.startswith("news~") else []
        used = nbytes("\n\n".join(parts)) + (2 if parts else 0) + sum(nbytes(l) + 1 for l in label)
        got = extras_for(lines, view, added, min(ENTRY_CAP, AGENT_CAP - used))
        text = "\n".join(label + got) if got else ""
        report.append({"entry": e, "bytes": nbytes(text), "found": True})
        if got:
            parts.append(text)
    return "\n\n".join(parts), report


def inventory(raw: str, pkg: str, view_text: str) -> list[tuple[str, str, int, int]]:
    view, rows, seen = View(view_text), [], set()
    for src, text in (("raw", raw), ("package", pkg)):
        for line in text.split("\n"):
            if re.match(r"^#{1,2} \S", line) and not re.match(r"^#{1,2} (\d{4}-|Source|Collected|SECTION)", line):
                key = line.strip()
                if key in seen:
                    continue
                seen.add(key)
                body = [l for l in block(text, key) or [] if l.strip() and not l.strip().startswith("#")]
                out = [l for l in body if not view.has(l)]
                rows.append((src, key[:70], sum(nbytes(l) + 1 for l in body), sum(nbytes(l) + 1 for l in out)))
    return rows


def rebuild_view(run: Path) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        bap.build(run)


def wire_phase_e(run: Path, ds: Path) -> list[str]:
    files = {n: ds / n / "latest.md" for n in PHASE_E_SOURCES}
    files = {n: p.read_text(encoding="utf-8") for n, p in files.items() if p.exists()}
    raw = (run / "00_raw_data.md").read_text(encoding="utf-8")
    for n in PHASE_E_SOURCES:
        if n in files:
            raw = raw.rstrip("\n") + "\n\n---\n\n" + files[n].rstrip("\n") + "\n"
    (run / "00_raw_data.md").write_text(raw, encoding="utf-8")
    pkg = (run / "00_data_package.md").read_text(encoding="utf-8")

    def insert_before(text: str, marker: str, add: str) -> str:
        m = re.search(rf"(?m)^{re.escape(marker)}", text)
        return text[:m.start()] + add + "\n\n" + text[m.start():] if m else text + "\n\n" + add + "\n"

    if "zdnet_kr" in files:
        pkg = insert_before(pkg, "# SECTION 5:", files["zdnet_kr"].rstrip())
    if "changelogs" in files:
        pkg = insert_before(pkg, "# SECTION 7:", files["changelogs"].rstrip())
    pm = [files[n].rstrip() for n in ("polymarket", "kalshi") if n in files]
    if pm:
        pkg = pkg.rstrip("\n") + "\n\n---\n\n# SECTION 8: PREDICTION MARKETS\n\n" + "\n\n".join(pm) + "\n"
    (run / "00_data_package.md").write_text(pkg, encoding="utf-8")
    bap.CAPS.update(PHASE_E_CAPS)
    bap.FALLBACK_NAMES.update(PHASE_E_FALLBACK)
    rebuild_view(run)
    return sorted(files)


def measure(run: Path) -> dict:
    raw = (run / "00_raw_data.md").read_text(encoding="utf-8", errors="ignore")
    pkg = (run / "00_data_package.md").read_text(encoding="utf-8", errors="ignore")
    view = (run / "01_filtered.md").read_text(encoding="utf-8", errors="ignore")
    agents, taken = {}, set()
    for agent, entries in LENS_RAW.items():
        text, rep = lens_extras(raw, pkg, view, entries, taken)
        body = {l.strip() for l in text.split("\n") if l.strip() and not l.startswith("#")}
        agents[agent] = {"bytes": nbytes(text), "entries": rep, "text": text, "body": body}
    for a, v in agents.items():  # lines another agent was also given
        others = set().union(*(o["body"] for b, o in agents.items() if b != a))
        v["shared"] = sum(nbytes(l) + 1 for l in v["body"] & others)
    return {"view_bytes": nbytes(view), "agents": agents, "raw": raw, "pkg": pkg, "view": view}


def main(argv: list[str]) -> int:
    args = list(argv)

    def opt(name: str, takes_value: bool = False):
        if name not in args:
            return None
        i = args.index(name)
        val = args[i + 1] if takes_value else True
        del args[i:i + (2 if takes_value else 1)]
        return val

    rebuild, inv, as_json = opt("--rebuild-view"), opt("--inventory"), opt("--json")
    ds, dump = opt("--phase-e", True), opt("--dump", True)
    if len(args) != 1:
        print(__doc__)
        return 2
    src = Path(args[0])
    tmp = Path(tempfile.mkdtemp(prefix="lens-probe-"))
    try:
        for f in ("00_data_package.md", "00_raw_data.md", "00_scorecard.md", "01_filtered.md"):
            if (src / f).exists():
                shutil.copy(src / f, tmp / f)
        wired = wire_phase_e(tmp, Path(ds)) if ds else []
        if rebuild and not ds:
            rebuild_view(tmp)
        res = measure(tmp)
        if inv:
            for row in inventory(res["raw"], res["pkg"], res["view"]):
                print(f"{row[0]:7} {row[2]:7} {row[3]:7}  {row[1]}")
            return 0
        if dump:
            print(res["agents"][dump]["text"])
            return 0
        if as_json:
            print(json.dumps({"run": str(src), "phase_e": wired, "view_bytes": res["view_bytes"],
                              "agents": {a: {"bytes": v["bytes"], "shared": v["shared"], "entries": v["entries"]}
                                         for a, v in res["agents"].items()}}, ensure_ascii=False, indent=1))
            return 0
        print(f"{src}  view {res['view_bytes']} B  phase-e {','.join(wired) or 'no'}")
        ok = 0
        for a, v in res["agents"].items():
            ok += v["bytes"] >= WARN_BELOW
            per = ", ".join(f"{e['entry'][:24]}={e['bytes'] if e['found'] else '-'}" for e in v["entries"])
            print(f"  {a:17} {v['bytes']:5} (shared {v['shared']:4})  {per}")
        print(f"  agents >= {WARN_BELOW} B: {ok}/9")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
