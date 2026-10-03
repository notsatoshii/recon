# RECON v2 — Phase C build spec: the debate redesign

Date: 2026-10-04. Status: build spec, written against Phase B as committed on `v2` (`f8991d5`,
`774afc5`). Source plan: `06-improvement-plan.md` §2.2 (F6–F16), §3 (redesign), §4, §7 row C,
§8 decisions. Measurements used: `model-log.md` (slim Codex prefix ~2.2 K tokens, resume only
caches).

Eric's standing instruction for this work: decide, do not ask. Every product choice the plan left
open is decided here and listed in §19 so it can be reversed in one place.

---

## 0. Scope

Phase C replaces the middle of the orchestrator (`recon/orchestrator.py`) from the fixed
`TENSIONS` challenges to the vote, and changes what the synthesizer reads about the debate.
It keeps everything Phase B built around it.

| Kept from Phase B, unchanged | Replaced or extended in Phase C |
|---|---|
| `score`, `collect`, `context`, `package` phases | `triage`: question shape, count rule, gate, carry-over, question ledger (§2) |
| `Run.call()` (schema, one re-ask), `Run.parallel()`, artifacts, `--from-phase`/`--resume` | `takes`: lens raw extras appended after the shared block; TAKE schema unchanged (§3) |
| `shared()` byte-identical prefix for triage and takes (provider cache) | `challenges`: fixed `TENSIONS` + wildcard → pairs by widest gap, steelman-then-rebut (§4, §5) |
| `evidence.Corpus.verify_quote`, `brief_claims`, `url_check`, `repeated_numbers`, `citation_overlap` | new programmatic crux search feeding the responses (§6) |
| `agentmem` file formats | `responses`: one per debate side, evidence gate on moves, no vote (§7) |
| `ph_checks`, `ph_deliver`, `ph_record` skeletons | `deepdive` → `cruxcheck`: 0–1 referee call that adds facts (§8) |
| dry-run provider, slim Codex, `PARALLEL=5` | `positions`: scoring (§9), final positions = the vote (§10), split sheet (§11) |
| | `synthesis`: split sheet + lens notes instead of the full record; THE CONTRARIAN CASE → WHERE THE VIEWS SPLIT (§11.4) |

Boundary with Phase D: C delivers the split sheet into the draft call and swaps that one
heading, because the replays (§15) must be judged on the brief. Phase D still owns the
programmatic claims check, the computed SCORECARD, removing the full-rewrite filter, the
Lever/InnovLabs lines, and the question resolver that writes outcomes into the ledger (§2.6).

Boundary with the bash pipeline: `scripts/run_recon.sh` is not touched. Cron keeps running bash
until the cutover in §18.

---

## 1. Flow and artifacts

```
phase        calls                     artifact (briefs/<run>/phases/)
triage       1 FAST, schema            triage.json            questions of the day, gated (§2)
takes        6–9 ANALYST, schema       takes/<agent>.json     unchanged shape (§3)
pairing      0                         pairing.json           pairs or consensus + red-team pick (§4)
challenges   2 per pair, or 1 red team challenges/<a>__<b>__<q>.json, challenges/redteam__<a>__<q>.json
responses    2 per pair, 0 on consensus responses/<a>__<q>.json; cruxsearch.json written first (§6)
cruxcheck    0–1 ANALYST, schema       cruxcheck.json         (§8)
positions    0                         positions.json         finals, stats, scores, flags (§9, §10)
split        0                         split_sheet.json + 07_split_sheet.md (§11)
memory       0                         memory.json            (agentmem via an adapter, §14)
synthesis    2 SYNTH (draft, filter)   synthesis.json         (§11.4)
checks … record                        as Phase B, plus the new checks (§11.5) and run.json fields (§12.3)
```

`PHASES` becomes:

```python
PHASES = ["score", "collect", "context", "package", "triage", "takes", "pairing", "challenges",
          "responses", "cruxcheck", "positions", "split", "memory", "synthesis", "checks",
          "deliver", "record"]
ITEM_DIRS = {"takes", "challenges", "responses"}
PHASE_ALIASES = {"deepdive": "cruxcheck"}   # --from-phase deepdive still works, with a log line
```

Item file names carry the question id because one agent can sit in two debates (§4.3).

---

## 2. Q — Questions of the day

### 2.1 Who writes them

The triage call (FAST tier, as in Phase B) writes them. It is the first call of the day, starts
with the same byte-identical `shared()` block as the takes, and now also reads:

- the open questions from the ledger (§2.6): not resolved, `resolves_on` ≥ today, newest 8;
- predictions and questions whose `resolves_on` is today or tomorrow (from the ledger and each
  agent's state file);
- yesterday's `novel` items (one per agent, from yesterday's `takes/*.json` if the run folder
  exists, else empty).

Nobody else writes questions. Agents do not propose questions inside the take (that would make
the take call depend on itself). Eric does not write them.

One experiment in the replays (§15.4): the 2026-09-11 triage is run once on FAST and once on
ANALYST, and the question gate pass rate and the number of real splits are compared. If ANALYST
gives at least one more real split or a gate pass rate 25 points higher, triage moves to ANALYST
(`RECON_TRIAGE_TIER=analyst`, default `fast`).

### 2.2 How many

By the depth triage sets in the same call:

| depth | questions kept | max debate pairs |
|---|---|---|
| quiet | 3 | 1 |
| normal | 4 | 3 |
| risk | 5 | 3 |

Triage is asked for `n_max = 5` every day; the program keeps the top `n` by `weight`, then by
domain coverage (at least one `markets_crypto`, one `macro_policy`, one `ai_product` while any
are available), then by order.

### 2.3 Shape

One question (schema in §12.1, `QUESTION`):

| field | meaning |
|---|---|
| `id` | `q1`…`q5`, reassigned by the program after the gate |
| `text` | a yes/no question, ≤ 200 characters, ends with `?` |
| `kind` | `threshold` (a number above/below a level on a date), `event` (a dated event happens or not), `direction` (a series up or down over a window), `judgment` (no clean data; at most one per day) |
| `domain` | `markets_crypto`, `macro_policy`, `ai_product`, `korea`, `prediction_markets` |
| `metric` | the series, e.g. `total DeFi TVL (DeFiLlama)`, `BTC/USD (CoinGecko)`; empty for `event`/`judgment` |
| `comparator` | `>`, `>=`, `<`, `<=`, or empty |
| `threshold` | the level as written, e.g. `86.61B`; empty when not a threshold |
| `baseline_quote` | the package line that gives today's value, verbatim; empty for `judgment` |
| `resolves_on` | `YYYY-MM-DD`, 1–30 days after the run day; empty only for `judgment` |
| `settles_with` | the observable and its source |
| `lenses` | 2–5 agents best placed to answer |
| `weight` | 1–3; 3 = it changes what a reader in crypto, prediction markets or AI education does this week |
| `carried_from` | ledger id (`2026-09-11-q2`) when re-asked, else empty |

Every active agent answers every question (Phase B's take prompt already says so). `lenses`
only weights pairing (§4.2) and seeds the take prompt's lens extras (§3).

### 2.4 The gate (programmatic, no LLM)

Run on the triage reply before `triage.json` is saved. A question is dropped, with the reason
recorded under `triage.json → gate.dropped[]`, when:

1. `text` is empty, longer than 220 characters, or is not a yes/no question (heuristic: ends with
   `?` and does not start with how/what/which/why/when/where/who — Korean equivalents
   어떻게/무엇/왜/언제/누가 included);
2. `kind != judgment` and `resolves_on` is not a valid date 1–30 days after the run day;
3. a second `judgment` question (keep the higher weight);
4. `kind in (threshold, direction)` and `baseline_quote` does not verify (`verify_quote` status
   `verified` or `partial`) against `00_data_package.md` + `00_raw_data.md`;
5. word-set Jaccard ≥ 0.6 with a question kept earlier today, or with an open ledger question
   while `carried_from` is empty (a silent repeat);
6. more than 2 questions have `carried_from` set (keep the higher weight).

`lenses` is cleaned to valid active agents; if fewer than 2 remain it is filled from the
domain's default lenses (`DOMAIN_LENSES` in `recon/debate.py`: markets_crypto → trader, analyst;
macro_policy → macro_strategist, policy_analyst; ai_product → ai_engineer, builder; korea →
policy_analyst, user_agent; prediction_markets → trader, skeptic).

