#!/usr/bin/env python3
"""RECON LLM client -- the single entry point for every model call.

Providers
    codex    (default) OpenAI Codex CLI, `codex exec`, ChatGPT-subscription auth
    claude   Anthropic Claude Code CLI, `claude -p`
    dry-run  No network. Returns canned text shaped so the pipeline's parsers pass.

Tiers map a role to a model + reasoning effort so call sites never name models:
    fast     votes, classification, memory/state updates, mechanical picks
    analyst  takes, challenges, responses, deep dives
    synth    environment classification, brief draft, hallucination pass
    lens     per-lens override for takes (recon/debate.py LENS_TIER): the SYNTH model at medium effort,
             the setting the Phase C spread probe measured (p3); RECON_MODEL_LENS / RECON_EFFORT_LENS

Environment (all optional)
    RECON_HOME                 repo root (default: parent of this file's directory)
    RECON_LLM_PROVIDER         codex | claude | dry-run
    RECON_MODEL_FAST / _ANALYST / _SYNTH        Codex model ids
    RECON_EFFORT_FAST / _ANALYST / _SYNTH       minimal|low|medium|high|xhigh
    RECON_CLAUDE_MODEL_FAST / _ANALYST / _SYNTH Claude model ids (claude provider only)
    RECON_LLM_TIMEOUT          seconds per call (default 420)
    RECON_LLM_RETRIES          attempts on empty output / rate limit (default 3)
    RECON_CODEX_SLIM           1 = replace Codex's built-in agent instructions and switch its tools off
                               (measured 2026-10-04: a one-line call drops from 11.6 K to 2.2 K input
                               tokens). The orchestrator sets it; the bash pipeline leaves it off.

CLI (used by the bash pipeline)
    printf '%s' "$prompt" | python3 recon/llm.py --tier analyst --persona personas/trader.md
Python
    from recon.llm import ask
    text = ask(prompt, tier="fast", persona_path="personas/synthesizer.md", schema_path=None)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

RECON_HOME = Path(os.environ.get("RECON_HOME") or Path(__file__).resolve().parent.parent)
LOG_FILE = RECON_HOME / "logs" / "llm_calls.log"
SLIM_INSTRUCTIONS = Path(__file__).resolve().parent / "codex_instructions.md"
_LOG_LOCK = threading.Lock()

# Codex features that add tools or instructions a single-shot text call never uses.
SLIM_DISABLE = ("apps", "browser_use", "computer_use", "image_generation", "multi_agent", "plugins",
                "goals", "shell_tool", "unified_exec", "view_image", "skill_search", "tool_suggest",
                "sleep_tool", "personality", "code_mode_host")

TIERS = ("fast", "analyst", "synth", "lens")

DEFAULT_CODEX = {
    "fast": ("gpt-5.6-luna", "low"),
    "analyst": ("gpt-5.6-terra", "medium"),
    "synth": ("gpt-5.6-sol", "high"),
}
DEFAULT_CLAUDE = {
    "fast": "claude-haiku-4-5-20251001",
    "analyst": "claude-sonnet-5",
    "synth": "claude-opus-5",
}

# Legacy v1 call sites passed Claude model ids as the third argument.
LEGACY_MODEL_TO_TIER = (
    (re.compile(r"opus", re.I), "synth"),
    (re.compile(r"haiku", re.I), "fast"),
    (re.compile(r"sonnet", re.I), "analyst"),
)

RATE_LIMIT_MARKERS = (
    "usage limit",
    "rate limit",
    "rate_limit",
    "too many requests",
    "429",
    "quota",
    "try again later",
    "reached your",
)

PERSONA_WRAPPER = """You are playing a specific role. Stay in character completely.
Answer from the material in this prompt only. Do not run commands, read files, browse, or
explore the working directory; there is nothing there. Reply with your analysis directly.

--- YOUR PERSONA ---
{persona}
--- END PERSONA ---

