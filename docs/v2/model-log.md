# RECON v2 — model and run log

One line per phase result or model change: date, what, calls, tokens, wall time.
Plan: `06-improvement-plan.md` §7. Tokens are from `logs/llm_calls.log` (Codex `--json` usage).

| Date | Entry | Calls (analyst / fast / synth) | Input tokens (cached) | Output tokens | Wall time |
|---|---|---|---|---|---|
| 2026-09-11 | Baseline: last v1-flow run before Phase A (from the plan, §1) | 61 (40 / 19 / 2) | 1.36 M | 37 K | 10 min collect + 11–12 min LLM |
| 2026-10-04 | **Phase A** dry run (dry-run provider, 09-11 package, through `cron_run.sh`): green; deep-dive parser, labelled votes (9/9 parsed), memory rewrite (1 copy each), 11-section check all exercised | 61 (0 tokens) | — | — | 1 min 40 s |
| 2026-10-04 | **Phase A** live validation run (fresh data, `--no-telegram`, via `cron_run.sh`): brief 1,762 words, 11/11 sections in order, 0 failed calls, 0 parse warnings, no deep dive triggered. Agent view 63.3 KB of a 176.9 KB package with all 8 sections (was the first 90 KB); X 43/112 handles before the 10-min budget hit a rate limit; Reddit 6/21 (pacing fixed after this run, `a49888b`) | 62 (38 / 22 / 2) | 1.26 M (0.68 M cached) | 41.7 K | 20 min (collect 7 min, LLM 12 min 45 s) |
