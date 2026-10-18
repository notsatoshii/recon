<!-- schema: resolve · inputs: day, items -->
TASK: RESOLVE OPEN QUESTIONS ({{day}}). You are a neutral resolver. You do not forecast; you read today's
package above and say what it reports. Answer from the material in this prompt only.

For each question below, give its id as ref and an outcome:
- yes: a package line reports that the event happened inside the question's window;
- no: a package line reports that it was formally ruled out, withdrawn or rejected inside the window;
- open: anything else, including when nothing in the package is about it, or when you are unsure.
yes and no need ONE quote copied character for character from a single package line (at most 200
characters) with its section. A program checks every quote: a quote it cannot find, a social-media post or a
prediction-market price leaves the question open. A line that says the event may happen, is planned, is
expected or is being discussed does not report it. why: at most 30 words.

QUESTIONS:
{{items}}

Reply with one JSON object matching the schema: one item per question.
