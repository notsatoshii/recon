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
    "crux_check": dict(qid="q1", question="Q?", crux="c", side_a="a", side_b="b", data_block="d", excerpts="x"),
    "brief_split": dict(split_sheet="S", lens_notes="L", questions_line="q"),
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

    def test_debate_format_names_the_other_view(self):
        self.assertIn('Refer to the other analyst only as "the other view"', DEBATE_FORMAT)


if __name__ == "__main__":
    unittest.main()
