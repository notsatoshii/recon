"""Phase D step 1: scripts/resolve_ledger.py (docs/v2/phase-d-debate-upgrade.md). Fixture series and package
folders, no network, no model call."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO), str(REPO / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from recon import evidence, schemas  # noqa: E402
import resolve_ledger as rl  # noqa: E402

DAY_MS = 86400


def ts(day: str) -> int:
    from datetime import datetime, timezone
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


def qline(lid, day, text, kind="threshold", metric="", comparator="", threshold="", resolves_on="2026-10-18",
          baseline="", settles=""):
    return {"type": "question", "ledger_id": lid, "run_id": day, "day": day,
            "question": {"id": lid[-2:], "text": text, "kind": kind, "domain": "markets_crypto", "metric": metric,
                         "comparator": comparator, "threshold": threshold, "baseline_quote": baseline,
                         "settled_quote": "", "resolves_on": resolves_on, "settles_with": settles, "lenses": [],
                         "weight": 2, "carried_from": ""},
            "finals": {}, "take_values": {}, "final_stats": {}, "debated": False, "split_type": "none"}


class FakeFetcher(rl.Fetcher):
    def __init__(self, data: dict):
        super().__init__(net=True)
        self.data, self.urls = data, []

    def json(self, url):
        self.urls.append(url)
        for k, v in self.data.items():
            if k in url:
                return v
        return None


def pkg(tvl: str, dex: str, usdt: str = "$184,000,000,000") -> str:
    return (f"# RECON INTELLIGENCE PACKAGE\n# SECTION 0: CROSS-SOURCE\n## CROSS-SOURCE SIGNALS\n- 7d volume change: +1.0%\n"
            f"# SECTION 3: ON-CHAIN & MARKET DATA\n## TOTAL DEFI TVL\n- Current: {tvl}\n## DEX VOLUMES (24h)\n"
            f"- Total 24h DEX volume: $10,541,001,512\n- 7d volume change: {dex}\n## STABLECOIN SUPPLY\n"
            f"- Tether (USDT): {usdt}\n")


class LevelAndRuleTests(unittest.TestCase):
    def test_parse_level(self):
        self.assertEqual(rl.parse_level("$90 billion"), 9e10)
        self.assertEqual(rl.parse_level("-5%"), -5.0)
        self.assertEqual(rl.parse_level("5,000 stars"), 5000.0)
        self.assertEqual(rl.parse_level(" +13.7%"), 13.7)
        self.assertAlmostEqual(rl.parse_level("86.61B"), 8.661e10)
        self.assertEqual(rl.parse_level("0%"), 0.0)
        self.assertIsNone(rl.parse_level("no figure"))

    def test_window_rule(self):
        self.assertEqual(rl.window_rule("Will reported total DeFi TVL fall below $90 billion by October 18, 2026?"), "any")
        self.assertEqual(rl.window_rule("Will reported seven-day DEX volume growth remain negative through October 12?"), "all")
        self.assertEqual(rl.window_rule("Will BTC close above $80,000 on 2026-10-20?"), "on")

    def test_decide(self):
        s = {"2026-10-11": 91.0, "2026-10-12": 89.5, "2026-10-13": 92.0}
        self.assertEqual(rl.decide("any", "<", 90, s, "2026-10-10", "2026-10-18", "2026-10-13")[:2], ("yes", 89.5))
        self.assertEqual(rl.decide("any", "<", 80, s, "2026-10-10", "2026-10-18", "2026-10-13")[0], None)   # not yet
        s["2026-10-18"] = 91.0
        self.assertEqual(rl.decide("any", "<", 80, s, "2026-10-10", "2026-10-18", "2026-10-18")[0], "no")
        self.assertEqual(rl.decide("all", ">", 90, s, "2026-10-10", "2026-10-18", "2026-10-13")[0], "no")
        self.assertEqual(rl.decide("on", ">", 90, s, "2026-10-10", "2026-10-18", "2026-10-18")[0], "yes")
        # the question day itself is the baseline, never part of the window
        self.assertIsNone(rl.decide("any", "<", 90, {"2026-10-10": 80.0}, "2026-10-10", "2026-10-18", "2026-10-11")[0])

    def test_current_only(self):
        self.assertEqual(rl.decide("any", ">", 5000, {"2026-10-15": 5100.0}, "2026-10-05", "2026-10-19", "2026-10-15",
                                   current_only=True)[0], "yes")
        self.assertIsNone(rl.decide("any", ">", 5000, {"2026-10-15": 4100.0}, "2026-10-05", "2026-10-19", "2026-10-15",
                                    current_only=True)[0])
        self.assertEqual(rl.decide("any", ">", 5000, {"2026-10-19": 4100.0}, "2026-10-05", "2026-10-19", "2026-10-19",
                                   current_only=True)[0], "no")
        self.assertIsNone(rl.decide("any", ">", 5000, {"2026-10-21": 6100.0}, "2026-10-05", "2026-10-19", "2026-10-21",
                                    current_only=True)[0])   # crossed when? after resolves_on nothing is settled


class NumericTests(unittest.TestCase):
    def test_api_tvl_early_yes_and_resolution_schema(self):
        lines = [qline("2026-10-10-q1", "2026-10-10", "Will reported total DeFi TVL fall below $90 billion by October 18, 2026?",
                       metric="Total DeFi TVL, DeFiLlama", comparator="<", threshold="$90,000,000,000",
                       baseline="- Current: $91,525,837,954", settles="Reported total DeFi TVL in DeFiLlama")]
        tvl = [{"date": ts(d), "tvl": v} for d, v in (("2026-10-10", 91.5e9), ("2026-10-11", 91.0e9),
                                                       ("2026-10-14", 89.9e9), ("2026-10-15", 90.4e9))]
        f = FakeFetcher({"historicalChainTvl": tvl})
        res, notes = rl.resolve_numeric(lines, "2026-10-15", f, REPO)
        self.assertEqual(len(res), 1, notes)
        r = res[0]
        self.assertEqual((r["outcome"], r["method"], r["value"]), ("yes", "api", 89.9e9))
        self.assertIn("2026-10-14", r["source"])
        schemas.validate(json.loads(json.dumps(r)), schemas.ARTIFACTS["resolution_line"])
        line = dict(lines[0], type="resolution")
        self.assertEqual(schemas.ARTIFACTS["ledger_line"]["properties"]["type"]["enum"], ["question", "indicator", "resolution"])
        self.assertEqual(line["type"], "resolution")

    def test_package_lines_remain_negative(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for d, dex in (("2026-10-08", "-16.1%"), ("2026-10-09", "-15.1%"), ("2026-10-10", "-3.0%"),
                           ("2026-10-11", "-32.6%"), ("2026-10-12", "-17.5%")):
                (home / "briefs" / d).mkdir(parents=True)
                (home / "briefs" / d / "00_data_package.md").write_text(pkg("$91,000,000,000", dex), encoding="utf-8")
            (home / "config").mkdir()
            (home / "config" / "resolvers.json").write_text((REPO / "config" / "resolvers.json").read_text(encoding="utf-8"),
                                                            encoding="utf-8")
            lines = [qline("2026-10-08-q1", "2026-10-08", "Will reported seven-day DEX volume growth remain negative "
                           "through October 12, 2026?", kind="direction", comparator="<", threshold="0%",
                           metric="Reported total DEX volume seven-day percentage change, DeFiLlama",
                           resolves_on="2026-10-12", baseline="- 7d volume change: -16.1%"),
                     qline("2026-10-09-q1", "2026-10-09", "Will reported seven-day DEX volume growth turn positive by "
                           "2026-10-12?", kind="direction", comparator=">", threshold="0%",
                           metric="Reported seven-day DEX volume change, DeFiLlama", resolves_on="2026-10-12",
                           baseline="- 7d volume change: -15.1%")]
            res, notes = rl.resolve_numeric(lines, "2026-10-12", rl.Fetcher(net=False), home)
            got = {r["ledger_id"]: (r["outcome"], r["method"], r["value"]) for r in res}
            self.assertEqual(got["2026-10-08-q1"], ("yes", "package", -17.5))      # every day < 0 through 10-12
            self.assertEqual(got["2026-10-09-q1"], ("no", "package", -17.5))       # never > 0 by 10-12
            # the CROSS-SOURCE copy (+1.0%) is never read: the DEX VOLUMES line is

    def test_open_without_data_then_void(self):
        lines = [qline("2026-10-05-q4", "2026-10-05", "Will tokenized-equity market capitalization exceed $4.5 billion "
                       "by October 26, 2026?", metric="Tokenized stock market capitalization, Token Terminal",
                       comparator=">", threshold="$4.5 billion", resolves_on="2026-10-12")]
        res, notes = rl.resolve_numeric(lines, "2026-10-13", rl.Fetcher(net=False), REPO)
        self.assertEqual(res, [])
        self.assertIn("no resolver", notes[0]["note"])
        self.assertEqual(rl.void_lines(lines, "2026-10-19"), [])                # 7 days after: not yet
        v = rl.void_lines(lines, "2026-10-20")
        self.assertEqual((v[0]["outcome"], v[0]["method"]), ("void", "void_rule"))
        self.assertEqual(rl.open_questions(lines + v, "2026-10-21"), [])        # resolved lines are not reopened

    def test_resolver_map(self):
        rs = rl.load_resolvers(REPO)
        def name(text, metric=""):
            r = rl.match_resolver({"text": text, "metric": metric, "settles_with": ""}, rs)
            return r["name"] if r else None
        self.assertEqual(name("Will Tether supply exceed $190 billion by October 25?", "Tether USDT supply, DeFiLlama"), "usdt_supply")
        self.assertEqual(name("Will BTC trade below $75,000 by 2026-09-18?"), "btc_price")
        self.assertIsNone(name("Will BTC dominance exceed 60% by 2026-10-20?"))
        self.assertEqual(name("x", "Total DEX volume seven-day percentage change, DeFiLlama"), "dex_volume_change_7d")
        self.assertEqual(name("Will OpenDots exceed 5,000 GitHub stars by 2026-10-19?", "GitHub repository star count"),
                         "github_stars")


class EventTests(unittest.TestCase):
    PKG = ("# RECON INTELLIGENCE PACKAGE\n# SECTION 4: NEWS INTELLIGENCE\n"
           "- [coindesk.com] SEC publishes formal notice for its crypto custody proposal in the Federal Register "
           "https://www.coindesk.com/a/1234567\n"
           "- [Oct 17] (31♥ 3🔁) the SEC custody text is out, huge https://x.com/a/status/123456789012\n")

    def test_groups_and_checks(self):
        text = "Will the SEC publish operative text or a formal notice for its crypto custody proposal by 2026-10-19?"
        lines = [qline("2026-10-05-q3", "2026-10-05", text, kind="event", resolves_on="2026-10-19"),
                 qline("2026-10-06-q3", "2026-10-06", text, kind="event", resolves_on="2026-10-19"),
                 qline("2026-10-06-q2", "2026-10-06", "Will a new major DeFi exploit be reported by 2026-10-12?",
                       kind="event", resolves_on="2026-10-12")]
        groups = rl.event_groups(lines, "2026-10-17")
        self.assertEqual(len(groups), 1)                    # 10-12 + 1 day of lag has passed
        self.assertEqual((groups[0]["ref"], groups[0]["start"], len(groups[0]["lines"])), ("e1", "2026-10-05", 2))
        self.assertIn("[e1]", rl.event_items(groups))
        loc = evidence.Locator({"package": self.PKG, "raw": "", "view": "", "social": ""})
        good = "SEC publishes formal notice for its crypto custody proposal in the Federal Register"
        res, notes = rl.check_events({"items": [{"ref": "e1", "outcome": "yes", "quote": good, "section": "NEWS", "why": ""}]},
                                     groups, loc)
        self.assertEqual(sorted(r["ledger_id"] for r in res), ["2026-10-05-q3", "2026-10-06-q3"])
        self.assertEqual({r["method"] for r in res}, {"package_llm"})
        for bad in ("the SEC custody text is out, huge", "SEC formally adopted the custody rule today, per officials",
                    ""):
            res, notes = rl.check_events({"items": [{"ref": "e1", "outcome": "yes", "quote": bad, "section": "",
                                                     "why": ""}]}, groups, loc)
            self.assertEqual(res, [], bad)
        res, _ = rl.check_events({"items": [{"ref": "e1", "outcome": "open", "quote": good, "section": "", "why": ""}]},
                                 groups, loc)
        self.assertEqual(res, [])


class PhaseNeverFailsTests(unittest.TestCase):
    def test_resolve_phase_catches_everything(self):
        from recon.orchestrator import Run
        args = argparse.Namespace(as_of="2026-10-04", dry_run=True, replay=None, run_id="2026-10-04-rz", state_dir=None,
                                  from_phase=None, resume=False)
        r = Run(args)
        r.log = lambda msg: None

        def boom():
            raise ValueError("ledger exploded")
        r._resolve = boom
        out = r.ph_resolve()
        self.assertFalse(out["ok"])
        self.assertIn("ledger exploded", out["error"])


if __name__ == "__main__":
    unittest.main()
