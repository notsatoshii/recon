# RECON v2 — Review of v1 and improvement proposals

Date: 2026-09-10. Findings come from reading the code at commit `0344e61` and inspecting the
live install on the droplet.

## 1. State of v1 on the droplet

- **Scheduled runs have never worked.** The root crontab lines start with `source …`, and
  cron runs them under `/bin/sh` (dash), which has no `source`. Every 06:00 run has died
  before starting since the crontab was written. All briefs on disk (last: 2026-06-20) came
  from manual runs. The 15-minute alert monitor fails the same way.
- 1,824 logged LLM calls: 1,602 Claude, 154 Hermes fallback, 68 failed. The last run took
  49 minutes.
- Working tree carries uncommitted runtime state (agent memory and state files) because those
  are tracked in git. v2 treats them as data, not source.
- World Monitor container is up but marked unhealthy; the Redis it feeds may be stale.
- The recon SSH deploy key is under the `recon` user; root (which runs cron and the worktree)
  cannot fetch or push. Fix: a Host alias in root's SSH config pointing at the same key.

## 2. Structural weaknesses in v1

1. **Every call re-sends the world.** Each of ~65 calls gets the persona plus up to 50 KB of
   package plus memory plus context. Agents receive their own take back four times. On a
   message-metered subscription this is the single biggest waste.
2. **Mechanical decisions parsed by regex.** Challenger/target, environment/weights, deep-dive
   choice, and every vote are free text parsed with grep. Any phrasing drift silently drops a
   phase (v1 already had to patch "empty agent names in Phase 5.5").
3. **Bash as orchestrator.** 920 lines with associative arrays, background subshells, and file
   round-trips to move state. No resume, no manifest, no typed outputs.
4. **Memory updates are 18 calls per run** (memory plus state, per agent), each re-sending
   the take, the vote, and the whole memory file.
5. **Hallucination pass is a full rewrite by the most expensive model** on every run, even
   when there is nothing to fix. It also has to re-read 30 KB of raw data.
6. **Hard-coded paths** in 14 files (`/home/recon/recon`), hard-coded model IDs at 16 sites.
7. **Collectors are all-or-nothing.** A dead source yields an empty section with no marker,
   so the synthesizer cannot say "no X data today" and agents cite stale memory instead.
8. **No test path.** There is no way to run the pipeline without spending model usage.
9. **Delivery is prose only.** Nothing machine-readable leaves a run except the log line, so
   nothing downstream (brain, library, Lever tooling) can consume it.

## 3. Improvements from what changed in AI tooling (verified 2026-09-10)

- **Codex sessions with `codex exec resume`.** Give each agent one Codex thread per run: the
  take call carries the package once; challenges, responses, votes, and memory updates are
  short follow-ups on the same thread. Cuts input volume per run by roughly two thirds and
  makes the agent's later answers consistent with its earlier reasoning. (Session ID capture
  from `--json` events to be confirmed at build; fallback is re-sending a trimmed context.)
- **Schema-validated output (`--output-schema`).** Every mechanical decision becomes JSON that
  is validated before the pipeline continues. Removes the regex class of failures entirely and
  gives the widget typed data for free.
- **Reasoning effort per call (`model_reasoning_effort`).** v1 could only pick a model. v2
  uses low effort on the fast tier and high on synthesis. Same model family, very different
  cost per call.
- **Model tiers instead of model names.** Luna/Terra/Sol today, whatever ships next quarter
  tomorrow; call sites never change. (See `03-models.md`.)
- **Ephemeral runs for one-shot calls, persisted threads for agents.** `--ephemeral` for
  digests and triage so `~/.codex/sessions` does not fill up; agent threads are deleted after
  the run's record is written.
- **Sandboxed, empty working directory.** `codex exec` is an agent, not a completion API. Each
  call runs with `--sandbox read-only` inside an empty temp dir so the model has nothing to
  read or execute; the prompt is the only input.

Not adopted, deliberately: Codex's own multi-agent features (unverified surface, and the
debate structure is RECON's product logic, better kept in our orchestrator); embeddings or
vector search (FTS5 over a few hundred briefs is fine and free); any pay-per-token API.

