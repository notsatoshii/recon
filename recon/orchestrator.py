#!/usr/bin/env python3
"""RECON v2 orchestrator (Phase C): the daily brief as typed phases with resume.

Phase B built the typed phases around the bash pipeline's stages; Phase C (docs/v2/phase-c-spec.md)
replaces the middle, from the fixed TENSIONS challenges to the vote:
  - triage writes the questions of the day from config/prompts/debate/questions.md; a programmatic gate
    (recon/debate.py gate_questions) drops bad ones; the question ledger carries them across runs.
  - takes answer every question from the lens: positions first, a 150-word take last, and up to 6 KB of
    YOUR LENS DATA per agent (debate.lens_extras, LENS_RAW) after the cached shared prefix.
  - pairing (no LLM) stages up to 3 debates by the widest probability gap that straddles the median,
    within the call budget; a split with no eligible pair is split_unpaired; a day with no split gets
    one red-team call (consensus).
  - challenges: both sides steelman then rebut (2 calls a pair). responses: a crux search over the run
    folder first (cruxsearch.json), then one response per side with the evidence gate on the move.
  - cruxcheck: 0-1 neutral referee call on the widest debate. positions, split, memory: no LLM.
  - synthesis reads the split sheet and the lens notes (not the full record) and writes WHERE THE VIEWS
    SPLIT with code-rendered count phrases; checks add agent names, count matches and process words.
  - RECON_CALL_BUDGET (24, planning) and RECON_CALL_CEILING (32, hard) bound the calls; every skip is in
    run.json usage.budget_skips.

Usage
  python3 recon/orchestrator.py [--skip-collect] [--no-telegram] [--run-id ID] [--as-of DATE]
                                [--from-phase NAME | --resume] [--package-from DIR] [--replay DIR]
                                [--state-dir DIR] [--dry-run]
  --dry-run    dry-run provider, no Telegram/archive/knowledge DB; implies --skip-collect --skip-score.
  --run-id     folder name under briefs/ (default: the run day). Tagged ids (anything but the bare day)
               skip archive/ and the knowledge DB, and keep memory, state, ledger and scores under
               <run>/state unless --state-dir is given.
  --as-of      the run's day (prompts, question dates, ledger ids). Default: today.
  --replay DIR implies --package-from DIR --skip-score --no-telegram, cold state in <run>/state, an empty
               historical context, and nothing read from data-sources/.
  --state-dir  where memory, state, ledger and agent scores live (default config/ for the daily run).
  RECON_STOP_AFTER=<phase>  return after that phase (spread probe: takes; stability rerun: pairing).
Exit code 0 when a brief was written (or the run stopped where RECON_STOP_AFTER said), 1 otherwise.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from recon import agentmem, debate, evidence, llm, prompts, schemas  # noqa: E402

RECON_HOME = llm.RECON_HOME
AGENTS = schemas.AGENTS
PHASES = ["score", "collect", "context", "package", "triage", "takes", "pairing", "challenges",
          "responses", "cruxcheck", "positions", "split", "memory", "synthesis", "checks",
          "deliver", "record"]
ITEM_DIRS = {"takes", "challenges", "responses"}
PHASE_ALIASES = {"deepdive": "cruxcheck"}   # --from-phase deepdive still works, with a log line
BRIEF_SECTIONS = schemas.BRIEF_SECTIONS
PARALLEL = int(os.environ.get("RECON_PARALLEL", "5"))
SYNTH_CALLS = 2            # draft + filter until Phase D drops the filter (§1.1)
LENS = {"trader": "markets, flows, positioning", "narrator": "narratives and social attention",
        "builder": "products and protocols", "analyst": "the sector model", "skeptic": "risks and weak claims",
        "policy_analyst": "regulation and policy", "user_agent": "users and adoption",
        "macro_strategist": "macro and geopolitics", "ai_engineer": "AI models and tools"}
# Sources of 00_raw_data.md and of the --skip-collect package (phase-e §4.1a); the last four pass the 72 h
# freshness check (phase-e §2), the v1 sources only with RECON_FRESH_V1=1.
RAW_SOURCES = ["reddit", "twitter", "onchain", "news", "ai_tools", "fundraising",
               "polymarket", "kalshi", "changelogs", "zdnet_kr"]
PKG_SOURCES = ["bettafish", "worldmonitor", "onchain", "news", "zdnet_kr", "reddit", "twitter", "ai_tools",
               "changelogs", "fundraising", "polymarket", "kalshi"]
PHASE_E_NAMES = {"polymarket": "Polymarket", "kalshi": "Kalshi", "changelogs": "Changelogs", "zdnet_kr": "ZDNet Korea"}
ARCHIVE_SOURCES = ("reddit", "twitter", "onchain", "news", "worldmonitor", "bettafish", "ai_tools", "fundraising",
                   "polymarket", "kalshi", "changelogs", "zdnet_kr")

ROLE = """--- YOUR ROLE ---
You are playing a specific role. Stay in character. Answer from the material in this prompt only.
{persona}
--- END ROLE ---"""

CITATION_RULE = """CITATION RULE (READ FIRST):
1. Only cite numbers that appear in TODAY'S INTELLIGENCE PACKAGE above or in YOUR LENS DATA.
2. Do NOT cite numbers from your memory or prior runs as current fact. Your memory is context, not today's data.
3. For claims from social media posts or Reddit threads, prefix with 'reportedly' or 'per social media'.
4. If a specific number isn't in the data package, say 'reportedly' or omit it. Never invent statistics."""

# §13.1: challenge.md, response.md and red_team.md rely on the last sentence.
DEBATE_FORMAT = ("This debate format replaces your persona's usual output format: no headings, no lists, no\n"
                 "portfolio or allocation lines, no ROADMAP or BUILD NOW tags, no content angles, no sign-off.\n"
                 "Refer to the other analyst only as \"the other view\", never by name or role.")


class PhaseFailed(RuntimeError):
    pass


class BudgetSkip(PhaseFailed):
    """An optional call skipped because the call ceiling is reached (§1.1)."""


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def read(p: Path, limit: int | None = None) -> str:
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    if limit is not None:
        b = t.encode("utf-8")
        if len(b) > limit:
            t = b[:limit].decode("utf-8", errors="ignore")
    return t


def cap_bytes(t: str, limit: int) -> str:
    b = (t or "").encode("utf-8")
    return t if len(b) <= limit else b[:limit].decode("utf-8", errors="ignore")


def section_text(text: str, heading: str, maxbytes: int) -> str:
    """Port of run_recon.sh section(): from HEADING to the next '## ' heading, capped."""
    out, n, on = [], 0, False
    for i, line in enumerate((text or "").splitlines()):
        if line.startswith(heading):
            on = True
        elif on and i > 0 and line.startswith("## "):
            break
        if on:
            n += len(line) + 1
            if n > maxbytes:
                break
            out.append(line)
    return "\n".join(out)


def section(path: Path, heading: str, maxbytes: int) -> str:
    return section_text(read(path), heading, maxbytes)


def block_text(text: str, heading: str, maxbytes: int) -> str:
    """A '# ' block (up to the next '# ' line) of the raw file, capped at whole lines."""
    lines = debate._block(text or "", heading) or []
    out, n = [], 0
    for line in lines:
        n += len(line.encode("utf-8")) + 1
        if n > maxbytes:
            break
        out.append(line)
    return "\n".join(out)


def ev_lines(items) -> str:
    out = [f"- [{e.get('section', '')}] \"{(e.get('quote') or '').strip()[:220]}\"" for e in items or []
           if (e.get("quote") or "").strip()]
    return "\n".join(out) or "- none"


def position(take: dict, qid: str) -> dict:
    return next((p for p in (take or {}).get("positions") or [] if p.get("question_id") == qid), {})


def take_quotes(take: dict) -> list[str]:
    qs = [e.get("quote", "") for p in (take or {}).get("positions") or [] for e in p.get("evidence") or []]
    qs += [c.get("quote", "") for c in (take or {}).get("claims") or []]
    return [q for q in qs if q]


def rec_quotes(rec: dict | None) -> list[str]:
    return [e.get("quote", "") for e in ((rec or {}).get("data") or {}).get("evidence") or [] if e.get("quote")]


