"""Phase D pure functions in recon/debate.py (docs/v2/phase-d-debate-upgrade.md, step 5): delphi_targets,
opposing_reasons, label_move, indicator_ok, judge_check, stage_values, xexam_score, plan_phase_d. No LLM."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from recon import debate, evidence, schemas  # noqa: E402

PKG = """# RECON INTELLIGENCE PACKAGE -- 2026-10-04
# SECTION 3: ON-CHAIN & MARKET DATA
## TOTAL DEFI TVL
- Current: $86,610,000,000
- Aave lending TVL rose to $31.2B on Base and Arbitrum this week, per DefiLlama data
# SECTION 4: NEWS INTELLIGENCE
- [coindesk.com] SEC delays decision on spot SOL ETF to November 14 https://www.coindesk.com/a/123456789
# SECTION 5: SOCIAL INTELLIGENCE
- [Oct 03 18:46] (31♥ 3🔁) DEX volume is exploding, TVL is fake https://x.com/someone/status/123456789012
# SECTION 8: PREDICTION MARKETS
- Will the Fed cut rates in October? YES: 62% NO: 38% (Polymarket, $1.2M volume)
"""


def loc():
    return evidence.Locator({"package": PKG, "raw": "", "view": "", "social": ""})


QS = [{"id": "q1", "text": "Will total DeFi TVL fall below $80B by 2026-10-11?", "kind": "threshold", "weight": 2},
      {"id": "q2", "text": "Will the SEC approve a SOL ETF by 2026-10-30?", "kind": "event", "weight": 3},
      {"id": "q3", "text": "Will X happen?", "kind": "event", "weight": 1}]
AG = ["trader", "narrator", "builder", "analyst", "skeptic", "policy_analyst", "user_agent", "macro_strategist",
      "ai_engineer"]
# q1: a wide split; q2: a split; q3: within 10 points (no split)
P = {a: {"q1": v1, "q2": v2, "q3": v3} for a, v1, v2, v3 in zip(
    AG, [10, 20, 30, 40, 50, 60, 70, 80, 90], [30, 35, 40, 45, 50, 55, 60, 65, 70], [50, 51, 52, 53, 54, 55, 56, 57, 58])}
IND = {"observable": "total DeFi TVL", "source": "DeFiLlama", "comparator": "<", "level": "80B",
       "by_date": "2026-10-10", "if_true": "yes_more_likely"}


class DelphiTests(unittest.TestCase):
    def test_targets_outside_the_iqr_endpoints_excluded(self):
        pairs = [{"question_id": "q1", "high": "ai_engineer", "low": "trader", "p_high": 90, "p_low": 10}]
        t = debate.delphi_targets(QS, P, pairs, gap_min=20)
        self.assertEqual(set(t["questions"]), {"q1", "q2"})            # q3's range is 8: no split
        q1 = t["questions"]["q1"]
        self.assertEqual((q1["q1"], q1["median"], q1["q3"]), (30.0, 50.0, 70.0))
        self.assertEqual(q1["targets"], ["macro_strategist", "narrator"])   # 20 and 80; 10 and 90 cross-examine
        self.assertEqual(t["questions"]["q2"]["targets"], ["ai_engineer", "macro_strategist", "narrator", "trader"])
        self.assertEqual(t["by_agent"]["narrator"], ["q1", "q2"])        # one call over all its questions
        self.assertEqual(len(t["by_agent"]), 4)

    def test_call_cap_keeps_the_furthest(self):
        t = debate.delphi_targets(QS, P, [], gap_min=20, max_calls=2)
        self.assertEqual(sorted(t["by_agent"]), ["ai_engineer", "trader"])   # 40 x 2 + 20 x 3 each
        self.assertEqual(len(t["dropped"]), 2)

    def test_lone_outlier_is_no_split_but_consensus_day_takes_the_widest(self):
        p = {a: {"q1": 50 + k, "q2": 40 + k} for k, a in enumerate(AG)}
        p["skeptic"]["q1"] = 95                                               # one lens alone: no split
        self.assertEqual(debate.delphi_targets(QS[:2], p, [], gap_min=20)["by_agent"], {})
        c = debate.delphi_targets(QS[:2], p, [], gap_min=20, consensus=True)
        self.assertEqual(list(c["questions"]), ["q1"])
        self.assertLessEqual(len(c["by_agent"]), debate.DELPHI_CONSENSUS_CALLS)
        self.assertIn("skeptic", c["by_agent"])

    def test_opposing_reasons_by_verified_quotes(self):
        ev = {"status": "verified", "cls": "data", "section": "S", "quote": "Current: $86,610,000,000"}
        evq = {a: {"q1": []} for a in AG}
        evq["analyst"]["q1"] = [ev, dict(ev, quote="Aave lending TVL rose")]
        evq["builder"]["q1"] = [ev]
        takes = {a: {"positions": [{"question_id": "q1", "reason": f"{a} reason"}]} for a in AG}
        r = debate.opposing_reasons("q1", P, evq, "ai_engineer", takes)
        self.assertEqual([x["agent"] for x in r], ["analyst", "builder"])   # most verified quotes, below the median
        self.assertEqual(r[0]["reason"], "analyst reason")
        self.assertEqual(len(r[0]["evidence"]), 2)
        r = debate.opposing_reasons("q1", P, {}, "trader", takes)
        self.assertEqual([x["agent"] for x in r], ["ai_engineer", "macro_strategist"])   # no quotes: furthest out
        self.assertEqual(debate.opposing_reasons("q1", P, evq, "skeptic", takes), [])  # at the median: no side

    def test_label_move(self):
        v = [{"status": "verified", "cls": "data"}]
        self.assertEqual(debate.label_move(60, 60, v), "held")
        self.assertEqual(debate.label_move(60, 45, v), "verified quote")
        self.assertEqual(debate.label_move(60, 45, [{"status": "partial", "cls": "data"}]), "argument")
        self.assertEqual(debate.label_move(60, 45, [{"status": "verified", "cls": "social"}]), "argument")


class IndicatorTests(unittest.TestCase):
    def test_indicator_ok(self):
        self.assertEqual(debate.indicator_ok(IND, "2026-10-04", "2026-10-11"), (True, []))
        for k, v, frag in (("comparator", "about", "comparator"), ("level", "", "without a level"),
                           ("by_date", "next week", "not a date"), ("by_date", "2026-10-01", "before"),
                           ("by_date", "2026-10-20", "after the question resolves"), ("if_true", "", "if_true"),
                           ("observable", "", "observable")):
            ok, why = debate.indicator_ok(dict(IND, **{k: v}), "2026-10-04", "2026-10-11")
            self.assertFalse(ok, k)
            self.assertTrue(any(frag in w for w in why), (k, why))
        ev = dict(IND, comparator="happens", level="", observable="SEC approval order for a spot SOL ETF")
        self.assertTrue(debate.indicator_ok(ev, "2026-10-04", "2026-10-30")[0])
        self.assertFalse(debate.indicator_ok(dict(IND, by_date="2026-12-31"), "2026-10-04", "")[0])   # judgment: 60 days
        self.assertEqual(debate.indicator_text(IND), "total DeFi TVL (DeFiLlama) < 80B by 2026-10-10")

    def test_judge_check(self):
        item = {"question_id": "q1", "indicator": IND, "status": "agreed", "resolvable": True, "why_not": "",
                "data_today": "favours_high", "quote": "- Current: $86,610,000,000", "section": "ON-CHAIN",
                "watch_line": "w", "split_line": "s"}
        ok = debate.judge_check(item, loc(), "2026-10-04", "2026-10-11")
        self.assertEqual((ok["data_today"], ok["resolvable"], ok["flags"]), ("favours_high", True, []))
        for quote, flag in (("Current TVL is $99,000,000,000 and rising", "not found"),
                            ("DEX volume is exploding, TVL is fake https://x.com/someone/status/123456789012", "social"),
                            ("Will the Fed cut rates in October? YES: 62% NO: 38%", "odds")):
            bad = debate.judge_check(dict(item, quote=quote), loc(), "2026-10-04", "2026-10-11")
            self.assertEqual(bad["data_today"], "no_data", quote)
            self.assertTrue(any(flag in f for f in bad["flags"]), (quote, bad["flags"]))
        nq = debate.judge_check(dict(item, quote=""), loc(), "2026-10-04", "2026-10-11")
        self.assertEqual(nq["data_today"], "no_data")
        late = debate.judge_check(dict(item, indicator=dict(IND, by_date="2026-11-30")), loc(), "2026-10-04", "2026-10-11")
        self.assertFalse(late["resolvable"])                      # the judge said resolvable; the date says no
        self.assertTrue(late["judge_resolvable"])


class XexamTests(unittest.TestCase):
    def test_stage_values(self):
        after, fin = debate.stage_values({"a": {"q1": 10, "q2": 20}, "b": {"q1": 90}}, {"a": {"q2": 35}}, {"b": {"q1": 70}})
        self.assertEqual(after, {"a": {"q1": 10, "q2": 35}, "b": {"q1": 90}})
        self.assertEqual(fin, {"a": {"q1": 10, "q2": 35}, "b": {"q1": 70}})

    def test_xexam_score(self):
        pr = {"question_id": "q1", "high": "ai_engineer", "low": "trader", "p_high": 90, "p_low": 10, "gap": 80}
        ev_v = {"status": "verified", "cls": "data"}
        opens = {"high": {"data": {"i_am_wrong_if": IND}, "evidence": [ev_v, {"status": "unverified", "cls": ""}]},
                 "low": {"data": {"i_am_wrong_if": dict(IND, level="95B")}, "evidence": [{"status": "partial", "cls": "data"}]}}
        replies = {"high": {"data": {"on_their_indicator": {"verdict": "accept", "counter": {}}},
                            "move": {"take": 90, "new": 75, "label": "argument"}, "evidence": []},
                   "low": {"data": {"on_their_indicator": {"verdict": "counter", "counter": IND}},
                           "move": {"take": 10, "new": 10, "label": "held"}, "evidence": [ev_v]}}
        sc = debate.xexam_score(pr, opens, replies, None, gap_min=20)
        self.assertEqual((sc["status"], sc["gap_before"], sc["gap_after"], sc["held_split"]), ("two-sided", 80, 65, True))
        self.assertEqual(sc["evidence"]["high"], {"claimed": 2, "verified": 1, "partial": 0, "unverified": 1, "data": 1,
                                                  "quality": 0.5})
        self.assertEqual(sc["evidence"]["low"]["quality"], 0.75)
        self.assertEqual(sc["indicators"]["verdicts"], {"high": "accept", "low": "counter"})
        self.assertEqual(sc["indicators"]["counters"], {"high": None, "low": IND})
        schemas.validate(json.loads(json.dumps(sc)), schemas.ARTIFACTS["xexam_score"])
        crossed = debate.xexam_score(pr, opens, {"high": dict(replies["high"], move={"take": 90, "new": 5, "label": "argument"}),
                                                 "low": replies["low"]}, None, 20)
        self.assertEqual((crossed["gap_after"], crossed["held_split"]), (-5, False))   # recorded, not prevented
        failed = debate.xexam_score(pr, opens, {}, None, 20)
        self.assertEqual((failed["status"], failed["gap_after"], failed["held_split"]), ("failed", None, False))


class BudgetTests(unittest.TestCase):
    def test_split_day_fits_29(self):
        b = debate.plan_phase_d(used=11, budget=30, synth_calls=2, n_pairs=2, delphi_calls=6, judge=True, premortem=True)
        self.assertEqual((b["pairs"], b["delphi"], b["judge"], b["premortem"], b["planned"]), (2, 6, True, True, 16))
        self.assertEqual(11 + b["planned"] + 2, 29)

    def test_consensus_day_17(self):
        b = debate.plan_phase_d(used=11, budget=30, synth_calls=2, n_pairs=0, delphi_calls=3, judge=False, premortem=True)
        self.assertEqual(11 + b["planned"] + 2, 17)

    def test_tight_budget_gives_up_the_second_pair_first(self):
        b = debate.plan_phase_d(used=11, budget=24, synth_calls=2, n_pairs=2, delphi_calls=6, judge=True, premortem=True)
        self.assertEqual((b["pairs"], b["judge"], b["delphi"], b["premortem"]), (1, True, 6, False))
        b = debate.plan_phase_d(used=11, budget=15, synth_calls=2, n_pairs=2, delphi_calls=6, judge=True, premortem=True)
        self.assertEqual((b["pairs"], b["planned"]), (0, 2))     # no pair fits: two Delphi calls
        self.assertFalse(b["judge"] and not b["delphi"])


class SplitSheetDTests(unittest.TestCase):
    """Step 7: counts from the take values, crux / settles on / Watch from the judge, the pre-mortem's early signs."""
    VALS = dict(zip(AG, [10, 15, 20, 25, 30, 35, 80, 85, 90]))

    def setup(self, vals):
        q = {"id": "q1", "text": "Will total DeFi TVL fall below $80B by 2026-10-11?", "kind": "threshold", "weight": 3,
             "resolves_on": "2026-10-11", "settles_with": "DeFiLlama total TVL", "ledger_id": "2026-10-04-q1",
             "carried_from": ""}
        ev = [{"section": "ON-CHAIN", "quote": "- Current: $86,610,000,000"}]
        takes = {a: {"positions": [{"question_id": "q1", "probability": v, "reason": f"reason at {v}", "evidence": ev}]}
                 for a, v in vals.items()}
        return [q], {a: {"q1": v} for a, v in vals.items()}, takes

    def test_debated_block_counts_takes_and_takes_the_judge_lines(self):
        qs, take_p, takes = self.setup(self.VALS)
        score = {"question_id": "q1", "high": "ai_engineer", "low": "trader", "status": "two-sided", "gap_before": 80,
                 "gap_after": 45, "held_split": True, "evidence": {}, "indicators": {}, "judge": None,
                 "moves": [{"agent": "ai_engineer", "take": 90, "new": 55, "label": "argument"},
                           {"agent": "trader", "take": 10, "new": 10, "label": "held"}]}
        opens = [{"agent": "trader", "question_id": "q1", "data": {"rebuttal": "TVL holds above the line on lending growth.",
                                                                     "steelman": "s", "evidence": [], "i_am_wrong_if": IND},
                  "checks": {"flags": []}},
                 {"agent": "ai_engineer", "question_id": "q1", "data": {"rebuttal": "Outflows break it.", "steelman": "s2",
                                                                         "evidence": [], "i_am_wrong_if": IND},
                  "checks": {"flags": []}}]
        item = debate.judge_check({"question_id": "q1", "indicator": IND, "status": "agreed", "resolvable": True, "why_not": "",
                                   "data_today": "favours_low", "quote": "- Current: $86,610,000,000", "section": "ON-CHAIN",
                                   "watch_line": "Watch total DeFi TVL against $80B by October 10.",
                                   "split_line": "Whether lending growth offsets outflows enough to keep TVL above $80B."},
                                  loc(), "2026-10-04", "2026-10-11")
        sheet = debate.split_sheet_d("2026-10-04", "r", "debate", qs, take_p, [score], opens, [item], None, takes, loc(), 20)
        schemas.validate(json.loads(json.dumps(sheet)), schemas.ARTIFACTS["split_sheet"])
        bl = sheet["blocks"][0]
        self.assertTrue(bl["debated"])
        self.assertEqual((bl["counts"]["range"], bl["counts"]["majority"], bl["counts"]["minority"]), ([10, 90], 6, 3))
        self.assertEqual(bl["crux"], "Whether lending growth offsets outflows enough to keep TVL above $80B.")
        self.assertEqual(bl["settles_on"], {"observable": "total DeFi TVL (DeFiLlama) < 80B", "by_date": "2026-10-10"})
        self.assertEqual(bl["watch"], ["Watch total DeFi TVL against $80B by October 10."])
        self.assertIn("base case", bl["crux_check"]["what_the_data_says"])   # the low view sits with the 6 below 50
        text = debate.render_split_sheet(sheet)
        self.assertIn("Watch: Watch total DeFi TVL", text)
        self.assertIn("Count phrase (copy exactly): 6 of 9 lenses put it at 10–35%; 3 put it at 80–90%", text)
        # an unresolvable judge item changes nothing: the question's own settles_with and date stay
        bad = dict(item, resolvable=False)
        bl2 = debate.split_sheet_d("2026-10-04", "r", "debate", qs, take_p, [score], opens, [bad], None, takes, loc(),
                                   20)["blocks"][0]
        self.assertEqual((bl2["settles_on"], bl2["watch"], bl2["crux"]),
                         ({"observable": "DeFiLlama total TVL", "by_date": "2026-10-11"}, [], ""))

    def test_consensus_block_is_the_premortem(self):
        qs, take_p, takes = self.setup({a: 60 + k for k, a in enumerate(AG)})
        pm = {"type": "premortem", "agent": "skeptic", "question_id": "q1", "median": 64,
              "data": {"consensus_view": "TVL holds", "failure_story": "A large unwind drained lending pools.",
                       "early_signs": [], "evidence": [], "probability": 30},
              "anon": {"consensus_view": "TVL holds", "failure_story": "A large unwind drained lending pools.",
                       "early_signs": [], "evidence": [], "probability": 30},
              "early_signs": [{"indicator": IND, "ok": True, "problems": []},
                              {"indicator": dict(IND, by_date="2026-12-01"), "ok": False, "problems": ["late"]}]}
        sheet = debate.split_sheet_d("2026-10-04", "r", "consensus", qs, take_p, [], [], [], pm, takes, loc(), 20)
        bl = sheet["blocks"][0]
        self.assertEqual(bl["type"], "consensus")
        self.assertEqual(bl["minority_case"]["source"], "red_team")
        self.assertIn("unwind", bl["minority_case"]["text"])
        self.assertEqual(bl["crux"], "total DeFi TVL (DeFiLlama) < 80B by 2026-10-10")
        self.assertEqual(bl["watch"], ["total DeFi TVL (DeFiLlama) < 80B by 2026-10-10"])     # the late sign is left out
        schemas.validate(json.loads(json.dumps(sheet)), schemas.ARTIFACTS["split_sheet"])


