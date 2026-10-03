# RECON v2 — Improvement plan (including the debate)

Date: 2026-10-04. Planning only. Nothing here has been built, committed, or run.

Evidence comes from the only two real v2 runs (2026-09-10 and 2026-09-11): the run folders and
logs on the droplet (`/home/recon/recon-v2`, read-only), the run exports in
`~/innovlabs/recon-exports/runs/<date>.json`, and the code in the local clone (branch `v2`,
4 commits behind the droplet; the droplet's extra commits touch the export, the Twitter
collector and `collect_data.sh`, not `run_recon.sh`). Line numbers for `run_recon.sh` are from
the local clone and match the droplet.

Relation to the existing plan: `01`–`05` stay the frame (Python orchestrator, schemas, desks).
This file says what the first two real runs taught us, redesigns the debate, and reorders the
work so the cheapest, highest-value fixes land first.

---

## 1. State of play

| Item | Value | Source |
|---|---|---|
| Last brief delivered | 2026-09-11. Nothing since (23 days). | `briefs/` on droplet |
| Scheduled runs | Not running. Root crontab still points at the v1 checkout `/home/recon/recon` and starts each line with `source`, which `/bin/sh` does not have. Same for the 15-min alerts. | `crontab -l` |
| LLM calls per run | 61 (40 ANALYST, 19 FAST, 2 SYNTH) | `runs/2026-09-1x.json` `usage` |
| Input tokens per run | 1.17 M (09-10), 1.36 M (09-11); about half is the cached fixed Codex prefix | same |
| Output tokens per run | 30 K / 37 K | same |
| Fixed prefix per call | ~9–11 K tokens (`cached_tok=8960` FAST, `10624` SYNTH; the smallest call is 13.9 K tokens for a ~10 KB prompt) | `logs/llm_calls.log` |
| LLM wall time | 11–12 min | `logs/2026-09-1x.log` |
| Collection time | 10 min on 09-11 (Reddit 8 min of it). The Twitter budget was raised to 40 min afterwards (commit `eb4ebfa`), so the next collection will take ~50 min. | log `[21:07:19]`→`[21:17:34]` |
| Calls that produced no brief | ~120 of the 244 Codex calls logged so far (a 09-10 test run on June data, and a 09-11 run that crashed at synthesis and was redone from scratch) | `logs/llm_calls.log`, `logs/2026-09-11.log` `[21:24:40]`→`[21:26:28]` |
| Data package | 61 KB (09-10) → 170 KB (09-11). Agents receive only the first 90 KB. | `00_data_package.md`, `run_recon.sh:409` |
| Brief | 903 words, 6 sections (09-10) → 2,025 words, 11 sections (09-11) | exports `synthesis.final` |

Short version: the pipeline works end to end, but it has not run for three weeks, half of what
it collects never reaches the agents, the debate mostly produces polite convergence, and about
two thirds of the calls could go without losing anything that reaches the brief.

---

## 2. Findings

Each finding names the evidence. "Export" means `~/innovlabs/recon-exports/runs/<date>.json`.
"Run folder" means `/home/recon/recon-v2/briefs/<date>/` on the droplet.

### 2.1 Data reaching the agents

**F1. Agents never see AI tools, fundraising, or Twitter.** Takes get `head -c 90000` of the
package (`run_recon.sh:409`). On 09-11 the package is 169,518 bytes. Section offsets
(`00_data_package.md`): news starts at 25,996, Reddit at 73,763, Twitter at 88,910, AI & tools
at 146,427, fundraising at 151,643. So agents see about 1 KB of Twitter and none of AI or
fundraising. The AI agent says so itself: "No GitHub Trending AI/tools data was supplied"
(export, `ai_engineer.take`). On 09-10 the package was 61 KB and fit; the 11-section widening
and the new Twitter collector pushed it over.

**F2. The hallucination filter sees even less.** It gets `head -c 30000` of the package
(`run_recon.sh:919`). That window ends 4 KB into the news section. It cannot check most news
numbers, any Reddit or Twitter text, or the scorecard.

