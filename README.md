# RECON

> **Production branch is `v2`; `master` is the retired v1.** Everything below describes `v2`. Nothing on
> `master` runs.

RECON writes a daily intelligence brief. Collectors gather about a dozen source layers into one data package,
nine persona agents (trader, narrator, builder, analyst, skeptic, policy_analyst, user_agent,
macro_strategist, ai_engineer) take positions, the Phase D debate cross-examines the splits, a synthesizer
writes the brief, and Telegram delivers it. LLM calls go through the Codex CLI by default (`recon/llm.py`);
the Claude CLI is an optional provider.

## Operator guide

**Where it runs.** The InnovLabs droplet, checkout `/home/recon/recon-v2` (owned by `recon`), branch `v2`.
The checkout does not follow GitHub on its own: a push reaches production only after a deploy.

**When.** Root's crontab at 05:00 KST: `/bin/bash /home/recon/recon-v2/scripts/cron_run.sh`. It loads
`/home/recon/.recon.env`, takes a lock, runs `recon/orchestrator.py`, retries once with `--resume`,
alerts on Telegram if no brief lands, and writes the export (`recon/export.py`). The README's old v1
crontab (06:00, `run_recon.sh`, alert monitor every 15 min) is gone; it never worked under cron.

**Config.** Copy `.recon.env.example` to `~/.recon.env` (or point `RECON_ENV` at it), `chmod 600`.
Main switches: `RECON_LLM_PROVIDER`, `RECON_MODEL_*` / `RECON_EFFORT_*` (model tiers), `RECON_DEBATE`
(Phase D debate, on in production), `RECON_TELEGRAM_TOKEN` / `RECON_TELEGRAM_CHAT_ID`. Full list in
`docs/v2/DEPLOY.md`.

**Deploy.** Push to `v2`, wait for the "tests" workflow, then on the droplet as root:

```bash
bash /home/recon/recon-v2/scripts/deploy.sh --dry-run   # what would change
bash /home/recon/recon-v2/scripts/deploy.sh             # test, pull, chown, record the SHA
```

From the desktop: `bash scripts/deploy.sh --host root@<droplet>`. The script refuses 04:30-05:30 KST
and while a run holds the lock, and writes `deploy/last-deploy.json`; each `run.json` then carries
`deployed_sha`.

**Run by hand.** `python3 recon/orchestrator.py` (flags: `--skip-collect`, `--no-telegram`, `--run-id`,
`--from-phase`, `--resume`, `--dry-run`). Use `--no-telegram` for anything that is not the real brief.

**Replay.** `python3 scripts/launch_run.py <YYYY-MM-DD>` replays a past day's package on current code as a
tagged run with no Telegram; `scripts/replay_report.py` compares it with the old export.

**Logs and output.** `logs/cron.log` (cron starts), `logs/<date>.log` (phases; look for `RECON COMPLETE`),
`briefs/<date>/07_daily_brief.md` and `run.json`, `briefs/<date>/phases/calls.jsonl` (every LLM call),
`exports/index.json` (per-run summary for the RUBRIC recon page).

**Tests.** Offline, stdlib `unittest`, Python 3.12+, about a minute:

```bash
python3 -m unittest discover -s tests && python3 -m unittest discover -s tests/collectors
```

GitHub Actions runs the same on every push and PR to `v2` (`.github/workflows/tests.yml`).

**Known loose ends.** `cron_run.sh` can still fall back to the v1 `scripts/run_recon.sh` (10-21 and
10-22 burned about 300 calls each for a 0-word brief); see `docs/v2/DEPLOY.md`. `scripts/alert_monitor.sh`
is not in the production crontab; keep or cut is undecided.

## Layout

```
recon/          orchestrator, debate, evidence, llm provider, export, schemas
scripts/        cron_run.sh, deploy.sh, collectors (collect_*.py), launch_run.py, replay and Brier reports
                run_recon.sh / collect_data.sh / ask_hermes.sh are the v1 pipeline, kept for the fallback
personas/       one prompt per agent plus the synthesizers
config/         diets, prompts, resolvers, seeds; runtime state (memory, ledger) is gitignored
schemas/        JSON schemas for every structured LLM call
tests/          unit tests and committed fixtures (no network)
docs/v2/        plans and specs: start at 00-README.md; deploy runbook in DEPLOY.md
```

Built with the help of Claude Code. MIT license.