## 4. Token and usage optimisations

Ordered by expected saving.

1. **Session reuse** (above): ~65 full-context calls → ~9 full-context + ~35 short follow-ups.
2. **Compact package with retrieval.** A FAST-tier digest per source group (four calls,
   cached per collection run) turns the 50 KB package into ~10 KB for everyone; agents also get
   the raw sections for their desk only. The synthesizer can pull any raw section on demand
   from disk for the claims check.
3. **Merge vote + memory + state into one schema call per agent.** 27 calls → 9, and the
   memory update becomes structured (tracking items, predictions with dates and probabilities,
   lessons), which the scorer can read without another model call.
4. **Programmatic claims check before the rewrite.** Extract numbers and named claims from the
   draft with a regex pass, look each up in the raw sections on disk, and send the synthesizer
   only the flagged ones with their context. Most days this is a small call or none at all,
   instead of an Opus-class rewrite of the whole brief with 30 KB of raw data attached.
5. **Adaptive depth from triage.** One FAST call classifies the day and picks the active agents
   per desk. QUIET days run three agents per desk and skip the deep dive.
6. **Prompt hygiene.** Static persona and sector context at the top of every prompt, dynamic
   data at the bottom, identical wording across calls. If the subscription backend applies
   prefix caching this helps; if not, it costs nothing.
7. **Truncate once, centrally.** Every `head -c` in v1 becomes one package policy with
   per-section budgets, so the same 3 KB of history is not attached in five different sizes.
8. **Dry-run provider** so all of the above can be exercised and profiled without spending a
   message.

Expected outcome: a full daily run at roughly 40–45 calls, most of them short, with three
SYNTH-tier calls or fewer. Measured after the first real runs with per-call token counts from
`--json`, and tuned from there.

## 5. Other optimisations

- **Parallelism.** `codex exec` processes are independent; run up to five at a time and back
  off only on rate-limit errors, instead of a fixed `sleep 3` per call.
- **Resume by phase.** Each phase writes typed artifacts; `--from-phase` restarts after a
  failure without re-running earlier phases. If the 5-hour window closes mid-run, the run
  checkpoints and a retry cron finishes it in the next window.
- **Source freshness in the package.** Each collector writes `{ok, items, fetched_at}`; the
  package shows "SOURCE UNAVAILABLE TODAY" so agents do not backfill from memory, and the widget
  shows what fed the run.
- **Calibrated predictions.** Agents' predictions carry a probability and a resolution date.
  Brier scoring per agent and per desk replaces RIGHT/WRONG/PENDING and produces a dataset
  Lever can actually use (how well do informed generalists price events versus the market).
- **Runtime state out of git.** Agent memory, state, knowledge DB, and runs are data under
  `state/` and ignored; the repo holds code, personas, and docs only.
- **Crontab fix.** `SHELL=/bin/bash` at the top of the crontab plus a launcher script that
  loads the env file itself. Verified by watching one scheduled run land in `logs/`.
- **Per-run manifest** with timings and usage, so slow phases and expensive prompts are visible
  without grepping logs.

## 6. Risks and unknowns

- **Codex model availability on this account.** The docs say availability depends on sign-in
  method and rollout. Each tier's model is verified with one tiny call after login before
  anything is wired.
- **Actual window limits.** Public numbers are third-party estimates. First week runs log
  usage; if the daily run does not fit comfortably in one window, the triage depth defaults
  drop before anything else changes.
- **X scraping.** nitter.cz was the only instance; expect it to be dead. Options when we get
  there: other public instances, dropping X, or treating BettaFish's sentiment as the social
  signal.
- **Korean sources.** RSS availability and scraping terms unverified; will be checked source
  by source in Phase 3 and only included where a feed exists.
- **World Monitor.** Unhealthy container; v2 treats it as optional and never blocks on it.
- **Headless login.** `codex login --device-auth` must be run by Eric on the droplet once;
  the auth file lives under `/root/.codex`. Token refresh behaviour on a server is unknown
  until observed; the launcher checks `codex login status` and alerts on Telegram if it fails.
