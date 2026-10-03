#!/usr/bin/env python3
"""
RECON changelogs collector (Phase E, docs/v2/phase-e-collectors-spec.md §3.3).

Release notes for the AI tools the brief tracks. GitHub repos come from the REST releases API
(anonymous, 12 calls a day against a 60-per-hour limit): drafts and releases flagged
prerelease are dropped. Cursor, GitHub Changelog (Copilot) and the Google AI blog are RSS.
Sources: config/changelog_sources.json. Window: RECON_FRESH_HOURS (72).

Writes data-sources/changelogs/latest.md, status.json and seen.json.

    python3 scripts/collect_changelogs.py      exit 0 when ok, 1 otherwise
"""
from __future__ import annotations

import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collector_common as cc  # noqa: E402

NAME, LABEL = "changelogs", "Changelogs"
SECTIONS = ["CODING AGENTS & CLIS", "SDKS & PROTOCOLS", "MODEL SERVING", "VENDOR BLOGS"]
# Fallback only (GitHub repos), for a pre-release its project forgot to flag.
PRERELEASE = re.compile(
    r"(?i)(?:^|[\s._-])(alpha|beta|rc\d*|nightly|preview|canary|dev|pre)(?:[\s._\d-]|$)|\d+\.\d+\.\d+-\d+$")
BOILER = re.compile(r"(?i)^(what'?s changed|full changelog|new contributors|contributors|changelog|"
                    r"release notes|assets|highlights?)\b")
MADE_FIRST = re.compile(r"(?i)made (their|his|her) first contribution")


def is_prerelease_name(*names: str) -> bool:
    return any(PRERELEASE.search(n or "") for n in names)


def md_notes(text: str, limit: int = 280) -> str:
    """First three bullets (or sentences) of a markdown or HTML release body, one line."""
    if "<" in (text or "") and re.search(r"</?(p|li|ul|div|h\d|br)\b", text or "", re.I):
        text = cc.strip_html(text)
    bullets, prose = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("```") or line.startswith("<!--"):
            continue
        line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)                  # images
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)               # links -> text
        line = re.sub(r"\s+(by @[\w-]+ )?in (https://github\.com/\S+|#\d+)\s*$", "", line)
        line = re.sub(r"https?://\S+", "", line)
        line = line.replace("**", "").replace("__", "").replace("`", "")
        line = line.strip()
        is_bullet = bool(re.match(r"^([-*+]|\d+\.)\s+", line))
        line = re.sub(r"^([-*+]|\d+\.)\s+", "", line).strip(" :")
        if not line or BOILER.match(line) or MADE_FIRST.search(line) or line.startswith("@"):
            continue
        if len(line) < 4:
            continue
        (bullets if is_bullet else prose).append(line)
    parts = bullets[:3]
    prose = [re.sub(r"(?i)\s*The post .* appeared first on .*$", "", x) for x in prose]
    if not parts:
        sentences = re.split(r"(?<=[.!?])\s+", " ".join(prose))
        parts = [s for s in sentences if s][:3]
    return cc.one_line("; ".join(parts), limit)


def fetch_github(src: dict, budget: cc.Budget) -> list[dict]:
    url = cc.build_url(f"https://api.github.com/repos/{src['repo']}/releases", {"per_page": 30})
    body, _ = cc.http_get(url, budget, accept="application/vnd.github+json")
    import json
    data = json.loads(body.decode("utf-8"))
    out = []
    for r in data if isinstance(data, list) else []:
        if r.get("draft") or r.get("prerelease"):
            continue
        tag, name = r.get("tag_name") or "", r.get("name") or ""
        if is_prerelease_name(tag, name):
            continue
        out.append({"id": f"gh:{src['repo']}:{r.get('id')}", "version": tag or name, "title": name or tag,
                    "when": cc.to_utc(r.get("published_at") or r.get("created_at")),
                    "notes": md_notes(r.get("body") or ""), "url": r.get("html_url") or ""})
    return out


def fetch_rss(src: dict, budget: cc.Budget) -> list[dict]:
    items = cc.parse_rss(cc.http_text(src["url"], budget))
    out = []
    for it in items:
        title = cc.one_line(cc.strip_html(it.get("title", "")), 140)
        out.append({"id": f"rss:{it.get('id') or it.get('link')}", "version": title, "title": title,
                    "categories": it.get("categories") or [],
                    "when": cc.to_utc(it.get("published")), "notes": md_notes(it.get("summary") or ""),
                    "url": it.get("link") or ""})
    return out


