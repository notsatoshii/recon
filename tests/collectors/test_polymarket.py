"""Polymarket (spec §3.1). Recorded on the droplet: Polymarket answers HTTP 451 from Korea."""
from __future__ import annotations

import re
import unittest
from datetime import timedelta
from unittest import mock

from helpers import MANIFEST, NOW, CollectorCase, cc, section, url_like
import collect_polymarket as pm


class FieldTests(unittest.TestCase):
    def test_json_strings_inside_json(self):
        self.assertEqual(pm.jlist('["0.87", "0.13"]'), ["0.87", "0.13"])
        self.assertEqual(pm.jlist(None), [])
        self.assertEqual(pm.jlist("not json"), [])

    def test_points_and_nulls(self):
        self.assertAlmostEqual(pm.pts(-0.3995), -39.95)
        self.assertEqual(pm.pts_s(None), "n/a")
        self.assertEqual(pm.pts_s(0.05), "+5 pts")
        self.assertEqual(pm.f(None), 0.0)
        self.assertEqual(pm.usd(1_630_000), "$1.63M")

    def test_sports_backstop(self):
        dota = {"tags": [{"slug": "esports"}, {"slug": "dota-2"}, {"slug": "games"}, {"slug": "sports"}]}
        self.assertTrue(pm.is_sports_event(dota))
        self.assertFalse(pm.is_sports_event({"tags": [{"slug": "crypto"}]}))
        ev = {"markets": [
            {"question": "game", "outcomePrices": '["0.5","0.5"]', "gameId": 123},
            {"question": "spread", "outcomePrices": '["0.5","0.5"]', "sportsMarketType": "spreads"},
            {"question": "bad prices", "outcomePrices": None},
            {"question": "closed", "outcomePrices": '["0.5","0.5"]', "closed": True},
            {"question": "kept", "outcomePrices": '["0.25","0.75"]', "liquidityNum": None, "bestBid": None}]}
        live = pm.live_markets(ev)
        self.assertEqual([m["question"] for m in live], ["kept"])
        self.assertEqual(live[0]["_yes"], 0.25)

    def test_leading_markets(self):
        mk = [{"question": q, "outcomePrices": f'["{p}","0"]', "volume24hr": v}
              for q, p, v in (("low strike", 0.99, 10), ("busy strike", 0.40, 900), ("mid", 0.70, 50))]
        self.assertEqual(pm.lead({"negRisk": True, "markets": mk}, 1)[0]["question"], "low strike")
        self.assertEqual(pm.lead({"markets": mk}, 1)[0]["question"], "busy strike")

    def test_book_depth_unsorted(self):
        book = {"bids": [{"price": "0.48", "size": "100"}, {"price": "0.50", "size": "200"},
                         {"price": "0.40", "size": "1000"}],
                "asks": [{"price": "0.99", "size": "5"}, {"price": "0.53", "size": "100"},
                         {"price": "0.52", "size": "300"}]}
        d = pm.book_depth(book)
        self.assertAlmostEqual(d["mid"], 0.51)
        self.assertAlmostEqual(d["spread"], 0.02)
        self.assertAlmostEqual(d["bid0.02"], 100.0)            # 0.50 x 200
        self.assertAlmostEqual(d["ask0.02"], 156.0 + 53.0)     # 0.52 x 300 + 0.53 x 100
        self.assertAlmostEqual(d["bid0.05"], 148.0)            # + 0.48 x 100
        self.assertAlmostEqual(d["ask0.05"], 209.0)
        self.assertIsNone(pm.book_depth({"bids": [], "asks": []}))


