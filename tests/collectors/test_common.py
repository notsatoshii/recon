"""collector_common: time, feeds, budget, HTTP retries, status and freshness (spec §1, §2)."""
from __future__ import annotations

import json
import socket
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest import mock

from helpers import CollectorCase, FakeResp, cc


class TimeTests(unittest.TestCase):
    def test_rfc822_with_offset(self):
        self.assertEqual(cc.to_utc("Sat, 03 Oct 2026 23:17:01 +0900"),
                         datetime(2026, 10, 3, 14, 17, 1, tzinfo=timezone.utc))

    def test_naive_is_kst(self):
        self.assertEqual(cc.to_utc("2026-10-04 05:00:00"), datetime(2026, 10, 3, 20, 0, tzinfo=timezone.utc))
        self.assertEqual(cc.to_utc("2026-10-04 05:00"), datetime(2026, 10, 3, 20, 0, tzinfo=timezone.utc))

    def test_iso(self):
        self.assertEqual(cc.to_utc("2026-10-04T21:00:00Z"), datetime(2026, 10, 4, 21, tzinfo=timezone.utc))
        self.assertEqual(cc.to_utc("2026-10-04T21:00:00.1234567+00:00"),
                         datetime(2026, 10, 4, 21, 0, 0, 123456, tzinfo=timezone.utc))
        self.assertIsNone(cc.to_utc("not a date"))
        self.assertIsNone(cc.to_utc(""))

    def test_fmt(self):
        d = datetime(2026, 10, 3, 23, 30, tzinfo=timezone.utc)
        self.assertEqual(cc.fmt_utc(d), "2026-10-03 23:30 UTC")
        self.assertEqual(cc.fmt_utc(None), "n/a")


class FeedTests(unittest.TestCase):
    RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<item><title>A &amp; B</title><link>https://x/1</link><pubDate>Sat, 03 Oct 2026 23:17:01 +0900</pubDate>
<description>short</description><content:encoded><![CDATA[<p>long</p>]]></content:encoded>
<category>AI</category><category>정책</category><guid>g1</guid></item>
</channel></rss>"""
    ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>v1.2.3</title><link rel="alternate" href="https://x/r"/><updated>2026-10-02T10:00:00Z</updated>
<id>tag:1</id><content type="html">&lt;li&gt;fix&lt;/li&gt;</content></entry></feed>"""

    def test_rss(self):
        [it] = cc.parse_rss(self.RSS)
        self.assertEqual(it["title"], "A & B")
        self.assertEqual(it["summary"], "short")
        self.assertEqual(it["categories"], ["AI", "정책"])
        self.assertEqual(it["id"], "g1")

    def test_atom_detected(self):
        [it] = cc.parse_rss(self.ATOM)
        self.assertEqual((it["title"], it["link"]), ("v1.2.3", "https://x/r"))
        self.assertIn("fix", cc.strip_html(it["summary"]))

    def test_one_line_limit(self):
        self.assertEqual(cc.one_line("a  b\n c"), "a b c")
        self.assertEqual(len(cc.one_line("가" * 100, 60)), 60)


class BudgetTests(unittest.TestCase):
    def test_request_limit(self):
        b = cc.Budget(seconds=60, requests=2, mbytes=1)
        b.take()
        b.take()
        with self.assertRaises(cc.BudgetExceeded):
            b.take()
        self.assertTrue(b.exhausted)

    def test_time_limit(self):
        clock = [100.0]
        with mock.patch.object(cc.time, "monotonic", lambda: clock[0]):
            b = cc.Budget(seconds=10, requests=99, mbytes=1)
            b.take()
            clock[0] += 11
            with self.assertRaises(cc.BudgetExceeded):
                b.take()

    def test_byte_limit(self):
        b = cc.Budget(seconds=60, requests=99, mbytes=0.001)
        b.take()
        b.add_bytes(2000)
        with self.assertRaises(cc.BudgetExceeded):
            b.take()


