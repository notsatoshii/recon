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