class ReplayTests(CollectorCase):
    def test_every_events_call_excludes_sports(self):
        urls = []
        real = cc.http_get

        def spy(url, budget, *a, **k):
            urls.append(url)
            return real(url, budget, *a, **k)
        with mock.patch.object(cc, "http_get", spy):
            code, _, _, _ = self.run_collector("polymarket")
        self.assertEqual(code, 0)
        events = [u for u in urls if "/events" in u]
        self.assertEqual(len(events), 11)  # 2 top pages + 7 topics + new + resolving
        self.assertTrue(all("exclude_tag_id=1" in u for u in events))

    def test_sections_and_rules(self):
        code, text, _, _ = self.run_collector("polymarket")
        self.assertEqual(code, 0)
        top = section(text, "## TOP EVENTS")
        self.assertEqual(len(top), 12)
        by_topic = section(text, "## BY TOPIC")
        per = {}
        for l in by_topic:
            per[l.split("]")[0]] = per.get(l.split("]")[0], 0) + 1
        self.assertTrue(all(n <= pm.PER_TOPIC for n in per.values()), per)
        urls = [l.rsplit("| ", 1)[1] for l in top + by_topic]
        self.assertEqual(len(urls), len(set(urls)), "an event is listed once across TOP and BY TOPIC")
        for l in section(text, "## 24H MOVERS"):
            self.assertGreaterEqual(abs(int(re.search(r"\(1d ([+-]\d+) pts", l).group(1))), 5, l)
        self.assertLessEqual(len(section(text, "## RESOLVING")), 10)
        self.assertEqual(len(section(text, "## BOOK DEPTH")), 8)

    def test_resolving_window(self):
        _, text, _, _ = self.run_collector("polymarket")
        for l in section(text, "## RESOLVING"):
            m = re.search(r"ends (\d{4}-\d\d-\d\d) (\d\d:\d\d) UTC", l)
            end = cc.to_utc(f"{m.group(1)}T{m.group(2)}:00Z")
            self.assertTrue(NOW - timedelta(minutes=1) < end <= NOW + timedelta(days=7), l)

    def test_book_404_skips_to_next_event(self):
        books = [v["url"] for v in MANIFEST["files"].values() if "clob.polymarket.com/book" in v["url"]]
        self.overlay(statuses={books[0]: 404})
        code, text, status, _ = self.run_collector("polymarket")
        self.assertEqual(code, 0)
        self.assertFalse(any(n.startswith("book:") for n in status["notes"]), status["notes"])
        self.assertGreaterEqual(len(section(text, "## BOOK DEPTH")), 7)

    def test_new_markets_filter(self):
        start = pm.cc.iso_z(NOW - timedelta(hours=6))
        end = pm.cc.iso_z(NOW + timedelta(days=30))

        def ev(i, title, slugs, vol):
            return {"id": f"n{i}", "title": title, "slug": f"n{i}", "volume24hr": vol, "liquidity": 1000,
                    "startDate": start, "endDate": end, "tags": [{"slug": s} for s in slugs],
                    "markets": [{"id": f"m{i}", "question": title, "outcomePrices": '["0.5","0.5"]',
                                 "endDate": end}]}
        self.overlay(bodies={url_like("gamma-api", "start_date_min"): [
            ev(1, "Solana Up or Down - 5m", ["crypto", "up-or-down"], 900_000),
            ev(2, "Daily recurring market", ["recurring"], 800_000),
            ev(3, "Hidden new market", ["hide-from-new"], 700_000),
            ev(4, "Dota 2 final", ["esports", "sports"], 600_000),
            ev(5, "Real new market", ["politics"], 10_000),
            ev(6, "Thin new market", ["politics"], 1_000)]})
        _, text, _, _ = self.run_collector("polymarket")
        new = section(text, "## NEW MARKETS")
        self.assertEqual(len(new), 1, new)
        self.assertTrue(new[0].startswith("- Real new market — started"))

    def test_sports_event_dropped_from_top(self):
        url = url_like("gamma-api", "offset=0")
        import json
        from helpers import fixture_body
        page = json.loads(fixture_body(url))
        dota = dict(page[0], id="dota", title="Dota 2: Team A vs Team B", slug="dota",
                    volume24hr=10 ** 12, tags=[{"slug": "esports"}, {"slug": "games"}])
        self.overlay(bodies={url: [dota] + page})
        _, text, _, _ = self.run_collector("polymarket")
        self.assertNotIn("Dota 2", text)


if __name__ == "__main__":
    unittest.main()
