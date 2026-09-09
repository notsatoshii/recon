# RECON v2 — Plan

Date: 2026-09-10. Owner: Eric. Runs on the InnovLabs droplet (the same box that already runs
v1 under `/home/recon/recon`). LLM backend: Codex CLI on the ChatGPT Pro 5x subscription.

## 1. Purpose

RECON v2 is a daily intelligence system that exists to make two projects move faster:

- **Lever** — synthetic leveraged perpetuals on prediction-market outcomes (Base, Polymarket
  oracle). Needs: what moved in prediction markets and why, where leverage demand and risk
  sit, what regulators and competitors did, who raised money, and calibrated probability
  calls it can learn from.
- **InnovLabs** — Korean AI-workflow education and services. Needs: what changed in the AI
  tool landscape that affects the curriculum, the toolkit, and the platform's knowledge base;
  what the Korean market and competitors are doing; content the courses and the site can use.

Everything else (world events, macro, crypto majors) stays as the shared context layer those
two desks read from, not as a product of its own.

## 2. Products

| Product | Cadence | Audience | Form |
|---|---|---|---|
| **Daily Brief** | daily 06:00 KST | Eric | Telegram, 600–900 words: world/macro context, Lever desk, InnovLabs desk, contrarian case, risks, watch list, scorecard |
| **Lever Radar** | weekly Mon | Eric, Lever | Prediction-market structure: volume/liquidity shifts, new markets suited to leverage, oracle anomalies, competitor perps, regulation (CFTC, Kalshi, Polymarket), fundraising in the space, calibration report of RECON's own probability calls |
| **InnovLabs Digest** | Mon/Wed/Fri | Eric, curriculum | Model and tool changes with "what it means for the curriculum/toolkit", Korean market signals, competitor moves, content ideas. Ships structured resource-library entries (JSON) alongside the prose |
| **Alerts** | 15-min cron | Eric | Threshold alerts (kept from v1, moved to the Python package) |
| **Run record** | every run | brain.html widget | `run.json`: every agent's inputs, outputs, and the argument graph |

## 3. Agents, reorganised into desks

v1 has nine generic crypto personas. v2 keeps the debate mechanics but assigns agents to desks so
the debate is about the questions Lever and InnovLabs actually have.

**Shared context layer (3):** `macro_strategist`, `narrator` (discourse and sentiment),
`skeptic` (always active, challenges everyone).