**F3. Historical context is wrong.** `00_historical_context.md` (245 bytes) shows
`btc_price: 63,228` and `fear_greed_index: 14` "over 1 days" — June values — while BTC was
$76,898. "Recent Briefs" is empty. Every agent reads this block.

**F4. The scorecard is mostly unscorable.** `00_scorecard.md` is 23.8 KB with 77 predictions,
most from April–June, written as compound sentences with no probability or measurable outcome
(e.g. "April 17 Iran timeline … triggers coordinated regulatory deployment …"). Agents get the
first 2,000 bytes (`run_recon.sh:384`).

**F5. Memory is uneven and duplicated.** `macro_strategist` and `policy_analyst` have no memory
file, so their memory phase is skipped (export `fed.memory_lines: 0`). `regulator.md` is an
orphan (17 lines, no agent). The memory update appends a full rewritten memory each run
(`run_recon.sh:695-699`), so `trader.md` already holds 4 copies of the same sections. The
analyst persona says it keeps a model in `config/analyst_model.md`; nothing loads it.

### 2.2 The debate (evidence from both runs)

**F6. Takes are near-identical in content.** On 09-11, 8 of 9 takes cite BTC at $76,898, 8 of 9
cite Fear & Greed 69→56, 6 of 9 cite the DEX-volume +7.6% vs TVL −0.7% split, 6 of 9 cite the
tokenized HIMS price. On 09-10, 7 of 9 cite the +30% DEX volume. Same model, same package
prefix, same "most significant thing today" prompt: they converge before the debate starts.

**F7. Pairs are fixed, not chosen by disagreement.** `TENSIONS` is a hard-coded list
(`run_recon.sh:20`): the same 10 directed pairs every day. Builder challenges two agents and is
challenged by two every day; skeptic always meets analyst. Only the wildcard varies, and its
exclusion list misses the ai_engineer–builder pair (`run_recon.sh:500`).

**F8. Challenges are mostly "right direction, overstated".** On 09-11, 6 of 11 challenges open
by agreeing ("correctly", "is right", "sound", "aligned"). Example: "Builder is right to reject
tokenized-stock hype, but overstates …" (export, `ai_engineer.challenges_made`). The prompt
"What did they get wrong?" (`run_recon.sh:464`) forces a challenge even when there is no real
disagreement, so they argue about degree.

**F9. The persona's output format leaks into the debate.** Narrator's challenge to trader ends
with three "content angles" and "Post now". Builder's challenges end with "**ROADMAP:**".
Policy analyst's challenge reuses its four take headings. None of that reaches the brief.

**F10. Responses concede by politeness.** "I am updating my position" appears in 7 of 9
responses on 09-11 and 6 of 9 on 09-10. On 09-11 builder and ai_engineer swapped positions:
builder adopted ai_engineer's "one partner, reconciliation and anomaly detection" product,
while ai_engineer wrote "Build Builder's narrower MVP" (export, both `response` fields). The
prompt "DEFEND or CONCEDE" (`run_recon.sh:535`) plus a model that likes to agree gives this.
Responders also never see the challenger's take or the data, only the challenge text
(`run_recon.sh:534-539`).

