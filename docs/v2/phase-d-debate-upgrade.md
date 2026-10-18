# Phase D: debate upgrade (2026-10-18)

Eric: "upgrade the debate structure for the agents". This replaces the Phase C crux-gated debate with a
structure that runs in production. Design by a planning pass over the code and spec (2026-10-18), reviewed here.

## Why the Phase C debate never ran

- Cron runs with `RECON_DEBATE=0` until the spread gate and the validation runs pass (phase-c-spec §0.1, §18).
  They never passed. Days 2026-10-05..18: 11 of 14 were `split_unpaired` (2 to 35 candidate pairs, ranges
  like 25-92 and 23-86) and 3 were consensus. Real splits exist almost every day and are never argued.
- With the debate on, the crux gate fails by construction, not by tuning: `gate_move` (debate.py:2151-2163)
  never lets a line either side already quoted count as evidence, so being persuaded by the opponent's verified
  quote, the normal way a debate moves anyone, is forbidden (model log row 24: "the corpus has no unquoted
  line"). Dozens of crux_terms / entity_backed / resolution_words commits could not fix that.

## New structure

| Part | Verdict | What it is |
|---|---|---|
| 1. Round-0 takes | keep | Independent record. `split_sheet` counts come from `take_p`, not `fin(qid)` (debate.py:2832), so drift toward each other cannot erase the split from the brief |
| 2. Delphi round | new | One call per agent over all its split questions (≤6 calls), cross-exam endpoints excluded. Shown: the anonymised distribution plus the two strongest opposing reasons, picked in code (the 2 agents across the median with most verified quotes, `ev_checked`). Hold is the default. Each revision states `would_change_my_mind` as an indicator |
| 3. Cross-exam | new, reuses the 4-calls-per-pair shape | Top 1-2 splits from `pair()` (660) / `eligible_agents` (575). Each extreme steelmans, names `i_am_wrong_if` as a resolvable dated indicator, rebuts; the reply accepts / rejects / counters the other's indicator, then holds or moves |
| 4. Judge | changed | Evidence quality scored in code from verified/partial/unverified counts (no call). One neutral call turns the indicators into checkable form: resolvable or not, does today's data lean either way, the WHAT TO WATCH and WHERE THE VIEWS SPLIT lines. Its quote is re-checked as `ph_cruxcheck` does (1469-1473). Moves are labelled (verified quote / argument / held), never blocked |
| 5. Pre-mortem | keep, rewritten | `pick_red_team` (596) stays; `red_team.md` becomes a pre-mortem ("it is the resolution date and the consensus was wrong: why?") with 1-3 early-sign indicators. Every consensus day; on split days the highest-weight non-split question when the budget allows |
| 6. Calibration | keep, phased | Resolve ledger questions, Brier per agent and for round 0 vs post-debate (mean, not median: outliers moving in rarely shift the median). Agent weights only after ≥40 resolved questions, shrunk and clipped 0.5-2x, plain mean published beside. Production gate: a component (Delphi, cross-exam, scored separately) turns off when its mean ΔBrier > 0 with paired-bootstrap P > 0.8, counting only questions it changed |

## Budget and order

- Baseline: triage 1 + takes 9 + synthesis 2 = 12 calls.
- Split day: + cross-exam 2 pairs x 4 + Delphi ≤6 + judge 1 + pre-mortem ≤1 + resolver 1 (FAST) = 29 (ceiling 32). Quiet depth: 1 pair.
- Consensus day: 12 + pre-mortem 1 + Delphi on the widest question ≤3 + resolver 1 = ≤17.
- Order: score, collect, context, package, **resolve**, triage, takes, pairing, [cross-exam A ∥ Delphi],
  cross-exam B, judge + pre-mortem, positions, split, memory, synthesis, checks, deliver, record. About +6 min
  at PARALLEL 5 (to be measured; bar ≤ 20 min).

## Schemas (Codex strict: every field required, `nullable()` where needed)

- `INDICATOR`: `{observable, source, comparator: >|>=|<|<=|happens|does_not_happen, level, by_date, if_true: yes_more_likely|no_more_likely}`
- `DELPHI`: `{revisions:[{question_id, verdict: hold|revise, new_probability, moved_by: reason_a|reason_b|own_recheck|none, reason ≤40w, evidence[0-2], would_change_my_mind: INDICATOR}]}`
- `XEXAM_OPEN` (replaces DEBATE_CHALLENGE, schemas.py:130): `{question_id, steelman, i_am_wrong_if: INDICATOR, rebuttal ≤120w, evidence[0-3], probability}`
- `XEXAM_REPLY` (replaces DEBATE_RESPONSE, :144): `{question_id, steelman_fair{verdict, correction}, on_their_indicator{verdict: accept|reject|counter, why, counter: nullable(INDICATOR)}, verdict, new_probability, reason ≤60w, new_evidence[0-2]}`
- `JUDGE` (replaces CRUX_CHECK, :164): `{items:[{question_id, indicator: INDICATOR, status: agreed|countered|rejected, resolvable, why_not, data_today: favours_high|favours_low|neither|no_data, quote, section, watch_line ≤25w, split_line ≤40w}]}`
- `PREMORTEM` (replaces RED_TEAM, :154): `{question_id, consensus_view, failure_story ≤150w, early_signs:[INDICATOR] 1-3, evidence[0-3], probability}`
- Artifacts: `XEXAM_SCORE {question_id, high, low, gap_before, gap_after, moves[{agent, take, new, label}], evidence{high/low:{claimed, verified, partial, unverified, data}}, indicators, judge, held_split}`;
  `LEDGER_LINE.type` grows to question|indicator|resolution (today :275 allows only "question");
  `RESOLUTION_LINE {ledger_id, outcome: yes|no|void, value, source, quote, method: api|package_llm|manual, resolved_at}`.

## Reuse and retire

- Reuse from debate.py: `norm_p`, `anonymise`, `excerpts`, `final_positions`, `split_sheet` (adapter),
  `question_stats`, `count_phrase`; `market_match` and `settled_line` (the triage gate, §2.4, not move gating);
  `Locator.locate` and `strict`.
- Retire behind `RECON_CRUX_SEARCH=0`, then delete with their tests after two green weeks: `gate_move`,
  `cap_pair`, `pair_allowance`, `qualifying_lines`, `quote_qualifies`, `crux_search`, `crux_terms`,
  `crux_keywords`, `resolution_words`, `entity_backed`, `drop_frequent_entities`, `score_debate`'s
  closed-on-data logic (orchestrator.py 1254-1356, 1372-1493). crux_search may live on only as retrieval of
  candidate lines for the judge, never as a gate.

## Calibration today

- There: every ledger line has `take_values` (round 0) and `finals` per agent, kind, metric, threshold,
  resolves_on; §2.6 defines the resolution line; `open_ledger` (orchestrator.py:608) skips resolved questions.
- Missing: nothing writes resolution lines; `score_yesterday.py` does not read the ledger (it regex-scrapes
  PREDICTIONS from state files); no Brier code; `replay_report` prints "hindsight Brier: pending".

## Build steps (small commits)

1. `scripts/resolve_ledger.py`: threshold/direction questions via CoinGecko, DeFiLlama, Fear & Greed
   (`config/resolvers.json` map); event questions by 1 FAST call over the day's package that must return a
   verified quote, else the question stays open; void 7 days after resolves_on. Fixture tests.
2. `scripts/brier_report.py` (per agent, mean and median, round 0 vs post), wired into the score phase and
   SCORECARD; baseline over the droplet's runs so far.
3. Schemas + `tests/test_schemas.py`; the dry-run stubs (`_dry_json`) cover them.
4. Templates `xexam_open.md`, `xexam_reply.md`, `delphi.md`, `judge.md`, rewritten `red_team.md` + `test_prompts.py`.
5. Pure functions in debate.py with unit tests: `delphi_targets` (IQR, endpoints excluded), `opposing_reasons`,
   `label_move`, `xexam_score`, `indicator_ok` (date and comparator check).
6. Orchestrator phases + budget planning; dry run end to end.
7. Split-sheet adapter: counts from takes, crux and settles_on from the judge / indicators.
8. Crux search behind the flag.

## Validation, then on

- `launch_run.py` replays on the latest 3 `split_unpaired` cron days + 1 consensus day. Pass bar: calls ≤ 30
  and ≤ 20 min; every top split ends with an indicator the judge marks resolvable; evidence verification ≥ 80%;
  0 agent names in the brief; the round-0 split still shows in the brief; the share of revisers who moved on
  argument alone is reported.
- Then 2 tagged live runs, then `RECON_DEBATE=1` in the env file. The calibration gate replaces the §0.1/§18
  spread gate; the spec is updated.

## Risks

Herding toward the median (round-0 records, symmetric cross-exam, measurement); slow evidence (20 changed
questions may take 2-3 weeks); free-text metrics some resolvers cannot read; Codex concurrency pushing wall
time past 20 min; a biased judge (it never decides moves or a winner).
