"""Kalshi (spec §3.2)."""
from __future__ import annotations

import re
import unittest
from datetime import timedelta

from helpers import NOW, CollectorCase, cc, section
import collect_kalshi as ks

ISO = cc.iso_z


def market(ticker, bid=None, ask=None, last=None, prev=None, vol=0, close=None, **kw):
    m = {"ticker": ticker, "yes_bid_dollars": bid, "yes_ask_dollars": ask, "last_price_dollars": last,
         "previous_price_dollars": prev, "volume_24h_fp": str(vol), "status": "active",
         "close_time": close or ISO(NOW + timedelta(days=3))}
    m.update(kw)
    return m


class PriceTests(unittest.TestCase):
    def test_mid_or_last(self):
        self.assertAlmostEqual(ks.prob(market("a", "0.40", "0.44", "0.10")), 0.42)
        self.assertAlmostEqual(ks.prob(market("b", "0.10", "0.40", "0.33")), 0.33)  # spread 30c
        self.assertAlmostEqual(ks.prob(market("c", None, "0.40", "0.21")), 0.21)
        self.assertIsNone(ks.prob(market("d")))

    def test_change(self):
        self.assertAlmostEqual(ks.change_pts(market("a", last="0.55", prev="0.50")), 5.0)
        self.assertIsNone(ks.change_pts(market("b", last="0.55", prev="0")))
        fresh = market("c", last="0.99", prev="0.01", open_time=ISO(NOW - timedelta(hours=3)))
        self.assertIsNone(ks.change_pts(fresh, NOW))
        self.assertEqual(ks.ch_str(None), "24h n/a, new")

    def test_ladder_interpolation(self):
        strikes = [(100, .9), (110, .7), (120, .45), (130, .2), (140, .05)]
        mk = [market(f"K{k}", last=str(p), floor_strike=k, strike_type="greater") for k, p in strikes]
        ev = ks.live_events([{"event_ticker": "KXBTCD-26OCT0417", "series_ticker": "KXBTCD", "markets": mk}],
                            NOW)[0]
        line = ks.ladder_line("BTC", ev)
        self.assertIn("median $118 ", line)         # 110 + (0.7 - 0.5) / (0.7 - 0.45) x 10
        self.assertIn("(25–75 %: $108–$128)", line)  # 107.5 rounds to even, 128 exact
        self.assertIn("from 5 strikes", line)

    def test_closed_events_dropped(self):
        evs = ks.live_events([
            {"event_ticker": "PAST", "markets": [market("p", close=ISO(NOW - timedelta(minutes=1)))]},
            {"event_ticker": "NOW", "markets": [market("n", close=ISO(NOW))]},
            {"event_ticker": "LIVE", "markets": [market("l", close=ISO(NOW + timedelta(hours=1)))]}], NOW)
        self.assertEqual([e["event_ticker"] for e in evs], ["LIVE"])

    def test_category_is_url_encoded(self):
        url = cc.build_url(f"{ks.BASE}/series", {"category": "Science and Technology", "include_volume": "true"})
        self.assertNotIn(" ", url)
        self.assertIn("category=Science+and+Technology", url)


class WalkTests(CollectorCase):
    CAT = "Economics"

    def series_url(self):
        return cc.build_url(f"{ks.BASE}/series", {"category": self.CAT, "include_volume": "true"})

    def events_url(self, t):
        return cc.build_url(f"{ks.BASE}/events", {"series_ticker": t, "status": "open",
                                                  "with_nested_markets": "true", "limit": 10})

    def walk(self, series, events):
        bodies = {self.series_url(): {"series": series}}
        bodies.update({self.events_url(t): {"events": e} for t, e in events.items()})
        self.overlay(bodies=bodies, empty=True)
        notes = []
        out = ks.category_walk(self.CAT, {"KXFEDDECISION"}, cc.Budget(60, 99, 10), NOW, notes)
        return out, notes

    def test_skips_fast_series_watchlist_and_dead_series(self):
        live = [{"event_ticker": "KXLIVE-1", "markets": [market("x")]}]
        dead = [{"event_ticker": "KXDEAD-1", "markets": [market("y", close=ISO(NOW - timedelta(hours=1)))]}]
        series = [{"ticker": "KXBTC15M", "frequency": "fifteen_min", "volume_fp": "9e9"},
                  {"ticker": "KXETH1H", "frequency": "daily", "volume_fp": "8e9"},
                  {"ticker": "KXHOURLY", "frequency": "hourly", "volume_fp": "7e9"},
                  {"ticker": "KXFEDDECISION", "frequency": "custom", "volume_fp": "6e9"},
                  {"ticker": "KXDEAD", "frequency": "monthly", "volume_fp": "5e9"},
                  {"ticker": "KXLIVE", "frequency": "monthly", "volume_fp": "4e9"}]
        out, notes = self.walk(series, {"KXDEAD": dead, "KXLIVE": live})
        self.assertEqual([t for t, _, _ in out], ["KXLIVE"])
        self.assertIn(f"{self.CAT}: kept 1 of 2 probed series", notes)

    def test_stops_at_twelve_probes_and_five_kept(self):
        dead = [{"event_ticker": "D", "markets": [market("y", close=ISO(NOW - timedelta(hours=1)))]}]
        series = [{"ticker": f"KXD{i:02d}", "frequency": "monthly", "volume_fp": str(100 - i)} for i in range(20)]
        _, notes = self.walk(series, {f"KXD{i:02d}": dead for i in range(20)})
        self.assertIn(f"{self.CAT}: kept 0 of 12 probed series", notes)
        live = [{"event_ticker": "L", "markets": [market("x")]}]
        out, notes = self.walk(series, {f"KXD{i:02d}": live for i in range(20)})
        self.assertEqual(len(out), 5)
        self.assertIn(f"{self.CAT}: kept 5 of 5 probed series", notes)


class ReplayTests(CollectorCase):
    def test_sections(self):
        code, text, status, _ = self.run_collector("kalshi")
        self.assertEqual(code, 0)
        self.assertEqual(len(section(text, "## TOP EVENTS")), 12)
        self.assertTrue(section(text, "## MACRO"), "watchlist KXFEDDECISION/KXFED")
        for l in section(text, "## 24H MOVERS"):
            self.assertGreaterEqual(abs(int(re.search(r"\(24h ([+-]\d+) pts\)", l).group(1))), 5, l)
        for l in section(text, "## CLOSING"):
            m = re.search(r"next close (\d{4}-\d\d-\d\d) (\d\d:\d\d) UTC", l)
            close = cc.to_utc(f"{m.group(1)}T{m.group(2)}:00Z")
            self.assertTrue(NOW < close <= NOW + timedelta(days=7), l)
        ladders = section(text, "## CRYPTO PRICE LADDERS")
        self.assertEqual(len(ladders), 2)
        for l in ladders:
            m = re.search(r"at (\d{4}-\d\d-\d\d) (\d\d:\d\d) UTC", l)
            self.assertGreaterEqual(cc.to_utc(f"{m.group(1)}T{m.group(2)}:00Z"), NOW + timedelta(hours=12), l)
        self.assertTrue((cc.DATA_DIR / "kalshi" / "prev.json").exists())


if __name__ == "__main__":
    unittest.main()
