#!/usr/bin/env python3
"""RECON v2 orchestrator (Phase B): the daily brief as typed phases with resume.

Same stages as scripts/run_recon.sh, with these changes (docs/v2/06-improvement-plan.md §7 row B):
  - triage runs FIRST (1 FAST call, schema): environment, depth, weights and 3-5 QUESTIONS OF THE
    DAY. It replaces the environment call and the wildcard-pick call.
  - takes are typed (schema): the persona's take text plus a probability per question with
    evidence quotes, claims, one prediction, what to watch.
  - challenges and responses are typed; the response carries the agent's final probabilities and
    its vote, so there are no separate vote calls. The wildcard pair and the deep dive are chosen
    by the widest probability gap (no decision call, no free-text parsing).
  - memory and state are written by code from the typed outputs (no LLM calls).
  - evidence quotes and the brief's numbers are checked against the package by code.
  - every phase writes an artifact under briefs/<run>/phases/; --from-phase / --resume reuse them.
  - run.json is written from typed data (the RUBRIC recon page's fields plus questions/positions).
  - up to 5 calls in parallel; llm.py backs off only on rate limits. Codex runs "slim" (no built-in
    agent instructions or tools): ~9 K fewer input tokens per call.

Usage
  python3 recon/orchestrator.py [--skip-collect] [--no-telegram] [--run-id ID]
                                [--from-phase NAME | --resume] [--package-from DIR] [--dry-run]
  --dry-run   dry-run provider, no Telegram/archive/knowledge DB, agent memory and state written
              into the run folder (config/ is not touched); implies --skip-collect --skip-score.
  --run-id    folder name under briefs/ (default: today's date). Validation runs use e.g.
              2026-10-04-b1 so they never overwrite the daily run; such runs skip archive/ and the
              knowledge DB.
Exit code 0 when a brief was written, 1 otherwise.
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
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from recon import agentmem, evidence, llm, schemas  # noqa: E402

RECON_HOME = llm.RECON_HOME
AGENTS = schemas.AGENTS
TENSIONS = [("trader", "narrator"), ("narrator", "trader"), ("builder", "policy_analyst"),
            ("policy_analyst", "builder"), ("analyst", "skeptic"), ("skeptic", "analyst"),
            ("macro_strategist", "user_agent"), ("user_agent", "macro_strategist"),
            ("ai_engineer", "builder"), ("builder", "ai_engineer")]
PHASES = ["score", "collect", "context", "package", "triage", "takes", "challenges", "responses",
          "deepdive", "positions", "memory", "synthesis", "checks", "deliver", "record"]
ITEM_DIRS = {"takes", "challenges", "responses"}
BRIEF_SECTIONS = ["WHAT HAPPENED", "WHAT IT MEANS", "MARKET MOOD", "THE CONTRARIAN CASE", "AI NEWSLETTER",
                  "FUNDRAISING", "KOREA", "AI EDUCATION", "RISKS", "WHAT TO WATCH", "SCORECARD"]
DEEP_DIVE_GAP = 30          # points between two lenses' final probabilities that trigger a deep dive
PARALLEL = int(os.environ.get("RECON_PARALLEL", "5"))
LENS = {"trader": "markets, flows, positioning", "narrator": "narratives and social attention",
        "builder": "products and protocols", "analyst": "the sector model", "skeptic": "risks and weak claims",
        "policy_analyst": "regulation and policy", "user_agent": "users and adoption",
        "macro_strategist": "macro and geopolitics", "ai_engineer": "AI models and tools"}

ROLE = """--- YOUR ROLE ---
You are playing a specific role. Stay in character. Answer from the material in this prompt only.
{persona}
--- END ROLE ---"""

CITATION_RULE = """CITATION RULE (READ FIRST):
1. Only cite numbers that appear in TODAY'S INTELLIGENCE PACKAGE above.
2. Do NOT cite numbers from your memory or prior runs as current fact. Your memory is context, not today's data.
3. For claims from social media posts or Reddit threads, prefix with 'reportedly' or 'per social media'.
4. If a specific number isn't in the data package, say 'reportedly' or omit it. Never invent statistics."""

DEBATE_FORMAT = ("This debate format replaces your persona's usual output format: no headings, no portfolio or "
                 "allocation lines, no ROADMAP or BUILD NOW tags, no content angles.")


class PhaseFailed(RuntimeError):
    pass


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


def section(path: Path, heading: str, maxbytes: int) -> str:
    """Port of run_recon.sh section(): from HEADING to the next '## ' heading, capped."""
    out, n, on = [], 0, False
    for i, line in enumerate(read(path).splitlines()):
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


