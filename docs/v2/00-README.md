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

## Decisions still needed from Eric

1. **Desk split** (plan §3): eleven personas across a shared layer, a Lever desk, and an
   InnovLabs desk, with triage choosing the day's active set. Yes, or a different cut?
2. **Products** (plan §2): Daily Brief + weekly Lever Radar + Mon/Wed/Fri InnovLabs Digest.
   Keep the fundraising radar as part of Lever Radar, or as its own product?
3. **Lever context**: may the `risk_engineer` persona and `context/lever_context.md` quote
   from Lever's private docs on the droplet (`RISK_MODEL.md`, `ORACLE.md`, `FEE_MODEL.md`)? They
   would live in the recon repo, which is public on GitHub. If not, the context stays in
   `state/` (gitignored) and only a redacted summary is committed.
4. **Widget placement**: a panel on `brain.html` (as planned) or a separate `recon.html` page
   in RUBRIC with its own board? A panel is faster; a page gives the debate graph room.
5. **Lever inbox**: drop the Lever Radar into `/home/lever/command/inbox` for Timmy?
6. **Phase 0**: run `codex login --device-auth` on the droplet when ready. Everything after
   that is Claude's work until the Phase 1 stop.
