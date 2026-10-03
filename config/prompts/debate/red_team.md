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
