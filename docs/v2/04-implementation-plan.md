# RECON v2 — Implementation plan

Date: 2026-09-10. Companion to `01-plan.md` (what), `02-review.md` (why), `03-models.md`
(which model where). This file says what gets built, where, and with what contracts.

## 1. Repository layout (branch `v2`)

```
recon/                     Python package (the orchestrator)
  llm.py                   single LLM client: providers codex | claude | dry-run, tiers, retry, usage log   [drafted]
  threads.py               per-agent Codex thread handling (start, resume, cleanup)
  config.py                env, paths (RECON_HOME, state dir), tier table, desks, tensions
  package.py               package builder: dedupe, per-source digest, budgets, freshness stamps
  phases/                  one module per phase: score, collect, triage, takes, challenges, responses,
                           deepdive, converge, synth, deliver
  pipeline.py              phase runner: manifest, --from-phase, --mode, --skip-collect, --dry-run
  claims.py                programmatic numeric/named-claim extraction and lookup
  scoring.py               prediction scoring incl. Brier for probability calls
  telegram.py              chunking + HTML conversion (ported from v1's inline Python)
  export.py                run.json + index.json + library entries + brain markdown export
  schemas/                 JSON schemas for every structured call (triage, picks, vote_memory_state,
                           claims_rewrite, library_entry, radar, digest)
collectors/                one module per source, common interface: collect() -> SourceResult
  reddit.py  polymarket.py  kalshi.py  defillama.py  news_rss.py  hn_github.py  arxiv.py
  changelogs.py  korea_rss.py  twitter_nitter.py  rootdata.py  worldmonitor.py  bettafish.py
personas/                  markdown, one per agent (11), plus synthesizer_*.md
context/                   sector_context.md, lever_context.md (from Lever docs), innovlabs_context.md
state/                     RUNTIME, gitignored: agent_memory/, agent_state/, knowledge.db, runs/, alert_state.json
scripts/
  run_recon.sh             thin launcher: loads env, checks codex login, execs python -m recon.pipeline
  alert_monitor.sh         thin launcher for recon.phases.alerts
  setup.sh                 dependency and login checks (codex first, claude optional)
  install_cron.sh          writes the crontab block with SHELL=/bin/bash
docs/v2/                   this plan set + model-log.md
tests/
  fixtures/package-2026-06-20/   a real collected package copied from v1 (sanitised)
  test_claims.py test_package.py test_schemas.py test_pipeline_dryrun.py
```

Everything under `state/` is created on first run; `scripts/migrate_v1_state.sh` copies v1's
memory and state files in once.

## 2. Contracts

### 2.1 `recon.llm.ask(prompt, tier, persona_path=None, schema_path=None, agent=None) -> str`
Never returns empty; raises `LLMError` after retries. Logs one line per attempt to
`logs/llm_calls.log` with tier, model, bytes, seconds, tokens. CLI form reads the prompt from
stdin so shell callers (bettafish during transition, ad-hoc use) share the same path.

### 2.2 `recon.threads.AgentThread`
`start(agent, prompt) -> reply`, `follow(prompt, schema=None) -> reply`, `close()`. Wraps
`codex exec` / `codex exec resume <id>`; captures the thread id from `--json` events. If the id
cannot be captured, degrades to stateless calls with a trimmed context summary and records
`thread_mode: "stateless"` in the manifest.

### 2.3 `SourceResult`
`{name, ok: bool, items: int, fetched_at: iso, text: str, error: str|None}`. The package builder
renders `text` when `ok`, otherwise a one-line "SOURCE UNAVAILABLE TODAY (<error>)".

### 2.4 Package
`package.build(sources, mode) -> Package` with `compact` (digest, ≤10 KB), `raw[section]`
(on disk), `by_desk[desk] -> [sections]`, and `freshness`. Budgets per section live in
`config.py`; there is no other truncation anywhere.