**F11. Deep dives end in agreement and their topic is mangled.** The point is extracted with a
greedy `sed 's/.*on //'` (`run_recon.sh:585`). All three real deep dives lost the start of their
topic: "on: toward credible rails or merely …" (09-10 `[16:00:22]`), "on: infrastructure from
day one" (09-11 first run `[21:21:32]`), "on: and shallow, unverifiable liquidity create …"
(`[21:30:30]`). Both delivered deep dives ended with the two sides at nearly the same position
(09-10 narrator and trader: "speculative, concentrated turnover, not broad adoption"; 09-11
analyst "elevated but not yet a choke point" vs skeptic "material near-term risk during
correlated stress"). Each side only sees its own take and response (`run_recon.sh:596-605`).

**F12. Votes ignore the debate and change nothing.** The vote prompt gets only the takes
(`run_recon.sh:619-635`), not challenges, responses or deep dives. Nothing counts the votes;
the synthesizer gets them as prose. On 09-11 all 9 "act on" answers are defensive. Two votes a
day are lost to parsing because the persona's section format overrides the vote format
(export: `policy_analyst` and `macro_strategist` votes empty on 09-11; `macro_strategist` and
`ai_engineer` on 09-10; the raw `06_vote_*.md` files exist).

**F13. The debate amplified an unverified claim.** The tokenized HIMS price ($132 vs ~$29) is
"per social media". It is in 6 of 9 takes and 7 of 9 votes. The synthesizer dropped it as
unverifiable (draft MARKET MOOD says so); it appears 0 times in the final brief. Roughly 30
calls argued about a claim nobody checked.

**F14. Disagreement barely reaches the brief.** The synthesizer persona forbids any mention of
debate, concession or disagreement (`personas/synthesizer.md`). The 09-11 CONTRARIAN CASE is a
bull case no agent held; parts of it come from one point in narrator's challenge (TVL falls
mechanically with prices). Lever-relevant debate points vanish: "Polymarket" is in 6 of 9 takes
and 0 times in the final brief, including skeptic's and analyst's point that the Polymarket Fed
market had $1.63 M volume against $238 K liquidity.

**F15. Which agents add something distinct.** Traceable into the brief: trader (numbers),
macro_strategist (Hormuz → oil → Fed chain), policy_analyst (CLARITY Act, ESMA), skeptic
(risks). Little distinct: analyst (overlaps trader and skeptic; its model file is never
loaded); user_agent and macro_strategist (converge every day; their specific items such as the
25.9% and 18.7% pool yields never reach the brief); ai_engineer (starved of AI data by F1,
then adopts builder's view; the brief's AI content comes from raw data, not from it). The
"BUILD NOW / ROADMAP" output of builder and ai_engineer and narrator's content angles have no
section in the brief, so they are dropped every day.

**F16. The environment classification is decorative.** It runs after the debate
(`run_recon.sh:768-782`); every agent is active regardless; the weights go to the synthesizer
as one line of text.

### 2.3 Synthesis and the brief

**F17. The section drop is fixed.** On 09-10 the final had 6 sections: MARKET MOOD and THE
CONTRARIAN CASE were removed and a new heading "WHERE THEY DISAGREE" appeared. On 09-11, after
the widening (`run_recon.sh:911`, "Never drop a section"), all 11 sections survived. One run is
the only evidence; the check should be programmatic.

**F18. The filter is a second full rewrite that also damages things.** It is a SYNTH call
(47 K tokens in, 142 s). On 09-11 it marked every SCORECARD line "[unverified prior forecast]"
because it is not given the scorecard; it turned the AI NEWSLETTER bullets (418 words) into
prose (290 words) although the prompt asks for bullets; and it added content of its own
(MARKET MOOD quotes, an ECB date). The added items do trace to the package (BettaFish section,
byte 6,417; calendar line 240), but a "filter" should not be writing new content.
`synthesis.claims` is empty in both exports: there is no claims check yet.

**F19. MARKET MOOD only works by accident.** The draft's input has no social section
(`run_recon.sh:845-852` builds AI, education, Korea and fundraising raws only). The 09-11 and
09-10 drafts both say no attributable posts were available. The final's quotes came from the
BettaFish summary inside the filter's 30 KB window. One quote is attributed to r/Base, which is
not in the collector's subreddit list (worth a check). Another is the title of a weekly
discussion thread, not a mood quote.

**F20. Instructions conflict.** `personas/synthesizer.md` asks for 600–1,000 words and 7
sections and still says "Opus 4.6"; the prompt asks for 1,400–2,000 words and 11 sections;
`01-plan.md` says 600–900 words.

**F21. The newsletter half bypasses the debate.** AI NEWSLETTER, FUNDRAISING, KOREA and
AI EDUCATION are 753 of 2,025 words on 09-11. They are written from raw data handed straight to
the synthesizer. That is fine, but it means the other 59 calls shape well under half the brief.

**F22. Facts repeat across sections.** The DEX volume vs TVL split appears in WHAT HAPPENED,
WHAT IT MEANS, THE CONTRARIAN CASE and RISKS on 09-11.

