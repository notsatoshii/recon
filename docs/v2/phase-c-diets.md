# Phase C addendum: per-lens diets and opinionated personas (2026-10-04)

Eric, 2026-10-04: the agents should be very opinionated, and like people they should read only what they
care about (some want sentiment, some are technical, some care about other things). They should not all get
the same information.

## Why

F6 (improvement plan §2.2): takes converge before the debate starts because nine lenses read the same
~60 KB view and answer the same questions with the same model. Lens extras (§3 item 3) add at most 6 KB per
lens on top of the shared 60 KB, which is too little to change what a lens sees first. The sixth review's
0 crux hits and the consensus days (09-11 c15: gaps 16/11/19 under GAP_MIN 20) are the same symptom.

## What changed

1. **Diets.** `config/diets.json` lists, per lens, the package sections and `## ` blocks it reads, each
   with a byte cap. `scripts/build_agent_package.py` (`build_diets`) writes `<run>/01_diets/<agent>.md`
   next to `01_filtered.md` and reports the bytes in `01_package_report.json → diets`. X picks go
   through the same 72 h and engagement ranking as the shared view.
2. **Takes read the diet.** `orchestrator.take_prompt` starts with `agent_block(agent)`: the same frame
   as `shared()` (sector context, history, scorecard) around the agent's own reading. Triage, synthesis,
   the crux search and every evidence check still use the shared view and the full package, so quotes
   are verified as before. `RECON_DIETS=0`, or a run with no `01_diets/`, gives the old shared prompt.
   The takes no longer share a cached prefix; each prompt is about a third of the old size.
3. **Lens extras stay** (`LENS_RAW`, unchanged and still equal to the probe), minus the lines the diet
   already carries (`debate.minus_view`). `lens_quote_share` now counts quotes from the agent's diet or
   its extras.
4. **take.md.** Answer from your lens and convictions. A question outside your sources gets the number
   your convictions imply, a reason starting "Outside my sources:" and no evidence. Such a position has
   no data quote, so the pairing rules never make it a debate endpoint.
5. **Personas** (`personas/*.md`, the nine lenses): each has standing convictions it defends ("What you
   believe"), what it reads and skips (matching its diet), how it argues, a deliberate blind spot, and no
   heading-based output format (the debate format already overrides it; headings were a leakage source).
   `user_agent` is now a Seoul office worker who uses AI at work and holds some crypto (it covers the
   Korea and AI-education questions in `DOMAIN_LENSES`).

## Diets (bytes on the fixtures, 09-11 / 10-04; shared view ~60 KB)

| lens | reads | skips | 09-11 | 10-04 |
|---|---|---|---|---|
| trader | prices, DEX volume, derivatives, Polymarket, stablecoin supply, prediction markets, market signals, trading Reddit, a little X, crypto headlines | AI, Korea, policy, fundraising | 12.3 K | 12.1 K |
| narrator | sentiment report, Reddit, X, cross-source, crypto headlines | on-chain tables, fundraising, filings | 29.1 K | 30.1 K |
| builder | AI & tools (GitHub, HN, changelogs), AI/tech news, rounds, fees and DEX volume, AI and chains Reddit | macro, geopolitics, sentiment | 18.5 K | 15.1 K |
| analyst | TVL, fees, stablecoins, PM protocols, fundraising, cross-source, crypto headlines, econ calendar | social, sentiment | 17.9 K | 18.1 K |
| skeptic | sentiment narrative/divergence/risk flags, X hype, volumes/fees/derivatives/stablecoins, cyber and regulatory actions, crypto headlines | launch posts, fundraising | 20.5 K | 21.0 K |
| policy_analyst | geopolitics and regulatory actions, Korea crypto and AI news, ZDNet Korea, politics Reddit | prices, GitHub, rounds | 17.1 K | 14.3 K |
| user_agent | AI education, Korea AI, ZDNet Korea, Reddit, a little X, HN | derivatives, macro, rounds, filings | 19.6 K | 20.2 K |
| macro_strategist | World Monitor, prediction markets, macro Reddit, key prices and stablecoins, crypto headlines | GitHub, launches, sentiment | 13.0 K | 9.3 K |
| ai_engineer | AI newsletters, AI/tech news, AI & tools, AI Reddit, AI rounds | crypto prices, macro, regulation | 20.5 K | 17.9 K |

Prediction markets go to trader and macro only, and Polymarket's live-markets block is out of every other
on-chain diet, so odds anchoring (the 9 of 9 at 60 % of the first probe) is limited to two lenses.

Tests: `tests/test_diets.py` (config covers all nine, personas name what they read, every diet > 4 KB and
< 60 % of the view, sections follow the config, no two lenses share > 60 % of their lines, `minus_view`).
`tests/diet_probe.py` prints the table above.

## Validation

Same bar as Phase C §15.5: replay 09-11 and 10-04 on the droplet with diets on, compare with the last
replays (c14, c15) on take spread (questions with a gap ≥ GAP_MIN), pairs staged, crux hits, citation
overlap, outside-my-sources share, and calls/tokens. Debate stays gated off in the daily run until this
passes.