**Lever desk (4):** `pm_trader` (prediction-market trader, from v1 `trader`), `risk_engineer`
(new: leverage, liquidation, oracle and settlement risk, reads Lever's own risk model),
`policy_analyst` (regulatory, from v1), `protocol_builder` (Base/DeFi perps and competitor
mechanics, from v1 `builder`).

**InnovLabs desk (4):** `ai_engineer` (from v1), `curriculum_lead` (new: what a change means
for the spine, the cartridges, and the resource library), `korea_market` (new: Korean AI
news, workplace adoption, regulation, competitor courses), `office_worker` (from v1
`user_agent`: ground-level usability and time-saved framing).

That is eleven personas, but not all run every day. A triage step picks the active set from the
day's data (see §5), so a typical day runs six to nine agents.

New persona files need Eric's read before first use: `risk_engineer`, `curriculum_lead`,
`korea_market`. Their drafts will cite Lever's docs (`RISK_MODEL.md`, `ORACLE.md`) and the
InnovLabs curriculum notes as their standing context.

## 4. Data sources

Keep everything in v1 that still returns data (audited by running it, Phase 3 of the build plan)
and add the desk-specific sources:

**Lever desk:** Polymarket gamma API (already used; extend to new markets, 24h movers, liquidity
depth, resolution calendar), Kalshi public markets API, DeFiLlama perps and DEX volumes (already
used), Base ecosystem feeds, Polymarket/Kalshi/CFTC news RSS, Hyperliquid/dYdX/GMX stats.

**InnovLabs desk:** GitHub Trending and HN (already used), AI news RSS (already used), vendor
changelogs (Anthropic, OpenAI, Cursor, GitHub Copilot release notes; RSS where available),
arXiv cs.AI/cs.CL daily RSS, Korean AI media RSS (AI타임스, ZDNet Korea, 전자신문 AI section;
availability to be verified), Korean competitor course listings (verify what is scrapeable
without violating terms).

**Shared:** Reddit RSS, X via nitter/Playwright (fragile; audited), World Monitor Redis (kept
optional; the container is on this box but reports unhealthy), BettaFish sentiment (kept).

## 5. Pipeline (v2)

```
-1  score yesterday's predictions (programmatic; Brier for probability calls)
 0  collect (parallel collectors, per-source freshness stamps, failures marked in package)
 0.5 package build: dedupe + per-source digest (FAST tier, 1 call per source group)
     → compact package (~10 KB) + raw sections on disk for retrieval
 1  triage (FAST, 1 call, schema): environment class, active agents per desk, run depth
 2  takes (ANALYST, one Codex session per agent; each gets the compact package + its desk's
     raw sections + its memory/state)
 3  challenges (ANALYST, resumed sessions): desk tensions + one wildcard picked by schema
 4  responses (ANALYST, resumed)
 5  deep dive only if triage/synth flags an unresolved point (ANALYST, resumed, ≤2 calls)
 6  vote + memory + state in ONE resumed call per agent (FAST, schema) — replaces 27 calls
 7  synthesis (SYNTH): draft → programmatic numeric-claim check → targeted rewrite of flagged
     claims only → Telegram format (programmatic)
 8  deliver: Telegram; write run.json; index into knowledge DB; export for brain.html
```

Depth is adaptive: a QUIET triage result runs the desks with three agents each and skips the
deep dive; a RISK-DRIVEN day runs everything.

## 6. Run record and the brain.html widget

Every run writes `briefs/<date>/run.json`:

```
{ date, mode, triage: {environment, weights, active_agents, depth},
  agents: [{ name, desk, persona_hash,
             fed: { package_sections: [...], raw_sections: [...], memory_lines, state_lines, bytes },
             take, challenges_received: [{from, type, text}], challenges_made: [{to, type, text}],
             response, deep_dive, vote, memory_update, calls: [{phase, tier, model, in_tok, out_tok, seconds}] }],
  edges: [{from, to, type: "tension"|"wildcard"|"deepdive"}],
  synthesis: { draft, claims: [{claim, found_in_source: bool, action}], final },
  usage: { calls, by_tier: {...}, wall_seconds }, sources: [{name, ok, items, fetched_at}] }
```

The widget lives in RUBRIC's `brain.html` as a new board panel (`data-w="recon"`) using the
existing `Widgets` engine, with tabs: **Runs** (list, status, usage), **Agents** (who was
active, what each was fed, take/response/vote), **Debate** (argument graph: nodes = agents,
edges = challenges, click to read both sides), **Brief** (final text and the claims check),
**Sources** (what returned data). Data reaches it through a `/recon/*` route in `serve.js`
that proxies to a token-protected static path on the droplet where RECON exports
`index.json` and `runs/<date>.json`. When RUBRIC itself moves to the droplet (its phase 4) the
proxy becomes a local path.

## 7. Delivery integrations beyond Telegram

- **RUBRIC second brain:** the InnovLabs Digest's resource-library entries are exported as
  Markdown files into a folder `scan.js` already indexes, so they appear as brain nodes.
- **Curriculum resource library:** the same entries in the library's schema (what it is / use
  it to / why it matters / watch out, link, difficulty L1–L4, status), ready for the planned
  Supabase `tools` table.
- **Lever command inbox (optional):** the Lever Radar can be dropped into
  `/home/lever/command/inbox` for Timmy if Eric wants it there.

## 8. What v2 does not do

No paid APIs. No fine-tuning. No vector database (FTS5 stays). No autonomous trading or
position advice: Lever desk output is market-structure intelligence, not trade calls. No
scraping that requires logging into a service.