### 2.4 Sources

**F23. Reddit: 9 of 40 subreddits on 09-11** (31 × HTTP 429; 8 min 18 s). 09-10: 8 of 40. The
collector spaces requests 1.5 s and then waits 15 s per 429 (`collect_data.sh`, Reddit block).
`economics` is listed twice.

**F24. X/Twitter: lots of cost, almost no effect.** 09-11 run: 26 of 112 handles, then every
lookup locked after 33 s (`[21:16:11]`). The background discovery job then used the same burner
account and failed on every handle with `strconv.ParseInt: parsing "<handle>"`
(`discover_twitter.py:83` passes a screen name where twscrape wants a numeric id), burning the
account's quota during the run. Discovery is now opt-in (`4ad897e`). A later standalone pull with
the new 40-min budget fetched 89 handles, 181 KB (`data-sources/twitter/latest.md`, 22:03). With
the 90 KB cut, agents would still see none of it. Social reaches the brief only through
BettaFish, whose LLM input is capped at 26 KB (`logs/llm_calls.log`, `agent=bettafish
input=26091b`).

**F25. Fundraising headlines lack the facts the section asks for.** The brief says "round label
not supplied" or "lead investor not disclosed" for 4 of 5 rounds on 09-11. Google News
headlines rarely carry them. RootData was dropped (captcha).

**F26. Sources that do their job.** On-chain (DeFiLlama, CoinGecko, Polymarket gamma, Fear &
Greed), news RSS, World Monitor (Hormuz, the economic calendar), Korean RSS (KOREA section),
HN + GitHub trending (via the synthesizer), BettaFish (the only route for social).

### 2.5 Reliability and cost

**F27. A crash means a full rerun.** The first 09-11 run stopped after the environment call at
the start of synthesis (`[21:24:40]`), after ~60 calls; the raw-section step there was fixed in
`1ae7198`, and the whole run was redone with `--skip-collect`. There is no resume.

**F28. Where the tokens go (09-11).** Takes 470 K (9 calls); challenges 169 K (11); responses
140 K (9); votes 166 K (9); memory and state 236 K (16); deep dive 31 K (2) plus a 22 K decision
call; wildcard pick 15 K; environment 15 K; draft 55 K; filter 47 K. Memory/state, votes and the
filter (26 calls, ~450 K tokens) produce nothing the reader sees directly.

**F29. Two plan assumptions are unverified.** `02-review.md` expects `codex exec resume` to cut
input by two thirds. Resuming re-sends the thread; it may only make the tokens cached, not
fewer. And `llm.py` already supports `--output-schema`, but no call site uses it.

**F30. Telegram noise.** Three status messages per run ("starting", "all 9 agents active",
"Debate complete") (`run_recon.sh:148, 327, 761`).

---

## 3. Debate redesign

### 3.1 What the debate is for

Find the two to four points where informed lenses really disagree today, test each against the
data, and tell Eric the split and what would settle it. Today the debate does the opposite: it
pairs agents by a fixed list, invites them to disagree about degree, rewards conceding, and then
hides the result.

### 3.2 The new flow

```
Q   Questions of the day   1 FAST call, schema          → 3–5 resolvable questions
T   Takes                  6–9 ANALYST calls, schema    → summary + a probability per question + cited evidence
P   Pairing                programmatic                 → up to 3 pairs with the widest real spread
C   Challenge              2 calls per pair, schema     → steelman, crux, rebuttal, evidence, what would change my mind
R   Response               2 calls per pair, schema     → hold / narrow / concede, new probability, new evidence
X   Crux check             programmatic + 0–1 call      → what the data says about the biggest open crux
S   Scoring                programmatic                 → spread before/after, who moved, evidence verified, flags
V   Final positions        programmatic                 → the "vote": median, range, minority count per question
B   Split sheet → brief    goes into the one SYNTH draft call
```

### 3.3 Stage by stage

