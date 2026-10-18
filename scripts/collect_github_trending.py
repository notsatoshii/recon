#!/usr/bin/env python3
"""
RECON GitHub trending AI repos, for the AI NEWSLETTER's "Trending repos" bullets.

GitHub has no trending API, so this reads the public github.com/trending pages (daily overall, daily
Python, daily TypeScript, weekly overall; 4 requests, keyless) and keeps the AI repos: a word-boundary
keyword match on the repo name and description, minus a blocklist (NSFW, game cheats, keygens).
A small history (config/github_trending_history.json, 30 days) adds how many days running a repo has
been on the daily list.

The block is written at the top of data-sources/ai_tools/latest.md by the AI/TOOLS step of
scripts/collect_data.sh (block()), so the synthesizer's AI_RAW (first 9 KB of that file) always holds it.
The old search-API blocks sorted by all-time stars (the same 250k-star repos every day) and let any
repo whose name held "ai" or "model" through (a Diablo II port, an NSFW image skill); is_ai() and
blocked() are used for the remaining "new this week" search too.

    python3 scripts/collect_github_trending.py      prints the block (no files written but the history)
"""
from __future__ import annotations

import html
import re
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collector_common as cc  # noqa: E402

PAGES = [  # (label, url, period)
    ("all", "https://github.com/trending?since=daily", "today"),
    ("python", "https://github.com/trending/python?since=daily", "today"),
    ("typescript", "https://github.com/trending/typescript?since=daily", "today"),
    ("all", "https://github.com/trending?since=weekly", "this week"),
]
BUDGET = {"seconds": 60, "requests": 8, "mbytes": 8}  # 4 pages of ~0.6 MB, one retry each
MAX_TODAY, MAX_WEEK, DESC_CHARS = 10, 6, 110
HISTORY = cc.CONFIG_DIR / "github_trending_history.json"
HISTORY_DAYS = 30

AI_WORDS = [
    "ai", "llm", "llms", "agent", "agents", "agentic", "mcp", "rag", "gpt", "chatgpt", "claude", "codex",
    "openai", "anthropic", "gemini", "deepseek", "qwen", "llama", "mistral", "ollama", "vllm", "sglang",
    "gguf", "mlx", "lora", "embedding", "embeddings", "inference", "diffusion", "transformer",
    "transformers", "prompt", "prompts", "copilot", "chatbot", "multimodal", "vlm", "tts", "asr", "stt",
    "whisper", "ocr", "speech", "elevenlabs", "pytorch", "huggingface", "neural", "genai", "aigc", "autogpt", "langchain",
    "langgraph", "crewai", "cursor", "windsurf", "cline", "aider", "opencode",
]
AI_PHRASES = ["language model", "machine learning", "deep learning", "fine-tun", "vibe cod", "coding assistant",
              "hugging face", "vector database", "speech recognition", "text-to-speech", "computer use",
              "browser agent", "claude code", "voice clon", "人工智能", "大模型", "智能体", "大语言模型"]
BLOCK_WORDS = ["nsfw", "porn", "porno", "hentai", "nude", "nudes", "nudify", "undress", "erotic", "sex",
               "sexual", "onlyfans", "18+", "r18", "cheat", "cheats", "aimbot", "keygen", "crack", "cracked"]

_AI_RX = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(w) for w in AI_WORDS) + r")(?![a-z0-9])", re.I)
_BLOCK_RX = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(w) for w in BLOCK_WORDS) + r")(?![a-z0-9])", re.I)


def _text(repo: dict) -> str:
    # repo names split on - _ . / so "claude-plugins" and "open_claude_ui" match on word boundaries
    return re.sub(r"[-_./]", " ", repo.get("repo", "")) + " " + (repo.get("desc") or "") + " " + " ".join(repo.get("topics") or [])


def is_ai(repo: dict) -> bool:
    t = _text(repo)
    return bool(_AI_RX.search(t)) or any(p in t.lower() for p in AI_PHRASES)


def blocked(repo: dict) -> bool:
    return bool(_BLOCK_RX.search(_text(repo)))


def _num(s: str | None) -> int | None:
    s = (s or "").replace(",", "").strip()
    return int(s) if s.isdigit() else None


