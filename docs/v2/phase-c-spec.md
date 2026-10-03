# RECON v2 — Phase C build spec: the debate redesign

Date: 2026-10-04. Status: build spec, written against Phase B as committed on `v2` (`f8991d5`,
`774afc5`). Source plan: `06-improvement-plan.md` §2.2 (F6–F16), §3 (redesign), §4, §7 row C,
§8 decisions. Measurements used: `model-log.md` (slim Codex prefix ~2.2 K tokens, resume only
caches).

Eric's standing instruction for this work: decide, do not ask. Every product choice the plan left
open is decided here and listed in §19 so it can be reversed in one place.

Revised 2026-10-04 after review: 23 findings applied; §20 lists each change and where it landed.
The templates in §13 are committed as `config/prompts/debate/*.md` and the call schemas in §12 as
`schemas/debate/*.json` (generated from this spec; `recon/schemas.py` must produce the same JSON,
a test in §17.2 compares them).

---

## 0. Scope

Phase C replaces the middle of the orchestrator (`recon/orchestrator.py`) from the fixed
`TENSIONS` challenges to the vote, and changes what the synthesizer reads about the debate.
It keeps everything Phase B built around it.

| Kept from Phase B, unchanged | Replaced or extended in Phase C |
|---|---|
| `score`, `collect`, `context`, `package` phases | `triage`: question shape, count rule, gate, carry-over, question ledger (§2) |
| `Run.call()` (schema, one re-ask), `Run.parallel()`, artifacts, `--from-phase`/`--resume` | `takes`: task text moves to `take.md` and answers from the lens; TAKE puts `positions` first and caps `take` at 150 words; lens raw extras after the shared block (§3) |
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

### 0.1 Build order and the spread gate

The debate only works if the takes disagree for reasons. The Phase A live run on 2026-10-04 showed
every package section to all nine agents (63 KB view), and all 9 `act_on` votes still came out
defensive and built on the same "TVL flat at $95.39B, DEX volume up" framing (export
`runs/2026-10-04.json`). F6 names the shared take task ("most significant development … today")
as one cause. No live run with per-question probabilities exists yet (model-log has only the
Phase B dry run). So Phase C is built in this order:

1. **Inputs first** (small, no pairing code): `take.md` and the TAKE field order (§3, §13.8),
   `LENS_RAW` and `evidence.locate` (§3), `00_raw_data.md` in the `--skip-collect` assembly
   (§3, phase-e §4.1a), `norm_p` (§7.2).
2. **Spread probe** (§15.0, ~32 calls on the droplet). It sets `GAP_MIN` and decides per-lens
   model or effort. If fewer than one question per package clears `GAP_MIN`, the inputs are fixed
   and the probe rerun **before** any pairing, challenge or response code is written.
3. Everything else in this spec, then the replays (§15).

---

## 1. Flow and artifacts

```
phase        calls                     artifact (briefs/<run>/phases/)
triage       1 FAST, schema            triage.json            questions of the day, gated (§2)
takes        6–9 ANALYST, schema       takes/<agent>.json     positions-first TAKE (§3)
pairing      0                         pairing.json           pairs, split_unpaired, or consensus + red-team pick (§4)
challenges   2 per pair, or 1 red team challenges/<a>__<b>__<q>.json, challenges/redteam__<a>__<q>.json
responses    2 per pair, 0 otherwise   responses/<a>__<q>.json; cruxsearch.json written first (§6)
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

`main()` declares `--from-phase` with `choices=PHASES + list(PHASE_ALIASES)` (argparse would
otherwise reject `deepdive` before any mapping), then maps `args.from_phase` through
`PHASE_ALIASES` and logs `--from-phase deepdive → cruxcheck`.

Item file names carry the question id because one agent can sit in two debates (§4.2).

### 1.1 Call budget

`RECON_CALL_BUDGET` (default 24) counts every line in `calls.jsonl`, failed attempts and schema
re-asks included. It is a planning budget, applied by pairing after the takes:

```python
free  = budget - used - synth_calls          # used: calls so far; synth_calls: 2 until Phase D drops the filter, then 1
with_crux, without_crux = (free - 1) // 4, free // 4    # a pair costs 4 calls (2 challenges, 2 responses)
if with_crux >= depth_target:      target, crux_check = depth_target, True
elif without_crux > with_crux:     target, crux_check = min(depth_target, without_crux), False   # drop the crux check first
else:                              target, crux_check = with_crux, True                            # then fewer pairs
```

So the order of sacrifice is the crux check, then the triage re-ask (made only when ≤ 1 question
survives the gate, §2.4), then pairs (the lowest-scoring first). With clean takes a normal day
gets 3 pairs and no crux check (24 calls); one take re-ask brings it to 2 pairs with the crux
check; after Phase D a normal day gets 3 pairs and the crux check.

`RECON_CALL_CEILING` (default 32) is the hard stop. Once `calls.jsonl` reaches it, every optional
call is skipped: triage re-ask, crux check, red team, challenges and responses not yet started,
and schema re-asks (a reply that fails the schema once then fails that item). The takes and the
synthesis calls are never skipped. Every skip is logged and written to
`run.json → usage.budget_skips[]` as `{phase, item, reason: "budget"|"ceiling", used}`.

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

The pair counts are maxima; the call budget (§1.1) can lower them (a normal day with a take
re-ask gets 2, §16).

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
| `weight` | integer 1–3; 3 = it changes what a reader in crypto, prediction markets or AI education does this week. Code rounds and clamps it to 1–3 after the reply (a dry run or a loose model can send 0 or 9) and records `weight clamped` under `gate.notes[]` |
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
4. `kind in (threshold, direction)` and `baseline_quote` does not verify (`evidence.locate` status
   `verified` or `partial`) against `00_data_package.md`, `00_raw_data.md`, the agent view
   `01_filtered.md` (triage reads the view, where X lines are re-rendered) and `01_social.md`;
5. word-set Jaccard ≥ 0.6 with a question kept earlier today, or with an open ledger question
   while `carried_from` is empty (a silent repeat);
6. more than 2 questions have `carried_from` set (keep the higher weight).

`lenses` is cleaned to valid active agents; if fewer than 2 remain it is filled from the
domain's default lenses (`DOMAIN_LENSES` in `recon/debate.py`: markets_crypto → trader, analyst;
macro_policy → macro_strategist, policy_analyst; ai_product → ai_engineer, builder; korea →
policy_analyst, user_agent; prediction_markets → trader, skeptic).

If at most 1 question survives, triage is asked once more (same call, plus the gate's reasons),
unless the call ceiling (§1.1) is reached. With 2 survivors the run goes on without a re-ask (one
split is still possible, and the call is worth more as a pair). After that the run goes on with
whatever survived; with 0 questions the day has no debate and no
split sheet (Phase B's soft-failure path), and the brief section says so (§11.4).

### 2.5 Carry-over

A carried question keeps its lineage: the ledger line for today gets `carried_from`, and the
split sheet can say "unchanged since 09-11 (median 64% → 61%)". A carried question still has to
pass the gate with today's baseline quote.

### 2.6 The question ledger

`config/questions/ledger.jsonl` (gitignored, like `knowledge.db`), one line per question per run,
appended by the `split` phase (§11) so it carries the final stats. Dry and replay runs write to
`<run>/state/questions/ledger.jsonl`, and so does every run with a tagged id (anything other than
the bare date, e.g. the §17.5 runs `<date>-c3`) unless `--state-dir config/` is passed
explicitly, so a validation run cannot collide with that day's cron run. Schema `LEDGER_LINE`
(§12.3).

`ledger_id = "<run_id>-<qid>"`, which equals `<day>-<qid>` for the daily run (its run id is the
date). Appends are idempotent: before writing, the phase reads the file and skips a line whose
`(type, ledger_id)` already exists, so `--from-phase split` or `--from-phase positions` does not
duplicate it. The same rule keys `config/agent_scores/<agent>.jsonl` on `(run_id, agent)`
(§9.2). Lines are never edited;
the Phase D resolver appends `{"type": "resolution", "ledger_id", "outcome": "yes|no|void",
"value", "source", "resolved_at"}` lines to the same file. Brier scores (Phase F) join on
`ledger_id`.

---

## 3. T — Takes

Phase B's take prompt gave every agent the same 85 KB block and the same task ("Cover the most
significant development in YOUR domain today"), and its TAKE schema put the 200–400 word persona
`take` before `positions`, so the shared story was settled before any probability was given.
Four changes:

1. **Task text** moves out of `take_prompt` into `config/prompts/debate/take.md` (§13.8). The
   sentence "Cover the most significant development in YOUR domain today." is replaced by:
   "Answer each question from your lens. At least one evidence item per position must come from
   YOUR LENS DATA block." The cached prefix (`shared()` + the questions block) is unchanged; the
   template is everything after it.
2. **TAKE field order and length** (`schemas.TAKE`, §12.1): `positions` first, then `claims`,
   `summary`, `prediction`, `novel`, `watching`, and `take` last, capped at 150 words (was
   200–400; nothing in Phase C's synthesis path reads it, it stays for the archive and the RUBRIC
   page). Readers access fields by key, so Phase B readers keep working. The take prompt's reply
   list follows the same order.
3. **Lens extras.** After the shared block (so the cached prefix stays byte-identical), each agent
   gets up to 6 KB of its own lens's raw material under the heading `YOUR LENS DATA`, chosen by a
   static map `LENS_RAW` in `recon/debate.py`. Phase A's raw file holds only the reddit, twitter,
   onchain, news, ai_tools and fundraising files (`collect_data.sh` line ~883); World Monitor, the
   economic calendar and BettaFish exist only in the package, `## RECENT HACKS` exists nowhere, and
   `## STABLECOINS` is not a prefix of the real `## STABLECOIN SUPPLY`. The first version of this
   table gave macro_strategist and skeptic 0 bytes. The corrected table and rules are the same as
   phase-e §4.5b (which also adds the Phase E entries); the two must stay identical:
   - **Search order**: `00_raw_data.md`, then `00_data_package.md`; first match per heading prefix.
     A `# ` heading takes its block up to the next `# ` line; a `## ` heading up to the next `#`
     or `## ` line.
   - **Subtract the view**: every line (stripped) already present in `01_filtered.md` is removed,
     and so is every line an earlier entry of the same agent already added. Headings are kept only
     when a body line under them survives. The extras are then only material the shared block does
     not carry.
   - **Line filters**: `news~<regex>` takes matching `- ` lines from the `# News Intelligence` and
     `# Twitter/X Intelligence` blocks of the raw file (case-insensitive).
   - Each entry capped at 3 KB, 6 KB per agent, in table order.

   | agent | entries (in order) |
   |---|---|
   | trader | `# Polymarket Intelligence`, `# Kalshi Intelligence`, `## DEX VOLUMES`, `## FEE REVENUE` |
   | narrator | `# Reddit Intelligence`, `# Twitter/X Intelligence` |
   | builder | `# Changelogs Intelligence`, `## GITHUB TRENDING`, `## HACKER NEWS` |
   | analyst | `## CHAIN TVLs`, `## PREDICTION MARKET PROTOCOLS`, `## STABLECOIN SUPPLY`, `## TOP STABLECOIN YIELDS`, `## DERIVATIVES PROTOCOLS` |
   | skeptic | `news~hack\|exploit\|depeg\|breach\|drain`, `## STABLECOIN SUPPLY`, `## DERIVATIVES PROTOCOLS`, `## 5. Controversy & Risk Flags` (package, BettaFish), `# BettaFish` (package) |
   | policy_analyst | `## KOREA — CRYPTO & MARKETS`, `news~regulat\|SEC\|CFTC\|ESMA\|CLARITY\|금융위`, `## REGULATORY ACTIONS` (package, World Monitor), `# ZDNet Korea Intelligence` |
   | user_agent | `# Reddit Intelligence`, `## KOREA — AI`, `# ZDNet Korea Intelligence` |
   | macro_strategist | `# World Monitor Intelligence` (package), `## ECONOMIC CALENDAR` (package), `## AI FORECASTS` (package), `# Kalshi Intelligence`, `news~Fed\|FOMC\|CPI\|inflation\|payroll\|tariff\|treasury\|yield` |
   | ai_engineer | `## AI NEWSLETTER SOURCES`, `# Changelogs Intelligence`, `## GITHUB TRENDING` |

   Phase E entries (`# Polymarket`, `# Kalshi`, `# Changelogs`, `# ZDNet Korea Intelligence`) are
   inert until phase-e §4.1a puts those files into `00_raw_data.md`. Entries that do not exist in a
   package are skipped silently (09-10 has no `## KOREA` or `## AI NEWSLETTER` blocks); the bytes
   actually added are recorded in `takes/<agent>.json → fed.lens_extra_bytes` and per entry in
   `fed.lens_extra_headings`, and the takes phase logs a warning when an active agent gets less
   than 2 KB. When an agent's extras are empty the block reads "(no lens data today: cite the
   package)" and the lens-data rule in `take.md` does not apply. Unit test: every agent gets more
   than 0 bytes on the 09-11 and 10-04 fixtures, and none of the added lines occurs in that day's
   `01_filtered.md` (§17.1). The point (F1, F6) is that lenses stop starting from the identical
   text; `citation_overlap` before/after and `lens_extra_bytes` are replay metrics (§15.5).

   The `--skip-collect` assembly in the orchestrator writes `00_raw_data.md` from the same source
   list as `collect_data.sh` (phase-e §4.1a), so a run built without collection still has a raw
   file.
