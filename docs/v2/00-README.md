# RECON v2 planning set

Read in order:

1. `01-plan.md` — what v2 is: purpose (serve Lever and InnovLabs), products, desks and
   agents, sources, pipeline, run record, brain.html widget, integrations.
2. `02-review.md` — what is wrong or fragile in v1 (including: cron never worked), what
   changed in AI tooling that we use, token and usage optimisations, other optimisations,
   risks.
3. `03-models.md` — which model and effort for which part, budget shape on Pro 5x, the
   fallback order if limits bite, where the top model is worth it.
4. `04-implementation-plan.md` — repository layout, contracts, schemas, personas, widget
   wiring, ops, tests.
5. `05-build-plan.md` — phases 0–6 with checklists, self-review criteria, and the stop points
   where Eric reviews.

## Decisions already taken (2026-09-10)

- Codex CLI on ChatGPT Pro 5x is the backend; Claude CLI stays as an optional provider.
- Runs on the InnovLabs droplet, which is the box v1 already lives on.
- Branch `v2` in `notsatoshii/recon`, developed in the worktree `/home/recon/recon-v2`.
- Python orchestrator.
- World Monitor stays optional.

## Decisions taken by Claude on Eric's delegation (2026-09-10, "can you do it yourself")

1. **Desk split**: as in plan §3 (shared layer + Lever desk + InnovLabs desk, triage picks the day's set).
2. **Products**: Daily Brief, weekly Lever Radar (fundraising folded in as a section), Mon/Wed/Fri InnovLabs Digest.
3. **Lever private docs**: NOT quoted in the public repo. `context/lever_context.md` lives under `state/` (gitignored); only a redacted one-paragraph summary is committed. Eric can relax this later.
4. **Widget**: a panel on `brain.html` first (what Eric asked for); promoted to its own `recon.html` board only if the debate graph needs the room.
5. **Lever inbox**: off by default (`RECON_LEVER_INBOX=0`); nothing is dropped into Timmy's inbox until Eric turns it on.

## Still needed from Eric

- Approve the Codex device-auth code on the droplet (Phase 0). Everything after that is Claude's work until the Phase 1 stop.
