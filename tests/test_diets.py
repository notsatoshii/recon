"""Per-lens diets (docs/v2/phase-c-diets.md): each lens reads only its own sections, built from the
package fixtures by scripts/build_agent_package.build_diets. No LLM, no network."""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from recon import debate  # noqa: E402
import diet_probe  # noqa: E402

DIETS = {k: v for k, v in json.loads((ROOT / "config" / "diets.json").read_text(encoding="utf-8")).items()
         if not k.startswith("_")}


class DietConfigTests(unittest.TestCase):
    def test_every_agent_has_a_diet_and_a_persona_that_names_it(self):
        self.assertEqual(sorted(DIETS), sorted(debate.AGENTS))
        for a in debate.AGENTS:
            persona = (ROOT / "personas" / f"{a}.md").read_text(encoding="utf-8")
            self.assertIn("## What you read", persona, a)
            self.assertIn("## What you believe", persona, a)

    def test_no_two_lenses_read_the_same_thing(self):
        sig = {a: json.dumps(sorted(json.dumps(e, sort_keys=True) for e in v)) for a, v in DIETS.items()}
        self.assertEqual(len(set(sig.values())), len(sig))


class DietBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rep = {}
        cls.text = {}
        for day in ("2026-09-11", "2026-10-04"):
            rep, tmp = diet_probe.diets_for(day)
            cls.rep[day] = rep
            cls.text[day] = {a: (tmp / "01_diets" / f"{a}.md").read_text(encoding="utf-8") for a in rep}
            cls.view = getattr(cls, "view", {})
            cls.view[day] = (diet_probe.FIX / day / "01_filtered.md").read_text(encoding="utf-8")

    def test_every_lens_gets_real_reading_smaller_than_the_shared_view(self):
        for day, rep in self.rep.items():
            for a, r in rep.items():
                self.assertGreater(r["bytes"], 4000, f"{day} {a}")
                self.assertLess(r["bytes"], len(self.view[day].encode("utf-8")) * 0.6, f"{day} {a}")

    def test_reading_follows_the_config(self):
        for day, rep in self.rep.items():
            for a, r in rep.items():
                allowed = {e["section"] for e in DIETS[a]}
                self.assertTrue(set(r["sections"]) <= allowed, f"{day} {a}: {r['sections']}")

    def test_lenses_read_different_material(self):
        for day, texts in self.text.items():
            lines = {a: {l.strip() for l in t.split("\n") if l.strip().startswith("- ")} for a, t in texts.items()}
            for a in lines:
                for b in lines:
                    if a < b and lines[a] and lines[b]:
                        j = len(lines[a] & lines[b]) / len(lines[a] | lines[b])
                        self.assertLess(j, 0.6, f"{day} {a}/{b} overlap {j:.2f}")

    def test_trader_reads_no_ai_tools_and_narrator_no_on_chain_tables(self):
        for day, texts in self.text.items():
            self.assertNotIn("# SECTION: AI & TOOLS", texts["trader"])
            self.assertNotIn("# SECTION: ON-CHAIN & MARKET DATA", texts["narrator"])
            self.assertNotIn("POLYMARKET LIVE MARKETS", texts["analyst"])


class MinusViewTests(unittest.TestCase):
    def test_drops_lines_the_reading_has_and_orphan_headings(self):
        lens = "## A\n- kept line one here\n  snippet of kept\n## B\n- shared line\n  snippet of shared\n"
        self.assertEqual(debate.minus_view(lens, "# SECTION: X\n- shared line\n"),
                         "## A\n- kept line one here\n  snippet of kept")

    def test_empty_view_keeps_everything_but_blanks(self):
        self.assertEqual(debate.minus_view("## A\n\n- x\n", ""), "## A\n- x")


if __name__ == "__main__":
    unittest.main()
