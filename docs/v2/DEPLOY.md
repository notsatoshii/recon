# Deploying and running RECON v2

Production runs branch `v2` from `/home/recon/recon-v2` on the InnovLabs droplet. `master` is the retired
v1 bash pipeline; nothing on it runs. The checkout does not follow GitHub by itself: a push to `v2` reaches
production only when someone deploys it (the 10-04 changes once sat unpulled for two weeks).

## Schedule
- Root's crontab, droplet time is KST: `0 5 * * * /bin/bash /home/recon/recon-v2/scripts/cron_run.sh`
- `cron_run.sh` loads `/home/recon/.recon.env`, takes `logs/.cron_run.lock`, runs `recon/orchestrator.py`,
  retries once with `--resume`, sends one Telegram alert on failure, then runs `recon/export.py`.
- Never deploy between 04:30 and 05:30 KST. `scripts/deploy.sh` refuses to.

## Env switches (names only; values live in `/home/recon/.recon.env`, mode 600)
- From `.recon.env.example`: `RECON_LLM_PROVIDER`, `RECON_MODEL_FAST`, `RECON_MODEL_ANALYST`,
  `RECON_MODEL_SYNTH`, `RECON_EFFORT_FAST`, `RECON_EFFORT_ANALYST`, `RECON_EFFORT_SYNTH`,
  `RECON_CLAUDE_MODEL_*`, `RECON_LLM_TIMEOUT`, `RECON_LLM_RETRIES`, `RECON_TELEGRAM_TOKEN`,
  `RECON_TELEGRAM_CHAT_ID`, `RECON_NO_TELEGRAM`, `RECON_HOME`, `RECON_VENV`, `RECON_TWSCRAPE_DB`,
  `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`, `CRYPTOPANIC_API_KEY`, `DUNE_API_KEY`.
- Read by `cron_run.sh` but not yet in the example: `RECON_DEBATE` (Phase D debate; 0 is off),
  `RECON_PIPELINE` (`bash` forces v1; do not set it), `RECON_ENV` (path of the env file).

## Deploy
1. Push to `v2` and wait for the GitHub "tests" workflow to go green.
2. On the droplet as root: `bash /home/recon/recon-v2/scripts/deploy.sh --dry-run`, then without `--dry-run`.
   From the desktop: `bash scripts/deploy.sh --host root@<droplet>` (runs the same script over ssh).
3. The script fetches, tests the new commit in a temporary copy as `recon`, pulls `--ff-only`, chowns to
   `recon`, and writes `deploy/last-deploy.json`. Every later `run.json` carries `deployed_sha`.
4. Next morning, check `logs/<date>.log` for `COMPLETE` and `run.json` `deployed_sha`.

## Replay (test a change on an old day without sending anything)
- `python3 scripts/launch_run.py 2026-10-18` syncs to origin/v2, refuses on stale code, and starts a tagged
  replay `briefs/<day>-c<N>` with `--no-telegram` in the background.
- Tagged runs use their own empty ledger under `briefs/<run-id>/state/`; only cron exercises the real resolver.
- `python3 scripts/replay_report.py <run-id> ... --old-dir exports/runs` compares a replay with the old export.

## Where things live (droplet, under `/home/recon/recon-v2`)
- `logs/cron.log`: one block per cron start. `logs/<date>.log`: the day's phases.
- `briefs/<date>/`: package, takes, `07_daily_brief.md`, `run.json`, `phases/calls.jsonl` (every LLM call).
- `exports/index.json` and `exports/runs/<date>.json`: written by `recon/export.py`, copied to the desktop.
- `deploy/last-deploy.json`: last deployed commit.

## The bash fallback
`cron_run.sh` still falls back to the v1 pipeline (`scripts/run_recon.sh --skip-collect`) when the
orchestrator and its `--resume` both fail with no takes. On 10-21 and 10-22 that path ran (`"pipeline": "bash"`
in the export): about 300 calls and a 0-word brief. It must not be reachable from cron. Recommended: keep
the crontab line above as the only entry (no `run_recon.sh` line, no `RECON_PIPELINE` in the env file) and
remove the fallback block from `cron_run.sh` in a separate change. Neither is applied by this document.
