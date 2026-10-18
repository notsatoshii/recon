"""GitHub trending AI repos (scripts/collect_github_trending.py)."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from helpers import cc  # noqa: F401  (puts scripts/ on sys.path)
import collect_github_trending as gt


def article(repo: str, desc: str, lang: str, stars: str, period: str) -> str:
    owner, name = repo.split("/")
    return f'''<article class="Box-row">
  <h2 class="h3 lh-condensed"><a data-hydro-click="{{}}" href="/{repo}" class="Link"><svg></svg>
      <span class="text-normal">{owner} /</span> {name}</a></h2>
    <p class="col-9 color-fg-muted my-1 tmp-pr-4">
      {desc}
    </p>
  <div class="f6 color-fg-muted mt-2">
  <span itemprop="programmingLanguage">{lang}</span>
      <a href="/{repo}/stargazers" class="Link"><svg aria-label="star"></svg>
        {stars}
</a>
        <span class="d-inline-block float-sm-right"><svg></svg>
        {period}</span>
  </div>
</article>'''


PAGE = "<html>" + article("anthropics/claude-plugins-official", "Directory of Claude Code Plugins.", "Python", "42,037", "1,397 stars today") \
    + article("jhaizhou-ops/d2r-mobile", "iOS compatibility layer for Diablo II.", "C", "3,696", "900 stars today") \
    + article("someone/qwen-nsfw-skill", "Qwen image NSFW pose skill for Claude Code", "Python", "2,625", "800 stars today") \
    + article("debpalash/VoiceStudio", "Fully-local ElevenLabs alternative with voice cloning", "Python", "73,816", "2,125 stars today") + "</html>"


class ParseTests(unittest.TestCase):
    def test_parse_fields(self):
        rs = gt.parse_trending(PAGE)
        self.assertEqual([r["repo"] for r in rs][:2], ["anthropics/claude-plugins-official", "jhaizhou-ops/d2r-mobile"])
        r = rs[0]
        self.assertEqual((r["stars"], r["period_stars"], r["lang"]), (42037, 1397, "Python"))
        self.assertEqual(r["url"], "https://github.com/anthropics/claude-plugins-official")
        self.assertEqual(r["desc"], "Directory of Claude Code Plugins.")

    def test_ai_filter_and_blocklist(self):
        rs = {r["repo"]: r for r in gt.parse_trending(PAGE)}
        self.assertTrue(gt.is_ai(rs["anthropics/claude-plugins-official"]))
        self.assertFalse(gt.is_ai(rs["jhaizhou-ops/d2r-mobile"]))  # "ai" inside a word is not AI
        self.assertTrue(gt.blocked(rs["someone/qwen-nsfw-skill"]))
        self.assertTrue(gt.is_ai(rs["debpalash/VoiceStudio"]))
        self.assertFalse(gt.is_ai({"repo": "x/maintain-tools", "desc": "Remodel your data models"}))
        self.assertTrue(gt.is_ai({"repo": "x/open_claude_ui", "desc": ""}))

    def test_layout_change_parses_to_nothing(self):
        self.assertEqual(gt.parse_trending("<html><div>no rows</div></html>"), [])


class CollectTests(unittest.TestCase):
    def test_block_orders_by_stars_today_and_tracks_streak(self):
        with tempfile.TemporaryDirectory() as d:
            hist = Path(d) / "hist.json"
            hist.write_text(json.dumps({"anthropics/claude-plugins-official": {"2026-10-17": 40000, "2026-10-16": 39000}}))
            now = datetime(2026, 10, 18, 1, 0, tzinfo=timezone.utc)
            with mock.patch.object(gt, "HISTORY", hist), mock.patch.object(gt.cc, "http_text", return_value=PAGE):
                lines, notes = gt.collect(now)
            text = "\n".join(lines)
            self.assertEqual(notes, [])
            today = text.split("THIS WEEK")[0]
            self.assertLess(today.index("VoiceStudio"), today.index("claude-plugins-official"))  # 2,125 before 1,397
            self.assertIn("+1,397 stars today, 42,037 total [Python] (on the daily list 3 days running)", text)
            self.assertNotIn("d2r-mobile", text)
            self.assertNotIn("nsfw", text.lower())
            self.assertIn("2026-10-18", json.loads(hist.read_text())["anthropics/claude-plugins-official"])
            self.assertIn("No other AI repos on this week's trending list.", text)  # weekly repos already listed today

    def test_all_pages_down_gives_a_stub(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(gt, "HISTORY", Path(d) / "h.json"), \
                mock.patch.object(gt.cc, "http_text", side_effect=gt.cc.HTTPFailure("u", 503, "HTTP 503")):
            lines, notes = gt.collect()
        self.assertTrue(lines[1].startswith("SOURCE UNAVAILABLE"))
        self.assertEqual(len(notes), 4)


if __name__ == "__main__":
    unittest.main()