def plus_days(day: str, n: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


class Run:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.day = args.as_of or self.today
        self.dry = args.dry_run
        self.replay = bool(args.replay)
        default_id = f"{self.day}-dry" if self.dry else (f"{self.day}-replay" if self.replay else self.day)
        self.run_id = args.run_id or default_id
        self.tagged = self.run_id != self.day or self.day != self.today
        self.dir = RECON_HOME / "briefs" / self.run_id
        self.pdir = self.dir / "phases"
        self.log_file = RECON_HOME / "logs" / f"{self.run_id}.log"
        self.started = datetime.now()
        self.t0 = time.time()
        self.lock = threading.Lock()
        self.forced: set[str] = set()
        self.phase_times: list[dict] = []
        self.data_dir = RECON_HOME / "data-sources"
        if args.state_dir:
            sd = Path(args.state_dir)
            self.state_root = sd if sd.is_absolute() else RECON_HOME / sd
        elif self.dry or self.replay or self.tagged:
            self.state_root = self.dir / "state"
        else:
            self.state_root = RECON_HOME / "config"
        self.mem_dir = self.state_root / "agent_memory"
        self.state_dir = self.state_root / "agent_state"
        self.ledger_path = self.state_root / "questions" / "ledger.jsonl"
        self.scores_dir = self.state_root / "agent_scores"
        self.schema_paths: dict[str, str] = {}
        self._shared: str | None = None
        self._locator: evidence.Locator | None = None
        self._lens: dict | None = None
        self.budget = int(os.environ.get("RECON_CALL_BUDGET", "24"))
        self.ceiling = int(os.environ.get("RECON_CALL_CEILING", "32"))
        self.ncalls = 0
        self.inflight = 0
        self.stop_after = os.environ.get("RECON_STOP_AFTER", "").strip()
        self.active = self.roster()

    # ── logging and artifacts ──────────────────────────────
    def log(self, msg: str) -> None:
        line = f"[{now()}] {msg}"
        print(line, flush=True)
        with self.lock:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def art(self, name: str) -> Path:
        return self.pdir / f"{name}.json"

    def have(self, name: str) -> bool:
        return name not in self.forced and self.art(name).exists()

    def load(self, name: str):
        return json.loads(self.art(name).read_text(encoding="utf-8"))

    def save(self, name: str, data) -> None:
        p = self.art(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)

    def check_artifact(self, kind: str, value, what: str) -> None:
        """Validate a programmatic artifact against schemas.ARTIFACTS; a mismatch is logged, not fatal."""
        try:
            schemas.validate(json.loads(json.dumps(value)), schemas.ARTIFACTS[kind])
        except schemas.SchemaError as e:
            self.log(f"  WARNING: {what} does not match the {kind} schema: {e}")

    def record_call(self, meta: dict) -> None:
        with self.lock:
            self.pdir.mkdir(parents=True, exist_ok=True)
            with (self.pdir / "calls.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(meta, ensure_ascii=False) + "\n")
            self.ncalls += 1

    def calls(self) -> list[dict]:
        p = self.pdir / "calls.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

    def skip(self, phase: str, item: str, reason: str) -> None:
        """Log and record a budget or ceiling skip (run.json usage.budget_skips)."""
        rec = {"phase": phase, "item": item, "reason": reason, "used": self.ncalls}
        self.log(f"  SKIP ({reason}): {phase}/{item} at {self.ncalls} calls "
                 f"(budget {self.budget}, ceiling {self.ceiling})")
        with self.lock:
            self.pdir.mkdir(parents=True, exist_ok=True)
            with (self.pdir / "budget_skips.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def budget_skips(self) -> list[dict]:
        p = self.pdir / "budget_skips.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

    def roster(self) -> list[str]:
        """Active agents: config/roster.json {"active": [...]} when present (§18), else all nine."""
        try:
            r = json.loads(read(RECON_HOME / "config" / "roster.json") or "{}")
            act = [a for a in r.get("active", []) if a in AGENTS]
            return act or list(AGENTS)
        except json.JSONDecodeError:
            return list(AGENTS)

    # ── setup ──────────────────────────────────────────────
    def prepare(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        a = self.args
        fresh = not a.from_phase and not a.resume
        if a.from_phase:
            i = PHASES.index(a.from_phase)
            self.forced = set(PHASES[i:])
            for name in self.forced:
                if self.art(name).exists():
                    self.art(name).unlink()
                if name in ITEM_DIRS:
                    shutil.rmtree(self.pdir / name, ignore_errors=True)
            for fname in ("calls.jsonl", "budget_skips.jsonl"):
                p = self.pdir / fname
                if p.exists():
                    keep = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
                    keep = [c for c in keep if c.get("phase") not in self.forced]
                    p.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in keep), encoding="utf-8")
            if "split" in self.forced:
                (self.pdir / "split_sheet.json").unlink(missing_ok=True)
            self.log(f"Resuming {self.run_id} from phase '{a.from_phase}'")
        elif a.resume:
            self.log(f"Resuming {self.run_id}: phases with artifacts are reused")
        else:
            # fresh run: clear earlier artifacts of this run id (00_* inputs stay for --skip-collect)
            shutil.rmtree(self.pdir, ignore_errors=True)
            for pat in ("03_take_*.md", "04a_*.md", "04c_*.md", "05_resp_*.md", "05_5_*.md", "06_vote_*.md",
                        "07_*.md", "07_*.json", "run.json"):
                for f in self.dir.glob(pat):
                    f.unlink()
        self.pdir.mkdir(parents=True, exist_ok=True)
        self.ncalls = len(self.calls())
        self.schema_paths = schemas.write_all(self.pdir / "schemas")
        in_run = self.dir in self.state_root.parents or self.state_root == self.dir
        if self.replay and in_run:
            if fresh:
                shutil.rmtree(self.state_root, ignore_errors=True)   # cold start: no October memory in September
            for d in (self.mem_dir, self.state_dir, self.ledger_path.parent, self.scores_dir):
                d.mkdir(parents=True, exist_ok=True)
        elif in_run:
            for src, dst in ((RECON_HOME / "config" / "agent_memory", self.mem_dir),
                             (RECON_HOME / "config" / "agent_state", self.state_dir)):
                if not dst.exists() and src.exists():
                    shutil.copytree(src, dst)

    # ── LLM ────────────────────────────────────────────────
    def call(self, phase: str, key: str, tier: str, prompt: str, schema: str | None = None,
             agent: str | None = None, persona_path: str | None = None, optional: bool = True):
        """One model call. With a schema the reply is parsed and validated; one re-ask on a bad reply.
        An optional call (everything but the first triage call, the takes and the synthesis) is skipped
        once calls.jsonl reaches RECON_CALL_CEILING; so is a schema re-ask outside takes and synthesis."""
        sp = self.schema_paths.get(schema) if schema else None
        reask_optional = phase not in ("takes", "synthesis")
        attempts = 0
        p = prompt
        while True:
            attempts += 1
            opt = optional if attempts == 1 else reask_optional
            with self.lock:
                if opt and self.ncalls + self.inflight >= self.ceiling:
                    blocked = True
                else:
                    blocked = False
                    self.inflight += 1
            if blocked:
                self.skip(phase, key if attempts == 1 else f"{key} (schema re-ask)", "ceiling")
                raise BudgetSkip(f"{phase}/{key}: call ceiling {self.ceiling} reached")
            try:
                try:
                    res = llm.ask_ex(p, tier=tier, persona_path=persona_path, schema_path=sp, agent=agent or key,
                                     note=f"phase={phase}")
                except llm.LLMError as e:
                    self.record_call({"phase": phase, "key": key, "agent": agent, "tier": tier, "ok": False,
                                      "error": str(e)[:300], "at": now()})
                    raise
                u = res["usage"] or {}
                meta = {"phase": phase, "key": key, "agent": agent, "tier": res["tier"], "model": res["model"],
                        "in_tok": u.get("input_tokens", 0), "cached_tok": u.get("cached_input_tokens", 0),
                        "out_tok": u.get("output_tokens", 0), "seconds": res["seconds"],
                        "prompt_bytes": res["prompt_bytes"], "ok": True, "at": now()}
                if not schema:
                    self.record_call(meta)
                    return res["text"], meta
                try:
                    data = schemas.parse(res["text"], schema)
                    self.record_call(meta)
                    return data, meta
                except schemas.SchemaError as e:
                    meta["ok"] = False
                    meta["error"] = f"schema: {e}"[:300]
                    self.record_call(meta)
                    if attempts >= 2:
                        raise PhaseFailed(f"{phase}/{key}: reply did not match the schema twice ({e})")
                    self.log(f"  {phase}/{key}: reply did not match the schema ({e}); asking once more")
                    p = (prompt + f"\n\nYour previous reply could not be used ({e}). "
                         "Reply again with ONE JSON object that matches the schema.")
            finally:
                with self.lock:
                    self.inflight -= 1

    def parallel(self, jobs: list[tuple[str, callable]]) -> dict:
        """Run (key, fn) jobs, PARALLEL at a time. Returns key -> result or the exception."""
        out = {}
        with ThreadPoolExecutor(max_workers=max(1, PARALLEL)) as ex:
            futs = {ex.submit(fn): key for key, fn in jobs}
            for f, key in futs.items():
                try:
                    out[key] = f.result()
                except Exception as e:  # noqa: BLE001 - one failed agent must not stop the others
                    out[key] = e
        return out

    # ── shared prompt pieces ───────────────────────────────
    def scorecard(self) -> str:
        p = self.dir / "00_scorecard_recent.md"
        if not p.exists():
            p = self.dir / "00_scorecard.md"
        return read(p, 6000)

    def docs(self) -> dict[str, str]:
        return {"package": read(self.dir / "00_data_package.md"), "raw": read(self.dir / "00_raw_data.md"),
                "view": read(self.dir / "01_filtered.md"), "social": read(self.dir / "01_social.md")}

    def locator(self) -> evidence.Locator:
        """Built once per run over package, raw, view and social (§3 item 4)."""
        if self._locator is None:
            self._locator = evidence.Locator(self.docs())
        return self._locator

    def lens(self) -> dict:
        if self._lens is None:
            d = self.docs()
            self._lens = debate.lens_extras(d["raw"], d["package"], d["view"])
        return self._lens

    def shared(self) -> str:
        """The block every triage and take prompt starts with, byte-identical, so the provider can
        reuse its cache across the calls."""
        if self._shared is None:
            view = read(self.dir / "01_filtered.md", 100000)
            sector = read(RECON_HOME / "config" / "sector_context.md", 8000)
            hist = read(self.dir / "00_historical_context.md", 3000)
            sc = self.scorecard()
            self._shared = f"""TODAY'S INTELLIGENCE PACKAGE ({self.day}). Every section is included, each trimmed to fit:
- SECTION 0 (CROSS-SOURCE): the same story seen in several sources.
- SECTION 1 (SENTIMENT): BettaFish sentiment analysis across social media and news.
- SECTION 2 (GEOPOLITICAL): World Monitor intelligence from 79 global sources.
- SECTION 3 (ON-CHAIN): market, DeFi and stablecoin data.
- SECTION 4 (NEWS): crypto, AI, AI education and Korea headlines.
- SECTION 5 (SOCIAL): Reddit hot posts and the most-engaged X posts of the last 72 h.
- SECTION 6 (AI & TOOLS): GitHub trending AI repos, Hacker News and AI tool changelogs.
- SECTION 7 (FUNDRAISING): crypto, AI and Korea rounds from news.
- SECTION 8 (PREDICTION MARKETS): Polymarket and Kalshi markets, when collected.

{view}
--- END PACKAGE ---

SECTOR CONTEXT (crypto and macro landscape; background, not today's data):
{sector}

--- HISTORICAL CONTEXT (reference, not re-analyze) ---
{hist}
--- END HISTORICAL CONTEXT ---

--- RECENT PREDICTIONS (check if any of yours were right or wrong) ---
{sc or 'No scorecard today.'}
--- END PREDICTIONS ---
"""
        return self._shared

    def persona(self, agent: str) -> str:
        return read(RECON_HOME / "personas" / f"{agent}.md")

    def role(self, agent: str) -> str:
        return ROLE.format(persona=self.persona(agent))

    def questions_block(self, triage: dict) -> str:
        qs = triage.get("questions") or []
        if not qs:
            return "QUESTIONS OF THE DAY: none today (leave positions empty).\n"
        lines = ["QUESTIONS OF THE DAY (answer each with your probability that the answer is yes):"]
        for q in qs:
            extra = "; ".join(x for x in (f"resolves {q['resolves_on']}" if q.get("resolves_on") else "",
                                          f"settles with: {q['settles_with']}" if q.get("settles_with") else "") if x)
            lines.append(f"[{q['id']}] {q['text']}" + (f" ({extra})" if extra else ""))
        return "\n".join(lines) + "\n"

    # ── ledger and agent scores (§2.6, §9.2) ───────────────
    def read_ledger(self) -> list[dict]:
        out = []
        for l in read(self.ledger_path).splitlines():
            try:
                out.append(json.loads(l))
            except json.JSONDecodeError:
                continue
        return out

    def open_ledger(self) -> list[dict]:
        """Open questions: not resolved, resolves_on >= today, newest 8."""
        lines = self.read_ledger()
        resolved = {l.get("ledger_id") for l in lines if l.get("type") == "resolution"}
        qs = [l for l in lines if l.get("type") == "question" and l.get("ledger_id") not in resolved
              and ((l.get("question") or {}).get("resolves_on") or "") >= self.day
              and l.get("day", "") < self.day]
        return qs[-8:]

    @staticmethod
    def append_jsonl(path: Path, lines: list[dict], key) -> int:
        """Append lines whose key is not in the file yet (idempotent appends); returns how many."""
        have = set()
        for l in read(path).splitlines():
            try:
                have.add(key(json.loads(l)))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        new = [l for l in lines if key(l) not in have]
        if new:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                for l in new:
                    f.write(json.dumps(l, ensure_ascii=False) + "\n")
        return len(new)

    # ── phases: inputs ─────────────────────────────────────
    def env(self) -> dict:
        e = dict(os.environ)
        e["RECON_HOME"] = str(RECON_HOME)
        e["RECON_RUN_DIR"] = str(self.dir)
        e["RECON_LOG_FILE"] = str(self.log_file)
        return e

    def py(self) -> str:
        v = Path(os.environ.get("RECON_VENV") or RECON_HOME.parent / "recon-venv") / "bin" / "python"
        return str(v) if v.exists() else sys.executable

    def sub(self, cmd: list[str], timeout: int, prefix: str = "  ") -> int:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, env=self.env(), cwd=str(RECON_HOME))
        except subprocess.TimeoutExpired:
            self.log(f"{prefix}{Path(cmd[1] if len(cmd) > 1 else cmd[0]).name}: timed out after {timeout}s")
            return 124
        for line in (p.stdout + p.stderr).splitlines():
            if line.strip():
                self.log(prefix + line.rstrip()[:300])
        return p.returncode

    def ph_score(self):
        if self.args.skip_score:
            self.log("PHASE -1: Scoring skipped (--skip-score)")
            return {"skipped": True}
        self.log("PHASE -1: Scoring yesterday's predictions...")
        rc = self.sub([self.py(), str(RECON_HOME / "scripts" / "score_yesterday.py")], 300)
        return {"rc": rc}

    def source_text(self, src: str) -> str:
        """data-sources/<src>/latest.md for the --skip-collect assembly. The Phase E files (and the v1
        files with RECON_FRESH_V1=1) pass the 72 h freshness check first (phase-e §2): a stale file is
        replaced by one SOURCE STALE line under its own heading."""
        p = self.data_dir / src / "latest.md"
        t = read(p)
        if not t:
            return ""
        if src in PHASE_E_NAMES or os.environ.get("RECON_FRESH_V1", "0") == "1":
            sys.path.insert(0, str(RECON_HOME / "scripts"))
            import collector_common  # noqa: E402 - stdlib only
            ok, age, stamp = collector_common.fresh(p)
            if not ok:
                name = PHASE_E_NAMES.get(src, src)
                when = (f"last good pull {stamp.strftime('%Y-%m-%d %H:%M UTC')} ({age:.0f} h old)" if stamp
                        else "no parseable stamp")
                return f"# {name} Intelligence\n\n- {name}: SOURCE STALE — {when}; not shown.\n"
        return t

    def assemble(self) -> None:
        """--skip-collect without a package: assemble the package and 00_raw_data.md from data-sources/."""
        pkg, raw = self.dir / "00_data_package.md", self.dir / "00_raw_data.md"
        if not pkg.exists():
            self.log("  Assembling the package from existing data-sources...")
            parts = [f"# RECON INTELLIGENCE PACKAGE -- {self.day}", f"## Assembled: {now()}", ""]
            for src in PKG_SOURCES:
                t = self.source_text(src)
                if t:
                    parts += ["---", "", t, ""]
            pkg.write_text("\n".join(parts), encoding="utf-8")
        if not raw.exists():
            self.log("  Assembling 00_raw_data.md from existing data-sources (phase-e §4.1a)...")
            parts = [f"# RAW DATA -- {self.day}", f"## Collected: {now()}", ""]
            for src in RAW_SOURCES:
                t = self.source_text(src)
                if t:
                    parts += ["---", "", t, ""]
            raw.write_text("\n".join(parts), encoding="utf-8")

    def ph_collect(self):
        pkg = self.dir / "00_data_package.md"
        a = self.args
        if a.package_from:
            src = Path(a.package_from)
            if not src.is_absolute():
                src = RECON_HOME / src
            n = 0
            for f in src.glob("00_*.md"):
                if f.name.startswith("00_scorecard") and (self.dir / f.name).exists() and not a.skip_score:
                    continue
                shutil.copy2(f, self.dir / f.name)
                n += 1
            self.log(f"PHASE 0: Package copied from {src} ({n} files)")
            if not (self.dir / "00_raw_data.md").exists():
                self.log("  WARNING: the package folder has no 00_raw_data.md (lens extras and the crux search "
                         "read only the package)")
        elif a.skip_collect:
            self.log("PHASE 0: Skipping data collection (--skip-collect)")
            self.assemble()
        else:
            self.log("PHASE 0: Collecting real data...")
            rc = self.sub(["/bin/bash", str(RECON_HOME / "scripts" / "collect_data.sh")], 5400, prefix="")
            if rc != 0:
                self.log(f"  collect_data.sh exited {rc}")
        if not pkg.exists():
            raise PhaseFailed("no data package")
        size = pkg.stat().st_size
        self.log(f"Data package: {size} bytes")
        if size < 2000:
            self.log(f"WARNING: Data package is only {size} bytes; proceeding with available data")
        return {"package_bytes": size, "raw_bytes": (self.dir / "00_raw_data.md").stat().st_size
                if (self.dir / "00_raw_data.md").exists() else 0}

    def ph_context(self):
        self.log("PHASE 0.1: Loading historical context...")
        hist = ""
        if self.replay:
            self.log("  Replay: historical context left empty (§15.1)")
        elif (RECON_HOME / "config" / "knowledge.db").exists():
            try:
                p = subprocess.run([sys.executable, str(RECON_HOME / "scripts" / "knowledge_db.py"), "context", "--days", "30"],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                                   env=self.env(), cwd=str(RECON_HOME))
                hist = p.stdout.strip()
            except subprocess.TimeoutExpired:
                pass
        if hist:
            self.log("  Loaded context from the knowledge database")
        (self.dir / "00_historical_context.md").write_text(hist + "\n", encoding="utf-8")
        sector = read(RECON_HOME / "config" / "sector_context.md")
        self.log(f"  Historical context: {len(hist.encode())} bytes; sector context {len(sector.encode())} bytes")
        return {"historical_bytes": len(hist.encode()), "sector_bytes": len(sector.encode())}

    def ph_package(self):
        self.log("PHASE 0.5: Building the agent view (per-section caps)...")
        p = subprocess.run([sys.executable, str(RECON_HOME / "scripts" / "build_agent_package.py"), str(self.dir)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                           env=self.env(), cwd=str(RECON_HOME))
        (self.dir / "01_package_report.txt").write_text(p.stdout + p.stderr, encoding="utf-8")
        for line in (p.stdout + p.stderr).splitlines():
            if line.strip():
                self.log("  " + line)
        view = self.dir / "01_filtered.md"
        if p.returncode == 2:
            raise PhaseFailed("the agent view dropped a package section that has content")
        if p.returncode != 0 or not view.exists() or view.stat().st_size == 0:
            self.log(f"WARNING: agent view builder failed (exit {p.returncode}); using the first 100 KB of the package")
            view.write_bytes((self.dir / "00_data_package.md").read_bytes()[:100000])
        if not (self.dir / "01_social.md").exists():
            (self.dir / "01_social.md").write_text("No social extract today.\n", encoding="utf-8")
        rep = {}
        try:
            rep = json.loads(read(self.dir / "01_package_report.json") or "{}")
        except json.JSONDecodeError:
            pass
        return {"rc": p.returncode, "view_bytes": view.stat().st_size, "report": rep}

    # ── triage (§2) ────────────────────────────────────────
    def due_items(self) -> str:
        """Ledger questions and agent predictions that resolve today or tomorrow."""
        days = {self.day, plus_days(self.day, 1)}
        out = []
        for l in self.read_ledger():
            q = l.get("question") or {}
            if l.get("type") == "question" and q.get("resolves_on") in days:
                med = (l.get("final_stats") or {}).get("median")
                out.append(f"- [{l.get('ledger_id')}] {q.get('text', '')} (resolves {q.get('resolves_on')}"
                           + (f"; median {med:.0f}%" if isinstance(med, (int, float)) else "") + ")")
        for a in self.active:
            st = read(self.state_dir / f"{agentmem.state_name(a)}_state.md")
            for line in st.splitlines():
                if line.startswith("- PREDICTIONS:") and any(f"resolves {d}" in line for d in days):
                    out.append(f"- ({LENS.get(a, a)} lens) {line[len('- PREDICTIONS:'):].strip()[:260]}")
        return "\n".join(dict.fromkeys(out)) or "- none"

    def novel_items(self) -> str:
        if self.replay:
            return "- none (replay: cold start)"
        prev = RECON_HOME / "briefs" / plus_days(self.day, -1) / "phases" / "takes"
        out = []
        if prev.exists():
            for f in sorted(prev.glob("*.json")):
                try:
                    t = json.loads(f.read_text(encoding="utf-8")).get("data", {})
                except json.JSONDecodeError:
                    continue
                if (t.get("novel") or "").strip():
                    out.append(f"- ({LENS.get(f.stem, f.stem)} lens) {debate.anonymise(t['novel'])[:240]}")
        return "\n".join(out) or "- none"

    def ph_triage(self):
        tier = os.environ.get("RECON_TRIAGE_TIER", "fast").strip().lower() or "fast"
        self.log(f"PHASE 1: Triage and questions of the day ({tier} tier, schema, gate)...")
        roster = "\n".join(f"- {a}: {LENS[a]}" for a in self.active)
        open_q = self.open_ledger()
        open_txt = "\n".join(f"- [{l['ledger_id']}] {(l.get('question') or {}).get('text', '')} (resolves "
                             f"{(l.get('question') or {}).get('resolves_on', '')})" for l in open_q) or "- none"
        prompt = self.shared() + "\n" + prompts.render(
            "questions", day=self.day, roster=roster, open_questions=open_txt, due_items=self.due_items(),
            novel_items=self.novel_items(), n_max=5)
        calls, from_model = [], True
        try:
            data, meta = self.call("triage", "triage", tier, prompt, schema="triage", agent="synthesizer", optional=False)
            calls.append(meta)
        except (llm.LLMError, PhaseFailed) as e:
            # The run still goes on without questions: no positions, no debate, no split sheet.
            self.log(f"WARNING: triage failed ({str(e)[:200]}); continuing without questions of the day")
            data, from_model = {"environment": "QUIET", "depth": "normal", "reason": "triage failed",
                                "weight_agents": [], "questions": []}, False
        depth = data.get("depth") if data.get("depth") in debate.QUESTIONS_BY_DEPTH else "normal"
        gate = debate.gate_questions(data.get("questions") or [], self.day, self.locator(), open_q, self.active, depth)
        reask = None
        if from_model and gate["needs_reask"]:
            reasons = "\n".join(f"- \"{d['text'][:120]}\": {d['reason']}" for d in gate["dropped"]) or "- (no questions)"
            p2 = (prompt + f"\n\nOnly {len(gate['kept'])} question(s) passed the program's checks. These were dropped:\n"
                  f"{reasons}\nReply again with one JSON object that matches the schema; give 3 to 5 questions that "
                  "avoid these problems.")
            self.log(f"  Gate kept {len(gate['kept'])} question(s); asking triage once more")
            try:
                data2, m2 = self.call("triage", "triage-reask", tier, p2, schema="triage", agent="synthesizer")
                calls.append(m2)
                depth2 = data2.get("depth") if data2.get("depth") in debate.QUESTIONS_BY_DEPTH else depth
                gate2 = debate.gate_questions(data2.get("questions") or [], self.day, self.locator(), open_q,
                                              self.active, depth2)
                reask = {"kept_before": len(gate["kept"]), "kept_after": len(gate2["kept"])}
                if len(gate2["kept"]) > len(gate["kept"]):
                    data, gate, depth = data2, gate2, depth2
            except BudgetSkip:
                reask = {"skipped": "ceiling"}
            except (llm.LLMError, PhaseFailed) as e:
                self.log(f"  triage re-ask failed ({str(e)[:150]})")
                reask = {"failed": str(e)[:200]}
        qs = gate["kept"]
        for q in qs:
            q["ledger_id"] = f"{self.run_id}-{q['id']}"
        data["questions"] = qs
        data["depth"] = depth
        data["weight_agents"] = [a for a in data.get("weight_agents", []) if a in self.active]
        data["active_agents"] = list(self.active)
        if data.get("environment") not in schemas.ENVIRONMENTS:
            data["environment"] = "QUIET"
        self.log(f"  Environment: ENVIRONMENT: {data['environment']} WEIGHT: {', '.join(data['weight_agents'])}")
        self.log(f"  Depth: {depth}; {len(qs)} questions kept, {len(gate['dropped'])} dropped by the gate")
        for d in gate["dropped"]:
            self.log(f"    dropped: {d['text'][:90]!r}: {d['reason']}")
        for q in qs:
            self.log(f"    [{q['id']}] ({q.get('kind')}, w{q.get('weight')}) {q['text'][:150]}")
        return {"data": data, "gate": {"dropped": gate["dropped"], "notes": gate["notes"], "reask": reask},
                "calls": calls}

    # ── takes (§3) ─────────────────────────────────────────
    def take_context(self, agent: str) -> str:
        extra = []
        mem = self.mem_dir / f"{agent}.md"
        if mem.exists():
            body = "\n".join(read(mem).splitlines()[2:])
            extra.append("YOUR RUNNING MEMORY (items you're tracking, prior predictions, recurring themes):\n"
                         + cap_bytes(body, 6000))
        if agent == "analyst" and (RECON_HOME / "config" / "analyst_model.md").exists():
            extra.append("YOUR WORKING MODEL (config/analyst_model.md):\n" + read(RECON_HOME / "config" / "analyst_model.md", 4000))
        st = self.state_dir / f"{agentmem.state_name(agent)}_state.md"
        if st.exists():
            tail = "\n".join(read(st).splitlines()[-60:])
            extra.append("YOUR STATE FROM PREVIOUS SESSIONS (newest entries):\n"
                         + tail.encode("utf-8")[-5000:].decode("utf-8", "ignore"))
        return "\n\n".join(extra) or "(no memory or state from earlier runs)"

    def take_prompt(self, agent: str, triage: dict) -> str:
        lx = self.lens().get(agent, {}).get("text") or "(no lens data today: cite the package)"
        return self.shared() + "\n" + self.questions_block(triage) + "\n" + prompts.render(
            "take", role=self.role(agent), context=self.take_context(agent), lens_data=lx, citation_rule=CITATION_RULE)

    def take_agents(self, triage: dict) -> list[str]:
        act = triage.get("active_agents") or self.active
        only = [a.strip() for a in os.environ.get("RECON_TAKE_AGENTS", "").split(",") if a.strip()]
        return [a for a in act if a in only] if only else list(act)

    def ph_takes(self, triage: dict):
        self.log(f"PHASE 3: Independent takes (parallel {PARALLEL}, schema, lens extras)...")
        d = self.pdir / "takes"
        d.mkdir(parents=True, exist_ok=True)
        agents = self.take_agents(triage)
        lens = self.lens()
        for a in agents:
            b = lens.get(a, {}).get("bytes", 0)
            if b < debate.LENS_WARN_BELOW:
                self.log(f"  WARNING: {a} gets {b} B of lens data (< {debate.LENS_WARN_BELOW})")
        has_q = bool(triage.get("questions"))

        def job(agent):
            f = d / f"{agent}.json"
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8"))
            prompt = self.take_prompt(agent, triage)
            tier = debate.LENS_TIER.get(agent, "analyst")
            data, meta = self.call("takes", agent, tier, prompt, schema="take", agent=agent, optional=False)
            calls = [meta]
            t = data.get("take", "")
            if re.search(r"I can't|I cannot|as an AI|I'm sorry", t[:400]) or (has_q and not data.get("positions")):
                self.log(f"  {agent}: a refusal or no positions; asking once more")
                data, meta = self.call("takes", agent, tier, prompt + "\n\nStay in character and answer every question.",
                                       schema="take", agent=agent, optional=False)
                calls.append(meta)
            lx = lens.get(agent, {})
            rec = {"agent": agent, "data": data, "calls": calls,
                   "fed": {"lens_extra_bytes": lx.get("bytes", 0),
                           "lens_extra_headings": [e for e in lx.get("entries", []) if e.get("found")]}}
            f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            (self.dir / f"03_take_{agent}.md").write_text(data.get("take", ""), encoding="utf-8")
            return rec

        res = self.parallel([(a, (lambda a=a: job(a))) for a in agents])
        takes = {}
        for a, r in res.items():
            if isinstance(r, Exception):
                self.log(f"  {a}: take FAILED ({str(r)[:200]})")
            else:
                takes[a] = r["data"]
                self.log(f"  {a}: take {len(r['data'].get('take', '').split())} words, "
                         f"{len(r['data'].get('positions', []))} positions, lens {r.get('fed', {}).get('lens_extra_bytes', 0)} B")
        need = min(3, len(agents))
        if len(takes) < need:
            raise PhaseFailed(f"only {len(takes)} takes")
        self.log(f"  All takes complete ({len(takes)}/{len(res)})")
        return {"agents": sorted(takes)}

    def load_items(self, phase: str) -> dict:
        d = self.pdir / phase
        out = {}
        if d.exists():
            for f in sorted(d.glob("*.json")):
                out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        return out

    # ── pairing (§4) ───────────────────────────────────────
    def ev_checked(self, items) -> list[dict]:
        out = []
        for e in items or []:
            q = (e.get("quote") or "").strip()
            loc = self.locator().locate(q) if q else {"status": "empty", "cls": "", "doc": None, "line": None}
            out.append({"section": e.get("section", "") or "", "quote": q[:300], "status": loc["status"],
                        "cls": loc.get("cls") or "", "doc": loc.get("doc"), "line": loc.get("line")})
        return out

    def take_evidence(self, takes: dict) -> dict:
        """{agent: {qid: [EV_CHECKED]}}: each take position's evidence, verified once (§3)."""
        return {a: {p.get("question_id", ""): self.ev_checked(p.get("evidence")) for p in t.get("positions") or []
                    if p.get("question_id")} for a, t in takes.items()}

    def yesterday_pairs(self) -> set:
        if self.replay:
            return set()
        p = RECON_HOME / "briefs" / plus_days(self.day, -1) / "phases" / "pairing.json"
        try:
            pr = json.loads(read(p) or "{}")
        except json.JSONDecodeError:
            return set()
        return {frozenset((x["high"], x["low"])) for x in pr.get("pairs", []) if x.get("high") and x.get("low")}

    def ph_pairing(self, triage: dict, takes: dict):
        self.log("PHASE 3.5: Pairing by widest probability gap (no LLM)...")
        qs = triage.get("questions") or []
        p = {a: debate.take_values(t) for a, t in takes.items()}
        evq = self.take_evidence(takes)
        active = sorted(takes)
        depth = triage.get("depth", "normal")
        depth_target = debate.PAIRS_BY_DEPTH.get(depth, 3)
        gap_min = int(os.environ.get("RECON_PAIR_GAP", debate.GAP_MIN_DEFAULT))
        used = self.ncalls
        target, crux = debate.budget_pairs(used, self.budget, SYNTH_CALLS, depth_target)
        at_ceiling = used >= self.ceiling
        if at_ceiling:
            target, crux = 0, False
        res = debate.pair(qs, p, evq, active, depth, target, gap_min, self.yesterday_pairs())
        full = debate.pair(qs, p, evq, active, depth, depth_target, gap_min, self.yesterday_pairs())
        kept = {(x["question_id"], x["high"], x["low"]) for x in res["pairs"]}
        for x in full["pairs"]:
            if (x["question_id"], x["high"], x["low"]) not in kept:
                self.skip("pairing", f"pair {x['question_id']} {x['high']}-{x['low']}", "ceiling" if at_ceiling else "budget")
        res["positions_evidence"] = evq
        res["budget"] = {"used": used, "budget": self.budget, "ceiling": self.ceiling,
                         "target_before_budget": depth_target, "crux_check_planned": bool(crux)}
        self.check_artifact("pairing", res, "pairing.json")
        self.log(f"  Day type: {res['day_type']}; gap_min {gap_min}; target {target} of {depth_target} "
                 f"(calls used {used}, budget {self.budget}); {res['candidates_considered']} candidate pairs")
        for x in res["pairs"]:
            self.log(f"    [{x['question_id']}] {x['high']} {x['p_high']}% vs {x['low']} {x['p_low']}% (gap {x['gap']}, score {x['score']})")
        for u in res["unpaired"]:
            self.log(f"    unpaired [{u['question_id']}] range {u['range']}: {u['reason']}")
        if res.get("red_team"):
            rt = res["red_team"]
            self.log(f"    red team: {rt['agent']} on [{rt['question_id']}] ({rt['reason']})")
        return res

    # ── challenges (§5) and the red team (§4.3) ────────────
    def qmap(self, triage: dict) -> dict:
        return {q["id"]: q for q in triage.get("questions") or []}

    def ph_challenges(self, triage: dict, takes: dict, pairing: dict):
        self.log(f"PHASE 4: Challenges (steelman, then rebut; parallel {PARALLEL}, schema)...")
        d = self.pdir / "challenges"
        d.mkdir(parents=True, exist_ok=True)
        qm = self.qmap(triage)
        p = {a: debate.take_values(t) for a, t in takes.items()}
        jobs = []

        def challenge(c, t, qid):
            f = d / f"{c}__{t}__{qid}.json"
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8"))
            q = qm[qid]
            mine, theirs = position(takes[c], qid), position(takes[t], qid)
            my_ev, their_ev = mine.get("evidence") or [], theirs.get("evidence") or []
            prompt = prompts.render(
                "challenge", role=self.role(c), debate_format=DEBATE_FORMAT, qid=qid,
                question=debate.anonymise(q["text"]), resolves_on=q.get("resolves_on") or "not dated",
                settles_with=debate.anonymise(q.get("settles_with") or "not stated"),
                my_p=p[c][qid], my_reason=debate.anonymise(mine.get("reason", "")), my_evidence=ev_lines(my_ev),
                their_p=p[t][qid], their_reason=debate.anonymise(theirs.get("reason", "")),
                their_summary=debate.anonymise(takes[t].get("summary", "")), their_evidence=ev_lines(their_ev),
                excerpts=debate.excerpts([my_ev, their_ev], self.locator()))
            data, meta = self.call("challenges", f"{c}__{t}__{qid}", "analyst", prompt, schema="debate_challenge", agent=c)
            data["question_id"] = qid
            checks = {"flags": debate.challenge_checks(data), "evidence": self.ev_checked(data.get("evidence")),
                      "words": {"rebuttal": debate.words(data.get("rebuttal")),
                                "steelman_sentences": len(debate.sentences(data.get("steelman", "")))}}
            rec = {"challenger": c, "target": t, "question_id": qid, "type": "pair", "data": data,
                   "anon": debate.anonymise_record(data), "checks": checks, "calls": [meta]}
            f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            return rec

        def red_team(rt):
            a, qid = rt["agent"], rt["question_id"]
            f = d / f"redteam__{a}__{qid}.json"
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8"))
            q = qm[qid]
            vals = {x: p[x][qid] for x in takes if qid in p[x]}
            med = statistics.median(vals.values())
            side = (lambda v: v >= 50) if med >= 50 else (lambda v: v < 50)
            maj = sorted([x for x in vals if side(vals[x]) and x != a], key=lambda x: (abs(vals[x] - med), x))[:4]
            reasons = "\n".join(f"- {vals[x]}%: {debate.anonymise(position(takes[x], qid).get('reason', ''))}" for x in maj) or "- none"
            maj_ev = [e for x in maj for e in (position(takes[x], qid).get("evidence") or [])][:4]
            mine = position(takes[a], qid)
            prompt = prompts.render(
                "red_team", role=self.role(a), debate_format=DEBATE_FORMAT, qid=qid,
                question=debate.anonymise(q["text"]), resolves_on=q.get("resolves_on") or "not dated",
                settles_with=debate.anonymise(q.get("settles_with") or "not stated"), median=int(round(med)),
                count_phrase=debate.count_phrase(vals, "consensus"), majority_reasons=reasons,
                my_p=vals.get(a, int(round(med))), my_reason=debate.anonymise(mine.get("reason", "")),
                excerpts=debate.excerpts([mine.get("evidence") or [], maj_ev], self.locator()))
            data, meta = self.call("challenges", f"redteam__{a}__{qid}", "analyst", prompt, schema="red_team", agent=a)
            data["question_id"] = qid
            prob, nflags = debate.norm_p(data.get("probability"), list(vals.values()))
            data["probability"] = prob if prob is not None else int(round(med))
            checks = {"flags": debate.challenge_checks(data) + nflags, "evidence": self.ev_checked(data.get("evidence"))}
            rec = {"challenger": a, "target": "", "agent": a, "question_id": qid, "type": "redteam", "data": data,
                   "anon": debate.anonymise_record(data), "checks": checks, "median": med, "calls": [meta]}
            f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            return rec

        if pairing.get("day_type") == "debate":
            for x in pairing["pairs"]:
                hi, lo, qid = x["high"], x["low"], x["question_id"]
                jobs.append((f"{hi}__{lo}__{qid}", (lambda c=hi, t=lo, q=qid: challenge(c, t, q))))
                jobs.append((f"{lo}__{hi}__{qid}", (lambda c=lo, t=hi, q=qid: challenge(c, t, q))))
        elif pairing.get("day_type") == "consensus" and pairing.get("red_team"):
            rt = pairing["red_team"]
            jobs.append((f"redteam__{rt['agent']}__{rt['question_id']}", (lambda rt=rt: red_team(rt))))
        else:
            self.log(f"  No challenges ({pairing.get('day_type')})")
        res = self.parallel(jobs)
        ok = []
        for k, r in res.items():
            if isinstance(r, BudgetSkip):
                self.log(f"  {k}: skipped (call ceiling)")
            elif isinstance(r, Exception):
                self.log(f"  {k}: challenge FAILED ({str(r)[:200]})")
            else:
                ok.append(k)
                fl = r["checks"]["flags"]
                self.log(f"  {k}: ok" + (f" (flags: {', '.join(fl)})" if fl else ""))
        out = {"jobs": [k for k, _ in jobs], "ok": ok}
        rts = [r for r in res.values() if isinstance(r, dict) and r.get("type") == "redteam"]
        if rts:
            out["redteam_search"] = self.redteam_search(rts[0], takes)
        self.log(f"  Challenges complete ({len(ok)}/{len(jobs)})")
        return out

    def corpus_docs(self) -> dict[str, str]:
        """The crux-search corpus: run folder only (§6)."""
        d = self.docs()
        return {"raw": d["raw"], "package": d["package"], "social": d["social"]}

    def search_terms(self, texts: list[str]) -> dict:
        docs = self.corpus_docs()
        vocab = debate.lowercase_vocab(docs.values())
        return debate.drop_frequent_entities(debate.crux_terms(texts, vocab), docs)

    def redteam_search(self, rec: dict, takes: dict) -> dict:
        """§6 on a consensus day: the crux search on the red team's crux, stored under 'redteam'."""
        dd = rec["data"]
        texts = [(dd.get("crux") or {}).get("claim", ""), (dd.get("crux") or {}).get("observable", ""),
                 (dd.get("would_change_my_mind") or {}).get("observable", "")]
        terms = self.search_terms(texts)
        excl = [q for t in takes.values() for q in take_quotes(t)] + rec_quotes(rec)
        res = debate.crux_search(terms, self.corpus_docs(), excl, self.locator(),
                                 exclude_positions=debate.quote_positions(excl, self.locator()))
        entry = {"question_id": rec["question_id"], "agent": rec["agent"], "crux_terms": terms, **res}
        cs = self.load("cruxsearch") if self.art("cruxsearch").exists() else {}
        cs = {"pairs": cs.get("pairs", []) if isinstance(cs, dict) else [], "redteam": entry}
        self.save("cruxsearch", cs)
        self.log(f"  Crux search (red team): {len(res['hits'])} hits")
        return {"hits": len(res["hits"])}

    # ── responses (§6, §7) ─────────────────────────────────
    def ph_responses(self, triage: dict, takes: dict, pairing: dict, chs: dict):
        self.log(f"PHASE 5: Crux search, then responses with the evidence gate (parallel {PARALLEL})...")
        qm = self.qmap(triage)
        p = {a: debate.take_values(t) for a, t in takes.items()}
        old = self.load("cruxsearch") if self.art("cruxsearch").exists() else {}
        cs = {"pairs": [], "redteam": (old or {}).get("redteam")}
        for x in pairing.get("pairs") or []:
            hi, lo, qid = x["high"], x["low"], x["question_id"]
            by_hi, by_lo = chs.get(f"{hi}__{lo}__{qid}"), chs.get(f"{lo}__{hi}__{qid}")
            texts = []
            for rec in (by_hi, by_lo):
                dd = (rec or {}).get("data") or {}
                texts += [(dd.get("crux") or {}).get("claim", ""), (dd.get("crux") or {}).get("observable", ""),
                          (dd.get("would_change_my_mind") or {}).get("observable", "")]
            terms = self.search_terms(texts)
            excl = take_quotes(takes[hi]) + take_quotes(takes[lo]) + rec_quotes(by_hi) + rec_quotes(by_lo)
            res = debate.crux_search(terms, self.corpus_docs(), excl, self.locator(),
                                     exclude_positions=debate.quote_positions(excl, self.locator()))
            cs["pairs"].append({"question_id": qid, "high": hi, "low": lo, "crux_terms": terms, **res})
            self.log(f"  Crux search [{qid}] {hi}/{lo}: {len(res['hits'])} hits; terms {res['terms']}")
        self.save("cruxsearch", cs)   # written first, so --from-phase responses reruns it (§6)
        csq = {e["question_id"]: e for e in cs["pairs"]}
        d = self.pdir / "responses"
        d.mkdir(parents=True, exist_ok=True)

        def job(rec):
            agent, other, qid = rec["target"], rec["challenger"], rec["question_id"]
            f = d / f"{agent}__{qid}.json"
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8"))
            q = qm[qid]
            mine = position(takes[agent], qid)
            an = rec["anon"]
            crux = an.get("crux") or {}
            wcm = an.get("would_change_my_mind") or {}
            entry = csq.get(qid) or {"block": "(nothing found on disk for this crux)", "hits": [], "crux_terms": {}}
            my_ev = mine.get("evidence") or []
            their_ev = (position(takes[other], qid).get("evidence") or []) + (rec["data"].get("evidence") or [])
            prompt = prompts.render(
                "response", role=self.role(agent), debate_format=DEBATE_FORMAT, qid=qid,
                question=debate.anonymise(q["text"]), resolves_on=q.get("resolves_on") or "not dated",
                my_p=p[agent][qid], my_reason=debate.anonymise(mine.get("reason", "")), my_evidence=ev_lines(my_ev),
                steelman=an.get("steelman", ""),
                crux=f"{crux.get('claim', '')} (observable: {crux.get('observable', '') or 'not given'}; "
                     f"by {crux.get('by_date') or 'no date'})",
                rebuttal=an.get("rebuttal", ""), their_evidence=ev_lines(an.get("evidence")),
                would_change=f"{wcm.get('observable', '')} at {wcm.get('level', '')} by {wcm.get('by_date') or 'no date'}",
                excerpts=debate.excerpts([my_ev, their_ev], self.locator()), crux_data=entry["block"])
            data, meta = self.call("responses", f"{agent}__{qid}", "analyst", prompt, schema="debate_response", agent=agent)
            data["question_id"] = qid
            own_rec = chs.get(f"{agent}__{other}__{qid}")
            own_q = take_quotes(takes[agent]) + rec_quotes(own_rec)
            oth_q = take_quotes(takes[other]) + rec_quotes(rec)
            move = debate.gate_move(agent, p[agent][qid], data.get("new_probability"), debate.take_values(takes[agent]),
                                    p[other][qid], data.get("verdict", "hold"), data.get("new_evidence"), own_q, oth_q,
                                    entry.get("hits"), entry.get("crux_terms") or {}, self.locator())
            out = {"agent": agent, "question_id": qid, "opponent": other, "data": data, "move": move, "calls": [meta]}
            f.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
            return out

        recs = [r for r in chs.values() if r.get("type") == "pair"]
        res = self.parallel([(f"{r['target']}__{r['question_id']}", (lambda r=r: job(r))) for r in recs])
        ok = []
        for k, r in res.items():
            if isinstance(r, BudgetSkip):
                self.log(f"  {k}: skipped (call ceiling)")
            elif isinstance(r, Exception):
                self.log(f"  {k}: response FAILED ({str(r)[:200]})")
            else:
                ok.append(k)
                mv = r["move"]
                self.log(f"  {k}: {r['data'].get('verdict')} {mv['take']}% -> requested {mv['requested']} -> gated "
                         f"{mv['gated']}% ({mv['evidence_source']})" + (f" flags: {', '.join(mv['flags'])}" if mv["flags"] else ""))
        self.log(f"  Responses complete ({len(ok)}/{len(recs)})")
        return {"ok": ok, "crux_hits": {e["question_id"]: len(e["hits"]) for e in cs["pairs"]}}

    # ── crux check (§8) ────────────────────────────────────
    def ph_cruxcheck(self, triage: dict, takes: dict, pairing: dict, chs: dict, resps: dict):
        self.log("PHASE 5.5: Crux check (0-1 neutral referee call)...")
        qm = self.qmap(triage)
        cs = self.load("cruxsearch") if self.art("cruxsearch").exists() else {}
        p = {a: debate.take_values(t) for a, t in takes.items()}
        cand = None
        for x in pairing.get("pairs") or []:
            hi, lo, qid = x["high"], x["low"], x["question_id"]
            entry = next((e for e in cs.get("pairs", []) if e["question_id"] == qid), None)
            rh, rl = resps.get(f"{hi}__{qid}"), resps.get(f"{lo}__{qid}")
            gb = x["p_high"] - x["p_low"]
            fh = rh["move"]["gated"] if rh else x["p_high"]
            fl = rl["move"]["gated"] if rl else x["p_low"]
            ga = abs(fh - fl)
            if gb < debate.CRUX_GAP or not entry or not entry.get("hits"):
                continue
            key = (-gb, -ga, qid)
            if cand is None or key < cand[0]:
                cand = (key, {"kind": "pair", "question_id": qid, "high": hi, "low": lo, "gap_before": gb,
                              "gap_after": ga, "entry": entry})
        rt = cs.get("redteam")
        if cand is None and pairing.get("day_type") == "consensus" and rt and rt.get("hits"):
            rec = next((r for r in chs.values() if r.get("type") == "redteam"), None)
            if rec:
                gap = abs(rec["data"].get("probability", 0) - rec.get("median", 0))
                if gap >= debate.CRUX_GAP:
                    cand = ((0,), {"kind": "redteam", "question_id": rec["question_id"], "agent": rec["agent"],
                                   "gap_before": gap, "gap_after": gap, "entry": rt, "rec": rec})
        if cand is None:
            why = ("no debate or red-team crux with a gap of at least "
                   f"{debate.CRUX_GAP} points and a crux-search hit")
            self.log(f"  No crux check: {why}")
            return {"ran": False, "reason": why}
        c = cand[1]
        qid = c["question_id"]
        if not pairing.get("budget", {}).get("crux_check_planned"):
            self.skip("cruxcheck", qid, "budget")
            return {"ran": False, "reason": "dropped by the call budget (§1.1)", "question_id": qid}
        q = qm[qid]
        if c["kind"] == "pair":
            hi, lo = c["high"], c["low"]
            ch_hi, ch_lo = chs.get(f"{hi}__{lo}__{qid}"), chs.get(f"{lo}__{hi}__{qid}")
            agreed = all((resps.get(f"{a}__{qid}") or {}).get("data", {}).get("crux_agreed", {}).get("verdict") == "yes"
                         for a in (hi, lo))
            cruxes = [((r or {}).get("anon") or {}).get("crux", {}).get("claim", "") for r in (ch_hi, ch_lo)]
            cruxes = [x for x in cruxes if x]
            crux = cruxes[0] if agreed and cruxes else " / ".join(dict.fromkeys(cruxes))

            def side(a):
                r = resps.get(f"{a}__{qid}")
                val = r["move"]["gated"] if r else p[a][qid]
                why = (r or {}).get("data", {}).get("reason") or position(takes[a], qid).get("reason", "")
                return f"{val}% — {debate.anonymise(why)}"
            side_a, side_b = side(hi), side(lo)
            ev = [position(takes[hi], qid).get("evidence") or [], position(takes[lo], qid).get("evidence") or []]
        else:
            rec = c["rec"]
            crux = rec["anon"].get("crux", {}).get("claim", "")
            vals = [p[x][qid] for x in p if qid in p[x]]
            side_a = f"{rec['data'].get('probability')}% — {rec['anon'].get('case', '')[:600]}"
            side_b = f"median {statistics.median(vals):.0f}% — the consensus view: {rec['anon'].get('consensus_view', '')}"
            ev = [rec["data"].get("evidence") or [], []]
        prompt = prompts.render("crux_check", qid=qid, question=debate.anonymise(q["text"]), crux=debate.anonymise(crux),
                                side_a=side_a, side_b=side_b, data_block=c["entry"].get("referee_block", ""),
                                excerpts=debate.excerpts(ev, self.locator()))
        try:
            data, meta = self.call("cruxcheck", qid, "analyst", prompt, schema="crux_check", agent="referee")
        except BudgetSkip:
            return {"ran": False, "reason": "call ceiling reached", "question_id": qid}
        except (llm.LLMError, PhaseFailed) as e:
            self.log(f"  crux check FAILED ({str(e)[:200]})")
            return {"ran": False, "reason": f"failed: {str(e)[:200]}", "question_id": qid}
        flags = []
        quote = (data.get("quote") or "").strip()
        loc = self.locator().locate(quote) if quote else {"status": "empty"}
        if data.get("resolved") != "no" and loc.get("status") != "verified":
            data["resolved"] = "no"
            flags.append("referee quote not found")
        self.log(f"  Crux check [{qid}]: resolved {data.get('resolved')}, leans {data.get('leans')}"
                 + (f" ({', '.join(flags)})" if flags else ""))
        return {"ran": True, "kind": c["kind"], "question_id": qid, "high": c.get("high"), "low": c.get("low"),
                "agent": c.get("agent"), "gap_before": c["gap_before"], "gap_after": c["gap_after"], "data": data,
                "quote_status": loc.get("status"), "flags": flags, "calls": [meta]}

    # ── positions (§9, §10) ────────────────────────────────
    def ph_positions(self, triage: dict, takes: dict, pairing: dict, chs: dict, resps: dict, cc: dict):
        self.log("PHASE 6: Final positions, scores, evidence (no LLM)...")
        qs = triage.get("questions") or []
        take_p = {a: debate.take_values(t) for a, t in takes.items()}
        resp_list = list(resps.values())
        gated: dict[str, dict] = {}
        for r in resp_list:
            gated.setdefault(r["agent"], {})[r["question_id"]] = r["move"]["gated"]
        finals = debate.final_positions(take_p, gated)
        gap_min = int(pairing.get("gap_min") or debate.GAP_MIN_DEFAULT)
        debates = []
        for x in pairing.get("pairs") or []:
            hi, lo, qid = x["high"], x["low"], x["question_id"]
            ch_by = {"high": chs.get(f"{hi}__{lo}__{qid}"), "low": chs.get(f"{lo}__{hi}__{qid}")}
            resp_of = {"high": resps.get(f"{hi}__{qid}"), "low": resps.get(f"{lo}__{qid}")}
            crux = cc.get("data") if cc.get("ran") and cc.get("kind") == "pair" and cc.get("question_id") == qid else None
            sc = debate.score_debate(x, ch_by, resp_of, crux, gap_min)
            self.check_artifact("debate_score", sc, f"debate {qid}")
            debates.append(sc)
            self.log(f"  [{qid}] {hi} vs {lo}: {sc['effect']}; live split {sc['live_split']}, useful {sc['useful']}")
        debated = {d["question_id"] for d in debates}
        qstats = []
        for q in qs:
            qid = q["id"]
            fin = {a: v[qid] for a, v in finals.items() if qid in v}
            qstats.append({"id": qid, "ledger_id": q.get("ledger_id", ""), "text": q["text"], "weight": int(q.get("weight", 1)),
                           "debated": qid in debated,
                           "take_stats": debate.question_stats([v[qid] for v in take_p.values() if qid in v]),
                           "final_stats": debate.question_stats(list(fin.values())), "finals": fin,
                           "kind": q.get("kind", ""), "resolves_on": q.get("resolves_on", ""),
                           "settles_with": q.get("settles_with", ""), "lenses": q.get("lenses", [])})
        # evidence verification, all phases (run.json cites, agent scores)
        items: dict[str, list[dict]] = {a: [] for a in takes}

        def add(agent, where, evs):
            for e in self.ev_checked(evs):
                if e["status"] != "empty":
                    items.setdefault(agent, []).append({"where": where, **e})

        for a, t in takes.items():
            for pp in t.get("positions") or []:
                add(a, f"take:{pp.get('question_id')}", pp.get("evidence"))
            add(a, "take:claims", [{"section": c.get("section"), "quote": c.get("quote")} for c in t.get("claims") or []])
        for rec in chs.values():
            add(rec["challenger"], f"{rec['type']}:{rec['question_id']}", rec["data"].get("evidence"))
        for r in resp_list:
            add(r["agent"], f"response:{r['question_id']}", r["data"].get("new_evidence"))

        def summary(lst):
            c = {"verified": 0, "partial": 0, "unverified": 0}
            for i in lst:
                if i["status"] in c:
                    c[i["status"]] += 1
            tot = sum(c.values())
            return {**c, "total": tot, "rate": round((c["verified"] + c["partial"]) / tot, 3) if tot else None,
                    "data": sum(1 for i in lst if i["status"] in ("verified", "partial") and i["cls"] == "data")}
        per_agent = {a: summary(v) for a, v in items.items()}
        overall = summary([i for v in items.values() for i in v])
        debate_items = [i for v in items.values() for i in v if not i["where"].startswith("take")]
        texts = {a: "\n".join([t.get("take", ""), t.get("summary", "")] + take_quotes(t)) for a, t in takes.items()}
        overlap = evidence.citation_overlap(texts)
        nums = {a: {f"{x['scaled']:.4g}" for x in evidence.numbers(tx) if x["value"] >= 100 or x["pct"]
                    or x["scaled"] != x["value"]} for a, tx in texts.items()}
        flags = [f for dd in debates for f in dd["flags"]]
        for rec in chs.values():
            if rec.get("type") == "redteam":
                flags += [{"agent": rec["agent"], "flag": f, "where": "challenge"} for f in rec["checks"]["flags"]]
        scores = {}
        for a in sorted(takes):
            sc = debate.agent_run_score(a, self.run_id, self.day, take_p, debates, items.get(a, []), nums, flags,
                                        takes[a].get("novel", ""))
            self.check_artifact("agent_run_score", sc, f"agent score {a}")
            scores[a] = sc
        appended = 0
        for a, sc in scores.items():
            appended += self.append_jsonl(self.scores_dir / f"{a}.jsonl", [sc], lambda l: (l["run_id"], l["agent"]))
        moves = {a: debate.legacy_moves(a, resp_list) for a in takes}
        cwe = [dd["closure_without_evidence"] for dd in debates]
        soft = sum(1 for r in resp_list if "soft move" in r["move"].get("flags", []))
        summ = {"closure_without_evidence": {"sum": sum(cwe), "median": statistics.median(cwe) if cwe else None},
                "soft_moves": {"count": soft, "responses": len(resp_list)},
                "live_splits": sum(1 for dd in debates if dd["live_split"]),
                "useful_debates": sum(1 for dd in debates if dd["useful"]),
                "debate_evidence_rate": summary(debate_items)["rate"]}
        self.log(f"  Evidence quotes: {overall['verified']} verified, {overall['partial']} partial, "
                 f"{overall['unverified']} unverified of {overall['total']} ({overall['data']} data)")
        self.log(f"  Debates: {len(debates)}, live splits {summ['live_splits']}, useful {summ['useful_debates']}; "
                 f"soft moves {soft}/{len(resp_list)}; citation overlap {overlap.get('mean_jaccard')}; "
                 f"agent score lines appended {appended}")
        for q in qstats:
            fs = q["final_stats"]
            if fs.get("n"):
                self.log(f"    [{q['id']}] median {fs['median']:.0f}% range {fs['min']:.0f}-{fs['max']:.0f} "
                         f"minority {fs['minority_count']}{' (debated)' if q['debated'] else ''}")
        return {"take_p": take_p, "final": finals, "questions": qstats, "debates": debates, "agent_scores": scores,
                "evidence": {"overall": overall, "per_agent": per_agent, "items": items}, "moves": moves,
                "citation_overlap": overlap, "summary": summ}

    # ── split sheet, lens notes, ledger (§11, §2.6) ────────
    def ph_split(self, triage: dict, takes: dict, pairing: dict, chs: dict, resps: dict, cc: dict, pos: dict):
        self.log("PHASE 6.2: Split sheet and lens notes (no LLM)...")
        qs = triage.get("questions") or []
        challenges_by = {(r["challenger"], r["question_id"]): r for r in chs.values() if r.get("type") == "pair"}
        responses_by = {(r["agent"], r["question_id"]): r for r in resps.values()}
        red = next((r for r in chs.values() if r.get("type") == "redteam"), None)
        red_in = ({"question_id": red["question_id"], "data": {**red["anon"], "evidence": red["data"].get("evidence", [])}}
                  if red else None)
        crux = {"question_id": cc["question_id"], "data": cc["data"]} if cc.get("ran") else None
        ledger = self.read_ledger()
        day_type = pairing.get("day_type", "no_questions")
        sheet = debate.split_sheet(self.day, self.run_id, day_type, qs, pos["take_p"], pos["final"], pos["debates"],
                                   challenges_by, responses_by, takes, self.locator(),
                                   int(pairing.get("gap_min") or debate.GAP_MIN_DEFAULT), red_team=red_in,
                                   crux_check=crux, ledger=ledger)
        self.check_artifact("split_sheet", sheet, "split sheet")
        rendered = debate.render_split_sheet(sheet)
        notes = debate.lens_notes(takes, [a for a in self.active if a in takes], self.locator())
        (self.dir / "07_split_sheet.md").write_text(rendered, encoding="utf-8")
        (self.dir / "07_lens_notes.md").write_text(notes, encoding="utf-8")
        self.save("split_sheet", sheet)
        # question ledger: one line per question, idempotent on (type, ledger_id)
        types = {b["question_id"]: b["type"] for b in sheet["blocks"]}
        qstats = {q["id"]: q for q in pos["questions"]}
        lines = []
        for q in qs:
            st = qstats.get(q["id"], {})
            line = {"type": "question", "ledger_id": q.get("ledger_id") or f"{self.run_id}-{q['id']}",
                    "run_id": self.run_id, "day": self.day,
                    "question": {k: q.get(k, [] if k == "lenses" else (1 if k == "weight" else ""))
                                 for k in schemas.QUESTION["properties"]},
                    "finals": st.get("finals", {}), "take_values": {a: v[q["id"]] for a, v in pos["take_p"].items() if q["id"] in v},
                    "final_stats": st.get("final_stats") or debate.question_stats([]),
                    "debated": bool(st.get("debated")), "split_type": types.get(q["id"], "none")}
            self.check_artifact("ledger_line", line, f"ledger line {line['ledger_id']}")
            lines.append(line)
        n = self.append_jsonl(self.ledger_path, lines, lambda l: (l.get("type"), l.get("ledger_id")))
        self.log(f"  Split sheet: {len(sheet['blocks'])} block(s) ({day_type}), {sheet['bytes']} B; lens notes "
                 f"{len(notes.encode('utf-8'))} B; ledger +{n} line(s) in {self.ledger_path}")
        return {"blocks": len(sheet["blocks"]), "bytes": sheet["bytes"], "lens_notes_bytes": len(notes.encode("utf-8")),
                "ledger_appended": n, "day_type": day_type}

    # ── memory (§14.1 adapters) ────────────────────────────
    def ph_memory(self, triage: dict, takes: dict, resps: dict, pos: dict):
        self.log("PHASE 6.5: Agent memory and state written from the typed outputs (no LLM)...")
        qtext = {q["id"]: q["text"] for q in triage.get("questions", [])}
        resp_list = list(resps.values())
        out = {}
        for a, t in takes.items():
            r = debate.legacy_response(a, resp_list)
            mv = pos["moves"].get(a, [])
            m = agentmem.update_memory(self.mem_dir / f"{a}.md", a, self.day, t, r, mv, qtext)
            s = agentmem.update_state(self.state_dir / f"{agentmem.state_name(a)}_state.md", a, self.day, t, r, mv,
                                      {q: float(v) for q, v in pos["final"].get(a, {}).items()})
            out[a] = {"memory_lines": m["lines"], "state_lines": s["lines"]}
        self.log(f"  Memory and state updated for {len(out)} agents ({self.state_root})")
        return out

    # ── the full record (archive, RUBRIC; not sent to the synthesizer) ──
    def build_record(self, triage, takes, pairing, chs, resps, cc, pos) -> str:
        env = f"ENVIRONMENT: {triage.get('environment', 'QUIET')} WEIGHT: {', '.join(triage.get('weight_agents', []))}"
        r = [f"# DEBATE RECORD -- {self.day} ({self.run_id})", "", "## ENVIRONMENT CLASSIFICATION", env,
             f"Depth: {triage.get('depth', 'normal')}; day type: {pairing.get('day_type', '')}; gap_min {pairing.get('gap_min')}",
             "", "## QUESTIONS OF THE DAY (final probabilities)"]
        for q in pos.get("questions", []):
            fs = q["final_stats"]
            if fs.get("n"):
                r.append(f"- [{q['id']}] {q['text']} — median {fs['median']:.0f}%, range {fs['min']:.0f}-{fs['max']:.0f}%, "
                         f"{fs['minority_count']} of {fs['n']} on the other side of 50"
                         + (f"; resolves {q['resolves_on']}" if q.get("resolves_on") else "")
                         + ("; debated" if q.get("debated") else ""))
        r += ["", "## TAKES"]
        for a in sorted(takes):
            t = takes[a]
            r.append(f"### {a.upper()}")
            r.append(f"Summary: {t.get('summary', '')}")
            for pp in t.get("positions") or []:
                r.append(f"- [{pp.get('question_id')}] {pp.get('probability')}% — {pp.get('reason', '')}")
            r += [t.get("take", ""), ""]
        r.append("## DEBATES")
        for dd in pos.get("debates", []):
            qid, hi, lo = dd["question_id"], dd["high"], dd["low"]
            r.append(f"### [{qid}] {hi.upper()} (high) vs {lo.upper()} (low): {dd['effect']}; live split {dd['live_split']}")
            for c, t in ((hi, lo), (lo, hi)):
                ch = chs.get(f"{c}__{t}__{qid}")
                if not ch:
                    r.append(f"--- {c.upper()} challenge: none ---")
                    continue
                dc = ch["data"]
                r += [f"--- {c.upper()} challenges {t.upper()} ---", f"Steelman: {dc.get('steelman', '')}",
                      f"Crux: {(dc.get('crux') or {}).get('claim', '')} ({(dc.get('crux') or {}).get('type', '')}; "
                      f"observable: {(dc.get('crux') or {}).get('observable', '')})",
                      f"Rebuttal: {dc.get('rebuttal', '')}",
                      f"Would change my mind: {(dc.get('would_change_my_mind') or {}).get('observable', '')}"
                      + (f" — flags: {', '.join(ch['checks']['flags'])}" if ch["checks"]["flags"] else "")]
            for side in (hi, lo):
                rs = resps.get(f"{side}__{qid}")
                if rs:
                    mv = rs["move"]
                    r.append(f"Response {side.upper()} ({rs['data'].get('verdict')}): {mv['take']}% -> requested "
                             f"{mv['requested']} -> gated {mv['gated']}% [{mv['evidence_source']}] {rs['data'].get('reason', '')}"
                             + (f" — flags: {', '.join(mv['flags'])}" if mv["flags"] else ""))
            r.append("")
        red = next((x for x in chs.values() if x.get("type") == "redteam"), None)
        if red:
            dr = red["data"]
            r += ["## RED TEAM", f"{red['agent'].upper()} on [{red['question_id']}] ({dr.get('probability')}%): "
                  f"{dr.get('consensus_view', '')}", dr.get("case", ""),
                  f"Crux: {(dr.get('crux') or {}).get('claim', '')}", ""]
        if cc.get("ran"):
            dc = cc["data"]
            r += ["## CRUX CHECK", f"[{cc['question_id']}] resolved {dc.get('resolved')}, leans {dc.get('leans')}: "
                  f"{dc.get('what_the_data_says', '')} (\"{dc.get('quote', '')}\")", ""]
        r += ["## SPLIT SHEET", read(self.dir / "07_split_sheet.md")]
        sc = self.scorecard()
        if sc:
            r += ["", "## PREDICTION SCORECARD (recent predictions; the legacy April-June list is left out)", sc]
        return "\n".join(r) + "\n"

    # ── synthesis (§11.4, §15.1) ───────────────────────────
    def raw_sections(self) -> dict[str, str]:
        """The synthesizer's raw blocks, from the run folder's 00_raw_data.md only (a replay reads the day
        it replays; nothing under data-sources/)."""
        raw = read(self.dir / "00_raw_data.md")
        return {
            "AI_RAW": "\n".join(x for x in (block_text(raw, "# AI & Tools Intelligence", 9000),
                                            section_text(raw, "## AI & TECH NEWS", 5000),
                                            section_text(raw, "## AI NEWSLETTER SOURCES", 6000),
                                            block_text(raw, "# Changelogs Intelligence", 5000)) if x),
            "EDU_RAW": section_text(raw, "## AI EDUCATION & WORKFORCE", 6000),
            "KR_RAW": "\n".join(x for x in (section_text(raw, "## KOREA — AI", 6000),
                                            section_text(raw, "## KOREA — CRYPTO & MARKETS", 5000),
                                            block_text(raw, "# ZDNet Korea Intelligence", 7000)) if x),
            "FUND_RAW": "\n".join(x for x in (block_text(raw, "# Fundraising Intelligence", 7000),
                                              section_text(raw, "## RECENT FUNDRAISING ROUNDS", 2500)) if x),
        }

    def questions_line(self, pos: dict, triage: dict) -> str:
        qs = sorted(pos.get("questions") or [], key=lambda q: (-int(q.get("weight", 1)), q["id"]))
        for q in qs:
            fs = q.get("final_stats") or {}
            if fs.get("n"):
                return f"the top question, \"{debate.anonymise(q['text'])}\", has a median of {fs['median']:.0f}% across {fs['n']} lenses"
        return "no questions of the day today"

    def ph_synthesis(self, triage, takes, pairing, chs, resps, cc, pos):
        self.log("PHASE 7: Synthesis (synth tier; split sheet + lens notes)...")
        record = self.build_record(triage, takes, pairing, chs, resps, cc, pos)
        (self.dir / "07_full_record.md").write_text(record, encoding="utf-8")
        raw = self.raw_sections()
        (self.dir / "07_raw_sections.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        social = read(self.dir / "01_social.md")
        sc = self.scorecard()
        env = f"ENVIRONMENT: {triage.get('environment', 'QUIET')} WEIGHT: {', '.join(triage.get('weight_agents', []))}"
        split_part = prompts.render("brief_split", split_sheet=read(self.dir / "07_split_sheet.md"),
                                    lens_notes=read(self.dir / "07_lens_notes.md"),
                                    questions_line=self.questions_line(pos, triage))
        raws = f"""--- RAW: AI & TOOLS ---
{raw['AI_RAW']}
--- RAW: AI EDUCATION & WORKFORCE ---
{raw['EDU_RAW']}
--- RAW: KOREA ---
{raw['KR_RAW']}
--- RAW: FUNDRAISING ---
{raw['FUND_RAW']}
--- RAW: SOCIAL (for MARKET MOOD) ---
{social}
--- RAW: SCORECARD (for SCORECARD) ---
{sc or 'No scorecard today.'}
--- END RAW ---"""
        draft_prompt = f"""Produce the RECON Daily Brief. 1400-2000 words. This gets read over morning coffee on a phone; it is longer than a typical brief because it doubles as the reader's AI newsletter, fundraising radar, and Korea desk.

Write like a smart colleague explaining what happened overnight — not like an academic paper. Use plain language. No markdown tables. No corporate jargon. Be conversational but precise. Every bullet in the newsletter sections cites its source name or link from the raw data.

{env}

CRITICAL: Never name an agent or describe the process (no debate, challenge, concession, agents, votes). Disagreement may be shown only in WHERE THE VIEWS SPLIT and only with the count phrases given. The reader should have no idea this was produced by agents. Present conclusions as direct analysis.

Start with the line '# RECON DAILY BRIEF'. Use EXACTLY this format — 11 sections, each a '### ' heading, in this order:
- WHAT HAPPENED (5-7 SHORT sentences, one per line. World events first, then markets, then crypto, then AI.)
- WHAT IT MEANS (3-4 key insights presented as direct analysis, from the lens notes. Say 'the data suggests...' not 'agents agreed...')
- MARKET MOOD (2-3 actual quotes from the SOCIAL raw section below, each with its @handle or r/subreddit and its URL. Quote real posts, not thread titles. If social data is thin today, say so in one clause.)
- WHERE THE VIEWS SPLIT (Follow the rules and the split sheet below exactly: up to three blocks, each with the question, the count phrase exactly as given, the base case, the minority view, what it turns on, when we will know.)
- AI NEWSLETTER (Two parts. First 'New developments:' 6-10 bullets on model, tool, pricing, research, and agent-tooling changes from the AI raw data; each bullet: what changed, then one clause on why it matters for someone building AI workflows. Then 'Trending:' 4-6 bullets on the GitHub repos and Hacker News threads gaining attention in AI, each with the repo or thread name, one line on what it does, and its link. Skip anything without a source.)
- FUNDRAISING (5-10 rounds from the fundraising raw data: company, amount, round, lead investor, sector. Crypto/web3 rounds first, then AI, then Korea if any. Close with one sentence on the pattern.)
- KOREA (4-8 bullets from the Korea raw data: Korean AI adoption and products, regulation, 가상자산 market and policy, prediction markets. Write in English; keep Korean company and product names in Korean in parentheses the first time.)
- AI EDUCATION (3-6 bullets on what is relevant to teaching practical AI workflows to office workers, students, and founders: learner-facing tools, corporate training moves, policy, competitor programs. End with one line starting 'Curriculum idea:'.)
- RISKS (Top 2-3. Plain language. How likely, how bad.)
- WHAT TO WATCH (3-5 concrete things with specific dates. Take the dated "settles on" items from the split sheet first.)
- SCORECARD (Score the predictions in the SCORECARD raw section below: RIGHT, WRONG, or PENDING with expiry date, using today's data. No hedging. Only predictions listed there.)

Each fact appears once: do not repeat the same number or development in several sections.

HALLUCINATION CHECK:
- Only use numbers from TODAY's raw data sections, the split sheet and the lens notes. Verify against the intelligence package.
- If a claim comes from Reddit/X, attribute it with the source.
- If a number doesn't trace to any data source, mark [unverified] or drop it.

{split_part}

{raws}"""
        persona = str(RECON_HOME / "personas" / "synthesizer.md")
        draft, m1 = self.call("synthesis", "draft", "synth", draft_prompt, agent="synthesizer", persona_path=persona,
                              optional=False)
        (self.dir / "07_brief_draft.md").write_text(draft, encoding="utf-8")
        self.log(f"  Draft brief: {len(draft.split())} words")
        sections = ", ".join(BRIEF_SECTIONS)
        filter_prompt = f"""Review this draft brief against the raw data. You are a checker, not a second author: you remove or mark, you do not add. Two jobs:

JOB 1 — HALLUCINATION FILTER:
Cross-reference every specific number, statistic, and claim in the brief against the raw data below. If a number appears in the brief but NOT in the source data, either:
- Mark it [unverified] if it came from analysis (plausible but not from data)
- Remove it entirely if it looks fabricated
Do NOT remove numbers that ARE in the source data. Do not remove newsletter bullets that cite a source present in the raw data.
SCORECARD lines are checked against the SCORECARD raw section: a prediction listed there is not unverified.
MARKET MOOD quotes are checked against the SOCIAL raw section.
WHERE THE VIEWS SPLIT: keep every count phrase ("N of M lenses ...") exactly as written; they come from the split sheet below.
Do NOT add facts, quotes, dates or items that are not already in the draft.

JOB 2 — TONE CHECK:
- Does it read like a human wrote it? If a prose section sounds robotic or academic, tighten it conversationally.
- Keep the format: sections that are bullet lists in the draft (AI NEWSLETTER, FUNDRAISING, KOREA, AI EDUCATION, WHAT TO WATCH, SCORECARD and any other) stay bullet lists, bullet for bullet. Never turn bullets into prose.
- Cut filler and redundancy, but don't over-compress. 1400-2000 words is the target.
- Keep ALL 11 sections in this order: {sections}. Never drop a section; if it has no material, keep the heading with one line saying so.
- Never name an agent or describe the process (no debate, challenge, concession, agents, votes).

CRITICAL: Your response must start with '# RECON DAILY BRIEF' — no preamble, no reasoning, no commentary before or after. Output ONLY the brief itself.

DRAFT BRIEF:
{draft}

SPLIT SHEET (the source of WHERE THE VIEWS SPLIT):
{read(self.dir / '07_split_sheet.md')}

RAW DATA (for cross-referencing numbers; every package section, each trimmed):
{read(self.dir / '01_filtered.md', 100000)}

{raws.replace('--- RAW: SOCIAL (for MARKET MOOD) ---', '--- RAW: SOCIAL ---').replace('--- RAW: SCORECARD (for SCORECARD) ---', '--- RAW: SCORECARD ---')}"""
        try:
            brief, m2 = self.call("synthesis", "filter", "synth", filter_prompt, agent="synthesizer", persona_path=persona,
                                  optional=False)
        except (llm.LLMError, PhaseFailed) as e:
            self.log(f"WARNING: filter call failed ({str(e)[:150]})")
            brief = ""

        def is_brief(t: str) -> bool:
            return bool(re.search(r"^# RECON DAILY BRIEF", t[:400], re.M))
        used = "filter"
        if not is_brief(brief):
            self.log(f"WARNING: filter pass returned no brief ({brief[:120]!r}); using the draft")
            if is_brief(draft):
                brief, used = draft, "draft"
            else:
                raise PhaseFailed("neither the draft nor the filter produced a brief")
        (self.dir / "07_daily_brief.md").write_text(brief, encoding="utf-8")
        self.log(f"  FINAL BRIEF: {len(brief.split())} words")
        return {"used": used, "draft_words": len(draft.split()), "final_words": len(brief.split())}

    # ── checks (§11.5) ─────────────────────────────────────
    def ph_checks(self, takes: dict):
        self.log("PHASE 8: Checks before delivery (no LLM)...")
        brief = read(self.dir / "07_daily_brief.md")
        heads = [re.sub(r"\*", "", re.sub(r"^#+\s*", "", l)).strip().upper()
                 for l in brief.splitlines() if re.match(r"^#{1,4} ", l)]
        missing, last = [], -1
        for s in BRIEF_SECTIONS:
            idx = next((i for i, h in enumerate(heads) if s in h), None)
            if idx is None:
                missing.append(s)
            elif idx < last:
                missing.append(f"{s} (out of order)")
            else:
                last = idx
        if missing:
            self.log(f"  WARNING: sections missing or out of order: {', '.join(missing)}")
        else:
            self.log("  Sections: all 11 present, in order")
        raw = {}
        try:
            raw = json.loads(read(self.dir / "07_raw_sections.json") or "{}")
        except json.JSONDecodeError:
            pass
        src = {"package": read(self.dir / "00_data_package.md"), "raw": read(self.dir / "00_raw_data.md"),
               "social": read(self.dir / "01_social.md"), "scorecard": read(self.dir / "00_scorecard.md"),
               **{k.lower(): v for k, v in raw.items()}}
        dbt = {"record": read(self.dir / "07_full_record.md"), "split": read(self.dir / "07_split_sheet.md"),
               "lens": read(self.dir / "07_lens_notes.md")}
        claims = evidence.brief_claims(brief, src, dbt, {a: t.get("take", "") for a, t in takes.items()})
        found = sum(1 for c in claims if c["found_in_source"])
        derived = sum(1 for c in claims if not c["found_in_source"] and c["action"].startswith("note"))
        urls = evidence.url_check(brief, "\n".join(src.values()))
        rep = evidence.repeated_numbers(brief)
        words = len(brief.split())
        sheet = self.load("split_sheet") if self.art("split_sheet").exists() else None
        bc = debate.brief_checks(brief, sheet)
        self.log(f"  Claims check: {len(claims)} numeric claims, {found} found in source, {derived} derived in the debate, "
                 f"{len(claims) - found - derived} not found")
        self.log(f"  URLs: {urls['urls']} in the brief, {len(urls['missing'])} not in the raw data; "
                 f"numbers in 3+ sections: {len(rep)}; words {words}")
        self.log(f"  Agent names: {len(bc['agent_names'])}; count mismatches: {len(bc['count_mismatch'])}; "
                 f"block issues: {len(bc['blocks'])}; process words: {len(bc['process_words'])}")
        for k in ("agent_names", "count_mismatch", "blocks", "process_words"):
            for v in bc[k][:5]:
                self.log(f"    {k}: {v}")
        return {"sections_ok": not missing, "missing": missing, "words": words, "claims": claims,
                "claims_found": found, "claims_derived": derived, "urls": urls, "repeated_numbers": rep, **bc}

    def telegram(self, text: str) -> bool:
        tok, chat = os.environ.get("RECON_TELEGRAM_TOKEN"), os.environ.get("RECON_TELEGRAM_CHAT_ID")
        if not tok or not chat:
            self.log("Telegram not configured")
            return False
        import urllib.request
        t = re.sub(r"^#{1,3}\s+(.+)$", r"<b>\1</b>", text.strip(), flags=re.M)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"^\|[-| ]+\|$", "", t, flags=re.M)
        t = re.sub(r"^---+$", "", t, flags=re.M)
        t = re.sub(r"\n{3,}", "\n\n", t)
        chunks, cur = [], ""
        for line in t.split("\n"):
            if cur and line.startswith("<b>") and len(cur) > 3000:
                chunks.append(cur.strip())
                cur = ""
            cur += line + "\n"
            if len(cur) > 3800:
                chunks.append(cur.strip())
                cur = ""
        if cur.strip():
            chunks.append(cur.strip())
        sent = 0
        for ch in chunks:
            for body in ({"chat_id": chat, "text": ch, "parse_mode": "HTML"}, {"chat_id": chat, "text": ch}):
                req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage",
                                             data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
                try:
                    urllib.request.urlopen(req, timeout=10)
                    sent += 1
                    break
                except Exception:  # noqa: BLE001 - retry without HTML, then give up on this chunk
                    continue
            time.sleep(1)
        return sent == len(chunks)

    def ph_deliver(self):
        brief = read(self.dir / "07_daily_brief.md")
        self.log("DELIVERING...")
        tg = False
        if self.args.no_telegram or self.dry:
            self.log(f"Telegram suppressed: {brief[:60]!r}...")
        else:
            tg = self.telegram(brief)
            self.log(f"  Telegram: {'sent' if tg else 'FAILED'}")
        archived = False
        if not self.tagged and not self.dry and not self.replay:
            self.log("Archiving daily data...")
            ad = RECON_HOME / "archive" / self.day
            ad.mkdir(parents=True, exist_ok=True)
            for src in ARCHIVE_SOURCES:
                f = self.data_dir / src / "latest.md"
                if f.exists():
                    shutil.copy2(f, ad / f"{src}.md")
            shutil.copy2(self.dir / "07_daily_brief.md", ad / "brief.md")
            shutil.copy2(self.dir / "07_full_record.md", ad / "debate.md")
            for extra in ("07_split_sheet.md", "07_lens_notes.md"):
                if (self.dir / extra).exists():
                    shutil.copy2(self.dir / extra, ad / extra[3:])
            with (RECON_HOME / "archive" / "INDEX.md").open("a", encoding="utf-8") as f:
                f.write(f"- [{self.day}](archive/{self.day}/brief.md) — {brief[:150].replace(chr(10), ' ')}\n")
            self.log(f"  Archived to {ad}")
            self.log("Indexing into knowledge database...")
            self.sub([sys.executable, str(RECON_HOME / "scripts" / "knowledge_db.py"), "index", str(self.dir)], 300)
            archived = True
        else:
            self.log("  Archive and knowledge DB skipped (validation, replay or dry run)")
        return {"telegram": tg, "archived": archived}

    # ── run.json (schema_version 2, §12.3, §14.1) ──────────
    def ph_record(self, triage, takes, pairing, chs, resps, cc, pos, split, mem, syn, checks, deliver, pkg):
        from recon import export  # the source records and package sections stay in one place
        all_calls = self.calls()
        calls = [c for c in all_calls if c.get("ok")]
        failed = [c for c in all_calls if not c.get("ok")]
        by_tier: dict[str, dict] = {}
        by_phase: dict[str, dict] = {}
        for c in calls:
            for key, bucket in ((c["tier"], by_tier), (c["phase"], by_phase)):
                t = bucket.setdefault(key, {"calls": 0, "in_tok": 0, "cached_tok": 0, "out_tok": 0, "seconds": 0})
                t["calls"] += 1
                t["in_tok"] += c.get("in_tok", 0)
                t["cached_tok"] += c.get("cached_tok", 0)
                t["out_tok"] += c.get("out_tok", 0)
                t["seconds"] += c.get("seconds", 0)
        finished = datetime.now()
        wall = int((finished - self.first_start()).total_seconds())
        pkg_text = read(self.dir / "00_data_package.md")
        sections = export.package_sections(pkg_text)
        fin_iso = finished.strftime("%Y-%m-%dT%H:%M:%S+09:00")
        srcs, scope = export.source_records_dir(self.dir, fin_iso)
        ev_items = pos["evidence"]["items"]
        sec_bodies = []
        for part in re.split(r"^# SECTION \d+: ", pkg_text, flags=re.M)[1:]:
            title, _, body = part.partition("\n")
            sec_bodies.append((title.strip().lower().replace(" & ", "_").replace(" ", "_")[:40], evidence.norm(body)))

        def cite_section(quote: str, given: str) -> str:
            q = evidence.norm(quote).strip(" .\"'")
            for name, body in sec_bodies:
                if q and q[:120] in body:
                    return name
            g = set(re.findall(r"[a-z]+", (given or "").lower())) - {"section", "and"}
            best = max(sec_bodies, key=lambda nb: len(g & set(re.findall(r"[a-z]+", nb[0]))), default=None)
            return best[0] if best and g & set(re.findall(r"[a-z]+", best[0])) else (given or "")

        made: dict[str, list] = {}
        got: dict[str, list] = {}
        edges = []
        for rec in chs.values():
            d = rec["data"]
            if rec.get("type") == "pair":
                c, t = rec["challenger"], rec["target"]
                edges.append({"from": c, "to": t, "type": "pair", "question_id": rec["question_id"]})
                item = {"type": "pair", "text": d.get("rebuttal", ""), "crux": (d.get("crux") or {}).get("claim", ""),
                        "question_id": rec["question_id"]}
                made.setdefault(c, []).append({"to": t, **item})
                got.setdefault(t, []).append({"from": c, **item})
            elif rec.get("type") == "redteam":
                a, qid = rec["agent"], rec["question_id"]
                vals = {x: v[qid] for x, v in pos["take_p"].items() if qid in v and x != a}
                med = rec.get("median", 50)
                near = sorted(vals, key=lambda x: (abs(vals[x] - med), x))[0] if vals else ""
                item = {"type": "redteam", "text": d.get("case", ""), "crux": (d.get("crux") or {}).get("claim", ""),
                        "question_id": qid}
                made.setdefault(a, []).append({"to": near, **item})
                if near:
                    got.setdefault(near, []).append({"from": a, **item})
                    edges.append({"from": a, "to": near, "type": "redteam", "question_id": qid})
        resp_list = list(resps.values())
        agents = []
        for a in sorted(takes):
            t = takes[a]
            lr = debate.legacy_response(a, resp_list)
            p = t.get("prediction") or {}
            ph = hashlib.sha1(read(RECON_HOME / "personas" / f"{a}.md").encode("utf-8")).hexdigest()[:8]
            my_calls = [{"phase": c["phase"].rstrip("s") if c["phase"] in ("takes", "challenges", "responses") else c["phase"],
                         "tier": c["tier"], "model": c["model"], "in_tok": c["in_tok"], "cached_tok": c.get("cached_tok", 0),
                         "out_tok": c["out_tok"], "seconds": c["seconds"]} for c in calls if c.get("agent") == a]
            positions = []
            for x in t.get("positions", []):
                q = x.get("question_id")
                positions.append({"question_id": q, "take_probability": pos["take_p"].get(a, {}).get(q),
                                  "final_probability": pos["final"].get(a, {}).get(q), "reason": x.get("reason", ""),
                                  "evidence": x.get("evidence", [])})
            mem_text = read(self.mem_dir / f"{a}.md")
            i = mem_text.find(f"### Last updated: {self.day}")
            trec = json.loads(read(self.pdir / "takes" / f"{a}.json") or "{}")
            agents.append({
                "name": a, "desk": "shared", "persona_hash": ph,
                "fed": {"package_sections": [s["name"] for s in sections], "raw_sections": [],
                        "memory_lines": mem.get(a, {}).get("memory_lines", 0), "state_lines": mem.get(a, {}).get("state_lines", 0),
                        "bytes": len(self.take_prompt(a, triage).encode("utf-8")),
                        **(trec.get("fed") or {})},
                "cites": [{"section": cite_section(e["quote"], e["section"]), "quote": e["quote"],
                           "verified": e["status"] in ("verified", "partial"), "status": e["status"], "cls": e.get("cls", ""),
                           "where": e["where"]} for e in ev_items.get(a, [])],
                "take": t.get("take", ""), "summary": t.get("summary", ""),
                "response": (lr or {}).get("text") or None, "verdict": (lr or {}).get("verdict"),
                "deep_dive": None, "vote": None,
                "predictions": [{"text": p.get("text", ""), "probability": p.get("probability"),
                                 "resolves_on": p.get("resolves_on") or None, "metric": p.get("metric", "")}] if p.get("text") else [],
                "positions": positions, "claims": t.get("claims", []), "novel": t.get("novel", ""),
                "watching": t.get("watching", []), "moves": pos["moves"].get(a, []),
                "evidence_check": pos["evidence"]["per_agent"].get(a), "score": pos["agent_scores"].get(a),
                "memory_update": re.sub(r"\s+", " ", mem_text[i:]).strip()[:600] if i >= 0 else None,
                "calls": my_calls, "challenges_made": made.get(a, []), "challenges_received": got.get(a, []),
            })
        synth_calls = [{"phase": c["key"], "tier": c["tier"], "model": c["model"], "in_tok": c["in_tok"],
                        "out_tok": c["out_tok"], "seconds": c["seconds"]} for c in calls if c["phase"] == "synthesis"]
        tri_calls = [{"phase": "triage", "tier": c["tier"], "model": c["model"], "in_tok": c["in_tok"], "out_tok": c["out_tok"],
                      "seconds": c["seconds"]} for c in calls if c["phase"] == "triage"]
        final = read(self.dir / "07_daily_brief.md")
        clean = checks.get("sections_ok") and not checks.get("agent_names") and not checks.get("count_mismatch")
        status = "ok" if final and clean else ("partial" if final else "failed")
        red = next((r for r in chs.values() if r.get("type") == "redteam"), None)
        sheet = self.load("split_sheet") if self.art("split_sheet").exists() else None
        tri_art = self.load("triage") if self.art("triage").exists() else {}
        run = {
            "date": self.run_id, "day": self.day, "mode": "replay" if self.replay else "daily", "status": status,
            "pipeline": "orchestrator", "schema_version": 2, "provider": llm.provider_name(), "slim_codex": llm.slim(),
            "started": self.first_start().strftime("%Y-%m-%dT%H:%M:%S+09:00"), "finished": fin_iso, "wall_seconds": wall,
            "checkpoint": None, "state_dir": str(self.state_root),
            "triage": {"environment": triage.get("environment"), "weights": [{"desk": w, "weight": 1.0} for w in triage.get("weight_agents", [])],
                       "active_agents": {"shared": sorted(takes)}, "depth": triage.get("depth", "normal"),
                       "reason": triage.get("reason", ""), "calls": tri_calls, "gate": tri_art.get("gate")},
            "questions": [{**q, "final_stats": q["final_stats"]} for q in pos.get("questions", [])],
            "sources": srcs, "sources_scope": scope, "sources_from": "orchestrator",
            "package": {"compact_bytes": len(pkg_text.encode("utf-8")), "sections": sections,
                        "view_bytes": pkg.get("view_bytes"), "report": pkg.get("report")},
            "agents": agents, "edges": edges,
            "pairing": {k: v for k, v in pairing.items() if k != "positions_evidence"},
            "debates": pos.get("debates", []),
            "red_team": ({"agent": red["agent"], "question_id": red["question_id"], "probability": red["data"].get("probability"),
                          "consensus_view": red["data"].get("consensus_view", ""), "case": red["data"].get("case", ""),
                          "crux": red["data"].get("crux"), "flags": red["checks"]["flags"]} if red else None),
            "crux_check": ({k: v for k, v in cc.items() if k != "calls"} if cc else None),
            "split_sheet": sheet, "agent_scores": pos.get("agent_scores", {}), "debate_summary": pos.get("summary"),
            "deep_dive": None,
            "evidence": {**pos["evidence"]["overall"], "citation_overlap": pos.get("citation_overlap")},
            "synthesis": {"draft": read(self.dir / "07_brief_draft.md"), "claims": checks.get("claims", []), "final": final,
                          "calls": synth_calls, "used": syn.get("used"),
                          "checks": {k: v for k, v in checks.items() if k != "claims"}},
            "usage": {"calls": len(calls), "calls_logged": len(all_calls), "failed_calls": len(failed), "by_tier": by_tier,
                      "by_phase": by_phase, "in_tok": sum(c.get("in_tok", 0) for c in calls),
                      "cached_tok": sum(c.get("cached_tok", 0) for c in calls),
                      "out_tok": sum(c.get("out_tok", 0) for c in calls), "wall_seconds": wall,
                      "budget": self.budget, "ceiling": self.ceiling, "budget_skips": self.budget_skips()},
            "delivery": {"telegram": bool(deliver.get("telegram")), "brief_words": len(final.split()),
                         "archived": deliver.get("archived", False)},
            "phases": self.phase_times,
        }
        self.check_artifact("run", run, "run.json")
        (self.dir / "run.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        self.log(f"  run.json written ({len(calls)} calls, {run['usage']['in_tok']:,} in / {run['usage']['out_tok']:,} "
                 f"out tokens; {len(run['usage']['budget_skips'])} budget skips)")
        return {"calls": len(calls), "status": status}

    def first_start(self) -> datetime:
        p = self.pdir / "started.txt"
        if not p.exists():
            p.write_text(self.started.isoformat(), encoding="utf-8")
        return datetime.fromisoformat(p.read_text(encoding="utf-8").strip())

    # ── driver ─────────────────────────────────────────────
    def phase(self, name: str, fn):
        if self.have(name):
            data = self.load(name)
            self.log(f"[{name}] reused from {self.art(name).name}")
            return data
        t = time.time()
        self.phase_times.append({"name": name, "at": now()})
        data = fn()
        self.save(name, data)
        self.phase_times[-1]["seconds"] = round(time.time() - t, 1)
        return data

    def stop_here(self, name: str) -> bool:
        if self.stop_after == name:
            self.log(f"RECON_STOP_AFTER={name}: stopping after this phase ({self.ncalls} calls)")
            return True
        return False

    def go(self) -> int:
        self.prepare()
        self.first_start()
        self.log("==============================================")
        self.log(f"RECON ORCHESTRATOR -- {self.run_id} (day {self.day}, provider {llm.provider_name()}, "
                 f"slim codex {llm.slim()}, parallel {PARALLEL}, budget {self.budget}/{self.ceiling}, "
                 f"state {self.state_root})")
        self.log("==============================================")
        self.phase("score", self.ph_score)
        self.phase("collect", self.ph_collect)
        self.phase("context", self.ph_context)
        pkg = self.phase("package", self.ph_package)
        if self.stop_here("package"):
            return 0
        tri = self.phase("triage", self.ph_triage)["data"]
        if self.stop_here("triage"):
            return 0
        self.phase("takes", lambda: self.ph_takes(tri))
        takes = {k: v["data"] for k, v in self.load_items("takes").items()}
        if self.stop_here("takes"):
            return 0
        pairing = self.phase("pairing", lambda: self.ph_pairing(tri, takes))
        if self.stop_here("pairing"):
            return 0
        self.phase("challenges", lambda: self.ph_challenges(tri, takes, pairing))
        chs = self.load_items("challenges")
        if self.stop_here("challenges"):
            return 0
        self.phase("responses", lambda: self.ph_responses(tri, takes, pairing, chs))
        resps = self.load_items("responses")
        if self.stop_here("responses"):
            return 0
        cc = self.phase("cruxcheck", lambda: self.ph_cruxcheck(tri, takes, pairing, chs, resps))
        pos = self.phase("positions", lambda: self.ph_positions(tri, takes, pairing, chs, resps, cc))
        split = self.phase("split", lambda: self.ph_split(tri, takes, pairing, chs, resps, cc, pos))
        if self.stop_here("split"):
            return 0
        mem = self.phase("memory", lambda: self.ph_memory(tri, takes, resps, pos))
        syn = self.phase("synthesis", lambda: self.ph_synthesis(tri, takes, pairing, chs, resps, cc, pos))
        checks = self.phase("checks", lambda: self.ph_checks(takes))
        dlv = self.phase("deliver", self.ph_deliver)
        self.forced.add("record")
        self.phase("record", lambda: self.ph_record(tri, takes, pairing, chs, resps, cc, pos, split, mem, syn, checks,
                                                    dlv, pkg))
        secs = int(time.time() - self.t0)
        self.log("==============================================")
        self.log(f"RECON COMPLETE in {secs // 60}m {secs % 60}s ({self.ncalls} calls)")
        self.log(f"Brief: {self.dir / '07_daily_brief.md'}")
        self.log("==============================================")
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RECON v2 orchestrator (Phase C)")
    ap.add_argument("--skip-collect", action="store_true")
    ap.add_argument("--skip-score", action="store_true")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--run-id")
    ap.add_argument("--as-of", help="the run's day, YYYY-MM-DD (default: today)")
    ap.add_argument("--package-from", help="copy 00_*.md inputs from this run folder (implies --skip-collect)")
    ap.add_argument("--replay", help="replay a run folder: --package-from DIR, cold state, no telegram/archive")
    ap.add_argument("--state-dir", help="memory, state, ledger and agent scores (default config/ for the daily run)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--from-phase", choices=PHASES + list(PHASE_ALIASES))
    g.add_argument("--resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    alias_note = None
    if args.from_phase in PHASE_ALIASES:
        alias_note = f"--from-phase {args.from_phase} → {PHASE_ALIASES[args.from_phase]}"
        args.from_phase = PHASE_ALIASES[args.from_phase]
    if args.as_of:
        try:
            date.fromisoformat(args.as_of)
        except ValueError:
            ap.error(f"--as-of {args.as_of!r} is not YYYY-MM-DD")
    if args.dry_run:
        os.environ["RECON_LLM_PROVIDER"] = "dry-run"
        args.skip_collect = args.skip_score = args.no_telegram = True
    if args.replay:
        args.package_from = args.replay
        args.skip_score = args.no_telegram = True
    if args.package_from:
        args.skip_collect = True
    os.environ.setdefault("RECON_CODEX_SLIM", "1")
    run = Run(args)
    if alias_note:
        run.log(alias_note)
    try:
        return run.go()
    except Exception as e:  # noqa: BLE001 - one line in the day log, then a non-zero exit for cron_run.sh
        run.log(f"FATAL: {type(e).__name__}: {e}")
        for line in traceback.format_exc().splitlines()[-6:]:
            run.log("  " + line)
        return 1


if __name__ == "__main__":
    sys.exit(main())