--- YOUR TASK ---
{task}
--- END TASK ---"""


class LLMError(RuntimeError):
    pass


# ── configuration ──────────────────────────────────────────────

def resolve_tier(name: str | None) -> str:
    if not name:
        return "analyst"
    n = name.strip().lower()
    if n in TIERS:
        return n
    for pattern, tier in LEGACY_MODEL_TO_TIER:
        if pattern.search(n):
            return tier
    raise LLMError(f"unknown tier or model '{name}'; expected one of {TIERS}")


def provider_name() -> str:
    p = os.environ.get("RECON_LLM_PROVIDER", "codex").strip().lower()
    if p not in ("codex", "claude", "dry-run"):
        raise LLMError(f"unknown RECON_LLM_PROVIDER '{p}'")
    return p


def codex_model(tier: str) -> tuple[str, str]:
    if tier == "lens":
        # The SYNTH model (whatever RECON_MODEL_SYNTH says) at medium effort: what the spread probe measured
        # as p3. Not sol at high effort, which no probe measured (phase-c-spec §15.0, §16).
        return (os.environ.get("RECON_MODEL_LENS") or codex_model("synth")[0],
                os.environ.get("RECON_EFFORT_LENS") or "medium")
    model, effort = DEFAULT_CODEX[tier]
    model = os.environ.get(f"RECON_MODEL_{tier.upper()}", model)
    effort = os.environ.get(f"RECON_EFFORT_{tier.upper()}", effort)
    return model, effort


def claude_model(tier: str) -> str:
    if tier == "lens":
        return os.environ.get("RECON_CLAUDE_MODEL_LENS") or claude_model("synth")
    return os.environ.get(f"RECON_CLAUDE_MODEL_{tier.upper()}", DEFAULT_CLAUDE[tier])


def timeout_seconds() -> int:
    return int(os.environ.get("RECON_LLM_TIMEOUT", "420"))


def retries() -> int:
    return max(1, int(os.environ.get("RECON_LLM_RETRIES", "3")))


def slim() -> bool:
    return os.environ.get("RECON_CODEX_SLIM", "0").strip().lower() in ("1", "true", "yes")


def slim_args() -> list[str]:
    args = ["-c", f'model_instructions_file="{SLIM_INSTRUCTIONS}"',
            "-c", "include_permissions_instructions=false", "-c", "include_apps_instructions=false",
            "-c", "include_environment_context=false", "-c", "include_collaboration_mode_instructions=false",
            "-c", 'web_search="disabled"']
    for f in SLIM_DISABLE:
        args += ["--disable", f]
    return args


# ── providers ──────────────────────────────────────────────────

def _looks_rate_limited(text: str) -> bool:
    t = text.lower()
    return any(m in t for m in RATE_LIMIT_MARKERS)


def _extract_usage(ndjson: str) -> dict:
    """Sum token usage from `codex exec --json` events. Tolerant of shape changes."""
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}
    for line in ndjson.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        u = ev.get("usage") if isinstance(ev, dict) else None
        if not isinstance(u, dict):
            continue
        for k in usage:
            v = u.get(k)
            if isinstance(v, (int, float)):
                usage[k] += int(v)
    return usage


def _call_codex(prompt: str, tier: str, schema_path: str | None) -> tuple[str, dict, str]:
    model, effort = codex_model(tier)
    with tempfile.TemporaryDirectory(prefix="recon-codex-") as tmp:
        out_file = Path(tmp) / "last_message.txt"
        cmd = [
            "codex", "exec", "-",
            "--model", model,
            "-c", f'model_reasoning_effort="{effort}"',
            "-c", "hide_agent_reasoning=true",
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--color", "never",
            "--cd", tmp,  # empty dir: nothing for the agent to read or run against
            "--json",
            "-o", str(out_file),
        ]
        if schema_path:
            cmd += ["--output-schema", schema_path]
        if slim() and SLIM_INSTRUCTIONS.exists():
            cmd += slim_args()
        try:
            proc = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True,
                timeout=timeout_seconds(),
            )
        except FileNotFoundError:
            raise LLMError("codex CLI not found on PATH (npm i -g @openai/codex)")
        except subprocess.TimeoutExpired:
            raise LLMError(f"codex call exceeded {timeout_seconds()}s")
        text = out_file.read_text(encoding="utf-8").strip() if out_file.exists() else ""
        usage = _extract_usage(proc.stdout)
        diag = (proc.stderr or "")[-2000:]
        if proc.returncode != 0 and not text:
            raise LLMError(f"codex exit {proc.returncode}: {diag.strip()[:500]}")
        return text, usage, diag


def _call_claude(prompt: str, tier: str) -> tuple[str, dict, str]:
    model = claude_model(tier)
    try:
        proc = subprocess.run(
            ["claude", "-p", "-", "--model", model],
            input=prompt, capture_output=True, text=True, timeout=timeout_seconds(),
        )
    except FileNotFoundError:
        raise LLMError("claude CLI not found on PATH")
    except subprocess.TimeoutExpired:
        raise LLMError(f"claude call exceeded {timeout_seconds()}s")
    text = (proc.stdout or "").strip()
    diag = (proc.stderr or "")[-2000:]
    if proc.returncode != 0 and not text:
        raise LLMError(f"claude exit {proc.returncode}: {diag.strip()[:500]}")
    return text, {}, diag


_DRY_FILLER = (
    "This is dry-run output. No model was called. The pipeline is being exercised end to end "
    "with placeholder analysis so that phase ordering, file handling, parsing, and delivery can "
    "be verified without spending subscription usage. "
)


def _dry_quotes(prompt: str) -> list[str]:
    """Verbatim lines from the prompt's package block, so dry-run evidence verifies."""
    lines = [l.strip() for l in prompt.splitlines()]
    out = [l[:140] for l in lines if 50 <= len(l) <= 400 and l.startswith("- ") and any(c.isdigit() for c in l)
           and not l.startswith("- SECTION ")]
    return list(dict.fromkeys(out)) or ["dry-run quote that is not in the package"]


