"""All four collectors replayed from the recorded fixtures: file layout, item lines, export and
evidence compatibility, and a failed run that must leave the good file alone (spec §5)."""
from __future__ import annotations

import os
import random
import re
import unittest
from datetime import timedelta

from helpers import FIXTURES, MODULES, NOW, CollectorCase, cc, item_lines

import build_agent_package as bap
import evidence
import export

HEADER = re.compile(r"^# .+ Intelligence\n## \d{4}-\d\d-\d\d \d\d:\d\d UTC\n")
SUMMARY = re.compile(r"^- (No |Not reachable)")  # quiet-day lines carry no URL


class ReplayTests(CollectorCase):
    def test_layout_and_items(self):
        for name in MODULES:
            with self.subTest(name):
                code, text, status, line = self.run_collector(name)
                self.assertEqual(code, 0, line)
                self.assertTrue(status["ok"])
                self.assertRegex(text, HEADER)
                self.assertEqual(text.splitlines()[1], f"## {cc.fmt_utc(NOW)}")
                self.assertEqual(status["items"], len(item_lines(text)))
                self.assertGreater(status["items"], 0)
                for l in text.splitlines():
                    if not l.strip() or l.startswith("#"):
                        continue
                    self.assertTrue(l.startswith("- "), l)
                    self.assertLessEqual(len(l.encode("utf-8")), 600, l)
                    if not SUMMARY.match(l):
                        self.assertRegex(l, r"https?://\S+$", l)

    def test_replay_matches_recorded_replay(self):
        """The droplet's replay of the same fixtures (tests/record_collector_fixtures.py) is the
        golden file: a change in any collector's output shows up here. Re-record to update."""
        for name in MODULES:
            with self.subTest(name):
                _, text, _, _ = self.run_collector(name)
                want = (FIXTURES / f"replay_{name}.md").read_text(encoding="utf-8")
                self.assertEqual(text, want)

    def test_export_source_record(self):
        for name in MODULES:
            with self.subTest(name):
                _, text, status, _ = self.run_collector(name)
                rec = export.source_record(name, name, text, NOW + timedelta(minutes=30))
                self.assertTrue(rec["ok"], rec)
                self.assertEqual(rec["items"], status["items"])
                # The stamp is real UTC, so the Seoul-time heuristic must not fire.
                self.assertEqual(rec["fetched_at"], NOW.astimezone(cc.KST).replace(second=0).isoformat())

    def test_quotes_verify_after_agent_view(self):
        rng = random.Random(7)
        for name in MODULES:
            with self.subTest(name):
                _, text, _, _ = self.run_collector(name)
                view = bap.fair_share(bap.split_chunks(bap.drop_noise(text)), 100_000)
                corpus = evidence.Corpus({"01_filtered.md": view})
                lines = item_lines(view)
                for _ in range(20):
                    l = rng.choice(lines)
                    n = rng.randint(20, min(200, len(l)))
                    start = rng.randint(0, len(l) - n)
                    q = l[start:start + n]
                    self.assertEqual(corpus.verify_quote(q)["status"], "verified", q)


class FailureTests(CollectorCase):
    def test_failed_run_keeps_good_file(self):
        for name in MODULES:
            with self.subTest(name):
                os.environ["RECON_COLLECTOR_FIXTURES"] = str(FIXTURES)
                code, _, first, _ = self.run_collector(name)
                self.assertEqual(code, 0)
                before = self.latest_bytes(name)
                self.overlay(empty=True)  # every request now fails (no fixture -> 404)
                code, _, status, line = self.run_collector(name)
                self.assertEqual(code, 1, line)
                self.assertIn("FAILED", line)
                self.assertEqual(self.latest_bytes(name), before)
                self.assertFalse(status["ok"])
                self.assertTrue(status["error"])
                self.assertEqual(status["last_good_at"], first["fetched_at"])

    def test_first_failure_writes_unavailable_stub(self):
        self.overlay(empty=True)
        code, text, status, _ = self.run_collector("zdnet_kr")
        self.assertEqual(code, 1)
        self.assertIn("SOURCE UNAVAILABLE", text)
        self.assertIsNone(status["last_good_at"])
        self.assertFalse(export.source_record("zdnet_kr", "zdnet_kr", text, None)["ok"])

    def test_polymarket_geo_block(self):
        _, _, _, _ = self.run_collector("polymarket")
        before = self.latest_bytes("polymarket")
        from helpers import url_like
        self.overlay(statuses={url_like("gamma-api", "offset=0"): 451})
        code, _, status, _ = self.run_collector("polymarket")
        self.assertEqual((code, status["error"]), (1, "geo-blocked (HTTP 451)"))
        self.assertEqual(self.latest_bytes("polymarket"), before)


if __name__ == "__main__":
    unittest.main()