4. **Evidence class.** Not a prompt change. Every evidence quote gets a `class` when it is
   verified. New helper `evidence.locate(quote, docs) -> {status, section, cls, doc, line}` built
   on `verify_quote`, where `docs` holds `package` (`00_data_package.md`), `raw`
   (`00_raw_data.md`), `view` (`01_filtered.md`, the agent view triage and the takes actually read,
   where X lines are re-rendered as `- @who (eng) rest`) and `social` (`01_social.md`).
   - `section` and `cls` come from the package section of the hit (the section split already in
     `ph_record.cite_section`): `social` for SENTIMENT & MARKET MOOD and SOCIAL INTELLIGENCE,
     `data` otherwise. In `00_raw_data.md`, `social` inside the Reddit, X and BettaFish blocks,
     `data` elsewhere. For a hit found only in the view, the view's `# SECTION: <name>` label gives
     the section (`social` for SENTIMENT and SOCIAL); a hit only in `01_social.md` is `social`.
     A quote found as `data` anywhere is `data`.
   - A `partial` match (shingle overlap) is anchored to the single line with the highest 4-gram
     overlap with the quote; that line gives `section`, `cls` and the ± 3-line excerpt (§5.2).
   This is what stops F13: a position resting only on "per social media" can be argued about but
   can open a debate only on a `judgment` question (§4.1), and cannot justify a move above the
   free move (§7.2).

The take is verified once, right after the takes phase, and the results are cached in
`positions_evidence` inside `pairing.json` so pairing does not re-run the check.

Whether lenses should also differ by model or effort is measured in the spread probe (§15.0):
`llm.py` already reads `RECON_MODEL_<TIER>` and `RECON_EFFORT_<TIER>`, and a per-agent override
`LENS_TIER` in `recon/debate.py` (default empty: every agent on ANALYST) is added only if the probe
shows retest-adjusted spread rises.

---

## 4. P — Pairing by widest probability gap (no LLM)

### 4.1 Inputs and eligibility

- `p[a][q]`: each agent's take probability (Phase B `pos_map`, fractions fixed by `norm_p`,
  §7.2), rounded to an integer and clamped to 0–100.
- `ev[a][q]`: that position's evidence after §3 verification.
- An agent is **eligible** on `q` when it has a probability on `q` and at least one evidence
  quote on `q` with status `verified`/`partial`, of any class. A position whose verified quotes are
  all `social` is eligible only on `judgment` questions; on the other kinds it needs at least one
  `data` quote. This lets narrator and user_agent, whose lens data is Reddit and X, open debates on
  judgment questions, and still keeps F13-type claims from opening a debate on a measurable
  question. The move cap (§7.2) applies to social-only endpoints as to everyone. Ineligible agents
  still count in the median and the final stats; they just cannot be a debate endpoint.
- `yesterday_pairs`: unordered pairs from yesterday's `pairing.json` (same `briefs/` root, the
  previous date folder; absent on replays and the first run).

### 4.2 Algorithm

```python
GAP_MIN = int(env("RECON_PAIR_GAP", GAP_MIN_DEFAULT))   # default set by the spread probe, §15.0
target  = min({"quiet": 1, "normal": 3, "risk": 3}[depth], budget_pairs())   # §1.1

cands = []
for q in questions:
    vals = [p[a][q] for a in active if q in p[a]]
    if len(vals) < 3: continue
    med = median(vals)
    w = round(min(3, max(1, q.weight)))                  # clamped, a weight of 10 cannot dominate
    for a, b in combinations(sorted(eligible(q)), 2):
        lo, hi = sorted((a, b), key=lambda x: (p[x][q], x))
        gap = p[hi][q] - p[lo][q]
        if gap < GAP_MIN: continue
        if not (p[lo][q] <= med <= p[hi][q]): continue      # must straddle the median
        score = gap * w
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
matter; the record labels them `high` and `low`. `GAP_MIN_DEFAULT` is 20 until the spread probe
sets it (§15.0: `max(20, 2 × median retest |Δp|)`); the value used is written to
`pairing.json → gap_min`.

### 4.3 Day types: debate, split_unpaired, consensus

- **debate**: `cands` is not empty; the pairs above are staged. (If `cands` is not empty but the
  budget leaves `target = 0`, the day is `split_unpaired` and the skip is logged.)
- **split_unpaired**: `cands` is empty but at least one question has a take `range ≥ GAP_MIN`
  (a range always straddles the median), so the views do split and the endpoints only failed
  eligibility. No debate and no red team are staged. The widest such questions (up to 3) go to the
  split sheet as **undebated** blocks (§11.1 item 2), and the brief does not say "no real split".
- **consensus**: no question has a range ≥ `GAP_MIN`. No debate is staged. Instead:
  - the **top question** is the one with the highest `weight`, then the widest range;
  - the **red-team agent** is the eligible agent furthest from that question's median, if that
    distance is ≥ 10 points; otherwise `skeptic` if active; otherwise the agent with the most
    verified data evidence on that question;
  - one ANALYST call (template `red_team.md`, schema `RED_TEAM`) argues the strongest case against
    the median's side, using the same excerpt rules as a challenge (§5.2). It is skipped when the
    call ceiling is reached (§1.1).

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
- **excerpts**: for every `verified`/`partial` quote of both sides on this question, the lines
  around it in the document where `locate` found it (the quote's line ± 3 lines; for a `partial`
  match, the anchor line of §3 item 4; merged when they overlap), capped at 4 KB total,
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
responses` re-runs it). On a consensus day it runs after the red-team call instead, on the red
team's `crux.claim`, `crux.observable` and `would_change_my_mind.observable`, and is stored in
`cruxsearch.json` under the key `redteam` (so the crux check of §8 can fire on that day).

For each pair, terms come from both challenges' `crux.claim`, `crux.observable` and
`would_change_my_mind.observable`:

- numbers (`evidence.numbers`), matched with the `NumberIndex` tolerance;
- entities: tokens with a capital letter or digits, length ≥ 3, excluding
  - a stop list (`The`, `This`, `If`, `Will`, `What`, weekday and month names…);
  - a sentence-initial capitalised token whose lower-case form is a dictionary word (a fixed list
    of the 5,000 most common English words in `recon/data/common_words.txt`) or appears in lower
    case elsewhere in the corpus;
  - date-like tokens (`2026-10-04`, `10/04`, `Q4`, `4Q26`, `H2`, a bare year `20\d\d`, `UTC`,
    `KST`);
  - plus Hangul words of ≥ 2 syllables;
- metric words from a fixed list: tvl, volume, liquidity, price, yield, apy, funding, open
  interest, inflow, outflow, spread, depth, market cap, dominance, supply, peg, 거래대금, 시가총액.

