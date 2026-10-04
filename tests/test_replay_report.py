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
        self.assertEqual((openai["asked"], openai["clears"], openai["unstable"]), (4, 4, False))
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
        self.assertIn("OpenAI, Pro: asked 4/4, take range 26-37 (37, 35, 34, 26), range >= GAP_MIN 20 in 4/4", text)


if __name__ == "__main__":
    unittest.main()
