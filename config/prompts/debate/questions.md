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
   - is genuinely contestable: informed lenses could land 25 or more points apart FOR DIFFERENT REASONS
     (macro, flows, regulation, adoption, product or risk pulling different ways). Drop any question
     you expect every lens to answer below 15% or above 85%, and any question that a market price or a
     prediction-market probability quoted in the package already answers (every lens would copy that
     number). At most one question is a pure price-level threshold;
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