def apply_filters(src: dict, entries: list[dict]) -> list[dict]:
    inc = re.compile(src["include"]) if src.get("include") else None
    exc = re.compile(src["exclude"]) if src.get("exclude") else None
    out = []
    for e in entries:
        hay = [e.get("version", ""), e.get("title", "")]
        if exc and any(exc.search(h) for h in hay):
            continue
        if inc and not any(inc.search(h) for h in hay + list(e.get("categories") or [])):
            continue
        out.append(e)
    return out


def collect(res: cc.SourceResult, stamp: datetime) -> None:
    cfg = cc.load_json(cc.CONFIG_DIR / "changelog_sources.json", {})
    sources = cfg.get("sources") or []
    if not sources:
        res.error = "config/changelog_sources.json missing or empty"
        return
    budget = cc.Budget(seconds=40, requests=20, mbytes=30)
    cutoff = stamp - timedelta(hours=cc.FRESH_HOURS)
    seen_path = cc.source_dir(NAME) / "seen.json"
    seen: dict = cc.load_json(seen_path, {})

    def run(src: dict):
        try:
            entries = fetch_github(src, budget) if src["type"] == "github" else fetch_rss(src, budget)
            return src, apply_filters(src, entries), None
        except cc.HTTPFailure as e:
            msg = e.msg
            if e.status in (403, 429) and e.headers.get("x-ratelimit-remaining") == "0":
                msg = "rate-limited"
            return src, [], msg
        except cc.BudgetExceeded as e:
            return src, [], str(e)
        except Exception as e:  # a malformed feed must not stop the others
            return src, [], f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(run, sources))

    by_section: dict[str, list[str]] = {s: [] for s in SECTIONS}
    quiet, failed = [], []
    for src, entries, err in results:
        if err:
            failed.append(f"{src['short']} ({err})")
            res.notes.append(f"{src['short']}: {err}")
            continue
        fresh = [e for e in entries if e["when"] and e["when"] >= cutoff]
        res.stale_items_dropped += sum(1 for e in entries if not e["when"] or e["when"] < cutoff)
        if not fresh:
            quiet.append(src["short"])
            continue
        fresh.sort(key=lambda e: e["when"], reverse=True)
        e = fresh[0]
        is_new = e["id"] not in seen
        for x in fresh:
            seen.setdefault(x["id"], cc.iso_z(stamp))
        version = e["version"]
        if src["type"] == "rss":
            label = f"{src['product']}: {version}"
        else:
            label = f"{src['product']} {version}"
        more = f" (+{len(fresh) - 1} earlier release{'s' if len(fresh) > 2 else ''} in 72 h)" if len(fresh) > 1 else ""
        notes = e["notes"] or "no release notes"
        line = (f"- [{cc.fmt_utc(e['when'])}] {cc.one_line(label, 160)}{' (new)' if is_new else ''}{more}: "
                f"{notes} | {e['url']}")
        by_section.setdefault(src.get("section", SECTIONS[0]), []).append(line)

    # Keep seen.json from growing forever: drop ids first seen more than 30 days ago.
    keep_after = stamp - timedelta(days=30)
    seen = {k: v for k, v in seen.items() if (cc.to_utc(v) or stamp) >= keep_after}
    cc.save_json(seen_path, seen)

    lines = cc.header(LABEL, stamp, ["Source: GitHub releases API (stable only), Cursor, GitHub Changelog, Google AI RSS; last 72 h"])
    for sec in SECTIONS + [s for s in by_section if s not in SECTIONS]:
        if by_section.get(sec):
            lines += ["", f"## {sec}", ""] + by_section[sec]
    tail = []
    if quiet:
        tail.append(f"- No release in the last 72 h: {', '.join(quiet)}.")
    if failed:
        tail.append(f"- Not reachable this run: {', '.join(failed)}.")
    if tail:
        lines += ["", "## QUIET OR UNREACHABLE", ""] + tail
    lines.append("")
    res.text = "\n".join(lines)
    res.requests, res.bytes_in = budget.requests, budget.bytes_in
    ok_sources = len(sources) - len(failed)
    res.ok = ok_sources > 0
    if not res.ok:
        res.error = "all sources failed"
    res.notes.append(f"sources ok {ok_sources}/{len(sources)}, with a release {ok_sources - len(quiet)}, quiet {len(quiet)}")


if __name__ == "__main__":
    sys.exit(cc.run_main(NAME, LABEL, collect))