class Run:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.day = datetime.now().strftime("%Y-%m-%d")
        self.dry = args.dry_run
        self.run_id = args.run_id or (f"{self.day}-dry" if self.dry else self.day)
        self.tagged = self.run_id != self.day
        self.dir = RECON_HOME / "briefs" / self.run_id
        self.pdir = self.dir / "phases"
        self.log_file = RECON_HOME / "logs" / f"{self.run_id}.log"
        self.started = datetime.now()
        self.t0 = time.time()
        self.lock = threading.Lock()
        self.forced: set[str] = set()
        self.phase_times: list[dict] = []
        self.data_dir = RECON_HOME / "data-sources"
        if self.dry:
            self.mem_dir = self.dir / "state" / "agent_memory"
            self.state_dir = self.dir / "state" / "agent_state"
        else:
            self.mem_dir = RECON_HOME / "config" / "agent_memory"
            self.state_dir = RECON_HOME / "config" / "agent_state"
        self.schema_paths: dict[str, str] = {}
        self._shared: str | None = None

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

    def record_call(self, meta: dict) -> None:
        with self.lock:
            self.pdir.mkdir(parents=True, exist_ok=True)
            with (self.pdir / "calls.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(meta, ensure_ascii=False) + "\n")

    def calls(self) -> list[dict]:
        p = self.pdir / "calls.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

    # ── setup ──────────────────────────────────────────────
    def prepare(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        a = self.args
        if a.from_phase:
            i = PHASES.index(a.from_phase)
            self.forced = set(PHASES[i:])
            for name in self.forced:
                if self.art(name).exists():
                    self.art(name).unlink()
                if name in ITEM_DIRS:
                    shutil.rmtree(self.pdir / name, ignore_errors=True)
            keep = [c for c in self.calls() if c.get("phase") not in self.forced]
            if (self.pdir / "calls.jsonl").exists():
                (self.pdir / "calls.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in keep),
                                                      encoding="utf-8")
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
        self.schema_paths = schemas.write_all(self.pdir / "schemas")
        if self.dry:
            for src, dst in ((RECON_HOME / "config" / "agent_memory", self.mem_dir),
                             (RECON_HOME / "config" / "agent_state", self.state_dir)):
                if not dst.exists() and src.exists():
                    shutil.copytree(src, dst)

    # ── LLM ────────────────────────────────────────────────
    def call(self, phase: str, key: str, tier: str, prompt: str, schema: str | None = None,
             agent: str | None = None, persona_path: str | None = None):
        """One model call. With a schema the reply is parsed and validated; one re-ask on a bad reply."""
        sp = self.schema_paths.get(schema) if schema else None
        attempts = 0
        p = prompt
        while True:
            attempts += 1
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
                    "out_tok": u.get("output_tokens", 0), "seconds": res["seconds"], "prompt_bytes": res["prompt_bytes"],
                    "ok": True, "at": now()}
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
                p = prompt + f"\n\nYour previous reply could not be used ({e}). Reply again with ONE JSON object that matches the schema."

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

    def shared(self) -> str:
        """The block every triage and take prompt starts with, byte-identical, so the provider can
        reuse its cache across the ten calls."""
        if self._shared is None:
            view = read(self.dir / "01_filtered.md", 80000)
            sector = read(RECON_HOME / "config" / "sector_context.md", 8000)
            hist = read(self.dir / "00_historical_context.md", 3000)
            sc = self.scorecard()
            self._shared = f"""TODAY'S INTELLIGENCE PACKAGE ({self.day}). Every section is included, each trimmed to fit:
- SECTION 0 (CROSS-SOURCE): the same story seen in several sources.
- SECTION 1 (SENTIMENT): BettaFish sentiment analysis across social media and news.
- SECTION 2 (GEOPOLITICAL): World Monitor intelligence from 79 global sources.
- SECTION 3 (ON-CHAIN): market, DeFi, prediction-market and stablecoin data.
- SECTION 4 (NEWS): crypto, AI, AI education and Korea headlines.
- SECTION 5 (SOCIAL): Reddit hot posts and the most-engaged X posts of the last 72 h.
- SECTION 6 (AI & TOOLS): GitHub trending AI repos and Hacker News.
- SECTION 7 (FUNDRAISING): crypto, AI and Korea rounds from news.

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

    # ── phases ─────────────────────────────────────────────
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
        elif a.skip_collect:
            self.log("PHASE 0: Skipping data collection (--skip-collect)")
            if not pkg.exists():
                self.log("  Assembling from existing data-sources...")
                parts = [f"# RECON INTELLIGENCE PACKAGE -- {self.day}", f"## Assembled: {now()}", ""]
                for src in ("bettafish", "worldmonitor", "onchain", "news", "reddit", "twitter", "ai_tools", "fundraising"):
                    t = read(self.data_dir / src / "latest.md")
                    if t:
                        parts += ["---", "", t, ""]
                pkg.write_text("\n".join(parts), encoding="utf-8")
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
        return {"package_bytes": size}

    def ph_context(self):
        self.log("PHASE 0.1: Loading historical context...")
        hist = ""
        if (RECON_HOME / "config" / "knowledge.db").exists():
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
            self.log(f"WARNING: agent view builder failed (exit {p.returncode}); using the first 80 KB of the package")
            view.write_bytes((self.dir / "00_data_package.md").read_bytes()[:80000])
        if not (self.dir / "01_social.md").exists():
            (self.dir / "01_social.md").write_text("No social extract today.\n", encoding="utf-8")
        rep = {}
        try:
            rep = json.loads(read(self.dir / "01_package_report.json") or "{}")
        except json.JSONDecodeError:
            pass
        return {"rc": p.returncode, "view_bytes": view.stat().st_size, "report": rep}

    def ph_triage(self):
        self.log("PHASE 1: Triage and questions of the day (fast tier, schema)...")
        roster = "\n".join(f"- {a}: {LENS[a]}" for a in AGENTS)
        prompt = self.shared() + f"""
TASK: TRIAGE. You set up today's analysis for nine analyst lenses:
{roster}

1. environment: what dominates today's data (MARKET-DRIVEN: price moves, volume, flows; NARRATIVE-DRIVEN:
   social discourse and sentiment shifts; PRODUCT-DRIVEN: launches, upgrades, competitive moves;
   RISK-DRIVEN: regulatory actions, hacks, depegs, systemic risk; QUIET: incremental). depth: quiet,
   normal or risk. reason: one sentence.
2. weight_agents: the 2-4 lenses whose view matters most today.
3. questions: 3 to 5 QUESTIONS OF THE DAY, ids q1, q2, ... Each one:
   - is a yes/no question about something in today's package, answerable with a probability;
   - is one where informed lenses could reasonably disagree (not one everybody answers 5% or 95%);
   - resolves on a date 1-30 days out where possible (resolves_on, YYYY-MM-DD) and names the data
     series or event that settles it (settles_with), e.g. a price or TVL level on a date, a vote, a launch;
   - lists the 2-5 lenses best placed to answer it.
   Cover different ground: at least one markets/crypto question, one macro or policy question and
   one AI or product question.
Reply with one JSON object matching the schema.
"""
        try:
            data, meta = self.call("triage", "triage", "fast", prompt, schema="triage", agent="synthesizer")
        except (llm.LLMError, PhaseFailed) as e:
            # The debate still runs without questions: no positions, no wildcard or deep dive.
            self.log(f"WARNING: triage failed ({str(e)[:200]}); continuing without questions of the day")
            data, meta = {"environment": "QUIET", "depth": "normal", "reason": "triage failed", "weight_agents": [],
                          "questions": []}, None
        qs = []
        for i, q in enumerate((data.get("questions") or [])[:5], 1):
            if (q.get("text") or "").strip():
                q["id"] = f"q{i}"
                q["lenses"] = [a for a in q.get("lenses", []) if a in AGENTS]
                qs.append(q)
        data["questions"] = qs
        data["weight_agents"] = [a for a in data.get("weight_agents", []) if a in AGENTS]
        data["active_agents"] = list(AGENTS)  # all nine through Phase B (§8 decision 5)
        self.log(f"  Environment: ENVIRONMENT: {data['environment']} WEIGHT: {', '.join(data['weight_agents'])}")
        self.log(f"  Depth: {data['depth']}; {len(qs)} questions")
        for q in qs:
            self.log(f"    [{q['id']}] {q['text'][:150]}")
        return {"data": data, "calls": [meta] if meta else []}

    def take_prompt(self, agent: str, triage: dict) -> str:
        extra = []
        mem = self.mem_dir / f"{agent}.md"
        if mem.exists():
            body = "\n".join(read(mem).splitlines()[2:])
            extra.append("YOUR RUNNING MEMORY (items you're tracking, prior predictions, recurring themes):\n"
                         + body.encode("utf-8")[:6000].decode("utf-8", "ignore"))
        if agent == "analyst" and (RECON_HOME / "config" / "analyst_model.md").exists():
            extra.append("YOUR WORKING MODEL (config/analyst_model.md):\n" + read(RECON_HOME / "config" / "analyst_model.md", 4000))
        st = self.state_dir / f"{agentmem.state_name(agent)}_state.md"
        if st.exists():
            tail = "\n".join(read(st).splitlines()[-60:])
            extra.append("YOUR STATE FROM PREVIOUS SESSIONS (newest entries):\n" + tail.encode("utf-8")[-5000:].decode("utf-8", "ignore"))
        ctx = "\n\n".join(extra)
        return self.shared() + "\n" + self.questions_block(triage) + f"""
{ROLE.format(persona=self.persona(agent))}