def parse_trending(page: str) -> list[dict]:
    """Repos on a github.com/trending page, in page order: repo, url, desc, lang, stars, period_stars."""
    out = []
    for chunk in page.split('<article class="Box-row')[1:]:
        chunk = chunk.split("</article>")[0]
        m = re.search(r'<h2[^>]*>.*?href="/([^"/]+/[^"/]+)"', chunk, re.S)
        if not m:
            continue
        repo = html.unescape(m.group(1))
        d = re.search(r'<p class="[^"]*color-fg-muted[^"]*"[^>]*>(.*?)</p>', chunk, re.S)
        lang = re.search(r'itemprop="programmingLanguage">([^<]+)<', chunk)
        stars = re.search(r'href="/' + re.escape(m.group(1)) + r'/stargazers"[^>]*>.*?</svg>\s*([\d,]+)', chunk, re.S)
        period = re.search(r'([\d,]+)\s+stars\s+(?:today|this week|this month)', chunk)
        out.append({
            "repo": repo, "url": "https://github.com/" + repo,
            "desc": cc.one_line(cc.strip_html(d.group(1))) if d else "",
            "lang": html.unescape(lang.group(1)).strip() if lang else "",
            "stars": _num(stars.group(1)) if stars else None,
            "period_stars": _num(period.group(1)) if period else None,
        })
    return out


def _streak(hist: dict, repo: str, today) -> int:
    """Days running (today included) the repo was on a daily list, from the history."""
    days, n = (hist.get(repo) or {}), 0
    d = today
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n


def _line(r: dict, period: str, streak: int) -> list[str]:
    bits = []
    if r.get("period_stars") is not None:
        bits.append(f"+{r['period_stars']:,} stars {period}")
    if r.get("stars") is not None:
        bits.append(f"{r['stars']:,} total")
    tail = f" [{r['lang']}]" if r.get("lang") else ""
    if streak >= 2:
        tail += f" (on the daily list {streak} days running)"
    lines = [f"- [{r['repo']}]({r['url']}) -- {', '.join(bits)}{tail}"]
    if r.get("desc"):
        lines.append("  " + cc.one_line(r["desc"], DESC_CHARS))
    return lines


def collect(now=None) -> tuple[list[str], list[str]]:
    """(markdown lines, notes). Never raises: a failed page is a note; no page at all is a stub line."""
    now = now or cc.now_utc()
    budget = cc.Budget(**BUDGET)
    notes, today, week, seen = [], [], [], set()
    pages_ok = 0
    for label, url, period in PAGES:
        try:
            repos = parse_trending(cc.http_text(url, budget, accept="text/html"))
            pages_ok += 1
        except (cc.HTTPFailure, cc.BudgetExceeded) as e:
            notes.append(f"{label} {period}: {e}")
            continue
        if not repos:
            notes.append(f"{label} {period}: page parsed to 0 repos (layout change?)")
        for r in repos:
            if r["repo"] in seen and period == "today":
                continue
            if not is_ai(r) or blocked(r):
                continue
            (today if period == "today" else week).append(r)
            if period == "today":
                seen.add(r["repo"])
    if not pages_ok:
        return ["## GITHUB TRENDING — AI REPOS", "SOURCE UNAVAILABLE: github.com/trending did not answer (" +
                "; ".join(notes)[:200] + ")", ""], notes

    day = now.astimezone(cc.KST).date()
    hist = cc.load_json(HISTORY, {})
    for r in today:
        hist.setdefault(r["repo"], {})[day.isoformat()] = r.get("stars")
    cutoff = (day - timedelta(days=HISTORY_DAYS)).isoformat()
    hist = {k: {d: s for d, s in v.items() if d >= cutoff} for k, v in hist.items()}
    hist = {k: v for k, v in hist.items() if v}
    try:
        cc.save_json(HISTORY, hist)
    except OSError as e:
        notes.append(f"history not saved: {e}")

    today.sort(key=lambda r: -(r.get("period_stars") or 0))
    week = [r for r in week if r["repo"] not in seen]
    week.sort(key=lambda r: -(r.get("period_stars") or 0))
    out = ["## GITHUB TRENDING — AI REPOS TODAY (github.com/trending, daily; stars gained today)", ""]
    for r in today[:MAX_TODAY]:
        out += _line(r, "today", _streak(hist, r["repo"], day))
    if not today:
        out.append("No AI repos on today's trending lists.")
    out += ["", "## GITHUB TRENDING — AI REPOS THIS WEEK (github.com/trending, weekly; not in today's list)", ""]
    for r in week[:MAX_WEEK]:
        out += _line(r, "this week", 0)
    if not week:
        out.append("No other AI repos on this week's trending list.")
    out.append("")
    return out, notes


def block(now=None) -> str:
    lines, _ = collect(now)
    return "\n".join(lines)


if __name__ == "__main__":
    lines, notes = collect()
    print("\n".join(lines))
    for n in notes:
        print("note:", n, file=sys.stderr)