**Q — Questions of the day.** The triage call (moved before the takes) reads the compact
package, yesterday's open questions and any predictions due today. It writes 3–5 questions.
Each is resolvable where possible, with a date and the data that would settle it. Examples from
09-11: "Will total DeFi TVL on 2026-09-18 be above today's $86.61 B?", "Will the CLARITY cloture
vote pass on 2026-09-15?", "Is this week's DEX volume rise mostly churn (TVL down again next
week)?". Each question is tagged with the lenses that should answer it. The same call sets the
day's depth (quiet / normal / risk) and the active agents, replacing the decorative Phase 7
classification (F16).

**T — Takes.** Same personas, new output contract (schema, validated):

- `summary`: up to 150 words in the persona's voice. This is the only free prose.
- `positions`: for each tagged question, a probability 0–100, one-line reason, and 1–3
  evidence items `{section, quote}`.
- `claims`: up to 4 claims with evidence and confidence.
- `prediction`: one, with probability and `resolves_on`.
- `novel`: one item the agent thinks the others will miss.

Every evidence quote is checked by string match against the package. Unverified quotes are
flagged in the run record and cannot count as evidence later (this would have caught F13). Each
agent gets the shared compact package plus its own lens's raw sections, so they do not all
start from the same first 90 KB (F1, F6). Overlap of cited numbers between agents is measured
and recorded as a diversity score.

**P — Pairing by actual disagreement (no LLM).** For each question, take the agents' stated
probabilities. Candidate pair = two agents on the same question. Score = gap in probability ×
question weight, minus penalties for: the same pair yesterday, an agent already in two debates,
both agents citing the same evidence only. Pick the top 3 pairs with a gap of at least 20
points (1 pair on quiet days). Pairs prefer the agents nearest each end of the spread.

If no pair clears 20 points, it is a consensus day. Then run one red-team call instead: the
agent furthest from the median (or skeptic, if all are within 10 points) argues the strongest
case against the consensus on the top question. That becomes the brief's minority case. No
debate is staged when there is nothing to debate.

**C — Challenge (steelman, then rebut).** Both sides of a pair write one challenge in parallel.
Input: the question, both positions with reasons and evidence, and the package excerpts their
evidence points to (up to ~4 KB). Not the whole package. Output (schema, ≤ 180 words):

- `steelman`: the opponent's best case in two sentences, in terms they would accept.
- `crux`: the single factual or causal claim the disagreement turns on.
- `rebuttal`: up to 120 words.
- `evidence`: quotes from the package.
- `would_change_my_mind`: an observable and a date.

The prompt states that the debate format replaces the persona's output format: no content
angles, no ROADMAP tag, no portfolio line (F9).

**R — Response.** Each side answers the challenge it received. Input: that challenge, its own
position, the cited excerpts. Output (schema): `steelman_fair` (yes / no, with a correction),
`verdict` (hold / narrow / concede), `new_probability`, `reason`, `new_evidence`.

The rule against politeness: moving the probability by more than 10 points requires at least
one evidence quote the agent did not already cite. A move without new evidence is kept but
flagged as "update without evidence" in the record and in the agent's scores. The words
"DEFEND or CONCEDE" go away (F10).

**X — Crux check (replaces the deep dive).** First, programmatic: for the open crux with the
largest remaining gap, search all raw sections on disk (not just the agent window) for the
named metric or entity. If something relevant is found, one ANALYST "referee" call reads the
crux and the hits and writes `{resolved: yes|no|partly, what_the_data_says (quote),
remaining_uncertainty, settles_on (observable, date)}`. It adds facts, not another round of
opinions. Typed output, so no `sed` (F11). Zero calls when no crux has a data hit.

**S — Scoring (no LLM).** Per debate: gap before and after, who moved and by how much,
evidence quotes verified vs claimed, steelman accepted or not, flags (update without evidence,
persona leakage, unverified evidence). Per agent over time: evidence verification rate, how
often it moves and whether moves had new evidence, and Brier score on questions and predictions
once they resolve. Stored in `run.json` and in the agent's state (programmatically). After about
three weeks of resolved questions, Eric can decide whether better-calibrated agents should weigh
more in the consensus.

