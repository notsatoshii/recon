"""ZDNet Korea + 디지털애셋 (spec §3.4)."""
from __future__ import annotations

import socket
import unittest
import urllib.error
from datetime import timedelta
from unittest import mock

from helpers import NOW, CollectorCase, FakeResp, cc, fixture_body, section
import collect_zdnet_kr as z

ZDNET, DA = z.FEEDS[0][1], z.FEEDS[1][1]
BOTH = {"AI", "가상자산"}


def rss(items: list[tuple[str, str, str, str]]) -> str:
    """items: (title, link, pubDate, description)."""
    body = "".join(f"<item><title>{t}</title><link>{l}</link><pubDate>{d}</pubDate>"
                   f"<description>{desc}</description></item>" for t, l, d, desc in items)
    return f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>{body}</channel></rss>'


class TaggingTests(unittest.TestCase):
    def test_latin_keywords_on_word_boundaries(self):
        self.assertIsNone(z.tag_for("MAIN 이벤트 개최", [], "", BOTH))
        self.assertIsNone(z.tag_for("STORY 공모전", [], "", BOTH))
        self.assertEqual(z.tag_for("오픈AI 신모델", [], "", BOTH), "AI")
        self.assertEqual(z.tag_for("AI가 바꾼 업무", [], "", BOTH), "AI")
        self.assertEqual(z.tag_for("토큰증권 STO 법안", [], "", BOTH), "가상자산")

    def test_both_sets_tag_ai_once_unless_digital_asset(self):
        title = "AI로 비트코인 거래 분석"
        self.assertEqual(z.tag_for(title, [], "", BOTH), "AI")
        self.assertEqual(z.tag_for(title, [], "", {"가상자산"}), "가상자산")

    def test_category_and_description_count(self):
        self.assertEqual(z.tag_for("새 정책 발표", ["인공지능"], "", BOTH), "AI")
        self.assertEqual(z.tag_for("새 정책 발표", [], "업비트 거래 재개", BOTH), "가상자산")
        self.assertIsNone(z.tag_for("새 정책 발표", [], "가" * 300 + " 업비트", BOTH))  # past 300 chars

    def test_description(self):
        self.assertEqual(z.clean_desc("[지디넷코리아] 같은 제목입니다", "같은 제목입니다"), "")
        d = z.clean_desc("<p>[지디넷코리아] " + "가나다라마" * 30 + "</p>", "제목")
        self.assertFalse(d.startswith("["))
        self.assertLessEqual(len(d), z.DESC_CHARS)


class FeedTests(CollectorCase):
    def test_replay_both_feeds(self):
        code, text, status, _ = self.run_collector("zdnet_kr")
        self.assertEqual(code, 0)
        ai, va = section(text, "## AI"), section(text, "## 가상자산")
        self.assertTrue(0 < len(ai) <= 12 and 0 < len(va) <= 8)
        self.assertTrue(all("[AI]" in l and "[디지털애셋]" not in l for l in ai))
        self.assertTrue(all(("[ZDNet]" in l) != ("[디지털애셋]" in l) for l in va))
        self.assertEqual(status["requests"], 2)

    def test_one_feed_failing_still_writes_the_other(self):
        for bad, good in ((DA, "ZDNet"), (ZDNET, "디지털애셋")):
            with self.subTest(failing=bad):
                self.overlay(statuses={bad: 500})
                code, text, status, _ = self.run_collector("zdnet_kr")
                self.assertEqual(code, 0)
                self.assertIn("## NOTE:", text)
                self.assertIn("HTTP 500", text)
                self.assertIn("feeds ok 1/2", status["notes"][-1])
                if good == "디지털애셋":
                    self.assertEqual(section(text, "## AI"), ["- No AI item in the last 72 h."])
                    self.assertTrue(all("[디지털애셋]" in l for l in section(text, "## 가상자산")))

    def test_window_and_kst(self):
        old = (NOW - timedelta(hours=73)).astimezone(cc.KST).strftime("%a, %d %b %Y %H:%M:%S +0900")
        new = (NOW - timedelta(hours=1)).astimezone(cc.KST).strftime("%a, %d %b %Y %H:%M:%S +0900")
        naive = (NOW - timedelta(hours=2)).astimezone(cc.KST).strftime("%Y-%m-%d %H:%M:%S")
        self.overlay(bodies={
            ZDNET: rss([("AI 옛 기사", "https://z/1", old, ""), ("AI 새 기사", "https://z/2", new, "")]),
            DA: rss([("비트코인 시황", "https://d/1", naive, "")])})
        code, text, status, _ = self.run_collector("zdnet_kr")
        self.assertEqual(code, 0)
        self.assertNotIn("옛 기사", text)
        self.assertEqual(status["stale_items_dropped"], 1)
        self.assertIn(f"- [{cc.fmt_utc(NOW - timedelta(hours=1))}] [AI] AI 새 기사 | https://z/2", text)
        self.assertIn(f"- [{cc.fmt_utc(NOW - timedelta(hours=2))}] [가상자산] [디지털애셋] 비트코인 시황 | https://d/1",
                      text)


class BudgetTests(CollectorCase):
    """Each feed has its own budget: retries or timeouts on ZDNet never stop 디지털애셋."""

    def setUp(self):
        super().setUp()
        self.bodies = {ZDNET: fixture_body(ZDNET), DA: fixture_body(DA)}
        import os
        os.environ.pop("RECON_COLLECTOR_FIXTURES")

    def run_with(self, script: dict[str, list]):
        calls = []

        def fake(req, timeout):
            url = req.full_url
            calls.append((url, timeout))
            r = script[url].pop(0)
            if isinstance(r, BaseException):
                raise r
            return FakeResp(r)
        with mock.patch.object(cc.urllib.request, "urlopen", fake), mock.patch.object(cc.time, "sleep"):
            out = self.run_collector("zdnet_kr")
        return out, calls

    def test_first_feed_retries_once_second_still_read(self):
        busy = urllib.error.HTTPError(ZDNET, 503, "busy", {}, None)
        (code, text, status, _), calls = self.run_with({ZDNET: [busy, self.bodies[ZDNET]], DA: [self.bodies[DA]]})
        self.assertEqual(code, 0)
        self.assertEqual([u for u, _ in calls], [ZDNET, ZDNET, DA])
        self.assertEqual(status["requests"], 3)
        self.assertIn("feeds ok 2/2", status["notes"][-1])
        self.assertTrue(any("[디지털애셋]" in l for l in section(text, "## 가상자산")))
        self.assertTrue(all(t <= z.REQUEST_TIMEOUT for _, t in calls))

    def test_first_feed_times_out_second_still_read(self):
        to = socket.timeout("timed out")
        (code, text, status, _), calls = self.run_with({ZDNET: [to, to, to], DA: [self.bodies[DA]]})
        self.assertEqual(code, 0)
        self.assertEqual(status["requests"], 4)
        self.assertIn("ZDNet: network error", text)
        self.assertTrue(any("[디지털애셋]" in l for l in section(text, "## 가상자산")))


if __name__ == "__main__":
    unittest.main()
