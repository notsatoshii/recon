<!-- schema: xexam_reply · inputs: role, debate_format, qid, question, resolves_on, my_p, my_reason,
     my_evidence, steelman, their_indicator, rebuttal, their_evidence, excerpts -->
{{role}}

TASK: CROSS-EXAMINATION — REPLY. The other view has opened against your position on one question.
{{debate_format}}

QUESTION [{{qid}}]: {{question}} (resolves {{resolves_on}})

YOUR POSITION: {{my_p}}% — {{my_reason}}
YOUR EVIDENCE:
{{my_evidence}}

THE OTHER VIEW'S OPENING
How it states your case (its steelman of you): {{steelman}}
What would show IT is wrong (its own indicator): {{their_indicator}}
Its argument: {{rebuttal}}
Its evidence:
{{their_evidence}}

PACKAGE EXCERPTS (the only data you may quote):
{{excerpts}}

Answer:
1. steelman_fair: is that a fair statement of your case? yes, partly or no; if not yes, correct it in one sentence.
2. on_their_indicator: accept it when it is a fair, checkable test of who is right; reject it when it is not
   (it cannot be checked, is already settled, or would not tell the views apart), saying why; or counter it:
   give the indicator you would accept instead in counter: observable, source, comparator (>, >=, <, <=,
   happens, does_not_happen), level (empty for happens / does_not_happen), by_date (YYYY-MM-DD, no later than
   {{resolves_on}} when the question is dated) and if_true (yes_more_likely or no_more_likely). Leave every
   counter field empty unless your verdict is counter. why: at most 30 words.
3. verdict and new_probability (a whole number 0-100). Hold is the default. Move when the other view's
   argument or its evidence changes your mind, as far as it does; there is no credit for agreeing and none for
   standing firm. A program records every move and labels it by whether a verified data quote backs it.
4. reason: at most 60 words — what changed and why, or why nothing did.
5. new_evidence: 0-2 quotes copied character for character from the excerpts, with their section.
Reply with one JSON object matching the schema.
