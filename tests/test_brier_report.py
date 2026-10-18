"""Phase D step 2: scripts/brier_report.py (docs/v2/phase-d-debate-upgrade.md). Hand-computed Brier scores, the
component gate and the scorecard section."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO), str(REPO / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from recon import debate  # noqa: E402
import brier_report as br  # noqa: E402


def q(lid, takes, finals=None, stages=None, day="2026-10-05"):
    line = {"type": "question", "ledger_id": lid, "day": day, "question": {"kind": "threshold"},
            "take_values": takes, "finals": finals if finals is not None else dict(takes)}
    if stages is not None:
        line["stages"] = stages
    return line


def r(lid, outcome, method="package"):
    return {"type": "resolution", "ledger_id": lid, "outcome": outcome, "method": method}


class BrierTests(unittest.TestCase):
    def test_scores_by_hand(self):
        lines = [q("a-q1", {"trader": 80, "skeptic": 20}, {"trader": 70, "skeptic": 40}), r("a-q1", "yes"),
                 q("a-q2", {"trader": 60, "skeptic": 10}), r("a-q2", "no"),
                 q("a-q3", {"trader": 50, "skeptic": 50}), r("a-q3", "void", "void_rule"),
                 q("a-q4", {"trader": 90})]                                   # open: not scored
        rep = br.report(lines)
        self.assertEqual((rep["resolved"], rep["yes"], rep["no"], rep["void"]), (2, 1, 1, 1))
        t = rep["per_agent"]["trader"]
        self.assertAlmostEqual(t["round0_mean"], ((0.8 - 1) ** 2 + 0.6 ** 2) / 2)
        self.assertAlmostEqual(t["post_mean"], ((0.7 - 1) ** 2 + 0.6 ** 2) / 2)
        g = rep["group"]
        self.assertAlmostEqual(g["round0_mean_brier"], round(((0.5 - 1) ** 2 + 0.35 ** 2) / 2, 4))
        self.assertAlmostEqual(g["post_mean_brier"], round(((0.55 - 1) ** 2 + 0.35 ** 2) / 2, 4))
        self.assertIsNone(rep["weights"])

    def test_newest_question_line_wins(self):
        lines = [q("a-q1", {"trader": 10}), q("a-q1", {"trader": 90}), r("a-q1", "yes")]
        self.assertAlmostEqual(br.report(lines)["per_agent"]["trader"]["round0_mean"], 0.01)

    def test_component_gate(self):
        worse = []
        for k in range(br.GATE_MIN_CHANGED):
            lid = f"d-q{k}"
            worse += [q(lid, {"a": 80, "b": 80}, {"a": 60, "b": 80}, stages={"delphi": {"a": 60}, "xexam": {}}),
                      r(lid, "yes")]
        rep = br.report(worse)
        d = rep["components"]["delphi"]
        self.assertEqual(d["changed"], br.GATE_MIN_CHANGED)
        self.assertGreater(d["mean_delta"], 0)
        self.assertFalse(d["on"])
        self.assertTrue(rep["components"]["xexam"]["on"])            # changed nothing
        self.assertTrue(br.report(worse[:-2])["components"]["delphi"]["on"])   # one under the minimum: stays on
        # lines without stages (Phase C, debate off) never count for a component
        plain = [x for k in range(30) for x in (q(f"p-q{k}", {"a": 80}, {"a": 20}), r(f"p-q{k}", "yes"))]
        self.assertEqual(br.report(plain)["components"]["xexam"]["changed"], 0)

    def test_weights_after_forty(self):
        lines = []
        for k in range(br.WEIGHTS_MIN):
            lines += [q(f"w-q{k}", {"good": 90, "bad": 30}), r(f"w-q{k}", "yes")]
        w = br.report(lines)["weights"]
        self.assertGreater(w["per_agent"]["good"], 1)
        self.assertLess(w["per_agent"]["bad"], 1)
        self.assertTrue(all(0.5 <= v <= 2 for v in w["per_agent"].values()))
        self.assertLess(w["weighted_mean_brier"], w["plain_mean_brier"])

    def test_scorecard_section_and_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = Path(tmp) / "questions" / "ledger.jsonl"
            led.parent.mkdir()
            led.write_text("\n".join(json.dumps(x) for x in (q("a-q1", {"trader": 80, "macro_strategist": 30}),
                                                              r("a-q1", "yes"))) + "\n", encoding="utf-8")
            sc = Path(tmp) / "00_scorecard.md"
            sc.write_text("# PREDICTION SCORECARD\n## Scored: 2026-10-18\n\n## Market Snapshot\n- bitcoin: $1\n\n"
                          "## Pending Predictions\n### TRADER\n- [2026-10-17] x\n", encoding="utf-8")
            md = Path(tmp) / "brier.md"
            self.assertEqual(br.main(["--ledger", str(led), "--md", str(md), "--scorecard", str(sc)]), 0)
            self.assertEqual(br.main(["--ledger", str(led), "--scorecard", str(sc)]), 0)   # once only
            body = sc.read_text(encoding="utf-8")
            self.assertEqual(body.count("## CALIBRATION"), 1)
            self.assertLess(body.index("## CALIBRATION"), body.index("## Market Snapshot"))
            sec = body[body.index("## CALIBRATION"):body.index("## Market Snapshot")]
            self.assertIn("1 resolved (1 yes, 0 no)", sec)
            self.assertEqual(debate.brief_checks(sec, None)["agent_names"], [])
            cal = json.loads((led.parent / "calibration.json").read_text(encoding="utf-8"))
            self.assertTrue(cal["components"]["delphi"]["on"])
            self.assertIn("| trader | 1 |", md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