If fewer than 3 questions survive, triage is asked once more (same call, plus the gate's reasons).
After that the run goes on with whatever survived; with 0 questions the day has no debate and no
split sheet (Phase B's soft-failure path), and the brief section says so (§11.4).

### 2.5 Carry-over

A carried question keeps its lineage: the ledger line for today gets `carried_from`, and the
split sheet can say "unchanged since 09-11 (median 64% → 61%)". A carried question still has to
pass the gate with today's baseline quote.

### 2.6 The question ledger

`config/questions/ledger.jsonl` (gitignored, like `knowledge.db`), one line per question per run,
appended by the `split` phase (§11) so it carries the final stats. Dry and replay runs write to
`<run>/state/questions/ledger.jsonl`. Schema `LEDGER_LINE` (§12.3). Lines are never edited;
the Phase D resolver appends `{"type": "resolution", "ledger_id", "outcome": "yes|no|void",
"value", "source", "resolved_at"}` lines to the same file. Brier scores (Phase F) join on
`ledger_id`.

---

## 3. T — Takes

The TAKE schema (`schemas.TAKE`) and the take task text stay as Phase B wrote them. Two changes:

1. **Lens extras.** After the shared block (so the cached prefix stays byte-identical), each agent
   gets up to 6 KB of its own lens's raw material from `00_raw_data.md`, chosen by a static map
   `LENS_RAW` in `recon/debate.py` (heading prefixes in the raw file):

   | agent | raw headings (first match wins, each capped, 6 KB total) |
   |---|---|
   | trader | `## MARKET OVERVIEW`, `## DEX VOLUMES`, `## POLYMARKET` |
   | narrator | Reddit (`# Reddit Intelligence`), X (`# Twitter/X Intelligence`) |
   | builder | `## GITHUB TRENDING`, `## HACKER NEWS`, `# Changelogs Intelligence` (Phase E) |
   | analyst | `## CHAIN TVLs`, `## PREDICTION MARKET PROTOCOLS`, `## STABLECOINS` |
   | skeptic | `## RECENT HACKS`, `## STABLECOINS`, `# BettaFish` |
   | policy_analyst | `## KOREA — CRYPTO & MARKETS`, news items matching regulat\|SEC\|CFTC\|ESMA\|CLARITY\|금융위 |
   | user_agent | Reddit, `## KOREA — AI` |
   | macro_strategist | `# World Monitor`, `## ECONOMIC CALENDAR` |
   | ai_engineer | `## AI NEWSLETTER SOURCES`, `## GITHUB TRENDING`, `# Changelogs Intelligence` |

   Headings that do not exist in a package are skipped silently; the bytes actually added are
   recorded in `takes/<agent>.json → fed.lens_extra_bytes`. The point (F1, F6) is that lenses stop
   starting from the identical text; `citation_overlap` before/after is a replay metric.
2. **Evidence class.** Not a prompt change. Every evidence quote gets a `class` when it is
   verified: `data` when the quote is found in a section other than SENTIMENT & MARKET MOOD or
   SOCIAL INTELLIGENCE (or in `00_raw_data.md` outside the Reddit/X/BettaFish blocks), else
   `social`. A quote found in both is `data`. New helper `evidence.locate(quote, pkg_text,
   raw_text) -> {status, section, class}` built on `verify_quote` and the section split already in
   `ph_record.cite_section`. This is what stops F13: a position resting only on "per social media"
   can be argued about but cannot open a debate (§4.1) or justify a move above 10 points (§7.2).

The take is verified once, right after the takes phase, and the results are cached in
`positions_evidence` inside `pairing.json` so pairing does not re-run the check.

---

## 4. P — Pairing by widest probability gap (no LLM)

### 4.1 Inputs and eligibility

- `p[a][q]`: each agent's take probability (Phase B `pos_map`, fractions fixed).
- `ev[a][q]`: that position's evidence after §3 verification.
- An agent is **eligible** on `q` when it has a probability on `q` and at least one evidence
  quote on `q` with status `verified`/`partial` and class `data`. Ineligible agents still count in
  the median and the final stats; they just cannot be a debate endpoint.
- `yesterday_pairs`: unordered pairs from yesterday's `pairing.json` (same `briefs/` root, the
  previous date folder; absent on replays and the first run).

### 4.2 Algorithm

```python
GAP_MIN = int(env("RECON_PAIR_GAP", 20))
target  = {"quiet": 1, "normal": 3, "risk": 3}[depth]

cands = []
for q in questions:
    vals = [p[a][q] for a in active if q in p[a]]
    if len(vals) < 3: continue
    med = median(vals)
    for a, b in combinations(sorted(eligible(q)), 2):
        lo, hi = sorted((a, b), key=lambda x: (p[x][q], x))
        gap = p[hi][q] - p[lo][q]
        if gap < GAP_MIN: continue
        if not (p[lo][q] <= med <= p[hi][q]): continue      # must straddle the median
        score = gap * q.weight
        if a in q.lenses and b in q.lenses: score *= 1.15
        if frozenset((a, b)) in yesterday_pairs: score -= 10
        if quotes(a, q) == quotes(b, q): score -= 5          # same evidence only, read differently
        cands.append((score, gap, verified_count(a, q) + verified_count(b, q), q.id, lo, hi))

cands.sort(key=lambda c: (-c[0], -c[1], -c[2], c[3], c[4], c[5]))   # deterministic
pairs, used_q, load = [], set(), Counter()
for cap in (1, 2):                                       # second pass only if short of target
    for c in cands:
        if len(pairs) == target: break
        if c[3] in used_q or load[c[4]] >= cap or load[c[5]] >= cap: continue
        pairs.append(c); used_q.add(c[3]); load[c[4]] += 1; load[c[5]] += 1
```

Rules in words: one pair per question; at most one debate per agent unless the day would
otherwise fall short of its target, then two; pairs straddle the median, so each debate is about
the consensus, not about two degrees of the same view; pairs prefer the extremes because the
score is the gap. Both sides of a pair challenge each other (2 calls), so direction does not
matter; the record labels them `high` and `low`.

### 4.3 Consensus day and the red team

When `cands` is empty the day is a **consensus day**. No debate is staged. Instead:

- the **top question** is the one with the highest `weight`, then the widest range;
- the **red-team agent** is the eligible agent furthest from that question's median, if that
  distance is ≥ 10 points; otherwise `skeptic` if active; otherwise the agent with the most
  verified data evidence on that question;
- one ANALYST call (template `red_team.md`, schema `RED_TEAM`) argues the strongest case against
  the median's side, using the same excerpt rules as a challenge (§5.2).

The red team's output feeds the split sheet as the consensus-day block (§11.2) and does not move
any probability. On a day with 0 questions there is no pairing, no red team and no split block.

`pairing.json` schema: `PAIRING` (§12.3).

---

## 5. C — Challenge: steelman, then rebut

### 5.1 Who and when

Both sides of every pair, in parallel (one `Run.parallel` batch for all pairs). Up to 6 calls.

### 5.2 Input (template `challenge.md`)

- the persona (Phase B's `ROLE` block) and `DEBATE_FORMAT`, extended (§13.1);
- the question with `resolves_on` and `settles_with`;
- own position: probability, reason, evidence quotes; the other side's position, reason,
  evidence and the other agent's `summary` (≤ 50 words, already in the TAKE);
- **excerpts**: for every `verified`/`partial` quote of both sides on this question, the package
  lines around it (the quote's line ± 3 lines, merged when they overlap), capped at 4 KB total,
  shared out equally between the two sides. Unverified quotes are listed as "(not found in the
  package)" so neither side builds on them.

Not given: the full package, either take's full text, memory, state. The challenge is about one
question, and the prompt must stay small (cost, §16).

### 5.3 Output (schema `DEBATE_CHALLENGE`, §12.2)

`steelman` (≤ 2 sentences, the opponent's best case in terms they would accept), `crux`
(`claim`, `type` factual|causal|definitional|timing, `observable`, `by_date`), `rebuttal`
(≤ 120 words), `evidence` (0–3 quotes from the excerpts), `would_change_my_mind`
(`observable`, `level`, `by_date`).

### 5.4 Programmatic checks on each challenge (no LLM)

Recorded under `challenges/<file>.json → checks`:

- word counts (`rebuttal` > 140 words or `steelman` > 3 sentences → flag `over length`;
  kept, not cut);
- **persona leakage**: regex `ROADMAP|BUILD NOW|content angle|Post now|allocation:|portfolio:|^#+ `
  on every text field → flag `persona leakage` (F9);
- **agreement opener**: rebuttal's first 12 words match
  `\b(correctly|is right|rightly|sound|agree|aligned|fair point)\b` → flag `opens by agreeing`
  (F8; counted, not blocked);
- evidence verified with `evidence.locate` (§3);
- names: any other agent's name in the text is replaced by "the other view" in the copy that
  goes to the responder and to the split sheet (the raw reply is kept as written).

A challenge that fails the schema twice fails that side only. If one side of a pair fails, the
other side's challenge still goes to its target; the pair is marked `one-sided`.

---

## 6. Crux search (programmatic, between C and R)

Written to `phases/cruxsearch.json` at the start of the responses phase (so `--from-phase
responses` re-runs it).

For each pair, terms come from both challenges' `crux.claim`, `crux.observable` and
`would_change_my_mind.observable`:

- numbers (`evidence.numbers`), matched with the `NumberIndex` tolerance;
- entities: tokens with a capital letter or digits, length ≥ 3, not in a stop list
  (`The`, `This`, `If`, weekday and month names…), plus Hangul words of ≥ 2 syllables;
- metric words from a fixed list: tvl, volume, liquidity, price, yield, apy, funding, open
  interest, inflow, outflow, spread, depth, market cap, dominance, supply, peg, 거래대금, 시가총액.

Corpus, run folder only (replays must not read today's `data-sources/`): `00_raw_data.md`,
`00_data_package.md`, `01_social.md`. Each line scores `3 × entities + 2 × numbers + 1 × metric
words`; a line is a hit when it scores ≥ 4 and matches at least two distinct terms. Lines already
quoted by either side are excluded (the point is facts neither side used). Top 12 hits by score,
each with its section, class and ± 1 line of context; 3 KB block for the responders, 5 KB for the
referee (§8).

Zero hits is a normal outcome and is recorded as such.

---

## 7. R — Response with the evidence gate

### 7.1 Who and input (template `response.md`)

Each side answers the challenge it received, in parallel. Input: the persona and
`DEBATE_FORMAT`; the question; its own position (probability, reason, evidence); the challenge
it received (steelman of itself, crux, rebuttal, evidence, would-change-my-mind) with names
replaced; the excerpts from §5.2; the **crux data block** from §6 ("data on disk about this
crux that neither of you quoted"). No vote.

### 7.2 Output and the gate

Schema `DEBATE_RESPONSE` (§12.2): `steelman_fair` (`yes|partly|no` + `correction`), `crux_agreed`
(`yes|no` + `own_crux` when no), `verdict` (`hold|narrow|concede`), `new_probability`, `reason`
(≤ 60 words), `new_evidence` (0–3 quotes, verbatim).

The gate, applied by code in the responses phase; the agent's **gated** probability is what
counts everywhere downstream:

```python
FREE_MOVE = 10                                  # points an agent may move on argument alone
d = new_p - take_p
new_ev = [e for e in resp.new_evidence
          if locate(e).status in ("verified", "partial")
          and norm(e.quote) not in own_take_quotes(agent, q)]      # the challenger's quotes count as new
data_new = [e for e in new_ev if e.cls == "data"]

if abs(d) <= FREE_MOVE:              gated = new_p
elif data_new:                       gated = new_p
else:                                gated = take_p + copysign(FREE_MOVE, d)
                                     flag("social evidence only, capped" if new_ev else "update without evidence, capped")
gated = min(100, max(0, gated))
```

Further flags (recorded, never blocking):

- `verbal concession`: verdict `concede` and |gated − take_p| < 5 (F10: conceding by politeness);
- `hold but moved`: verdict `hold` and |d| > 10;
- `moved away`: verdict `narrow`/`concede` but the move goes away from the opponent's take value;
- `rejected steelman`: `steelman_fair == "no"` (the challenger failed to state this side fairly;
  scored against the challenger, §9).

The words "DEFEND or CONCEDE" do not appear anywhere. Positions on questions the agent did not
debate do not move (no new information reached it), which removes Phase B's `final_positions`
array and its "update on everything" noise.

---

## 8. X — Crux check (replaces the deep dive)

After the responses. Programmatic pick, then 0 or 1 ANALYST call (template `crux_check.md`,
schema `CRUX_CHECK`, no persona: the referee is neutral).

- **Pick**: for each debate, `gap_after = |gated_high − gated_low|`. Take the debate with the
  largest `gap_after`, if it is ≥ 15 points (`RECON_CRUX_GAP`) and its crux search has ≥ 1 hit.
  On a consensus day the red team's crux is the candidate, with gap = |red team probability −
  median|. Otherwise no call; `cruxcheck.json` says why.
- **Input**: the question, both cruxes (or the agreed one), both gated positions with reasons, the
  5 KB hit block, the excerpts from §5.2.
- **Output**: `resolved` (`yes|no|partly`), `what_the_data_says` (≤ 60 words), `quote` + `section`
  (verbatim), `remaining_uncertainty` (≤ 40 words), `settles_on` (`observable`, `by_date`),
  `leans` (`higher|lower|neither`: which side the data favours).
- **Check**: the referee's quote is verified; if it does not verify, `resolved` is forced to `no`
  and the result is flagged `referee quote not found`.

The crux check adds a fact to the split sheet. It changes no probability: a third round of
opinions is what F11 showed to be useless.

---

## 9. S — Scoring (no LLM)

### 9.1 Per debate (`positions.json → debates[]`, schema `DEBATE_SCORE`)

`question_id`, `high`, `low`, `gap_before`, `gap_after`, `moves` (per side: take, requested,
gated, delta, clamped, new data evidence count), `verdicts`, `steelman_fair` (as rated by each
target), `crux_agreed`, `evidence` (claimed vs verified vs data-class per side), `flags` (all of
§5.4 and §7.2), `crux_check` (resolved, leans) when run, and two derived booleans:

- `live_split`: `gap_after ≥ 20` (goes into WHERE THE VIEWS SPLIT);
- `useful`: `live_split` or any side moved ≥ 10 points on new data evidence or the crux check
  resolved `yes`/`partly`. "Useful debates per run" is the headline replay metric.

### 9.2 Per agent per run (`positions.json → agent_scores{}`, schema `AGENT_RUN_SCORE`)

`distinctness` (mean |p − median| over questions, take values), `endpoint_count` (debates as an
endpoint), `evidence_rate` (verified+partial / claimed, all phases), `data_share` (data-class
share of verified quotes), `unique_numbers` (numbers it cited that no other take cited, from
`citation_overlap` sets), `moves_with_evidence`, `moves_capped`, `verbal_concessions`,
`steelmen_rejected` (as challenger), `leakage_flags`, `novel` (text). Appended by code to
`config/agent_scores/<agent>.jsonl` (gitignored; run folder for dry and replay runs).

### 9.3 Over time

Brier per agent and per question needs resolved ledger lines (Phase D resolver, Phase F report).
Phase C only guarantees the inputs: every final probability is in the ledger with `ledger_id`,
agent and value.

---

## 10. V — Final positions (the "vote")

No vote calls. Per agent and question: `final = gated response value` if the agent debated that
question, else the take value. Per question (`positions.json → questions[]`, schema
`QUESTION_STATS`): `n`, `median`, `mean`, `min`, `max`, `range`, `iqr`, `majority_side`
(`yes` when median > 50, `no` when < 50, `even` at 50), `majority_count`, `minority_count`
(agents on the other side of 50; agents at exactly 50 count with neither), `take_stats` (same
fields from take values), `debated` (bool).

This replaces Phase B's `votes` dict. `run.json` keeps an `agents[].vote` key set to `null` so the
RUBRIC page's reader does not break; the page already handles empty votes (F12 days).

---

## 11. The split sheet and how "Where the views split" reaches the brief

### 11.1 Which questions get a block

Programmatic, in the `split` phase. Candidates, ordered by `weight × gap`:

1. debated questions with `live_split` (gap_after ≥ 20);
2. undebated questions with `minority_count ≥ 2` and `range ≥ 40` (possible when more questions
   split than there were pair slots).

At most 3 blocks. A block is a **direction** split when `minority_count ≥ 1`, else a **degree**
split (everyone on one side, but ≥ 30 points apart). A gap under 20 points is never a block (plan
§3.4). With no block and a red team, the sheet holds one **consensus** block. With neither, the
sheet says `no split today`.

### 11.2 Block contents (schema `SPLIT_BLOCK`)

| field | built from |
|---|---|
| `question`, `resolves_on`, `settles_with` | the question |
| `counts` | `{"n": 9, "majority": 7, "minority": 2, "median": 68, "range": [25, 85]}` |
| `count_phrase` | rendered by code, the only form the brief may use: "7 of 9 lenses put it at 60–85%; 2 put it at 25–40%" (direction) or "all 9 lenses lean yes, from 55% to 90%" (degree) |
| `base_case` | the majority debater's rebuttal if a majority-side agent debated it, else the `reason` of the majority agent closest to the majority median with the most verified data evidence; plus one verified data quote |
| `minority_case` | the majority debater's **steelman** of the minority if the minority debater rated it `yes`, else the minority debater's rebuttal, else the most extreme minority agent's reason; plus one verified quote |
| `crux` | the agreed crux (both `crux_agreed == yes`), else the minority side's crux |
| `crux_check` | `resolved`, `what_the_data_says`, `quote` when §8 ran on this question |
| `settles_on` | crux check `settles_on`, else the question's `resolves_on`/`settles_with`, else the earlier of the two `would_change_my_mind` dates |
| `carried` | `unchanged since <date> (median a% → b%)` when the question was carried |
| `type` | `direction`, `degree` or `consensus` |

Consensus block: `question`, `counts`, `count_phrase` ("all 9 lenses within 15 points of 70%"),
`red_team_case` (its `case` and `evidence`), `crux`, `crux_check`, `settles_on`, labelled
`type: consensus` so the brief calls it the red-team case.

Every text field passes the anonymiser: the nine agent names (snake case, title case, upper case,
with or without "the"), "@agent", and lens words used as names ("the Skeptic") become "the other
view" / are dropped; lines matching the §5.4 leakage regex are dropped; each text is cut at a
sentence boundary to fit. The whole sheet is ≤ 6 KB rendered (`07_split_sheet.md`); if over,
quotes are dropped first, then cases are shortened evenly.

### 11.3 Lens notes (what replaces the full record in the draft)

Built in the same phase, ≤ 8 KB, `07_lens_notes.md`. One entry per active agent, **labelled by
lens, never by name** ("markets lens", "risk lens", "AI tools lens"… from `LENS`): its `summary`;
up to 2 claims whose quote verified as `data`; its `novel` item; its prediction with probability
and date. Unverified claims and social-only claims are left out, which is what keeps F13-type
claims out of WHAT IT MEANS.

`07_full_record.md` is still written (archive, RUBRIC, debugging) but is no longer sent to the
synthesizer. Draft input drops from ~50 KB of record to ≤ 14 KB.

### 11.4 The brief (decision 1: counts, no agent names)

- `BRIEF_SECTIONS[3]` changes from `THE CONTRARIAN CASE` to `WHERE THE VIEWS SPLIT`, in the
  orchestrator, the draft prompt, the filter prompt's section list, and the synthesizer persona.
- The draft prompt replaces `{record}` with the fragment `config/prompts/debate/brief_split.md`
  (§13.7), which carries the split sheet and the lens notes and the section rules:
  up to three blocks, each: the question in plain words, the `count_phrase` exactly as given, the
  base case, the minority view, what it turns on, when we will know. Consensus block: "No real
  split today. The strongest case against the consensus:" + the red-team case. No block: one line
  saying the lenses broadly agree today, with the median of the top question.
- The draft's CRITICAL paragraph changes from "do not reference debates … disagreement" to:
  "Never name an agent or describe the process (no debate, challenge, concession, agents, votes).
  Disagreement may be shown only in WHERE THE VIEWS SPLIT and only with the count phrases given."
- `personas/synthesizer.md`: the "Critical Rule: NO AGENT REFERENCES" section keeps the ban on
  names and process words and gains the same one-sentence exception; the bullet "Minority view
  worth noting = frame it as 'the contrarian case is…'" becomes "Splits go in WHERE THE VIEWS
  SPLIT, with the count phrase given."
- WHAT TO WATCH: the prompt tells the draft to take dated `settles_on` items from the split sheet
  first.

### 11.5 Checks before delivery (added to `ph_checks`, no LLM)

- **No agent names**: regex on the brief for the snake-case names (`policy_analyst`, `user_agent`,
  `macro_strategist`, `ai_engineer`) and for case-sensitive whole-word title-case names
  `\b(Trader|Narrator|Builder|Analyst|Skeptic|Policy Analyst|User Agent|Macro Strategist|AI Engineer)\b(?!s)`
  (plurals such as "Analysts expect" pass; a rare false positive such as a job title inside a quoted
  headline is accepted as a warning). Hit → `checks.agent_names[]`; the brief still ships (Phase D
  decides blocking) and the run status becomes `partial`.
- **Counts match**: every `N of M` in WHERE THE VIEWS SPLIT must equal a `majority`/`minority`
  and `n` in the split sheet. Mismatch → `checks.count_mismatch[]`.
- **Blocks ≤ 3**, and a consensus day's section contains "case against".
- **Process words** in the whole brief: `debate|conced|challeng|our agents|the agents|voted` →
  `checks.process_words[]`.

---

## 12. JSON schemas

All in `recon/schemas.py`, using its helpers (`obj` makes every property required and sets
`additionalProperties: false`, as Codex strict mode needs). LLM call schemas go in `ALL` (written
to `phases/schemas/` for `--output-schema`); artifact schemas go in a new `ARTIFACTS` dict, used
only by `validate()` in tests and before `save()`. Artifacts may use nullable types, so
`validate()` gains support for `"type": [..., "null"]`, `"boolean"` and `"integer"` (never sent to
Codex). New helpers:

```python
def b(desc=""): return {"type": "boolean", **({"description": desc} if desc else {})}
def i(desc=""): return {"type": "integer", **({"description": desc} if desc else {})}
def nullable(sch): return {**sch, "type": [sch["type"], "null"]}
def free(desc=""): return {"type": "object", **({"description": desc} if desc else {})}   # free keys, artifacts only
DATE = s("YYYY-MM-DD or empty")
QIDS = ["q1", "q2", "q3", "q4", "q5"]
```

### 12.1 Triage (replaces `TRIAGE`)

```python
QUESTION = obj(
    id=s("q1, q2, ... in order"),
    text=s("a yes/no question about today's package, at most 200 characters, ending with ?"),
    kind=enum(["threshold", "event", "direction", "judgment"]),
    domain=enum(["markets_crypto", "macro_policy", "ai_product", "korea", "prediction_markets"]),
    metric=s("the series that settles it, with its source; empty for event and judgment"),
    comparator=enum([">", ">=", "<", "<=", ""]),
    threshold=s("the level as written, e.g. 86.61B; empty when not a threshold"),
    baseline_quote=s("the package line giving today's value, copied verbatim; empty for judgment"),
    resolves_on=DATE,
    settles_with=s("the observable or event and where it is published"),
    lenses=arr(enum(AGENTS)),
    weight=num("1, 2 or 3"),
    carried_from=s("ledger id such as 2026-09-11-q2 if this re-asks an open question, else empty"),
)
TRIAGE = obj(
    environment=enum(ENVIRONMENTS, "what dominates today's data"),
    depth=enum(["quiet", "normal", "risk"]),
    reason=s("one sentence: why this environment and depth"),
    weight_agents=arr(enum(AGENTS)),
    questions=arr(QUESTION),
)
```

Phase B readers keep working: `id`, `text`, `resolves_on`, `settles_with`, `lenses` are unchanged.

### 12.2 Debate calls

```python
EVIDENCE  # unchanged: {section, quote}

DEBATE_CHALLENGE = obj(
    question_id=enum(QIDS),
    steelman=s("the other view's best case in at most two sentences, in terms it would accept"),
    crux=obj(
        claim=s("the single factual or causal claim the disagreement turns on"),
        type=enum(["factual", "causal", "definitional", "timing"]),
        observable=s("what you would look at to settle it"),
        by_date=DATE,
    ),
    rebuttal=s("at most 120 words, plain prose, no headings"),
    evidence=arr(EVIDENCE),
    would_change_my_mind=obj(observable=s(), level=s("level or event"), by_date=DATE),
)

DEBATE_RESPONSE = obj(
    question_id=enum(QIDS),
    steelman_fair=obj(verdict=enum(["yes", "partly", "no"]), correction=s("empty when yes")),
    crux_agreed=obj(verdict=enum(["yes", "no"]), own_crux=s("empty when yes")),
    verdict=enum(["hold", "narrow", "concede"]),
    new_probability=num("0-100: your probability now"),
    reason=s("at most 60 words; what changed and why, or why nothing did"),
    new_evidence=arr(EVIDENCE),
)

RED_TEAM = obj(
    question_id=enum(QIDS),
    consensus_view=s("the view you argue against, one sentence"),
    case=s("the strongest case against it, at most 150 words"),
    crux=obj(claim=s(), type=enum(["factual", "causal", "definitional", "timing"]),
             observable=s(), by_date=DATE),
    evidence=arr(EVIDENCE),
    probability=num("0-100: your own probability that the answer is yes"),
    would_change_my_mind=obj(observable=s(), level=s(), by_date=DATE),
)

CRUX_CHECK = obj(
    resolved=enum(["yes", "no", "partly"]),
    what_the_data_says=s("at most 60 words"),
    quote=s("verbatim from the data block, at most 200 characters; empty if none"),
    section=s(),
    remaining_uncertainty=s("at most 40 words"),
    settles_on=obj(observable=s(), by_date=DATE),
    leans=enum(["higher", "lower", "neither"]),
)

ALL = {"triage": TRIAGE, "take": TAKE, "debate_challenge": DEBATE_CHALLENGE,
       "debate_response": DEBATE_RESPONSE, "red_team": RED_TEAM, "crux_check": CRUX_CHECK}
```

Phase B's `CHALLENGE`, `RESPONSE`, `DEEP_DIVE` stay in the file under `LEGACY` (readers of old run
folders, `export.py`) and are removed from `ALL`.

The dry-run generator needs two small changes: `enum(QIDS)` fields take a question id present in
the prompt (it already does this for keys named `question_id`, and enum handling must not win
over that), and `RECON_DRY_SPREAD` (`wide` default: probabilities 5–95 by agent hash, as today;
`narrow`: 60 ± 5) so a dry run can exercise both the debate path and the consensus path.

### 12.3 Artifacts (programmatic, `ARTIFACTS`)

```python
EV_CHECKED = obj(section=s(), quote=s(), status=enum(["verified", "partial", "unverified", "empty"]),
                 cls=enum(["data", "social", ""]), doc=nullable(s()))

PAIR = obj(question_id=s(), high=enum(AGENTS), low=enum(AGENTS), p_high=num(), p_low=num(),
           gap=num(), score=num(), both_lenses=b(), repeat_of_yesterday=b())
PAIRING = obj(
    day_type=enum(["debate", "consensus", "no_questions"]),
    depth=enum(["quiet", "normal", "risk"]), target=i(), gap_min=num(),
    pairs=arr(PAIR),
    candidates_considered=i(),
    red_team=nullable(obj(agent=enum(AGENTS), question_id=s(), median=num(), distance=num(),
                          reason=s())),
    eligible=obj(**{q: arr(enum(AGENTS)) for q in QIDS}),   # all five keys, empty arrays allowed
    positions_evidence=obj(**{a: arr(EV_CHECKED) for a in AGENTS}),
)

SIDE_MOVE = obj(agent=enum(AGENTS), take=num(), requested=nullable(num()), gated=num(), delta=num(),
                clamped=b(), new_evidence_verified=i(), new_data_evidence=i())
DEBATE_SCORE = obj(
    question_id=s(), high=enum(AGENTS), low=enum(AGENTS), status=enum(["two-sided", "one-sided", "failed"]),
    gap_before=num(), gap_after=nullable(num()),
    moves=arr(SIDE_MOVE),
    verdicts=obj(high=s(), low=s()),
    steelman_fair=obj(high=s(), low=s()),          # as rated by that side about the other's steelman of it
    crux_agreed=b(),
    evidence=obj(high=obj(claimed=i(), verified=i(), data=i()), low=obj(claimed=i(), verified=i(), data=i())),
    flags=arr(obj(agent=enum(AGENTS), flag=s(), where=enum(["challenge", "response"]))),
    crux_check=nullable(obj(resolved=s(), leans=s())),
    live_split=b(), useful=b(),
)

STATS = obj(n=i(), median=nullable(num()), mean=nullable(num()), min=nullable(num()), max=nullable(num()),
            range=nullable(num()), iqr=nullable(num()), majority_side=enum(["yes", "no", "even", ""]),
            majority_count=i(), minority_count=i())
QUESTION_STATS = obj(id=s(), ledger_id=s(), text=s(), weight=num(), debated=b(),
                     take_stats=STATS, final_stats=STATS, finals=free("{agent: probability}"))

AGENT_RUN_SCORE = obj(
    run_id=s(), day=DATE, agent=enum(AGENTS), distinctness=nullable(num()), endpoint_count=i(),
    evidence_rate=nullable(num()), data_share=nullable(num()), unique_numbers=i(),
    moves_with_evidence=i(), moves_capped=i(), verbal_concessions=i(), steelmen_rejected=i(),
    leakage_flags=i(), novel=s(),
)

SPLIT_BLOCK = obj(
    type=enum(["direction", "degree", "consensus"]), question_id=s(), ledger_id=s(),
    question=s(), resolves_on=DATE, settles_with=s(),
    counts=obj(n=i(), majority=i(), minority=i(), median=num(), range=arr(num())),
    count_phrase=s(),
    base_case=obj(text=s(), quote=s()),
    minority_case=obj(text=s(), quote=s(), source=enum(["steelman", "rebuttal", "reason", "red_team"])),
    crux=s(), crux_check=nullable(obj(resolved=s(), what_the_data_says=s(), quote=s())),
    settles_on=obj(observable=s(), by_date=DATE),
    carried=s(),
)
SPLIT_SHEET = obj(day=DATE, run_id=s(), day_type=enum(["debate", "consensus", "no_questions"]),
                  blocks=arr(SPLIT_BLOCK), no_split_line=s(), bytes=i())

LEDGER_LINE = obj(
    type=enum(["question"]), ledger_id=s("<day>-<qid>"), run_id=s(), day=DATE,
    question=QUESTION,                                 # as gated
    finals=free("{agent: gated probability}"), take_values=free("{agent: take probability}"),
    final_stats=STATS, debated=b(), split_type=enum(["direction", "degree", "consensus", "none"]),
)
```

`run.json` (`schema_version` 1 → 2) gains: `pairing` (as above, without `positions_evidence`),
`debates` (list of `DEBATE_SCORE`, replacing Phase B's shorter `debates`), `red_team`,
`crux_check`, `split_sheet`, `agent_scores`, and `questions[]` items gain `ledger_id`,
`final_stats.majority_count`/`minority_count`. `edges` use types `pair` (both directions) and
`redteam`; `deep_dive` is `null`. The RUBRIC recon page maps unknown edge types to a plain edge
until Phase F styles them (to check in the RUBRIC repo when Phase F starts).

---

## 13. Prompt templates (`config/prompts/debate/`)

### 13.1 Renderer

`recon/prompts.py`:

```python
def render(name: str, **vars) -> str:
    """Load config/prompts/debate/<name>.md, drop the leading <!-- ... --> header, replace
    {{key}} placeholders. Raises KeyError on a placeholder without a value and ValueError on a
    value the template does not use, so code and templates cannot drift silently."""
```

`{{…}}` is used instead of `str.format` because templates contain JSON-like braces. Each file
starts with a comment header naming its schema and inputs. The persona block and the citation
rule are passed in as variables (`role`, `debate_format`) so Phase B's constants remain the single
source.

`DEBATE_FORMAT` (orchestrator constant) becomes:

```
This debate format replaces your persona's usual output format: no headings, no lists, no
portfolio or allocation lines, no ROADMAP or BUILD NOW tags, no content angles, no sign-off.
Refer to the other analyst only as "the other view", never by name or role.
```

### 13.2 `config/prompts/debate/questions.md`

Appended after `shared()` in the triage call (replaces Phase B's inline triage task).

```
<!-- schema: triage · inputs: day, roster, open_questions, due_items, novel_items, n_max -->
TASK: TRIAGE AND QUESTIONS OF THE DAY ({{day}}). You set up today's analysis for these analyst lenses:
{{roster}}

OPEN QUESTIONS FROM EARLIER RUNS (not yet resolved; re-ask at most 2, and only if today's data moved them):
{{open_questions}}

PREDICTIONS AND QUESTIONS THAT RESOLVE TODAY OR TOMORROW:
{{due_items}}

WHAT THE LENSES SAID OTHERS WOULD MISS (last run):
{{novel_items}}

1. environment: what dominates today's data (MARKET-DRIVEN: price moves, volume, flows; NARRATIVE-DRIVEN:
   social discourse and sentiment shifts; PRODUCT-DRIVEN: launches, upgrades, competitive moves;
   RISK-DRIVEN: regulatory actions, hacks, depegs, systemic risk; QUIET: incremental).
   depth: quiet, normal or risk. reason: one sentence.
2. weight_agents: the 2-4 lenses whose view matters most today.
3. questions: up to {{n_max}} QUESTIONS OF THE DAY. Each one:
   - is a yes/no question (at most 200 characters, ending with "?") about something in today's package;
   - is genuinely contestable: informed lenses could land 25 or more points apart. Drop any question
     you expect every lens to answer below 15% or above 85%;
   - kind: threshold (a number above or below a level on a date), event (a dated event happens or not),
     direction (a measured series up or down over a stated window), or judgment (no clean data; at most one);
   - resolves_on: a date 1 to 30 days after {{day}} (empty only for judgment); settles_with: the exact
     series or event and where it is published;
   - for threshold and direction: metric, comparator and threshold, and baseline_quote — the package line
     that gives today's value, copied character for character (a program checks it; a question whose
     baseline is not in the package is dropped);
   - domain; together the questions cover markets_crypto, macro_policy and ai_product;
   - lenses: the 2-5 lenses best placed to answer; weight: 3 if the answer changes what a reader in
     crypto, prediction markets or AI education does this week, 2 if it matters this month, else 1;
   - carried_from: the id of the open question above when you re-ask it, else empty. Do not restate an
     open question without carrying it.
Reply with one JSON object matching the schema.
```

### 13.3 `config/prompts/debate/challenge.md`

```
<!-- schema: debate_challenge · inputs: role, debate_format, qid, question, resolves_on, settles_with,
     my_p, my_reason, my_evidence, their_p, their_reason, their_summary, their_evidence, excerpts -->
{{role}}

TASK: DEBATE — CHALLENGE. Today's lenses split on one question and you are on one side of it.
{{debate_format}}

QUESTION [{{qid}}]: {{question}}
Resolves: {{resolves_on}}. Settles with: {{settles_with}}

YOUR POSITION: {{my_p}}% — {{my_reason}}
YOUR EVIDENCE:
{{my_evidence}}

THE OTHER VIEW: {{their_p}}% — {{their_reason}}
ITS READ OF THE DAY: {{their_summary}}
ITS EVIDENCE:
{{their_evidence}}

PACKAGE EXCERPTS AROUND THE EVIDENCE (the only data you may quote):
{{excerpts}}

Write, in this order:
1. steelman: the other view's best case in at most two sentences, stated so that its holder would
   call it fair. No "but", no rebuttal inside it.
2. crux: the one factual or causal claim the disagreement turns on; its type; the observable that
   would show it; the date by which it shows.
3. rebuttal: at most 120 words on why your number is closer to right. Do not open by agreeing and do
   not spend words on what you share.
4. evidence: 0-3 quotes copied character for character from the excerpts, each with its section. A
   program checks every quote; a quote it cannot find counts against you.
5. would_change_my_mind: the observable, the level or event, and the date.
Reply with one JSON object matching the schema.
```

### 13.4 `config/prompts/debate/response.md`

```
<!-- schema: debate_response · inputs: role, debate_format, qid, question, resolves_on, my_p,
     my_reason, my_evidence, steelman, crux, rebuttal, their_evidence, would_change, excerpts, crux_data -->
{{role}}

TASK: DEBATE — RESPONSE. The other view has challenged your position on one question.
{{debate_format}}

QUESTION [{{qid}}]: {{question}} (resolves {{resolves_on}})

YOUR POSITION: {{my_p}}% — {{my_reason}}
YOUR EVIDENCE:
{{my_evidence}}

THE CHALLENGE
How the other view states your case (its steelman of you): {{steelman}}
What it says the disagreement turns on: {{crux}}
Its argument: {{rebuttal}}
Its evidence:
{{their_evidence}}
What would change its mind: {{would_change}}

PACKAGE EXCERPTS:
{{excerpts}}

DATA ON DISK ABOUT THIS CRUX THAT NEITHER SIDE QUOTED (found by a program; may be empty):
{{crux_data}}

Answer:
1. steelman_fair: is that a fair statement of your case? yes, partly or no; if not yes, correct it in one sentence.
2. crux_agreed: is that what the disagreement turns on? If not, state your crux.
3. verdict and new_probability. The rule: you may move up to 10 points on argument alone. A larger move
   counts only with evidence you did not cite before — a verbatim quote from the excerpts, the other
   view's evidence, or the crux data above. A program enforces this: larger moves without such evidence
   are cut back to 10 points and recorded. Social-media quotes alone do not justify a larger move.
   Hold when the challenge brings no new fact; there is no credit for agreeing.
4. reason: at most 60 words — what changed and why, or why nothing did.
5. new_evidence: 0-3 quotes you did not cite before, verbatim, with their section.
Reply with one JSON object matching the schema.
```

### 13.5 `config/prompts/debate/red_team.md`

```
<!-- schema: red_team · inputs: role, debate_format, qid, question, resolves_on, settles_with, median,
     count_phrase, majority_reasons, my_p, my_reason, excerpts -->
{{role}}

TASK: RED TEAM. Today the lenses broadly agree on this question, so there is no debate. Your job is to
make the strongest honest case against the consensus, so the reader sees what could make it wrong.
{{debate_format}}

QUESTION [{{qid}}]: {{question}}
Resolves: {{resolves_on}}. Settles with: {{settles_with}}
CONSENSUS: median {{median}}% ({{count_phrase}}).
THE REASONS BEHIND IT:
{{majority_reasons}}
YOUR OWN TAKE: {{my_p}}% — {{my_reason}}

PACKAGE EXCERPTS (the only data you may quote):
{{excerpts}}

Write: consensus_view (one sentence); case (at most 150 words: the strongest argument that the consensus
is wrong, built on the data above, not on generic doubt); crux (the claim it all turns on, its type,
observable and date); evidence (0-3 verbatim quotes with section); probability (your own honest number —
it does not have to sit on the other side of 50); would_change_my_mind.
Reply with one JSON object matching the schema.
```

### 13.6 `config/prompts/debate/crux_check.md`

No persona; the referee is neutral.

```
<!-- schema: crux_check · inputs: qid, question, crux, side_a, side_b, data_block, excerpts -->
You are a neutral referee. You do not hold a view on the question. You read data and say what it shows.
Answer from the material in this prompt only.

QUESTION [{{qid}}]: {{question}}
WHAT THE DISAGREEMENT TURNS ON: {{crux}}
ONE VIEW: {{side_a}}
THE OTHER VIEW: {{side_b}}

DATA FOUND ON DISK FOR THIS CRUX:
{{data_block}}

EXCERPTS BOTH VIEWS CITED:
{{excerpts}}

Say whether the data settles the crux (resolved: yes, partly or no), what it says in at most 60 words,
the single quote that shows it (verbatim, with its section; empty if nothing does), what remains
uncertain in at most 40 words, the observable and date that will settle it, and which view the data
leans towards (higher means the view with the higher probability). Do not add opinions, forecasts or
facts that are not in the data above.
Reply with one JSON object matching the schema.
```

### 13.7 `config/prompts/debate/brief_split.md`

Inserted into the draft prompt in place of `{record}`.

```
<!-- inputs: split_sheet, lens_notes, questions_line -->
WHERE THE VIEWS SPLIT — rules for this section:
- Up to three blocks, one per block in the split sheet below, in that order. Each block: the question in
  plain words; the count phrase exactly as given (do not compute or change numbers); the base case; the
  minority view; what it turns on; when we will know (date).
- A block marked consensus: start with "No real split today. The strongest case against the consensus:"
  and give the red-team case and what would settle it.
- If the sheet says no split today, write one line saying the lenses broadly agree, with this figure:
  {{questions_line}}
- Never name an agent or describe the process (no debate, challenge, concession, agents, votes).
  Disagreement appears only in this section and only with the count phrases given.
- WHAT TO WATCH takes the dated "settles on" items from this sheet first.

SPLIT SHEET:
{{split_sheet}}

LENS NOTES (what each analytical lens found today; verified data only; for WHAT HAPPENED, WHAT IT MEANS,
RISKS and WHAT TO WATCH):
{{lens_notes}}
```

---

## 14. Code changes, file by file

| File | Change |
|---|---|
| `recon/debate.py` (new) | Pure functions, no I/O beyond what is passed in: `gate_questions`, `pair`, `pick_red_team`, `excerpts`, `crux_terms`, `crux_search`, `gate_move`, `score_debate`, `final_positions`, `question_stats`, `agent_run_score`, `split_sheet`, `render_split_sheet`, `lens_notes`, `anonymise`, `leakage_flags`. Constants `GAP_MIN`, `FREE_MOVE`, `CRUX_GAP`, `DOMAIN_LENSES`, `LENS_RAW`, `LENS_LABEL`. |
| `recon/prompts.py` (new) | `render()` (§13.1). |
| `config/prompts/debate/*.md` (new) | The six templates in §13. |
| `recon/schemas.py` | §12: new schemas, `LEGACY`, `ARTIFACTS`, `validate()` nullable/boolean/integer. |
| `recon/evidence.py` | `locate()` with section and class (§3); `Corpus` built once per run and reused (it is rebuilt today in `ph_positions` and `ph_checks`). |
| `recon/llm.py` | Dry-run: enum question ids, `RECON_DRY_SPREAD`, dry replies for the new schemas come from `_dry_json` (no new canned text). |
| `recon/orchestrator.py` | `PHASES`/aliases; `ph_triage` uses `questions.md`, the gate and the ledger reads; `take_prompt` appends lens extras; new `ph_pairing`, `ph_challenges` (pairs or red team), `ph_responses` (crux search first, gate after), `ph_cruxcheck`, `ph_split`; `ph_positions` rewritten around `debate.py`; `ph_synthesis` uses `brief_split.md`, the split sheet and lens notes; `ph_checks` adds §11.5; `ph_record` writes §12.3 fields; `TENSIONS`, `wildcard()`, `ph_deepdive` deleted; `--as-of`, `--replay`, `--state-dir` flags (§15). |
| `recon/agentmem.py` | No format change. Adapter in the orchestrator: `legacy_response(agent)` → `{verdict: strongest of its debates (concede > narrow > hold), text: its reasons joined, final_positions: gated values}`; agents that did not debate get `None` as in Phase B. Lessons lines then come from real gated moves. |
| `recon/export.py` | Nothing for the orchestrator path (it uses `run.json` as is); `LAYER`/`SECTION_LAYER` change in Phase E. |
| `personas/synthesizer.md` | §11.4 edits. |
| `.gitignore` | `config/questions/`, `config/agent_scores/`. |
| `tests/` (new) | §17. |

Effort: 2 days of Claude time, as the plan says; `debate.py` and its tests are about half of it.

---

## 15. The replay harness

### 15.1 Flags

```
python3 recon/orchestrator.py --replay briefs/2026-09-11 --as-of 2026-09-11 --run-id 2026-09-11-c1 --no-telegram
```

- `--as-of DATE`: the run's `day` (prompts, question dates, ledger ids, gate windows). Default
  today. Without it a September package would be dated October and every `resolves_on` window
  would be wrong.
- `--replay DIR`: implies `--package-from DIR`, `--skip-score`, `--no-telegram`, no archive, no
  knowledge DB, and `--state-dir <run>/state` with **empty** memory, state, ledger and scores
  (cold start). Using today's `config/agent_memory` would leak October into September. This
  differs from the original runs, which had memory; the report notes it.
- `--state-dir DIR`: where memory, state, ledger and agent scores are read and written (default
  `config/`; dry runs already use the run folder).
- The September `00_historical_context.md` (June values, F3) is replaced by an empty file in
  replays, as Phase A's fix would have done.

The September folders are on the droplet (`/home/recon/recon-v2/briefs/2026-09-10` and
`-09-11`: packages 61,066 and 169,518 bytes; 6 and 8 `# SECTION` markers). The 09-10 package has
no AI & TOOLS or FUNDRAISING section; `build_agent_package.py` handles that (`FALLBACK_NAMES`).
Replays run on the droplet from the `v2` checkout once Phase C is deployed there (the droplet
worktree is at `a585ef1` today, behind Phase B).

### 15.2 Runs

| Run id | What | Calls (est.) |
|---|---|---|
| `2026-09-10-c1` | full new flow, FAST triage | ~22 |
| `2026-09-11-c1` | full new flow, FAST triage | ~25 |
| `2026-09-11-c2` | triage only, on ANALYST (`RECON_TRIAGE_TIER=analyst RECON_STOP_AFTER=triage`, same `--replay`/`--as-of`), to compare the questions (§2.1) | 1 |

`RECON_STOP_AFTER=<phase>` is a small new switch: the driver returns after that phase.

### 15.3 Report

`scripts/replay_report.py <run_id> --old ~/innovlabs/recon-exports/runs/<date>.json` (runs
anywhere; reads the run folder's `run.json` and the old export) writes
`briefs/<run_id>/replay_report.md` and prints one model-log row. Columns, new vs old:

| metric | new run from | old run from |
|---|---|---|
| questions kept / dropped by the gate | `triage.json` | — |
| real splits found (`live_split` debates) and useful debates | `positions.json` | deep dive gap (09-10: one, ended in agreement; 09-11: one, ended in agreement) |
| consensus day? red team? | `pairing.json` | — |
| evidence verification rate, data share | `positions.json` | not measured (cites empty) |
| concession rate (concede verdicts / responses), verbal concessions | responses | "I am updating my position": 6 of 9 (09-10), 7 of 9 (09-11) |
| moves capped by the gate | responses | — |
| persona leakage flags, agreement openers | challenges | 09-11: 6 of 11 challenges open by agreeing |
| citation overlap (mean Jaccard) | `positions.json` | recomputed from the old takes in the export |
| Polymarket structure points in the brief (F14) | grep the new brief | 0 on 09-11 |
| HIMS tokenized-price claim as debate evidence (F13) | evidence items with class `social` | in 6 of 9 takes, 7 of 9 votes |
| brief: sections in order, words, agent names, count mismatches | `checks.json` | export |
| calls, input / cached / output tokens, wall time | `run.json → usage` | 61 calls, 1.17 M / 1.36 M input |

### 15.4 Hindsight scoring (free, no LLM)

The September questions have resolved by now. For `threshold`/`direction` questions on series with
free history — DeFiLlama `historicalChainTvl` (total and per chain), CoinGecko
`/coins/{id}/market_chart/range`, alternative.me Fear & Greed `?limit=60` — the report script
resolves them and computes a Brier score per agent and for the median. `event` questions are
resolved by Claude reading public news once and are marked `manual`. This gives the first Brier
numbers before any live run, and the outcomes are appended to the replay's own ledger only.

### 15.5 Pass bar for the replays

Both replays together:

- at least one `live_split` across the two days, or a consensus day with a red-team block that
  the split sheet renders;
- evidence verification rate (verified + partial) ≥ 80 % on debate evidence;
- concede verdicts ≤ 30 % of responses, and no gated move above 10 points without new data evidence
  (true by construction; the check is that the gate ran);
- persona leakage flags ≤ 1 per run;
- ≤ 26 calls and ≤ 0.6 M input tokens per replay;
- the brief has the 11 sections in order with WHERE THE VIEWS SPLIT, no agent names, no count
  mismatch;
- the F13 claim is not the basis of any debate (no pair whose endpoints' evidence on that question
  is social-only — enforced by eligibility, verified in the report).

A failed bar item is fixed and that replay rerun (`--from-phase` the earliest affected phase).
The two replays plus each side-by-side (old brief, new brief, split sheet) are published as one
private artifact page for Eric with the model-log rows, and the result is reported, not asked.

---

## 16. Cost per stage

Per-call input = slim Codex prefix (~2.2 K, measured) + prompt. Prompt tokens are estimated at
~3.6 bytes per token for this mixed English/number text (Korean headlines are denser; the live
runs replace these estimates). The shared block is the ~65–80 KB agent view + sector context +
historical context + scorecard ≈ 85 KB ≈ 24 K tokens, byte-identical across triage and takes, so
after the first call it is mostly cached.

| Stage | Phase B today (as built) | Phase C, normal day (3 pairs, 9 agents) | Phase C, consensus day | Output tokens (C, normal) |
|---|---|---|---|---|
| Triage (questions) | 1 FAST / ~27 K | 1 FAST / ~28 K (+ ledger lines ~1 K) | same | ~1.2 K |
| Takes | 9 ANALYST / ~300 K (~200 K cached) | 9 ANALYST / ~315 K (lens extras +1.6 K each) | same | ~14 K |
| Pairing | 0 (wildcard is programmatic) | 0 | 0 | — |
| Challenges | 11 ANALYST / ~70 K (both full takes in each) | 6 ANALYST / ~38 K (~6.3 K each) | 1 red team / ~7 K | ~3 K |
| Crux search | — | 0 | 0 | — |
| Responses | 9 ANALYST / ~65 K (+ votes) | 6 ANALYST / ~42 K (~7 K each, incl. crux data) | 0 | ~2 K |
| Deep dive / crux check | 0–2 ANALYST / ~16 K | 0–1 ANALYST / ~5 K | 0–1 / ~5 K | ~0.3 K |
| Votes, memory, state | 0 (Phase B) | 0 | 0 | — |
| Draft | 1 SYNTH / ~30 K (full record) | 1 SYNTH / ~20 K (split sheet + lens notes) | ~19 K | ~3.5 K |
| Filter (until Phase D) | 1 SYNTH / ~38 K | 1 SYNTH / ~38 K | same | ~3.5 K |
| **Total** | **32–34 calls / ~0.55 M** | **24–25 calls / ~0.49 M** | **13–14 calls / ~0.41 M** | **~27 K** |
| Quiet day (1 pair) | — | 16–17 calls / ~0.44 M | | |

What this says honestly: Phase C cuts calls by about a quarter (and by 60 % on consensus days),
but input tokens only by ~10 %, because the nine takes carry the shared block and dominate. The
real token lever after C is the roster (6 agents ≈ −105 K) and Phase D's filter removal (−38 K).
Wall time: takes 2 waves of 5 (~3 min), challenges and responses 2 waves each (~2 min), synthesis
(~4 min): ~9 min of LLM time, down from Phase A's 12 min 45 s.

Quota to validate Phase C: 2 replays (~47 calls) + 1 triage call + 2 live runs (~50 calls) ≈ 100
calls, about one and a half old v1 runs.

---

## 17. Test plan

All tests are stdlib `unittest` (`python3 -m unittest discover tests`), no network, under 10 s.

### 17.1 Unit tests, `tests/test_debate.py` (pure functions, fixtures in `tests/fixtures/debate/`)

| Test | Case | Expect |
|---|---|---|
| gate | question without `?`; "How much…?"; `resolves_on` 45 days out; two judgment questions; unverifiable baseline; Jaccard 0.7 repeat; 3 carried | each dropped with the right reason; ids renumbered q1… |
| gate | 2 survivors | `needs_reask` true |
| pairing | values 20/30/70/80 on q1, 45–55 on q2 | one pair on q1 (20 vs 80), none on q2 |
| pairing | 3 questions all split, 9 agents | 3 pairs, 6 distinct agents (cap 1) |
| pairing | 3 questions split but all extremes are the same two agents | second pass uses cap 2; no agent in 3 debates |
| pairing | same pair yesterday, equal gaps | the other pair wins |
| pairing | an extreme agent with only social or unverified evidence | not an endpoint; next eligible agent pairs |
| pairing | both on the same side of the median, gap 40 | no pair |
| pairing | all within 12 points | `day_type: consensus`, red team = furthest agent if ≥ 10 from median, else skeptic |
| pairing | deterministic | same input in shuffled order → identical `pairing.json` |
| gate_move | +25 with no new evidence | gated +10, flag `update without evidence, capped` |
| gate_move | +25 citing the challenger's verified data quote | gated +25, no flag |
| gate_move | +25 with only a social quote | +10, `social evidence only, capped` |
| gate_move | −8 with nothing | −8, no flag; verdict concede + 3 → `verbal concession` |
| gate_move | quote already in own take | not new |
| crux_search | crux "DEX volume rises while TVL falls" over the 09-11 raw file fixture | hits include DEX and TVL lines; lines already quoted excluded; ≤ 12 hits; ≤ 3 KB |
| stats | 9 values with two at 50 | majority/minority counts exclude the 50s |
| split_sheet | direction, degree, consensus, none | right `type`, `count_phrase` text, ≤ 6 KB, no agent names after `anonymise` (all nine names in all casings in the input) |
| split_sheet | minority rated the steelman `yes` | `minority_case.source == "steelman"` |
| lens_notes | unverified and social claims in input | left out; labels are lens labels |
| leakage | "**ROADMAP:**", "Post now", "## Take" | flagged |

### 17.2 Schema tests, `tests/test_schemas.py`

- every schema in `ALL` is strict-mode clean: every object's `required` equals its property keys
  and `additionalProperties` is false (walk recursively); no `null` types in `ALL`;
- a hand-written golden reply per schema validates; a reply missing one field fails with the path;
- `_dry_json` output for every schema validates, with question ids taken from the prompt;
- every `ARTIFACTS` schema validates a fixture artifact produced by `debate.py` from the fixtures.

### 17.3 Template tests, `tests/test_prompts.py`

- each template renders with the variables the orchestrator passes; a missing or extra variable
  raises (catches drift in both directions);
- the rendered challenge and response prompts for the fixture pair are under 9 KB.

### 17.4 Dry-run end to end

- `--dry-run --package-from briefs/2026-10-04` (wide spread): green; `pairing.json` has ≥ 1 pair;
  challenges, responses, cruxcheck, split, synthesis, checks, run.json present; run.json
  `schema_version` 2 validates against the run.json part of `ARTIFACTS`.
- same with `RECON_DRY_SPREAD=narrow`: `day_type: consensus`, 1 red-team call, 0 responses, a
  consensus block in the split sheet.
- resume: `--from-phase responses` reuses triage, takes, pairing and challenges (calls.jsonl
  unchanged for them); `--from-phase deepdive` maps to `cruxcheck` with a log line.
- `--replay` on the dry provider: state written only under `<run>/state/`; `config/` unchanged
  (checked by hashing `config/` before and after).

### 17.5 Live

1. The two replays and the triage comparison (§15). Pass bar §15.5.
2. Two live validation runs on fresh data with tagged ids (`<date>-c3`, `<date>-c4`, `--no-telegram`),
   same bar plus: the brief would have been delivered on time from a 05:00 KST start.
3. Model-log rows for each: calls, tokens (cached), output, wall, useful debates, splits.

---

## 18. Exit, roster and cutover

- **Exit**: dry runs green, unit tests green, replays and live runs meet §15.5, model-log updated,
  `06-improvement-plan.md` §7 row C marked done with the measured numbers.
- **Cutover**: after the two live runs pass, `scripts/cron_run.sh` switches from `run_recon.sh`
  to `python3 recon/orchestrator.py` (one line; bash stays on disk as the fallback for a week).
  This is a droplet write made from the session (decision 6).
- **Roster (decision 5, deferred to Phase C)**: after the replays and the first 5 orchestrator
  runs, compute each agent's mean `distinctness`, endpoint share, `data_share` and
  `unique_numbers` from `config/agent_scores/`. An agent goes inactive when it is in the bottom two
  on distinctness **and** on unique numbers **and** has an endpoint share under 10 %. At most 3 go;
  never `skeptic` (red-team fallback); never the only default lens of a domain in `DOMAIN_LENSES`.
  The active list lives in `config/roster.json` (`{"active": [...], "inactive": [...], "decided":
  "<date>", "evidence": {...}}`), read by triage instead of the hard-coded nine. The change and
  its numbers go in the model log. F15 predicts `analyst` and `user_agent`; the data decides.

---

## 19. Decisions made in this spec

| # | Decision | Where |
|---|---|---|
| 1 | Triage stays FAST; one A/B on 09-11 decides whether it moves to ANALYST | §2.1 |
| 2 | 3 / 4 / 5 questions by depth; max pairs 1 / 3 / 3 | §2.2 |
| 3 | Questions carry kind, domain, metric, baseline quote, weight, lineage; programmatic gate | §2.3–2.4 |
| 4 | Question ledger, append-only, gitignored | §2.6 |
| 5 | TAKE schema unchanged; lens raw extras after the cached prefix | §3 |
| 6 | Evidence class data vs social; social-only positions cannot open a debate | §3, §4.1 |
| 7 | Pairs must straddle the median; one debate per agent unless short; gap ≥ 20 | §4.2 |
| 8 | Both sides challenge each other; no persona format; names replaced | §5 |
| 9 | Programmatic crux search feeds the responders (gives "new evidence" a real source) | §6 |
| 10 | Free move 10 points; larger moves capped at 10 without new verified data evidence | §7.2 |
| 11 | Undebated positions do not move | §7.2 |
| 12 | Crux check: one neutral referee call, adds facts, moves nothing | §8 |
| 13 | Vote calls gone; final positions are the vote | §10 |
| 14 | Split sheet ≤ 6 KB + lens notes ≤ 8 KB replace the full record in the draft | §11 |
| 15 | WHERE THE VIEWS SPLIT lands in Phase C (heading, prompt, persona, checks); the rest of Phase D stays in D | §0, §11.4 |
| 16 | Count phrases rendered by code; the brief may only copy them | §11.2 |
| 17 | Replays cold-start state, empty historical context, run on the droplet | §15.1 |
| 18 | Replays and live runs reported in one private artifact page; not a gate for Eric | §15.5 |
| 19 | Roster rule and `config/roster.json` | §18 |
| 20 | Cron cutover to the orchestrator after the two live runs | §18 |

Path: `docs/v2/phase-c-spec.md`.
