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
      domain="markets_crypto", lenses=None):
    return {"id": "", "text": text, "kind": kind, "domain": domain, "metric": "", "comparator": "", "threshold": "",
            "baseline_quote": bq, "resolves_on": resolves, "settles_with": "DeFiLlama", "lenses": lenses or [],
            "weight": weight, "carried_from": carried}


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
        self.assertEqual(debate.budget_pairs(10, 24, 2, 3), (3, False))   # 12 free
        self.assertEqual(debate.budget_pairs(11, 24, 2, 3), (2, True))    # 11 free
        self.assertEqual(debate.budget_pairs(9, 24, 2, 3), (3, True))     # 13 free

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
        self.assertIn("soft move", m["flags"])

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
        self.assertTrue(s["live_split"])            # not confirmed: the block stays
        self.assertTrue(s["narrowed_on_data"])
        s = debate.score_debate(PR, {}, resp, {"resolved": "yes", "leans": "lower"}, 20)
        self.assertFalse(s["live_split"])
        s = debate.score_debate(PR, {}, resp, {"resolved": "partly", "leans": "lower"}, 20)
        self.assertFalse(s["live_split"])
        s = debate.score_debate(PR, {}, resp, {"resolved": "partly", "leans": "higher"}, 20)
        self.assertTrue(s["live_split"])

    def test_useful_on_crux_check(self):
        pr = {**PR, "p_high": 58, "p_low": 40}
        resp = {"high": side_rec(58, 55), "low": side_rec(40, 42)}
        neither = debate.score_debate(pr, {}, resp, {"resolved": "partly", "leans": "neither"}, 20)
        higher = debate.score_debate(pr, {}, resp, {"resolved": "partly", "leans": "higher"}, 20)
        self.assertFalse(neither["useful"])
        self.assertTrue(higher["useful"])


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

    def test_entity_alone_does_not_qualify(self):
        t = debate.crux_terms(["Kalshi volume rises"])
        self.assertFalse(debate.shares_specific("- Kalshi announced a new office in Seoul today", t))
        self.assertTrue(debate.shares_specific("- Kalshi volume hit $1.2B this week", t))


# ── stats, split sheet, notes, checks, adapters ─────────────────────────────────────────

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


if __name__ == "__main__":
    unittest.main()
