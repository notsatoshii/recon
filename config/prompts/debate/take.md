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
Odds quoted in the package (prediction markets, futures pricing) are evidence, not your answer: start from
what your lens sees and give your own number; when it differs from the market or the obvious base rate,
the reason says why.
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
