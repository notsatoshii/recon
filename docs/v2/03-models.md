# RECON v2 — Model selection

Date: 2026-09-10. Account: ChatGPT Pro 5x. Models available in Codex CLI today: `gpt-6-astra`,
`gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna` (all with reasoning effort minimal→xhigh).
Availability per sign-in method is verified with a one-line call after login before wiring.

## 1. Principle

Call sites name a **tier**, never a model. Tiers are set in `.recon.env`. Changing a model is a
one-line config change and a note in the run manifest.

## 2. Tiers

| Tier | Default model | Effort | Used for | Why |
|---|---|---|---|---|
| **FAST** | `gpt-5.6-luna` | low | source digests, triage, challenger/target and deep-dive picks, merged vote+memory+state, alert wording, BettaFish sentiment | Short, structured, high-volume. Quality difference is invisible behind a schema; the allowance is roughly 50–80x that of the top model. |
| **ANALYST** | `gpt-5.6-terra` | medium | agent takes, challenges, responses, deep dives (per-agent Codex thread) | The persona work. Needs real reasoning over a 10–25 KB package but not top-tier depth; runs 30–40 times a day. |
| **SYNTH** | `gpt-5.6-sol` | high | brief draft, targeted claims rewrite, Lever Radar and InnovLabs Digest synthesis | The reader-facing text. Three calls or fewer per run, worth the highest effort we can afford daily. |
| **SYNTH+** (opt-in) | `gpt-6-astra` | high | the daily brief draft only, weekly Lever Radar | Enabled per product with `RECON_MODEL_SYNTH_PLUS`. Off by default until a week of usage logs shows headroom. |

Claude CLI stays available as a provider (`RECON_LLM_PROVIDER=claude`) with the same tiers
mapped to Haiku 4.5 / Sonnet 5 / Opus 5, for fallback or side-by-side quality checks. It is not
in the daily path.

## 3. Budget shape per full daily run (target)

| Phase | Calls | Tier | Context size |
|---|---|---|---|
| source digests | 4 | FAST | 10–15 KB each |
| triage | 1 | FAST | ~12 KB |
| takes | 6–9 | ANALYST (new thread) | 15–25 KB |
| challenges | 6–10 | ANALYST (resumed) | 1–3 KB |
| responses | 6–9 | ANALYST (resumed) | 1–3 KB |
| deep dive | 0–2 | ANALYST (resumed) | ~1 KB |
| vote+memory+state | 6–9 | FAST (resumed) | ~2 KB |
| synthesis draft | 1 | SYNTH | 20–30 KB |
| claims rewrite | 0–1 | SYNTH | 3–8 KB |
| **total** | **36–46** | | |

Against v1's ~65 calls at 30–60 KB each. Digests and lightweight products (Radar, Digest) are
one SYNTH call plus one FAST formatting call.

## 4. Fit within Pro 5x

Public estimates put Plus at 3–30 top-model messages and 250–2,000 light-model messages per
5-hour window; Pro 5x multiplies that. A run shaped as above uses on the order of 15 FAST, 25
ANALYST, and 2–3 SYNTH messages, which should sit inside one window even at the pessimistic end,
with the weekly cap the number to watch. The rules if reality is tighter:

1. Drop SYNTH+ (it is off by default anyway).
2. Triage depth defaults to "light" (three agents per desk) on QUIET and NARRATIVE days.
3. Move ANALYST to `gpt-5.6-luna` at medium effort before touching SYNTH.
4. Only then reduce the number of desks per day (alternate Lever/InnovLabs deep coverage).

Usage is read from `--json` events into `run.json` per call, and the launcher checks the
subscription state before starting (`codex login status`; a usage probe if the CLI exposes
one non-interactively, otherwise the first call's error is the signal).

## 5. Where the top model is worth it

Not in the debate. The debate's value is diversity of framing under a shared data package; a
mid-tier model in nine roles beats one top model in three. The top model is worth it exactly
where a human reads the words: the daily brief draft, the weekly Lever Radar (structural
analysis with numbers that Eric will act on), and nowhere else.

## 6. Tuning loop

After the first seven real runs: compare per-tier token totals and wall time from the manifests,
read the claims-check flag rates per tier, and adjust effort first, model second. Record every
change in `docs/v2/model-log.md` with the date and the reason.
