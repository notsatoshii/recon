"""Phase C template tests (docs/v2/phase-c-spec.md §17.3): every template renders with the variables the
orchestrator passes, a missing or an extra variable raises, the pair prompts stay small, and response.md
states the gate the code enforces."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from recon import debate, evidence, prompts  # noqa: E402
from recon.orchestrator import CITATION_RULE, DEBATE_FORMAT, ROLE  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "package" / "2026-10-04"

# The variables each template gets from recon/orchestrator.py
VARS = {
    "questions": dict(day="2026-10-04", roster="- trader: markets", open_questions="- none", due_items="- none",
                      novel_items="- none", n_max=5),
    "take": dict(role="R", context="C", lens_data="L", citation_rule=CITATION_RULE),
    "challenge": dict(role="R", debate_format=DEBATE_FORMAT, qid="q1", question="Q?", resolves_on="2026-10-11",
                      settles_with="S", my_p=40, my_reason="r", my_evidence="- e", their_p=80, their_reason="r",
                      their_summary="s", their_evidence="- e", excerpts="x"),
    "response": dict(role="R", debate_format=DEBATE_FORMAT, qid="q1", question="Q?", resolves_on="2026-10-11", my_p=40,
                     my_reason="r", my_evidence="- e", steelman="s", crux="c", rebuttal="r", their_evidence="- e",
                     would_change="w", excerpts="x", crux_data="d"),
    "red_team": dict(role="R", debate_format=DEBATE_FORMAT, qid="q1", question="Q?", resolves_on="2026-10-11",
                     settles_with="S", median=60, count_phrase="all 9 lenses within 8 points of 60%",
                     majority_reasons="- r", my_p=40, my_reason="r", excerpts="x"),
    "crux_check": dict(qid="q1", question="Q?", crux="c", side_a="a", side_b="b",
                       higher_label="the view that started at 70%", lower_label="the view that started at 40%",
                       data_block="d", excerpts="x"),
    "brief_split": dict(split_sheet="S", lens_notes="L", questions_line="q"),
    "resolve": dict(day="2026-10-04", items="[e1] Q? (window: after 2026-10-01 through 2026-10-05)"),
    "delphi": dict(role="R", debate_format=DEBATE_FORMAT, day="2026-10-04", items="QUESTION [q1]: Q?", excerpts="x"),
    "xexam_open": dict(role="R", debate_format=DEBATE_FORMAT, qid="q1", question="Q?", resolves_on="2026-10-11",
                       settles_with="S", my_p=40, my_reason="r", my_evidence="- e", their_p=80, their_reason="r",
                       their_summary="s", their_evidence="- e", excerpts="x"),
    "xexam_reply": dict(role="R", debate_format=DEBATE_FORMAT, qid="q1", question="Q?", resolves_on="2026-10-11",
                        my_p=40, my_reason="r", my_evidence="- e", steelman="s", their_indicator="i", rebuttal="r",
                        their_evidence="- e", excerpts="x"),
    "judge": dict(day="2026-10-04", items="QUESTION [q1]: Q?", candidates="- none"),
}


class TemplateTests(unittest.TestCase):
    def test_render_and_drift(self):
        for name, v in VARS.items():
            with self.subTest(name=name):
                out = prompts.render(name, **v)
                self.assertNotIn("{{", out)
                self.assertFalse(out.lstrip().startswith("<!--"))
                k = next(iter(v))
                with self.assertRaises(KeyError):
                    prompts.render(name, **{x: y for x, y in v.items() if x != k})
                with self.assertRaises(ValueError):
                    prompts.render(name, **v, not_a_placeholder="x")

    def test_pair_prompts_small(self):
        docs = {f: (FIX / f"{f}").read_text(encoding="utf-8") for f in ("00_data_package.md", "00_raw_data.md", "01_filtered.md")}
        loc = evidence.Locator({"package": docs["00_data_package.md"], "raw": docs["00_raw_data.md"],
                                "view": docs["01_filtered.md"]})
        lines = [l for l in docs["00_data_package.md"].splitlines() if l.startswith("- ") and re.search(r"\d", l)
                 and 60 < len(l) < 200]
        mine = [{"section": "ON-CHAIN", "quote": lines[10]}, {"section": "NEWS", "quote": lines[40]}]
        theirs = [{"section": "ON-CHAIN", "quote": lines[20]}, {"section": "NEWS", "quote": lines[60]}]
        ex = debate.excerpts([mine, theirs], loc)
        role = ROLE.format(persona=(REPO / "personas" / "trader.md").read_text(encoding="utf-8"))
        ch = prompts.render("challenge", **{**VARS["challenge"], "role": role, "excerpts": ex,
                                            "my_reason": "x" * 150, "their_reason": "y" * 150, "their_summary": "z" * 300,
                                            "my_evidence": "\n".join(f"- \"{e['quote']}\"" for e in mine),
                                            "their_evidence": "\n".join(f"- \"{e['quote']}\"" for e in theirs)})
        rs = prompts.render("response", **{**VARS["response"], "role": role, "excerpts": ex, "steelman": "s" * 300,
                                           "rebuttal": "r" * 800, "crux_data": "d" * 3000})
        self.assertLess(len(ch.encode("utf-8")), 9 * 1024)
        self.assertLess(len(rs.encode("utf-8")), 12 * 1024)   # + the 3 KB crux data block

    def test_response_states_the_gate(self):
        body = prompts.template("response")
        rule = body[body.index("3. verdict"):body.index("4. reason")]
        self.assertNotIn("the other view's evidence", rule)
        self.assertIn(f"up to {debate.FREE_MOVE} points on", rule)
        self.assertIn(f"{debate.EVIDENCE_MOVE_PER_ITEM} points more", rule)
        self.assertIn(f"up to {debate.FREE_MOVE + debate.EVIDENCE_MOVE_MAX} points in total", rule)

    def test_phase_d_prompts_state_the_rules(self):
        """Phase D: moves are labelled, never blocked, and hold is the default; every indicator is dated and has a
        comparator from schemas.COMPARATORS; the pre-mortem is a pre-mortem; the judge picks no winner."""
        from recon import schemas
        for name in ("delphi", "xexam_reply"):
            body = prompts.template(name).lower()
            self.assertIn("hold is the default", body, name)
            self.assertIn("labels", body, name)
            self.assertNotIn("up to 5 points", body, name)
        for name in ("delphi", "xexam_open", "xexam_reply", "judge", "red_team"):
            body = prompts.template(name)
            for c in schemas.COMPARATORS:
                self.assertIn(c, body, (name, c))
            self.assertIn("YYYY-MM-DD", body, name)
        self.assertIn("PRE-MORTEM", prompts.template("red_team"))
        self.assertIn("<!-- schema: premortem", (prompts.PROMPT_DIR / "red_team.md").read_text(encoding="utf-8"))
        self.assertIn("do not pick a winner", prompts.template("judge"))

    def test_xexam_prompts_small(self):
        role = ROLE.format(persona=(REPO / "personas" / "skeptic.md").read_text(encoding="utf-8"))
        op = prompts.render("xexam_open", **{**VARS["xexam_open"], "role": role, "excerpts": "e" * 4096,
                                             "my_reason": "x" * 150, "their_reason": "y" * 150, "their_summary": "z" * 300})
        rp = prompts.render("xexam_reply", **{**VARS["xexam_reply"], "role": role, "excerpts": "e" * 4096,
                                              "steelman": "s" * 300, "rebuttal": "r" * 800, "their_indicator": "i" * 300})
        self.assertLess(len(op.encode("utf-8")), 10 * 1024)
        self.assertLess(len(rp.encode("utf-8")), 10 * 1024)

    def test_debate_format_names_the_other_view(self):
        self.assertIn('Refer to the other analyst only as "the other view"', DEBATE_FORMAT)


if __name__ == "__main__":
    unittest.main()
