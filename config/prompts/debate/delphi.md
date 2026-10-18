<!-- schema: delphi · inputs: role, debate_format, day, items, excerpts -->
{{role}}

TASK: SECOND LOOK ({{day}}). On the questions below your number sits outside where most lenses landed. You
see the anonymised spread and the two strongest reasons from the other side of the median, picked by a
program for their verified evidence. Look again, once, and answer for every question.
{{debate_format}}

{{items}}

PACKAGE EXCERPTS AROUND THEIR EVIDENCE (the only data you may quote):
{{excerpts}}

For each question give one revision:
- verdict: hold is the default. revise only when one of the two reasons, or your own recheck of the data
  above, changes your number. Being outside the spread is not a reason to move: lenses that read different
  sources should disagree.
- new_probability: a whole number 0-100 (your take when you hold).
- moved_by: reason_a, reason_b, own_recheck, or none when you hold.
- reason: at most 40 words, what changed or why nothing did.
- evidence: 0-2 quotes copied character for character from the excerpts, each with its section. A program
  checks every quote and labels each move by whether a verified data quote backs it; your first number stays
  on record either way.
- would_change_my_mind: the one dated observation that would move you: the observable, where it is
  published, the comparator (>, >=, <, <=, happens, does_not_happen), the level (empty for happens /
  does_not_happen), by_date (YYYY-MM-DD, no later than the question resolves) and if_true (yes_more_likely or
  no_more_likely: what it coming true means for the question).
Reply with one JSON object matching the schema.
