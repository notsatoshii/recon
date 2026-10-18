<!-- schema: xexam_open · inputs: role, debate_format, qid, question, resolves_on, settles_with,
     my_p, my_reason, my_evidence, their_p, their_reason, their_summary, their_evidence, excerpts -->
{{role}}

TASK: CROSS-EXAMINATION — OPENING. Today's lenses split on one question and you hold one end of it. The
other view holds the other end. You will not decide who is right; the date will. Your job is to make the
split checkable.
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
1. steelman: the other view's best case in at most two sentences, stated so that its holder would call it
   fair. No "but", no rebuttal inside it.
2. i_am_wrong_if: the dated observation that would show YOU are wrong, before or when the question resolves.
   An observable a reader can check (a named series, filing, announcement or count), where it is published,
   the comparator (>, >=, <, <=, happens, does_not_happen), the level as written (empty for happens /
   does_not_happen), by_date (YYYY-MM-DD, no later than {{resolves_on}} when the question is dated) and if_true
   (yes_more_likely or no_more_likely: what it coming true means for the question). Pick one you would really
   concede on, not one that cannot happen.
3. rebuttal: at most 120 words on why your number is closer to right. Do not open by agreeing.
4. evidence: 0-3 quotes copied character for character from the excerpts, each with its section. A program
   checks every quote; a quote it cannot find counts against you.
5. probability: your number now, a whole number 0-100.
Reply with one JSON object matching the schema.