**V — Final positions replace the vote.** No separate vote calls. Each agent's final
probability per question is its take value, updated by its response if it debated. Per
question: median, range, how many agents sit on the other side of 50 from the median. This is
what "the vote" means from now on, and it feeds the brief directly (F12).

### 3.4 How dissent reaches the brief

The synthesizer stops receiving the 108 KB raw debate record. It receives a split sheet
(≤ 6 KB), built programmatically:

```
question · median p · range · minority count
base case (one paragraph, from the majority's reasons and evidence)
minority case (the best steelman/rebuttal from the debate)
crux · crux check result · settles on <observable> by <date>
```

Brief changes:

- THE CONTRARIAN CASE becomes **WHERE THE VIEWS SPLIT**: up to three blocks, one per live
  question. Each says the base case, the minority view, what it turns on, and when we will know.
  A gap under 20 points is not presented as a split. On a consensus day the block is the
  red-team case, labelled as such.
- WHAT TO WATCH takes the `settles_on` items with dates.
- SCORECARD is computed, not written: questions and predictions that resolved, with the outcome
  and the probability we gave.
- Whether the brief may say "most of our lenses (7 of 9)" or must stay anonymous is Eric's call
  (§8). Agent names stay out either way.

### 3.5 Cost per stage

Estimates for a full day, using the measured ~11 K fixed prefix per call. "Today" is 09-11.

| Stage | Today: calls / input tokens | Proposed: calls / input tokens |
|---|---|---|
| Questions + triage (before takes) | 1 FAST after the debate / 15 K | 1 FAST / ~20 K |
| Takes | 9 ANALYST / 470 K | 6–9 ANALYST / ~250 K (every section visible, ~60 KB package cap) |
| Pairing | wildcard pick 1 FAST / 15 K | 0 |
| Challenges | 11 ANALYST / 169 K | 6 ANALYST / ~95 K (3 pairs × 2) |
| Responses | 9 ANALYST / 140 K | 6 ANALYST / ~95 K |
| Deep dive / crux check | 1 FAST + 2 ANALYST / 53 K | 0–1 ANALYST / ~20 K |
| Votes | 9 ANALYST / 166 K | 0 (final positions) |
| Memory + state | 16 FAST / 236 K | 0 (written from the typed outputs) |
| Draft | 1 SYNTH / 55 K | 1 SYNTH / ~40 K (split sheet instead of raw record) |
| Filter | 1 SYNTH / 47 K | 0–1 SYNTH / ~15 K (only flagged claims) |
| **Total** | **61 calls / 1.36 M** | **~15–24 calls / ~0.5–0.55 M** |

Quiet day (1 pair or a red-team call, 6 agents): about 12 calls and ~0.35 M tokens. Wall time
should fall from 12 min to about 5. Validate against the usage logs after the first live runs;
the numbers above are estimates, not measurements.

### 3.6 Testing the redesign without guessing

Replay both old packages (`briefs/2026-09-10`, `briefs/2026-09-11`) through the new flow with
`--skip-collect` and compare with the delivered briefs: number of real splits found, evidence
verification rate, concession rate, calls, tokens. Two replays cost about the same as one old
run.

---

## 4. Sources and collection

1. **One package budget, every section visible.** Per-section byte caps in one place, total
   ≤ ~60 KB for agents, ordered by value; full raw sections stay on disk for the crux check and
   the claims check. Fixes F1. A check fails the run if any section is cut out entirely.
2. **Give the synthesizer the social section** (ranked: posts with URLs and engagement) for
   MARKET MOOD, and the scorecard data for SCORECARD (F19, F18).
3. **X/Twitter** (pending Eric, §8). If kept: a 10-minute budget, not 40; rotate category
   subsets by weekday so each handle is read about twice a week inside the ~27 lookups per
   15 minutes; keep the top tweets by engagement up to ~12 KB; keep the user-id cache. Fix or
   delete `discover_twitter.py` (handle → id via `user_by_login`, which itself costs lookups) and
   never run discovery in the collection window.
4. **Reddit.** Cut to ~20 subreddits that serve the desks; space requests ~6–7 s and honour
   `Retry-After`; cache each sub for 6 h; run Reddit in parallel with the other collectors
   instead of first and alone. Optional, Eric's call: a free Reddit API app (100 requests/min)
   ends the 429s. Remove the duplicate `economics`.