{ctx}

TASK: YOUR TAKE ON TODAY'S PACKAGE.
{CITATION_RULE}

If historical context is provided, reference yesterday's brief: note what changed, what predictions held,
what was wrong. Continuity matters. Cover the most significant development in YOUR domain today. The
ecosystem includes world events, macro, crypto/BTC/ETH, DeFi, stablecoins, AI/ML, regulation, prediction
markets, fundraising and infrastructure. Analyze what matters most TODAY; don't default to one sector.

Reply with one JSON object matching the schema:
- take: your analysis in your persona's own output format, 200-400 words of markdown. Be specific:
  cite data points from the package, name sources, give numbers.
- summary: your main call in one or two sentences (at most 50 words).
- positions: one entry for EVERY question of the day above (question_id q1, q2, ...): your probability
  0-100 that the answer is yes, a reason of at most 25 words, and 1-2 evidence items, each a quote copied
  character for character from the package (at most 200 characters) with its section name. A program
  checks every quote against the package; a quote that is not there counts against you.
- claims: up to 4 key factual claims behind your take, each with a verbatim quote, section and confidence.
- prediction: one testable prediction with a probability, a resolution date (YYYY-MM-DD) and the metric.
- novel: one thing the other analysts will probably miss.
- watching: 2-5 short items to track next session.
"""

    def ph_takes(self, triage: dict):
        self.log(f"PHASE 3: Independent takes (parallel {PARALLEL}, schema)...")
        d = self.pdir / "takes"
        d.mkdir(parents=True, exist_ok=True)

        def job(agent):
            f = d / f"{agent}.json"
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8"))
            prompt = self.take_prompt(agent, triage)
            data, meta = self.call("takes", agent, "analyst", prompt, schema="take", agent=agent)
            calls = [meta]
            t = data.get("take", "")
            if len(t) < 300 or re.search(r"I can't|I cannot|as an AI|I'm sorry", t[:400]):
                self.log(f"  {agent}: take too short or a refusal; asking once more")
                data, meta = self.call("takes", agent, "analyst", prompt + "\n\nStay in character and produce the full analysis.",
                                       schema="take", agent=agent)
                calls.append(meta)
            rec = {"agent": agent, "data": data, "calls": calls}
            f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            (self.dir / f"03_take_{agent}.md").write_text(data.get("take", ""), encoding="utf-8")
            return rec

        res = self.parallel([(a, (lambda a=a: job(a))) for a in triage.get("active_agents", AGENTS)])
        takes = {}
        for a, r in res.items():
            if isinstance(r, Exception):
                self.log(f"  {a}: take FAILED ({str(r)[:200]})")
            else:
                takes[a] = r["data"]
                self.log(f"  {a}: take {len(r['data'].get('take', '').split())} words, {len(r['data'].get('positions', []))} positions")
        if len(takes) < 3:
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

    @staticmethod
    def pos_map(positions: list[dict]) -> dict[str, float]:
        out = {}
        for p in positions or []:
            try:
                v = float(p.get("probability"))
            except (TypeError, ValueError):
                continue
            out[p.get("question_id", "")] = max(0.0, min(100.0, v))
        if out and all(v <= 1.0 for v in out.values()) and any(v > 0 for v in out.values()):
            out = {q: v * 100 for q, v in out.items()}  # answered as fractions
        return out

    def positions_text(self, take: dict) -> str:
        lines = []
        for p in take.get("positions", []):
            ev = "; ".join(f"\"{e.get('quote', '')[:160]}\"" for e in p.get("evidence", [])[:2])
            lines.append(f"[{p.get('question_id')}] {p.get('probability', 0):.0f}% — {p.get('reason', '')}"
                         + (f" (evidence: {ev})" if ev else ""))
        return "\n".join(lines) or "none"

    def wildcard(self, takes: dict) -> tuple[str, str] | None:
        """The non-tension pair with the widest probability gap on any question (replaces the LLM pick)."""
        fixed = {frozenset(p) for p in TENSIONS}
        pm = {a: self.pos_map(t.get("positions")) for a, t in takes.items()}
        best = None
        agents = sorted(takes)
        for i, a in enumerate(agents):
            for b in agents[i + 1:]:
                if frozenset((a, b)) in fixed:
                    continue
                for q in set(pm[a]) & set(pm[b]):
                    gap = abs(pm[a][q] - pm[b][q])
                    if best is None or gap > best[0]:
                        best = (gap, a, b, q)
        if not best:
            return None
        gap, a, b, q = best
        vals = [pm[x][q] for x in agents if q in pm[x]]
        med = statistics.median(vals)
        # the agent further from the median challenges the other
        return (a, b) if abs(pm[a][q] - med) >= abs(pm[b][q] - med) else (b, a)

    def ph_challenges(self, triage: dict, takes: dict):
        self.log(f"PHASE 4: Challenges (fixed tensions + widest-gap wildcard, parallel {PARALLEL}, schema)...")
        pairs = [(c, t, "tension") for c, t in TENSIONS if c in takes and t in takes]
        wc = self.wildcard(takes)
        if wc:
            pairs.append((wc[0], wc[1], "wildcard"))
            self.log(f"  Wildcard: {wc[0]} -> {wc[1]} (widest gap outside the fixed pairs)")
        d = self.pdir / "challenges"
        d.mkdir(parents=True, exist_ok=True)
        qb = self.questions_block(triage)

        def job(c, t, kind):
            f = d / f"{c}_vs_{t}.json"
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8"))
            prompt = f"""{ROLE.format(persona=self.persona(c))}

TASK: CHALLENGE {t.upper()}'s analysis. What did they get wrong? Where are the blind spots? Argue about
substance: where their reading of the data or their probability is wrong. Where you agree with them,
say so in one clause and spend your words where you disagree.
{DEBATE_FORMAT}

{qb}
YOUR TAKE:
{takes[c].get('take', '')}
YOUR POSITIONS:
{self.positions_text(takes[c])}

{t.upper()}'S TAKE:
{takes[t].get('take', '')}
{t.upper()}'S POSITIONS:
{self.positions_text(takes[t])}