class HttpTests(CollectorCase):
    URL = "https://example.test/feed"

    def setUp(self):
        super().setUp()
        import os
        os.environ.pop("RECON_COLLECTOR_FIXTURES")  # these tests drive urlopen directly

    def test_retry_on_5xx_then_success(self):
        err = urllib.error.HTTPError(self.URL, 503, "busy", {}, None)
        calls = [err, FakeResp(b"ok")]

        def fake(req, timeout):
            r = calls.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        b = cc.Budget(seconds=30, requests=3, mbytes=1)
        with mock.patch.object(cc.urllib.request, "urlopen", fake), mock.patch.object(cc.time, "sleep"):
            body, _ = cc.http_get(self.URL, b)
        self.assertEqual(body, b"ok")
        self.assertEqual(b.requests, 2)

    def test_4xx_not_retried_and_451_named(self):
        for code, msg in ((404, "HTTP 404"), (451, "geo-blocked (HTTP 451)")):
            n = [0]

            def fake(req, timeout, code=code):
                n[0] += 1
                raise urllib.error.HTTPError(self.URL, code, "x", {}, None)
            with mock.patch.object(cc.urllib.request, "urlopen", fake):
                with self.assertRaises(cc.HTTPFailure) as cm:
                    cc.http_get(self.URL, cc.Budget(seconds=30, requests=5, mbytes=1))
            self.assertEqual((cm.exception.status, cm.exception.msg, n[0]), (code, msg, 1))

    def test_timeouts_give_up_after_two_retries(self):
        n = [0]

        def fake(req, timeout):
            n[0] += 1
            raise socket.timeout("timed out")
        with mock.patch.object(cc.urllib.request, "urlopen", fake), mock.patch.object(cc.time, "sleep"):
            with self.assertRaises(cc.HTTPFailure):
                cc.http_get(self.URL, cc.Budget(seconds=60, requests=10, mbytes=1))
        self.assertEqual(n[0], 3)

    def test_per_request_timeout_passed(self):
        seen = []

        def fake(req, timeout):
            seen.append(timeout)
            return FakeResp(b"x")
        with mock.patch.object(cc.urllib.request, "urlopen", fake):
            cc.http_get(self.URL, cc.Budget(seconds=60, requests=2, mbytes=1), timeout=15)
            cc.http_get(self.URL, cc.Budget(seconds=60, requests=2, mbytes=1))
        self.assertEqual(seen, [15, cc.TIMEOUT])

    def test_fixture_status_file(self):
        d = self.overlay(statuses={self.URL: 500}, empty=True)
        self.assertTrue((d / f"{cc.fixture_name(self.URL)}.status").exists())
        with self.assertRaises(cc.HTTPFailure) as cm:
            cc.http_get(self.URL, cc.Budget(seconds=5, requests=2, mbytes=1))
        self.assertEqual(cm.exception.status, 500)
        with self.assertRaises(cc.HTTPFailure) as cm:  # no fixture at all
            cc.http_get(self.URL + "?other", cc.Budget(seconds=5, requests=2, mbytes=1))
        self.assertEqual(cm.exception.msg, "no fixture")


class OutputTests(CollectorCase):
    def test_status_keeps_last_good(self):
        ok = cc.SourceResult(name="t", ok=True, items=3, fetched_at="2026-10-03T20:00:00Z")
        cc.write_status(ok)
        bad = cc.SourceResult(name="t", ok=False, fetched_at="2026-10-04T20:00:00Z", error="HTTP 500")
        cc.write_status(bad)
        st = json.loads((cc.DATA_DIR / "t" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual((st["ok"], st["error"], st["last_good_at"]), (False, "HTTP 500", "2026-10-03T20:00:00Z"))

    def test_header_is_real_utc(self):
        stamp = datetime(2026, 10, 3, 23, 30, tzinfo=timezone.utc)  # Seoul is already 10-04 08:30
        self.assertEqual(cc.header("X", stamp)[:2], ["# X Intelligence", "## 2026-10-03 23:30 UTC"])

    def test_fresh(self):
        now = datetime(2026, 10, 4, 20, 0, tzinfo=timezone.utc)
        p = cc.DATA_DIR / "f.md"
        for age, ok in ((71, True), (73, False)):
            p.write_text("\n".join(cc.header("F", now - timedelta(hours=age))) + "\n", encoding="utf-8")
            got = cc.fresh(p, now)
            self.assertEqual((got[0], got[1]), (ok, float(age)))
        p.write_text("# F Intelligence\nno stamp\n", encoding="utf-8")
        self.assertEqual(cc.fresh(p, now), (False, None, None))
        self.assertEqual(cc.fresh(cc.DATA_DIR / "missing.md", now), (False, None, None))


if __name__ == "__main__":
    unittest.main()