### 2.5 Structured calls (schemas)
- `triage`: `{environment, weights[], active_agents{desk: [names]}, depth: light|full}`
- `pick_pair`: `{challenger, target, reason}`
- `deep_dive_decision`: `{needed: bool, agents[2], point}`
- `vote_memory_state`: `{vote{act_on, market_wrong_about, unseen_risk}, tracking[], predictions[{text, probability, resolves_on}], lessons[], position, changed, watching[]}`
- `claims_rewrite`: `{rewritten_sections{name: text}}` for flagged claims only
- `library_entry`: the curriculum library schema (what/use/why/watch_out, link, difficulty, status, tags)
- `radar` and `digest`: section-typed outputs for the weekly products
Schemas are validated on our side after the call as well (the CLI validates, we re-check).

### 2.6 `run.json`
As specified in `01-plan.md` §6. Written incrementally after each phase so a crashed run still
leaves a readable partial record. `export.py` copies it to the served path and updates
`index.json`.

### 2.7 Manifest and resume
`state/runs/<date>/manifest.json`: phases with status, started/finished, calls, tokens.
`pipeline.py --from-phase takes` re-enters using artifacts on disk; `--checkpoint-on-limit`
stops cleanly on a rate-limit error and exits 75 so a retry cron can continue it.

## 3. Personas and context

- Port the eight v1 personas that survive (rename `trader→pm_trader`, `builder→protocol_builder`,
  `user_agent→office_worker`; drop `regulator` in favour of `policy_analyst`; drop `analyst`
  whose role the desks now cover).
- New: `risk_engineer.md`, `curriculum_lead.md`, `korea_market.md`. Drafted from Lever's
  `RISK_MODEL.md`/`ORACLE.md`/`FEE_MODEL.md` and the InnovLabs curriculum notes; Eric reads them
  before the first real run.
- `context/lever_context.md` (protocol summary, mechanisms, what the desk cares about) and
  `context/innovlabs_context.md` (audiences, curriculum spine, library schema, brand rules) are
  static standing context for their desks. `sector_context.md` trimmed to the shared layer.
- Synthesizer personas gain the desk structure for the daily brief and typed sections for
  Radar and Digest.

## 4. The brain.html widget (RUBRIC repo, local)

- `brain.html`: new panel `data-w="recon"` registered in the `Widgets({...defaults})` call;
  tabbed content (Runs / Agents / Debate / Brief / Sources) rendered from fetched JSON; argument
  graph drawn as inline SVG with the same dark/orange tokens as the rest of the board.
- `serve.js`: `GET /recon/index.json` and `GET /recon/runs/<date>.json` proxied to
  `RECON_URL` with `RECON_TOKEN` from `brain.config.json` (never in the page). Allowlisted path
  pattern only.
- Droplet side: `export.py` writes to `/var/www/recon/` served by the existing nginx site under
  `/recon/<token>/…` (static, no directory listing). When RUBRIC moves to the droplet, the proxy
  reads the folder directly.
- Also: `scan.js` indexes `state/exports/library/*.md` (synced or served) so digest entries
  become brain nodes.

## 5. Ops

- `install_cron.sh` writes: `SHELL=/bin/bash`, `PATH` including the npm global bin, then the
  daily brief 06:00 KST, Digest Mon/Wed/Fri 07:00, Radar Mon 07:30, alerts every 15 min, a
  retry job at 09:00 that resumes any run that checkpointed on a limit, and a nightly export.
- Launcher checks `codex login status` and posts a Telegram alert if not logged in.
- Logs rotate weekly; runs older than 90 days are compressed.
- v1 stays untouched at `/home/recon/recon` until cutover; its crontab lines are replaced by
  v2's in one edit at cutover.

## 6. Testing

- Unit: claims extraction, package budgets, schema validation, telegram chunking.
- Dry-run: full pipeline on the fixture package with the dry-run provider; asserts every phase
  artifact and `run.json` shape. Runs in seconds, spends nothing.
- Live smoke: one tiny call per tier after login (model availability), then one real
  `--skip-collect` run on the fixture package.
- Collector audit: each collector run alone, results table in `docs/v2/sources-audit.md`.