_DRY_DOMAIN_LENSES = {"markets_crypto": ["trader", "analyst"], "macro_policy": ["macro_strategist", "policy_analyst"],
                      "ai_product": ["ai_engineer", "builder"], "korea": ["policy_analyst", "user_agent"],
                      "prediction_markets": ["trader", "skeptic"]}


def _dry_day(prompt: str) -> str:
    """The run day: from the triage task line, else the package header of the shared block."""
    for rx in (r"TRIAGE AND QUESTIONS OF THE DAY \((\d{4}-\d\d-\d\d)\)", r"INTELLIGENCE PACKAGE \((\d{4}-\d\d-\d\d)\)",
               r"Resolves: (\d{4}-\d\d-\d\d)"):
        m = re.search(rx, prompt)
        if m:
            return m.group(1)
    return ""


def _plus_days(day: str, n: int) -> str:
    from datetime import date, timedelta
    try:
        return (date.fromisoformat(day) + timedelta(days=n)).isoformat()
    except ValueError:
        return "2026-10-31"


def _dry_questions(prompt: str, item_schema: dict, gen) -> list[dict]:
    """Five QUESTION objects that pass the §2.4 gate: distinct texts ending with '?', baselines copied from
    package lines, resolves_on = run day + 7 (empty for judgment), weights 1-3, nothing carried."""
    quotes = [q for q in _dry_quotes(prompt) if not q.startswith("dry-run")]
    day = _dry_day(prompt)
    kinds = ["event", "threshold", "direction", "event", "judgment"]
    domains = ["markets_crypto", "macro_policy", "ai_product", "korea", "prediction_markets"]
    out = []
    for k in range(5):
        o = gen(item_schema, "question")
        bq = quotes[(k * 7) % len(quotes)] if quotes else ""
        kind = kinds[k]
        o.update({"id": f"q{k + 1}", "kind": kind, "domain": domains[k],
                  "text": f"Will {(bq or 'the package figure')[2:62].strip()} hold through day+7 (item {k + 1})?",
                  "baseline_quote": bq if kind in ("threshold", "direction") else "", "settled_quote": "",
                  "resolves_on": "" if kind == "judgment" else _plus_days(day, 7),
                  "settles_with": "the same package line on the resolution day",
                  "metric": "the figure in the baseline line" if kind in ("threshold", "direction") else "",
                  "comparator": ">=" if kind == "threshold" else "",
                  "threshold": (re.findall(r"[\d.,]+[%KMBkmb]?", bq) or [""])[0] if kind == "threshold" else "",
                  "lenses": list(_DRY_DOMAIN_LENSES[domains[k]]), "weight": 1 + k % 3, "carried_from": ""})
        out.append(o)
    return out


