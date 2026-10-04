"""scripts/replay_report.py item (g): take-spread stability across same-day runs (phase-c-spec §15.5, §20.7 #73).

The 09-11 numbers are the measured take ranges of the c1, c1-r1, c6 and c8 replays and the p1 take-only probe
(briefs on the droplet, 2026-10-04): the OpenAI Pro question cleared GAP_MIN 20 in every sample (22-37), the
Hormuz question only in some (12 on the troop wording, 23-38 on the contribution wording) and c8's triage did
not ask it, so c8 staged 1 pair where c6 staged 2. Before #73 the summary judged depth from one replay.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
import replay_report  # noqa: E402

AGENTS = ["ai_engineer", "analyst", "builder", "macro_strategist", "narrator", "policy_analyst", "skeptic",
          "trader", "user_agent"]
OPENAI = "Will OpenAI resume new Pro subscription sign-ups by 2026-09-18?"
HORMUZ = "Will South Korea announce a contribution to Hormuz security by 2026-09-25?"
HORMUZ_TROOP = "Will South Korea announce troop deployment to support Hormuz security by 2026-09-25?"
BTC = "Will Bitcoin trade below $75,000 by 2026-09-18?"
COWORK = "Will Anthropic restore Claude Cowork's Windows local-command functionality by 2026-09-18?"

# run id -> ([(qid, text, sorted take values)], pairs or None for a take-only run)
RUNS = {
    "2026-09-11-c6": ([("q1", BTC, [57, 58, 61, 61, 62, 62, 62, 64, 64]),
                       ("q3", HORMUZ, [38, 38, 44, 46, 46, 57, 57, 58, 61]),
                       ("q4", OPENAI, [35, 35, 38, 43, 55, 64, 67, 68, 72])], 2),
    "2026-09-11-c8": ([("q1", BTC, [54, 57, 57, 58, 61, 62, 62, 64, 64]),
                       ("q3", OPENAI, [35, 35, 38, 38, 38, 42, 43, 58, 70]),
                       ("q4", COWORK, [67, 68, 70, 72, 72, 73, 74, 78, 82])], 1),
    "2026-09-11-c1-r1": ([("q1", BTC, [59, 62, 62, 62, 62, 63, 63, 67, 68]),
                          ("q3", HORMUZ, [34, 46, 56, 57, 58, 58, 58, 62, 72]),
                          ("q4", OPENAI, [31, 32, 34, 35, 35, 42, 44, 55, 65])], 2),
    "2026-09-11-p1": ([("q1", BTC, [57, 57, 57, 62, 62, 62, 64, 66, 67]),
                       ("q3", HORMUZ_TROOP, [12, 14, 18, 18, 18, 18, 18, 18, 24]),
                       ("q4", OPENAI, [45, 61, 62, 64, 65, 66, 68, 68, 71])], None),
}


def write_run(root: Path, rid: str, qs, pairs) -> None:
    d = root / rid / "phases"
    (d / "takes").mkdir(parents=True)
    take_p = {a: {qid: vals[i] for qid, _, vals in qs} for i, a in enumerate(AGENTS)}
    questions = [{"id": qid, "text": text} for qid, text, _ in qs]
    (d / "triage.json").write_text(json.dumps({"data": {"questions": questions}, "gate": {}}), encoding="utf-8")
    if pairs is None:   # take-only rerun (RECON_STOP_AFTER=takes): no positions.json, no pairing.json
        for a in AGENTS:
            pos = [{"question_id": qid, "probability": take_p[a][qid]} for qid, _, _ in qs]
            (d / "takes" / f"{a}.json").write_text(json.dumps({"agent": a, "data": {"positions": pos}}), encoding="utf-8")
        return
    (d / "positions.json").write_text(json.dumps({"take_p": take_p, "questions": questions, "debates": []}),
                                      encoding="utf-8")
    pr = [{"question_id": f"q{i}", "high": "a", "low": "b", "gap": 30} for i in range(pairs)]
    (d / "pairing.json").write_text(json.dumps({"day_type": "debate", "pairs": pr, "target": 2, "gap_min": 20}),
                                    encoding="utf-8")


class SpreadStabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for rid, (qs, pairs) in RUNS.items():
            write_run(self.root, rid, qs, pairs)

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_topic_across_triage_wordings(self):
        self.assertTrue(replay_report.same_topic(OPENAI, "Will OpenAI reopen ChatGPT Pro subscriptions by 2026-09-25?"))
        self.assertTrue(replay_report.same_topic(HORMUZ, HORMUZ_TROOP))
        self.assertFalse(replay_report.same_topic(OPENAI, COWORK))
        self.assertFalse(replay_report.same_topic(BTC, HORMUZ))

    def test_take_only_run_is_a_sample(self):
        s = replay_report.run_sample(self.root, "2026-09-11-p1")
        self.assertIsNotNone(s)
        self.assertIsNone(s["pairs"])
        self.assertEqual(s["take_p"]["ai_engineer"], {"q1": 57, "q3": 12, "q4": 45})

    def test_09_11_pair_count_is_a_range_and_hormuz_unstable(self):
        samples = [replay_report.run_sample(self.root, r) for r in RUNS]
        (day,) = replay_report.spread_stability(samples, 20)
        self.assertEqual(sorted(day["pairs"]), [1, 2, 2])
        topics = {t["label"]: t for t in day["topics"]}
        openai, hormuz = topics["OpenAI, Pro"], topics["Hormuz, South Korea"]
        # p1's 26 is one lens (45 against 61-71, 10 without it): it no longer clears, so OpenAI is unstable (§20.7 #83)
        self.assertEqual((openai["asked"], openai["clears"], openai["lone"], openai["unstable"]), (4, 3, 1, True))
        self.assertEqual(sorted(openai["ranges"]), [26, 34, 35, 37])
        self.assertEqual((hormuz["asked"], hormuz["clears"], hormuz["unstable"]), (3, 2, True))
        self.assertEqual(topics["Bitcoin"]["clears"], 0)
        self.assertEqual(topics["Anthropic, Claude, Cowork"]["asked"], 1)
        self.assertEqual(day["topics"][0]["label"], "OpenAI, Pro")

    def test_summary_reports_item_g(self):
        with contextlib.redirect_stdout(io.StringIO()):
            replay_report.main(["2026-09-11-c6", "2026-09-11-c8", "--root", str(self.root), "--old-dir", str(self.root),
                                "--spread", "2026-09-11-c1-r1", "2026-09-11-p1"])
        text = (self.root / "replay_summary.md").read_text(encoding="utf-8")
        self.assertIn("(g) take-spread stability 2026-09-11, 4 samples", text)
        self.assertIn("pairs 1-2 over 3 runs to pairing (target 2); one replay's pair count is one sample", text)
        self.assertIn("Hormuz, South Korea: asked 3/4, take range 12-38", text)
        self.assertIn("in 2/3 (UNSTABLE", text)
        self.assertIn("OpenAI, Pro: asked 4/4, take range 26-37 (37, 35, 34, 26), range >= GAP_MIN 20 in 3/4 (one lens "
                      "carries the range in 1: not counted) (UNSTABLE", text)


if __name__ == "__main__":
    unittest.main()


class GapRatioTests(unittest.TestCase):
    """Pass-bar item (c) per debate (§20.7 #78): on 09-11 the q3 Hormuz pair went 22 -> 13 on two free moves (0.59,
    under the 0.6 bar) while the median read 0.7; the report showed only the median."""
    DEBATES = [{"question_id": "q3", "high": "macro_strategist", "low": "trader", "gap_before": 22, "gap_after": 13},
               {"question_id": "q4", "high": "skeptic", "low": "builder", "gap_before": 37, "gap_after": 30},
               {"question_id": "q5", "high": "analyst", "low": "narrator", "gap_before": 30, "gap_after": 21},
               {"question_id": "q1", "high": "ai_engineer", "low": "user_agent", "gap_before": 30, "gap_after": 5,
                "closed_on_data": True}]

    def test_each_ratio_beside_the_median(self):
        per, med = replay_report.gap_ratios(self.DEBATES)
        self.assertAlmostEqual(med, 0.7)
        self.assertEqual([x["question_id"] for x in per], ["q3", "q4", "q5"])   # q1 closed on crux data
        self.assertEqual([x["below_bar"] for x in per], [True, False, False])
        self.assertEqual(replay_report.render_ratios(per),
                         "q3 macro_strategist/trader 22->13 0.59 (< 0.6); q4 skeptic/builder 37->30 0.81; "
                         "q5 analyst/narrator 30->21 0.70")

    def test_report_prints_per_debate_ratios(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qs = [("q3", HORMUZ, [27, 30, 35, 40, 41, 42, 45, 47, 49])]
            write_run(root, "2026-09-11-x", qs, 1)
            pos = root / "2026-09-11-x" / "phases" / "positions.json"
            data = json.loads(pos.read_text(encoding="utf-8"))
            data["debates"] = [dict(self.DEBATES[0], effect="narrowed from 22 to 13 on argument")]
            pos.write_text(json.dumps(data), encoding="utf-8")
            text, m = replay_report.report(root, "2026-09-11-x", None, None)
        self.assertEqual(m["gap_ratios_no_crux"][0]["ratio"], 0.591)
        self.assertIn("0.59 (per debate: q3 macro_strategist/trader 22->13 0.59 (< 0.6))", text)


class NearMissTests(unittest.TestCase):
    """09-11 c10 (§20.7 #82): q1-q3 take ranges 18/18/19 sat just under GAP_MIN 20, so 1 pair was staged (q4, OpenAI
    Pro, 32) where c9 staged 2; the report gave the pair count with nothing saying three questions missed by 1-2
    points on one draw. The per-run report lists near misses; item (g) flags a topic that misses in two samples."""
    C10 = [("q1", BTC, [54, 56, 57, 58, 60, 62, 63, 70, 72]),               # 18
           ("q2", COWORK, [60, 62, 64, 66, 70, 72, 74, 76, 78]),            # 18
           ("q3", HORMUZ, [30, 33, 36, 38, 40, 42, 44, 46, 49]),            # 19
           ("q4", OPENAI, [36, 38, 40, 44, 50, 60, 62, 66, 68])]            # 32
    C10T1 = [("q1", BTC, [55, 57, 58, 60, 61, 62, 64, 70, 73]),             # 18 again
             ("q2", COWORK, [62, 64, 66, 68, 70, 72, 74, 76, 77]),          # 15: outside the band
             ("q3", HORMUZ, [28, 32, 36, 38, 40, 42, 44, 46, 50]),          # 22: clears
             ("q4", OPENAI, [38, 40, 42, 44, 50, 60, 62, 66, 68])]          # 30

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_run(self.root, "2026-09-11-c10", self.C10, 1)
        write_run(self.root, "2026-09-11-c10t1", self.C10T1, None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_report_lists_near_misses(self):
        text, m = replay_report.report(self.root, "2026-09-11-c10", None, None)
        self.assertEqual(m["near_misses"], [{"question_id": "q1", "range": 18}, {"question_id": "q2", "range": 18},
                                            {"question_id": "q3", "range": 19}])
        self.assertIn("near misses: take range within 3 under gap_min 20", text)
        self.assertIn("| q1 18; q2 18; q3 19 |", text)

    def test_no_near_miss_when_ranges_clear_or_fall_short(self):
        pos = {"questions": [{"id": "q1", "take_stats": {"range": 20}}, {"id": "q2", "take_stats": {"range": 16}},
                             {"id": "q3", "take_stats": {"range": 17}}]}
        self.assertEqual(replay_report.near_misses(pos, 20), [{"question_id": "q3", "range": 17}])

    def test_repeated_near_miss_flagged_in_item_g(self):
        samples = [replay_report.run_sample(self.root, r) for r in ("2026-09-11-c10", "2026-09-11-c10t1")]
        (day,) = replay_report.spread_stability(samples, 20)
        topics = {t["label"]: t for t in day["topics"]}
        self.assertTrue(topics["Bitcoin"]["near_repeat"])                            # 18, 18
        self.assertFalse(topics["Anthropic, Claude, Cowork"]["near_repeat"])         # 18, 15
        self.assertFalse(topics["Hormuz, South Korea"]["near_repeat"])               # 19, 22: UNSTABLE instead
        self.assertTrue(topics["Hormuz, South Korea"]["unstable"])
        self.assertFalse(topics["OpenAI, Pro"]["near_repeat"])
        text = "\n".join(replay_report.render_spread_stability([day]))
        self.assertIn("Bitcoin: asked 2/2, take range 18-18 (18, 18), range >= GAP_MIN 20 in 0/2 (NEAR MISS in 2/2, "
                      "within 3 under GAP_MIN: recheck GAP_MIN 20 against the probe)", text)
        self.assertEqual(text.count("NEAR MISS"), 1)


class LoneOutlierTests(unittest.TestCase):
    """09-11 c11 q3 (§20.7 #83): OpenAI Pro reached range 27 only through one lens (8 of 9 at 31-42%, one at 58), where
    c10 asked the topic at range 19. Item (g) must not count that range as clearing GAP_MIN, and the run report names it."""
    C11 = [("q3", OPENAI, [31, 33, 35, 36, 38, 39, 40, 42, 58])]
    C10 = [("q3", OPENAI, [53, 54, 56, 58, 60, 64, 66, 70, 72])]          # 19

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_run(self.root, "2026-09-11-c11", self.C11, 1)
        write_run(self.root, "2026-09-11-c10", self.C10, 1)

    def tearDown(self):
        self.tmp.cleanup()

    def test_one_lens_range_does_not_clear(self):
        samples = [replay_report.run_sample(self.root, r) for r in ("2026-09-11-c10", "2026-09-11-c11")]
        (day,) = replay_report.spread_stability(samples, 20)
        (t,) = day["topics"]
        self.assertEqual((t["clears"], t["lone"], t["unstable"]), (0, 1, False))
        text = "\n".join(replay_report.render_spread_stability([day]))
        self.assertIn("range >= GAP_MIN 20 in 0/2 (one lens carries the range in 1: not counted)", text)

    def test_report_lists_one_lens_ranges(self):
        text, m = replay_report.report(self.root, "2026-09-11-c11", None, None)
        self.assertEqual(m["lone_outliers"], [{"question_id": "q3", "agent": AGENTS[8], "range": 27, "trimmed_range": 11}])
        self.assertIn(f"| q3 27 (11 without {AGENTS[8]}) |", text)


class UndebatedRunTests(unittest.TestCase):
    """09-11 c15 (replay on f8b74f7, debate on): a consensus day with 0 pairs, take ranges q1 16 (7 yes / 2 no), q2 11,
    q3 19 (analyst 76 against a median of 63, one under GAP_MIN 20), where c14 gave q3 34 on the same package and
    take prompt. No debate ran, so there were no gaps, moves or verdicts to check, yet the run report printed
    'FAIL: b held split' and passed (c), (d) and debate evidence on nothing, and the summary read the draw as a
    failed bar. A run that staged no pair is one take draw: its debate items are NOT COUNTED and point at the
    take-only resamples (launch_run.py --resample); the summary judges (b) on the runs that debated."""
    C15 = [("q1", BTC, [44, 46, 52, 55, 56, 57, 58, 59, 60]),                # 16
           ("q2", COWORK, [40, 41, 43, 44, 46, 47, 48, 50, 51]),             # 11
           ("q3", OPENAI, [57, 58, 60, 62, 63, 64, 65, 66, 76])]             # 19, median 63
    C14 = [("q3", OPENAI, [36, 38, 40, 44, 50, 60, 62, 66, 70])]             # 34

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_run(self.root, "2026-09-11-c15", self.C15, 0)
        pr = self.root / "2026-09-11-c15" / "phases" / "pairing.json"
        pr.write_text(json.dumps({"day_type": "consensus", "pairs": [], "target": 2, "gap_min": 20}), encoding="utf-8")
        write_run(self.root, "2026-09-11-c14", self.C14, 1)
        pos = self.root / "2026-09-11-c14" / "phases" / "positions.json"
        data = json.loads(pos.read_text(encoding="utf-8"))
        data["debates"] = [{"question_id": "q3", "high": "narrator", "low": "user_agent", "gap_before": 34,
                            "gap_after": 31, "held_split": False, "effect": "narrowed from 34 to 31"}]
        pos.write_text(json.dumps(data), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def summary(self, *runs: str) -> str:
        with contextlib.redirect_stdout(io.StringIO()):
            replay_report.main([*runs, "--root", str(self.root), "--old-dir", str(self.root)])
        return (self.root / "replay_summary.md").read_text(encoding="utf-8")

    def test_run_without_a_pair_is_not_counted(self):
        text, m = replay_report.report(self.root, "2026-09-11-c15", None, None)
        self.assertFalse(m["debated"])
        debate_items = [k for k in m["bar"] if k.startswith(("b ", "c ", "d ", "debate evidence"))]
        self.assertEqual(len(debate_items), 4)
        self.assertEqual({m["bar"][k] for k in debate_items}, {None})
        self.assertNotIn("FAIL: b held split", text)
        self.assertIn("- NOT COUNTED: b held split (two-sided, gap_after >= GAP_MIN, cruxes stated)", text)
        self.assertNotIn("PASS: c gap_after", text)
        self.assertIn("no debate ran on this draw (consensus, 0 pairs; near misses q3 19): resample the takes "
                      "twice before counting this run (scripts/launch_run.py --resample 2026-09-11-c15)", text)

    def test_summary_does_not_count_an_undebated_draw(self):
        s = self.summary("2026-09-11-c15", "2026-09-11-c15")
        self.assertNotIn("(b) a held split across the runs: FAIL", s)
        self.assertIn("(b) a held split across the runs: NOT JUDGED (no run debated; not counted: 2026-09-11-c15, "
                      "0 pairs; resample the takes or replay another package)", s)
        self.assertIn("- 2026-09-11-c15: not counted (no debate ran):", s)
        s = self.summary("2026-09-11-c14", "2026-09-11-c15")
        self.assertIn("(b) a held split across the runs: FAIL (not counted: 2026-09-11-c15, 0 pairs)", s)

    def test_debated_run_still_fails_b(self):
        text, m = replay_report.report(self.root, "2026-09-11-c14", None, None)
        self.assertTrue(m["debated"])
        self.assertIn("- FAIL: b held split", text)


class OldExportTests(unittest.TestCase):
    """The old column compares against the v1 export of the same day. Replays run on the droplet, where the exports
    are <repo>/exports/runs, but the default --old-dir was the laptop's ~/innovlabs/recon-exports/runs: the 09-11
    c13 report found nothing and printed '— calls, 0.00 M input' for a 61-call, 1.36 M-input run."""
    OLD = {"usage": {"calls": 61, "by_tier": {"analyst": {"calls": 40, "in_tok": 975048},
                                              "fast": {"calls": 19, "in_tok": 287843},
                                              "synth": {"calls": 2, "in_tok": 101678}}},
           "agents": [], "synthesis": {"final": "brief words here"}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "briefs"
        write_run(self.root, "2026-09-11-x", [("q4", OPENAI, [35, 35, 38, 43, 55, 64, 67, 68, 72])], 1)

    def tearDown(self):
        self.tmp.cleanup()

    def run_report(self, *extra):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            replay_report.main(["2026-09-11-x", "--root", str(self.root), *extra])
        return (self.root / "2026-09-11-x" / "replay_report.md").read_text(encoding="utf-8"), err.getvalue()

    def test_default_old_dir_is_the_repo_exports(self):
        runs = self.base / "repo" / "exports" / "runs"
        runs.mkdir(parents=True)
        (runs / "2026-09-11.json").write_text(json.dumps(self.OLD), encoding="utf-8")
        orig = replay_report.REPO
        replay_report.REPO = self.base / "repo"
        try:
            self.assertEqual(replay_report.default_old_dir(), runs)
            text, err = self.run_report()
        finally:
            replay_report.REPO = orig
        self.assertIn("| 61 calls, 1.36 M input |", text)
        self.assertIn(f"Old run: {runs / '2026-09-11.json'}.", text)
        self.assertEqual(err, "")

    def test_missing_old_export_is_empty_not_zero(self):
        empty = self.base / "none"
        empty.mkdir()
        text, err = self.run_report("--old-dir", str(empty))
        self.assertNotIn("0.00 M input", text)
        self.assertIn("| no old export |", text)
        self.assertIn("Old run: NO EXPORT", text)
        self.assertIn("no old export at", err)