Corpus, run folder only (replays must not read today's `data-sources/`): `00_raw_data.md`,
`00_data_package.md`, `01_social.md`. Each line scores `3 × entities + 2 × numbers + 1 × metric
words`; a line is a hit when it scores ≥ 4, matches at least two distinct terms, **and** at least
one of them is a number or an entity (metric words alone, e.g. a generic "TVL … volume" line, do
not make a hit). Lines already quoted by either side, in the takes or the challenges, are excluded
(the point is facts neither side used). Top 12 hits by score, each with its section, class, line
number and ± 1 line of context; 3 KB block for the responders, 5 KB for the referee (§8). The hit
lines and their terms are kept in `cruxsearch.json → pairs[].hits[]` because the response gate
(§7.2) matches new evidence against them.

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
(`yes|no` + `own_crux` when no), `verdict` (`hold|narrow|concede`), `new_probability` (integer),
`reason` (≤ 60 words), `new_evidence` (0–3 quotes, verbatim).

**Fractions.** `debate.norm_p(value, ref_values) -> (int, flags)`: if `value ≤ 1` and
`max(ref_values) > 1`, the value is multiplied by 100 and flagged `fraction rescaled` (Phase B
takes have sent 0.65 for 65 %, commit `774afc5`); the result is rounded and clamped to 0–100,
flagged `clamped` when clamping changed it. It is applied to `new_probability` (ref: the agent's
take values) and to `RED_TEAM.probability` (ref: all take values on that question), before the gate.

The gate, applied by code in the responses phase; the agent's **gated** probability is what
counts everywhere downstream:

```python
FREE_MOVE = 5                                   # points an agent may move on argument alone
new_p, f = norm_p(resp.new_probability, take_values(agent))
d = new_p - take_p
seen = quotes_of(take[agent][q]) | quotes_of(take[other][q]) \
     | quotes_of(challenge_by[agent]) | quotes_of(challenge_by[other])   # both sides, takes and challenges
terms = crux_terms(pair)                                                  # §6: numbers, entities, metric words

for e in resp.new_evidence:
    loc = locate(e.quote, docs)
    e.source = ("own"        if norm(e.quote) in quotes_of(take[agent][q]) | quotes_of(challenge_by[agent]) else
                "challenger" if norm(e.quote) in seen else
                "crux_data"  if loc.line in crux_hit_lines(pair) else
                "other")
    e.qualifies = (loc.status == "verified"          # not partial: a stitched quote does not count
                   and e.source in ("crux_data", "other")   # not in either side's evidence
                   and loc.cls == "data"
                   and shares_term(e.quote, terms))  # at least one crux number, entity or metric word

qualifying = [e for e in resp.new_evidence if e.qualifies]

if abs(d) <= FREE_MOVE:              gated = new_p
elif qualifying:                     gated = new_p
else:                                gated = take_p + copysign(FREE_MOVE, d)
                                     flag("social evidence only, capped" if any(e.source in ("crux_data", "other")
                                          and e.cls == "social" for e in resp.new_evidence)
                                          else "argument only, capped" if any(e.source == "challenger" for e in resp.new_evidence)
                                          else "update without evidence, capped")
gated = min(100, max(0, gated))
```

The challenger's quotes are argument, not new evidence: a responder that copies them can move
only the free 5 points. Each evidence item is stored with `new_evidence_source`
(`crux_data|challenger|own|other`) and `qualifies`; each side's move records
`evidence_source`: `crux_data` if any qualifying item came from the crux hits, `other` if a
qualifying item came from elsewhere in the run folder, else `none`.

Further flags (recorded, never blocking):

- `verbal concession`: verdict `concede` and |gated − take_p| < 5 (F10: conceding by politeness);
- `hold but moved`: verdict `hold` and |d| > FREE_MOVE;
- `moved away`: verdict `narrow`/`concede` but the move goes away from the opponent's take value;
- `soft move`: a move of ≥ 5 points towards the opponent without qualifying evidence, whatever the
  verdict label (this is what the pass bar counts, §15.5 d);
- `rejected steelman`: `steelman_fair == "no"` (the challenger failed to state this side fairly;
  scored against the challenger, §9).

The words "DEFEND or CONCEDE" do not appear anywhere. Positions on questions the agent did not
debate do not move (no new information reached it), which removes Phase B's `final_positions`
array and its "update on everything" noise.

---

## 8. X — Crux check (replaces the deep dive)

After the responses. Programmatic pick, then 0 or 1 ANALYST call (template `crux_check.md`,
schema `CRUX_CHECK`, no persona: the referee is neutral). It is the first call dropped when the
budget is short (§1.1), which with the filter call still in place is a normal day with 3 clean
pairs.

- **Pick**: for each debate, `gap_before = p_high − p_low` (take values). Take the debate with the
  largest `gap_before` (ties: the larger `gap_after`, then question id), if `gap_before` is ≥ 15
  points (`RECON_CRUX_GAP`) and its crux search has ≥ 1 hit. Picking on `gap_before` means a
  split that closed during the debate without data can still be checked. On a consensus day the
  red team's crux is the candidate (crux search under `redteam`, §6), with gap = |red team
  probability − median|. Otherwise no call; `cruxcheck.json` says why.
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
gated, delta, clamped, new data evidence count, evidence source), `verdicts`, `steelman_fair` (as
rated by each target), `crux_agreed`, `evidence` (claimed vs verified vs data-class per side),
`flags` (all of §5.4 and §7.2), `crux_check` (resolved, leans) when run, and these derived fields:

- `closed_on_data`: at least one side's move has `evidence_source == "crux_data"` and moved
  towards the opponent;
- `closure_without_evidence`: `max(0, gap_before − gap_after)` when `closed_on_data` is false,
  else 0 (points of the split that disappeared on argument alone);
- `effect`: rendered by code for the replay report and RUBRIC, never for the brief, e.g.
  "narrowed from 30 to 18 on argument", "narrowed from 30 to 8 on crux data", "held at 32";
- `live_split`: `gap_after ≥ 20` **or** (`gap_before ≥ GAP_MIN` **and** not `closed_on_data`).
  A split that two polite debaters closed by 5 points each is still a split; it stops being one
  only when data closed it;
- `useful`: `live_split`, or any side moved ≥ 10 points on qualifying evidence, or the crux check
  resolved `yes`, or resolved `partly` with `leans != "neither"`. A referee's bare `partly` (that
  model's default hedge) does not count. "Useful debates per run" is the headline replay metric.