Reply with one JSON object matching the schema: text (your challenge, at most 220 words), crux (the
single factual or causal claim the disagreement turns on), question_id (the question most at stake, or
empty), evidence (0-3 quotes, verbatim from the package as quoted in the takes above, with section).
"""
            data, meta = self.call("challenges", f"{c}_vs_{t}", "analyst", prompt, schema="challenge", agent=c)
            rec = {"challenger": c, "target": t, "type": kind, "data": data, "calls": [meta]}
            f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            name = f"04c_wildcard_{c}_vs_{t}.md" if kind == "wildcard" else f"04a_{c}_vs_{t}.md"
            (self.dir / name).write_text(data.get("text", "") + f"\n\nCRUX: {data.get('crux', '')}\n", encoding="utf-8")
            return rec

        res = self.parallel([(f"{c}_vs_{t}", (lambda c=c, t=t, k=k: job(c, t, k))) for c, t, k in pairs])
        ok = [k for k, r in res.items() if not isinstance(r, Exception)]
        for k, r in res.items():
            if isinstance(r, Exception):
                self.log(f"  {k}: challenge FAILED ({str(r)[:200]})")
        self.log(f"  All challenges complete ({len(ok)}/{len(pairs)})")
        return {"pairs": [{"challenger": c, "target": t, "type": k} for c, t, k in pairs], "ok": ok}

    def ph_responses(self, triage: dict, takes: dict, challenges: dict):
        self.log(f"PHASE 5: Responses (parallel {PARALLEL}, schema; final positions and votes)...")
        received: dict[str, list[dict]] = {}
        for rec in challenges.values():
            received.setdefault(rec["target"], []).append(rec)
        d = self.pdir / "responses"
        d.mkdir(parents=True, exist_ok=True)
        qb = self.questions_block(triage)

        def job(agent):
            f = d / f"{agent}.json"
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8"))
            ch = []
            for rec in received[agent]:
                c = rec["challenger"]
                ev = "; ".join(f"[{e.get('section', '')}] \"{e.get('quote', '')}\"" for e in rec["data"].get("evidence", []))
                ch.append(f"--- Challenge from {c.upper()} ({rec['type']}) ---\n{rec['data'].get('text', '')}\n"
                          f"CRUX: {rec['data'].get('crux', '')}\nEVIDENCE: {ev or 'none'}\n"
                          f"{c.upper()}'S POSITIONS:\n{self.positions_text(takes.get(c, {}))}")
            prompt = f"""{ROLE.format(persona=self.persona(agent))}

TASK: RESPOND to the challenges below, then give your vote for today.
For each point: hold, narrow, or concede, on the evidence. Do not concede to be polite and do not dig in
to be stubborn. Change a probability only for a reason you can point to; moving one by more than 10
points needs evidence you did not already cite (a verbatim quote from the package or from the
challenger's evidence).
{DEBATE_FORMAT}

{qb}
YOUR TAKE:
{takes[agent].get('take', '')}
YOUR POSITIONS:
{self.positions_text(takes[agent])}

CHALLENGES:
{chr(10).join(ch)}

Reply with one JSON object matching the schema: text (at most 220 words), verdict (overall: hold, narrow
or concede), final_positions (one per question of the day: your probability now and why), new_evidence
(quotes you did not cite before, verbatim, with section), vote (act_on, market_wrong_about, unseen_risk:
1-3 sentences each).
"""
            data, meta = self.call("responses", agent, "analyst", prompt, schema="response", agent=agent)
            rec = {"agent": agent, "data": data, "calls": [meta]}
            f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            v = data.get("vote", {})
            (self.dir / f"05_resp_{agent}.md").write_text(
                f"{data.get('text', '')}\n\nVERDICT: {data.get('verdict', '')}\n", encoding="utf-8")
            (self.dir / f"06_vote_{agent}.md").write_text(
                f"1. ACT ON: {v.get('act_on', '')}\n2. MARKET IS WRONG ABOUT: {v.get('market_wrong_about', '')}\n"
                f"3. UNDISCUSSED RISK: {v.get('unseen_risk', '')}\n", encoding="utf-8")
            return rec

        res = self.parallel([(a, (lambda a=a: job(a))) for a in sorted(received) if a in takes])
        ok = [k for k, r in res.items() if not isinstance(r, Exception)]
        for k, r in res.items():
            if isinstance(r, Exception):
                self.log(f"  {k}: response FAILED ({str(r)[:200]})")
            else:
                self.log(f"  {k}: {r['data'].get('verdict')}")
        self.log(f"  All responses complete ({len(ok)}/{len(res)})")
        return {"ok": ok}

    def finals(self, takes: dict, responses: dict) -> dict[str, dict[str, float]]:
        out = {}
        for a, t in takes.items():
            fp = self.pos_map(t.get("positions"))
            r = responses.get(a)
            if r:
                fp.update(self.pos_map(r["data"].get("final_positions")))
            out[a] = fp
        return out

    def ph_deepdive(self, triage: dict, takes: dict, responses: dict):
        self.log("PHASE 5.5: Deep dive on the widest remaining split (programmatic pick)...")
        fin = self.finals(takes, responses)
        best = None
        for q in [q["id"] for q in triage.get("questions", [])]:
            vals = [(fin[a][q], a) for a in fin if q in fin[a]]
            if len(vals) < 2:
                continue
            lo, hi = min(vals), max(vals)
            if best is None or hi[0] - lo[0] > best[0]:
                best = (hi[0] - lo[0], q, hi[1], lo[1], hi[0], lo[0])
        if not best or best[0] < DEEP_DIVE_GAP:
            self.log(f"  No deep dive: widest gap {best[0]:.0f} points" if best else "  No deep dive: no positions")
            return {"triggered": False, "gap": best[0] if best else None}
        gap, q, a_hi, a_lo, v_hi, v_lo = best
        qtext = next(x["text"] for x in triage["questions"] if x["id"] == q)
        self.log(f"  DEEP DIVE: {a_hi} vs {a_lo} on [{q}] ({v_hi:.0f}% vs {v_lo:.0f}%): {qtext[:120]}")

        def side(me, other, mine, theirs):
            r_me = responses.get(me, {}).get("data", {})
            r_ot = responses.get(other, {}).get("data", {})
            prompt = f"""{ROLE.format(persona=self.persona(me))}

TASK: DEEP DIVE. The widest remaining split today is on [{q}] {qtext}
You are at {mine:.0f}%. {other.upper()} is at {theirs:.0f}%. This is the one disagreement that could change
today's brief. Be precise: state your final position and probability, and the specific observable (with a
date) that would change your mind. {DEBATE_FORMAT}

YOUR TAKE:
{takes[me].get('take', '')}
YOUR POSITIONS:
{self.positions_text(takes[me])}
YOUR RESPONSE TO CHALLENGES:
{r_me.get('text', 'none')}

THE OTHER SIDE ({other.upper()}) TAKE:
{takes[other].get('take', '')}
{other.upper()}'S POSITIONS:
{self.positions_text(takes[other])}
{other.upper()}'S RESPONSE TO CHALLENGES:
{r_ot.get('text', 'none')}

