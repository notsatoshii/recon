# RECON v2 — Build plan

Date: 2026-09-10. Each phase ends with a self-review against its checklist and a stop for
Eric. Nothing is pushed to GitHub or put on cron until Eric says so. v1 keeps running (or
rather, keeps failing silently on cron) untouched until the cutover phase.

Work happens in the worktree `/home/recon/recon-v2` on the droplet (branch `v2`, already
created from master `0344e61`). Commits are small and on the droplet; the local scratchpad
clone is only an editing convenience.

## Phase 0 — Access and login (Eric, ~10 minutes)

- [ ] Eric runs on the droplet: `codex login --device-auth`, follows the device-code prompt in
      a browser, then `codex login status` shows the account.
- [ ] Claude: add `Host github-recon` to `/root/.ssh/config` using the recon deploy key so the
      worktree can fetch and (later) push.
- [ ] Claude: one tiny call per tier to confirm `gpt-5.6-luna`, `gpt-5.6-terra`, `gpt-5.6-sol`
      (and `gpt-6-astra`) respond on this account. Result recorded in `docs/v2/model-log.md`.

Stop: Eric confirms login; Claude reports which models answered.

## Phase 1 — Backend swap on the v1 pipeline (Claude, half a day)

Goal: the existing bash pipeline produces a brief through Codex, otherwise unchanged. This is
the safety net: if anything later slips, a working Codex-backed v1 exists.

- [ ] `recon/llm.py` (drafted) + `scripts/ask_hermes.sh` rewritten as a shim calling it; the
      15 call sites get tier names (`fast`/`analyst`/`synth`) instead of model IDs.
- [ ] `collect_bettafish.py` uses the client.
- [ ] `RECON_HOME` and env-file discovery replace the 14 hard-coded paths; `--no-telegram` flag.
- [ ] `setup.sh` checks codex first; `.recon.env.example` gains the tier variables.
- [ ] Dry-run: `RECON_LLM_PROVIDER=dry-run ./scripts/run_recon.sh --skip-collect --no-telegram`
      on a copy of v1's last package completes all phases.
- [ ] Live: same command with the codex provider and Telegram on; brief lands in Eric's chat.
- [ ] Commit: "v2 phase 1: Codex backend via recon/llm.py, tiers, portable paths".

Self-review checklist: no `claude` string left in scripts except the optional provider; every
call site names a tier; `llm_calls.log` shows tokens; a killed call retries and logs the attempt.

Stop: Eric reads the first Codex-produced brief and judges quality against a v1 brief.

## Phase 2 — Python orchestrator, threads, schemas (Claude, 2–3 days)

- [ ] `recon/config.py`, `package.py`, `threads.py`, `pipeline.py`, `phases/*`, `schemas/*`,
      `telegram.py`, `claims.py`, `scoring.py`, `export.py` per the implementation plan.
- [ ] Runtime state moved to `state/`; `migrate_v1_state.sh`; `.gitignore` updated.
- [ ] Merged vote+memory+state call; programmatic claims check; adaptive depth from triage.
- [ ] `run.json` and manifest written incrementally; `--from-phase`; `--checkpoint-on-limit`.
- [ ] `tests/` with the fixture package; dry-run pipeline test green.
- [ ] Live: one full run; compare call count, tokens, wall time, and brief against Phase 1's.
- [ ] Commits per module group (config+package, threads+llm, phases, synth+claims, export+tests).

Self-review: thread reuse actually happened (manifest says `thread_mode: "resumed"` for every
agent); no phase parses free text; a simulated rate-limit error checkpoints and resumes.

Stop: Eric reviews the run record (`run.json`) and the brief; approves the desk structure before
personas change.

## Phase 3 — Desks, personas, sources (Claude drafts, Eric reads; 2–3 days)

- [ ] Personas renamed/ported; three new personas drafted with `context/lever_context.md` and
      `context/innovlabs_context.md`. Eric reads all three new ones.
- [ ] Collector audit: every v1 source run alone; results in `docs/v2/sources-audit.md`; dead
      sources replaced or marked optional.
- [ ] New collectors: Kalshi, arXiv, vendor changelogs, Korean RSS (only feeds that verify),
      Polymarket extensions (new markets, movers, resolution calendar).
- [ ] Synthesizer personas for the desk-structured daily brief, Lever Radar, InnovLabs Digest;
      `library_entry` export into `state/exports/library/`.
- [ ] Brier scoring live on predictions with probabilities.
- [ ] Live: three consecutive daily runs plus one Radar and one Digest.

Stop: Eric judges whether the brief now says things useful for Lever and InnovLabs. This is
the phase most likely to loop; that is expected.

## Phase 4 — brain.html widget (Claude, 1–2 days, RUBRIC repo)

- [ ] Droplet: `export.py` writes `index.json` + `runs/<date>.json` to `/var/www/recon/`;
      nginx location under a token path; verified with curl.
- [ ] RUBRIC `serve.js`: `/recon/*` proxy with token from `brain.config.json`.
- [ ] `brain.html`: `recon` panel with Runs / Agents / Debate / Brief / Sources tabs; argument
      graph as SVG; movable and resizable like every other tile.
- [ ] `scan.js`: index the library export folder so digest entries appear as brain nodes.
- [ ] Verify at `http://127.0.0.1:4750/brain.html` with a real run loaded; screenshots to Eric.

Stop: Eric uses the widget on a real run.

## Phase 5 — Ops and cutover (Claude, half a day; Eric flips the switch)

- [ ] `install_cron.sh` with `SHELL=/bin/bash`; retry job; export job; log rotation.
- [ ] Launcher login check with Telegram alert.
- [ ] README rewritten for v2 (Codex first, desks, products, widget, usage limits in window
      terms, cron block).
- [ ] Cutover: replace the four v1 crontab lines with v2's; leave v1's directory in place for
      two weeks; then archive it.
- [ ] Watch one scheduled 06:00 run land unattended. Confirm on Telegram.
- [ ] Push `v2` to GitHub and open a PR to master (Eric merges).

Stop: Eric merges.

## Phase 6 — First-week tuning (Claude, ongoing, light)

- [ ] After seven scheduled runs: usage per tier, claims-flag rates, wall time → model-log
      entries and any tier/effort change.
- [ ] Decide on SYNTH+ (astra) for the daily draft based on headroom.
- [ ] Widget polish from Eric's usage notes.

## Sequencing notes

- Phase 1 can start as soon as Phase 0's login is done; nothing else in Phase 1 waits.
- Phases 2 and 3 overlap in practice: persona drafts and the collector audit can run while the
  orchestrator is being written, and a subagent handles the audit.
- The widget (Phase 4) only needs `run.json`, which exists from Phase 2, so it can start before
  Phase 3 finishes if Eric wants to see it earlier.

## Estimated time

Claude working time roughly six to eight working days across the phases, spread over two
weeks to leave room for Eric's reviews and for three consecutive real runs in Phase 3.
