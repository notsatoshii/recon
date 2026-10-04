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
   argument alone, and the other view's argument and its quotes count as argument. Each new fact allows
   10 points more, up to 25 points in total: a new fact is one verbatim line that neither side has cited,
   from the crux data above or the excerpts, carrying a number about what the disagreement turns on.
   A program enforces this: a move larger than your new facts allow is cut back and recorded.
   Social-media quotes do not justify a larger move. Hold when the challenge brings no new fact; there
   is no credit for agreeing.
4. reason: at most 60 words — what changed and why, or why nothing did.
5. new_evidence: 0-3 quotes that neither side cited before, verbatim, with their section.
Reply with one JSON object matching the schema.