Reply with one JSON object matching the schema.
"""
            data, meta = self.call("deepdive", me, "analyst", prompt, schema="deep_dive", agent=me)
            (self.dir / f"05_5_deepdive_{me}.md").write_text(
                f"{data.get('text', '')}\n\nFINAL: {data.get('final_probability', 0):.0f}%\n"
                f"WOULD CHANGE MY MIND: {data['would_change_my_mind'].get('observable', '')} "
                f"(by {data['would_change_my_mind'].get('by_date', '')})\n", encoding="utf-8")
            return {"agent": me, "data": data, "calls": [meta]}

        res = self.parallel([(a_hi, lambda: side(a_hi, a_lo, v_hi, v_lo)), (a_lo, lambda: side(a_lo, a_hi, v_lo, v_hi))])
        sides = {k: v for k, v in res.items() if not isinstance(v, Exception)}
        for k, v in res.items():
            if isinstance(v, Exception):
                self.log(f"  {k}: deep dive FAILED ({str(v)[:200]})")
        after = [float(s["data"].get("final_probability", 0)) for s in sides.values()]
        gap_after = abs(after[0] - after[1]) if len(after) == 2 else None
        self.log("  Deep dive complete" + (f": gap {gap:.0f} -> {gap_after:.0f} points" if gap_after is not None else ""))
        return {"triggered": True, "question_id": q, "agents": [a_hi, a_lo], "gap_before": gap, "gap_after": gap_after,
                "sides": sides}

    def ph_positions(self, triage: dict, takes: dict, challenges: dict, responses: dict, deep: dict):
        self.log("PHASE 6: Final positions, evidence check, diversity (no LLM)...")
        fin = self.finals(takes, responses)
        if deep.get("triggered"):
            for a, s in deep.get("sides", {}).items():
                fin.setdefault(a, {})[deep["question_id"]] = float(s["data"].get("final_probability", 0))
        start = {a: self.pos_map(t.get("positions")) for a, t in takes.items()}

        def stats(vals: list[float]) -> dict:
            if not vals:
                return {"n": 0}
            med = statistics.median(vals)
            side = (lambda v: v >= 50) if med >= 50 else (lambda v: v < 50)
            return {"n": len(vals), "median": round(med, 1), "mean": round(sum(vals) / len(vals), 1),
                    "min": min(vals), "max": max(vals), "range": max(vals) - min(vals),
                    "minority": sum(1 for v in vals if not side(v))}

        qstats = []
        for q in triage.get("questions", []):
            qid = q["id"]
            qstats.append({**q, "take_stats": stats([start[a][qid] for a in start if qid in start[a]]),
                           "final_stats": stats([fin[a][qid] for a in fin if qid in fin[a]])})

        # evidence verification
        docs = {"package": read(self.dir / "00_data_package.md"), "raw": read(self.dir / "00_raw_data.md"),
                "social": read(self.dir / "01_social.md"), "scorecard": read(self.dir / "00_scorecard.md"),
                "view": read(self.dir / "01_filtered.md")}
        corpus = evidence.Corpus(docs)
        checked: dict[str, list[dict]] = {a: [] for a in takes}

        def check(agent, where, items):
            for e in items or []:
                q = (e.get("quote") or "").strip()
                if not q:
                    continue
                v = corpus.verify_quote(q)
                checked.setdefault(agent, []).append({"where": where, "section": e.get("section", ""), "quote": q[:300], **v})

        for a, t in takes.items():
            for p in t.get("positions", []):
                check(a, f"take:{p.get('question_id')}", p.get("evidence"))
            check(a, "take:claims", [{"section": c.get("section"), "quote": c.get("quote")} for c in t.get("claims", [])])
        for rec in challenges.values():
            check(rec["challenger"], f"challenge:{rec['target']}", rec["data"].get("evidence"))
        for a, r in responses.items():
            check(a, "response", r["data"].get("new_evidence"))
        for a, s in (deep.get("sides") or {}).items():
            check(a, "deepdive", s["data"].get("evidence"))

        def summary(items):
            c = {"verified": 0, "partial": 0, "unverified": 0}
            for i in items:
                if i["status"] in c:
                    c[i["status"]] += 1
            tot = sum(c.values())
            return {**c, "total": tot, "rate": round((c["verified"] + c["partial"]) / tot, 3) if tot else None}

        per_agent = {a: summary(v) for a, v in checked.items()}
        overall = summary([i for v in checked.values() for i in v])

        # moves between take and final, flagged when large and without new verified evidence
        moves: dict[str, list[dict]] = {}
        for a in takes:
            r = responses.get(a, {}).get("data", {})
            reasons = {p.get("question_id"): p.get("reason", "") for p in r.get("final_positions", [])}
            new_ok = sum(1 for i in checked.get(a, []) if i["where"] in ("response", "deepdive") and i["status"] != "unverified")
            for q, v0 in start.get(a, {}).items():
                v1 = fin.get(a, {}).get(q, v0)
                if abs(v1 - v0) >= 1:
                    moves.setdefault(a, []).append({
                        "question_id": q, "from": v0, "to": v1, "delta": v1 - v0, "reason": reasons.get(q, ""),
                        "flag": "update without evidence" if abs(v1 - v0) > 10 and new_ok == 0 else None})
        overlap = evidence.citation_overlap({a: t.get("take", "") for a, t in takes.items()})
        flagged = sum(1 for v in moves.values() for m in v if m["flag"])
        self.log(f"  Evidence quotes: {overall['verified']} verified, {overall['partial']} partial, "
                 f"{overall['unverified']} unverified of {overall['total']}")
        self.log(f"  Moves: {sum(len(v) for v in moves.values())} ({flagged} without new evidence); "
                 f"citation overlap (mean Jaccard) {overlap.get('mean_jaccard')}")
        for q in qstats:
            fs = q["final_stats"]
            if fs.get("n"):
                self.log(f"    [{q['id']}] median {fs['median']:.0f}% range {fs['min']:.0f}-{fs['max']:.0f} minority {fs['minority']}")
        return {"final": fin, "start": start, "questions": qstats, "evidence": {"overall": overall, "per_agent": per_agent,
                "items": checked}, "moves": moves, "citation_overlap": overlap,
                "votes": {a: r["data"].get("vote") for a, r in responses.items()}}

    def ph_memory(self, triage: dict, takes: dict, responses: dict, pos: dict):
        self.log("PHASE 6.5: Agent memory and state written from the typed outputs (no LLM)...")
        qtext = {q["id"]: q["text"] for q in triage.get("questions", [])}
        out = {}
        for a, t in takes.items():
            r = responses.get(a, {}).get("data")
            mv = pos["moves"].get(a, [])
            m = agentmem.update_memory(self.mem_dir / f"{a}.md", a, self.day, t, r, mv, qtext)
            s = agentmem.update_state(self.state_dir / f"{agentmem.state_name(a)}_state.md", a, self.day, t, r, mv,
                                      pos["final"].get(a, {}))
            out[a] = {"memory_lines": m["lines"], "state_lines": s["lines"]}
        self.log(f"  Memory and state updated for {len(out)} agents ({'run folder' if self.dry else 'config/'})")
        return out

    def build_record(self, triage, takes, challenges, responses, deep, pos) -> str:
        env = f"ENVIRONMENT: {triage.get('environment', 'QUIET')} WEIGHT: {', '.join(triage.get('weight_agents', []))}"
        r = [f"# DEBATE RECORD -- {self.day}", "", "## ENVIRONMENT CLASSIFICATION", env, "",
             "## QUESTIONS OF THE DAY (the lenses' final probabilities)"]
        for q in pos.get("questions", []):
            fs = q["final_stats"]
            if fs.get("n"):
                r.append(f"- [{q['id']}] {q['text']} — median {fs['median']:.0f}%, range {fs['min']:.0f}-{fs['max']:.0f}%, "
                         f"{fs['minority']} of {fs['n']} on the other side of 50"
                         + (f"; resolves {q['resolves_on']}" if q.get("resolves_on") else "")
                         + (f"; settles with {q['settles_with']}" if q.get("settles_with") else ""))
        r += ["", "## TAKES"]
        for a in sorted(takes):
            r += [f"### {a.upper()}", takes[a].get("take", ""), ""]
        r += ["## CHALLENGES & RESPONSES"]
        by_target: dict[str, list] = {}
        for rec in challenges.values():
            by_target.setdefault(rec["target"], []).append(rec)
        for a in sorted(by_target):
            r.append(f"### To {a.upper()}:")
            for rec in by_target[a]:
                kind = "Wildcard" if rec["type"] == "wildcard" else "Challenge"
                r += [f"--- {kind} from {rec['challenger'].upper()} ---", rec["data"].get("text", ""),
                      f"Crux: {rec['data'].get('crux', '')}"]
            resp = responses.get(a, {}).get("data", {})
            r += [f"Response ({resp.get('verdict', 'none')}): {resp.get('text', 'none')}", ""]
        if deep.get("triggered"):
            r.append("## DEEP DIVE")
            for a, s in deep.get("sides", {}).items():
                d = s["data"]
                r += [f"### {a.upper()} (deep dive, final {d.get('final_probability', 0):.0f}%):", d.get("text", ""),
                      f"Would change my mind: {d['would_change_my_mind'].get('observable', '')} "
                      f"(by {d['would_change_my_mind'].get('by_date', '')})", ""]
        r.append("## VOTES")
        for a in sorted(responses):
            v = responses[a]["data"].get("vote", {})
            r.append(f"### {a.upper()}: 1. ACT ON: {v.get('act_on', '')} 2. MARKET IS WRONG ABOUT: "
                     f"{v.get('market_wrong_about', '')} 3. UNDISCUSSED RISK: {v.get('unseen_risk', '')}")
        sc = self.scorecard()
        if sc:
            r += ["", "## PREDICTION SCORECARD (recent predictions; the legacy April-June list is left out)", sc]
        return "\n".join(r) + "\n"

    def raw_sections(self) -> dict[str, str]:
        news = self.data_dir / "news" / "latest.md"
        return {
            "AI_RAW": read(self.data_dir / "ai_tools" / "latest.md", 9000) + "\n" + section(news, "## AI & TECH NEWS", 5000)
                      + "\n" + section(news, "## AI NEWSLETTER SOURCES", 6000),
            "EDU_RAW": section(news, "## AI EDUCATION & WORKFORCE", 6000),
            "KR_RAW": section(news, "## KOREA — AI", 6000) + "\n" + section(news, "## KOREA — CRYPTO & MARKETS", 5000),
            "FUND_RAW": read(self.data_dir / "fundraising" / "latest.md", 7000) + "\n"
                        + section(self.data_dir / "onchain" / "latest.md", "## RECENT FUNDRAISING ROUNDS", 2500),
        }

    def ph_synthesis(self, triage, takes, challenges, responses, deep, pos):
        self.log("PHASE 7: Synthesis (synth tier)...")
        record = self.build_record(triage, takes, challenges, responses, deep, pos)
        (self.dir / "07_full_record.md").write_text(record, encoding="utf-8")
        raw = self.raw_sections()
        (self.dir / "07_raw_sections.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        social = read(self.dir / "01_social.md")
        sc = self.scorecard()
        env = f"ENVIRONMENT: {triage.get('environment', 'QUIET')} WEIGHT: {', '.join(triage.get('weight_agents', []))}"
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

CRITICAL: Do NOT mention agent names (Trader, Skeptic, Builder, etc.) in the output. Do NOT reference debates, concessions, challenges, or convergence. The reader should have no idea this was produced by agents. Present conclusions as direct analysis.

Start with the line '# RECON DAILY BRIEF'. Use EXACTLY this format — 11 sections, each a '### ' heading, in this order:
- WHAT HAPPENED (5-7 SHORT sentences, one per line. World events first, then markets, then crypto, then AI.)
- WHAT IT MEANS (3-4 key insights presented as direct analysis. Say 'the data suggests...' not 'agents agreed...')
- MARKET MOOD (2-3 actual quotes from the SOCIAL raw section below, each with its @handle or r/subreddit and its URL. Quote real posts, not thread titles. If social data is thin today, say so in one clause.)
- THE CONTRARIAN CASE (The strongest argument against the consensus. Frame as analysis. The QUESTIONS OF THE DAY in the record show where the views are furthest apart.)
- AI NEWSLETTER (Two parts. First 'New developments:' 6-10 bullets on model, tool, pricing, research, and agent-tooling changes from the AI raw data; each bullet: what changed, then one clause on why it matters for someone building AI workflows. Then 'Trending:' 4-6 bullets on the GitHub repos and Hacker News threads gaining attention in AI, each with the repo or thread name, one line on what it does, and its link. Skip anything without a source.)
- FUNDRAISING (5-10 rounds from the fundraising raw data: company, amount, round, lead investor, sector. Crypto/web3 rounds first, then AI, then Korea if any. Close with one sentence on the pattern.)
- KOREA (4-8 bullets from the Korea raw data: Korean AI adoption and products, regulation, 가상자산 market and policy, prediction markets. Write in English; keep Korean company and product names in Korean in parentheses the first time.)
- AI EDUCATION (3-6 bullets on what is relevant to teaching practical AI workflows to office workers, students, and founders: learner-facing tools, corporate training moves, policy, competitor programs. End with one line starting 'Curriculum idea:'.)
- RISKS (Top 2-3. Plain language. How likely, how bad.)
- WHAT TO WATCH (3-5 concrete things with specific dates. The questions of the day give dates and the data that settles them.)
- SCORECARD (Score the predictions in the SCORECARD raw section below: RIGHT, WRONG, or PENDING with expiry date, using today's data. No hedging. Only predictions listed there.)

Each fact appears once: do not repeat the same number or development in several sections.

HALLUCINATION CHECK:
- Only use numbers from TODAY's raw data sections. Agents sometimes repeat claims from prior runs — verify against the intelligence package.
- If a claim comes from Reddit/X, attribute it with the source.
- If a number doesn't trace to any data source, mark [unverified] or drop it.

{record}

{raws}"""
        persona = str(RECON_HOME / "personas" / "synthesizer.md")
        draft, m1 = self.call("synthesis", "draft", "synth", draft_prompt, agent="synthesizer", persona_path=persona)
        (self.dir / "07_brief_draft.md").write_text(draft, encoding="utf-8")
        self.log(f"  Draft brief: {len(draft.split())} words")
        filter_prompt = f"""Review this draft brief against the raw data. You are a checker, not a second author: you remove or mark, you do not add. Two jobs:

JOB 1 — HALLUCINATION FILTER:
Cross-reference every specific number, statistic, and claim in the brief against the raw data below. If a number appears in the brief but NOT in the source data, either:
- Mark it [unverified] if it came from analysis (plausible but not from data)
- Remove it entirely if it looks fabricated
Do NOT remove numbers that ARE in the source data. Do not remove newsletter bullets that cite a source present in the raw data.
SCORECARD lines are checked against the SCORECARD raw section: a prediction listed there is not unverified.
MARKET MOOD quotes are checked against the SOCIAL raw section.
Do NOT add facts, quotes, dates or items that are not already in the draft.

JOB 2 — TONE CHECK:
- Does it read like a human wrote it? If a prose section sounds robotic or academic, tighten it conversationally.
- Keep the format: sections that are bullet lists in the draft (AI NEWSLETTER, FUNDRAISING, KOREA, AI EDUCATION, WHAT TO WATCH, SCORECARD and any other) stay bullet lists, bullet for bullet. Never turn bullets into prose.
- Cut filler and redundancy, but don't over-compress. 1400-2000 words is the target.
- Keep ALL 11 sections in this order: WHAT HAPPENED, WHAT IT MEANS, MARKET MOOD, THE CONTRARIAN CASE, AI NEWSLETTER, FUNDRAISING, KOREA, AI EDUCATION, RISKS, WHAT TO WATCH, SCORECARD. Never drop a section; if it has no material, keep the heading with one line saying so.

CRITICAL: Your response must start with '# RECON DAILY BRIEF' — no preamble, no reasoning, no commentary before or after. Output ONLY the brief itself.

DRAFT BRIEF:
{draft}

RAW DATA (for cross-referencing numbers; every package section, each trimmed):
{read(self.dir / '01_filtered.md', 80000)}

{raws.replace('--- RAW: SOCIAL (for MARKET MOOD) ---', '--- RAW: SOCIAL ---').replace('--- RAW: SCORECARD (for SCORECARD) ---', '--- RAW: SCORECARD ---')}"""
        try:
            brief, m2 = self.call("synthesis", "filter", "synth", filter_prompt, agent="synthesizer", persona_path=persona)
        except llm.LLMError as e:
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
        debate = {"record": read(self.dir / "07_full_record.md")}
        claims = evidence.brief_claims(brief, src, debate, {a: t.get("take", "") for a, t in takes.items()})
        found = sum(1 for c in claims if c["found_in_source"])
        derived = sum(1 for c in claims if not c["found_in_source"] and c["action"].startswith("note"))
        urls = evidence.url_check(brief, "\n".join(src.values()))
        rep = evidence.repeated_numbers(brief)
        words = len(brief.split())
        self.log(f"  Claims check: {len(claims)} numeric claims, {found} found in source, {derived} derived in the debate, "
                 f"{len(claims) - found - derived} not found")
        self.log(f"  URLs: {urls['urls']} in the brief, {len(urls['missing'])} not in the raw data; "
                 f"numbers in 3+ sections: {len(rep)}; words {words}")
        return {"sections_ok": not missing, "missing": missing, "words": words, "claims": claims,
                "claims_found": found, "claims_derived": derived, "urls": urls, "repeated_numbers": rep}

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
        if not self.tagged and not self.dry:
            self.log("Archiving daily data...")
            ad = RECON_HOME / "archive" / self.day
            ad.mkdir(parents=True, exist_ok=True)
            for src in ("reddit", "twitter", "onchain", "news", "worldmonitor", "bettafish"):
                f = self.data_dir / src / "latest.md"
                if f.exists():
                    shutil.copy2(f, ad / f"{src}.md")
            shutil.copy2(self.dir / "07_daily_brief.md", ad / "brief.md")
            shutil.copy2(self.dir / "07_full_record.md", ad / "debate.md")
            with (RECON_HOME / "archive" / "INDEX.md").open("a", encoding="utf-8") as f:
                f.write(f"- [{self.day}](archive/{self.day}/brief.md) — {brief[:150].replace(chr(10), ' ')}\n")
            self.log(f"  Archived to {ad}")
            self.log("Indexing into knowledge database...")
            self.sub([sys.executable, str(RECON_HOME / "scripts" / "knowledge_db.py"), "index", str(self.dir)], 300)
            archived = True
        else:
            self.log("  Archive and knowledge DB skipped (validation or dry run)")
        return {"telegram": tg, "archived": archived}

    # ── run.json ───────────────────────────────────────────
    def ph_record(self, triage, takes, challenges, responses, deep, pos, mem, syn, checks, deliver, pkg):
        from recon import export  # the source records and package sections stay in one place
        calls = [c for c in self.calls() if c.get("ok")]
        failed = [c for c in self.calls() if not c.get("ok")]
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
        made: dict[str, list] = {}
        got: dict[str, list] = {}
        edges = []
        for rec in challenges.values():
            c, t, d = rec["challenger"], rec["target"], rec["data"]
            edges.append({"from": c, "to": t, "type": rec["type"]})
            item = {"type": rec["type"], "text": d.get("text", ""), "crux": d.get("crux", ""), "question_id": d.get("question_id", "")}
            made.setdefault(c, []).append({"to": t, **item})
            got.setdefault(t, []).append({"from": c, **item})
        if deep.get("triggered") and len(deep.get("agents", [])) == 2:
            edges.append({"from": deep["agents"][0], "to": deep["agents"][1], "type": "deepdive"})
        agents = []
        for a in sorted(takes):
            t = takes[a]
            r = responses.get(a, {}).get("data")
            dd = (deep.get("sides") or {}).get(a, {}).get("data")
            p = t.get("prediction") or {}
            ph = hashlib.sha1(read(RECON_HOME / "personas" / f"{a}.md").encode("utf-8")).hexdigest()[:8]
            my_calls = [{"phase": c["phase"].rstrip("s") if c["phase"] in ("takes", "challenges", "responses") else c["phase"],
                         "tier": c["tier"], "model": c["model"], "in_tok": c["in_tok"], "cached_tok": c.get("cached_tok", 0),
                         "out_tok": c["out_tok"], "seconds": c["seconds"]} for c in calls if c.get("agent") == a]
            positions = []
            for x in t.get("positions", []):
                q = x.get("question_id")
                positions.append({"question_id": q, "take_probability": x.get("probability"),
                                  "final_probability": pos["final"].get(a, {}).get(q), "reason": x.get("reason", ""),
                                  "evidence": x.get("evidence", [])})
            mem_text = read(self.mem_dir / f"{a}.md")
            i = mem_text.find(f"### Last updated: {self.day}")
            agents.append({
                "name": a, "desk": "shared", "persona_hash": ph,
                "fed": {"package_sections": [s["name"] for s in sections], "raw_sections": [],
                        "memory_lines": mem.get(a, {}).get("memory_lines", 0), "state_lines": mem.get(a, {}).get("state_lines", 0),
                        "bytes": len(self.take_prompt(a, triage).encode("utf-8"))},
                "cites": [{"section": e["section"], "quote": e["quote"], "verified": e["status"] in ("verified", "partial"),
                           "status": e["status"], "where": e["where"]} for e in ev_items.get(a, [])],
                "take": t.get("take", ""), "summary": t.get("summary", ""),
                "response": (r or {}).get("text") or None, "verdict": (r or {}).get("verdict"),
                "deep_dive": dd.get("text") if dd else None,
                "vote": (r or {}).get("vote") or None,
                "predictions": [{"text": p.get("text", ""), "probability": p.get("probability"),
                                 "resolves_on": p.get("resolves_on") or None, "metric": p.get("metric", "")}] if p.get("text") else [],
                "positions": positions, "claims": t.get("claims", []), "novel": t.get("novel", ""),
                "watching": t.get("watching", []), "moves": pos["moves"].get(a, []),
                "evidence_check": pos["evidence"]["per_agent"].get(a),
                "memory_update": re.sub(r"\s+", " ", mem_text[i:]).strip()[:600] if i >= 0 else None,
                "calls": my_calls, "challenges_made": made.get(a, []), "challenges_received": got.get(a, []),
            })
        debates = []
        for rec in challenges.values():
            c, t, q = rec["challenger"], rec["target"], rec["data"].get("question_id", "")
            sc, st = pos["start"].get(c, {}), pos["start"].get(t, {})
            fc, ft = pos["final"].get(c, {}), pos["final"].get(t, {})
            debates.append({"challenger": c, "target": t, "type": rec["type"], "question_id": q, "crux": rec["data"].get("crux", ""),
                            "gap_before": abs(sc[q] - st[q]) if q in sc and q in st else None,
                            "gap_after": abs(fc[q] - ft[q]) if q in fc and q in ft else None,
                            "response_verdict": responses.get(t, {}).get("data", {}).get("verdict")})
        synth_calls = [{"phase": c["key"], "tier": c["tier"], "model": c["model"], "in_tok": c["in_tok"],
                        "out_tok": c["out_tok"], "seconds": c["seconds"]} for c in calls if c["phase"] == "synthesis"]
        tri_calls = [{"phase": "triage", "tier": c["tier"], "model": c["model"], "in_tok": c["in_tok"], "out_tok": c["out_tok"],
                      "seconds": c["seconds"]} for c in calls if c["phase"] == "triage"]
        final = read(self.dir / "07_daily_brief.md")
        status = "ok" if final and checks.get("sections_ok") else ("partial" if final else "failed")
        run = {
            "date": self.run_id, "day": self.day, "mode": "daily", "status": status, "pipeline": "orchestrator",
            "schema_version": 1, "provider": llm.provider_name(), "slim_codex": llm.slim(),
            "started": self.first_start().strftime("%Y-%m-%dT%H:%M:%S+09:00"), "finished": fin_iso, "wall_seconds": wall,
            "checkpoint": None,
            "triage": {"environment": triage.get("environment"), "weights": [{"desk": w, "weight": 1.0} for w in triage.get("weight_agents", [])],
                       "active_agents": {"shared": sorted(takes)}, "depth": triage.get("depth", "normal"),
                       "reason": triage.get("reason", ""), "calls": tri_calls},
            "questions": pos.get("questions", []),
            "sources": srcs, "sources_scope": scope, "sources_from": "orchestrator",
            "package": {"compact_bytes": len(pkg_text.encode("utf-8")), "sections": sections,
                        "view_bytes": pkg.get("view_bytes"), "report": pkg.get("report")},
            "agents": agents, "edges": edges, "debates": debates,
            "deep_dive": {k: v for k, v in deep.items() if k != "sides"} if deep.get("triggered") else None,
            "evidence": {**pos["evidence"]["overall"], "citation_overlap": pos.get("citation_overlap")},
            "synthesis": {"draft": read(self.dir / "07_brief_draft.md"), "claims": checks.get("claims", []), "final": final,
                          "calls": synth_calls, "used": syn.get("used"),
                          "checks": {k: v for k, v in checks.items() if k != "claims"}},
            "usage": {"calls": len(calls), "failed_calls": len(failed), "by_tier": by_tier, "by_phase": by_phase,
                      "in_tok": sum(c.get("in_tok", 0) for c in calls), "cached_tok": sum(c.get("cached_tok", 0) for c in calls),
                      "out_tok": sum(c.get("out_tok", 0) for c in calls), "wall_seconds": wall},
            "delivery": {"telegram": bool(deliver.get("telegram")), "brief_words": len(final.split()),
                         "archived": deliver.get("archived", False)},
            "phases": self.phase_times,
        }
        (self.dir / "run.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        self.log(f"  run.json written ({len(calls)} calls, {run['usage']['in_tok']:,} in / {run['usage']['out_tok']:,} out tokens)")
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

    def go(self) -> int:
        self.prepare()
        self.first_start()
        self.log("==============================================")
        self.log(f"RECON ORCHESTRATOR -- {self.run_id} (provider {llm.provider_name()}, slim codex {llm.slim()}, parallel {PARALLEL})")
        self.log("==============================================")
        self.phase("score", self.ph_score)
        self.phase("collect", self.ph_collect)
        self.phase("context", self.ph_context)
        pkg = self.phase("package", self.ph_package)
        tri = self.phase("triage", self.ph_triage)["data"]
        self.phase("takes", lambda: self.ph_takes(tri))
        takes = {k: v["data"] for k, v in self.load_items("takes").items()}
        self.phase("challenges", lambda: self.ph_challenges(tri, takes))
        chs = self.load_items("challenges")
        self.phase("responses", lambda: self.ph_responses(tri, takes, chs))
        resps = self.load_items("responses")
        deep = self.phase("deepdive", lambda: self.ph_deepdive(tri, takes, resps))
        pos = self.phase("positions", lambda: self.ph_positions(tri, takes, chs, resps, deep))
        mem = self.phase("memory", lambda: self.ph_memory(tri, takes, resps, pos))
        syn = self.phase("synthesis", lambda: self.ph_synthesis(tri, takes, chs, resps, deep, pos))
        checks = self.phase("checks", lambda: self.ph_checks(takes))
        dlv = self.phase("deliver", self.ph_deliver)
        self.forced.add("record")
        self.phase("record", lambda: self.ph_record(tri, takes, chs, resps, deep, pos, mem, syn, checks, dlv, pkg))
        secs = int(time.time() - self.t0)
        self.log("==============================================")
        self.log(f"RECON COMPLETE in {secs // 60}m {secs % 60}s")
        self.log(f"Brief: {self.dir / '07_daily_brief.md'}")
        self.log("==============================================")
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RECON v2 orchestrator")
    ap.add_argument("--skip-collect", action="store_true")
    ap.add_argument("--skip-score", action="store_true")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--run-id")
    ap.add_argument("--package-from", help="copy 00_*.md inputs from this run folder (implies --skip-collect)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--from-phase", choices=PHASES)
    g.add_argument("--resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.dry_run:
        os.environ["RECON_LLM_PROVIDER"] = "dry-run"
        args.skip_collect = args.skip_score = args.no_telegram = True
    if args.package_from:
        args.skip_collect = True
    os.environ.setdefault("RECON_CODEX_SLIM", "1")
    run = Run(args)
    try:
        return run.go()
    except Exception as e:  # noqa: BLE001 - one line in the day log, then a non-zero exit for cron_run.sh
        run.log(f"FATAL: {type(e).__name__}: {e}")
        for line in traceback.format_exc().splitlines()[-6:]:
            run.log("  " + line)
        return 1


if __name__ == "__main__":
    sys.exit(main())
