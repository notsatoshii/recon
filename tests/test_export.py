"""recon/export.py: the Phase D block for the RUBRIC recon page (docs/v2/phase-d-debate-upgrade.md), and old runs
exporting exactly as before. Fixtures: tests/fixtures/export/runs/ (2026-10-18-dlive1, the tagged live Phase D run,
trimmed: no calls, anonymised copies, takes or package text; 2026-10-18, a Phase D-off orchestrator run.json)."""
from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from recon import export  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "export" / "runs"
LIVE = "2026-10-18-dlive1"


class ExportHome(unittest.TestCase):
    """Each test gets a RECON_HOME with the fixture runs under briefs/ (briefs/ itself is gitignored)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(FIX, self.tmp / "briefs")
        self.old_home = export.RECON_HOME
        export.RECON_HOME = self.tmp

    def tearDown(self):
        export.RECON_HOME = self.old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_json(self, run_id: str) -> dict:
        return json.loads((self.tmp / "briefs" / run_id / "run.json").read_text(encoding="utf-8"))


class PhaseDBlock(ExportHome):
    def test_pairs_carry_both_sides(self):
        pd = export.build_run(LIVE)["phase_d"]
        self.assertEqual(pd["version"], 1)
        self.assertEqual([p["question_id"] for p in pd["pairs"]], ["q4", "q2"])
        q4 = pd["pairs"][0]
        self.assertTrue(q4["question"].startswith("Will the SEC or CFTC publish"))
        self.assertEqual((q4["gap_before"], q4["gap_after"], q4["held_split"]), (23, 9, False))
        hi, lo = q4["sides"]
        self.assertEqual((hi["role"], hi["agent"], hi["opponent"]), ("high", "policy_analyst", "macro_strategist"))
        self.assertEqual((hi["take"], hi["final"], hi["label"]), (68, 54, "verified quote"))
        self.assertEqual((lo["agent"], lo["take"], lo["final"], lo["label"]), ("macro_strategist", 45, 45, "held"))
        self.assertTrue(hi["steelman"] and hi["rebuttal"])
        self.assertEqual(hi["i_am_wrong_if"]["by_date"], "2026-11-15")
        self.assertIn("by 2026-11-15", hi["i_am_wrong_if"]["text"])
        self.assertEqual(lo["i_am_wrong_if"]["means"], "yes more likely")
        # the other side's verdict on this side's indicator, and this side's reply
        self.assertEqual(hi["indicator_verdict"], "accept")
        self.assertEqual(hi["reply"]["verdict"], "narrow")
        self.assertEqual(lo["reply"]["verdict"], "hold")
        self.assertEqual(hi["reply"]["on_their_indicator"]["verdict"], "accept")
        self.assertIsNone(hi["reply"]["on_their_indicator"]["counter"])  # an all-empty counter is no indicator

    def test_evidence_statuses(self):
        pd = export.build_run(LIVE)["phase_d"]
        hi = pd["pairs"][0]["sides"][0]
        self.assertEqual(sorted({e["status"] for e in hi["evidence"]}), ["partial", "verified"])
        self.assertEqual(hi["evidence_counts"]["claimed"], 4)
        self.assertEqual((hi["evidence_counts"]["verified"], hi["evidence_counts"]["partial"]), (2, 2))
        self.assertTrue(all(e["quote"] for e in hi["evidence"]))

    def test_judge_items(self):
        pd = export.build_run(LIVE)["phase_d"]
        self.assertEqual([j["question_id"] for j in pd["judge"]], ["q2", "q4"])
        q2 = pd["judge"][0]
        self.assertEqual(q2["indicator"]["text"], "Total DEX volume 7-day percentage change (DeFiLlama) > 0% by 2026-10-25")
        self.assertEqual((q2["status"], q2["resolvable"], q2["data_today"]), ("agreed", True, "favours_high"))
        self.assertTrue(q2["watch_line"].startswith("Watch DeFiLlama"))
        self.assertTrue(q2["split_line"])
        self.assertIs(pd["pairs"][1]["judge"], q2)  # the pair links its judge item

    def test_delphi(self):
        pd = export.build_run(LIVE)["phase_d"]
        self.assertEqual(len(pd["delphi"]), 7)
        b = next(d for d in pd["delphi"] if d["agent"] == "builder")
        self.assertEqual((b["question_id"], b["verdict"], b["from"], b["to"], b["delta"]), ("q5", "revise", 36, 24, -12))
        self.assertEqual((b["moved_by"], b["label"]), ("own_recheck", "verified quote"))
        self.assertEqual(b["would_change_my_mind"]["level"], "187B")
        self.assertEqual(b["evidence"][0]["status"], "verified")
        n = next(d for d in pd["delphi"] if d["agent"] == "narrator")
        self.assertEqual((n["moved_by"], n["label"], n["from"], n["to"]), ("reason_a", "argument", 56, 47))
        self.assertTrue(all(d["question"] for d in pd["delphi"]))

    def test_premortem(self):
        pm = export.build_run(LIVE)["phase_d"]["premortem"]
        self.assertEqual((pm["agent"], pm["question_id"], pm["median"], pm["probability"]), ("analyst", "q1", 31, 58))
        self.assertTrue(pm["failure_story"].startswith("The missing-anchor premise"))
        self.assertEqual(len(pm["early_signs"]), 3)
        self.assertEqual(pm["early_signs"][0]["text"], "Total DeFi TVL (DeFiLlama) < $85B by 2026-10-25")
        self.assertTrue(pm["early_signs"][0]["ok"])

    def test_moves_line_and_summary(self):
        pd = export.build_run(LIVE)["phase_d"]
        self.assertEqual(pd["moves_line"], "5 moves: 4 on a verified quote, 1 on argument alone. Delphi: 6 agents looked "
                                           "again at 7 positions, 2 revised. Cross-exam: 2 pairs, no split held.")
        self.assertEqual(pd["summary"]["moves"], 5)
        self.assertEqual(pd["plan"]["pairs"], 2)
        self.assertEqual(export.moves_line({"moves": 0, "labels": {}}, []), "0 moves.")
        self.assertEqual(export.moves_line({"moves": 1, "labels": {"argument": 1}, "xexam": {"pairs": 1, "held_splits": 1}}, []),
                         "1 move: 0 on a verified quote, 1 on argument alone. Cross-exam: 1 pair, 1 split held.")

    def test_calibration_from_the_runs_json(self):
        c = export.build_run(LIVE)["phase_d"]["calibration"]
        self.assertEqual((c["from"], c["resolved"], c["round0_mean_brier"], c["post_mean_brier"]), ("json", 0, None, None))
        self.assertEqual(c["components"]["delphi"], {"changed": 0, "mean_delta": None, "on": True})
        self.assertFalse(c["weights"])

    def test_calibration_from_the_md(self):
        rd = self.tmp / "briefs" / LIVE
        (rd / "state" / "questions" / "calibration.json").unlink()
        md = (rd / "00_brier.md").read_text(encoding="utf-8")
        md = md.replace("Resolved yes/no: 0 (0 yes, 0 no); void 0", "Resolved yes/no: 12 (5 yes, 7 no); void 1")
        md = md.replace("| lenses' mean probability, mean Brier | None | None |", "| lenses' mean probability, mean Brier | 0.214 | 0.198 |")
        md = md.replace("| agent | n | round 0 mean | round 0 median | post mean | post median |\n|---|---|---|---|---|---|",
                        "| agent | n | round 0 mean | round 0 median | post mean | post median |\n|---|---|---|---|---|---|\n"
                        "| trader | 12 | 0.2 | 0.18 | 0.19 | 0.17 |\n| skeptic | 12 | 0.25 | 0.2 | 0.22 | 0.2 |")
        (rd / "00_brier.md").write_text(md, encoding="utf-8")
        c = export.build_run(LIVE)["phase_d"]["calibration"]
        self.assertEqual((c["from"], c["resolved"], c["yes"], c["no"], c["void"]), ("md", 12, 5, 7, 1))
        self.assertEqual((c["round0_mean_brier"], c["post_mean_brier"]), (0.214, 0.198))
        self.assertEqual(c["agents_scored"], 2)
        self.assertEqual(c["components"]["xexam"]["on"], True)

    def test_state_dir_calibration_only_when_it_is_the_same_report(self):
        rd = self.tmp / "briefs" / LIVE
        cal = rd / "state" / "questions" / "calibration.json"
        other = self.tmp / "state" / "questions"
        other.mkdir(parents=True)
        j = json.loads(cal.read_text(encoding="utf-8"))
        cal.unlink()
        run = self.run_json(LIVE)
        run["state_dir"] = str(self.tmp / "state")
        (other / "calibration.json").write_text(json.dumps({**j, "generated": "2026-10-19T20:00:00Z", "resolved": 9}), encoding="utf-8")
        self.assertEqual(export.calibration_out(rd, run)["from"], "md")  # a later report: the run's own md wins
        (other / "calibration.json").write_text(json.dumps({**j, "resolved": 9}), encoding="utf-8")
        self.assertEqual(export.calibration_out(rd, run)["resolved"], 9)

    def test_pairs_rebuilt_from_openings_without_scores(self):
        run = self.run_json(LIVE)
        run["phase_d"]["xexam"] = []
        pd = export.phase_d_block(self.tmp / "briefs" / LIVE, run)
        self.assertEqual(sorted(p["question_id"] for p in pd["pairs"]), ["q2", "q4"])
        q4 = next(p for p in pd["pairs"] if p["question_id"] == "q4")
        self.assertEqual(q4["sides"][0]["agent"], "policy_analyst")  # the higher opening is the high side
        self.assertEqual(q4["sides"][0]["final"], 54)  # from the reply's move


class PageFields(ExportHome):
    def test_existing_xexam_entries_are_not_doubled(self):
        run = export.build_run(LIVE)
        orig = self.run_json(LIVE)
        self.assertEqual(run["edges"], orig["edges"])
        for a, o in zip(run["agents"], orig["agents"]):
            self.assertEqual(len(a["challenges_made"]), len(o["challenges_made"]), a["name"])
            self.assertEqual(len(a["challenges_received"]), len(o["challenges_received"]), a["name"])

    def test_debates_filled_from_the_pairs(self):
        run = export.build_run(LIVE)
        self.assertEqual(len(run["debates"]), 4)
        d = next(x for x in run["debates"] if x["challenger"] == "builder")
        self.assertEqual((d["target"], d["type"], d["question_id"], d["gap_before"], d["gap_after"]), ("skeptic", "xexam", "q2", 20, 11))
        self.assertEqual(d["response_verdict"], "narrow")
        self.assertIn("DeFiLlama", d["crux"])

    def test_edges_and_challenges_filled_when_missing(self):
        run = self.run_json(LIVE)
        run["edges"] = []
        for a in run["agents"]:
            a["challenges_made"], a["challenges_received"] = [], []
        (self.tmp / "briefs" / LIVE / "run.json").write_text(json.dumps(run), encoding="utf-8")
        out = export.build_run(LIVE)
        self.assertEqual(sorted((e["from"], e["to"]) for e in out["edges"]),
                         [("builder", "skeptic"), ("macro_strategist", "policy_analyst"),
                          ("policy_analyst", "macro_strategist"), ("skeptic", "builder")])
        self.assertTrue(all(e["type"] == "xexam" for e in out["edges"]))
        ag = {a["name"]: a for a in out["agents"]}
        self.assertEqual(ag["builder"]["challenges_made"][0]["to"], "skeptic")
        self.assertEqual(ag["builder"]["challenges_received"][0]["from"], "skeptic")
        self.assertTrue(ag["builder"]["challenges_made"][0]["text"])
        self.assertEqual(ag["trader"]["challenges_made"], [])
        again = copy.deepcopy(out)
        export.fill_debate_fields(again, again["phase_d"])
        self.assertEqual(again["edges"], out["edges"])  # idempotent
        self.assertEqual(again["debates"], out["debates"])


class OldRunsUnchanged(ExportHome):
    def test_phase_d_off_run_json_exports_as_written(self):
        self.assertEqual(export.build_run("2026-10-18"), self.run_json("2026-10-18"))
        self.assertIsNone(export.build_run("2026-10-18").get("phase_d"))

    def test_phase_c_run_json_exports_as_written(self):
        run = self.run_json("2026-10-18")
        run.update({"debate_mode": "c", "debates": [{"challenger": "trader", "target": "skeptic", "type": "pair"}]})
        rd = self.tmp / "briefs" / "2026-10-19"
        (rd / "phases" / "challenges").mkdir(parents=True)
        (rd / "phases" / "challenges" / "pair__trader__q1.json").write_text("{}", encoding="utf-8")
        (rd / "run.json").write_text(json.dumps(run), encoding="utf-8")
        self.assertEqual(export.build_run("2026-10-19"), run)

    def test_v1_layout_still_scraped(self):
        rd = self.tmp / "briefs" / "2026-09-01"
        rd.mkdir()
        (rd / "03_take_trader.md").write_text("Trader take", encoding="utf-8")
        (rd / "03_take_skeptic.md").write_text("Skeptic take", encoding="utf-8")
        (rd / "04a_trader_vs_skeptic.md").write_text("I challenge you", encoding="utf-8")
        run = export.build_run("2026-09-01")
        self.assertNotIn("phase_d", run)
        self.assertEqual(run["edges"], [{"from": "trader", "to": "skeptic", "type": "tension"}])
        self.assertEqual({a["name"] for a in run["agents"]}, {"trader", "skeptic"})


class Indicators(unittest.TestCase):
    def test_text(self):
        self.assertEqual(export.indicator_text({"observable": "Total DeFi TVL", "source": "DeFiLlama", "comparator": "<",
                                                "level": "$85B", "by_date": "2026-10-25"}),
                         "Total DeFi TVL (DeFiLlama) < $85B by 2026-10-25")
        self.assertEqual(export.indicator_text({"observable": "SEC posts a rule", "comparator": "does_not_happen",
                                                "by_date": "2026-11-01"}), "SEC posts a rule does not happen by 2026-11-01")
        self.assertEqual(export.indicator_text({"observable": "", "source": "x"}), "")
        self.assertIsNone(export.indicator_out(None))


if __name__ == "__main__":
    unittest.main()