def _dry_json(schema: dict, prompt: str, agent: str) -> str:
    """Fill a JSON schema with plausible values: question ids from the prompt, probabilities spread by
    agent (RECON_DRY_SPREAD=wide: 5-95; narrow: 55-65), quotes copied from the prompt (one in five
    invented, to exercise the unverified path), integers for integer fields, and gate-passing questions
    for the triage call (phase-c-spec §12.2)."""
    import hashlib
    qids = list(dict.fromkeys(re.findall(r"\[(q\d)\]", prompt))) or ["q1", "q2", "q3"]
    quotes = _dry_quotes(prompt)
    spread = os.environ.get("RECON_DRY_SPREAD", "wide").strip().lower()
    day = _dry_day(prompt)
    seed = int(hashlib.sha1((agent or "-").encode()).hexdigest()[:12], 16)
    counter = [0]

    def nxt() -> int:
        counter[0] += 1
        return (seed // (counter[0] * 7 + 1)) + counter[0] * 37

    def prob() -> int:
        if spread == "narrow":
            return 55 + nxt() % 11
        return 5 + nxt() % 91

    def gen(sch: dict, key: str = ""):
        t = sch.get("type")
        if isinstance(t, list):
            t = next((x for x in t if x != "null"), "string")
        if key == "question_id":          # a question id present in the prompt wins over the enum
            return qids[nxt() % len(qids)]
        if "enum" in sch:
            return sch["enum"][nxt() % len(sch["enum"])]
        if t == "object":
            return {k: gen(v, k) for k, v in sch.get("properties", {}).items()}
        if t == "array":
            items = sch.get("items", {})
            props = items.get("properties", {}) if items.get("type") == "object" else {}
            if "kind" in props and "baseline_quote" in props:
                return _dry_questions(prompt, items, gen)
            if "question_id" in props:
                out = []
                for q in qids:
                    o = gen(items, key)
                    o["question_id"] = q
                    out.append(o)
                return out
            return [gen(items, key) for _ in range(2)]
        if t == "integer":
            if "probab" in key:
                return prob()
            return 1 + nxt() % 3 if key == "weight" else nxt() % 10
        if t == "number":
            return prob() if "probab" in key else nxt() % 10
        if t == "boolean":
            return bool(nxt() % 2)
        if key == "quote":
            n = nxt()
            return "dry-run invented figure 123.4%" if n % 5 == 0 else quotes[n % len(quotes)]
        if key in ("resolves_on", "by_date"):
            return _plus_days(day, 7) if day else "2026-10-31"
        if key == "section":
            return "ON-CHAIN & MARKET DATA"
        if key in ("take", "text", "rebuttal", "case", "reason"):
            return f"[dry-run] {agent or '-'} {key}. " + _DRY_FILLER * (2 if key in ("take", "text") else 1)
        return f"[dry-run] {key or 'value'}"

    return json.dumps(gen(schema), ensure_ascii=False)


def _dry_brief(prompt: str) -> str:
    """The canned brief: the sections of schemas.BRIEF_SECTIONS; WHERE THE VIEWS SPLIT copies every
    count phrase on the split sheet and, on a consensus sheet, the words 'case against' (§11.5 checks pass)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from recon.schemas import BRIEF_SECTIONS
    phrases = re.findall(r"Count phrase \(copy exactly\): (.+)", prompt)
    split = "\n".join(f"- {p.strip()}." for p in phrases) if phrases else "- The lenses broadly agree today."
    if "[consensus" in prompt:
        split = "- No real split today. The strongest case against the consensus: dry-run red-team case.\n" + split
    parts = []
    for sec in BRIEF_SECTIONS:
        body = split if sec == "WHERE THE VIEWS SPLIT" else f"- {_DRY_FILLER}"
        parts.append(f"### {sec}\n{body}\n")
    return "# RECON DAILY BRIEF\n\n" + "\n".join(parts)


def _call_dry_run(prompt: str, tier: str, agent: str, schema_path: str | None = None) -> tuple[str, dict, str]:
    p = prompt
    if schema_path:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        return _dry_json(schema, p, agent), {}, ""
    if "Reply EXACTLY: CHALLENGER" in p:
        return "CHALLENGER: trader TARGET: ai_engineer", {}, ""
    if "DEEP_DIVE:" in p and "NO_DEEP_DIVE" in p:
        # exercises the point parser: the point itself contains " on "
        return ("DEEP_DIVE: analyst vs skeptic on whether stablecoin depth on Base is a choke point\n"
                "Both cite the same liquidity figures but read them differently."), {}, ""
    if "Reply EXACTLY: ENVIRONMENT" in p:
        return "ENVIRONMENT: QUIET WEIGHT: skeptic, analyst", {}, ""
    if "VOTE. This format replaces" in p:
        return ("1. ACT ON: dry-run action.\n2. MARKET IS WRONG ABOUT: dry-run mispricing.\n"
                "3. UNDISCUSSED RISK: dry-run risk."), {}, ""
    if "Update your running memory file" in p:
        return ("### Active Tracking\n- [dry-run] item\n\n### Predictions\n- [dry-run] prediction [status: pending]\n\n"
                "### Recurring Themes\n- dry-run theme — seen 1x\n\n### Lessons Learned\n- none\n\n### Archived\n- none"), {}, ""
    body = f"[dry-run] agent={agent or '-'} tier={tier} prompt_bytes={len(p)}\n\n" + _DRY_FILLER * 3
    if "RECON DAILY BRIEF" in p:
        return _dry_brief(p), {}, ""
    return body, {}, ""


# ── public API ─────────────────────────────────────────────────

def _log(agent: str, tier: str, model: str, provider: str, in_bytes: int, out_bytes: int,
         seconds: int, usage: dict, note: str = "") -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tok = ""
        if usage.get("input_tokens") or usage.get("output_tokens"):
            tok = (f" in_tok={usage.get('input_tokens', 0)} cached_tok={usage.get('cached_input_tokens', 0)}"
                   f" out_tok={usage.get('output_tokens', 0)} reason_tok={usage.get('reasoning_output_tokens', 0)}")
        with _LOG_LOCK, LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(
                f"[{time.strftime('%H:%M:%S')}] agent={agent or '-'} tier={tier} model={model} "
                f"provider={provider} input={in_bytes}b output={out_bytes}b duration={seconds}s{tok}"
                f"{' ' + note if note else ''}\n"
            )
    except OSError:
        pass


def ask_ex(prompt: str, tier: str = "analyst", persona_path: str | None = None,
           schema_path: str | None = None, agent: str | None = None, note: str = "",
           on_failed_attempt=None, before_retry=None) -> dict:
    """Like ask(), but returns {text, usage, seconds, attempts, model, provider, tier, prompt_bytes}.

    Retries: a rate-limit or usage-window error waits 180 s x attempt; anything else retries after
    2 s (no fixed pauses between calls). Raises LLMError after all retries fail.

    Every provider attempt is a call against the budget (phase-c-spec §1.1): `on_failed_attempt(meta)`
    is called after each failed attempt ({attempt, error, seconds, usage, model, tier}), and
    `before_retry(attempt)` before the next one starts (before any wait); it may raise to stop.
    """
    tier = resolve_tier(tier)
    provider = provider_name()
    if persona_path:
        persona = Path(persona_path).read_text(encoding="utf-8")
        agent = agent or Path(persona_path).stem
        full_prompt = PERSONA_WRAPPER.format(persona=persona, task=prompt)
    else:
        full_prompt = prompt

    if provider == "codex":
        model = codex_model(tier)[0]
    elif provider == "claude":
        model = claude_model(tier)
    else:
        model = "dry-run"

    last_err = "no attempts"
    started = time.time()
    for attempt in range(1, retries() + 1):
        start = time.time()
        try:
            if provider == "codex":
                text, usage, diag = _call_codex(full_prompt, tier, schema_path)
            elif provider == "claude":
                text, usage, diag = _call_claude(full_prompt, tier)
            else:
                text, usage, diag = _call_dry_run(full_prompt, tier, agent or "", schema_path)
        except LLMError as e:
            text, usage, diag = "", {}, str(e)

        elapsed = int(time.time() - start)
        if text:
            _log(agent or "", tier, model, provider, len(full_prompt), len(text), elapsed, usage, note=note)
            return {"text": text, "usage": usage, "seconds": round(time.time() - started, 1), "attempts": attempt,
                    "model": model, "provider": provider, "tier": tier,
                    "prompt_bytes": len(full_prompt.encode("utf-8"))}

        last_err = diag.strip()[-300:] or "empty output"
        _log(agent or "", tier, model, provider, len(full_prompt), 0, elapsed, usage,
             note=(note + " " if note else "") + f"attempt={attempt} FAILED: {last_err[:120]!r}")
        if on_failed_attempt is not None:
            on_failed_attempt({"attempt": attempt, "error": last_err, "seconds": elapsed, "usage": usage or {},
                               "model": model, "tier": tier})
        if attempt < retries():
            if before_retry is not None:
                before_retry(attempt + 1)
            # Usage-window exhaustion needs a long pause; anything else retries almost at once.
            time.sleep(180 * attempt if _looks_rate_limited(last_err) else 2)

    raise LLMError(f"all {retries()} attempts failed ({provider}/{model}): {last_err}")


def ask(prompt: str, tier: str = "analyst", persona_path: str | None = None,
        schema_path: str | None = None, agent: str | None = None) -> str:
    """Send a prompt through the configured provider. Returns the final text.

    Raises LLMError after all retries fail. Never returns an empty string.
    """
    return ask_ex(prompt, tier, persona_path, schema_path, agent)["text"]


# ── CLI ────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RECON LLM client")
    ap.add_argument("--tier", default="analyst", help="fast | analyst | synth (legacy model ids accepted)")
    ap.add_argument("--persona", help="persona markdown file; wraps the prompt in the role frame")
    ap.add_argument("--agent", help="agent name for the log (default: persona file stem)")
    ap.add_argument("--schema", help="JSON schema file for structured output (codex only)")
    ap.add_argument("--prompt-file", help="read the prompt from this file instead of stdin")
    ap.add_argument("--provider", help="override RECON_LLM_PROVIDER for this call")
    args = ap.parse_args(argv)

    if args.provider:
        os.environ["RECON_LLM_PROVIDER"] = args.provider
    prompt = Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else sys.stdin.read()
    if not prompt.strip():
        print("[ERROR] empty prompt", file=sys.stderr)
        return 2
    try:
        sys.stdout.write(ask(prompt, tier=args.tier, persona_path=args.persona,
                             schema_path=args.schema, agent=args.agent))
        sys.stdout.write("\n")
        return 0
    except LLMError as e:
        # Keep v1's contract: an error marker on stdout so callers can detect it,
        # a non-zero exit so scripts that care can react.
        sys.stdout.write(f"[ERROR] {e}\n")
        print(f"[llm] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
