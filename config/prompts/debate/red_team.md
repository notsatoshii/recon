<!-- schema: premortem · inputs: role, debate_format, qid, question, resolves_on, settles_with, median,
     count_phrase, majority_reasons, my_p, my_reason, excerpts -->
{{role}}

TASK: PRE-MORTEM. Today the lenses broadly agree on this question. Imagine it is the resolution date
({{resolves_on}}) and the consensus turned out to be wrong. Your job is to explain why, so the reader knows
what could go wrong and what to watch for before it does.
{{debate_format}}

QUESTION [{{qid}}]: {{question}}
Resolves: {{resolves_on}}. Settles with: {{settles_with}}
CONSENSUS: median {{median}}% ({{count_phrase}}).
THE REASONS BEHIND IT:
{{majority_reasons}}
YOUR OWN TAKE: {{my_p}}% — {{my_reason}}

PACKAGE EXCERPTS (the only data you may quote):
{{excerpts}}

Write: consensus_view (one sentence: the view that turned out wrong); failure_story (at most 150 words: the
most plausible way it went wrong, built on the data above, not on generic doubt); early_signs (1-3 dated
indicators that would show it going wrong before the resolution date: observable, source, comparator (>, >=,
<, <=, happens, does_not_happen), level (empty for happens / does_not_happen), by_date (YYYY-MM-DD, no later
than the resolution date when it is dated), if_true (yes_more_likely or no_more_likely)); evidence (0-3
verbatim quotes with section); probability (your own honest number, a whole number 0-100 — the story is a
stress test, your number does not have to follow it).
Reply with one JSON object matching the schema.