Run-level metric in `positions.json → summary`: `closure_without_evidence` (sum and median over
the day's debates) and `soft_moves` (count of `soft move` flags / responses).

### 9.2 Per agent per run (`positions.json → agent_scores{}`, schema `AGENT_RUN_SCORE`)

`distinctness` (mean |p − median| over questions, take values), `endpoint_count` (debates as an
endpoint), `evidence_rate` (verified+partial / claimed, all phases), `data_share` (data-class
share of verified quotes), `unique_numbers` (numbers it cited that no other take cited, from
`citation_overlap` sets), `moves_with_evidence`, `moves_capped`, `verbal_concessions`,
`steelmen_rejected` (as challenger), `leakage_flags`, `novel` (text). Appended by code to
`config/agent_scores/<agent>.jsonl` (gitignored; `<run>/state/` for dry, replay and tagged runs,
§2.6), skipped when a line with the same `(run_id, agent)` exists.

### 9.3 Over time

Brier per agent and per question needs resolved ledger lines (Phase D resolver, Phase F report).
Phase C only guarantees the inputs: every final probability is in the ledger with `ledger_id`,
agent and value.

---

## 10. V — Final positions (the "vote")

No vote calls. Per agent and question: `final = gated response value` if the agent debated that
question, else the take value (both integers 0–100 after `norm_p`). Per question
(`positions.json → questions[]`, schema `QUESTION_STATS`): `n`, `median`, `mean`, `min`, `max`,
`range`, `iqr`, `majority_side` (`yes` when median > 50, `no` when < 50, `even` at 50),
`majority_count`, `minority_count` (agents on the other side of 50; agents at exactly 50 count
with neither), `take_stats` (same fields from take values), `debated` (bool).

This replaces Phase B's `votes` dict. `run.json` keeps an `agents[].vote` key set to `null` so the
RUBRIC page's reader does not break; the page already handles empty votes (F12 days).

---

## 11. The split sheet and how "Where the views split" reaches the brief

### 11.1 Which questions get a block

Programmatic, in the `split` phase. Blocks are selected on the split the takes showed
(`gap_before`, the take spread), not on what is left after the debate, so a split that two polite
debaters talked down without data still reaches the reader. Candidates:

1. debated questions with `live_split` (§9.1: `gap_after ≥ 20`, or `gap_before ≥ GAP_MIN` not
   closed on crux data), ordered by `weight × gap_before`;
2. undebated questions with `minority_count ≥ 2` and take `range ≥ 40` (possible when more
   questions split than there were pair slots), ordered by `weight × range`. On a
   `split_unpaired` day (§4.3) the bar is `range ≥ GAP_MIN` and `minority_count ≥ 1` or a degree
   split, because these are the only splits that day.

At most 3 blocks, debated first. A block is a **direction** split when `minority_count ≥ 1`, else a
**degree** split (everyone on one side, but ≥ 30 points apart; ≥ `GAP_MIN` on a `split_unpaired`
day). A take gap under `GAP_MIN` is never a block (plan §3.4). With no block and a red team, the
sheet holds one **consensus** block. With neither, the sheet says `no split today`.

### 11.2 Block contents (schema `SPLIT_BLOCK`)

| field | built from |
|---|---|
| `question`, `resolves_on`, `settles_with` | the question |
| `counts` | final values: `{"n": 9, "majority": 7, "minority": 2, "median": 68, "range": [25, 85]}` |
| `count_phrase` | rendered by code from `counts`, the only form the brief may use: "7 of 9 lenses put it at 60–85%; 2 put it at 25–40%" (direction) or "all 9 lenses lean yes, from 55% to 90%" (degree) |
| `debated` | whether a pair debated the question |
| `base_case` | the majority debater's rebuttal if a majority-side agent debated it, else the `reason` of the majority agent closest to the majority median with the most verified data evidence; plus one verified data quote |
| `minority_case` | the minority debater's **rebuttal** (≤ 120 words) with its verified quote, `source: rebuttal`. Fallback when the rebuttal is missing or flagged (`persona leakage`, `over length`): the majority debater's **steelman** of the minority, `source: steelman`. Undebated: the most extreme minority agent's reason, `source: reason`. The minority states its own case; the opponent's softened summary is only the fallback |
| `crux` | the agreed crux (both `crux_agreed == yes`), else the minority side's crux; empty for undebated blocks |
| `crux_check` | `resolved`, `what_the_data_says`, `quote` when §8 ran on this question, else `null` |
| `settles_on` | crux check `settles_on`, else the question's `resolves_on`/`settles_with`, else the earlier of the two `would_change_my_mind` dates |
| `carried` | `unchanged since <date> (median a% → b%)` when the question was carried, else empty |
| `type` | `direction`, `degree` or `consensus` |

Consensus block, same schema: `type: consensus`, `debated: false`; `question`, `counts`,
`count_phrase` ("all 9 lenses within 15 points of 70%"); `base_case` = the majority reasons (the
reason of the agent closest to the median, plus one verified data quote); `minority_case` = the
red-team `case` and its first verified quote, `source: red_team`; `crux` = the red team's
`crux.claim`; `crux_check`; `settles_on`. The brief calls it the red-team case.

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
claims out of WHAT IT MEANS. The `take` prose is not used.

`07_full_record.md` is still written (archive, RUBRIC, debugging) but is no longer sent to the
synthesizer. Draft input drops from ~50 KB of record to ≤ 14 KB.

### 11.4 The brief (decision 1: counts, no agent names)

- `BRIEF_SECTIONS` moves from the orchestrator into `recon/schemas.py` (the orchestrator, the
  checks and `llm.py`'s dry-run brief import it from there), and `BRIEF_SECTIONS[3]` changes from
  `THE CONTRARIAN CASE` to `WHERE THE VIEWS SPLIT`, in that constant, the draft prompt, the filter
  prompt's section list, and the synthesizer persona.
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

- **No agent names**: the snake-case names (`policy_analyst`, `user_agent`, `macro_strategist`,
  `ai_engineer`) anywhere in the brief. Case-sensitive whole-word title-case names
  `\b(Trader|Narrator|Builder|Analyst|Skeptic|Policy Analyst|User Agent|Macro Strategist|AI Engineer)\b(?!s)(?!\.ai)(?! [A-Z][a-z])`
  only inside WHAT IT MEANS and WHERE THE VIEWS SPLIT, the two sections written from the lens
  notes and the split sheet. The lookaheads let through plurals ("Analysts expect"), product names
  ("Builder.ai") and a name followed by a capitalised noun ("Analyst Firm", "Builder Program");
  a sentence opening "Analyst firm …" is still a hit and is accepted as a warning. Hit →
  `checks.agent_names[]`; the brief still ships (Phase D decides blocking) and the run status
  becomes `partial`.
- **Counts match**: every `N of M` in WHERE THE VIEWS SPLIT must equal a `majority`/`minority`
  and `n` in the split sheet. Mismatch → `checks.count_mismatch[]`.
- **Blocks ≤ 3**, and a consensus day's section contains "case against".
- **Process words** in the whole brief, narrowed to phrases that only describe our process:
  `(?i)\b(our|the) (agents?|lenses|analysts) (debated|conceded|challenged|voted)\b|\bvot(e|ed) of (the )?(agents|lenses)\b|\bour lenses\b|\bthe debate showed\b`
  → `checks.process_words[]`. Ordinary news phrasing ("regulatory challenges", "debate over
  CLARITY", "Senate voted", "legal challenge") does not match.

---

## 12. JSON schemas

All in `recon/schemas.py`, using its helpers (`obj` makes every property required and sets
`additionalProperties: false`, as Codex strict mode needs). LLM call schemas go in `ALL` (written
to `phases/schemas/` for `--output-schema`); artifact schemas go in a new `ARTIFACTS` dict, used
only by `validate()` in tests and before `save()`. `validate()` gains support for `"integer"`
(coerces an integral float or numeric string, rejects 0.5), `"boolean"` and
`"type": [..., "null"]`. `integer` is used in `ALL` (Codex strict mode accepts it); nullable
types and free-key objects are artifacts only, never sent to Codex. New helpers:

```python
def b(desc=""): return {"type": "boolean", **({"description": desc} if desc else {})}
def i(desc=""): return {"type": "integer", **({"description": desc} if desc else {})}
def nullable(sch): return {**sch, "type": [sch["type"], "null"]}
def free(desc=""): return {"type": "object", **({"description": desc} if desc else {})}   # free keys, artifacts only
DATE = s("YYYY-MM-DD or empty")
QIDS = ["q1", "q2", "q3", "q4", "q5"]
BRIEF_SECTIONS = ["WHAT HAPPENED", "WHAT IT MEANS", "MARKET MOOD", "WHERE THE VIEWS SPLIT", "AI NEWSLETTER",
                  "FUNDRAISING", "KOREA", "AI EDUCATION", "RISKS", "WHAT TO WATCH", "SCORECARD"]   # moved here, §11.4
```

The call schemas below are committed as JSON in `schemas/debate/<name>.json` (`question`,
`triage`, `take`, `debate_challenge`, `debate_response`, `red_team`, `crux_check`), generated from
this section. `tests/test_schemas.py` asserts `schemas.ALL[name] == json.load(schemas/debate/<name>.json)`
for each (`question.json` against `schemas.QUESTION`), so the spec, the files and the code
cannot drift. Probabilities and weights are
integers: code still runs `norm_p` and the clamps (§7.2, §2.3) because a non-Codex provider (dry
run, claude CLI) does not enforce the schema.

### 12.1 Triage (replaces `TRIAGE`) and the take (reordered `TAKE`)

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
    weight=i("1, 2 or 3"),
    carried_from=s("ledger id such as 2026-09-11-q2 if this re-asks an open question, else empty"),
)
TRIAGE = obj(
    environment=enum(ENVIRONMENTS, "what dominates today's data"),
    depth=enum(["quiet", "normal", "risk"]),
    reason=s("one sentence: why this environment and depth"),
    weight_agents=arr(enum(AGENTS)),
    questions=arr(QUESTION),
)

TAKE = obj(                                            # same fields as Phase B; positions first, take last
    positions=arr(obj(
        question_id=s(),
        probability=num("0-100: your probability that the answer is yes"),
        reason=s("at most 25 words, from your lens"),
        evidence=arr(EVIDENCE),
    )),
    claims=arr(obj(
        claim=s(),
        section=s(),
        quote=s("verbatim from the package"),
        confidence=enum(["low", "medium", "high"]),
    )),
    summary=s("your main call in one or two sentences, at most 50 words"),
    prediction=obj(
        text=s("one testable prediction"),
        probability=num("0-100"),
        resolves_on=s("YYYY-MM-DD"),
        metric=s("the number or event that decides it"),
    ),
    novel=s("one thing you expect the other analysts to miss"),
    watching=arr(s("an item to track next session")),
    take=s("your analysis in your persona's own voice, at most 150 words, written last"),
)
```

Phase B readers keep working: `id`, `text`, `resolves_on`, `settles_with`, `lenses` and every
TAKE key are unchanged. TAKE keeps `num` for probabilities (Phase B's readers and `pos_map` handle
it, and `norm_p` fixes fractions); the new call schemas use `i`.

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
    new_probability=i("integer 0-100: your probability now"),
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
    probability=i("integer 0-100: your own probability that the answer is yes"),
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

**Dry-run provider** (`recon/llm.py`). Phase B's `_dry_json` cannot produce a gate-passing
question: its text is `[dry-run] - text. …` with no `?`, its two array items have identical text
(the Jaccard rule drops one), `baseline_quote` is `[dry-run] baseline_quote` (fails gate rule 4),
`weight` is 0–9, `carried_from` is non-empty and `resolves_on` is fixed at 2026-10-31 (out of the
window for `--as-of 2026-09-11`). Changes:

- `enum(QIDS)` fields take a question id present in the prompt (it already does this for keys
  named `question_id`, and enum handling must not win over that);
- objects that have both `kind` and `baseline_quote` (the QUESTION shape) are special-cased:
  `questions` gets 5 items; item `k` has `kind` cycling `event, threshold, direction, event,
  judgment`; `baseline_quote` = the k-th line from `_dry_quotes` (lines that exist in the
  package); `text = f"Will {baseline_quote[:60]} hold through day+7?"` (distinct per item, ends
  with `?`); `resolves_on` = run day + 7, with the run day parsed from the prompt's
  `TRIAGE AND QUESTIONS OF THE DAY (YYYY-MM-DD)` (empty for `judgment`); `weight` = 1 + k % 3;
  `carried_from` = `""`; `metric`, `comparator`, `threshold` filled for threshold/direction,
  empty otherwise; `lenses` = two agents from `DOMAIN_LENSES`;
- integers (`i`) get integers; `RECON_DRY_SPREAD` (`wide` default: probabilities 5–95 by agent
  hash, as today; `narrow`: 60 ± 5) so a dry run can exercise both the debate path and the
  consensus path;
- `_call_dry_run`'s canned brief is built from `schemas.BRIEF_SECTIONS` (not its own hard-coded
  tuple with `THE CONTRARIAN CASE`), and its WHERE THE VIEWS SPLIT section copies the first
  `count_phrase` found in the prompt's split sheet and, on a consensus sheet, the words "case
  against", so the §11.5 checks pass on a dry run.

### 12.3 Artifacts (programmatic, `ARTIFACTS`)

```python
P = i("integer 0-100")
EV_CHECKED = obj(section=s(), quote=s(), status=enum(["verified", "partial", "unverified", "empty"]),
                 cls=enum(["data", "social", ""]), doc=nullable(s()), line=nullable(i()))

PAIR = obj(question_id=s(), high=enum(AGENTS), low=enum(AGENTS), p_high=P, p_low=P,
           gap=i(), score=num(), both_lenses=b(), repeat_of_yesterday=b())
PAIRING = obj(
    day_type=enum(["debate", "split_unpaired", "consensus", "no_questions"]),
    depth=enum(["quiet", "normal", "risk"]), target=i(), gap_min=i(),
    pairs=arr(PAIR),
    candidates_considered=i(),
    unpaired=arr(obj(question_id=s(), range=i(), reason=s())),   # split_unpaired questions and why
    red_team=nullable(obj(agent=enum(AGENTS), question_id=s(), median=num(), distance=num(),
                          reason=s())),
    eligible=obj(**{q: arr(enum(AGENTS)) for q in QIDS}),   # all five keys, empty arrays allowed
    positions_evidence=free("{agent: [EV_CHECKED]}; active agents only, so §18 can retire agents"),
    budget=obj(used=i(), budget=i(), ceiling=i(), target_before_budget=i(), crux_check_planned=b()),
)

EV_NEW = obj(section=s(), quote=s(), status=enum(["verified", "partial", "unverified", "empty"]),
             cls=enum(["data", "social", ""]),
             new_evidence_source=enum(["crux_data", "challenger", "own", "other"]), qualifies=b())
SIDE_MOVE = obj(agent=enum(AGENTS), take=P, requested=nullable(P), gated=P, delta=i(),
                clamped=b(), rescaled=b(), new_evidence=arr(EV_NEW),
                new_evidence_verified=i(), new_data_evidence=i(),
                evidence_source=enum(["crux_data", "other", "none"]))
DEBATE_SCORE = obj(
    question_id=s(), high=enum(AGENTS), low=enum(AGENTS), status=enum(["two-sided", "one-sided", "failed"]),
    gap_before=i(), gap_after=nullable(i()),
    moves=arr(SIDE_MOVE),
    verdicts=obj(high=s(), low=s()),
    steelman_fair=obj(high=s(), low=s()),          # as rated by that side about the other's steelman of it
    crux_agreed=b(),
    evidence=obj(high=obj(claimed=i(), verified=i(), data=i()), low=obj(claimed=i(), verified=i(), data=i())),
    flags=arr(obj(agent=enum(AGENTS), flag=s(), where=enum(["challenge", "response"]))),
    crux_check=nullable(obj(resolved=s(), leans=s())),
    closed_on_data=b(), closure_without_evidence=i(), effect=s(),
    live_split=b(), useful=b(),
)

STATS = obj(n=i(), median=nullable(num()), mean=nullable(num()), min=nullable(num()), max=nullable(num()),
            range=nullable(num()), iqr=nullable(num()), majority_side=enum(["yes", "no", "even", ""]),
            majority_count=i(), minority_count=i())
QUESTION_STATS = obj(id=s(), ledger_id=s(), text=s(), weight=i(), debated=b(),
                     take_stats=STATS, final_stats=STATS, finals=free("{agent: probability}"))

AGENT_RUN_SCORE = obj(
    run_id=s(), day=DATE, agent=enum(AGENTS), distinctness=nullable(num()), endpoint_count=i(),
    evidence_rate=nullable(num()), data_share=nullable(num()), unique_numbers=i(),
    moves_with_evidence=i(), moves_capped=i(), verbal_concessions=i(), steelmen_rejected=i(),
    leakage_flags=i(), novel=s(),
)

SPLIT_BLOCK = obj(
    type=enum(["direction", "degree", "consensus"]), debated=b(), question_id=s(), ledger_id=s(),
    question=s(), resolves_on=DATE, settles_with=s(),
    counts=obj(n=i(), majority=i(), minority=i(), median=num(), range=arr(i())),
    count_phrase=s(),
    base_case=obj(text=s(), quote=s()),
    minority_case=obj(text=s(), quote=s(), source=enum(["rebuttal", "steelman", "reason", "red_team"])),
    crux=s(), crux_check=nullable(obj(resolved=s(), what_the_data_says=s(), quote=s())),
    settles_on=obj(observable=s(), by_date=DATE),
    carried=s(),
)
SPLIT_SHEET = obj(day=DATE, run_id=s(), day_type=enum(["debate", "split_unpaired", "consensus", "no_questions"]),
                  blocks=arr(SPLIT_BLOCK), no_split_line=s(), bytes=i())

LEDGER_LINE = obj(
    type=enum(["question"]), ledger_id=s("<run_id>-<qid>; <day>-<qid> for the daily run"), run_id=s(), day=DATE,
    question=QUESTION,                                 # as gated, weight clamped
    finals=free("{agent: gated probability}"), take_values=free("{agent: take probability}"),
    final_stats=STATS, debated=b(), split_type=enum(["direction", "degree", "consensus", "none"]),
)
```

`run.json` (`schema_version` 1 → 2) gains: `pairing` (as above, without `positions_evidence`),
`debates` (list of `DEBATE_SCORE`, replacing Phase B's shorter `debates`), `red_team`,
`crux_check`, `split_sheet`, `agent_scores`, `usage.budget_skips[]` (§1.1), and `questions[]`
items gain `ledger_id`, `final_stats.majority_count`/`minority_count`. `edges` use types `pair`
(both directions) and `redteam`; `deep_dive` is `null`. The record shapes the RUBRIC page reads are
kept by the adapters in §14.1.

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
starts with a comment header naming its schema and inputs. The persona block, the debate format
and the citation rule are passed in as variables (`role`, `debate_format`, `citation_rule`) so
Phase B's constants remain the single source. The templates are committed with this spec
(`config/prompts/debate/`); §13.2–13.8 below are their exact text as committed, and from then on
the files are the source.

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
3. verdict and new_probability (a whole number 0-100). The rule: you may move up to 5 points on
   argument alone, and the other view's argument and its quotes count as argument. A larger move counts
   only with a new fact: a verbatim quote that neither side has cited, from the crux data above or the
   excerpts, about what the disagreement turns on. A program enforces this: larger moves without such a
   quote are cut back to 5 points and recorded. Social-media quotes do not justify a larger move.
   Hold when the challenge brings no new fact; there is no credit for agreeing.
4. reason: at most 60 words — what changed and why, or why nothing did.
5. new_evidence: 0-3 quotes that neither side cited before, verbatim, with their section.
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
observable and date); evidence (0-3 verbatim quotes with section); probability (your own honest number,
a whole number 0-100 — it does not have to sit on the other side of 50); would_change_my_mind.
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
- A block with no crux: leave out "what it turns on" and do not invent one.
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

### 13.8 `config/prompts/debate/take.md`

The take task (§3 item 1). Appended after `shared()` + the questions block, which stay the cached
prefix; `take_prompt` keeps building `context` (memory, analyst model, state) as Phase B does and
passes `ROLE.format(persona=…)` and `CITATION_RULE` in. `lens_data` is the `LENS_RAW` block (§3
item 3), or "(no lens data today: cite the package)" when it is empty.

```
<!-- schema: take · inputs: role, context, lens_data, citation_rule -->
{{role}}

{{context}}

YOUR LENS DATA (raw material picked for your lens; the other lenses were not given it):
{{lens_data}}

TASK: YOUR TAKE ON TODAY'S PACKAGE.
{{citation_rule}}

If historical context is provided, reference yesterday's brief: note what changed, what predictions held,
what was wrong. Continuity matters. Answer each question from your lens. At least one evidence item per
position must come from YOUR LENS DATA block (if that block says there is none today, cite the package).
The ecosystem includes world events, macro, crypto/BTC/ETH, DeFi, stablecoins, AI/ML, regulation,
prediction markets, fundraising and infrastructure.

Reply with one JSON object matching the schema, writing the fields in this order:
- positions: one entry for EVERY question of the day above (question_id q1, q2, ...): your probability
  0-100 that the answer is yes, a reason of at most 25 words from your lens, and 1-2 evidence items, each
  a quote copied character for character (at most 200 characters) with its section name, at least one of
  them from YOUR LENS DATA. A program checks every quote; a quote that is not there counts against you.
- claims: up to 4 key factual claims behind your positions, each with a verbatim quote, section and
  confidence.
- summary: your main call in one or two sentences (at most 50 words).
- prediction: one testable prediction with a probability, a resolution date (YYYY-MM-DD) and the metric.
- novel: one thing the other analysts will probably miss.
- watching: 2-5 short items to track next session.
- take: last, at most 150 words in your persona's own voice. Give numbers; do not repeat the positions.
```

---

## 14. Code changes, file by file

| File | Change |
|---|---|
| `recon/debate.py` (new) | Pure functions, no I/O beyond what is passed in: `gate_questions`, `pair`, `pick_red_team`, `excerpts`, `crux_terms`, `crux_search`, `norm_p`, `gate_move`, `score_debate`, `final_positions`, `question_stats`, `agent_run_score`, `split_sheet`, `render_split_sheet`, `lens_notes`, `lens_extras`, `anonymise`, `leakage_flags`, `budget_pairs`, `legacy_moves`, `legacy_response`. Constants `GAP_MIN_DEFAULT`, `FREE_MOVE`, `CRUX_GAP`, `DOMAIN_LENSES`, `LENS_RAW`, `LENS_LABEL`, `LENS_TIER` (empty unless §15.0 says otherwise). |
| `recon/data/common_words.txt` (new) | ~5,000 common English words for the crux-search entity rule (§6). |
| `recon/prompts.py` (new) | `render()` (§13.1). |
| `config/prompts/debate/*.md` (new, committed) | The seven templates in §13: `questions`, `take`, `challenge`, `response`, `red_team`, `crux_check`, `brief_split`. |
| `schemas/debate/*.json` (new, committed) | The call schemas of §12.1–12.2 as JSON (§12). |
| `recon/schemas.py` | §12: new schemas, reordered `TAKE`, `BRIEF_SECTIONS` moved here, `LEGACY`, `ARTIFACTS`, `validate()` nullable/boolean/integer. |
| `recon/evidence.py` | `locate(quote, docs)` with section, class, doc and anchor line over package, raw, view and social (§3); `Corpus` built once per run and reused (it is rebuilt today in `ph_positions` and `ph_checks`). |
| `recon/llm.py` | Dry-run changes of §12.2: question objects that pass the gate, enum question ids, integers, `RECON_DRY_SPREAD`, the canned brief from `schemas.BRIEF_SECTIONS`. |
| `recon/orchestrator.py` | `PHASES`/aliases and `choices=PHASES + list(PHASE_ALIASES)`; call budget and ceiling (§1.1); `ph_triage` uses `questions.md`, the gate and the ledger reads; `take_prompt` renders `take.md` with lens extras; new `ph_pairing`, `ph_challenges` (pairs or red team), `ph_responses` (crux search first, gate after), `ph_cruxcheck`, `ph_split`; `ph_positions` rewritten around `debate.py`; `ph_synthesis` uses `brief_split.md`, the split sheet and lens notes, and `raw_sections()` reads the run folder (§15.1); `ph_checks` adds §11.5; `ph_record` and `build_record` per §14.1; the `--skip-collect` assembly writes `00_raw_data.md` (phase-e §4.1a); `TENSIONS`, `wildcard()`, `ph_deepdive` deleted; `--as-of`, `--replay`, `--state-dir` flags, `RECON_STOP_AFTER` (§15). |
| `recon/agentmem.py` | No format change; fed through the adapters in §14.1. |
| `recon/export.py` | Nothing for the orchestrator path (it uses `run.json` as is); `LAYER`/`SECTION_LAYER` change in Phase E. |
| `personas/synthesizer.md` | §11.4 edits. |
| `.gitignore` | `config/questions/`, `config/agent_scores/`. |
| `tests/` (new) | §17, with the fixtures of §17.1 committed under `tests/fixtures/`. |
| `scripts/spread_probe.py`, `scripts/replay_report.py` (new) | §15.0 and §15.3; read run folders only, no LLM. |

Effort: 2 days of Claude time, as the plan says; `debate.py` and its tests are about half of it.
The spread probe (§15.0) runs between the input changes and the rest.

### 14.1 Adapters for Phase B readers

Phase B code still expects the old shapes: `agentmem.update_memory` and `update_state` read
`pos["moves"][agent]` as `[{question_id, from, to, delta, reason}]`; `ph_record` and
`build_record` read a challenge's `rec["data"]["text"]` and `crux` as a string; the RUBRIC
`recon-page.js` (line 61) renders `challenges_made[].text` and `challenges_received[].text` as
strings. Without adapters the memory Lessons lines are empty, the RUBRIC challenge text is blank
and the crux shows `[object Object]`.

- `debate.legacy_moves(agent) -> [{question_id, from: take_p, to: gated, delta, reason:
  response.reason}]`, one item per debated question; passed to `agentmem` as `pos["moves"]`.
- `debate.legacy_response(agent) -> {verdict: strongest of its debates (concede > narrow > hold),
  text: its response reasons joined, final_positions: gated values}`; agents that did not debate
  get `None` as in Phase B. Lessons lines then come from real gated moves.
- `ph_record`: `agents[].challenges_made[]` and `challenges_received[]` items become
  `{to | from, type: "pair" | "redteam", text: rebuttal (or the red-team case), crux: crux.claim,
  question_id}`, all strings; `agents[].response` = the debate reasons joined; `agents[].verdict` =
  the strongest verdict.
- `edges`: `pair` edges in both directions. A red-team edge has no opponent, so it is drawn from
  the red-team agent to the agent nearest the median on that question (the page drops edges
  without both `from` and `to`), type `redteam`.
- `build_record` (writes `07_full_record.md`) is rewritten for the pair, red-team and crux-check
  shapes: per debate, both steelmen, cruxes, rebuttals, responses with requested and gated
  values, flags and the crux check.

---

## 15. The replay harness

### 15.0 The spread probe (gate before the debate code, §0.1)

Purpose: measure whether the takes disagree by more than their own noise before pairing code is
written, and set `GAP_MIN` from that measurement.

Code: Phase B's triage and takes phases as committed, plus the input changes of §0.1 step 1
(`take.md`, TAKE order, lens extras, `locate`, `norm_p`), run on the droplet with
`RECON_STOP_AFTER=takes` (the one-line switch of §15.2, landed with step 1) and `--state-dir`
pointing into the probe's run folder (cold memory, as replays).

| Run id | Package | What | Calls |
|---|---|---|---|
| `2026-09-11-p1` | `briefs/2026-09-11` | triage + 9 takes | 10 |
| `2026-10-04-p1` | `briefs/2026-10-04` | triage + 9 takes | 10 |
| `2026-10-04-p2` | same | 9 takes again on `p1`'s `triage.json` (`--from-phase takes` on a copy of `p1`), for test-retest | 9 |
| `2026-10-04-p3` | same | skeptic and macro_strategist only, again on `p1`'s triage, with `RECON_MODEL_ANALYST` set to the SYNTH model; then the same two at `RECON_EFFORT_ANALYST=high` | 4 |

About 33 calls. `scripts/spread_probe.py <run ids>` (no LLM) reads the `takes/*.json` and writes
`briefs/spread_probe.md` plus one model-log row:

- per question: `n`, median, range, the largest gap between two agents that straddles the median,
  and how many agents sit on each side of 50;
- per agent: test-retest `|Δp|` between `10-04-p1` and `10-04-p2` on each question, and its median;
- `GAP_MIN = max(20, 2 × median retest |Δp|)` over all agents and questions, rounded up to an
  integer. It becomes `GAP_MIN_DEFAULT` in `recon/debate.py` (the env var `RECON_PAIR_GAP` still
  overrides it) and is written to the model log;
- per package: the number of questions whose largest straddling gap is ≥ `GAP_MIN` **and**
  exceeds the two agents' combined retest `|Δp|` on that question;
- lens diversity: for skeptic and macro_strategist, the distance from the median on each question
  under ANALYST (`p1`, `p2` mean) and under SYNTH model and high effort (`p3`), each minus that
  agent's retest `|Δp|`. If the retest-adjusted distance rises on at least half the questions, the
  setting is kept in `LENS_TIER` for that agent (and its call cost goes in §16); otherwise
  `LENS_TIER` stays empty.

Gate: at least one question per package must clear `GAP_MIN` against the retest noise. If not,
the inputs are fixed first (lens extras, the take task, triage's "contestable" rule, per-lens
model) and the probe is rerun; pairing, challenge and response code is not written until it
passes. The probe's result and decision go in the model log and in the replay report page.

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
  `config/` for the daily run; the run folder for dry, replay and tagged runs, §2.6).
- The September `00_historical_context.md` (June values, F3) is replaced by an empty file in
  replays, as Phase A's fix would have done.
- **The synthesis reads the run folder, not `data-sources/`.** Phase B's
  `ph_synthesis.raw_sections()` builds `AI_RAW`, `EDU_RAW`, `KR_RAW` and `FUND_RAW` from
  `self.data_dir/<src>/latest.md`, i.e. today's collectors; a 2026-09-11 replay's draft would get
  October AI news, Korea and fundraising. `raw_sections()` is changed to read the run folder's
  `00_raw_data.md` always (the run folder is the record): `AI_RAW` = the `# AI & Tools
  Intelligence` block + `section(self.dir/"00_raw_data.md", "## AI & TECH NEWS", 5000)`; `EDU_RAW`
  = `## AI EDUCATION & WORKFORCE`; `KR_RAW` = `## KOREA — AI` + `## KOREA — CRYPTO & MARKETS`;
  `FUND_RAW` = the `# Fundraising Intelligence` block + `## RECENT FUNDRAISING ROUNDS`, with the
  same byte caps as today (phase-e §4.5 adds the Changelogs and ZDNet Korea blocks, also from the
  run folder). A missing block gives an empty string, as today. A `--replay` reads
  nothing under `data-sources/` (checked by hashing in §17.4).

The September folders are on the droplet (`/home/recon/recon-v2/briefs/2026-09-10` and
`-09-11`: packages 61,066 and 169,518 bytes; 6 and 8 `# SECTION` markers; neither has
`01_social.md`, so the crux search skips it there). The 09-10 package has no AI & TOOLS or
FUNDRAISING section; `build_agent_package.py` handles that (`FALLBACK_NAMES`). The third replay
package is `briefs/2026-10-04` (package 176,002 bytes, 8 `# SECTION` markers, raw 159,068 bytes,
`01_social.md` 8,036 bytes; the Phase A live run whose export shows the 9 defensive `act_on`
votes). Replays run on the droplet from the `v2` checkout once Phase C is deployed there (the
droplet worktree is at `a585ef1` today, behind Phase B).

### 15.2 Runs

| Run id | What | Calls (est.) |
|---|---|---|
| `2026-09-10-c1` | full new flow, FAST triage | ≤ 24 |
| `2026-09-11-c1` | full new flow, FAST triage | ≤ 24 |
| `2026-10-04-c1` | full new flow, FAST triage | ≤ 24 |
| `2026-10-04-c1s` | stability: takes rerun once on `c1`'s `triage.json` (`--from-phase takes` on a copy), stopped after `pairing` (`RECON_STOP_AFTER=pairing`) | 9 |
| `2026-09-11-c2` | triage only, on ANALYST (`RECON_TRIAGE_TIER=analyst RECON_STOP_AFTER=triage`, same `--replay`/`--as-of`), to compare the questions (§2.1) | 1 |

`RECON_STOP_AFTER=<phase>` is a small new switch: the driver returns after that phase. The
stability check is on the top pair, so the rerun needs only `pairing.json`.

### 15.3 Report

`scripts/replay_report.py <run_id> --old ~/innovlabs/recon-exports/runs/<date>.json` (runs
anywhere; reads the run folder's `run.json` and the old export) writes
`briefs/<run_id>/replay_report.md` and prints one model-log row. Columns, new vs old:

| metric | new run from | old run from |
|---|---|---|
| questions kept / dropped by the gate | `triage.json` | — |
| take spread: per question range, largest straddling gap, gap vs retest noise | `positions.json` + `briefs/spread_probe.md` | — |
| real splits found (`live_split` debates), `useful` debates, `effect` per debate | `positions.json` | deep dive gap (09-10: one, ended in agreement; 09-11: one, ended in agreement) |
| day type (debate, split_unpaired, consensus), red team | `pairing.json` | — |
| `closure_without_evidence`, gap_after/gap_before per debate | `positions.json` | — |
| evidence verification rate, data share | `positions.json` | not measured (cites empty) |
| concession rate (concede verdicts / responses), verbal concessions, soft moves (≥ 5 points towards the opponent without qualifying evidence) | responses | "I am updating my position": 6 of 9 (09-10), 7 of 9 (09-11) |
| moves capped by the gate, `new_evidence_source` counts | responses | — |
| persona leakage flags, agreement openers | challenges | 09-11: 6 of 11 challenges open by agreeing |
| citation overlap (mean Jaccard) | `positions.json` | recomputed from the old takes in the export |
| Polymarket structure points in the brief (F14) | grep the new brief | 0 on 09-11 |
| HIMS tokenized-price claim as debate evidence (F13) | evidence items with class `social` | in 6 of 9 takes, 7 of 9 votes |
| hindsight Brier: minority debater vs median (§15.4) | ledger + resolutions | — |
| brief: sections in order, words, agent names, count mismatches | `checks.json` | export |
| calls (incl. re-asks), budget skips, input / cached / output tokens, wall time | `run.json → usage` | 61 calls, 1.17 M / 1.36 M input |

### 15.4 Hindsight scoring (free, no LLM)

The September questions have resolved by now. For `threshold`/`direction` questions on series with
free history — DeFiLlama `historicalChainTvl` (total and per chain), CoinGecko
`/coins/{id}/market_chart/range`, alternative.me Fear & Greed `?limit=60` — the report script
resolves them and computes a Brier score per agent, for the median, and for the **minority
debater** of each debated question (the endpoint on the minority side of 50, or the endpoint
further from the median on a degree split) against the median on the same questions. `event`
questions are resolved by Claude reading public news once and are marked `manual`. The 10-04
questions resolve 1–30 days later; the report is rerun then. This gives the first Brier numbers
before any live run, and the outcomes are appended to the replay's own ledger only.

### 15.5 Pass bar for the replays

The three replays (09-10, 09-11, 10-04) together:

- (a) **real spread**: in each replay, at least one question whose largest straddling take gap
  exceeds `GAP_MIN` and the two agents' combined retest `|Δp|` (§15.0);
- (b) **a live split**: at least one `live_split` across the three packages;
- (c) **no closure by politeness**: for debates without crux-data evidence, median
  `gap_after / gap_before ≥ 0.6`;
- (d) **soft moves**: moves of 5 or more points towards the opponent without qualifying (crux-data
  or other new verified data) evidence in ≤ 30 % of responses, whatever the verdict label says;
- (e) **stability**: in the `2026-10-04-c1s` rerun, the top pair lands on the same question as in
  `2026-10-04-c1`;
- (f) **hindsight**: the minority-debater Brier vs the median is reported for every resolved
  question (a number, not a threshold; with few questions it is a signal, not a verdict);
- lens extras: `lens_extra_bytes ≥ 2 KB` for at least 7 of 9 agents, and `citation_overlap` lower
  than the old run's (phase-e §4.5b);
- evidence verification rate (verified + partial) ≥ 80 % on debate evidence;
- persona leakage flags ≤ 1 per run;
- calls never exceed `RECON_CALL_CEILING`, every budget skip is logged, and ≤ 0.6 M input tokens
  per replay;
- the brief has the 11 sections in order with WHERE THE VIEWS SPLIT, no agent names, no count
  mismatch; a `split_unpaired` day does not print "No real split";
- the F13 claim is not the basis of any debate on a measurable question (no pair on a
  non-judgment question whose endpoints' evidence is social-only — enforced by eligibility,
  verified in the report).

Items (a)–(e) can fail on the question Phase C exists for; that is the point. If (a) fails, the
problem is upstream of the debate and goes back to §15.0. A failed bar item is fixed and that
replay rerun (`--from-phase` the earliest affected phase). The replays, the probe, and each
side-by-side (old brief, new brief, split sheet) are published as one private artifact page for
Eric with the model-log rows, and the result is reported, not asked.

---

## 16. Cost per stage

Per-call input = slim Codex prefix (~2.2 K, measured) + prompt. Prompt tokens are estimated at
~3.6 bytes per token for this mixed English/number text (Korean headlines are denser; the live
runs replace these estimates). The shared block is the ~65–80 KB agent view + sector context +
historical context + scorecard ≈ 85 KB ≈ 24 K tokens, byte-identical across triage and takes.

**Cache.** Provider prompt caches are per model. Triage runs on FAST (gpt-5.6-luna) and the takes
on ANALYST (gpt-5.6-terra), so triage cannot warm the takes' prefix, and the first `PARALLEL=5`
wave of takes starts cold at the same moment. The cached share is therefore expected only from
take 6 onward (4 of 9 takes), not "after the first call". Total input tokens are unchanged by
this, and the input-token pass bar stays as it is. The replays record the cached share of the
takes; if it is under 40 %, `ph_takes` runs one take alone first and the other 8 after it (about
1.5 min more wall time) — decided on the replay numbers, not before.

**Budget.** `RECON_CALL_BUDGET = 24` (§1.1) counts re-asks and failures. With the filter call still
in place (Phase D removes it), a normal day with clean takes has 24 − 10 − 2 = 12 calls left:
3 pairs and no crux check. One take re-ask leaves 11: 2 pairs and the crux check. After Phase D
drops the filter, a normal day has 3 pairs and the crux check.

| Stage | Phase B today (as built) | Phase C, normal day (3 pairs, 9 agents, budget 24) | Phase C, consensus day | Output tokens (C, normal) |
|---|---|---|---|---|
| Triage (questions) | 1 FAST / ~27 K | 1 FAST / ~28 K (+ ledger lines ~1 K); re-ask only if ≤ 1 question survives | same | ~1.2 K |
| Takes | 9 ANALYST / ~300 K (~200 K cached) | 9 ANALYST / ~315 K (lens extras +1.6 K each; cached from take 6 on) | same | ~9 K (`take` ≤ 150 words, was ~14 K) |
| Pairing | 0 (wildcard is programmatic) | 0 | 0 | — |
| Challenges | 11 ANALYST / ~70 K (both full takes in each) | 6 ANALYST / ~38 K (~6.3 K each) | 1 red team / ~7 K | ~3 K |
| Crux search | — | 0 | 0 | — |
| Responses | 9 ANALYST / ~65 K (+ votes) | 6 ANALYST / ~42 K (~7 K each, incl. crux data) | 0 | ~2 K |
| Deep dive / crux check | 0–2 ANALYST / ~16 K | 0 (dropped by the budget; 0–1 with 2 pairs) | 0–1 / ~5 K | — |
| Votes, memory, state | 0 (Phase B) | 0 | 0 | — |
| Draft | 1 SYNTH / ~30 K (full record) | 1 SYNTH / ~20 K (split sheet + lens notes) | ~19 K | ~3.5 K |
| Filter (until Phase D) | 1 SYNTH / ~38 K | 1 SYNTH / ~38 K | same | ~3.5 K |
| **Total** | **32–34 calls / ~0.55 M** | **24 calls / ~0.49 M** (2 pairs + crux check: 22 / ~0.46 M) | **13–14 calls / ~0.41 M** | **~22 K** |
| Quiet day (1 pair) | — | 16–17 calls / ~0.43 M | | |
| `split_unpaired` day | — | 12 calls / ~0.40 M | | |
| After Phase D (no filter), normal day | — | 23–24 calls (3 pairs + crux check) / ~0.46 M | | |

Ceiling: `RECON_CALL_CEILING = 32` bounds the worst case (every take re-asked once and every
schema re-ask used) at 32 calls; the old unbounded case was up to 4 take calls per agent at
25–35 K input each.

What this says honestly: Phase C cuts calls by about a quarter (and by 60 % on consensus days),
but input tokens only by ~10 %, because the nine takes carry the shared block and dominate. The
real token lever after C is the roster (6 agents ≈ −105 K) and Phase D's filter removal (−38 K).
Wall time: takes 2 waves of 5 (~3 min), challenges and responses 2 waves each (~2 min),
synthesis (~4 min): ~9 min of LLM time, down from Phase A's 12 min 45 s.

Quota to validate Phase C: spread probe (~33 calls) + 3 replays (~70 calls) + stability rerun
(~9–21) + 1 triage call + live schema smoke (6 tiny calls) + 2 live runs (~48 calls) ≈ 170 calls,
about two and a half old v1 runs.

---

## 17. Test plan

All tests are stdlib `unittest` (`python3 -m unittest discover tests`), no network, under 10 s,
and runnable on the desktop and the droplet alike.

**Fixtures.** `briefs/` is gitignored and exists only on the droplet, so the package fixtures are
committed: `tests/fixtures/package/2026-09-11/` and `tests/fixtures/package/2026-10-04/`, each with
`00_data_package.md`, `00_raw_data.md`, `00_scorecard.md`, `01_filtered.md` and (10-04 only)
`01_social.md`, pulled from `/home/recon/recon-v2/briefs/` and trimmed to ≤ 200 KB per file
(the packages are 169–176 KB today, so most need no trimming; trimming cuts whole blocks from the
end of the longest sections and keeps every heading). Debate fixtures (takes, challenges,
responses) are hand-written in `tests/fixtures/debate/`.

### 17.1 Unit tests, `tests/test_debate.py` (pure functions)

| Test | Case | Expect |
|---|---|---|
| gate | question without `?`; "How much…?"; `resolves_on` 45 days out; two judgment questions; unverifiable baseline; Jaccard 0.7 repeat; 3 carried | each dropped with the right reason; ids renumbered q1… |
| gate | 1 survivor / 2 survivors | `needs_reask` true / false |
| gate | weight 0, 9, 2.6 | clamped to 1, 3, 3 with a `weight clamped` note |
| gate | baseline quote present only in `01_filtered.md` (re-rendered X line) | verifies |
| lens extras | every agent in `LENS_RAW` on the 09-11 and 10-04 fixtures | > 0 bytes each; ≤ 6 KB; macro_strategist's and skeptic's include package blocks; no added line occurs in `01_filtered.md` |
| locate | quote in the view only, in `# SECTION: SOCIAL`; partial match | `cls: social`; partial anchored to the best 4-gram line |
| norm_p | 0.65 with take values up to 80; 0.65 with take values all ≤ 1; 104 | 65 `fraction rescaled`; 1 (no rescale); 100 `clamped` |
| pairing | values 20/30/70/80 on q1, 45–55 on q2 | one pair on q1 (20 vs 80), none on q2 |
| pairing | 3 questions all split, 9 agents, budget allows 3 | 3 pairs, 6 distinct agents (cap 1) |
| budget | normal day, 12 calls free / 11 free / 13 free | 3 pairs, no crux check / 2 pairs + crux check / 3 pairs + crux check |
| pairing | 3 questions split, but `budget_pairs()` = 2 | 2 pairs, the two highest scores; skip logged |
| pairing | 3 questions split but all extremes are the same two agents | second pass uses cap 2; no agent in 3 debates |
| pairing | same pair yesterday, equal gaps | the other pair wins |
| pairing | an extreme agent with only unverified evidence | not an endpoint; next eligible agent pairs |
| pairing | an extreme agent with only social verified evidence, on a threshold question and on a judgment question | not an endpoint on the threshold question; an endpoint on the judgment question |
| pairing | weight 10 on one question | treated as 3 |
| pairing | both on the same side of the median, gap 40 | no pair |
| pairing | range ≥ GAP_MIN but no eligible straddling pair | `day_type: split_unpaired`, no red team |
| pairing | all within 12 points | `day_type: consensus`, red team = furthest agent if ≥ 10 from median, else skeptic |
| pairing | deterministic | same input in shuffled order → identical `pairing.json` |
| gate_move | +25 with no new evidence | gated +5, flag `update without evidence, capped` |
| gate_move | +25 citing only the challenger's verified data quote | gated +5, `argument only, capped`, source `challenger` |
| gate_move | +25 with a verified data quote from the crux hits that shares a crux number | gated +25, source `crux_data`, no flag |
| gate_move | +25 with a `partial` quote from the crux hits | +5 (partial does not qualify) |
| gate_move | +25 with a verified data quote sharing no crux term | +5 |
| gate_move | +25 with only a social quote | +5, `social evidence only, capped` |
| gate_move | −4 with nothing | −4, no flag; verdict concede + 3 → `verbal concession` |
| gate_move | verdict hold, +8 towards the opponent | gated +5, `hold but moved`, `soft move` |
| gate_move | quote already in own take | source `own`, does not qualify |
| score | gap 30 → 20 on argument | `live_split` true, `closure_without_evidence` 10, effect "narrowed from 30 to 20 on argument" |
| score | gap 30 → 8 with crux-data evidence | `live_split` false, `closed_on_data` true |
| score | crux check `partly` + `neither` / `partly` + `higher` | `useful` false / true |
| crux_search | crux "DEX volume rises while TVL falls" over the 09-11 raw fixture | hits include DEX and TVL lines with a number or entity; lines already quoted excluded; ≤ 12 hits; ≤ 3 KB |
| crux_search | "The", "September", "2026-10-04", "Q4", a sentence-initial "Volume" | not entities |
| crux_search | red-team crux on a consensus fixture | stored under `redteam` |
| stats | 9 values with two at 50 | majority/minority counts exclude the 50s |
| split_sheet | direction, degree, consensus, split_unpaired, none | right `type`, `count_phrase` text, ≤ 6 KB, no agent names after `anonymise` (all nine names in all casings in the input) |
| split_sheet | debated question, rebuttal clean / rebuttal flagged `persona leakage` | `minority_case.source` `rebuttal` / `steelman` |
| split_sheet | consensus block | validates against `SPLIT_BLOCK`; `minority_case.source == "red_team"`; no `red_team_case` key |
| split_sheet | question with gap_before 30 debated down to 15 on argument | still a block |
| ledger | append the same run twice | one line per `ledger_id`; tagged run id writes under `<run>/state/` |
| legacy adapters | fixture debate | `legacy_moves` items have `from`, `to`, `delta`, `reason`; challenge records have string `text` and `crux` |
| lens_notes | unverified and social claims in input | left out; labels are lens labels |
| leakage | "**ROADMAP:**", "Post now", "## Take" | flagged |
| brief checks | "regulatory challenges", "debate over CLARITY", "Senate voted", "Builder.ai", "Analyst Firm" | no hit |
| brief checks | "the agents debated", "our lenses", "Skeptic" in WHAT IT MEANS | hit |

### 17.2 Schema tests, `tests/test_schemas.py`

- every schema in `ALL` is strict-mode clean: every object's `required` equals its property keys
  and `additionalProperties` is false (walk recursively); no `null` types in `ALL`. The repo has no
  CI, so this file is run on the droplet after every `git pull` of `v2`
  (`python3 -m unittest tests.test_schemas`) and its result goes in the deploy note;
- `schemas.ALL[name]` equals `schemas/debate/<name>.json` for every committed file, and
  `schemas.QUESTION` equals `schemas/debate/question.json`;
- a hand-written golden reply per schema validates; a reply missing one field fails with the path;
- `_dry_json` output for every schema validates, with question ids taken from the prompt, and the
  dry triage reply keeps ≥ 3 questions through `gate_questions` for `--as-of 2026-09-11` and
  `2026-10-04`;
- every `ARTIFACTS` schema validates a fixture artifact produced by `debate.py` from the fixtures,
  including a consensus split sheet and a `pairing.json` with an inactive agent missing from
  `positions_evidence`.

### 17.3 Template tests, `tests/test_prompts.py`

- each template renders with the variables the orchestrator passes; a missing or extra variable
  raises (catches drift in both directions);
- the rendered challenge and response prompts for the fixture pair are under 9 KB;
- `response.md` does not contain "the other view's evidence" in its list of sources for a larger
  move, and its free-move number equals `debate.FREE_MOVE`.

### 17.4 Dry-run end to end

- `--dry-run --package-from tests/fixtures/package/2026-10-04` (wide spread): green;
  `triage.json` keeps ≥ 3 questions; `pairing.json` has ≥ 1 pair; challenges, responses,
  cruxcheck, split, synthesis, checks, run.json present; the dry brief has 11 sections in order,
  with WHERE THE VIEWS SPLIT; run.json `schema_version` 2 validates against the run.json part of
  `ARTIFACTS`.
- same with `RECON_DRY_SPREAD=narrow`: `day_type: consensus`, 1 red-team call, 0 responses, a
  consensus block in the split sheet, "case against" in the brief.
- budget: `RECON_CALL_BUDGET=18` gives fewer pairs and `usage.budget_skips` entries;
  `RECON_CALL_CEILING=12` skips the optional calls and still writes a brief.
- resume: `--from-phase responses` reuses triage, takes, pairing and challenges (calls.jsonl
  unchanged for them); `--from-phase deepdive` is accepted by argparse and maps to `cruxcheck`
  with a log line; `--from-phase split` twice leaves one ledger line per question.
- `--replay` on the dry provider: state written only under `<run>/state/`; `config/` **and
  `data-sources/`** unchanged and `data-sources/` unread (hash both trees before and after; the
  read check patches `raw_sections` and `read()` to fail on any `data-sources/` path).

### 17.4b Live schema smoke (droplet, before the replays)

`--output-schema` has only been exercised live with a 1-field schema (model-log measurement b);
Phase B was only dry-run and Phase A ran bash without schemas. Before the first replay, one FAST
call per schema in `ALL` (6 calls), each with a tiny prompt ("Fill every field with a short
placeholder; question ids q1") and `RECON_CODEX_SLIM=1`, on the droplet's Codex 0.153.4 (it
already lists all 15 slim `--disable` features and the three gpt-5.6 models, so no CLI upgrade is
needed). Pass: exit code 0 and `schemas.parse()` accepts each reply. A rejection is fixed in the
schema before any replay spends calls on it.

### 17.5 Live

1. The spread probe (§15.0), then the three replays, the stability rerun and the triage
   comparison (§15). Pass bar §15.5.
2. Two live validation runs on fresh data with tagged ids (`<date>-c3`, `<date>-c4`, `--no-telegram`;
   state, ledger and scores under the run folder per §2.6), same bar plus: the brief would have
   been delivered on time from a 05:00 KST start.
3. Model-log rows for each: calls, tokens (cached), output, wall, useful debates, splits.

---

## 18. Exit, roster and cutover

- **Exit**: spread probe passed (§15.0), dry runs green, unit tests green, live schema smoke green
  (§17.4b), replays and live runs meet §15.5, model-log updated, `06-improvement-plan.md` §7 row C
  marked done with the measured numbers.
- **Cutover**: after the two live runs pass, `scripts/cron_run.sh` switches from `run_recon.sh`
  to `python3 recon/orchestrator.py` (one line; bash stays on disk as the fallback for a week).
  This is a droplet write made from the session (decision 6).
- **Roster (decision 5, deferred to Phase C)**: after the replays and the first 5 orchestrator
  runs, compute each agent's mean `distinctness`, endpoint share, `data_share` and
  `unique_numbers` from `config/agent_scores/`. An agent goes inactive when it is in the bottom two
  on distinctness **and** on unique numbers **and** has an endpoint share under 10 %. At most 3 go;
  never `skeptic` (red-team fallback); never the only default lens of a domain in `DOMAIN_LENSES`.
  The active list lives in `config/roster.json` (`{"active": [...], "inactive": [...], "decided":
  "<date>", "evidence": {...}}`), read by triage instead of the hard-coded nine. Artifacts already
  allow it: `positions_evidence` is a free-key object holding active agents only, and `eligible`
  lists only active agents. The change and its numbers go in the model log. F15 predicts `analyst`
  and `user_agent`; the data decides.

---

## 19. Decisions made in this spec

| # | Decision | Where |
|---|---|---|
| 1 | Triage stays FAST; one A/B on 09-11 decides whether it moves to ANALYST | §2.1 |
| 2 | 3 / 4 / 5 questions by depth; max pairs 1 / 3 / 3, further capped by the call budget | §2.2, §1.1 |
| 3 | Questions carry kind, domain, metric, baseline quote, integer weight 1–3 (clamped), lineage; programmatic gate | §2.3–2.4 |
| 4 | Question ledger, append-only, gitignored, idempotent, keyed `<run_id>-<qid>`; tagged runs write under the run folder | §2.6 |
| 5 | TAKE keeps its fields but puts `positions` first and caps `take` at 150 words; the take task answers from the lens and needs one lens-data quote per position; lens raw extras after the cached prefix, looked up in raw then package | §3, §13.8 |
| 6 | Evidence class data vs social; social-only positions open debates only on judgment questions | §3, §4.1 |
| 7 | Pairs must straddle the median; one debate per agent unless short; gap ≥ `GAP_MIN` = max(20, 2 × median retest \|Δp\|) | §4.2, §15.0 |
| 8 | Both sides challenge each other; no persona format; names replaced | §5 |
| 9 | Programmatic crux search feeds the responders (gives "new evidence" a real source); also run on the red team | §6 |
| 10 | Free move 5 points; larger moves need a verified, data-class quote that neither side cited and that shares a crux term; the challenger's quotes are argument | §7.2 |
| 11 | Undebated positions do not move | §7.2 |
| 12 | Crux check: one neutral referee call, picked on `gap_before`, adds facts, moves nothing | §8 |
| 13 | Vote calls gone; final positions are the vote | §10 |
| 14 | Split sheet ≤ 6 KB + lens notes ≤ 8 KB replace the full record in the draft | §11 |
| 15 | WHERE THE VIEWS SPLIT lands in Phase C (heading, prompt, persona, checks); the rest of Phase D stays in D | §0, §11.4 |
| 16 | Count phrases rendered by code; the brief may only copy them | §11.2 |
| 17 | Replays cold-start state, empty historical context, synthesis raw from the run folder, run on the droplet | §15.1 |
| 18 | Replays and live runs reported in one private artifact page; not a gate for Eric | §15.5 |
| 19 | Roster rule and `config/roster.json` | §18 |
| 20 | Cron cutover to the orchestrator after the two live runs | §18 |
| 21 | The build is gated on a spread probe before any pairing code | §0.1, §15.0 |
| 22 | `live_split` = gap_after ≥ 20, or gap_before ≥ `GAP_MIN` not closed on crux data; blocks chosen on `gap_before` | §9.1, §11.1 |
| 23 | Day type `split_unpaired`: a split with no eligible pair gets undebated blocks, not the red team | §4.3, §11.1 |
| 24 | The minority case is the minority's own rebuttal; the opponent's steelman is only the fallback | §11.2 |
| 25 | Call budget 24 (planning) and ceiling 32 (hard); crux check dropped first, then the triage re-ask (only with ≤ 1 survivor), then pairs; Phase D's filter removal stays in D, so a normal day has no crux check until then | §1.1, §16 |
| 26 | Probabilities in the new call schemas are integers; `norm_p` rescales fractions and clamps | §7.2, §12 |
| 27 | Pass bar can fail on spread, splits, closure by politeness, soft moves and stability; three packages | §15.5 |
| 28 | Takes stay fully parallel; a lead take is added only if the replays show < 40 % cached on takes | §16 |
| 29 | Package fixtures for 09-11 and 10-04 are committed so tests run without `briefs/` | §17 |

---

## 20. Review changes (2026-10-04)

| # | Severity | Finding | Change | Where |
|---|---|---|---|---|
| 1 | high | Nothing changed why takes converge; no measurement of spread | Spread probe gates the build and sets `GAP_MIN`; TAKE positions-first, `take` ≤ 150 words; take task answers from the lens with a lens-data quote per position; per-lens model/effort tested | §0.1, §3, §12.1, §13.8, §15.0 |
| 2 | high | Gate gamed by conceding through the challenger's quotes; `partial` counted | New evidence must be verified (not partial), data class, cited by neither side, share a crux term; source recorded; prompt no longer lists the other view's evidence | §7.2, §13.4 |
| 3 | high | Free move 10 × 2 erased the smallest split | `FREE_MOVE = 5`; blocks on `gap_before`; new `live_split`; crux check on `gap_before`; `closure_without_evidence`, `effect` | §7.2, §8, §9.1, §11.1 |
| 4 | high | Pass bar could not fail | Bar items (a)–(f), third package 10-04, stability rerun, minority Brier | §15.2–15.5 |
| 5 | medium | Data-only eligibility locked out social lenses; split days labelled consensus | Any verified quote; social-only on judgment questions; `split_unpaired` | §4.1, §4.3 |
| 6 | medium | Minority case written by the majority | Minority rebuttal by default; steelman fallback | §11.2 |
| 7 | low | Consensus block failed the schema; nine-key `positions_evidence`; unbounded numbers | Consensus block in `minority_case`/`base_case`; `free()`; integer weights and probabilities, clamped and flagged | §11.2, §12 |
| 8 | low | Noisy metrics: `useful`, crux entities, process words | `useful` needs yes or a leaning partly; entity rule excludes sentence-initial words and dates, hits need a number or entity; narrowed process words | §6, §9.1, §11.5 |
| 9 | low | Re-asks unbounded; take prose unread | Ceiling 32; `take` capped | §1.1, §3, §16 |
| 10 | high | `LENS_RAW` headings missed (0 bytes for macro and skeptic) | Raw then package lookup, view subtracted; corrected table shared with phase-e §4.5b; test > 0 bytes; `--skip-collect` writes the raw file; raw loop gains the Phase E sources (phase-e §4.1a, revised in that spec) | §3, §15.5 |
| 11 | high | Replay synthesis read today's `data-sources/` | `raw_sections()` reads the run folder; hash check | §15.1, §17.4 |
| 12 | high | Dry run could not produce a gate-passing question; dry brief had the old heading | `_dry_json` question special case; `BRIEF_SECTIONS` in `schemas.py` | §12.2, §17.4 |
| 13 | medium | Normal day over the call budget | `RECON_CALL_BUDGET`, skip order, `usage.budget_skips` | §1.1, §16 |
| 14 | medium | Phase B readers expect old shapes | `legacy_moves`, record shapes, `build_record`, red-team edge | §14.1 |
| 15 | medium | Fractions in responses and red team | `norm_p`; weight clamp | §7.2, §2.3 |
| 16 | medium | Ledger ids collide; re-runs duplicate lines | `<run_id>-<qid>`, idempotent appends, tagged runs under the run folder | §2.6, §9.2 |
| 17 | medium | Strict schemas never run live | Live schema smoke before the replays; walker run after each pull | §17.2, §17.4b |
| 18 | low | No crux search on consensus days | Red-team crux search under `redteam` | §6, §8 |
| 19 | low | View-only quotes unverified; partial matches had no line | `locate` over package, raw, view, social; partial anchor line; view in gate rule 4 | §2.4, §3, §5.2 |
| 20 | low | argparse rejected `deepdive` | `choices=PHASES + list(PHASE_ALIASES)` | §1 |
| 21 | low | Fixtures only on the droplet | Committed package fixtures | §17 |
| 22 | low | Name and process-word checks hit real news | Name check limited to two sections with lookaheads; narrowed process regex | §11.5 |
| 23 | low | Cache claim overstated | Cache restated per model; lead take only if measured | §16 |

Path: `docs/v2/phase-c-spec.md`.