5. **Run collectors in parallel.** Today they run one after another, so Reddit and X waits
   block everything. Target: collection under 5 min (excluding any X budget).
6. **Fix or replace historical context.** Today it injects June metrics (F3). Replace it with
   yesterday's split sheet, open questions, and real deltas from the knowledge DB once the
   metric extraction is checked.
7. **Retire the old scorecard.** Archive the 77 legacy predictions. From now on only structured
   predictions and questions (probability, metric, date) are scored (F4).
8. **Fundraising.** Check whether DeFiLlama `/raises` is free; if so use it for round type and
   lead; keep Google News for Korea and AI rounds; dedupe by company.
9. **BettaFish.** Keep. Feed it a ranked sample of social posts instead of the first 26 KB, and
   have it carry post URLs through.
10. **Lever and InnovLabs sources** (Polymarket movers and liquidity, Kalshi, vendor changelogs,
    arXiv, Korean RSS) stay in the desk phase as planned in `01-plan.md` §4.

---

## 5. Pipeline and reliability

1. **Cron.** Replace the four v1 lines with v2 lines: `SHELL=/bin/bash`, a launcher that loads
   the env file itself, `flock` so runs cannot overlap, log to `logs/cron.log`, one Telegram
   message on failure. Schedule so the brief lands by 06:00 KST (collection start depends on
   the X budget). Watch the first unattended run land. Needs Eric's allow rule for droplet
   writes.
2. **Resume.** Typed artifacts per phase and `--from-phase`, so a crash at synthesis reuses the
   debate (F27).
3. **Schemas everywhere a program reads the output**: questions, takes, challenge, response,
   crux check, claims fix. `llm.py` already accepts a schema path (F29).
4. **No free-text parsing.** Remove the wildcard regex, the deep-dive `sed`, the environment
   string.
5. **Memory and state written by code** from the typed outputs: positions, predictions, what
   changed and why. Removes 16 calls a day. Create state for every active agent; drop
   `regulator.md`; stop appending full copies (F5). Load `analyst_model.md` or drop the claim
   from the persona.
6. **Checks with no LLM cost**, run before delivery: every package section visible; evidence
   quotes verified; numbers in the brief found in the package or the debate evidence; all
   required sections present and in order; word budget; every URL in the brief present in the
   raw data; any number used in more than two sections flagged (F22).
7. **Run record from typed data.** `export.py` (droplet) currently scrapes text: votes come out
   empty for two agents a day, `cites` is always empty, predictions have no probability. With
   typed outputs, `run.json` gains `questions`, per-agent `positions`, `debates`, and the split
   sheet, and the RUBRIC recon page's chain view gets recorded citations instead of inferred
   ones. Keep the old fields filled where they still make sense so the page keeps working.
8. **Measure two assumptions before building on them.** (a) Does `codex exec resume` reduce
   input tokens or only cache them? (b) Can Codex 0.153.4 run with slimmer built-in
   instructions (a config option to verify)? If (b) works, ~10 K tokens drop from every call.
   Two or three tiny calls answer both.
9. **Parallelism.** Raise from 3 to 5 concurrent calls, drop the fixed `sleep 3`, back off only
   on rate-limit errors.
10. **Telegram.** Drop the three status messages; keep one failure alert (F30).
11. **Dry run stays green.** The dry-run provider runs the full new flow on a stored package in
    seconds. Every phase below ends with it passing.

---

## 6. Brief quality

1. **One set of instructions.** Align the synthesizer persona, the prompt and `01-plan.md` on
   length and sections (F20) once Eric picks the shape (§8).
2. **Retire the full-rewrite filter.** Programmatic claims check, then a SYNTH call only for
   flagged claims, with their source excerpts. Format checks are programmatic. The scorecard is
   never touched by an LLM (F18).
3. **WHERE THE VIEWS SPLIT** replaces THE CONTRARIAN CASE (§3.4).
4. **MARKET MOOD from real posts with URLs** (F19). Skip thread titles and posts under an
   engagement floor. Say plainly when social data was thin.
