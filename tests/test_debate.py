"""Phase C unit tests for recon/debate.py (docs/v2/phase-c-spec.md §17.1). Pure functions, no network,
no LLM. Run: python3 -m unittest discover -s tests (desktop and droplet)."""
from __future__ import annotations

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO), str(REPO / "tests"), str(REPO / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from recon import debate, evidence, schemas  # noqa: E402
import lens_extras_probe as probe  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "package"
DAYS = ("2026-09-11", "2026-10-04")
AG = debate.AGENTS


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")


PACKAGE = """# RECON INTELLIGENCE PACKAGE -- 2026-10-04
# SECTION 3: ON-CHAIN & MARKET DATA
## TOTAL DEFI TVL
- Current: $86,610,000,000
- Total 24h DEX volume: $11,121,712,895
- Uniswap V3: 24h $1,234,567,890 | 7d $8,100,000,000
- Aave lending TVL rose to $31.2B on Base and Arbitrum this week, per DefiLlama data
- Ethena USDe supply fell 4.1% to $5.3B over seven days on redemptions
- Hyperliquid open interest reached $9.8B, a record, as BTC perps funding turned negative
# SECTION 4: NEWS INTELLIGENCE
- [coindesk.com] SEC delays decision on spot SOL ETF to November 14 https://www.coindesk.com/a/123456789
# SECTION 5: SOCIAL INTELLIGENCE
- [Oct 03 18:46] (31♥ 3🔁) DEX volume is exploding, TVL is fake https://x.com/someone/status/123456789012
"""
VIEW = """# SECTION: SOCIAL
- @whale [Oct 03 18:46] (31♥ 3🔁) Stablecoin flows into Base hit 1.2B this week, biggest since March
"""


def locator(extra_pkg: str = "") -> evidence.Locator:
    return evidence.Locator({"package": PACKAGE + extra_pkg, "raw": "", "view": VIEW, "social": ""})


def q(text, kind="threshold", resolves="2026-10-11", bq="- Current: $86,610,000,000", weight=2, carried="",
      domain="markets_crypto", lenses=None, settled=""):
    return {"id": "", "text": text, "kind": kind, "domain": domain, "metric": "", "comparator": "", "threshold": "",
            "baseline_quote": bq, "settled_quote": settled, "resolves_on": resolves, "settles_with": "DeFiLlama",
            "lenses": lenses or [], "weight": weight, "carried_from": carried}


class GateTests(unittest.TestCase):
    def test_drops_with_reasons_and_renumbers(self):
        qs = [q("Will total DeFi TVL stay above 86B by 10-11"),                         # no ?
              q("How much will TVL be on 10-11?"),                                      # wh-question
              q("Will TVL exceed 90B by mid November?", resolves="2026-11-18"),        # 45 days out
              q("Is the AI trade crowded this week?", kind="judgment", bq="", resolves="", weight=3),
              q("Will crypto sentiment sour before the FOMC?", kind="judgment", bq="", resolves="", weight=1),
              q("Will SOL TVL top 10B by 10-11?", bq="- SOL TVL: $99,999,999,999"),    # unverifiable baseline
              q("Will total DeFi TVL stay above 86B through 10-11?"),                  # kept
              q("Will total DeFi TVL stay above 86B through 10-12?"),                  # Jaccard repeat
              q("Will Aave TVL hold 31B by 10-11?", carried="2026-10-01-q1", bq="- Aave lending TVL rose to $31.2B on Base and Arbitrum this week, per DefiLlama data"),
              q("Will USDe supply fall again by 10-11?", carried="2026-10-01-q2", kind="event", bq="", weight=1),
              q("Will Hyperliquid OI pass 10B by 10-11?", carried="2026-10-01-q3", kind="event", bq="", weight=1)]
        res = debate.gate_questions(qs, "2026-10-04", locator(), depth="risk")
        reasons = {d["text"]: d["reason"] for d in res["dropped"]}
        self.assertIn("ending with ?", reasons["Will total DeFi TVL stay above 86B by 10-11"])
        self.assertIn("ending with ?", reasons["How much will TVL be on 10-11?"])
        self.assertIn("not 1-30 days", reasons["Will TVL exceed 90B by mid November?"])
        self.assertIn("second judgment", reasons["Will crypto sentiment sour before the FOMC?"])
        self.assertIn("baseline_quote", reasons["Will SOL TVL top 10B by 10-11?"])
        self.assertTrue(any("repeats" in d["reason"] for d in res["dropped"]))
        self.assertTrue(any("more than 2 carried" in d["reason"] for d in res["dropped"]))
        self.assertEqual([x["id"] for x in res["kept"]], [f"q{i}" for i in range(1, len(res["kept"]) + 1)])
        self.assertEqual(sum(1 for x in res["kept"] if x["carried_from"]), 2)

    def test_needs_reask(self):
        one = debate.gate_questions([q("Will total DeFi TVL stay above 86B through 10-11?")], "2026-10-04", locator())
        two = debate.gate_questions([q("Will total DeFi TVL stay above 86B through 10-11?"),
                                     q("Is the AI trade crowded this week?", kind="judgment", bq="", resolves="")],
                                    "2026-10-04", locator())
        self.assertTrue(one["needs_reask"])
        self.assertFalse(two["needs_reask"])

    def test_weight_clamped(self):
        qs = [q("Will total DeFi TVL stay above 86B through 10-11?", weight=0),
              q("Is the AI trade crowded this week?", kind="judgment", bq="", resolves="", weight=9),
              q("Will the SEC decide on the spot SOL ETF by 10-30?", kind="event", bq="", resolves="2026-10-30", weight=2.6)]
        res = debate.gate_questions(qs, "2026-10-04", locator(), depth="risk")
        self.assertEqual([x["weight"] for x in res["kept"]], [1, 3, 3])
        self.assertEqual(sum(1 for n in res["notes"] if n.startswith("weight clamped")), 3)

    def test_baseline_in_view_only(self):
        bq = "- @whale [Oct 03 18:46] (31♥ 3🔁) Stablecoin flows into Base hit 1.2B this week, biggest since March"
        res = debate.gate_questions([q("Will Base stablecoin flows stay above 1B by 10-11?", kind="direction", bq=bq)],
                                    "2026-10-04", locator())
        self.assertEqual(len(res["kept"]), 1)


class LensExtrasTests(unittest.TestCase):
    def test_table_matches_probe(self):
        self.assertEqual(debate.LENS_RAW, probe.LENS_RAW)
        self.assertIn("## CROSS-SOURCE SIGNALS", debate.LENS_RAW["analyst"])

    def test_fixture_days(self):
        for day in DAYS:
            run = FIX / day
            raw, pkg, view = (read(run / f) for f in ("00_raw_data.md", "00_data_package.md", "01_filtered.md"))
            got = debate.lens_extras(raw, pkg, view)
            ref = probe.measure(run)["agents"]
            v = probe.View(view)
            seen: dict[str, str] = {}
            for a in debate.LENS_RAW:
                with self.subTest(day=day, agent=a):
                    self.assertGreater(got[a]["bytes"], 0)
                    self.assertLessEqual(got[a]["bytes"], debate.AGENT_CAP)
                    self.assertEqual(got[a]["text"], ref[a]["text"])
                    for line in got[a]["text"].split("\n"):
                        if line.strip() and not line.startswith("#"):
                            self.assertFalse(v.has(line), line[:80])
                            self.assertNotIn(line.strip(), seen, f"also given to {seen.get(line.strip())}")
                            seen[line.strip()] = a
            self.assertTrue(got["macro_strategist"]["text"].startswith("# World Monitor Intelligence"))


class LocateAndNormTests(unittest.TestCase):
    def test_locate_view_social_and_partial(self):
        loc = locator()
        r = loc.locate("Stablecoin flows into Base hit 1.2B this week, biggest since March")
        self.assertEqual((r["status"], r["cls"]), ("verified", "social"))
        p = loc.locate("Aave lending TVL rose to $31.2B on Base and Arbitrum this week according to DefiLlama data")
        self.assertEqual(p["status"], "partial")
        self.assertIn("Aave lending TVL", loc.line_text(p["doc"], p["line"]))

    def test_norm_p(self):
        self.assertEqual(debate.norm_p(0.65, [30, 80]), (65, ["fraction rescaled"]))
        self.assertEqual(debate.norm_p(0.65, [0.4, 1]), (1, []))
        self.assertEqual(debate.norm_p(104, [50]), (100, ["clamped"]))
        # a {qid: int} dict from take_values: its values are the references, not its keys
        self.assertEqual(debate.norm_p(0.6, {"q1": 30, "q2": 70}), (60, ["fraction rescaled"]))

    def test_take_values_mixed_scales(self):
        t = {"positions": [{"question_id": "q1", "probability": 0.65}, {"question_id": "q2", "probability": 70},
                           {"question_id": "q3", "probability": 1}]}
        self.assertEqual(debate.take_values(t), {"q1": 65, "q2": 70, "q3": 1})
        allf = {"positions": [{"question_id": "q1", "probability": 0.65}, {"question_id": "q2", "probability": 1}]}
        self.assertEqual(debate.take_values(allf), {"q1": 65, "q2": 100})


# ── pairing ──────────────────────────────────────────────────────────────────────────────

def ev(cls="data", status="verified", quote="x"):
    return [{"section": "", "quote": quote, "status": status, "cls": cls, "doc": "package", "line": 1}]


def setup(values: dict, kinds=None, weights=None, evq=None, lenses=None):
    """values: {qid: {agent: p}} -> (questions, p, evq)."""
    qs, p = [], {}
    for qid, vals in values.items():
        qs.append({"id": qid, "text": qid, "kind": (kinds or {}).get(qid, "threshold"),
                   "weight": (weights or {}).get(qid, 2), "lenses": (lenses or {}).get(qid, [])})
        for a, v in vals.items():
            p.setdefault(a, {})[qid] = v
    e = evq or {a: {qid: ev(quote=f"{a}-{qid}") for qid in p[a]} for a in p}
    return qs, p, e


class PairingTests(unittest.TestCase):
    def test_one_pair_on_the_split(self):
        qs, p, e = setup({"q1": {"trader": 20, "analyst": 30, "skeptic": 70, "builder": 80},
                          "q2": {"trader": 45, "analyst": 50, "skeptic": 55, "builder": 52}})
        r = debate.pair(qs, p, e, list(p), "normal", 3)
        self.assertEqual([(x["question_id"], x["low"], x["high"]) for x in r["pairs"]], [("q1", "trader", "builder")])

    def test_three_pairs_six_agents(self):
        vals = {}
        for i, qid in enumerate(("q1", "q2", "q3")):
            vals[qid] = {a: 50 + ((k * 7 + i * 13) % 9 - 4) * 10 for k, a in enumerate(AG)}
        qs, p, e = setup(vals)
        r = debate.pair(qs, p, e, AG, "normal", 3)
        self.assertEqual(len(r["pairs"]), 3)
        ends = [x["high"] for x in r["pairs"]] + [x["low"] for x in r["pairs"]]
        self.assertEqual(len(set(ends)), 6)

    def test_budget_pairs(self):
        # the last pair goes before the crux check (decision 2026-10-04): a clean normal day is 2 pairs + crux check
        self.assertEqual(debate.budget_pairs(10, 24, 2, 3), (2, True))    # 12 free
        self.assertEqual(debate.budget_pairs(11, 24, 2, 3), (2, True))    # 11 free
        self.assertEqual(debate.budget_pairs(9, 24, 2, 3), (3, True))     # 13 free
        self.assertEqual(debate.budget_pairs(16, 24, 2, 3), (1, True))    # 6 free
        self.assertEqual(debate.budget_pairs(18, 24, 2, 3), (1, False))   # 4 free: no pair fits with it
        self.assertEqual(debate.budget_pairs(19, 24, 2, 3), (0, False))   # 3 free
        self.assertEqual(debate.budget_pairs(10, 24, 2, 1), (1, True))    # quiet day

    def test_budget_target_two(self):
        vals = {qid: {a: v for a, v in zip(AG, [10, 20, 30, 40, 50, 60, 70, 80, 90 - i * 5])}
                for i, qid in enumerate(("q1", "q2", "q3"))}
        qs, p, e = setup(vals, weights={"q1": 3, "q2": 2, "q3": 1})
        full = debate.pair(qs, p, e, AG, "normal", 3)
        two = debate.pair(qs, p, e, AG, "normal", 2)
        self.assertEqual(len(two["pairs"]), 2)
        self.assertEqual(two["pairs"], full["pairs"][:2])

    def test_cap_two_second_pass(self):
        vals = {qid: {"trader": 10, "skeptic": 90, "analyst": 50, "builder": 52, "narrator": 48}
                for qid in ("q1", "q2", "q3")}
        e = {a: {qid: (ev() if a in ("trader", "skeptic") else ev(status="unverified")) for qid in vals}
             for a in vals["q1"]}
        qs, p, _ = setup(vals)
        r = debate.pair(qs, p, e, list(p), "normal", 3)
        self.assertEqual(len(r["pairs"]), 2)        # cap 2: no agent in three debates

    def test_yesterday_pair_loses_tie(self):
        qs, p, e = setup({"q1": {"trader": 20, "analyst": 20, "skeptic": 80, "builder": 50}})
        r = debate.pair(qs, p, e, list(p), "normal", 1, yesterday_pairs={frozenset(("analyst", "skeptic"))})
        self.assertEqual((r["pairs"][0]["low"], r["pairs"][0]["high"]), ("trader", "skeptic"))

    def test_unverified_extreme_not_endpoint(self):
        vals = {"q1": {"trader": 5, "analyst": 20, "skeptic": 80, "builder": 50}}
        qs, p, e = setup(vals)
        e["trader"]["q1"] = ev(status="unverified")
        r = debate.pair(qs, p, e, list(p), "normal", 1)
        self.assertEqual(r["pairs"][0]["low"], "analyst")

    def test_social_only_endpoint(self):
        for kind, expect in (("threshold", "analyst"), ("judgment", "trader")):
            vals = {"q1": {"trader": 5, "analyst": 20, "skeptic": 80, "builder": 50}}
            qs, p, e = setup(vals, kinds={"q1": kind})
            e["trader"]["q1"] = ev(cls="social")
            r = debate.pair(qs, p, e, list(p), "normal", 1)
            with self.subTest(kind=kind):
                self.assertEqual(r["pairs"][0]["low"], expect)

    def test_weight_ten_is_three(self):
        qs, p, e = setup({"q1": {"trader": 20, "analyst": 50, "skeptic": 80}}, weights={"q1": 10})
        r = debate.pair(qs, p, e, list(p), "normal", 1)
        self.assertEqual(r["pairs"][0]["score"], 60 * 3)

    def test_free_move_margin_ranks_first(self):
        # 09-11 q3 (Hormuz): a 22-point pair fell to 13 on two free moves (49 -> 45 on no evidence, 27 -> 32), under
        # GAP_MIN, and the only held split was lost. A pair under GAP_MIN + 2 x FREE_MOVE ranks after one with that
        # margin, even when its weight gives it the higher score (66 vs 40); with a free slot it is still debated.
        qs, p, e = setup({"q1": {"trader": 27, "analyst": 40, "macro_strategist": 49},
                          "q2": {"builder": 20, "narrator": 45, "skeptic": 60}}, weights={"q1": 3, "q2": 1})
        one = debate.pair(qs, p, e, list(p), "normal", 1)
        self.assertEqual([(x["question_id"], x["gap"]) for x in one["pairs"]], [("q2", 40)])
        two = debate.pair(qs, p, e, list(p), "normal", 2)
        self.assertEqual([x["question_id"] for x in two["pairs"]], ["q2", "q1"])
        alone = debate.pair([q for q in qs if q["id"] == "q1"], p, e, list(p), "normal", 1)
        self.assertEqual([(x["question_id"], x["gap"]) for x in alone["pairs"]], [("q1", 22)])

    def test_same_side_no_pair(self):
        qs, p, e = setup({"q1": {"trader": 10, "analyst": 50, "skeptic": 55, "builder": 60}})
        e["skeptic"]["q1"] = ev(status="unverified")
        e["builder"]["q1"] = ev(status="unverified")
        r = debate.pair(qs, p, e, list(p), "normal", 1, gap_min=60)
        self.assertEqual(r["pairs"], [])

    def test_split_unpaired(self):
        qs, p, e = setup({"q1": {"trader": 10, "analyst": 50, "skeptic": 90}})
        for a in p:
            e[a]["q1"] = ev(status="unverified")
        r = debate.pair(qs, p, e, list(p), "normal", 3)
        self.assertEqual(r["day_type"], "split_unpaired")
        self.assertIsNone(r["red_team"])
        self.assertEqual(r["unpaired"][0]["question_id"], "q1")

    def test_consensus_red_team(self):
        qs, p, e = setup({"q1": {"trader": 60, "analyst": 62, "skeptic": 70, "builder": 58, "narrator": 61}})
        r = debate.pair(qs, p, e, list(p), "normal", 3)
        self.assertEqual(r["day_type"], "consensus")
        self.assertEqual(r["red_team"]["agent"], "skeptic")       # 9 from the median: the skeptic fallback
        qs, p, e = setup({"q1": {"trader": 60, "analyst": 62, "skeptic": 63, "builder": 58, "narrator": 72}})
        r = debate.pair(qs, p, e, list(p), "normal", 3)
        self.assertEqual(r["red_team"]["agent"], "narrator")      # 11 from the median

    def test_deterministic(self):
        vals = {qid: {a: (k * 37 + i * 11) % 100 for k, a in enumerate(AG)} for i, qid in enumerate(("q1", "q2", "q3"))}
        qs, p, e = setup(vals)
        r1 = debate.pair(qs, p, e, list(AG), "normal", 3)
        ag = list(AG)
        random.Random(4).shuffle(ag)
        qs2 = list(reversed(qs))
        p2 = {a: p[a] for a in ag}
        r2 = debate.pair(qs2, p2, e, ag, "normal", 3)
        self.assertEqual(json.dumps(r1, sort_keys=True), json.dumps(r2, sort_keys=True))


# ── the evidence gate ────────────────────────────────────────────────────────────────────

CRUX = "Total 24h DEX volume stays above $11.1B and Uniswap V3 keeps $1.23B a day"
HIT_A = "- Total 24h DEX volume: $11,121,712,895"
HIT_B = "- Uniswap V3: 24h $1,234,567,890 | 7d $8,100,000,000"


class GateMoveTests(unittest.TestCase):
    def setUp(self):
        self.loc = locator()
        self.terms = debate.crux_terms([CRUX])
        self.hits = [{"doc": "package", "line": 5, "text": HIT_A}, {"doc": "package", "line": 6, "text": HIT_B}]

    def gm(self, requested, new_ev, verdict="narrow", take=40, other=80, own=None, oth=None):
        return debate.gate_move("trader", take, requested, {"q1": take, "q2": 55}, other, verdict, new_ev,
                                own or [], oth or [], self.hits, self.terms, self.loc)

    def test_no_evidence(self):
        m = self.gm(65, [])
        self.assertEqual(m["gated"], 45)
        self.assertIn("update without evidence, capped", m["flags"])

    def test_challenger_quote_is_argument(self):
        m = self.gm(65, [{"section": "", "quote": HIT_A}], oth=[HIT_A])
        self.assertEqual(m["gated"], 45)
        self.assertIn("argument only, capped", m["flags"])
        self.assertEqual(m["new_evidence"][0]["new_evidence_source"], "challenger")

    def test_crux_quote_per_line(self):
        one = self.gm(65, [{"section": "", "quote": HIT_A}])
        self.assertEqual(one["gated"], 55)                 # 5 free + 10 for one new line
        self.assertEqual(one["evidence_source"], "crux_data")
        self.assertIn("evidence move capped at 15", one["flags"])
        two = self.gm(65, [{"section": "", "quote": HIT_A}, {"section": "", "quote": HIT_B}])
        self.assertEqual(two["gated"], 65)                 # 5 + 2 x 10 = 25: the full move
        self.assertNotIn("evidence move capped at 25", two["flags"])

    def test_partial_does_not_qualify(self):
        m = self.gm(65, [{"section": "", "quote": "Total 24h DEX volume: $11,121,712,895 ... Uniswap V3: 24h $1,234,567,890"}])
        self.assertEqual(m["gated"], 45)

    def test_no_crux_term(self):
        m = self.gm(65, [{"section": "", "quote": "- Ethena USDe supply fell 4.1% to $5.3B over seven days on redemptions"}])
        self.assertEqual(m["gated"], 45)
        self.assertFalse(m["new_evidence"][0]["qualifies"])

    def test_social_only(self):
        m = self.gm(65, [{"section": "", "quote": "Stablecoin flows into Base hit 1.2B this week, biggest since March"}])
        self.assertEqual(m["gated"], 45)
        self.assertIn("social evidence only, capped", m["flags"])

    def test_small_move_and_verbal_concession(self):
        m = self.gm(36, [], verdict="hold")
        self.assertEqual((m["gated"], m["flags"]), (36, []))
        c = self.gm(43, [], verdict="concede")
        self.assertIn("verbal concession", c["flags"])

    def test_hold_but_moved(self):
        m = self.gm(48, [], verdict="hold")
        self.assertEqual(m["gated"], 45)
        self.assertIn("hold but moved", m["flags"])
        # the free move is not a soft move (§15.5 d: more than FREE_MOVE); the request beyond it is recorded
        self.assertNotIn("soft move", m["flags"])
        self.assertIn("soft request", m["flags"])

    def test_shared_line_splits_the_allowance(self):
        alone = self.gm(65, [{"section": "", "quote": HIT_A}])
        self.assertEqual(alone["gated"], 55)
        shared = debate.gate_move("trader", 40, 65, {"q1": 40}, 80, "narrow", [{"section": "", "quote": HIT_A}], [], [],
                                  self.hits, self.terms, self.loc, shared_lines={debate.line_key(self.loc, "package", 5)})
        self.assertEqual(shared["gated"], 50)             # 5 free + half of the shared line's 10
        self.assertIn("shared evidence line, allowance split", shared["flags"])
        lines = debate.qualifying_lines(alone, [{"section": "", "quote": HIT_A}], self.loc)
        self.assertEqual(lines, {debate.line_key(self.loc, "package", 5)})
        # both responders cite the one crux line: a split of 40 closes by at most 5 + 5 + 10 = 20
        hi = debate.gate_move("skeptic", 80, 40, {"q1": 80}, 40, "concede", [{"section": "", "quote": HIT_A}], [], [],
                              self.hits, self.terms, self.loc, shared_lines=lines)
        self.assertEqual(hi["gated"] - shared["gated"], 20)

    def test_pair_closure_capped_on_different_lines(self):
        # each side qualifies on a different pair of crux lines: per side 5 + 20 = 25, but the pair may close at
        # most 2 x 5 + min(20, 10 x 2 distinct lines... here 4) = 30, not 50
        lines = ["- Aave lending TVL rose to $31.2B on Base and Arbitrum this week, per DefiLlama data",
                 "- Hyperliquid open interest reached $9.8B, a record, as BTC perps funding turned negative"]
        terms = debate.crux_terms([CRUX, "Aave TVL $31.2B and Hyperliquid open interest $9.8B"])
        lo = debate.gate_move("trader", 40, 65, {"q1": 40}, 80, "narrow",
                              [{"section": "", "quote": HIT_A}, {"section": "", "quote": HIT_B}], [], [],
                              self.hits, terms, self.loc)
        hi = debate.gate_move("skeptic", 80, 55, {"q1": 80}, 40, "narrow",
                              [{"section": "", "quote": x} for x in lines], [], [], [], terms, self.loc)
        self.assertEqual((lo["gated"], hi["gated"]), (65, 55))        # 25 each before the pair cap
        k_lo = debate.qualifying_lines(lo, [{"quote": HIT_A}, {"quote": HIT_B}], self.loc)
        k_hi = debate.qualifying_lines(hi, [{"quote": x} for x in lines], self.loc)
        self.assertEqual(len(k_lo | k_hi), 4)
        self.assertEqual(debate.pair_allowance(k_hi, k_lo), 30)
        mh, ml, capped = debate.cap_pair(hi, lo, k_hi, k_lo)
        self.assertTrue(capped)
        self.assertEqual((80 - mh["gated"]) + (ml["gated"] - 40), 30)
        self.assertTrue(any(f.startswith("pair evidence cap") for f in mh["flags"]))
        again = debate.cap_pair(mh, ml, k_hi, k_lo)                    # idempotent
        self.assertFalse(again[2])
        # a move that fits is left alone; a missing side is fine
        self.assertFalse(debate.cap_pair(None, debate.gate_move("trader", 40, 45, {"q1": 40}, 80, "narrow", [], [], [],
                                                                self.hits, terms, self.loc), set(), set())[2])

    def test_same_headline_twice_is_one_line(self):
        head = "SEC delays decision on spot SOL ETF to November 14"
        loc = evidence.Locator({"package": "# SECTION 0: CROSS-SOURCE SIGNALS\n- " + head + "\n"
                                           "# SECTION 4: NEWS INTELLIGENCE\n- [coindesk.com] " + head
                                           + " https://www.coindesk.com/a/1\n"})
        self.assertEqual(debate.line_key(loc, "package", 2), debate.line_key(loc, "package", 4))

    def test_event_question_entity_line_qualifies(self):
        line = "- South Korea weighs role in Hormuz security after Macron talks, contribution options under review"
        loc = locator("# SECTION 4: NEWS INTELLIGENCE\n" + line + "\n")
        terms = debate.crux_terms(["South Korea commits naval support to Hormuz security"])
        ev = [{"section": "", "quote": line}]
        thr = debate.gate_move("user_agent", 30, 50, {"q1": 30}, 60, "narrow", ev, [], [], [], terms, loc, kind="threshold")
        self.assertEqual(thr["gated"], 35)
        self.assertIn("evidence not qualifying, capped", thr["flags"])
        self.assertEqual(thr["new_evidence"][0]["why_not"], "no crux number or entity")
        evt = debate.gate_move("user_agent", 30, 50, {"q1": 30}, 60, "narrow", ev, [], [], [], terms, loc, kind="event")
        self.assertTrue(evt["new_evidence"][0]["qualifies"])
        self.assertEqual(evt["gated"], 45)

    def test_market_odds_line_never_qualifies(self):
        mk = "# SECTION 8: PREDICTION MARKETS\n- Total 24h DEX volume above $11,121,712,895 on Friday? YES 62% (1d +3 pts)\n"
        loc = locator(mk)
        m = debate.gate_move("trader", 40, 65, {"q1": 40}, 80, "narrow",
                             [{"section": "", "quote": "- Total 24h DEX volume above $11,121,712,895 on Friday? YES 62% (1d +3 pts)"}],
                             [], [], [], self.terms, loc)
        self.assertFalse(m["new_evidence"][0]["qualifies"])
        self.assertEqual(m["new_evidence"][0]["why_not"], "prediction-market odds line")
        self.assertEqual(m["gated"], 45)
        self.assertIn("- Total 24h DEX volume above $11,121,712,895 on Friday? YES 62% (1d +3 pts)",
                      debate.market_lines(loc))

    def test_own_quote(self):
        m = self.gm(65, [{"section": "", "quote": HIT_A}], own=[HIT_A])
        self.assertEqual(m["new_evidence"][0]["new_evidence_source"], "own")
        self.assertFalse(m["new_evidence"][0]["qualifies"])

    def test_fraction_against_take_values(self):
        m = debate.gate_move("trader", 30, 0.6, {"q1": 30, "q2": 70}, 80, "concede", [], [], [], [], self.terms, self.loc)
        self.assertTrue(m["rescaled"])
        self.assertEqual(m["requested"], 60)
        self.assertNotIn("moved away", [f for f in m.get("flags", [])])

    def test_moves_capped_counts_evidence_cap(self):
        flags = [{"agent": "trader", "flag": "evidence move capped at 15", "where": "response"},
                 {"agent": "trader", "flag": "argument only, capped", "where": "response"}]
        s = debate.agent_run_score("trader", "r", "2026-10-04", {"trader": {"q1": 40}}, [], [], {}, flags, "")
        self.assertEqual(s["moves_capped"], 2)


# ── scoring ──────────────────────────────────────────────────────────────────────────────

def side_rec(take, gated, source="none", agent="trader"):
    return {"move": {"agent": agent, "take": take, "requested": gated, "gated": gated, "delta": gated - take,
                     "clamped": False, "rescaled": False, "new_evidence": [], "new_evidence_verified": 0,
                     "new_data_evidence": 0, "evidence_source": source, "flags": []},
            "data": {"verdict": "narrow", "steelman_fair": {"verdict": "yes"}, "crux_agreed": {"verdict": "yes"}}}


PR = {"question_id": "q1", "high": "skeptic", "low": "trader", "p_high": 70, "p_low": 40}


class ScoreTests(unittest.TestCase):
    def test_narrowed_on_argument(self):
        s = debate.score_debate(PR, {}, {"high": side_rec(70, 65), "low": side_rec(40, 45)}, None, 20)
        self.assertTrue(s["live_split"])
        self.assertEqual(s["closure_without_evidence"], 10)
        self.assertEqual(s["effect"], "narrowed from 30 to 20 on argument")
        schemas.validate(json.loads(json.dumps(s)), schemas.ARTIFACTS["debate_score"])

    def test_closed_on_crux_data_needs_the_check(self):
        resp = {"high": side_rec(70, 48, "crux_data"), "low": side_rec(40, 40)}
        s = debate.score_debate(PR, {}, resp, None, 20)
        self.assertTrue(s["closed_on_data"])
        self.assertTrue(s["in_split"])              # not confirmed: the block stays
        self.assertFalse(s["live_split"])           # but the split measured after the debate is gone (8 < 20)
        self.assertTrue(s["narrowed_on_data"])
        ok = {"quote_qualifies": True}
        s = debate.score_debate(PR, {}, resp, {"resolved": "yes", "leans": "lower", **ok}, 20)
        self.assertFalse(s["in_split"])
        s = debate.score_debate(PR, {}, resp, {"resolved": "partly", "leans": "lower", **ok}, 20)
        self.assertFalse(s["in_split"])
        s = debate.score_debate(PR, {}, resp, {"resolved": "partly", "leans": "higher", **ok}, 20)
        self.assertTrue(s["in_split"])

    def test_yes_leaning_against_the_mover_confirms_nothing(self):
        # the high side came down on crux data; the referee says yes, but the data favours the high view
        resp = {"high": side_rec(70, 48, "crux_data"), "low": side_rec(40, 40)}
        s = debate.score_debate(PR, {}, resp, {"resolved": "yes", "leans": "higher", "quote_qualifies": True}, 20)
        self.assertTrue(s["in_split"])
        self.assertIn("crux data", s["effect"])
        self.assertNotIn("confirmed", s["effect"])
        # a referee quote that would not qualify a move (social, odds line, not strict) confirms nothing either
        for cc in ({"resolved": "yes", "leans": "lower", "quote_qualifies": False}, {"resolved": "yes", "leans": "lower"}):
            with self.subTest(cc=cc):
                self.assertTrue(debate.score_debate(PR, {}, resp, cc, 20)["in_split"])

    def test_two_movers_never_confirmed(self):
        # sixth review: 70 vs 40, both sides move to 55 on crux data; a crux check leaning either way said the
        # other side was right to stay, so it confirms neither move and the block stays in the brief
        resp = {"high": side_rec(70, 55, "crux_data"), "low": side_rec(40, 55, "crux_data")}
        for resolved in ("yes", "partly"):
            for lean in ("higher", "lower", "neither"):
                with self.subTest(resolved=resolved, lean=lean):
                    s = debate.score_debate(PR, {}, resp, {"resolved": resolved, "leans": lean, "quote_qualifies": True}, 20)
                    self.assertTrue(s["closed_on_data"])
                    self.assertTrue(s["in_split"])
                    self.assertNotIn("confirmed", s["effect"])
                    self.assertEqual(s["gap_after"], 0)
        # one mover, lean the mover's way: confirmed (the block may go)
        one = {"high": side_rec(70, 48, "crux_data"), "low": side_rec(40, 40)}
        s = debate.score_debate(PR, {}, one, {"resolved": "partly", "leans": "lower", "quote_qualifies": True}, 20)
        self.assertFalse(s["in_split"])
        self.assertIn("confirmed", s["effect"])

    def test_useful_on_crux_check(self):
        pr = {**PR, "p_high": 58, "p_low": 40}
        resp = {"high": side_rec(58, 55), "low": side_rec(40, 42)}
        ok = {"quote_qualifies": True}
        neither = debate.score_debate(pr, {}, resp, {"resolved": "partly", "leans": "neither", **ok}, 20)
        higher = debate.score_debate(pr, {}, resp, {"resolved": "partly", "leans": "higher", **ok}, 20)
        bad_quote = debate.score_debate(pr, {}, resp, {"resolved": "partly", "leans": "higher"}, 20)
        self.assertFalse(neither["useful"])
        self.assertTrue(higher["useful"])
        self.assertFalse(bad_quote["useful"])

    def test_live_and_useful_can_fail(self):
        # a staged pair is not live or useful by construction: a failed debate is neither
        failed = debate.score_debate(PR, {}, {}, None, 20)
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(failed["in_split"])
        self.assertFalse(failed["live_split"])
        self.assertFalse(failed["useful"])
        self.assertFalse(failed["held_split"])
        # narrowed on argument below gap_min: not live, not useful (no data move, no crux check)
        s = debate.score_debate(PR, {}, {"high": side_rec(70, 65), "low": side_rec(40, 46)}, None, 20)
        self.assertEqual(s["gap_after"], 19)
        self.assertFalse(s["live_split"])
        self.assertFalse(s["useful"])
        self.assertTrue(s["in_split"])

    def test_held_split_needs_stated_cruxes(self):
        resp = {"high": side_rec(70, 70), "low": side_rec(40, 40)}
        ch = {"data": {"crux": {"claim": "DEX volume holds above $11B"}}}
        held = debate.score_debate(PR, {"high": ch, "low": ch}, resp, None, 20)
        self.assertTrue(held["held_split"])
        self.assertTrue(held["live_split"])
        self.assertTrue(held["useful"])                       # held with an agreed crux
        no_crux = debate.score_debate(PR, {"high": ch, "low": {"data": {"crux": {"claim": ""}}}}, resp, None, 20)
        self.assertFalse(no_crux["held_split"])
        one = debate.score_debate(PR, {"high": ch, "low": ch}, {"high": side_rec(70, 70)}, None, 20)
        self.assertFalse(one["held_split"])
        schemas.validate(json.loads(json.dumps(held)), schemas.ARTIFACTS["debate_score"])


# ── crux search ──────────────────────────────────────────────────────────────────────────

class CruxSearchTests(unittest.TestCase):
    def test_raw_fixture(self):
        raw = read(FIX / "2026-09-11" / "00_raw_data.md")
        docs = {"raw": raw, "package": "", "social": ""}
        loc = evidence.Locator({"raw": raw})
        terms = debate.drop_frequent_entities(debate.crux_terms(["DEX volume rises while TVL falls"]), docs)
        quoted = "- Total 24h DEX volume: $11,121,712,895"
        res = debate.crux_search(terms, docs, [quoted], loc)
        self.assertTrue(res["hits"])
        self.assertLessEqual(len(res["hits"]), 12)
        self.assertLessEqual(len(res["block"].encode("utf-8")), 3000)
        for h in res["hits"]:
            self.assertTrue(h["terms"]["entities"] or h["terms"]["numbers"])
            self.assertNotEqual(h["text"], quoted.strip())
            self.assertEqual(h["cls"], "data")
        self.assertTrue(any("DEX" in h["text"] for h in res["hits"]))

    def test_not_entities(self):
        ents = debate.entities("The September report. Volume rose 2026-10-04 in Q4 on Kalshi.", set())
        for w in ("The", "September", "2026-10-04", "Q4", "Volume"):
            self.assertNotIn(w, ents)
        self.assertIn("Kalshi", ents)

    def test_numbers_need_unit_and_scale(self):
        t = debate.crux_terms(["BTC holds 85K"])
        for line, ok in (("- BTC: $85,000 today", True), ("- 85 tokens listed", False), ("- raised $85.2M", False),
                         ("- 85.1% of supply", False)):
            with self.subTest(line=line):
                self.assertEqual(bool(debate.term_hits(line, t)["numbers"]), ok)
        b = debate.crux_terms(["a $1B round"])
        self.assertFalse(debate.term_hits("- fees fell 1.0%", b)["numbers"])

    def test_frequent_entities_dropped(self):
        docs = {"raw": "\n".join([f"- BTC line {i}" for i in range(200)] + ["- Kalshi lists a market"])}
        t = debate.drop_frequent_entities({"numbers": [], "entities": ["BTC", "Kalshi"], "metrics": []}, docs)
        self.assertEqual(t["entities"], ["Kalshi"])

    def test_question_entities_pinned_not_scored(self):
        # an event question's own entities are pinned: kept for ranking, never a scoring or qualifying entity
        docs = {"raw": "\n".join([f"- Hormuz line {i}" for i in range(200)] + ["- Kalshi lists a market"])}
        t = debate.drop_frequent_entities({"numbers": [], "entities": ["Hormuz", "Kalshi"], "metrics": []}, docs,
                                          keep_always=["Hormuz"], subject=["Hormuz"])
        self.assertEqual((t["entities"], t["pinned"]), (["Kalshi"], ["Hormuz"]))
        h = debate.term_hits("- Hormuz traffic resumes as Kalshi lists a market", t)
        self.assertEqual((h["entities"], h["pinned"]), (["Kalshi"], ["Hormuz"]))

    def test_btc_dominance_does_not_qualify_on_a_btc_price_crux(self):
        # sixth review 2026-10-04: on the 10-04 fixture a BTC / $87,500 crux let 10 strict data lines qualify,
        # 'BTC dominance: 58.6%' and 'BTC mined (24h): 403.12 BTC' among them
        raw, pkg = read(FIX / "2026-10-04" / "00_raw_data.md"), read(FIX / "2026-10-04" / "00_data_package.md")
        docs = {"raw": raw, "package": pkg, "social": ""}
        loc = evidence.Locator({"package": pkg, "raw": raw})
        vocab = debate.lowercase_vocab(docs.values())
        question = "Will BTC close above $87,500 on 2026-10-11?"
        crux = ["Bitcoin closes above $87,500 on CoinGecko by 2026-10-11", "BTC daily close above $87,500"]
        subj = debate.entities(question, vocab)
        for kind, keep in (("threshold", []), ("direction", []), ("event", subj), ("judgment", subj)):
            with self.subTest(kind=kind):
                t = debate.drop_frequent_entities(debate.crux_terms(crux, vocab), docs, keep_always=keep, subject=subj)
                for line in ("- BTC dominance: 58.6%", "- BTC mined (24h): 403.12 BTC"):
                    self.assertTrue(loc.strict(line)["ok"])
                    self.assertFalse(debate.shares_specific(line, t, kind), line)
                    m = debate.gate_move("trader", 40, 65, {"q1": 40}, 80, "narrow", [{"section": "", "quote": line}],
                                         [], [], [], t, loc, kind=kind)
                    self.assertFalse(m["new_evidence"][0]["qualifies"], line)
                    self.assertEqual(m["gated"], 45)
                self.assertTrue(debate.shares_specific("- BTC closed at $87,500 on Friday", t, kind))   # the crux number
                res = debate.crux_search(t, docs, [], loc)
                self.assertFalse([h for h in res["hits"] if "dominance" in h["text"] or "mined" in h["text"]])
                for h in res["hits"]:
                    self.assertTrue(h["terms"]["numbers"] or h["terms"]["entities"])
                    self.assertFalse({"BTC", "Bitcoin"} & set(h["terms"]["entities"]))

    def test_event_subject_alone_does_not_qualify(self):
        # an event headline that only names the question's subject is not about the crux; one naming another
        # crux entity is
        line = "- South Korea weighs role in Hormuz security after Macron talks, contribution options under review"
        loc = locator("# SECTION 4: NEWS INTELLIGENCE\n" + line + "\n")
        subj = debate.entities("Will South Korea send naval forces to Hormuz by 2026-10-25?")
        bare = debate.drop_frequent_entities(debate.crux_terms(["South Korea commits naval support to Hormuz security"]),
                                             {"package": line}, keep_always=subj, subject=subj)
        m = debate.gate_move("user_agent", 30, 50, {"q1": 30}, 60, "narrow", [{"section": "", "quote": line}], [], [], [],
                             bare, loc, kind="event")
        self.assertFalse(m["new_evidence"][0]["qualifies"])
        self.assertEqual(m["new_evidence"][0]["why_not"], "no crux entity beyond the question's subject")
        more = debate.drop_frequent_entities(
            debate.crux_terms(["South Korea commits naval support to Hormuz security after Macron talks"]),
            {"package": line}, keep_always=subj, subject=subj)
        m = debate.gate_move("user_agent", 30, 50, {"q1": 30}, 60, "narrow", [{"section": "", "quote": line}], [], [], [],
                             more, loc, kind="event")
        self.assertTrue(m["new_evidence"][0]["qualifies"])

    def test_hyphenated_entities_split(self):
        ents = debate.entities("OpenAI can add capacity despite Astra-driven load; no OpenAI-confirmed Pro-signup reopening")
        self.assertIn("Astra", ents)
        self.assertNotIn("Astra-driven", ents)
        self.assertNotIn("Pro-signup", ents)
        whole = debate.entities("GPT-5 and Llama-3.1 versus US-China export rules")
        for e in ("GPT-5", "Llama-3.1", "US-China"):
            self.assertIn(e, whole)

    def test_market_lines_are_not_crux_hits(self):
        mk = ("# SECTION 8: PREDICTION MARKETS\n- Kalshi DEX volume above $11,121,712,895 market YES 62% (1d +3 pts)\n")
        pkg = PACKAGE + mk
        loc = evidence.Locator({"package": pkg})
        terms = debate.crux_terms(["Kalshi says DEX volume stays above $11,121,712,895"])
        res = debate.crux_search(terms, {"package": pkg, "raw": "", "social": ""}, [], loc)
        self.assertFalse([h for h in res["hits"] if "YES 62%" in h["text"]])

    def test_entity_alone_does_not_qualify(self):
        t = debate.crux_terms(["Kalshi volume rises"])
        self.assertFalse(debate.shares_specific("- Kalshi announced a new office in Seoul today", t))
        self.assertTrue(debate.shares_specific("- Kalshi volume hit $1.2B this week", t))


# ── stats, split sheet, notes, checks, adapters ─────────────────────────────────────────

class MarketGateTests(unittest.TestCase):
    LINES = ['- [fed-rates] Fed Decision in October? — 24h vol $189K | leading: "No change" YES 82% · "25 bps increase" YES 18%',
             '- [Economics] Fed decision in Oct 2026? (On Oct 28, 2026) — top: "Fed maintains rate" 83% · "Hike 25bps" 18%',
             '- [crypto] Bitcoin above ___ on October 4? — leading: "74,000" YES 99.9% · "84,000" YES 88%',
             '- [Crypto] Bitcoin price at the end of 2026 — 24h vol 136,753 contracts | top: "80,000 to 84,999.99" 13%']

    def test_market_match(self):
        self.assertTrue(debate.market_match("Will the Fed hike rates at its October 28 meeting?", self.LINES))
        self.assertTrue(debate.market_match("Will Bitcoin trade above $84,000 on October 4?", self.LINES))
        self.assertFalse(debate.market_match("Will Bitcoin ETF inflows exceed $500M this week?", self.LINES))
        self.assertFalse(debate.market_match("Will Uniswap V3 daily volume stay above $1.2B by October 10?", self.LINES))
        # one body under any of its names, and a multi-word name is one entity, not two
        self.assertTrue(debate.market_match("Will the FOMC hike rates at its October 28 meeting?", self.LINES))
        self.assertTrue(debate.market_match("Will the Federal Reserve hike rates at its October 28 meeting?", self.LINES))
        self.assertFalse(debate.market_match("Will the Federal Reserve cut by 50 bps?",
                                             ["- Federal Reserve Chair speaks at Jackson Hole — YES: 40%"]))
        self.assertTrue(debate.market_match("Will the Securities and Exchange Commission approve a SOL ETF by October 31?",
                                            ["- SEC approves Solana ETF by October 31? — YES: 35%"]))

    def test_odds_by_content(self):
        for line, ok in (("- Fed Rate Hike by September 2026 Meeting? — YES: 59.5% | vol: $3,037,770", True),
                         ("  YES: 40% | 24h vol: $2,031,739 | total vol: $28,333,986", True),
                         ('- [Economics] Fed decision? — top: "Hike 25bps" 18% (+2)', True),
                         ('- [fed-rates] Fed Decision in October? — leading: "No change" YES 82%', True),
                         ("- BITCOIN: $76,898.00 (-1.1% 24h)", False), ("- 7d TVL change: +1.8%", False),
                         ("- Polymarket International: 24h $63,700,846 | 7d $509,396,573 (+18.1% 7d)", False)):
            with self.subTest(line=line):
                self.assertEqual(debate.odds_line(line), ok)

    def test_world_monitor_odds_on_the_0911_fixture(self):
        # World Monitor's Polymarket block sits in SECTION 2 GEOPOLITICAL CONTEXT, not SECTION 8
        f = FIX / "2026-09-11"
        pkg, raw = read(f / "00_data_package.md"), read(f / "00_raw_data.md")
        loc = evidence.Locator({"package": pkg, "raw": raw})
        wm = "- Fed Rate Hike by September 2026 Meeting? — YES: 59.5% | vol: $3,037,770"
        lines = debate.market_lines(loc)
        self.assertIn(wm, lines)
        self.assertTrue(any(x.startswith("- Will the Fed increase interest rates by 25 bps") and "YES: 60%" in x for x in lines))
        fed = "Will the Fed hike rates at its September 2026 meeting?"
        self.assertTrue(debate.market_match(fed, lines))
        # rule 7 drops the Fed-hike question
        qs = [q(fed, kind="event", bq="", domain="macro_policy", resolves="2026-09-18"),
              q("Will total DeFi TVL increase over the next seven days?", kind="direction", bq="", resolves="2026-09-18")]
        r = debate.gate_questions(qs, "2026-09-11", loc, market=lines)
        self.assertEqual([d["text"] for d in r["dropped"] if "prediction market already prices it" in d["reason"]], [fed])
        # Phase C: the same market under the Fed's other names (FOMC, Federal Reserve) is dropped too
        fomc = "Will the FOMC hike by 25 bps in September 2026?"
        held = "Will the Federal Reserve leave rates unchanged at the September 2026 meeting?"
        qs2 = [q(fomc, kind="event", bq="", domain="macro_policy", resolves="2026-09-18"),
               q(held, kind="event", bq="", domain="macro_policy", resolves="2026-09-18"),
               q("Will total DeFi TVL increase over the next seven days?", kind="direction", bq="", resolves="2026-09-18")]
        r2 = debate.gate_questions(qs2, "2026-09-11", loc, market=lines)
        self.assertEqual([d["text"] for d in r2["dropped"] if "prediction market already prices it" in d["reason"]],
                         [fomc, held])
        # the 59.5% line passes Locator.strict as a GEOPOLITICAL CONTEXT data line, but never qualifies a move ...
        st = loc.strict(wm)
        self.assertTrue(st["ok"])
        self.assertEqual((st["section"], st["cls"]), ("GEOPOLITICAL CONTEXT", "data"))
        terms = debate.crux_terms(["The Fed hikes at the September 2026 meeting; markets price 59.5%"])
        m = debate.gate_move("macro_strategist", 40, 65, {"q1": 40}, 70, "narrow", [{"section": "", "quote": wm}],
                             [], [], [], terms, loc, kind="event")
        self.assertFalse(m["new_evidence"][0]["qualifies"])
        self.assertEqual(m["new_evidence"][0]["why_not"], "prediction-market odds line")
        self.assertEqual(m["gated"], 45)
        # ... and is never a crux hit shown to the responders
        res = debate.crux_search(terms, {"package": pkg, "raw": raw, "social": ""}, [], loc)
        self.assertFalse([h for h in res["hits"] if "59.5%" in h["text"] or debate.odds_line(h["text"])])

    def test_odds_line_is_market_class_not_data(self):
        odds = "- Total 24h DEX volume above $11,121,712,895 on Friday? YES 62% (1d +3 pts)"
        loc = locator("# SECTION 8: PREDICTION MARKETS\n" + odds + "\n")
        self.assertEqual(debate.ev_class(loc.locate(odds), loc), "market")
        self.assertEqual(debate.ev_class(loc.locate("- Current: $86,610,000,000"), loc), "data")
        qq = {"id": "q1", "kind": "threshold"}
        p = {"trader": {"q1": 70}, "analyst": {"q1": 30}}
        evq = {"trader": {"q1": [{"status": "verified", "cls": debate.ev_class(loc.locate(odds), loc)}]},
               "analyst": {"q1": [{"status": "verified", "cls": "data"}]}}
        self.assertEqual(debate.eligible_agents(qq, p, evq, ["trader", "analyst"]), ["analyst"])
        # a market odds line is no lens quote
        lens = {"trader": {"text": odds + "\n- Hyperliquid open interest reached $9.8B"}}
        take = {"positions": [{"question_id": "q1", "evidence": [{"quote": odds}]}]}
        self.assertEqual(debate.lens_quote_share({"trader": take}, lens)["per_agent"]["trader"], 0.0)
        # the raw Polymarket and Kalshi blocks are no lens's data until the e1 probe measures them
        for a, entries in debate.LENS_RAW.items():
            self.assertFalse([e for e in entries if "Polymarket" in e or "Kalshi" in e], a)

    def test_gate_drops_a_settled_question(self):
        settled = "- [coindesk.com] SEC delays decision on spot SOL ETF to November 14 https://www.coindesk.com/a/123456789"
        qs = [q("Will the SEC decide on a spot SOL ETF by 2026-10-20?", kind="event", bq="",
                settled="SEC delays decision on spot SOL ETF to November 14"),
              q("Will the SEC approve a spot XRP ETF by 2026-10-20?", kind="event", bq="", settled="SEC approves XRP fund"),
              q("Will TVL stay above 86B by 10-11?")]
        r = debate.gate_questions(qs, "2026-10-04", locator())
        self.assertEqual([x["text"] for x in r["kept"]], ["Will the SEC approve a spot XRP ETF by 2026-10-20?",
                                                          "Will TVL stay above 86B by 10-11?"])
        self.assertTrue(r["dropped"][0]["reason"].startswith("the package already settles it"))
        self.assertTrue(any("settled_quote not found" in n for n in r["notes"]))
        self.assertIn(settled[2:40], locator().raw_docs["package"])

    def test_gate_drops_a_priced_question(self):
        qs = [q("Will Bitcoin trade above $84,000 on October 11?", lenses=["trader", "analyst"]),
              q("Will total DeFi TVL stay above $85B by October 11?", lenses=["trader", "analyst"]),
              q("Will the Fed hike rates at its October 28 meeting?", kind="event", bq="", domain="macro_policy")]
        r = debate.gate_questions(qs, "2026-10-04", locator(), market=self.LINES)
        self.assertEqual([k["text"][:20] for k in r["kept"]], ["Will total DeFi TVL "])
        self.assertTrue(all("prediction market already prices it" in d["reason"] for d in r["dropped"]))


class StatsTests(unittest.TestCase):
    def test_fifties_count_with_neither(self):
        st = debate.question_stats([50, 50, 60, 70, 80, 30, 20, 65, 75])
        self.assertEqual((st["majority_side"], st["majority_count"], st["minority_count"]), ("yes", 5, 2))


def mk_takes(vals: dict, qid="q1", reason_of=None):
    return {a: {"positions": [{"question_id": qid, "probability": v,
                               "reason": (reason_of or {}).get(a, f"{a.replace('_', ' ').title()} thinks {v}"),
                               "evidence": [{"section": "", "quote": "- Current: $86,610,000,000"}]}],
                "summary": f"{a} summary", "claims": []} for a, v in vals.items()}


class SplitSheetTests(unittest.TestCase):
    def sheet(self, vals, day_type="debate", debates=None, challenges=None, red=None, weight=2, gap_min=20):
        qs = [{"id": "q1", "text": "Will TVL stay above 86B by 10-11?", "weight": weight, "resolves_on": "2026-10-11",
               "settles_with": "DeFiLlama total TVL", "ledger_id": "2026-10-04-q1"}]
        takes = mk_takes(vals)
        tp = {a: {"q1": v} for a, v in vals.items()}
        return debate.split_sheet("2026-10-04", "2026-10-04", day_type, qs, tp, tp, debates or [], challenges or {}, {},
                                  takes, locator(), gap_min, red_team=red)

    def assert_clean(self, sh):
        text = debate.render_split_sheet(sh)
        self.assertLessEqual(len(text.encode("utf-8")), debate.SPLIT_SHEET_CAP)
        for a in AG:
            for form in (a, a.replace("_", " ").title(), a.upper(), a.replace("_", " ")):
                if "_" in a or form[0].isupper():
                    self.assertNotIn(form, text, form)
        schemas.validate(json.loads(json.dumps(sh)), schemas.ARTIFACTS["split_sheet"])

    def test_direction(self):
        vals = dict(zip(AG, [60, 65, 70, 75, 80, 85, 62, 25, 40]))
        sh = self.sheet(vals, day_type="debate")
        self.assertEqual(sh["blocks"][0]["type"], "direction")
        self.assertEqual(sh["blocks"][0]["count_phrase"], "7 of 9 lenses put it at 60–85%; 2 put it at 25–40%")
        self.assert_clean(sh)

    def test_degree_on_split_unpaired(self):
        vals = dict(zip(AG, [55, 60, 65, 70, 75, 80, 85, 90, 58]))
        sh = self.sheet(vals, day_type="split_unpaired")
        self.assertEqual(sh["blocks"][0]["type"], "degree")
        self.assertEqual(sh["blocks"][0]["count_phrase"], "all 9 lenses lean yes, from 55% to 90%")
        self.assert_clean(sh)

    def test_consensus_and_none(self):
        vals = dict(zip(AG, [60, 62, 64, 66, 61, 63, 65, 67, 60]))
        red = {"question_id": "q1", "data": {"case": "The Skeptic says the trader's read misses outflows.",
                                             "evidence": [{"quote": "- Current: $86,610,000,000"}],
                                             "crux": {"claim": "outflows"}, "would_change_my_mind": {}}}
        sh = self.sheet(vals, day_type="consensus", red=red)
        bl = sh["blocks"][0]
        self.assertEqual((bl["type"], bl["minority_case"]["source"]), ("consensus", "red_team"))
        self.assertNotIn("red_team_case", bl)
        self.assertTrue(bl["count_phrase"].startswith("all 9 lenses within"))
        schemas.validate(json.loads(json.dumps(bl)), schemas.ARTIFACTS["split_block"])
        self.assert_clean(sh)
        none = self.sheet(vals, day_type="consensus")
        self.assertEqual(none["blocks"], [])
        self.assertTrue(none["no_split_line"].startswith("no split today"))

    def test_rebuttal_or_steelman(self):
        vals = dict(zip(AG, [60, 65, 70, 75, 80, 85, 62, 25, 40]))   # policy_analyst 85 (high), user_agent 25 (minority)
        d = {"question_id": "q1", "high": "policy_analyst", "low": "macro_strategist", "gap_before": 60, "live_split": True,
             "crux_agreed": False, "narrowed_on_data": False}
        mk = lambda flags, rebut: {"data": {"rebuttal": rebut, "steelman": "The minority sees outflows coming.",
                                            "evidence": [], "crux": {"claim": "outflows"}}, "checks": {"flags": flags}}
        clean = {("macro_strategist", "q1"): mk([], "Outflows will win."), ("policy_analyst", "q1"): mk([], "TVL holds.")}
        sh = self.sheet(vals, debates=[d], challenges=clean)
        self.assertEqual(sh["blocks"][0]["minority_case"]["source"], "rebuttal")
        flagged = {("macro_strategist", "q1"): mk(["persona leakage"], "ROADMAP: x"),
                   ("policy_analyst", "q1"): mk([], "TVL holds.")}
        sh = self.sheet(vals, debates=[d], challenges=flagged)
        self.assertEqual(sh["blocks"][0]["minority_case"]["source"], "steelman")

    def test_debated_block_counts_from_takes(self):
        takes_v = dict(zip(AG, [60, 65, 70, 75, 80, 85, 62, 25, 40]))
        finals = {**{a: {"q1": v} for a, v in takes_v.items()}, "macro_strategist": {"q1": 55}, "ai_engineer": {"q1": 55}}
        qs = [{"id": "q1", "text": "Will TVL stay above 86B by 10-11?", "weight": 2, "resolves_on": "2026-10-11",
               "settles_with": "DeFiLlama total TVL", "ledger_id": "2026-10-04-q1"}]
        d = {"question_id": "q1", "high": "policy_analyst", "low": "macro_strategist", "gap_before": 60, "live_split": True,
             "crux_agreed": False, "narrowed_on_data": False}
        tp = {a: {"q1": v} for a, v in takes_v.items()}
        sh = debate.split_sheet("2026-10-04", "2026-10-04", "debate", qs, tp, finals, [d], {}, {}, mk_takes(takes_v),
                                locator(), 20)
        bl = sh["blocks"][0]
        self.assertEqual(bl["type"], "direction")                     # not 'all 9 lenses lean yes'
        self.assertEqual(bl["count_phrase"], "7 of 9 lenses put it at 60–85%; 2 put it at 25–40%")
        self.assertEqual((bl["counts"]["majority"], bl["counts"]["minority"]), (7, 2))

    def test_minority_voice_on_the_minority_side(self):
        # the pair straddles the median (55-90), not 50: two lenses at 30 are the minority, neither debated
        vals = dict(zip(AG, [55, 90, 70, 75, 30, 72, 30, 68, 66]))
        reasons = {"skeptic": "Outflows break the floor.", "user_agent": "Users are leaving.", "trader": "Holds near 55."}
        d = {"question_id": "q1", "high": "narrator", "low": "trader", "gap_before": 35, "live_split": True,
             "crux_agreed": False, "narrowed_on_data": False}
        chs = {("trader", "q1"): {"data": {"rebuttal": "It holds, barely.", "steelman": "s", "evidence": [],
                                           "crux": {"claim": "outflows"}}, "checks": {"flags": []}},
               ("narrator", "q1"): {"data": {"rebuttal": "It rises.", "steelman": "s", "evidence": [],
                                             "crux": {"claim": "outflows"}}, "checks": {"flags": []}}}
        qs = [{"id": "q1", "text": "Will TVL stay above 86B by 10-11?", "weight": 2, "resolves_on": "2026-10-11",
               "settles_with": "DeFiLlama total TVL", "ledger_id": "2026-10-04-q1"}]
        tp = {a: {"q1": v} for a, v in vals.items()}
        sh = debate.split_sheet("2026-10-04", "2026-10-04", "debate", qs, tp, tp, [d], chs, {},
                                mk_takes(vals, reason_of=reasons), locator(), 20)
        mc = sh["blocks"][0]["minority_case"]
        self.assertEqual(mc["source"], "reason")
        self.assertIn(mc["text"], ("Outflows break the floor.", "Users are leaving."))

    def test_crux_check_shown_only_when_its_quote_held(self):
        vals = dict(zip(AG, [60, 65, 70, 75, 80, 85, 62, 25, 40]))
        qs = [{"id": "q1", "text": "Will TVL stay above 86B by 10-11?", "weight": 2, "resolves_on": "2026-10-11",
               "settles_with": "DeFiLlama total TVL", "ledger_id": "2026-10-04-q1"}]
        d = {"question_id": "q1", "high": "policy_analyst", "low": "user_agent", "gap_before": 60, "in_split": True,
             "live_split": True, "crux_agreed": False, "narrowed_on_data": False}
        tp = {a: {"q1": v} for a, v in vals.items()}

        def cc(status, qualifies):
            return {"question_id": "q1", "quote_status": status,
                    "data": {"resolved": "no" if status != "verified" else "partly", "leans": "higher",
                             "what_the_data_says": "TVL printed 91.4B on Friday.", "quote": "- Current: $86,610,000,000",
                             "quote_qualifies": qualifies,
                             "settles_on": {"observable": "the referee's own series", "by_date": "2026-10-20"}}}
        for status, qual, shown in (("verified", True, True), ("unverified", False, False), ("partial", True, False),
                                    ("verified", False, False)):
            with self.subTest(status=status, qualifies=qual):
                sh = debate.split_sheet("2026-10-04", "2026-10-04", "debate", qs, tp, tp, [d], {}, {}, mk_takes(vals),
                                        locator(), 20, crux_check=cc(status, qual))
                bl = sh["blocks"][0]
                text = debate.render_split_sheet(sh)
                if shown:
                    self.assertEqual(bl["crux_check"]["resolved"], "partly")
                    self.assertEqual(bl["settles_on"], {"observable": "the referee's own series", "by_date": "2026-10-20"})
                    self.assertIn("Data check:", text)
                else:
                    self.assertIsNone(bl["crux_check"])
                    self.assertNotIn("91.4B", text)
                    self.assertEqual(bl["settles_on"], {"observable": "DeFiLlama total TVL", "by_date": "2026-10-11"})

    def test_debated_down_on_argument_still_a_block(self):
        vals = dict(zip(AG, [55, 58, 60, 62, 57, 59, 61, 56, 50]))
        d = {"question_id": "q1", "high": "builder", "low": "user_agent", "gap_before": 30, "live_split": True,
             "crux_agreed": True, "narrowed_on_data": False}
        sh = self.sheet(vals, debates=[d])
        self.assertEqual(len(sh["blocks"]), 1)


class LedgerTests(unittest.TestCase):
    def test_append_idempotent_and_tagged_state(self):
        from recon.orchestrator import Run, main  # noqa: F401
        import argparse
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "ledger.jsonl"
            lines = [{"type": "question", "ledger_id": "2026-10-04-c3-q1"}, {"type": "question", "ledger_id": "2026-10-04-c3-q2"}]
            key = lambda l: (l["type"], l["ledger_id"])
            self.assertEqual(Run.append_jsonl(p, lines, key), 2)
            self.assertEqual(Run.append_jsonl(p, lines, key), 0)
            self.assertEqual(len(p.read_text(encoding="utf-8").splitlines()), 2)
        args = argparse.Namespace(as_of="2026-10-04", dry_run=False, replay=None, run_id="2026-10-04-c3", state_dir=None,
                                  from_phase=None, resume=False)
        r = Run(args)
        self.assertEqual(r.ledger_path, r.dir / "state" / "questions" / "ledger.jsonl")

    def test_daily_run_debate_off_by_default(self):
        import argparse
        import os
        from recon.orchestrator import Run
        keep = os.environ.pop("RECON_DEBATE", None)
        try:
            daily = Run(argparse.Namespace(as_of=None, dry_run=False, replay=None, run_id=None, state_dir=None,
                                           from_phase=None, resume=False))
            self.assertFalse(daily.debate_on)                 # §0.1 gate failed: production does not debate
            tagged = Run(argparse.Namespace(as_of="2026-10-04", dry_run=False, replay=None, run_id="2026-10-04-c3",
                                            state_dir=None, from_phase=None, resume=False))
            self.assertTrue(tagged.debate_on)                 # validation runs (c3/c4) and replays do
            os.environ["RECON_DEBATE"] = "1"
            self.assertTrue(Run(argparse.Namespace(as_of=None, dry_run=False, replay=None, run_id=None, state_dir=None,
                                                   from_phase=None, resume=False)).debate_on)
        finally:
            os.environ.pop("RECON_DEBATE", None)
            if keep is not None:
                os.environ["RECON_DEBATE"] = keep


class AdapterAndTextTests(unittest.TestCase):
    def test_legacy_adapters(self):
        resp = [{"agent": "trader", "question_id": "q1", "move": {"take": 40, "gated": 45},
                 "data": {"reason": "new data", "verdict": "narrow"}},
                {"agent": "trader", "question_id": "q2", "move": {"take": 60, "gated": 60},
                 "data": {"reason": "held", "verdict": "hold"}}]
        mv = debate.legacy_moves("trader", resp)
        self.assertEqual(set(mv[0]), {"question_id", "from", "to", "delta", "reason"})
        lr = debate.legacy_response("trader", resp)
        self.assertEqual(lr["verdict"], "narrow")
        self.assertIsInstance(lr["text"], str)
        self.assertIsNone(debate.legacy_response("skeptic", resp))

    def test_lens_notes(self):
        takes = {"trader": {"summary": "TVL flat.", "novel": "Watch Aave.",
                            "claims": [{"claim": "TVL at 86.6B", "quote": "- Current: $86,610,000,000"},
                                       {"claim": "made up", "quote": "- TVL is 999B per nobody"},
                                       {"claim": "social", "quote": "Stablecoin flows into Base hit 1.2B this week, biggest since March"}],
                            "prediction": {"text": "TVL > 85B", "probability": 0.7, "resolves_on": "2026-10-11"}}}
        notes = debate.lens_notes(takes, ["trader"], locator())
        self.assertIn("MARKETS LENS", notes)
        self.assertIn("TVL at 86.6B", notes)
        self.assertNotIn("made up", notes)
        self.assertNotIn("1.2B this week", notes)
        self.assertNotIn("trader", notes.lower().replace("markets lens", ""))

    def test_leakage(self):
        for t in ("**ROADMAP:** ship it", "Post now on X", "## Take"):
            self.assertTrue(debate.leakage_flags(t), t)

    def test_anonymise_forms(self):
        cases = ["the policy analyst argues", "As the macro strategist, I think", "AI engineer view", "user agent data",
                 "The skeptic overstates the risk", "the trader's read", "Policy_Analyst", "TRADER", "@narrator",
                 "The Skeptic says"]
        for c in cases:
            with self.subTest(c=c):
                self.assertIn("other view", debate.anonymise(c))
        for keep in ("a trader would sell", "analyst consensus", "the analyst consensus holds", "Builder.ai shipped",
                     "Analysts expect"):
            with self.subTest(keep=keep):
                self.assertNotIn("other view", debate.anonymise(keep))

    def test_anonymise_joined_names_and_roles(self):
        # hyphen-joined names, 'As the <role>', 'the <role>,' and '<role> and the <role>' (Phase C review)
        cases = {"The macro-strategist case is that rates hold.": "The other view case is that rates hold.",
                 "The AI-engineer read: shipping slips.": "The other view read: shipping slips.",
                 "As the skeptic, I see 30%.": "As the other view, I see 30%.",
                 "the skeptic and the trader disagree on timing": "One view and the other disagree on timing",
                 "Policy-Analyst view holds.": "The other view holds.",
                 "We side with the analyst; the odds hold.": "We side with the other view; the odds hold.",
                 "As the trader sees it, flows lead.": "As the other view sees it, flows lead.",
                 "As the skeptic I see 30%.": "As the other view I see 30%."}
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(debate.anonymise(src), want)
        for keep in ("a trader would sell, then buy", "the analyst consensus holds", "a builder and the trader class",
                     "As the analyst community expects, rates hold."):
            with self.subTest(keep=keep):
                self.assertNotIn("other view", debate.anonymise(keep))

    def test_brief_checks_joined_names_and_roles(self):
        for leak in ("The macro-strategist case is that rates hold.", "The AI-engineer read: shipping slips.",
                     "As the skeptic, I see 30%.", "the skeptic and the trader disagree on timing.",
                     "Policy-Analyst view holds."):
            with self.subTest(leak=leak):
                brief = "### WHERE THE VIEWS SPLIT\n" + leak + "\n"
                self.assertTrue(debate.brief_checks(brief, None)["agent_names"], leak)
        for leak in ("### RISKS\n- the macro-strategist sees a hike\n", "### RISKS\nMACRO-STRATEGIST flagged it.\n"):
            with self.subTest(leak=leak):
                self.assertTrue(debate.brief_checks("# RECON DAILY BRIEF\n" + leak, None)["agent_names"], leak)
        news = "### AI NEWSLETTER\n- Browsers rotate the User-Agent header; a trader, an analyst; and more.\n"
        self.assertEqual(debate.brief_checks(news, None)["agent_names"], [])

    def test_brief_checks(self):
        sheet = {"day_type": "debate", "blocks": [{"type": "direction", "counts": {"n": 9, "majority": 7, "minority": 2}}]}
        clean = ("### WHAT IT MEANS\nAnalysts expect regulatory challenges and a debate over CLARITY; the Senate voted. "
                 "Builder.ai and Analyst Firm both grew.\n### WHERE THE VIEWS SPLIT\n7 of 9 lenses put it at 60–85%.\n")
        r = debate.brief_checks(clean, sheet)
        self.assertEqual((r["agent_names"], r["process_words"], r["count_mismatch"]), ([], [], []))
        bad = ("### WHAT IT MEANS\nThe agents debated it; our lenses agree. Skeptic warns. The policy analyst argues.\n"
               "### WHERE THE VIEWS SPLIT\n6 of 9 lenses put it at 60%.\n")
        r = debate.brief_checks(bad, sheet)
        self.assertTrue(any("Skeptic" in x for x in r["agent_names"]))
        self.assertTrue(any("policy analyst" in x.lower() for x in r["agent_names"]))
        self.assertEqual(len(r["process_words"]), 2)
        self.assertEqual(r["count_mismatch"], ["6 of 9"])

    def test_brief_checks_whole_brief(self):
        for leak in ("### SCORECARD\n- Analyst: WRONG on TVL\n", "### RISKS\n- the macro strategist sees a hike\n",
                     "### SCORECARD\n### MACRO_STRATEGIST\n- [2026-10-01] call\n", "### RISKS\nMACRO_STRATEGIST flagged it.\n",
                     "### KOREA\nThe SKEPTIC view.\n"):
            with self.subTest(leak=leak):
                self.assertTrue(debate.brief_checks("# RECON DAILY BRIEF\n" + leak, None)["agent_names"], leak)
        news = ("### KOREA\n- A policy analyst at KDI said exports rose.\n### AI NEWSLETTER\n- Builder.ai relaunched; "
                "Analysts expect more.\n")
        self.assertEqual(debate.brief_checks(news, None)["agent_names"], [])


class OverlapTests(unittest.TestCase):
    def test_question_overlap_and_lens_share(self):
        takes = {"trader": {"positions": [{"question_id": "q1", "evidence": [{"quote": "- Current: $86,610,000,000"}]}]},
                 "skeptic": {"positions": [{"question_id": "q1", "evidence": [{"quote": "- Current: $86,610,000,000"},
                                                                               {"quote": "- Aave lending TVL rose to $31.2B"}]}]}}
        qo = debate.question_overlap(takes)
        self.assertEqual(qo["per_question"]["q1"], 0.5)
        ls = debate.lens_quote_share(takes, {"trader": {"text": "## X\n- Current: $86,610,000,000"}, "skeptic": {"text": ""}})
        self.assertEqual(ls["per_agent"], {"trader": 1.0, "skeptic": 0.0})


class PairOffTests(unittest.TestCase):
    def test_debate_off(self):
        p = {"trader": {"q1": 20}, "analyst": {"q1": 50}, "skeptic": {"q1": 80}}
        ev = [{"section": "", "quote": "q", "status": "verified", "cls": "data", "doc": "package", "line": 3}]
        evq = {a: {"q1": ev} for a in p}
        qs = [{"id": "q1", "text": "t", "kind": "threshold", "weight": 2, "lenses": []}]
        r = debate.pair(qs, p, evq, list(p), "normal", 3, off_reason="debate off")
        self.assertEqual((r["day_type"], r["pairs"], r["unpaired"][0]["reason"]), ("split_unpaired", [], "debate off"))
        c = debate.pair(qs, {"trader": {"q1": 60}, "analyst": {"q1": 62}, "skeptic": {"q1": 65}}, evq, list(p), "normal", 3,
                        off_reason="debate off")
        self.assertEqual((c["day_type"], c["red_team"]), ("consensus", None))


class LedgerReplaceTests(unittest.TestCase):
    def test_newest_attempt_wins(self):
        from recon.orchestrator import Run
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "ledger.jsonl"
            old = [{"type": "question", "ledger_id": "r-q1", "run_id": "r", "question": {"text": "old"}},
                   {"type": "question", "ledger_id": "x-q1", "run_id": "x"}, {"type": "resolution", "ledger_id": "r-q1", "run_id": "r"}]
            Run.replace_jsonl(p, old, lambda l: False)
            Run.replace_jsonl(p, [{"type": "question", "ledger_id": "r-q1", "run_id": "r", "question": {"text": "new"}}],
                              lambda l: l.get("type") == "question" and l.get("run_id") == "r")
            got = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([(g["type"], g["run_id"]) for g in got], [("question", "x"), ("resolution", "r"), ("question", "r")])
            self.assertEqual(got[-1]["question"]["text"], "new")


class LensTierTests(unittest.TestCase):
    def test_lens_tier_is_synth_model_at_medium(self):
        import os
        from recon import llm
        keep = {k: os.environ.pop(k, None) for k in ("RECON_MODEL_LENS", "RECON_EFFORT_LENS", "RECON_MODEL_SYNTH")}
        try:
            self.assertEqual(llm.codex_model("lens"), (llm.DEFAULT_CODEX["synth"][0], "medium"))
            os.environ["RECON_MODEL_SYNTH"] = "m-synth"
            self.assertEqual(llm.codex_model("lens"), ("m-synth", "medium"))
            self.assertEqual(llm.resolve_tier("lens"), "lens")
            self.assertEqual(set(debate.LENS_TIER.values()), {"lens"})
        finally:
            for k, v in keep.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

    def test_every_attempt_is_reported(self):
        import os
        from recon import llm
        calls, retries = [], []
        real, sleep = llm._call_dry_run, llm.time.sleep
        seq = iter(["", "", "ok"])
        llm._call_dry_run = lambda *a, **k: (next(seq), {}, "boom")
        llm.time.sleep = lambda s: None
        old = os.environ.get("RECON_LLM_PROVIDER")
        os.environ["RECON_LLM_PROVIDER"] = "dry-run"
        try:
            res = llm.ask_ex("p", on_failed_attempt=calls.append, before_retry=retries.append)
        finally:
            llm._call_dry_run, llm.time.sleep = real, sleep
            if old is None:
                os.environ.pop("RECON_LLM_PROVIDER", None)
            else:
                os.environ["RECON_LLM_PROVIDER"] = old
        self.assertEqual((res["text"], res["attempts"]), ("ok", 3))
        self.assertEqual([c["attempt"] for c in calls], [1, 2])
        self.assertEqual(retries, [2, 3])


# ── seventh review (2026-10-04) ──────────────────────────────────────────────────────────

LADDER_BTC = ("- BTC at 2026-10-04 21:00 UTC (Oct 04 5 pm ET close): market-implied median $84,816 (25–75 %: "
              "$84,398–$85,223), from 80 strikes | event KXBTCD-26OCT0417 | 24h vol 261,505 contracts | "
              "https://kalshi.com/markets/kxbtcd")
LADDER_ETH = ("- ETH at 2026-10-04 21:00 UTC (Oct 04 5 pm ET close): market-implied median $2,693 (25–75 %: "
              "$2,670–$2,715), from 40 strikes | event KXETHD-26OCT0417 | 24h vol 18,771 contracts | "
              "https://kalshi.com/markets/kxethd")


class SeventhReviewScoreTests(unittest.TestCase):
    def test_other_plus_crux_data_is_two_movers(self):
        # high 70 -> 55 on 'other' evidence, low 40 -> 55 on crux data: two movers, nothing confirmed, block stays
        resp = {"high": side_rec(70, 55, "other"), "low": side_rec(40, 55, "crux_data")}
        cc = {"resolved": "yes", "leans": "higher", "quote_qualifies": True}
        s = debate.score_debate(PR, {}, resp, cc, 20)
        self.assertTrue(s["in_split"])
        self.assertNotIn("confirmed", s["effect"])
        for lean in ("higher", "lower"):
            self.assertTrue(debate.score_debate(PR, {}, resp, {**cc, "leans": lean}, 20)["in_split"])
        # an argument-only mover (source 'none', capped at 5) is not a mover: one crux-data mover, confirmed
        one = {"high": side_rec(70, 66), "low": side_rec(40, 55, "crux_data")}
        s = debate.score_debate(PR, {}, one, cc, 20)
        self.assertFalse(s["in_split"])
        self.assertIn("confirmed", s["effect"])

    def test_confirm_needs_the_other_side_to_hold(self):
        cc = {"resolved": "yes", "leans": "lower", "quote_qualifies": True}
        # the low side moved 10 away from the high side: it did not hold
        resp = {"high": side_rec(70, 48, "crux_data"), "low": side_rec(40, 30)}
        self.assertTrue(debate.score_debate(PR, {}, resp, cc, 20)["in_split"])
        # one-sided: the other side never answered, so it did not hold either
        self.assertTrue(debate.score_debate(PR, {}, {"high": side_rec(70, 48, "crux_data")}, cc, 20)["in_split"])

    def test_tiers_recorded(self):
        s = debate.score_debate(PR, {}, {"high": side_rec(70, 65), "low": side_rec(40, 45)}, None, 20)
        self.assertEqual(s["tiers"], {"high": "lens", "low": "analyst"})           # skeptic vs trader
        r = side_rec(70, 65)
        r["calls"] = [{"tier": "synth"}]
        self.assertEqual(debate.score_debate(PR, {}, {"high": r}, None, 20)["tiers"]["high"], "synth")
        schemas.validate(json.loads(json.dumps(s)), schemas.ARTIFACTS["debate_score"])
        self.assertEqual(debate.side_tier("macro_strategist"), "lens")

    def test_swapped_sides_close_to_zero(self):
        pr = {**PR, "p_high": 60, "p_low": 40}
        s = debate.score_debate(pr, {}, {"high": side_rec(60, 60), "low": side_rec(40, 65, "crux_data")}, None, 20)
        self.assertEqual(s["gap_after"], 0)


class SeventhReviewGateTests(unittest.TestCase):
    def setUp(self):
        self.loc = locator()
        self.terms = debate.crux_terms([CRUX])
        self.hits = [{"doc": "package", "line": 5, "text": HIT_A}, {"doc": "package", "line": 6, "text": HIT_B}]

    def test_never_past_the_other_take(self):
        # gap 20, two qualifying lines (25 points allowed): 40 asks for 65, stops at the other take of 60
        m = debate.gate_move("trader", 40, 65, {"q1": 40}, 60, "concede",
                             [{"section": "", "quote": HIT_A}, {"section": "", "quote": HIT_B}], [], [], self.hits,
                             self.terms, self.loc)
        self.assertEqual(m["gated"], 60)
        self.assertIn("stopped at the other view", m["flags"])
        down = debate.gate_move("skeptic", 60, 30, {"q1": 60}, 40, "concede",
                                [{"section": "", "quote": HIT_A}, {"section": "", "quote": HIT_B}], [], [], self.hits,
                                self.terms, self.loc)
        self.assertEqual(down["gated"], 40)

    def test_cap_pair_stops_crossing(self):
        mk = lambda agent, take, gated: {"agent": agent, "take": take, "requested": gated, "gated": gated,
                                         "delta": gated - take, "flags": []}
        mh, ml, changed = debate.cap_pair(mk("skeptic", 60, 45), mk("trader", 40, 52), {"a"}, {"b", "c"})
        self.assertTrue(changed)
        self.assertGreaterEqual(mh["gated"], ml["gated"])
        self.assertEqual((mh["gated"], ml["gated"]), (52, 52))           # the further mover stops where the other is
        self.assertIn("stopped at the other view", mh["flags"])
        again = debate.cap_pair(mh, ml, {"a"}, {"b", "c"})
        self.assertFalse(again[2])                                         # idempotent
        s = debate.score_debate({**PR, "p_high": 60, "p_low": 40}, {}, {"high": {"move": {**side_rec(60, 52)["move"], **mh},
                                                                                 "data": {}},
                                                                        "low": {"move": {**side_rec(40, 52)["move"], **ml},
                                                                                "data": {}}}, None, 20)
        self.assertEqual(s["gap_after"], 0)


class SeventhReviewMarketTests(unittest.TestCase):
    """The real Kalshi ladder lines (2026-10-04-e1 collection) are market lines wherever they sit."""

    def setUp(self):
        pkg = ("# RECON INTELLIGENCE PACKAGE -- 2026-10-04\n# SECTION 0: CROSS-SOURCE SIGNALS\n" + LADDER_BTC + "\n"
               "# SECTION 3: ON-CHAIN & MARKET DATA\n- BTC: $84,790 (-0.4% 24h)\n"
               "# SECTION 8: PREDICTION MARKETS\n## CRYPTO PRICE LADDERS (nearest daily close ≥ 12 h out)\n\n"
               + LADDER_BTC + "\n" + LADDER_ETH + "\n")
        raw = "# Kalshi Intelligence\n## CRYPTO PRICE LADDERS (nearest daily close ≥ 12 h out)\n\n" + LADDER_ETH + "\n"
        self.docs = {"package": pkg, "raw": raw, "social": ""}
        self.loc = evidence.Locator({"package": pkg, "raw": raw})

    def test_ladder_is_market_class(self):
        self.assertTrue(debate.odds_line(LADDER_BTC))
        self.assertTrue(debate.is_market_line("CROSS-SOURCE SIGNALS", LADDER_BTC))
        self.assertTrue(debate.is_market_line("PREDICTION MARKETS", "- anything in SECTION 8 without odds"))
        self.assertFalse(debate.is_market_line("PREDICTION MARKETS", "## CRYPTO PRICE LADDERS"))
        for line in (LADDER_BTC, LADDER_ETH):
            self.assertEqual(debate.ev_class(self.loc.locate(line), self.loc), "market")
        self.assertEqual(debate.ev_class(self.loc.locate("- BTC: $84,790 (-0.4% 24h)"), self.loc), "data")

    def test_ladder_never_qualifies_or_hits(self):
        terms = debate.drop_frequent_entities(debate.crux_terms(["Bitcoin closes above $84,800 on Kalshi's close"]),
                                              self.docs, subject=["BTC"])
        self.assertTrue(debate.shares_specific(LADDER_BTC, terms))        # it carries the crux number ...
        m = debate.gate_move("trader", 40, 65, {"q1": 40}, 80, "narrow", [{"section": "", "quote": LADDER_BTC}],
                             [], [], [], terms, self.loc)
        self.assertFalse(m["new_evidence"][0]["qualifies"])              # ... and still never qualifies
        self.assertEqual(m["new_evidence"][0]["cls"], "market")
        res = debate.crux_search(terms, self.docs, [], self.loc)
        self.assertFalse([h for h in res["hits"] if "market-implied" in h["text"]])

    def test_gate_rule_7_drops_a_priced_price_question(self):
        lines = debate.market_lines(self.loc)
        self.assertIn(LADDER_BTC, lines)
        self.assertIn(LADDER_ETH, lines)
        for text in ("Will BTC close above $87,500 on 2026-10-11?", "Will Bitcoin fall below $80,000 by 2026-10-11?",
                     "Will ETH close above $2,800 on 2026-10-09?"):
            with self.subTest(text=text):
                self.assertTrue(debate.market_match(text, lines))
        self.assertFalse(debate.market_match("Will Solana TVL exceed $10B by 2026-10-11?", lines))
        r = debate.gate_questions([q("Will BTC close above $87,500 on 2026-10-11?", bq="- BTC: $84,790 (-0.4% 24h)")],
                                  "2026-10-04", self.loc, market=lines)
        self.assertEqual(r["kept"], [])
        self.assertIn("prediction market already prices it", r["dropped"][0]["reason"])


class SeventhReviewTermTests(unittest.TestCase):
    def test_small_percentage_needs_a_crux_word(self):
        docs = {"raw": "- Solana TVL: $9.12B (+1.03% 24h)\n- Aave fees: $1.2M (0.98%)\n- Stablecoin supply rose 1.02% to $310B"}
        t = debate.drop_frequent_entities(debate.crux_terms(["stablecoin supply grows above 1% this week"]), docs)
        self.assertFalse(debate.shares_specific("- Solana TVL: $9.12B (+1.03% 24h)", t, "threshold"))
        self.assertFalse(debate.shares_specific("- Aave fees: $1.2M (0.98%)", t, "threshold"))
        self.assertTrue(debate.shares_specific("- Stablecoin supply rose 1.02% to $310B", t, "threshold"))   # metric word

    def test_frequent_numbers_dropped(self):
        docs = {"raw": "\n".join([f"- token {i}: +1.0{i % 5}% 24h volume" for i in range(200)] + ["- USDC supply $75.2B"])}
        t = debate.drop_frequent_entities(debate.crux_terms(["USDC supply holds $75.2B, a 1% weekly volume rise"]), docs)
        self.assertIn("1%", t["frequent_numbers"])
        self.assertEqual([x["raw"] for x in t["numbers"]], ["$75.2B"])

    def test_mid_sentence_common_words_are_not_entities(self):
        ents = debate.entities("Lawmakers in the House and the Treasury expect the Senate to vote; Kalshi and the Fed watch")
        for w in ("House", "Treasury", "Senate"):
            self.assertNotIn(w, ents)
        for w in ("Kalshi", "Fed"):
            self.assertIn(w, ents)
        self.assertIn("GPT-5", debate.entities("the GPT-5 release"))      # digits stay
        self.assertIn("SEC", debate.entities("the SEC rules"))           # all caps stay
        t = debate.crux_terms(["The Senate passes the CLARITY Act after the House and Treasury sign off"])
        for line in ("- House passes defense appropriations bill after overnight session",
                     "- Treasury yields rise as traders weigh the Fed path"):
            with self.subTest(line=line):
                self.assertFalse(debate.shares_specific(line, t, "event"))
        self.assertTrue(debate.shares_specific("- Senators move CLARITY Act to the floor", t, "event"))
        # market_match keeps the older rule: a named body mid-sentence still finds its market
        self.assertTrue(debate.market_match("Will the Supreme Court rule on tariffs by 2026-10-30?",
                                            ['- [politics] Supreme Court tariff ruling by Oct 30? — YES 41%']))

    def test_pinned_subject_plus_one_crux_entity_is_a_hit(self):
        docs = {"raw": "- Hormuz escort talks: UAE hosts the Korean team\n- Hormuz traffic disrupted again\n"
                       "- UAE port volumes steady"}
        t = debate.drop_frequent_entities({"numbers": [], "entities": ["UAE", "Hormuz"], "metrics": []}, docs,
                                          keep_always=["Hormuz"], subject=["Hormuz"])
        res = debate.crux_search(t, docs, [])
        self.assertEqual([h["text"] for h in res["hits"]], ["- Hormuz escort talks: UAE hosts the Korean team"])
        self.assertEqual(res["pool"], {"term_lines": 3, "pass_with_subject": 1, "pass": 1, "after_quote_exclusion": 1,
                                       "shared": 0})
        self.assertFalse(debate.shares_specific("- Hormuz traffic disrupted again", t, "event"))
        quoted = debate.crux_search(t, docs, ["- Hormuz escort talks: UAE hosts the Korean team"])
        self.assertEqual(quoted["pool"]["after_quote_exclusion"], 0)


class SeventhReviewSettledTests(unittest.TestCase):
    """09-10 c6: the triage left settled_quote empty beside 'Microsoft has new AI privacy rules for schools'."""

    def setUp(self):
        self.loc = evidence.Locator({"package": read(FIX / "2026-09-10" / "00_data_package.md")})

    def test_0910_package_drops_the_school_privacy_question(self):
        privacy = "Will a major AI platform announce new school-specific privacy controls by 2026-09-24?"
        hormuz = "Will South Korea announce a concrete Hormuz security contribution by 2026-09-20?"
        qs = [q(privacy, kind="event", bq="", domain="ai_product", resolves="2026-09-24"),
              q(hormuz, kind="event", bq="", domain="korea", resolves="2026-09-20")]
        r = debate.gate_questions(qs, "2026-09-10", self.loc)
        self.assertEqual([k["text"] for k in r["kept"]], [hormuz])
        self.assertEqual(r["dropped"][0]["text"], privacy)
        self.assertIn("the package already reports it", r["dropped"][0]["reason"])
        self.assertIn("Microsoft has new AI privacy rules for schools", r["dropped"][0]["reason"])

    def test_no_false_settles(self):
        for text in ("Will OpenAI resume new Pro subscriptions by 2026-09-18?",
                     "Will the SEC approve a spot SOL ETF by 2026-10-20?",
                     "Will OpenAI release GPT-6 by 2026-10-20?"):
            with self.subTest(text=text):
                self.assertEqual(debate.settled_line(text, self.loc), "")
        hedged = evidence.Locator({"package": "# SECTION 4: NEWS INTELLIGENCE\n"
                                              "- [x.com] South Korea weighs role in Hormuz security after talks\n"
                                              "- Microsoft plans to launch AI privacy rules for schools\n"})
        self.assertEqual(debate.settled_line("Will South Korea announce a Hormuz security role by 2026-09-20?", hedged), "")
        self.assertEqual(debate.settled_line("Will Microsoft launch AI privacy rules for schools by 2026-09-30?", hedged), "")


class SeventhReviewSheetTests(unittest.TestCase):
    def test_held_split_before_degree_and_closed_degree_left_out(self):
        qs = [{"id": "q1", "text": "Will a major AI platform add school privacy controls by 09-24?", "weight": 3,
               "resolves_on": "2026-09-24", "settles_with": "an announcement", "ledger_id": "x-q1"},
              {"id": "q2", "text": "Will South Korea announce a Hormuz role by 09-20?", "weight": 1,
               "resolves_on": "2026-09-20", "settles_with": "Yonhap", "ledger_id": "x-q2"}]
        v1 = dict(zip(AG, [68, 80, 68, 85, 90, 95, 98, 84, 88]))        # degree: all lean yes
        v2 = dict(zip(AG, [35, 40, 67, 45, 47, 52, 55, 60, 38]))        # direction
        tp = {a: {"q1": v1[a], "q2": v2[a]} for a in AG}
        takes = {a: {"positions": [{"question_id": qq, "probability": tp[a][qq], "reason": f"r {qq}",
                                    "evidence": [{"section": "", "quote": "- Current: $86,610,000,000"}]}
                                   for qq in ("q1", "q2")], "summary": "", "claims": []} for a in AG}
        d1 = {"question_id": "q1", "high": "policy_analyst", "low": "builder", "gap_before": 27, "gap_after": 17,
              "in_split": True, "live_split": False, "held_split": False, "crux_agreed": False, "narrowed_on_data": False}
        d2 = {"question_id": "q2", "high": "builder", "low": "trader", "gap_before": 32, "gap_after": 27,
              "in_split": True, "live_split": True, "held_split": True, "crux_agreed": False, "narrowed_on_data": False}
        sh = debate.split_sheet("2026-09-10", "r", "debate", qs, tp, tp, [d1, d2], {}, {}, takes, locator(), 20)
        self.assertEqual([b["question_id"] for b in sh["blocks"]], ["q2"])
        # without a held split the degree block stays, after the live direction block, with its levels
        d2b = {**d2, "held_split": False, "live_split": True}
        sh = debate.split_sheet("2026-09-10", "r", "debate", qs, tp, tp, [d1, d2b], {}, {}, takes, locator(), 20)
        self.assertEqual([b["question_id"] for b in sh["blocks"]], ["q2", "q1"])
        deg = sh["blocks"][1]
        self.assertEqual(deg["type"], "degree")
        self.assertTrue(deg["base_case"]["level"].endswith("%"))
        self.assertIn(deg["minority_case"]["level"], ("95%", "68%"))
        text = debate.render_split_sheet(sh)
        self.assertIn("Base case (most lenses at ", text)
        schemas.validate(json.loads(json.dumps(sh)), schemas.ARTIFACTS["split_sheet"])


class SeventhReviewScorecardTests(unittest.TestCase):
    def test_user_header_never_reaches_the_synthesizer(self):
        import types
        from recon import orchestrator
        card = ("## Market Snapshot\n- BTC: $84,000\n\n## Pending Predictions\n"
                "*Agents: review your predictions below.*\n\n### USER\n- [2026-10-01] adoption up\n\n"
                "### AI_ENGINEER\n- [2026-10-01] new model\n\n### SOMEONE NEW\n- [2026-10-02] x\n")
        out = orchestrator.Run.synth_scorecard(types.SimpleNamespace(scorecard=lambda: card))
        self.assertNotIn("USER", out)
        self.assertNotIn("AI_ENGINEER", out)
        self.assertNotIn("SOMEONE NEW", out)
        self.assertIn("## Market Snapshot", out)
        self.assertIn("- [2026-10-01] adoption up", out)
        self.assertTrue(debate.brief_checks("## SCORECARD\nUSER: WRONG\n", None)["agent_names"])

    def test_score_yesterday_lists_every_agent(self):
        src = read(REPO / "scripts" / "score_yesterday.py")
        self.assertIn('("user_agent", "user")', src)
        self.assertEqual(src.count('"ai_engineer"'), 3)


if __name__ == "__main__":
    unittest.main()


class EighthReviewCruxKeywordTests(unittest.TestCase):
    """09-10 / 09-11 c6: the Hormuz crux search passed 1 line (quoted by both sides), so no debate could move on
    data and the 09-11 crux check did not run. Event cruxes are written in words ('troop', 'deployment',
    'options'), not names or numbers: on an event or judgment question the pinned subject plus two crux keywords
    is a hit and qualifies a quote. Lines and cruxes are the real 09-11 c6 ones."""

    Q = "Will South Korea announce a concrete Hormuz security contribution by September 20, 2026?"
    CRUXES = [
        "The disrupted Hormuz environment and UAE assessment mission will convert Seoul's option review into an "
        "announced defined logistical or maritime contribution.",
        "A South Korean government announcement or major wire-service report specifying a Hormuz security contribution.",
        "Seoul's assessment and option discussions will convert into an announced, concrete Hormuz contribution by "
        "the resolution date.",
        "An official South Korean announcement or major wire-service report specifying a committed Hormuz role, "
        "asset, personnel deployment, or logistical support.",
    ]
    WEIGHS = "- [middle-east-online.com] South Korea weighs role in Hormuz security after Macron talks"
    DISCUSS = "- [freemalaysiatoday.com] South Korea says discussing Hormuz contribution options , not troop deployment"
    CONCERN = ("- [al-monitor.com] South Korea says Hormuz talks with France concern contribution options , "
               "not troop deployment")
    TEAM = "- [koreaherald.com] Seoul dispatches assessment team to UAE but deployment decision still pending"

    def setUp(self):
        from types import SimpleNamespace
        from recon.orchestrator import Run
        filler = [f"- [wire] Unrelated headline number {i} about markets and weather" for i in range(150)]
        self.docs = {"raw": "", "social": "",
                     "package": "\n".join(filler[:75] + [self.WEIGHS, self.DISCUSS, self.CONCERN, self.TEAM] + filler[75:])}
        stub = SimpleNamespace(corpus_docs=lambda: self.docs)
        self.terms = lambda kind: Run.search_terms(stub, self.CRUXES, self.Q, kind)

    def test_subject_plus_two_crux_keywords_is_a_hit_on_event_questions(self):
        t = self.terms("event")
        self.assertIn("troop", debate.crux_keywords(["no troop role"], self.Q))
        kw = set(t["keywords"])
        self.assertTrue({"option", "deploy", "discussion"} <= kw)
        # question words, boilerplate and capitalised names are never keywords
        self.assertFalse({"contribution", "security", "announc", "official", "report", "hormuz", "seoul"} & kw)
        both_quoted = [self.DISCUSS, self.TEAM]          # what the 09-11 pair quoted
        res = debate.crux_search(t, self.docs, both_quoted)
        self.assertEqual([h["text"] for h in res["hits"]], [self.CONCERN])
        self.assertEqual(res["pool"]["pass"], 3)
        self.assertTrue(debate.shares_specific(self.CONCERN, t, "event"))
        # the subject plus one keyword is not enough, and keywords without the subject never qualify
        self.assertFalse(debate.shares_specific(self.WEIGHS, t, "event"))
        self.assertFalse(debate.shares_specific("- Insurers price troop deployment options for the Gulf", t, "event"))

    def test_unrelated_korea_headlines_never_qualify(self):
        """Eighth review follow-up: keywords matched as 5-letter prefixes ('missi' from 'mission' on 'missile',
        'commi' from 'commits' on 'commission', 'appro' from 'approval' on 'approves') and the subject pinned as
        the single token 'Korea' ('South' is a common word) let unrelated Korea headlines qualify on an event
        question. Keywords match as whole words up to an inflection; 'South Korea' stays one subject."""
        q = "Will South Korea announce a naval deployment to the Strait of Hormuz?"
        cruxes = ["Seoul commits a destroyer to an escort mission once the defensive posture review ends.",
                  "A ministerial approval naming the vessels, crew and escort mission for the strait."]
        stub_docs = {"raw": "", "social": "", "package": read(FIX / "2026-10-04" / "00_data_package.md")}
        from types import SimpleNamespace
        from recon.orchestrator import Run
        t = Run.search_terms(SimpleNamespace(corpus_docs=lambda: stub_docs), cruxes, q, "event")
        self.assertIn("South Korea", t["pinned"])
        self.assertNotIn("Korea", t["pinned"])
        north = "- North Korea test-fires ballistic missile toward the sea, defense ministry says"
        won = "- South Korea's financial commission approves won stablecoin pilot"
        for line in (north, won):
            with self.subTest(line=line):
                self.assertFalse(debate.shares_specific(line, t, "event"))
                self.assertLess(len(debate.term_hits(line, t)["keywords"]), debate.KEYWORD_PAIR)
        self.assertEqual(debate.term_hits(north, t)["pinned"], [])
        # inflections still match: 'commits' / 'committed', 'mission' / 'missions', 'approval' / 'approvals'
        good = "- South Korea committed two destroyers to escort missions after cabinet approvals"
        self.assertTrue(debate.shares_specific(good, t, "event"))
        self.assertEqual(debate.entities("North Korea fires a missile; South Korea and Korea Exchange respond"),
                         ["North Korea", "South Korea", "Korea"])

    def test_threshold_questions_get_no_keywords(self):
        t = self.terms("threshold")
        self.assertNotIn("keywords", t)
        self.assertEqual(debate.crux_search(t, self.docs, [self.DISCUSS, self.TEAM])["hits"], [])
        self.assertFalse(debate.shares_specific(self.CONCERN, t, "threshold"))


class SharedCruxPoolTests(unittest.TestCase):
    """09-11 c8 q3 (OpenAI Pro): the crux search found 0 hits because the one story about the subject was quoted
    by both sides, so no crux check ran and the held split (35 -> 32) had no data path to closure. The lines both
    sides quote, with the rest of their list item, are the shared pool the referee reads when the split held.
    Lines, quotes and cruxes are the real c8 ones."""

    Q = "Will OpenAI resume new Pro subscription sign-ups by 2026-09-18?"
    CRUXES = [
        "OpenAI can add or reallocate sufficient serving capacity to reopen new Pro sign-ups by 2026-09-18.",
        "OpenAI's subscription availability page or an official announcement showing that new Pro subscriptions "
        "are available.",
        "OpenAI's subscription availability page continues to show new Pro sign-ups on hold.",
        "OpenAI can add or free enough Astra serving capacity to reopen new Pro sign-ups within one week.",
    ]
    HEAD = "- [Thu, 10 Sep 2026] OpenAI puts Pro subscriptions on hold due to Astra demand"
    BODY = ("  The company said Pro subscriptions put the most strain on its systems, so it's pausing sign-ups "
            "while adding more capaci")
    LAUNCH = "- [Fri, 11 Sep 2026] GPT-6 Astra: The next generation in intelligence for work - OpenAI"
    HI = ["OpenAI puts Pro subscriptions on hold due to Astra demand",
          "The company said Pro subscriptions put the most strain on its systems,"]
    LO = HI + ["[Fri, 11 Sep 2026] GPT-6 Astra: The next generation in intelligence for work - OpenAI"]

    def setUp(self):
        from types import SimpleNamespace
        from recon.orchestrator import Run
        filler = [f"- [wire] Unrelated headline number {i} about markets and weather" for i in range(150)]
        pkg = "\n".join(["# SECTION 4: NEWS INTELLIGENCE"] + filler[:75] + [self.HEAD, self.BODY, self.LAUNCH]
                        + filler[75:])
        self.docs = {"raw": "", "social": "", "package": pkg}
        self.loc = evidence.Locator({"package": pkg})
        self.terms = Run.search_terms(SimpleNamespace(corpus_docs=lambda: self.docs), self.CRUXES, self.Q, "event")

    def search(self, sides=True, extra=()):
        excl = self.HI + self.LO + list(extra)
        return debate.crux_search(self.terms, self.docs, excl, self.loc,
                                  exclude_positions=debate.quote_positions(excl, self.loc),
                                  sides=(self.HI + list(extra), self.LO + list(extra)) if sides else None)

    def test_the_story_both_sides_quote_is_the_shared_pool(self):
        res = self.search()
        self.assertEqual(res["hits"], [])
        self.assertEqual(res["pool"]["after_quote_exclusion"], 0)
        # the headline both quote and its body (same list item); the launch line only the low side quoted stays out
        self.assertEqual([h["text"] for h in res["shared"]], [self.HEAD, self.BODY.strip()])
        self.assertEqual([h["quoted_by_both"] for h in res["shared"]], [True, False])
        self.assertIn(self.HEAD, res["shared_block"])
        self.assertNotIn("Next generation", res["shared_block"])
        self.assertEqual(res["pool"]["shared"], 2)
        # never a hit: the responders' block and the gate are unchanged
        self.assertEqual(res["block"], "(nothing found on disk for this crux)")
        # without the two sides' quotes (threshold questions, the red team) there is no shared pool
        self.assertEqual(self.search(sides=False)["shared"], [])

    def test_no_shared_pool_when_a_line_neither_side_quoted_is_a_hit(self):
        fresh = "- OpenAI says Pro sign-ups reopen next week as Astra serving capacity is reallocated"
        self.docs["package"] += "\n" + fresh
        self.loc = evidence.Locator({"package": self.docs["package"]})
        res = self.search()
        self.assertEqual([h["text"] for h in res["hits"]], [fresh])
        self.assertEqual(res["shared"], [])

    def _cruxcheck(self, gap_after_low, quote="OpenAI puts Pro subscriptions on hold due to Astra demand"):
        """Run.ph_cruxcheck on the c8 pair (70 / 35 takes, low side moved to `gap_after_low`) with a canned referee."""
        from types import SimpleNamespace
        from recon.orchestrator import Run
        res = self.search()
        cs = {"pairs": [{"question_id": "q3", "high": "policy_analyst", "low": "ai_engineer",
                         "crux_terms": self.terms, **res}]}
        prompts = []

        def call(phase, qid, tier, prompt, schema=None, agent=None):
            prompts.append(prompt)
            return ({"resolved": "partly", "what_the_data_says": "Sign-ups paused while capacity is added.",
                     "quote": quote, "section": "NEWS",
                     "remaining_uncertainty": "When capacity lands.",
                     "settles_on": {"observable": "Pro sign-up page", "by_date": "2026-09-18"}, "leans": "lower"},
                    {"tier": "analyst"})
        take = lambda p: {"positions": [{"question_id": "q3", "probability": p, "reason": "r", "evidence": []}]}
        stub = SimpleNamespace(log=lambda m: None, art=lambda n: SimpleNamespace(exists=lambda: True),
                               load=lambda n: cs, locator=lambda: self.loc, call=call, ncalls=16, budget=24,
                               skip=lambda *a: None, qmap=lambda tri: Run.qmap(None, tri))
        tri = {"questions": [{"id": "q3", "text": self.Q, "kind": "event"}]}
        takes = {"policy_analyst": take(70), "ai_engineer": take(35)}
        pairing = {"gap_min": 20, "pairs": [{"question_id": "q3", "high": "policy_analyst", "low": "ai_engineer",
                                             "p_high": 70, "p_low": 35, "gap": 35}]}
        resps = {"policy_analyst__q3": {"move": {"gated": 70}, "data": {}},
                 "ai_engineer__q3": {"move": {"gated": gap_after_low}, "data": {}}}
        return Run.ph_cruxcheck(stub, tri, takes, pairing, {}, resps), prompts

    def test_crux_check_runs_on_the_held_split_from_the_shared_pool(self):
        cc, prompts = self._cruxcheck(38)            # held at 32 >= GAP_MIN, as on c8
        self.assertTrue(cc["ran"])
        self.assertEqual((cc["question_id"], cc["pool"], cc["gap_before"], cc["gap_after"]), ("q3", "shared", 35, 32))
        self.assertIn(self.HEAD, prompts[0])
        self.assertEqual(cc["quote_status"], "verified")
        self.assertTrue(cc["data"]["quote_qualifies"])
        self.assertTrue(debate.crux_check_usable(cc))

    def test_referee_quote_of_headline_and_body_qualifies(self):
        # 09-11 c9 q5: the referee read shared_block and quoted the Pro-hold headline with its body. quote_status was
        # verified but the strict check said 'not a single verbatim line', so a correct answer confirmed nothing.
        quote = self.HEAD[2:] + " " + self.BODY.strip()
        cc, _ = self._cruxcheck(38, quote=quote)
        self.assertEqual(cc["quote_status"], "verified")
        self.assertTrue(cc["data"]["quote_qualifies"])
        self.assertEqual(cc["flags"], [])
        self.assertTrue(debate.crux_check_usable(cc))
        st = debate.quote_qualifies(quote, self.terms, "event", self.loc)["strict"]
        self.assertEqual((st["doc"], st["line"]), ("package", self.loc.lines["package"].index(self.HEAD) + 1))
        # the gate (§7.2) keeps one line
        self.assertEqual(self.loc.strict(quote)["reason"], "not a single verbatim line")

    def test_referee_quote_across_two_list_items_fails(self):
        # body of the Pro-hold item run into the next item's headline: two items, not one
        quote = self.BODY.strip() + " " + self.LAUNCH
        st = self.loc.strict(quote, item=True)
        self.assertFalse(st["ok"])
        self.assertEqual(st["reason"], "not a single verbatim line")
        cc, _ = self._cruxcheck(38, quote=self.HEAD[2:] + " " + self.BODY.strip() + " " + self.LAUNCH)
        self.assertFalse(cc["data"]["quote_qualifies"])

    def test_no_shared_crux_check_once_the_split_closed(self):
        cc, prompts = self._cruxcheck(60)            # gap 10 < GAP_MIN: nothing held for the referee to read
        self.assertFalse(cc["ran"])
        self.assertEqual(prompts, [])


class GenericCruxKeywordTests(unittest.TestCase):
    """09-11 c9 q5 (OpenAI Pro, policy_analyst / user_agent): the only crux hit was the GPT-6 Astra launch headline,
    which says nothing about Pro capacity or reopening. It matched on the crux entity Astra, the pinned OpenAI and
    the keyword 'next' ('within the next week'), and the gate qualified it as crux_data. Time and generic words
    (next, week, remain, user, accept, offer) are no crux keywords, and where the cruxes have keywords a crux entity
    needs one beside it (entity_backed). The Pro-hold line (Astra + 'demand' + OpenAI Pro) stays a hit.
    Cruxes, question and lines are the real c9 ones."""

    Q = "Will OpenAI resume new Pro subscription sign-ups by 2026-09-18?"
    CRUXES = [
        "OpenAI can add enough usable capacity and manage Astra demand to reopen at least some new Pro sign-ups by "
        "2026-09-18.",
        "OpenAI’s subscription-availability page accepting new Pro subscriptions, or an official announcement "
        "that sign-ups have resumed.",
        "The official subscription page remains closed to new Pro users with no reopening announcement.",
        "OpenAI can add enough usable capacity within the next week to reopen new Pro sign-ups before 2026-09-18.",
        "OpenAI's subscription-availability page or an official announcement showing new Pro sign-ups have resumed.",
        "An official OpenAI announcement or subscription page offering new Pro purchases again.",
    ]
    HOLD = "- [Thu, 10 Sep 2026] OpenAI puts Pro subscriptions on hold due to Astra demand"
    LAUNCH = "- [Fri, 11 Sep 2026] GPT-6 Astra: The next generation in intelligence for work - OpenAI"

    def setUp(self):
        from types import SimpleNamespace
        from recon.orchestrator import Run
        filler = [f"- [wire] Unrelated headline number {i} about markets and weather" for i in range(150)]
        pkg = "\n".join(["# SECTION 4: NEWS INTELLIGENCE"] + filler[:75] + [self.HOLD, self.LAUNCH] + filler[75:])
        self.docs = {"raw": "", "social": "", "package": pkg}
        self.loc = evidence.Locator({"package": pkg})
        self.terms = Run.search_terms(SimpleNamespace(corpus_docs=lambda: self.docs), self.CRUXES, self.Q, "event")

    def test_time_and_generic_words_are_no_crux_keywords(self):
        kws = self.terms["keywords"]
        for w in ("next", "week", "remain", "user", "accept", "offer"):
            self.assertNotIn(debate._kw_base(w), kws, w)
        for w in ("capacity", "demand", "reopen"):
            self.assertIn(debate._kw_base(w), kws, w)
        self.assertEqual(debate.keyword_hits(self.LAUNCH, kws), [])

    def test_launch_headline_is_no_hit_and_no_qualifying_quote(self):
        res = debate.crux_search(self.terms, self.docs, [], self.loc)
        self.assertEqual([h["text"] for h in res["hits"]], [self.HOLD])
        launch = self.LAUNCH[2:]
        self.assertFalse(debate.shares_specific(launch, self.terms, "event"))
        self.assertTrue(debate.shares_specific(self.HOLD[2:], self.terms, "event"))
        # the same entity alone still counts where the cruxes have no keywords (threshold and direction questions)
        bare = {k: v for k, v in self.terms.items() if k != "keywords"}
        self.assertTrue(debate.shares_specific(launch, bare, "event"))


class MarketQuestionLineTests(unittest.TestCase):
    """Fourth review #48, closed at the reader (ninth review): a POLYMARKET LIVE MARKETS question line
    ('- US x Iran Effective Ceasefire by September 4?') carries its odds on the next line, and the package copies
    such question lines into CROSS-SOURCE SIGNALS without them. Every reader (ev_class, the gate's strict
    qualification, crux_search, the referee's quote check) used to look at the bare line and class it data: on
    09-10 the Iran line was the only crux hit and qualified a Hormuz move (70 -> 55), and the LAPTOP $500M copy
    qualified a threshold move on its number alone."""

    IRAN = "US x Iran Effective Ceasefire by September 4?"
    LAPTOP = "LAPTOP FDV above $500M one day after launch?"
    CRUX = ["An effective US Iran ceasefire by September 4 would reopen Hormuz shipping lanes."]

    def setUp(self):
        pkg = read(FIX / "2026-09-10" / "00_data_package.md")
        self.docs = {"package": pkg, "raw": "", "social": ""}
        self.loc = evidence.Locator({"package": pkg})
        t = debate.drop_frequent_entities(debate.crux_terms(self.CRUX), self.docs, subject=["Hormuz"])
        t["keywords"] = debate.crux_keywords(self.CRUX, "Will Hormuz reopen to commercial shipping?")
        self.event_terms = t

    def test_question_line_and_its_copy_are_market_class(self):
        self.assertEqual(self.loc.line_text("package", 374).strip(), "- " + self.IRAN)
        self.assertTrue(debate.market_at(self.loc, "package", 374))
        self.assertEqual(debate.ev_class(self.loc.locate(self.IRAN), self.loc), "market")
        self.assertEqual(self.loc.locate(self.LAPTOP)["line"], 32)        # the CROSS-SOURCE copy, no odds under it
        self.assertEqual(debate.ev_class(self.loc.locate(self.LAPTOP), self.loc), "market")
        self.assertEqual(debate.ev_class(self.loc.locate("- Base: TVL $5,643,886,489"), self.loc), "data")

    def test_not_a_crux_hit_and_never_qualifies(self):
        res = debate.crux_search(self.event_terms, self.docs, [], self.loc)
        self.assertFalse([h for h in res["hits"] if "Ceasefire" in h["text"]])
        self.assertFalse(debate.crux_search(self.event_terms, self.docs, [])["hits"])     # without a locator too
        m = debate.gate_move("a", 70, 40, {"q": 70}, 30, "narrow", [{"quote": self.IRAN}], [], [], [],
                             self.event_terms, self.loc, kind="event")
        self.assertFalse(m["new_evidence"][0]["qualifies"])
        self.assertEqual(m["new_evidence"][0]["cls"], "market")
        self.assertEqual(m["gated"], 70 - debate.FREE_MOVE)
        t = debate.drop_frequent_entities(debate.crux_terms(["LAPTOP FDV above $500M one day after launch"]), self.docs)
        m = debate.gate_move("a", 40, 70, {"q": 40}, 80, "narrow", [{"quote": self.LAPTOP}], [], [], [], t, self.loc)
        self.assertFalse(m["new_evidence"][0]["qualifies"])
        self.assertEqual(m["new_evidence"][0]["why_not"], "prediction-market odds line")
        rq = debate.quote_qualifies(self.IRAN, self.event_terms, "event", self.loc)
        self.assertFalse(rq["qualifies"])
        self.assertTrue(rq["market"])

    def test_no_market_question_line_is_data_on_any_fixture(self):
        for day, name, doc in (("2026-09-10", "00_data_package.md", "package"), ("2026-09-11", "00_data_package.md",
                               "package"), ("2026-09-11", "00_raw_data.md", "raw"), ("2026-10-04", "00_data_package.md",
                               "package"), ("2026-10-04", "00_raw_data.md", "raw")):
            text = read(FIX / day / name)
            loc = evidence.Locator({doc: text})
            lines = text.split("\n")
            qs = [n for n in range(1, len(lines)) if lines[n - 1].strip().startswith("- ")
                  and debate._ODDS_CONT.match(lines[n])]
            with self.subTest(day=day, doc=doc):
                self.assertTrue(qs)
                self.assertEqual([n for n in qs if debate.ev_class(loc.locate(lines[n - 1].strip()), loc) != "market"], [])
        lines = debate.market_lines(self.loc)
        self.assertIn(f"- {self.IRAN} — YES: 62% | 24h vol: $511,565 | total vol: $1,300,401 | liq: $59,632", lines)


class DroppedPairTests(unittest.TestCase):
    """§11.1 rule 2: a split whose candidate pair the budget, ceiling or load cap dropped keeps a block on the
    split_unpaired bar (review 2026-10-04: budget_pairs(12, 24, 2, 3) gave up q3, and q3 left the brief)."""

    def test_budget_dropped_pair_keeps_its_block(self):
        self.assertEqual(debate.budget_pairs(12, 24, 2, 3), (2, True))
        v1 = dict(zip(AG, [10, 20, 30, 70, 80, 90, 85, 15, 75]))
        v2 = dict(zip(AG, [80, 25, 70, 20, 30, 85, 15, 75, 90]))
        v3 = dict(zip(AG, [66, 70, 40, 62, 75, 68, 64, 72, 60]))        # one lens at 40, median 66, range 35
        vals = {"q1": v1, "q2": v2, "q3": v3}
        qs, p, e = setup(vals, weights={"q1": 3, "q2": 3, "q3": 2})
        res = debate.pair(qs, p, e, list(AG), "normal", 2)
        full = debate.pair(qs, p, e, list(AG), "normal", 3)
        self.assertEqual(sorted(x["question_id"] for x in res["pairs"]), ["q1", "q2"])
        self.assertIn("q3", [x["question_id"] for x in full["pairs"]])
        unp = debate.dropped_unpaired(res, full, "budget", p)
        self.assertEqual([(u["question_id"], u["range"]) for u in unp], [("q3", 35)])
        self.assertIn("budget", unp[0]["reason"])
        qrec = [{"id": qid, "text": f"Will {qid} happen by 10-11?", "weight": w, "resolves_on": "2026-10-11",
                 "settles_with": "a print", "ledger_id": f"x-{qid}"} for qid, w in (("q1", 3), ("q2", 3), ("q3", 2))]
        tp = {a: {qid: vals[qid][a] for qid in vals} for a in AG}
        takes = {a: {"positions": [{"question_id": qid, "probability": tp[a][qid], "reason": f"r {qid}",
                                    "evidence": [{"section": "", "quote": "- Current: $86,610,000,000"}]}
                                   for qid in vals], "summary": "", "claims": []} for a in AG}
        debates = [{"question_id": x["question_id"], "high": x["high"], "low": x["low"], "gap_before": x["gap"],
                    "gap_after": x["gap"], "in_split": True, "live_split": True, "held_split": False,
                    "crux_agreed": False, "narrowed_on_data": False} for x in res["pairs"]]
        sh = debate.split_sheet("2026-10-04", "r", "debate", qrec, tp, tp, debates, {}, {}, takes, locator(), 20,
                                unpaired=[u["question_id"] for u in unp])
        self.assertEqual(sorted(b["question_id"] for b in sh["blocks"]), ["q1", "q2", "q3"])
        b3 = next(b for b in sh["blocks"] if b["question_id"] == "q3")
        self.assertEqual((b3["type"], b3["debated"]), ("direction", False))
        schemas.validate(json.loads(json.dumps(sh)), schemas.ARTIFACTS["split_sheet"])
        # a question that was never a candidate pair keeps the strict rule (range >= 40, minority >= 2)
        sh = debate.split_sheet("2026-10-04", "r", "debate", qrec, tp, tp, debates, {}, {}, takes, locator(), 20)
        self.assertNotIn("q3", [b["question_id"] for b in sh["blocks"]])

    def test_load_cap_drop_is_unpaired(self):
        vals = {qid: {"trader": 10, "analyst": 50, "skeptic": 90} for qid in ("q1", "q2", "q3")}
        qs, p, e = setup(vals)
        r = debate.pair(qs, p, e, list(p), "normal", 3)
        self.assertEqual(len(r["pairs"]), 2)
        self.assertEqual([(u["question_id"], u["reason"]) for u in r["unpaired"]], [("q3", debate.UNPAIRED_LOAD_CAP)])
        self.assertEqual([u["question_id"] for u in debate.dropped_unpaired(r, r, "budget")], ["q3"])

    def test_no_candidate_split_is_unpaired_on_a_debate_day(self):
        # q2's only dissenter (builder at 40) is social-only on a threshold question: no candidate pair. Alone it
        # is a split_unpaired day with a block; beside a debated q1 it must keep that block (review 2026-10-04).
        v1 = dict(zip(AG, [10, 20, 30, 70, 80, 90, 85, 15, 75]))
        v2 = dict(zip(AG, [66, 70, 40, 62, 75, 68, 64, 72, 60]))        # builder at 40, median 66, range 35
        qrec = [{"id": qid, "text": f"Will {qid} happen by 10-11?", "weight": 2, "resolves_on": "2026-10-11",
                 "settles_with": "a print", "ledger_id": f"x-{qid}"} for qid in ("q1", "q2")]

        def sheet(vals):
            qs, p, e = setup(vals)
            if "q2" in vals:
                e["builder"]["q2"] = ev(cls="social")
            res = debate.pair(qs, p, e, list(AG), "normal", 3)
            tp = {a: {qid: vals[qid][a] for qid in vals} for a in AG}
            takes = {a: {"positions": [{"question_id": qid, "probability": tp[a][qid], "reason": f"r {qid}",
                                        "evidence": [{"section": "", "quote": "- Current: $86,610,000,000"}]}
                                       for qid in vals], "summary": "", "claims": []} for a in AG}
            debates = [{"question_id": x["question_id"], "high": x["high"], "low": x["low"], "gap_before": x["gap"],
                        "gap_after": x["gap"], "in_split": True, "live_split": True, "held_split": False,
                        "crux_agreed": False, "narrowed_on_data": False} for x in res["pairs"]]
            sh = debate.split_sheet("2026-10-04", "r", res["day_type"], [q for q in qrec if q["id"] in vals], tp, tp,
                                    debates, {}, {}, takes, locator(), 20,
                                    unpaired=[u["question_id"] for u in res["unpaired"]])
            return res, sorted(b["question_id"] for b in sh["blocks"])

        res, blocks = sheet({"q2": v2})
        self.assertEqual((res["day_type"], [u["question_id"] for u in res["unpaired"]], blocks),
                         ("split_unpaired", ["q2"], ["q2"]))
        alone_reason = res["unpaired"][0]["reason"]
        res, blocks = sheet({"q1": v1, "q2": v2})
        self.assertEqual(res["day_type"], "debate")
        self.assertEqual([x["question_id"] for x in res["pairs"]], ["q1"])
        self.assertEqual([(u["question_id"], u["range"], u["reason"]) for u in res["unpaired"]],
                         [("q2", 35, alone_reason)])
        self.assertEqual(blocks, ["q1", "q2"])


COLLECTORS = REPO / "tests" / "fixtures" / "collectors"
STARSHIP = ('- [Science and Technology] SpaceX Starship 15th launch? (SpaceX Starship (15th launch)): "Before Oct 30, '
            '2026" 11% (24h -22 pts) | 24h vol 7,141 | https://kalshi.com/markets/kxspacexstarship')


def dedup_package() -> tuple[dict, str]:
    """The pipeline's SECTION 0 on the Polymarket + Kalshi replays: collect_data.sh runs scripts/deduplicate.py on
    the raw file (the raw Polymarket and Kalshi blocks) and puts its CROSS-SOURCE SIGNALS report before SECTION 8.
    Returns (docs, the dedup report)."""
    import deduplicate
    pm, ks = read(COLLECTORS / "replay_polymarket.md"), read(COLLECTORS / "replay_kalshi.md")
    raw = "# RAW DATA -- 2026-10-04\n\n---\n\n" + pm + "\n\n---\n\n" + ks + "\n"
    rep = deduplicate.format_deduplicated(deduplicate.deduplicate(deduplicate.extract_items(raw)))
    pkg = ("# RECON INTELLIGENCE PACKAGE -- 2026-10-04\n\n---\n\n# SECTION 0: CROSS-SOURCE SIGNALS\n\n" + rep
           + "\n---\n\n# SECTION 4: NEWS INTELLIGENCE\n\n- [Sat, 03 Oct 2026] SpaceX moves Starship flight 15 to the "
           "Oct 30 window after a pad repair\n\n---\n\n# SECTION 8: PREDICTION MARKETS\n\n" + pm + "\n\n" + ks + "\n")
    return {"package": pkg, "raw": raw, "social": ""}, rep


class CrossSourceMarketCopyTests(unittest.TestCase):
    """Phase C (2026-10-04): deduplicate.py copies prediction-market lines into SECTION 0 CROSS-SOURCE SIGNALS, and
    Locator.strict / locate pick that first data hit, not the SECTION 8 original. A copy is market by its _core text
    (market_question_keys covers every PREDICTION MARKETS list item) and by content (_ODDS knows the collector formats:
    '(24h +53 pts)', 'Above 4.00% 14%', 'mid 9.5¢', NEW MARKETS 'started ... | 24h vol')."""

    def setUp(self):
        self.docs, self.rep = dedup_package()
        self.loc = evidence.Locator({k: v for k, v in self.docs.items() if k != "social"})
        self.items = [l for l in self.rep.split("\n") if l.startswith("- ")]

    def test_every_cross_source_copy_is_market(self):
        self.assertGreaterEqual(len(self.items), 15)
        self.assertIn(STARSHIP, self.items)
        checked = 0
        for item in self.items:
            st = self.loc.strict(item)
            if not st["ok"]:                                       # 'by...?' reads as a stitched quote
                continue
            checked += 1
            with self.subTest(item=item[:80]):
                self.assertEqual(st["section"], "CROSS-SOURCE SIGNALS")   # the copy is the hit that classifies
                self.assertTrue(debate.market_at(self.loc, st["doc"], st["line"], st["section"]))
                self.assertEqual(debate.ev_class(self.loc.locate(item), self.loc), "market")
        self.assertGreaterEqual(checked, 15)

    def test_copy_is_market_by_section_key_alone(self):
        # Without the content rule the SECTION 8 original still makes the copy market (its _core is a key).
        keys = debate.market_question_keys(self.loc.lines)
        st = self.loc.strict(STARSHIP)
        lines = self.loc.lines[st["doc"]]
        self.assertIn(evidence._core(STARSHIP), keys)
        self.assertTrue(debate.market_line_in("CROSS-SOURCE SIGNALS", lines, st["line"] - 1, keys))
        self.assertFalse(debate.market_line_in("NEWS INTELLIGENCE", ["- SpaceX moves Starship flight 15 to the Oct 30 "
                                                                    "window after a pad repair"], 0, keys))

    def test_collector_formats_are_odds_lines(self):
        for line in ('- [Companies] Amazon Credit Card Spend in September (September 2026): "Above 108" 76% (24h +53 pts)',
                     '- Fed funds rate after Oct 2026 meeting? (On Oct 28, 2026): Above 3.75% 99.5% · Above 4.00% 14%',
                     '- Will Indiana enact a data center moratorium by December 31, 2027?: mid 9.5¢ | spread 1.0¢',
                     "- Gemini Argon: Humanity's Last Exam Debut? — started 2026-10-02 22:17 UTC | 24h vol $21K | liq $3K",
                     STARSHIP):
            with self.subTest(line=line[:60]):
                self.assertTrue(debate.odds_line(line))
        for line in ("- BTC: $84,790 (-0.4% 24h)", "- SOL: $150.20 +3.2% (24h)",
                     "- [coindesk.com] Bitcoin Climbs Above $84,000, Up 5% On The Week",
                     "- Ethena USDe supply fell 4.1% to $5.3B over seven days on redemptions"):
            with self.subTest(line=line):
                self.assertFalse(debate.odds_line(line))

    def test_starship_copy_never_qualifies_hits_or_confirms(self):
        terms = debate.drop_frequent_entities(debate.crux_terms(["SpaceX Starship 15th launch before Oct 30, 2026"]),
                                              self.docs, subject=["Starship"])
        self.assertTrue(debate.shares_specific(STARSHIP, terms, "event"))   # it is about the crux ...
        m = debate.gate_move("trader", 30, 45, {"q1": 30}, 70, "narrow", [{"section": "", "quote": STARSHIP}],
                             [], [], [], terms, self.loc, kind="event")
        self.assertFalse(m["new_evidence"][0]["qualifies"])                # ... and never qualifies a move
        self.assertEqual(m["new_evidence"][0]["cls"], "market")
        res = debate.crux_search(terms, self.docs, [], self.loc)
        self.assertFalse([h for h in res["hits"] if "kxspacexstarship" in h["text"]])
        qq = debate.quote_qualifies(STARSHIP, terms, "event", self.loc)
        self.assertTrue(qq["market"])
        self.assertFalse(qq["qualifies"])

    def test_headline_odds_are_market_evidence_not_gate_markets(self):
        pkg = read(FIX / "2026-09-11" / "00_data_package.md")
        loc = evidence.Locator({"package": pkg})
        line = "- [Fri, 11 Sep 2026] Brent Tops $106 And Hike Odds Reach 64% As Crypto Sells Off"
        self.assertEqual(debate.ev_class(loc.locate(line), loc), "market")
        terms = debate.drop_frequent_entities(debate.crux_terms(["Fed hike odds above 60% before the September meeting"]),
                                              {"package": pkg})
        self.assertFalse(debate.quote_qualifies(line, terms, "threshold", loc)["qualifies"])
        self.assertNotIn(line, debate.market_lines(loc))                  # a headline is not a priced market
        self.assertFalse(debate.odds_line(line))


class ScorecardExpiryTests(unittest.TestCase):
    """09-11 c9 SCORECARD: the synthesizer computed the expiries itself and shipped 'Over the next session, ...'
    (made 2026-09-10) as PENDING (expiry 2029-09-10), three years instead of one day; the claims check counted
    numbers only, so the date passed. Expiries are computed in code and dates are claims (§11.4, §11.5, §20.7 #79)."""
    ANALYST = ("- [2026-09-10] Over the next session, live ETF flows, entity-adjusted whale data, options positioning, "
               "and stress-depth persistence will determine whether exposure changes; tokenized-asset value capture "
               "will increasingly accrue to reliable trading surfaces rather than issuance chains over the next 2–3 years.")
    CARD = ("## Market Snapshot\n- bitcoin: $76,933.00\n\n## Pending Predictions\n"
            "*Agents: review your predictions below.*\n\n### ANALYST\n" + ANALYST + "\n\n### BUILDER\n"
            "- [2026-09-10] Within the next MVP cycle, no regulated venue will validate live quotes.\n\n"
            "### MACRO_STRATEGIST\n- [2026-09-10] Over the next 3–6 months, regulated rails should outperform.\n\n"
            "### TRADER\n- [2026-09-10] Over the next 1–4 weeks, BTC realized volatility should remain elevated.\n"
            "- [2026-09-10] Through the Fed/Hormuz window, expect elevated liquidation risk; spot demand remains "
            "unconfirmed over the next 1–2 weeks.\n\n"
            "### USER\n- [2026-09-10] Within the next week, incentive-driven volume will prove weaker.\n")

    def synth(self, card=None):
        import types
        from recon import orchestrator
        return orchestrator.Run.synth_scorecard(types.SimpleNamespace(scorecard=lambda: card or self.CARD))

    def test_expiry_is_computed_from_the_first_horizon(self):
        self.assertEqual(evidence.prediction_expiry(self.ANALYST[15:], "2026-09-10"),
                         {"horizon": "Over the next session", "expiry": "2026-09-11"})
        for text, exp in (("Over the next 3–6 months, x", "2027-03-10"), ("Over 3-6 months, x", "2027-03-10"),
                          ("Over the next 1–4 weeks, x", "2026-10-08"), ("Within the next week, x", "2026-09-17"),
                          ("Through the Fed window, y over the next 1–2 weeks.", "2026-09-24"),
                          ("within 72 hours of the deadline", "2026-09-13"), ("within 90 days", "2026-12-09"),
                          ("over the next 2–3 years", "2029-09-10"), ("legislation by August 2026", "2026-08-31"),
                          ("Iran resolves by April 17", "2027-04-17"), ("tokenization by Q3 2026", "2026-09-30")):
            self.assertEqual(evidence.prediction_expiry(text, "2026-09-10")["expiry"], exp, text)
        for text in ("Within the next MVP cycle, no venue", "The narrative should accelerate short term", ""):
            self.assertEqual(evidence.prediction_expiry(text, "2026-09-10")["expiry"], "", text)

    def test_synthesizer_reads_the_computed_expiry(self):
        out = self.synth()
        line = next(l for l in out.splitlines() if "Over the next session" in l)
        self.assertIn('expiry 2026-09-11, from "Over the next session"', line)
        self.assertNotIn("2029", out)
        self.assertIn("expiry 2027-03-10", out)
        self.assertIn("expiry 2026-10-08", out)
        self.assertIn("expiry 2026-09-24", out)
        self.assertIn("expiry 2026-09-17", out)
        mvp = next(l for l in out.splitlines() if "MVP cycle" in l)
        self.assertIn("no expiry: no dated horizon", mvp)
        self.assertNotIn("USER", out)                                  # the #67 header rule still holds

    def test_sub_bullets_take_their_parent_date(self):
        out = self.synth("## Pending Predictions\n### TRADER\n- [2026-06-20] **\n"
                         "  - Strategy forced selling within 60 days if BTC drops\n  - no horizon here\n")
        self.assertIn("within 60 days if BTC drops (expiry 2026-08-19", out)
        self.assertIn("- [2026-06-20] **\n", out + "\n")             # a dated line with no text gets no note

    def test_claims_check_covers_dates(self):
        src = {"scorecard": self.CARD, "synth_scorecard": self.synth()}
        bad = evidence.brief_claims("### SCORECARD\n- ETF, whale and options signals — **PENDING (expiry: 2029-09-10).**\n",
                                    src, {}, {})
        self.assertEqual(len(bad), 1)
        self.assertFalse(bad[0]["found_in_source"])
        self.assertIn("2029-09-10", bad[0]["action"])
        self.assertEqual(bad[0]["dates"], ["2029-09-10"])
        good = evidence.brief_claims("### SCORECARD\n- ETF, whale and options signals — **PENDING (expiry: 2026-09-11).**\n"
                                     "- Volatility stays elevated — PENDING (expiry October 8, 2026).\n"
                                     "- Liquidation risk elevated — PENDING until Sept. 24.\n", src, {}, {})
        self.assertEqual([c["found_in_source"] for c in good], [True, True, True])
        derived = evidence.brief_claims("### WHAT TO WATCH\n- The Fed decides on September 16.\n", src,
                                        {"split": "settles on 2026-09-16"}, {})
        self.assertTrue(derived[0]["action"].startswith("note"))
        self.assertEqual(evidence.dates("May 5 may be late; the 2026-09-10 call; 17 September 2026"),
                         [{"raw": "May 5", "key": "05-05", "year": ""}, {"raw": "2026-09-10", "key": "09-10", "year": "2026"},
                          {"raw": "17 September 2026", "key": "09-17", "year": "2026"}])