class ModeTests(unittest.TestCase):
    def test_debate_modes_and_budget(self):
        import argparse
        import os
        from recon.orchestrator import Run
        keep = {k: os.environ.pop(k, None) for k in ("RECON_DEBATE", "RECON_CALL_BUDGET")}

        def run(run_id=None, as_of=None):
            return Run(argparse.Namespace(as_of=as_of, dry_run=False, replay=None, run_id=run_id, state_dir=None,
                                          from_phase=None, resume=False))
        try:
            daily = run()
            self.assertEqual((daily.debate_mode, daily.budget), ("off", 24))      # cron: RECON_DEBATE unset/0
            tagged = run("2026-10-04-c3", "2026-10-04")
            self.assertEqual((tagged.debate_mode, tagged.phase_d, tagged.budget), ("d", True, 30))
            os.environ["RECON_DEBATE"] = "c"
            self.assertEqual((run("2026-10-04-c3", "2026-10-04").debate_mode, run().debate_mode), ("c", "c"))
            os.environ["RECON_DEBATE"] = "1"
            self.assertEqual(run().debate_mode, "d")
            os.environ["RECON_DEBATE"] = "0"
            self.assertEqual(run("2026-10-04-c3", "2026-10-04").debate_mode, "off")
        finally:
            for k, v in keep.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