5. **Each fact once.** The flagged-repeat check plus a rule in the prompt (F22).
6. **Lever and InnovLabs lines.** Until the desks land, add one short "For Lever" and one "For
   InnovLabs" line, built from the questions tagged to them. Polymarket structure points stop
   disappearing (F14).
7. **Unverified social claims.** Allowed in MARKET MOOD with attribution; not allowed as the
   basis of a WHAT IT MEANS point (F13).

---

## 7. Build order

Effort is Claude working time. Quota is model usage for validation runs only; building and dry
runs cost nothing. "Old run" ≈ 61 calls / ~1.3 M input tokens.

| Phase | What | Effort | Quota to validate | Calls per day after |
|---|---|---|---|---|
| **A. Run again, stop the leaks** (bash, current pipeline) | v2 cron with bash + flock + failure alert; package section caps so every section is visible; social and scorecard into the synthesizer; filter gets the scorecard and keeps bullets; deep-dive and vote parsing fixes; memory files for all agents, no duplicate appends; synthesizer persona aligned; Telegram noise off; X budget cut to 10 min; Reddit spacing and list trim | 1 day | 1 live run (~61 calls) | ~61 |
| **B. Orchestrator core** (= `05-build-plan` Phase 2, re-scoped) | Python phases with resume; typed takes with questions of the day; triage first; programmatic memory/state; evidence verification; run.json from typed data; the two Codex measurements (§5.8) | 2–3 days | 2 live runs (~35 calls each) + ~3 tiny calls | ~35 |
| **C. Debate redesign** | pairing by gap; steelman/rebut challenge; evidence-gated response; crux check; scoring; final positions; red-team path; split sheet; replay of both September packages | 2 days | 2 replays + 2 live runs (~20 calls each) | ~15–24 |
| **D. Brief** | WHERE THE VIEWS SPLIT; programmatic claims check and scorecard; filter removed; checks before delivery; Lever/InnovLabs lines | 1–1.5 days | 2 live runs | same, SYNTH 1–2 |
| **E. Desks and sources** (= `05-build-plan` Phase 3) | Lever and InnovLabs desks, the three new personas (Eric reads), Polymarket/Kalshi/Korean/changelog collectors, Reddit and X per Eric's decision, parallel collectors | 3–4 days | 3 live runs | ~18–26 |
| **F. Calibration and dashboard** | Brier per agent and per question; RUBRIC recon page gains questions, split and debate scores | 1–2 days (RUBRIC repo) | none extra | — |

Order notes:
- A comes first because nothing has run for 23 days and every day of runs is calibration data
  lost. It is small, reversible, and does not block B.
- B and C can share one stop for Eric: he reviews a replayed September brief next to the
  original.
- Each phase ends with the dry run green and a short entry in `docs/v2/model-log.md` with calls,
  tokens and wall time.

---

## 8. Decisions only Eric can make

1. **Can the brief show the split?** Today it may not mention disagreement at all. Proposal:
   allow "most of our lenses …, a minority argues …" with counts, no agent names.
2. **Brief shape and length.** Keep one ~2,000-word daily, or go back to a ~900-word daily and
   move AI NEWSLETTER, KOREA, AI EDUCATION and FUNDRAISING into the Mon/Wed/Fri digest that
   `01-plan.md` describes.
3. **X/Twitter.** Keep the burner account with a 10-minute budget and rotation, or drop X and
   rely on Reddit and BettaFish. Today X costs 10–40 minutes and the lock risk, and feeds agents
   about 1 KB.
4. **Reddit API app.** Create a free Reddit app on your account (ends the 429s), or stay on RSS
   with fewer subreddits.
5. **Roster before the desks land.** Drop analyst and user_agent now (6 agents, cheaper, little
   lost per F15), or keep all 9 until Phase E.
6. **Droplet writes and schedule.** An allow rule so the cron and deploy changes can be made
   from a session, and confirm 06:00 KST every day including weekends.

Path: `docs/v2/06-improvement-plan.md` (local clone only, not committed).
